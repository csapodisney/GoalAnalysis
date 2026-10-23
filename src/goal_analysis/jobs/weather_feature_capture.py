"""Prospective native baseline / physical forecast-weather feature pairs.

No weather probability, fit, ranking, outcome selection or publication authority.
Readers recheck original model, raw weather, venue and availability evidence.
"""
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic

from goal_analysis.agents.weather_expert import POLICY as WEATHER_POLICY
from goal_analysis.jobs import native_forecast_capture as native
from goal_analysis.jobs.native_weather_shadow import inspect_native_weather
from goal_analysis.jobs.portfolio import RunLock
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha, snapshot

VERSION = 'native-baseline-weather-features-shadow-v1'
POLICY = {'enabled': True, 'mode': 'SHADOW', 'max_seconds': 30}
POPULATION = 'ALL_ADMITTED_NATIVE_BASELINE_FORECASTS_WITH_VERIFIED_WEATHER'
MAX_CAPTURES = 128
IDENTITY = ('fixture_id', 'provider_fixture_id', 'provider_league_id', 'provider_season_id',
            'provider_home_team_id', 'provider_away_team_id', 'kickoff')


def enabled(root):
    path = Path(root)/'config/weather-feature-capture.json'
    return path.is_file() and path.stat().st_size < 4096 and sha(native._read(path)) == sha(POLICY)


def _paths(root, run_id, capture_id):
    root, _, _ = native._paths(root, run_id)
    native._paths(root, capture_id)  # Same strictly bounded, path-safe identifier contract.
    folder = root/'data/arthur/weather-feature-capture'
    work = folder/capture_id
    if not work.resolve().is_relative_to(root):
        raise ValueError('WEATHER_FEATURE_PATH_ESCAPE')
    return root, folder, work


def _budget(deadline):
    remaining = min(30, int(deadline-monotonic()))
    if remaining < 1:
        raise ValueError('WEATHER_FEATURE_TIME_BUDGET')
    return remaining


def _source_pairs(root, run_id, report_hash, cutoff, deadline, current_targets=None):
    _, _, work = native._paths(root, run_id)
    body, receipt = native._bundle(root, work, cutoff)
    if body['report_sha256'] != report_hash:
        raise ValueError('WEATHER_FEATURE_BASELINE_REPORT_CONFLICT')
    # The existing strict reader validates actual models/datasets/raw receipts.
    admitted = native.read_forecasts(root, as_of=cutoff, max_seconds=_budget(deadline))
    rows = [r for r in admitted['dataset']['rows'] if receipt['receipt_id'] in r['source_receipt_ids']]
    fixtures = {}
    for row in body['rows']:
        fid = row['fixture_id']
        target = row['target']
        if fid in fixtures and sha(fixtures[fid]) != sha(target):
            raise ValueError('WEATHER_FEATURE_AMBIGUOUS_BASELINE_TARGET')
        fixtures[fid] = target
    eligible = {r['fixture_id'] for r in rows if utc(r['kickoff']) > cutoff+timedelta(minutes=15)}
    current = fixtures if current_targets is None else {f['fixture_id']:f for f in current_targets}
    if current_targets is not None and len(current) != len(current_targets):
        raise ValueError('DUPLICATE_WEATHER_FEATURE_CURRENT_TARGET')
    identity_conflicts = {fid for fid in eligible if fid in current and any(
        (utc(current[fid].get(k)) != utc(fixtures[fid][k]) if k == 'kickoff'
         else current[fid].get(k) != fixtures[fid].get(k)) for k in IDENTITY)}
    targets = [current[fid] for fid in sorted(eligible) if fid in current and fid not in identity_conflicts]
    _budget(deadline)
    audit = inspect_native_weather(root, targets, as_of=cutoff, _deadline=deadline)
    contexts = {r['fixture_id']: r for r in audit['rows']}
    pairs, gaps = [], Counter()
    if not rows:
        gaps['NO_VERIFIED_BASELINE_FORECASTS'] += 1
        gaps.update({'BASELINE:'+k:v for k,v in admitted['gaps'].items()})
    for baseline in rows:
        _budget(deadline)
        fid = baseline['fixture_id']
        if fid not in eligible:
            gaps['CURRENT_PREMATCH_LOCKOUT'] += 1
            continue
        if fid not in current:
            gaps['MISSING_CURRENT_WEATHER_TARGET_INPUT'] += 1
            continue
        if fid in identity_conflicts:
            gaps['CURRENT_WEATHER_TARGET_IDENTITY_CONFLICT'] += 1
            continue
        context = contexts[fid]
        if context['status'] != 'FORECAST_CONTEXT_VERIFIED_SHADOW':
            gaps[context['reason']] += 1
            continue
        # The weather archive preserves exact fixture ID/kickoff. The existing
        # strict context reader separately binds the FULL identity to actual
        # fresh native raw metadata, then verifies the venue/forecast coordinates.
        # Do not pretend discarded provider fields existed in weather's payload.
        if any(fixtures[fid].get(k) is None for k in IDENTITY):
            gaps['WEATHER_FEATURE_FULL_TARGET_IDENTITY_MISSING'] += 1
            continue
        original = {k:v for k,v in baseline.items() if k != 'result_receipts'}
        pairs.append({'fixture_id': fid, 'kickoff': baseline['kickoff'],
            'baseline': original, 'target': {k:fixtures[fid][k] for k in IDENTITY},
            'weather_context': context, 'weather_context_sha256': sha(context),
            'feature_use': 'RECORDED_FOR_FUTURE_FIXED_MODEL_SHADOW_VALIDATION',
            'weather_model_probability': None, 'probability_adjustment': 0,
            'ranking_multiplier': 1., 'live_decision_effect': False})
    refs = {**native._files(root, [work/'capture.json',work/'claim.json',
        work/(native.RECOVERY_FILE if receipt['basis'] == native.RECOVERY_BASIS else 'receipt.json')]),
        **body['files']}
    if receipt['basis'] == native.RECOVERY_BASIS:
        refs.update(native._files(root, [work/native.RECOVERY_CLAIM]))
    return pairs, dict(gaps), refs, len(rows)


def _completion(pair, available):
    context = pair['weather_context']
    return (utc(pair['baseline']['saved_at']) <= available
        and utc(pair['kickoff']) > available+timedelta(minutes=15)
        and 0 <= (available-utc(context['forecast']['observed_at'])).total_seconds()
            <= WEATHER_POLICY['forecast_freshness_hours']*3600
        and 0 <= (available-utc(context['target_receipt']['times']['observed_at'])).total_seconds() <= 1800)


def _bundle(root, work, cutoff):
    body, receipt, claim = (native._read(work/name) for name in ('capture.json','receipt.json','claim.json'))
    if (not body['version'] == receipt['version'] == claim['version'] == VERSION
            or not body['capture_id'] == receipt['capture_id'] == claim['capture_id'] == work.name
            or body['population'] != POPULATION or body['live_decision_effect'] is not False
            or sha(body) != receipt['capture_sha256'] or sha(claim) != receipt['claim_sha256']
            or receipt['basis'] != 'COMMITTED_WEATHER_FEATURE_PAIR_PRESENCE_UPPER_BOUND'
            or receipt['receipt_id'] != sha({k:v for k,v in receipt.items() if k != 'receipt_id'})
            or not utc(claim['started_at']) == utc(body['as_of']) <= utc(body['prepared_at'])
            <= utc(receipt['available_at']) <= cutoff):
        raise ValueError('INVALID_WEATHER_FEATURE_RECEIPT')
    native._verify_files(root, body['files'])
    return body, receipt


def _summary(body, receipt, created):
    count = sum(_completion(p, utc(receipt['available_at'])) for p in body['rows'])
    return {'version': VERSION, 'status': 'CAPTURED_FEATURES_SHADOW' if count else 'GAPS',
        'capture_id': body['capture_id'], 'new_pairs': count if created else 0,
        'eligible_feature_pairs': count, 'baseline_forecasts_inspected': body['baseline_forecasts_inspected'],
        'receipt_id': receipt['receipt_id'], 'available_at': receipt['available_at'], 'gaps': body['gaps'],
        'external_requests': 0, 'additional_model_fits': 0, 'llm_requests': 0,
        'ledger_writes': 0, 'live_decision_effect': False, 'weather_probability_forecasts': 0}


def capture(root, run_id, report_hash, *, fixtures=None, capture_id=None, max_seconds=30, clock=lambda: datetime.now(UTC)):
    if type(max_seconds) is not int or not 1 <= max_seconds <= 30:
        raise ValueError('INVALID_WEATHER_FEATURE_TIME_BUDGET')
    if not isinstance(report_hash, str) or re.fullmatch('[0-9a-f]{64}', report_hash) is None:
        raise ValueError('INVALID_WEATHER_FEATURE_REPORT_HASH')
    if fixtures is not None and (not isinstance(fixtures, list) or len(fixtures) > 1000):
        raise ValueError('BOUNDED_CURRENT_WEATHER_FEATURE_TARGETS_REQUIRED')
    # Accept only target identity / actual source pointers. Caller model numbers,
    # score hints and free-form weather values are never inputs to this layer.
    current = None if fixtures is None else [{k:f[k] for k in (*IDENTITY,'fixture_source',
        '_venue_source','source_provider','neutral_venue') if k in f} for f in fixtures]
    root, folder, work = _paths(root, run_id, run_id if capture_id is None else capture_id)
    if not enabled(root):
        return {'status': 'DISABLED', 'external_requests': 0, 'additional_model_fits': 0}
    deadline = monotonic()+max_seconds
    folder.mkdir(parents=True, exist_ok=True)
    with RunLock(folder/'capture.lock'):
        if work.exists():
            if not (work/'receipt.json').is_file():
                return {'status': 'UNCERTAIN_PREVIOUS_CAPTURE', 'new_pairs': 0, 'automatic_retry': False}
            body, receipt = _bundle(root, work, utc(clock()))
            if body['run_id'] != run_id or body['report_sha256'] != report_hash:
                raise ValueError('WEATHER_FEATURE_CAPTURE_CONFLICT')
            return _summary(body, receipt, False)
        if sum(p.is_dir() for p in folder.iterdir()) >= MAX_CAPTURES:
            raise ValueError('WEATHER_FEATURE_CAPTURE_COUNT_BUDGET')
        cutoff = utc(clock()); work.mkdir()
        claim = {'version': VERSION, 'capture_id': work.name, 'started_at': cutoff.isoformat()}
        native._new(work/'claim.json', claim)
        rows, gaps, files, count = _source_pairs(root, run_id, report_hash, cutoff, deadline, current)
        prepared = utc(clock()); _budget(deadline)
        if prepared < cutoff:
            raise ValueError('WEATHER_FEATURE_CLOCK_ROLLBACK')
        body = {'version': VERSION, 'capture_id': work.name, 'run_id': run_id, 'report_sha256': report_hash,
            'as_of': cutoff.isoformat(), 'prepared_at': prepared.isoformat(), 'rows': rows, 'gaps': gaps,
            'files': files, 'baseline_forecasts_inspected': count, 'population': POPULATION,
            'target_inputs': current, 'live_decision_effect': False}
        native._new(work/'capture.json', body)
        actual = native._read(work/'capture.json'); native._verify_files(root, files)
        available = utc(clock()); _budget(deadline)
        if sha(actual) != sha(body) or available < prepared:
            raise ValueError('WEATHER_FEATURE_COMMIT_OR_CLOCK_CONFLICT')
        receipt = {'version': VERSION, 'capture_id': work.name, 'capture_sha256': sha(actual),
            'claim_sha256': sha(claim), 'available_at': available.isoformat(),
            'basis': 'COMMITTED_WEATHER_FEATURE_PAIR_PRESENCE_UPPER_BOUND'}
        receipt['receipt_id'] = sha(receipt); native._new(work/'receipt.json', receipt)
        return _summary(body, receipt, True)


def read_pairs(root, *, as_of, max_seconds=30):
    if type(max_seconds) is not int or not 1 <= max_seconds <= 30:
        raise ValueError('INVALID_WEATHER_FEATURE_TIME_BUDGET')
    root = Path(root).resolve(); cutoff = utc(as_of); deadline = monotonic()+max_seconds
    folder = root/'data/arthur/weather-feature-capture'
    if not folder.resolve().is_relative_to(root):
        raise ValueError('WEATHER_FEATURE_PATH_ESCAPE')
    works = sorted(p for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []
    if len(works) > MAX_CAPTURES:
        raise ValueError('WEATHER_FEATURE_CAPTURE_COUNT_BUDGET')
    rows, refs, gaps, cache, total = [], [], Counter(), {}, 0
    for work in works:
        try:
            _budget(deadline)
            _paths(root, work.name, work.name)
            body, receipt = _bundle(root, work, cutoff)
            _paths(root, body['run_id'], work.name)
            total += sum(p.stat().st_size for p in work.glob('*.json'))
            if total > 128_000_000:
                raise ValueError('WEATHER_FEATURE_READER_BYTE_BUDGET')
            key = body['run_id'], body['report_sha256'], body['as_of'],sha(body['target_inputs'])
            if key not in cache:
                cache[key] = _source_pairs(root, key[0], key[1], utc(key[2]), deadline,body['target_inputs'])
            actual, issues, files, count = cache[key]
            if sha(actual) != sha(body['rows']) or issues != body['gaps'] or files != body['files'] or count != body['baseline_forecasts_inspected']:
                raise ValueError('WEATHER_FEATURE_PHYSICAL_SOURCE_CONFLICT')
            for pair in actual:
                if not _completion(pair, utc(receipt['available_at'])):
                    gaps['EXPIRED_AT_FEATURE_COMMIT'] += 1
                    continue
                rows.append({**pair, 'pair_id': sha([receipt['receipt_id'],sha(pair)]),
                    'saved_at': receipt['available_at'], 'pair_receipt_id': receipt['receipt_id']})
            refs.append({'capture_id':work.name,'receipt_id':receipt['receipt_id'],'available_at':receipt['available_at']})
            gaps.update(issues)
            if len(rows) > 10000:
                raise ValueError('WEATHER_FEATURE_READER_ROW_BUDGET')
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
            if str(error).endswith('_BUDGET'):
                raise
            gaps[str(error) if isinstance(error, ValueError) else type(error).__name__] += 1
    data = snapshot(rows, role='native_baseline_weather_feature_pairs_shadow', as_of=cutoff,
        recipe={'version':VERSION,'population':POPULATION,'weather_probability_forecasts':0},
        lineage={'captures':refs,'gaps':dict(gaps)})
    return {'dataset':data,'eligible_feature_pairs':len(rows),'gaps':dict(gaps),
        'external_requests':0,'additional_model_fits':0,'production_writes':0,
        'weather_probability_forecasts':0,'heldout_validation_complete':False,'live_decision_effect':False}
