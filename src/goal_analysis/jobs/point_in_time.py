"""Read-only, receipt-bound result inputs for strict SHADOW model replay.

No provider calls, model fitting, timestamp backdating or betting path switch.
The original numerical history remains separate until a validated promotion.
"""
import json
from bisect import bisect_right
from collections import Counter
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.leakage import LookAheadError, model_inputs
from goal_analysis.normalization.training_datasets import sha, snapshot
from goal_analysis.storage import Database
from goal_analysis.storage.canonical_registry import digest
from goal_analysis.storage.historical_results import HistoricalResults
from goal_analysis.storage.temporal import TemporalStore, _source_hash

VERSION = 'strict-result-model-view-v1'


def result_model_view(root, *, as_of, limit=10000):
    """Return only selected regulation results with a verified local witness.

    A selected primary result lacking a witness remains a gap. Names, assumed
    publication delays, ingestion alone and a fresh download cannot fill one.
    A truncated candidate query is marked LIMIT_REACHED, never called complete.
    """
    cutoff = utc(as_of)
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError('INVALID_POINT_IN_TIME_LIMIT')
    root = Path(root).resolve()
    path = root / 'data/goal_analysis.sqlite3'
    counts, envelopes = Counter(), []
    if not path.is_file():
        return {'version': VERSION, 'mode': 'SHADOW', 'status': 'MISSING_SOURCE',
                'records': [], 'envelopes': [], 'external_requests': 0}
    database = Database(path)
    with closing(database.connect()) as db:
        db.execute('PRAGMA query_only=ON')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'temporal_receipts', 'historical_result_versions', 'historical_result_links'} <= tables:
        return {'version': VERSION, 'mode': 'SHADOW', 'status': 'MISSING_PROOF_TABLES',
                'records': [], 'envelopes': [], 'external_requests': 0}
    view = HistoricalResults(database).view(as_of=cutoff)
    selected_results = []
    for match in view['matches']:
        selected = match['score_resolutions'].get('REGULATION', {})
        if selected.get('status') != 'AVAILABLE':
            counts['NO_UNAMBIGUOUS_REGULATION_RESULT'] += 1
            continue
        identifier = selected['selected_version_id']
        chosen = next(r for r in match['source_records'] if r['version_id'] == identifier)
        if chosen['record']['provider'] not in {'the_stats_api', 'api_football'}:
            counts['UNSUPPORTED_NUMERICAL_PROVIDER_IDENTITY'] += 1
            continue
        if chosen['record']['kickoff'] is None:
            counts['UNPROVEN_EXACT_RESULT'] += 1
            continue
        selected_results.append((chosen['record']['kickoff'], chosen['record']['provider'],
                                 chosen['record']['provider_id'], identifier))
    # Select applicable identities BEFORE bounding receipts. Otherwise a large
    # CSV archive can occupy every receipt slot and starve all native model IDs.
    selected_results.sort(reverse=True)  # most recent events first, never sort by scores/hashes
    truncated = len(selected_results) > limit
    if truncated:
        counts['CANDIDATE_LIMIT'] = len(selected_results) - limit
    selected_results = [r[-1] for r in selected_results[:limit]]
    receipts = TemporalStore(database, root / 'data/raw').records(
        'result', cutoff, limit=limit, result_version_ids=selected_results)
    proof_by_version = {r['source_key']: r for r in receipts}
    with closing(database.connect()) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        for identifier in selected_results:
            proof = proof_by_version.get(identifier)
            if proof is None:
                counts['MISSING_AVAILABLE_AT_WITNESS'] += 1
                continue
            source = db.execute('SELECT rowid AS _sequence,* FROM historical_result_versions WHERE version_id=?',
                                (identifier,)).fetchone()
            if source is None or _source_hash(dict(source)) != proof['source_sha256']:
                counts['SOURCE_CHANGED_AFTER_PROOF_READ'] += 1
                continue
            row = json.loads(source['record_json'])
            if digest(row) != identifier or row['kickoff'] is None:
                counts['UNPROVEN_EXACT_RESULT'] += 1
                continue
            if row['provider'] not in {'the_stats_api', 'api_football'}:
                counts['UNSUPPORTED_NUMERICAL_PROVIDER_IDENTITY'] += 1
                continue
            payload = {'fixture_id': row['provider_id'], 'league_id': row['competition_id'],
                'home_team_id': row['home_id'], 'away_team_id': row['away_id'],
                'kickoff': row['kickoff'], 'season': row['season_id'], 'status': 'FT',
                'full_time_home': row['home_goals'], 'full_time_away': row['away_goals'],
                'provider': row['provider'], 'observed_at': row['observed_at'],
                'source_id': 'historical_result_versions:' + identifier,
                **{k: proof['times'][k] for k in ('available_at', 'ingested_at', 'published_at')}}
            envelopes.append({'record_id': identifier, 'payload': payload,
                'payload_sha256': sha(payload), 'source_sha256': proof['source_sha256'],
                'receipt_id': proof['receipt_id'], 'times': proof['times']})
    records = model_inputs(envelopes, as_of=cutoff)
    dataset = snapshot(records, role='strict_result_model_input', as_of=cutoff,
        recipe={'version': VERSION, 'mode': 'SHADOW', 'availability': 'VERIFIED_LOCAL_WITNESS'},
        lineage=[{k:v for k,v in envelope.items() if k not in {'payload', 'payload_sha256'}}
                 for envelope in envelopes])
    return {'version': VERSION, 'mode': 'SHADOW', 'effective_data_path': 'LEGACY_UNCHANGED',
        'status': 'LIMIT_REACHED' if truncated else 'GAPS' if counts else 'AVAILABLE',
        'as_of': cutoff.isoformat(), 'records': records, 'envelopes': envelopes,
        'gaps': dict(counts), 'receipt_count': len(receipts), 'dataset': dataset,
        'external_requests': 0, 'ledger_writes': 0}


def require_chronological_availability(records):
    """Certify the Pi feature timeline, not merely today's outer fit cutoff.

    The existing Pi release rule is kickoff+3h. Every result must already have
    been proven available at the first subsequent feature clock that could use
    it. A late backfill/correction cannot masquerade as an archival holdout.
    This conservative test may withhold a sparse league; it never guesses.
    """
    leagues = {}
    for row in records:
        leagues.setdefault(row['league_id'], []).append(row)
    for rows in leagues.values():
        clocks = sorted({utc(row['kickoff']) for row in rows})
        for row in rows:
            release = utc(row['kickoff']) + timedelta(hours=3)
            next_index = bisect_right(clocks, release)
            if next_index < len(clocks) and (
                    utc(row['available_at']) > clocks[next_index]
                    or utc(row['observed_at']) > clocks[next_index]):
                raise LookAheadError('UNPROVEN_CHRONOLOGICAL_FEATURE_AVAILABILITY')


def replay_models(root, fixture_envelopes, *, as_of, profiles=('gauss', 'kepler', 'laplace'), predictor=None):
    """Explicit SHADOW entry point. All data is admitted before any model call.

    Callers supply verified source-bound fixture envelopes. The source view
    independently verifies result receipts. Historical Pi evaluation is
    withheld if its earlier feature clocks lack actual availability evidence.
    The production portfolio does not call or promote this experimental path.
    """
    from goal_analysis.models import predict_models

    fixtures = model_inputs(fixture_envelopes, as_of=as_of)
    source = result_model_view(root, as_of=as_of)
    records = model_inputs(source['envelopes'], as_of=as_of)
    enabled = tuple(dict.fromkeys(p.lower() for p in profiles))
    if not set(enabled) <= {'gauss', 'kepler', 'laplace'}:
        raise ValueError('UNSUPPORTED_POINT_IN_TIME_MODEL')
    excluded = {}
    if 'kepler' in enabled:
        try:
            require_chronological_availability(records)
        except LookAheadError as error:
            excluded['kepler'] = {'profile_id': 'kepler', 'status': 'UNPROVEN_POINT_IN_TIME_HISTORY',
                                  'predictions': [], 'reason': str(error)}
            enabled = tuple(p for p in enabled if p != 'kepler')
    results = (predictor or predict_models)(fixtures, records, utc(as_of), enabled_profiles=enabled) if enabled else {}
    return {'mode': 'SHADOW', 'version': VERSION, 'effective_data_path': 'LEGACY_UNCHANGED',
            'source_status': source['status'], 'numeric_models': {**results, **excluded},
            'input_dataset_id': source.get('dataset', {}).get('manifest', {}).get('dataset_id'),
            'external_requests': 0, 'ledger_writes': 0}
