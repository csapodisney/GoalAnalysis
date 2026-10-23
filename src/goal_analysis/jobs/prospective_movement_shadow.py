"""Receipt-bound early history for existing native options, read-only SHADOW.

No fitting, ranking, review opinion or publication occurs here. The existing
movement formula runs only on authenticated source prices and available history.
"""
import json
import math
import re
import sqlite3
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from goal_analysis.agents import canonical_sha256
from goal_analysis.engine.model_recommendations import SETTLEMENT
from goal_analysis.engine.odds_movement import IDENTITY_FIELDS, annotate_odds_movement
from goal_analysis.normalization.canonical import utc
from goal_analysis.providers.the_stats_odds import valid_quote_source
from goal_analysis.storage.source_observations import read_source_raw, source_key

VERSION = 'prospective-movement-source-review-shadow-v1'
MAX_SNAPSHOTS = 512
MAX_QUOTES = 50000
MAX_BYTES = 32 * 1024 * 1024
MAX_SOURCES = 256


def _open(path):
    db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db


def _raw_price(data, quote):
    """Check compact product identity against the original native last_seen."""
    if data['match_id'] != quote['fixture_id'].split(':')[1]:
        raise ValueError('RAW_FIXTURE_CONFLICT')
    books = [v for v in data['bookmakers'] if v['bookmaker'] == quote['provider_bookmaker']]
    if len(books) != 1:
        raise ValueError('AMBIGUOUS_BOOKMAKER')
    families = {'h2h': ('match_odds', 'FULL_TIME'),
        'h2h_3_way_h1': ('first_half_result', 'FIRST_HALF'),
        'btts': ('btts', 'FULL_TIME'), 'btts_h1': ('btts_first_half', 'FIRST_HALF')}
    point = None
    key = quote['market_key']
    if key in families:
        market, period = families[key]
    else:
        line = re.fullmatch(r'(totals|totals_h1)_(\d+)_5', key)
        if line is None:
            raise ValueError('UNSUPPORTED_MARKET')
        market, period = (('total_goals', 'FULL_TIME') if line[1] == 'totals'
                          else ('first_half_total_goals', 'FIRST_HALF'))
        point = Decimal(line[2])+Decimal('.5')
    if quote['provider_market_id'] != market or quote['period'] != period:
        raise ValueError('RAW_MARKET_PERIOD_CONFLICT')
    values = books[0]['markets'][market]
    if point is not None:
        lines = [v for k, v in values.items() if Decimal(k) == point]
        if len(lines) != 1:
            raise ValueError('AMBIGUOUS_RAW_LINE')
        values = lines[0]
    if Decimal(str(values[quote['selection_key']]['last_seen'])) != Decimal(str(quote['decimal_price'])):
        raise ValueError('RAW_PRICE_CONFLICT')


class _Reader:
    def __init__(self, db, root, cutoff):
        self.db, self.root, self.cutoff = db, root, cutoff
        self.cache, self.failed, self.bytes_read = {}, set(), 0

    def quote(self, quote, fixture):
        if (not valid_quote_source(quote, fixture) or quote.get('quoted_at') is not None
                or quote.get('quote_available') is False or quote.get('settlement') != SETTLEMENT
                or type(quote.get('decimal_price')) not in (int, float)
                or not math.isfinite(quote['decimal_price']) or not 1 < quote['decimal_price'] <= 1000
                or any(not isinstance(quote.get(k), str) or not quote[k] for k in IDENTITY_FIELDS)
                or quote['fixture_id'] != fixture['fixture_id']
                or quote['home_team'] != fixture['home_team'] or quote['away_team'] != fixture['away_team']
                or utc(quote['kickoff']) != utc(fixture['kickoff'])):
            raise ValueError('INVALID_NATIVE_PRODUCT')
        observed = utc(quote['quote_observed_at'])
        if observed > self.cutoff or observed >= utc(quote['kickoff'])-timedelta(minutes=15):
            raise ValueError('QUOTE_CLOCK_OR_LOCKOUT')
        source = {'provider':'the_stats_api', 'content_hash':quote['quote_source_sha256'],
                  'source_url':quote['quote_source_id'], 'observed_at':quote['quote_observed_at']}
        key = source_key(source)
        if key in self.failed:
            raise ValueError('REJECTED_SOURCE')
        if key not in self.cache:
            row = self.db.execute("SELECT * FROM raw_snapshots WHERE content_hash=? AND provider='the_stats_api'",
                                  (source['content_hash'],)).fetchone()
            if row is None:
                raise ValueError('MISSING_RAW_SOURCE')
            if (len(self.cache)+len(self.failed) >= MAX_SOURCES or row['size_bytes'] > 2_000_000
                    or self.bytes_read+row['size_bytes'] > MAX_BYTES):
                raise OverflowError('SOURCE_READ_LIMIT')
            self.failed.add(key)
            self.bytes_read += row['size_bytes']
            body, proof = read_source_raw(self.db, dict(row), self.root, self.cutoff, source)
            if utc(proof['times']['observed_at']) != observed:
                raise ValueError('EXACT_OBSERVATION_REQUIRED')
            self.cache[key] = body['data'], proof
            self.failed.remove(key)
        data, proof = self.cache[key]
        _raw_price(data, quote)
        return deepcopy(proof)


def review_prospective_movement(root, fixtures, candidates, models, options, *, as_of):
    """Annotate a detached source view and bind existing option legs, never cards.

    Absence of a real elapsed 24h observation interval cannot produce a positive
    review, even when a baseline happens to be 24h old at the decision cutoff.
    """
    cutoff, root = utc(as_of), Path(root).resolve()
    if (not isinstance(fixtures, list) or len(fixtures) > 1000
            or not isinstance(candidates, list) or len(candidates) > 10000
            or not isinstance(models, dict) or not isinstance(options, dict)
            or any(not isinstance(rows, list) for rows in options.values())
            or sum(len(rows) for rows in options.values()) > 10000):
        raise ValueError('BOUNDED_MOVEMENT_REVIEW_REQUIRED')
    original = canonical_sha256([fixtures, candidates, models, options])
    result = {'version':VERSION, 'mode':'SHADOW', 'status':'MISSING', 'as_of':cutoff.isoformat(),
        'decision_effect':'NONE', 'live_promoted':False, 'production_writes':0,
        'external_requests':0, 'additional_model_fits':0, 'new_grok_calls':0,
        'provider_quote_clock_known':False, 'quote_assessments':[], 'option_leg_bindings':[],
        'history':{'snapshots_scanned':0, 'verified_quote_occurrences':0, 'source_receipts':[]},
        'gaps':{}, 'actual_elapsed_24h_pairs':0, 'models_sha256':canonical_sha256(models)}
    result['implementation_sha256'] = sha256(Path(__file__).read_bytes()).hexdigest()
    history_path = root/'data/arthur/prospective-odds/shadow-monitor.sqlite3'
    source_path = root/'data/goal_analysis.sqlite3'
    issues = Counter()
    try:
        ids = Counter(f.get('fixture_id') for f in fixtures if isinstance(f, dict))
        targets = {f['fixture_id']:f for f in fixtures if isinstance(f, dict)
            and ids[f.get('fixture_id')] == 1 and utc(f['kickoff']) > cutoff+timedelta(minutes=15)}
        runs, history_count, snapshot_count, packed_bytes = [], 0, 0, 0
        prices, receipts, baseline_proofs = [], {}, {}
        if not source_path.is_file():
            raise ValueError('MISSING_SOURCE_DATABASE')
        with closing(_open(source_path)) as db:
            reader = _Reader(db, root, cutoff)
            if not history_path.is_file():
                issues['MISSING_PROSPECTIVE_HISTORY'] += 1
            if history_path.is_file():
                with closing(_open(history_path)) as history:
                    window = ((cutoff-timedelta(hours=48)).isoformat(),cutoff.isoformat())
                    size = history.execute('SELECT count(*),coalesce(sum(length(CAST(quotes_json AS BLOB))),0) '
                        'FROM monitor_snapshots WHERE observed_at>=? AND observed_at<=?',window).fetchone()
                    if size[0] > MAX_SNAPSHOTS or size[1] > MAX_BYTES:
                        raise OverflowError('SNAPSHOT_READ_LIMIT')
                    rows = history.execute('SELECT snapshot_id,query_key,provider,observed_at, '
                        'CASE WHEN length(CAST(quotes_json AS BLOB))<=? THEN quotes_json END AS quotes_json '
                        'FROM monitor_snapshots WHERE observed_at>=? '
                        'AND observed_at<=? ORDER BY observed_at,snapshot_id LIMIT ?',
                        (MAX_BYTES,*window,MAX_SNAPSHOTS+1))
                    rows = list(rows)
                if len(rows) > MAX_SNAPSHOTS:
                    raise OverflowError('SNAPSHOT_READ_LIMIT')
                for row in rows:
                    if row['quotes_json'] is None:
                        raise OverflowError('SNAPSHOT_BYTE_LIMIT')
                    packed_bytes += len(row['quotes_json'].encode())
                    if packed_bytes > MAX_BYTES:
                        raise OverflowError('SNAPSHOT_BYTE_LIMIT')
                    quotes = json.loads(row['quotes_json'])
                    if not isinstance(quotes, list):
                        raise TypeError('INVALID_SNAPSHOT_QUOTES')
                    snapshot_count += 1
                    history_count += len(quotes)
                    if history_count > MAX_QUOTES:
                        raise OverflowError('QUOTE_READ_LIMIT')
                    if (row['provider'] != 'the_stats_api' or
                            canonical_sha256([row['provider'],row['query_key'],row['observed_at'],quotes]) != row['snapshot_id']):
                        raise ValueError('SNAPSHOT_HASH_CONFLICT')
                    accepted, available = [], utc(row['observed_at'])
                    for quote in quotes:
                        if not isinstance(quote, dict):
                            raise TypeError('INVALID_SNAPSHOT_QUOTE')
                        if quote.get('fixture_id') not in targets:
                            continue
                        try:
                            if utc(quote['quote_observed_at']) > utc(row['observed_at']):
                                raise ValueError('QUOTE_AFTER_SNAPSHOT')
                            proof = reader.quote(quote, targets[quote['fixture_id']])
                            available = max(available, utc(proof['times']['available_at']))
                            accepted.append(deepcopy(quote))
                            baseline_proofs.setdefault((tuple(quote[k] for k in IDENTITY_FIELDS),
                                utc(quote['quote_observed_at'])),deepcopy(proof))
                        except OverflowError:
                            raise
                        except (ValueError, TypeError, KeyError, ArithmeticError, OSError) as error:
                            issues[type(error).__name__] += 1
                    runs.append({'run_id':'prospective-shadow-'+row['snapshot_id'],
                        'observed_at':row['observed_at'], 'finished_at':available.isoformat(),
                        'odds_history_snapshot':{'quotes':accepted}})
            aliases = Counter(q.get('candidate_id') for q in candidates if isinstance(q, dict))
            for quote in candidates:
                try:
                    if (not isinstance(quote, dict) or not isinstance(quote.get('candidate_id'), str)
                            or aliases[quote['candidate_id']] != 1 or quote.get('fixture_id') not in targets):
                        raise ValueError('AMBIGUOUS_OR_UNSCOPED_CURRENT_QUOTE')
                    if not timedelta(0) <= cutoff-utc(quote['quote_observed_at']) <= timedelta(minutes=30):
                        raise ValueError('STALE_CURRENT_PRICE')
                    proof = reader.quote(quote, targets[quote['fixture_id']])
                    prices.append(deepcopy(quote)); receipts[quote['candidate_id']] = proof
                except OverflowError:
                    raise
                except (ValueError, TypeError, KeyError, ArithmeticError, OSError) as error:
                    issues[type(error).__name__] += 1
            annotated = annotate_odds_movement({'candidates':prices}, runs, cutoff)['candidates']
            for quote in annotated:
                move = quote['odds_movement']
                if 'baseline_price' in move and not 24 <= move['window_hours'] <= 48:
                    quote['odds_movement'] = {'status':'INSUFFICIENT_ELAPSED_HISTORY',
                        'observed_as_of':cutoff.isoformat(), 'time_basis':'LOCAL_SNAPSHOT_OBSERVATIONS'}
                elif 'baseline_price' in move:
                    result['actual_elapsed_24h_pairs'] += 1
            result['history'] = {'snapshots_scanned':snapshot_count,
                'verified_quote_occurrences':sum(len(run['odds_history_snapshot']['quotes']) for run in runs),
                'source_receipts':[deepcopy(v[1]) for v in reader.cache.values()],
                'source_bytes_read':reader.bytes_read, 'history_runs_sha256':canonical_sha256(runs)}
            result['quote_assessments'] = [{'candidate_id':q['candidate_id'], 'fixture_id':q['fixture_id'],
                'odds_movement':deepcopy(q['odds_movement']), 'current_receipt':receipts[q['candidate_id']],
                'baseline_receipt':deepcopy(baseline_proofs.get((tuple(q[k] for k in IDENTITY_FIELDS),
                    utc(q['odds_movement']['baseline_observed_at']))))
                    if q['odds_movement'].get('baseline_observed_at') else None}
                for q in annotated]
            by_id = {q['candidate_id']:q for q in result['quote_assessments']}
            for profile, rows in options.items():
                for index, option in enumerate(rows):
                    for leg in option['recommendation']['legs']:
                        assessment = by_id.get(leg['source_candidate_id'])
                        predictions = [p for p in models.get(profile,{}).get('predictions',[])
                            if p.get('fixture_id') == leg['fixture_id']]
                        result['option_leg_bindings'].append({'profile_id':profile, 'option_index':index,
                            'leg_sha256':canonical_sha256(leg), 'fixture_id':leg['fixture_id'],
                            'source_candidate_id':leg['source_candidate_id'],
                            'status':'USED_REVIEW' if assessment is not None and len(predictions)==1 else 'MISSING',
                            'assessment':deepcopy(assessment),
                            'existing_native_prediction_sha256':canonical_sha256(predictions[0]) if len(predictions)==1 else None,
                            'decision_effect':'NONE'})
            result['status'] = 'USED_REVIEW' if annotated else 'MISSING'
        result['gaps'] = dict(issues)
    except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error):
        result.update(status='UNAVAILABLE', quote_assessments=[], option_leg_bindings=[],
            actual_elapsed_24h_pairs=0, gaps={'SOURCE_REVIEW_FAILED':1})
    if original != canonical_sha256([fixtures, candidates, models, options]):
        raise ValueError('MOVEMENT_REVIEW_INPUT_MUTATED')
    return result
