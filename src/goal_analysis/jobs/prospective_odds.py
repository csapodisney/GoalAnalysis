"""Bounded SHADOW first-price capture 36–48h ahead, independent of ticket saves.

The existing timer/monitor owns polling, leases and snapshots. This optional
spare-capacity stage never builds tickets or claims a yet-unelapsed price pair.
"""
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from goal_analysis.agents import canonical_sha256
from goal_analysis.normalization.canonical import utc
from goal_analysis.providers.the_stats_leagues import LEAGUES
from goal_analysis.providers.the_stats_odds import valid_quote_source
from goal_analysis.storage.http_observations import read_http_observation
from goal_analysis.storage.provider_quota import SharedQuota
from goal_analysis.storage.raw_evidence import read_witnessed_raw

from .background_history import policy_at, publication_gate
from .odds_monitor import MonitorPolicy, OddsMonitor
from .odds_monitor_providers import build_monitor_queries, fetch_monitor_query
from .portfolio import RunLock
from .the_stats_context import Resources
from .the_stats_live import BERLIN, extra_routing, normalize_fixture

VERSION = 'prospective-first-odds-shadow-v1'
SOURCE_FILES = ('src/goal_analysis/jobs/prospective_odds.py',
    'scripts/run-arthur-odds-monitor.py', 'src/goal_analysis/jobs/odds_monitor.py',
    'src/goal_analysis/jobs/odds_monitor_providers.py', 'src/goal_analysis/jobs/the_stats_live.py',
    'src/goal_analysis/jobs/the_stats_context.py', 'src/goal_analysis/providers/the_stats_odds.py',
    'src/goal_analysis/storage/http_observations.py', 'src/goal_analysis/storage/provider_quota.py',
    'src/goal_analysis/jobs/background_history.py','config/historical-background.json',
    'config/daily223-live.json','src/goal_analysis/providers/the_stats_primary.py',
    'src/goal_analysis/providers/the_stats_api.py')
MAX_REQUESTS = 4
MAX_PAGES = 10
MAX_SHARED_DAILY = 400
FIRST_OBSERVATION_RESERVE = 100


def implementation_hash():
    root = Path(__file__).resolve().parents[3]
    return canonical_sha256({name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in SOURCE_FILES})


def _read(path):
    if path.stat().st_size > 100000:
        raise ValueError('PROSPECTIVE_STATE_SIZE')
    return json.loads(path.read_bytes())


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


def armed(root, now):
    path = root/'data/arthur/prospective-odds/activation.json'
    if not path.is_file():
        return False
    value = _read(path)
    return (value.get('version') == VERSION and value.get('enabled') is True
            and utc(value['activated_at']) <= now
            and value.get('source_sha256') == implementation_hash())


def witnessed_source(resources, source, now):
    with closing(resources.database.connect()) as db:
        if source.get('http_observation_id'):
            return read_http_observation(db, resources.root, source['http_observation_id'], now, source=source)
        row = db.execute("SELECT * FROM raw_snapshots WHERE content_hash=? AND provider='the_stats_api'",
                         (source['content_hash'],)).fetchone()
        if (row is None or row['source_url'] != source['source_url']
                or utc(row['fetched_at']) != utc(source['observed_at'])):
            raise ValueError('PROSPECTIVE_SOURCE_RECEIPT_MISMATCH')
        return read_witnessed_raw(db, dict(row), resources.root, now)


def plan_page(rows, source, config, now):
    """Exact curated routing, not calendar names or fuzzy team identities."""
    routes = {LEAGUES[str(v['api_football_id'])][0]:v for v in config['leagues']
              if str(v['api_football_id']) in LEAGUES}
    for identifier, mapping in LEAGUES.items():
        routes.setdefault(mapping[0], extra_routing(int(identifier), now.date()))
    counts = Counter(row.get('id') for row in rows if isinstance(row, dict))
    fixtures, issues = [], Counter()
    for row in rows:
        try:
            if not isinstance(row, dict) or counts[row['id']] != 1:
                raise ValueError('AMBIGUOUS_FIXTURE')
            if row.get('status') != 'scheduled':
                continue
            kickoff = utc(row['utc_date'])
            if not now+timedelta(hours=36) <= kickoff <= now+timedelta(hours=48):
                continue
            comp = row.get('competition_id') or (row.get('competition') or {}).get('id')
            if comp not in routes:
                issues['UNMAPPED_COMPETITION'] += 1
                continue
            fixtures.append(normalize_fixture(row, routes[comp], kickoff.astimezone(BERLIN).date(), source))
        except (ValueError, TypeError, KeyError):
            issues['INVALID_OR_AMBIGUOUS_FIXTURE'] += 1
    return sorted(fixtures, key=lambda v:(v['kickoff'],v['fixture_id'])), dict(issues)


def first_captured(root, query, now):
    """Verify the successful compact snapshot; a failed/empty attempt is no first price."""
    path = root/'data/arthur/prospective-odds/shadow-monitor.sqlite3'
    if not path.is_file():
        return False
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        row = db.execute('SELECT state_json FROM monitor_queries WHERE query_key=?', (query['query_key'],)).fetchone()
        if row is None:
            return False
        state = json.loads(row[0])
        snapshot = db.execute('SELECT provider,query_key,observed_at,quotes_json FROM monitor_snapshots '
            'WHERE snapshot_id=?', (state.get('snapshot_id'),)).fetchone()
    if snapshot is None or snapshot[0] != 'the_stats_api' or snapshot[1] != query['query_key']:
        return False
    quotes = json.loads(snapshot[3])
    if canonical_sha256([*snapshot[:3],quotes]) != state['snapshot_id'] or utc(snapshot[2]) > now:
        return False
    return any(valid_quote_source(q) and q.get('quoted_at') is None
        and q.get('fixture_id') == query['fixtures'][0]['fixture_id']
        and utc(q['quote_observed_at']) <= utc(snapshot[2])
        and utc(q['kickoff']) == utc(query['fixtures'][0]['kickoff']) for q in quotes)


def tick(root, *, clock=lambda:datetime.now(UTC), api_key=None, transport=None):
    root = Path(root).resolve()
    now = utc(clock())
    base = {'version':VERSION,'mode':'SHADOW','requests':0,'llm_calls':0,'ledger_writes':0,
            'live_promoted':False,'actual_24h_pair_proven':False}
    client = None
    try:
        if not armed(root, now):
            return {**base,'status':'DISARMED_OR_SOURCE_CHANGED'}
        with RunLock(root/'data/arthur/run.lock'):
            gate = publication_gate(root, now, 30)
            if gate['status'] != 'AVAILABLE':
                return {**base,'status':gate['status']}
            path = root/'data/arthur/prospective-odds/state.json'
            state = _read(path) if path.is_file() else {}
            if state.get('attempted_at') and utc(state['attempted_at']) > now:
                return {**base,'status':'CLOCK_MOVED_BACKWARDS'}
            if state.get('next_attempt_at') and now < utc(state['next_attempt_at']):
                return {**base,'status':'COOLDOWN'}
            policy = policy_at(root)
            if policy is None:
                return {**base,'status':'NO_SHARED_SPARE_QUOTA_POLICY'}
            quota = SharedQuota(root, kind='history', clock=clock,
                policy={**policy['quota'],'max_requests':MAX_REQUESTS,'per_minute_cap':MAX_REQUESTS,
                    'daily_cap':min(MAX_SHARED_DAILY,policy['quota']['daily_cap']+FIRST_OBSERVATION_RESERVE)},
                legacy_requests=gate['legacy_requests'],
                source_check=lambda:armed(root,utc(clock())))
            allowance = quota.allowance()
            if allowance['requests'] <= 0:
                return {**base,'status':allowance['status']}
            from goal_analysis.config.daily_223_live import validate_live_config
            from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient

            config = validate_live_config(json.loads((root/'config/daily223-live.json').read_text('utf-8-sig')))
            limit = min(MAX_REQUESTS, allowance['requests'])
            stop = gate['stop_at']
            client = TheStatsPrimaryClient(root, api_key=api_key, transport=transport, clock=clock,
                quota_observer=quota, max_requests=limit, timeout=5)
            resources = Resources(root,client,clock,limit)
            lower, upper = now+timedelta(hours=36), now+timedelta(hours=48)
            query = {'date_from':lower.date().isoformat(),'date_to':upper.date().isoformat(),
                     'utc_offset':'+00:00','status':'scheduled'}
            query_id = canonical_sha256(query)
            page = state.get('next_page',1) if state.get('query_id') == query_id else 1
            if type(page) is not int or not 1 <= page <= MAX_PAGES:
                raise ValueError('INVALID_PROSPECTIVE_CURSOR')
            # Durable before network I/O: crashes keep a bounded retry cooldown.
            _save(path,{'attempted_at':now.isoformat(),'next_attempt_at':(now+timedelta(minutes=15)).isoformat(),
                'query_id':query_id,'next_page':page})
            data, source, error = resources.get('/football/matches',
                {**query,'page':page,'per_page':100},ttl=timedelta(hours=6))
            if error:
                return {**base,'status':'CALENDAR_UNAVAILABLE','requests':client.request_count}
            body, proof = witnessed_source(resources,source,utc(clock()))
            meta = body.get('meta',{})
            total = meta.get('total_pages')
            if (not isinstance(data,list) or len(data)>100 or type(meta.get('page')) is not int
                    or meta['page'] != page or meta.get('per_page') != 100 or type(total) is not int
                    or not 0 <= total <= 100000 or data and page > total or not data and total > 0):
                raise ValueError('INVALID_PROSPECTIVE_PAGINATION')
            fixtures, issues = plan_page(data, source, config, utc(clock()))
            planned = build_monitor_queries(fixtures,utc(clock()),{**config,'monitor_providers':['the_stats_api']})
            pending = [q for q in planned if not first_captured(root,q,utc(clock()))]
            # Remaining requests share the same client and conservative quota.
            remaining = max(0,limit-client.request_count)
            monitor = {'provider_calls':0,'status':'NO_REQUEST_CAPACITY','queries':[]}
            if remaining and utc(clock()) < stop-timedelta(seconds=6):
                engine = OddsMonitor(root,clock=clock,shadow=True,policy=MonitorPolicy(max_queries_per_tick=remaining,
                    max_requests_per_minute=30,daily_credit_limits={'the_stats_api':2000},
                    quota_reserve_limits={'the_stats_api':1000}))

                def fetch(q):
                    if utc(clock()) >= stop-timedelta(seconds=6):
                        raise ValueError('DAILY_PRIORITY_TIME_LIMIT')
                    return fetch_monitor_query(q,football_client=client,now=utc(clock()))

                monitor = engine.tick(pending,fetch)
            result = {**base,'status':'OBSERVATION_STAGE_COMPLETE','requests':client.request_count,
                'observed_at':utc(clock()).isoformat(),'calendar_page':page,'calendar_total_pages':total,
                'calendar_complete':total <= 1,'calendar_partial':total > 1,'calendar_pages_capped':total > MAX_PAGES,
                'calendar_receipt':proof,'fixtures':fixtures,'eligible_fixtures':len(fixtures),
                'already_captured':len(planned)-len(pending),'missing_first_observations':len(pending),
                'issues':issues,'monitor':monitor,'first_captured_now':sum(first_captured(root,q,utc(clock())) for q in pending),
                'time_basis':'ACTUAL_RESPONSE_RECEIPT_NOT_PROVIDER_QUOTE_CLOCK'}
            _save(path,{'attempted_at':now.isoformat(),'next_attempt_at':(now+timedelta(minutes=15)).isoformat(),
                'query_id':query_id,'next_page':page+1 if page < min(total,MAX_PAGES) else 1})
            _save(path.with_name('latest.json'),result)
            return result
    except Exception:  # noqa: BLE001 - optional collection; no provider/secret text or daily veto
        return {**base,'status':'PROSPECTIVE_UNAVAILABLE',
                'requests':client.request_count if client is not None else 0}
