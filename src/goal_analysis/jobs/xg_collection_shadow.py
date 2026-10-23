"""Small cache-first match-xG source completion, never a decision or refit."""
import json
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs.player_collection_shadow import QUOTA, _bootstrap_allowed
from goal_analysis.jobs.the_stats_context import Resources
from goal_analysis.jobs.xg_shadow import _evidence, _normalize
from goal_analysis.jobs.xg_specialist_shadow import _targets
from goal_analysis.normalization.canonical import utc
from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
from goal_analysis.storage.provider_quota import SharedQuota
from goal_analysis.storage.raw_admission import admit_raw_sources

VERSION = 'bounded-match-xg-source-collection-shadow-v1'
POLICY = {'enabled': True, 'mode': 'SHADOW', 'max_requests': 20, 'max_seconds': 30}


def enabled(root):
    path = Path(root)/'config/xg-collection-shadow.json'
    return path.is_file() and path.stat().st_size < 1000 and json.loads(path.read_bytes()) == POLICY


def _eligible(match, target, cutoff):
    """Identity and explicit regulation/quality flags, before a statistics GET."""
    score = match.get('score', {})
    if (not isinstance(score, dict)
            or any(not isinstance(match.get(side+'_team'), dict) for side in ('home', 'away'))):
        return False
    return (bool(re.fullmatch(r'mt_\d+', str(match.get('id'))))
        and match.get('competition_id') == target['competition_id']
        and match.get('season_id') == target['season_id']
        and match.get('status') == 'finished' and match.get('is_neutral') is False
        and score.get('went_to_extra_time') is False and score.get('went_to_penalties') is False
        and match.get('xg_available') is True and match.get('xg_quality') == 'full'
        and cutoff-timedelta(days=180) <= utc(match['utc_date']) < min(cutoff, utc(target['kickoff']))
        and all(re.fullmatch(r'tm_\d+', str(match.get(side+'_team', {}).get('id')))
                for side in ('home', 'away'))
        and match['home_team']['id'] != match['away_team']['id'])


def _metadata(root, targets, cutoff, deadline):
    """Read witnessed history only. Equal-time identity conflicts abstain."""
    matches, stamps, issues, total = {}, {}, Counter(), 0
    path = root/'data/goal_analysis.sqlite3'
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        rows = db.execute("""SELECT s.* FROM raw_snapshots s JOIN temporal_receipts t
            ON t.namespace='raw' AND t.source_key=s.content_hash
            WHERE s.provider='the_stats_api' AND s.source_url LIKE '%/football/matches?%'
            AND s.fetched_at<=? AND t.available_at<=?
            ORDER BY s.fetched_at DESC,s.content_hash LIMIT 1001""",
            (cutoff.isoformat(), cutoff.isoformat())).fetchall()
        if len(rows) > 1000:
            raise OverflowError('INCOMPLETE_XG_METADATA_SOURCE_LIMIT')
        for raw in rows:
            if monotonic() >= deadline:
                raise TimeoutError('INCOMPLETE_XG_METADATA_TIME_LIMIT')
            row = dict(raw)
            if (not 0 < row['size_bytes'] <= 4*1024*1024
                    or total+row['size_bytes'] > 128*1024*1024):
                raise OverflowError('INCOMPLETE_XG_METADATA_BYTE_LIMIT')
            total += row['size_bytes']
            try:
                body, proof = _evidence(db, row, root, cutoff)
                if not isinstance(body, list) or len(body) > 10000:
                    raise ValueError('BOUNDED_XG_METADATA_LIST_REQUIRED')
                occurrences = Counter(m.get('id') for m in body if isinstance(m, dict))
                for index, match in enumerate(body):
                    if index % 1024 == 0 and monotonic() >= deadline:
                        raise TimeoutError('INCOMPLETE_XG_METADATA_TIME_LIMIT')
                    if not isinstance(match, dict) or not re.fullmatch(r'mt_\d+', str(match.get('id'))):
                        continue
                    native, stamp = match['id'], utc(proof['times']['observed_at'])
                    if native not in stamps:
                        stamps[native] = stamp
                        if occurrences[native] == 1:
                            matches[native] = (match, proof)
                    elif stamp == stamps[native] and (occurrences[native] != 1
                            or native in matches and matches[native][0] != match):
                        matches.pop(native, None)
            except (ValueError, KeyError, TypeError, OSError):
                issues['INVALID_WITNESSED_HISTORY_SOURCE'] += 1
    index = defaultdict(list)
    for match, proof in matches.values():
        if not all(re.fullmatch(prefix+r'_\d+', str(match.get(field)))
                   for prefix, field in (('comp', 'competition_id'), ('sn', 'season_id'))):
            issues['INVALID_XG_HISTORY_SCOPE'] += 1
            continue
        for side in ('home', 'away'):
            team = match.get(side+'_team')
            if not isinstance(team, dict) or not re.fullmatch(r'tm_\d+', str(team.get('id'))):
                continue
            index[(match.get('competition_id'), match.get('season_id'), side,
                   team['id'])].append((match, proof))
    queues = []
    for fid, target in sorted(targets.items(), key=lambda item: (utc(item[1]['kickoff']), item[0])):
        if monotonic() >= deadline:
            raise TimeoutError('INCOMPLETE_XG_METADATA_TIME_LIMIT')
        for side in ('home', 'away'):
            values = []
            for match, proof in index[(target['competition_id'], target['season_id'], side, target[side+'_team_id'])]:
                try:
                    if (_eligible(match, target, cutoff)
                            and match[side+'_team']['id'] == target[side+'_team_id']):
                        values.append((match, proof))
                except (ValueError, KeyError, TypeError):
                    issues['INVALID_XG_HISTORY_ROW'] += 1
            values.sort(key=lambda pair: (utc(pair[0]['utc_date']), pair[0]['id']), reverse=True)
            queues.append([(fid, match, proof) for match, proof in values[:6]])
    plan, seen = [], set()
    # Recent-first within each exact team/venue history; distribute the bounded
    # budget across targets without cherry-picking xG values or outcomes.
    for index in range(6):
        for queue in queues:
            if index < len(queue) and queue[index][1]['id'] not in seen:
                plan.append(queue[index]); seen.add(queue[index][1]['id'])
    return plan, dict(issues)


def _stored(root, database, native, match, metadata_proof, cutoff, clock):
    endpoint = f'https://api.thestatsapi.com/api/football/matches/{native}/stats'
    with closing(database.connect()) as db:
        rows = db.execute("""SELECT * FROM raw_snapshots WHERE provider='the_stats_api'
            AND source_url=? ORDER BY fetched_at DESC,content_hash LIMIT 1001""", (endpoint,)).fetchall()
        if len(rows) > 1000:
            raise OverflowError('XG_STATS_VERSION_LIMIT')
        prior = [dict(row) for row in rows if utc(row['fetched_at']) <= cutoff]
        if not prior:
            return None
        latest = utc(prior[0]['fetched_at'])
        chosen = [row for row in prior if utc(row['fetched_at']) == latest]
        if len(chosen) != 1 or not 0 < chosen[0]['size_bytes'] <= 4*1024*1024:
            raise ValueError('AMBIGUOUS_OR_OVERSIZED_LATEST_XG_STATS')
    # A committed but not yet witnessed historical source may gain a genuine
    # presence witness NOW. Existing receipts are only revalidated, never edited.
    admission = admit_raw_sources(database, root, [chosen[0]['content_hash']], clock=clock)
    if admission['rejected']:
        raise ValueError('LATEST_STORED_XG_PRESENCE_UNPROVEN')
    with closing(database.connect()) as db:
        stats, proof = _evidence(db, chosen[0], root, utc(clock()))
        return _normalize(match, stats, metadata_proof, proof)


def collect(root, fixtures, *, clock=None, max_requests=20, max_seconds=30):
    """Only already admitted histories of fresh exact current native targets.

    Never witness old target metadata/prices afresh. New statistics receive an
    actual new presence receipt, which cannot make an old forecast admissible.
    """
    if (not isinstance(fixtures, list) or len(fixtures) > 1000
            or type(max_requests) is not int or not 0 <= max_requests <= 20
            or type(max_seconds) is not int or not 1 <= max_seconds <= 30):
        raise ValueError('INVALID_XG_COLLECTION_BUDGET')
    root = Path(root).resolve(); clock = clock or (lambda: datetime.now(UTC))
    started = utc(clock()); timer = monotonic(); initial = canonical_sha256(fixtures)
    by_id = {}
    for fixture in fixtures:
        if fixture['fixture_id'] in by_id:
            raise ValueError('DUPLICATE_XG_COLLECTION_TARGET')
        by_id[fixture['fixture_id']] = fixture
    targets, _, target_issues = _targets(root, by_id, started)
    targets = {fid: target for fid, target in targets.items()
               if utc(target['kickoff']) > started+timedelta(minutes=15)}
    plan, metadata_issues = _metadata(root, targets, started, timer+max_seconds-4) if targets else ([], {})
    quota = SharedQuota(root, kind='daily', policy=QUOTA, clock=clock)
    client = TheStatsPrimaryClient(root, max_requests=max(1, max_requests), timeout=3,
        clock=clock, quota_observer=quota) if plan else None
    resources = Resources(root, client, clock, max_requests) if plan else None
    done, outcomes, envelopes = set(), [], []
    bootstrapped = False; bootstrap_requests = 0
    for cache_only in (True, False):
        for index, (fid, match, metadata_proof) in enumerate(plan):
            if index in done:
                continue
            now = utc(clock()); native = match['id']; status = None; envelope = None
            source = None
            if now < started or monotonic()-timer >= max_seconds-4:
                status = 'COLLECTION_TIME_LIMIT'
            elif utc(targets[fid]['kickoff']) <= now+timedelta(minutes=15):
                status = 'PREMATCH_LOCKOUT'
            elif now-utc(by_id[fid]['fixture_source']['observed_at']) > timedelta(minutes=30):
                status = 'STALE_TARGET_METADATA'
            if status is None and cache_only:
                try:
                    envelope = _stored(root, resources.database, native, match, metadata_proof, now, clock)
                    if envelope is not None:
                        status = 'REUSED_WITNESSED_XG'
                except (ValueError, KeyError, TypeError, OSError):
                    # Never refetch just to hide a bad latest source/witness.
                    status = 'LATEST_STORED_XG_UNPROVEN'
            if status is None:
                if not cache_only:
                    if resources.usage['requests'] >= max_requests or resources.usage['stopped_reason']:
                        status = resources.usage['stopped_reason'] or 'REQUEST_BUDGET'
                    else:
                        allowance = quota.allowance()
                        if (allowance['status'] == 'QUOTA_REFRESH_REQUIRED' and not bootstrapped
                                and _bootstrap_allowed(quota, now)):
                            client.quota_observer = SharedQuota(root, kind='daily', clock=clock)
                            bootstrapped = True
                        elif allowance['requests'] > 0:
                            client.quota_observer = quota
                        else:
                            status = allowance['status']
                if status is None:
                    previous = client.authenticated_request_count
                    stats, source, reason = resources.get(f'/football/matches/{native}/stats', cache_only=cache_only)
                    if client.quota_observer is not quota:
                        bootstrap_requests += client.authenticated_request_count-previous
                    if reason == 'CACHE_MISS':
                        continue
                    status = reason or 'STATS_SOURCE_STORED'
                    if source:
                        try:
                            if (not isinstance(stats, dict) or stats.get('match_id') != native
                                    or urlsplit(source['source_url']).path != f'/api/football/matches/{native}/stats'):
                                raise ValueError('XG_MATCH_ID_MISMATCH')
                            # The unchanged strict reader, not a source-shaped
                            # caller dictionary, decides admissibility after commit.
                            admission = admit_raw_sources(resources.database, root, [source['content_hash']], clock=clock)
                            if admission['rejected']:
                                raise ValueError('XG_SOURCE_PRESENCE_UNPROVEN')
                            envelope = _stored(root, resources.database, native, match, metadata_proof, utc(clock()), clock)
                            status = 'AVAILABLE_WITNESSED_XG'
                        except (ValueError, KeyError, TypeError, OSError):
                            status = 'MISSING_OR_UNPROVEN_REGULATION_XG'
            done.add(index)
            if envelope is not None:
                envelopes.append(envelope)
            outcomes.append({'target_fixture_id': fid, 'history_match_id': native, 'status': status,
                             'source_sha256': source['content_hash'] if source else None})
    assert canonical_sha256(fixtures) == initial
    return {'version': VERSION, 'mode': 'SHADOW', 'status': 'OBSERVED' if plan else 'NO_SCOPED_XG_HISTORY',
        'started_at': started.isoformat(), 'completed_at': utc(clock()).isoformat(),
        'seconds': monotonic()-timer, 'verified_targets': len(targets), 'planned_matches': len(plan),
        'target_issues': target_issues, 'metadata_issues': metadata_issues,
        'outcomes': outcomes, 'envelopes': envelopes, 'accepted_matches': len(envelopes),
        'usage': resources.usage if resources else {'requests': 0, 'cache_hits': 0},
        'external_requests': client.request_count if client else 0,
        'authenticated_requests': client.authenticated_request_count if client else 0,
        'quota_bootstrap_requests': bootstrap_requests, 'model_fits': 0, 'llm_requests': 0,
        'ledger_writes': 0, 'probability_adjustment': 0, 'live_decision_effect': False}
