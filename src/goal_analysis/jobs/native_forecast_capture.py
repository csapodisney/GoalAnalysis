"""Prospective own-presence receipts for existing native SHADOW forecasts.

No prediction reconstruction, network, fitting, price invention or wager. A
late capture preserves historical bytes but cannot become a prematch sample.
"""
import json
import os
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from time import monotonic
from uuid import uuid4

from goal_analysis.jobs.portfolio import RunLock
from goal_analysis.jobs.the_stats_results import _result
from goal_analysis.learning.calibration_sources import Sources
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import encoded, sha, snapshot
from goal_analysis.storage.canonical_registry import digest
from goal_analysis.storage.historical_results import identity
from goal_analysis.storage.historical_results import validate as validate_result
from goal_analysis.storage.portfolio_ledger import _leg_result

VERSION = 'native-prospective-forecast-capture-v1'
POLICY = {'enabled': True, 'mode': 'SHADOW', 'max_seconds': 10}
POPULATION = 'ALL_NATIVE_NUMERIC_MARKETS'
MARKETS = (('h2h', 'home', 'home', False), ('h2h', 'draw', 'draw', False),
           ('h2h', 'away', 'away', False), ('totals_2_5', 'over', 'over25', False),
           ('totals_2_5', 'under', 'under25', False), ('btts', 'yes', 'btts_yes', False),
           ('btts', 'no', 'btts_yes', True))
MAX_BYTES = 64 * 1024 * 1024
RECOVERY_VERSION = 'native-forecast-presence-recovery-v1'
RECOVERY_BASIS = 'RECOVERED_COMMITTED_FORECAST_PRESENCE_UPPER_BOUND'
RECOVERY_FILE = 'presence-recovery.json'
RECOVERY_CLAIM = 'presence-recovery-claim.json'


def enabled(root):
    path = Path(root)/'config/native-calibration-capture.json'
    return path.is_file() and path.stat().st_size < 4096 and sha(json.loads(path.read_bytes())) == sha(POLICY)


def _paths(root, run_id):
    root = Path(root).resolve()
    if not isinstance(run_id, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,100}', run_id):
        raise ValueError('INVALID_FORECAST_CAPTURE_RUN')
    output = root/'data/arthur/native-shadow'/run_id
    work = root/'data/arthur/native-calibration-capture'/run_id
    if not output.resolve().is_relative_to(root) or not work.resolve().is_relative_to(root):
        raise ValueError('FORECAST_CAPTURE_PATH_ESCAPE')
    return root, output, work


def _read(path):
    if not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError('FORECAST_CAPTURE_FILE_BUDGET')
    return json.loads(path.read_text('utf-8-sig'))


def _new(path, value):
    data = encoded(value)+b'\n'
    if len(data) > MAX_BYTES:
        raise ValueError('FORECAST_CAPTURE_FILE_BUDGET')
    temp = path.with_name('.'+uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.link(temp, path)  # exclusive atomic publication; never overwrite
    finally:
        temp.unlink(missing_ok=True)


def _files(root, paths):
    result, total = {}, 0
    for path in sorted(set(paths)):
        if not path.resolve().is_relative_to(root):
            raise ValueError('FORECAST_INPUT_PATH_ESCAPE')
        size = path.stat().st_size; total += size
        if not 0 < size <= MAX_BYTES or total > 128_000_000:
            raise ValueError('FORECAST_INPUT_TOTAL_BYTE_BUDGET')
        result[path.relative_to(root).as_posix()] = {'sha256': sha256(path.read_bytes()).hexdigest(), 'bytes': size}
    return result


def _verify_files(root, files):
    paths = [root/name for name in files]
    if _files(root, paths) != files:
        raise ValueError('ORIGINAL_FORECAST_INPUT_CHANGED')


def _inputs(root, output, run_id, report_hash):
    staging = root/'data/arthur/nightly-staging'/f'{run_id}.json'
    paths = [staging, output/'analysis.json', output/'summary.json', output/'settings.json']
    original = _files(root, paths)
    bundle, analysis, summary, settings = [_read(p) for p in paths]
    if (sha(bundle['artifacts']['report']) != report_hash or summary['report_sha256'] != report_hash
            or summary['analysis_sha256'] != sha(analysis) or summary['run_id'] != run_id
            or summary['mode'] != 'SHADOW' or summary['live_decision_effect'] is not False
            or analysis['mode'] != 'SHADOW' or analysis['live_promotion'] is not False
            or utc(summary['as_of']) != utc(analysis['as_of'])
            or utc(summary['as_of']) > utc(summary['completed_at'])
            or not re.fullmatch('[0-9a-f]{64}', str(summary['implementation_sha256']))):
        raise ValueError('ORIGINAL_NATIVE_FORECAST_BINDING_MISMATCH')
    return analysis, summary, settings, original


def _extract(root, output, analysis, summary, settings, sources):
    """Keep a separate all-forecast population, never fabricate a ticket."""
    cutoff = utc(analysis['as_of']); models = analysis['numeric_models']
    fixtures = analysis['source_view'].get('fixtures', [])
    if not isinstance(fixtures, list) or len(fixtures) > 1000:
        raise ValueError('NATIVE_FORECAST_TARGET_BUDGET')
    by_id = {}
    for fixture in fixtures:
        if fixture['fixture_id'] in by_id:
            raise ValueError('AMBIGUOUS_NATIVE_FORECAST_TARGET')
        by_id[fixture['fixture_id']] = fixture
    rows, gaps, files = [], Counter(), []
    for profile in ('gauss', 'kepler', 'laplace'):
        model = models.get(profile) or {}
        predictions = model.get('predictions', [])
        if not predictions:
            continue
        if not isinstance(predictions, list) or len(predictions) > 1000:
            raise ValueError('NATIVE_FORECAST_PREDICTION_BUDGET')
        if profile not in settings['enabled_profiles'] or model.get('status') != 'READY':
            gaps['MODEL_NOT_READY_OR_ENABLED'] += len(predictions)
            continue
        seen = Counter(p.get('fixture_id') for p in predictions)
        for index, prediction in enumerate(predictions):
            try:
                fixture = by_id[prediction['fixture_id']]
                if seen[prediction['fixture_id']] != 1:
                    raise ValueError('DUPLICATE_NATIVE_FORECAST_PREDICTION')
                spec = prediction['model_details']; version = spec['version']
                if utc(spec['cutoff']) != cutoff:
                    raise ValueError('NATIVE_FORECAST_MODEL_CUTOFF_MISMATCH')
                if fixture['fixture_id'] != 'the_stats_api:'+fixture['provider_fixture_id']:
                    raise ValueError('NATIVE_FORECAST_PROVIDER_ID_MISMATCH')
                target = sources.target(fixture, cutoff)
                ids, receipts = sources.training(model, version, cutoff)
                captured = model['training_datasets']
                fitted = {r['dataset_id'] for r in captured['uses'] if r['status'] == 'FIT_CALL_RETURNED'}
                recipes = [m['recipe'] for m in captured['manifests'] if m['dataset_id'] in fitted]
                # Exclude dataset IDs, dates, target IDs and outcomes from the
                # cohort: these change daily without changing the fixed logic.
                recipe_ids = sorted({sha(r) for r in recipes})
                strategy = sha({'population': POPULATION, 'contract': VERSION,
                    'worker_version': summary['version'], 'worker_implementation': summary['implementation_sha256'],
                    'model_version': version, 'model_recipes': recipe_ids, 'settings_sha256': sha(settings)})
                for did in ids:
                    files.append(output/'fit/datasets'/f'{did}.json')
                p = prediction['probabilities']
                if (not {m[2] for m in MARKETS} <= p.keys()
                        or any(type(v) not in (int, float) or not 0 <= v <= 1 for v in p.values())
                        or abs(sum(p[k] for k in ('home', 'draw', 'away'))-1) > 1e-6
                        or abs(p['over25']+p['under25']-1) > 1e-6):
                    raise ValueError('NATIVE_PROBABILITY_VECTOR_NOT_NUMERIC_OR_NORMALIZED')
                batch = []
                for market, selection, key, complement in MARKETS:
                    probability = 1-p[key] if complement else p[key]
                    row = {'profile_id': 'native_shadow_'+profile,
                        'strategy_version': 'native_shadow_'+profile+'-'+strategy,
                        'model_version': version, 'fixture_id': fixture['fixture_id'],
                        'market_key': market, 'selection_key': selection, 'period': 'FULL_TIME',
                        'settlement': 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES',
                        'line': '2.5' if market == 'totals_2_5' else None,
                        'probability': probability, 'probability_kind': 'ORIGINAL_NUMERIC_MODEL_ESTIMATE',
                        'model_as_of': cutoff.isoformat(), 'created_at': utc(summary['completed_at']).isoformat(),
                        'kickoff': fixture['kickoff'], 'dataset_ids': ids,
                        'source_receipt_ids': sorted(set(receipts+[target['receipt_id']])),
                        'run_sha256': summary['analysis_sha256'], 'population': POPULATION,
                        'prediction_path': ['numeric_models', profile, 'predictions', index, 'probabilities', key],
                        'probability_transform': 'COMPLEMENT' if complement else 'IDENTITY',
                        'target': fixture}
                    batch.append(row)
                if len(rows)+len(batch) > 10000:
                    raise ValueError('NATIVE_FORECAST_ROW_BUDGET')
                rows.extend(batch)
            except (KeyError, ValueError, TypeError, OSError, sqlite3.DatabaseError) as error:
                if str(error).endswith('_BUDGET'):
                    raise
                gaps[str(error) if isinstance(error, ValueError) else type(error).__name__] += 1
    return rows, dict(gaps), files


def capture(root, run_id, report_hash, *, max_seconds=10, clock=lambda: datetime.now(UTC)):
    if type(max_seconds) is not int or not 1 <= max_seconds <= 10:
        raise ValueError('INVALID_FORECAST_CAPTURE_TIME_BUDGET')
    root, output, work = _paths(root, run_id)
    deadline = monotonic()+max_seconds
    if work.exists():
        if not (work/'receipt.json').is_file() and not (work/RECOVERY_FILE).is_file():
            return {'status': 'UNCERTAIN_PREVIOUS_CAPTURE', 'new_forecasts': 0, 'automatic_retry': False}
        body, receipt = _bundle(root, work, utc(clock()))
        if body['report_sha256'] != report_hash:
            raise ValueError('FORECAST_CAPTURE_REPORT_CONFLICT')
        return _summary(body, receipt, created=False)
    work.mkdir(parents=True, exist_ok=False)
    started = utc(clock())
    _new(work/'claim.json', {'version': VERSION, 'run_id': run_id, 'started_at': started.isoformat()})
    analysis, summary, settings, files = _inputs(root, output, run_id, report_hash)
    if utc(summary['completed_at']) > started:
        raise ValueError('FUTURE_NATIVE_WORKER_COMPLETION')
    path = root/'data/goal_analysis.sqlite3'
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row; db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        source = Sources(root, db, dataset_root=output/'fit/datasets', deadline=deadline)
        rows, gaps, datasets = _extract(root, output, analysis, summary, settings, source)
    files.update(_files(root, datasets))
    if sum(p['bytes'] for p in files.values()) > 128_000_000:
        raise ValueError('FORECAST_INPUT_TOTAL_BYTE_BUDGET')
    prepared = utc(clock())
    if prepared < started or monotonic() >= deadline:
        raise ValueError('FORECAST_CAPTURE_CLOCK_OR_TIME_BUDGET')
    body = {'version': VERSION, 'run_id': run_id, 'report_sha256': report_hash,
        'analysis_sha256': summary['analysis_sha256'], 'as_of': summary['as_of'],
        'prepared_at': prepared.isoformat(), 'rows': rows, 'gaps': gaps, 'files': files,
        'population': POPULATION, 'live_decision_effect': False}
    _verify_files(root, files)
    _new(work/'capture.json', body)
    committed = _read(work/'capture.json')
    assert sha(committed) == sha(body)
    _verify_files(root, files)
    available = utc(clock())  # AFTER the committed read; never summary time/mtime
    if available < prepared or monotonic() >= deadline:
        raise ValueError('FORECAST_CAPTURE_CLOCK_OR_TIME_BUDGET')
    receipt = {'version': VERSION, 'run_id': run_id, 'capture_sha256': sha(committed),
        'available_at': available.isoformat(), 'basis': 'COMMITTED_FORECAST_PRESENCE_UPPER_BOUND'}
    receipt['receipt_id'] = sha(receipt)
    _new(work/'receipt.json', receipt)
    return _summary(body, receipt, created=True)


def _bundle(root, work, cutoff):
    body, claim = (_read(work/name) for name in ('capture.json', 'claim.json'))
    recovered = (work/RECOVERY_FILE).exists()
    if recovered and (work/'receipt.json').exists():
        raise ValueError('CONFLICTING_NATIVE_FORECAST_RECEIPTS')
    receipt = _read(work/(RECOVERY_FILE if recovered else 'receipt.json'))
    if (body['version'] != VERSION or receipt['version'] != VERSION or claim['version'] != VERSION
            or not body['run_id'] == receipt['run_id'] == claim['run_id'] == work.name
            or sha(body) != receipt['capture_sha256']
            or receipt['receipt_id'] != sha({k: v for k, v in receipt.items() if k != 'receipt_id'})
            or receipt['basis'] != (RECOVERY_BASIS if recovered else 'COMMITTED_FORECAST_PRESENCE_UPPER_BOUND')
            or not utc(claim['started_at']) <= utc(body['prepared_at']) <= utc(receipt['available_at']) <= cutoff):
        raise ValueError('INVALID_NATIVE_FORECAST_CAPTURE_RECEIPT')
    if recovered:
        recovery_claim = _read(work/RECOVERY_CLAIM)
        if (receipt.get('recovery_version') != RECOVERY_VERSION
                or recovery_claim.get('version') != RECOVERY_VERSION or recovery_claim.get('run_id') != work.name
                or receipt.get('recovery_claim_sha256') != sha(recovery_claim)
                or receipt.get('original_claim_sha256') != sha(claim)
                or receipt.get('original_files') != _files(root, [work/'capture.json', work/'claim.json'])
                or recovery_claim.get('original_files') != receipt['original_files']
                or not utc(body['prepared_at']) <= utc(recovery_claim['started_at'])
                    <= utc(receipt['verification_started_at']) <= utc(receipt['available_at'])
                or receipt.get('live_decision_effect') is not False):
            raise ValueError('INVALID_NATIVE_FORECAST_RECOVERY_RECEIPT')
    _verify_files(root, body['files'])
    return body, receipt


def recover_presence(root, run_id, report_hash, *, max_seconds=10, clock=lambda: datetime.now(UTC)):
    """Explicit bounded attestation of existing bytes, never a fit/capture retry.

    The old missing receipt remains missing. A new own-presence clock governs
    eligibility; reading forecasts never invokes this writer. The normal
    uncertain-capture path still cannot automatically repeat any generation.
    """
    if type(max_seconds) is not int or not 1 <= max_seconds <= 10:
        raise ValueError('INVALID_FORECAST_RECOVERY_TIME_BUDGET')
    root, output, work = _paths(root, run_id)
    if not enabled(root):
        return {'status': 'DISABLED', 'external_requests': 0, 'additional_model_fits': 0}
    if not work.is_dir():
        return {'status': 'NO_PARTIAL_CAPTURE', 'external_requests': 0, 'additional_model_fits': 0}
    deadline = monotonic()+max_seconds
    with RunLock(work/'presence-recovery.lock'):
        if (work/'receipt.json').exists() or (work/RECOVERY_FILE).exists():
            body, receipt = _bundle(root, work, utc(clock()))
            if body['report_sha256'] != report_hash:
                raise ValueError('FORECAST_CAPTURE_REPORT_CONFLICT')
            return _summary(body, receipt, created=False)
        started = utc(clock())
        body, claim = (_read(work/name) for name in ('capture.json', 'claim.json'))
        original_files = _files(root, [work/'capture.json', work/'claim.json'])
        if (body['version'] != VERSION or claim['version'] != VERSION
                or not body['run_id'] == claim['run_id'] == run_id
                or body['report_sha256'] != report_hash or body['population'] != POPULATION
                or body['live_decision_effect'] is not False
                or not utc(claim['started_at']) <= utc(body['prepared_at']) <= started):
            raise ValueError('INVALID_PARTIAL_NATIVE_FORECAST_CAPTURE')
        if not (work/RECOVERY_CLAIM).exists():
            _new(work/RECOVERY_CLAIM, {'version': RECOVERY_VERSION, 'run_id': run_id,
                'started_at': started.isoformat(), 'original_files': original_files})
        recovery_claim = _read(work/RECOVERY_CLAIM)
        if (recovery_claim.get('version') != RECOVERY_VERSION or recovery_claim.get('run_id') != run_id
                or recovery_claim.get('original_files') != original_files
                or not utc(body['prepared_at']) <= utc(recovery_claim['started_at']) <= started):
            raise ValueError('INVALID_NATIVE_FORECAST_RECOVERY_CLAIM')
        analysis, summary, settings, files = _inputs(root, output, run_id, report_hash)
        if (body['analysis_sha256'] != summary['analysis_sha256'] or utc(body['as_of']) != utc(analysis['as_of'])
                or utc(summary['completed_at']) > utc(claim['started_at'])):
            raise ValueError('PARTIAL_NATIVE_FORECAST_INPUT_DIFFERENCE')
        with closing(sqlite3.connect((root/'data/goal_analysis.sqlite3').as_uri()+'?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row; db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            source = Sources(root, db, dataset_root=output/'fit/datasets', deadline=deadline)
            rows, gaps, datasets = _extract(root, output, analysis, summary, settings, source)
        files.update(_files(root, datasets))
        if sha(rows) != sha(body['rows']) or gaps != body['gaps'] or files != body['files']:
            raise ValueError('PARTIAL_NATIVE_FORECAST_SOURCE_DIFFERENCE')
        _verify_files(root, files)
        if original_files != _files(root, [work/'capture.json', work/'claim.json']):
            raise ValueError('PARTIAL_NATIVE_FORECAST_BYTES_CHANGED')
        committed = _read(work/'capture.json')
        if sha(committed) != sha(body):
            raise ValueError('PARTIAL_NATIVE_FORECAST_BYTES_CHANGED')
        available = utc(clock())  # Real new clock AFTER existing committed bytes/source verification.
        if available < started or monotonic() >= deadline:
            raise ValueError('FORECAST_RECOVERY_CLOCK_OR_TIME_BUDGET')
        receipt = {'version': VERSION, 'recovery_version': RECOVERY_VERSION, 'run_id': run_id,
            'capture_sha256': sha(committed), 'original_claim_sha256': sha(claim),
            'recovery_claim_sha256': sha(recovery_claim),
            'original_files': original_files, 'verification_started_at': started.isoformat(),
            'available_at': available.isoformat(), 'basis': RECOVERY_BASIS, 'live_decision_effect': False}
        receipt['receipt_id'] = sha(receipt)
        _new(work/RECOVERY_FILE, receipt)
        if _read(work/RECOVERY_FILE) != receipt:
            raise ValueError('FORECAST_RECOVERY_COMMIT_DIFFERENCE')
        return {**_summary(body, receipt, created=False), 'new_presence_receipt': True}


def _summary(body, receipt, *, created):
    available = utc(receipt['available_at'])
    eligible = sum(utc(r['kickoff']) > available+timedelta(minutes=15) for r in body['rows'])
    return {'version': VERSION, 'status': 'CAPTURED_SHADOW' if eligible else 'GAPS',
        'new_capture': created, 'new_forecasts': len(body['rows']) if created else 0,
        'archived_forecasts': len(body['rows']), 'prospectively_eligible_forecasts': eligible,
        'locked_forecasts_not_admitted': len(body['rows'])-eligible, 'gaps': body['gaps'],
        'receipt_id': receipt['receipt_id'], 'available_at': receipt['available_at'],
        'population': POPULATION, 'external_requests': 0, 'additional_model_fits': 0,
        'ledger_writes': 0, 'live_decision_effect': False,
        'presence_recovered': receipt['basis'] == RECOVERY_BASIS}


def _results(sources, row, cutoff):
    db = sources.db; target = row['target']; native_id = target['provider_fixture_id']
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'historical_result_matches', 'historical_result_versions'} <= tables:
        return []
    records = db.execute('''SELECT v.* FROM historical_result_versions v
        JOIN historical_result_matches m USING(match_id)
        WHERE m.provider='the_stats_api' AND m.provider_id=?
        AND julianday(v.observed_at)<=julianday(?) AND julianday(v.ingested_at)<=julianday(?)
        ORDER BY v.observed_at,v.version_id LIMIT 101''', (native_id, cutoff.isoformat(), cutoff.isoformat())).fetchall()
    if len(records) > 100:
        raise ValueError('NATIVE_FORECAST_RESULT_BUDGET')
    receipts = []
    for stored in records:
        record = validate_result(json.loads(stored['record_json']), now=cutoff)
        if digest(record) != stored['version_id'] or identity(record) != stored['identity_sha256']:
            raise ValueError('CORRUPT_NATIVE_CALIBRATION_RESULT')
        if (record['provider_id'] != native_id or record['competition_id'] != target['provider_league_id']
                or record['season_id'] != target['provider_season_id']
                or record['home_id'] != target['provider_home_team_id']
                or record['away_id'] != target['provider_away_team_id']
                or utc(record['kickoff']) != utc(target['kickoff']) or record['score_scope'] != 'REGULATION'
                or utc(stored['observed_at']) != utc(record['observed_at'])
                or utc(stored['ingested_at']) < utc(stored['observed_at'])):
            raise ValueError('NATIVE_RESULT_CALIBRATION_IDENTITY_CONFLICT')
        source = {'content_hash': record['source_sha256'], 'source_url': record['source_url'],
                  'observed_at': record['observed_at']}
        body, proof = sources.raw(source, cutoff)
        raw = body['data']; items = raw if isinstance(raw, list) else [raw]
        exact = [r for r in items if isinstance(r, dict) and r.get('id') == native_id]
        if len(exact) != 1 or sha(exact[0]) != sha(record['raw']):
            raise ValueError('NATIVE_CALIBRATION_RESULT_RAW_CONFLICT')
        payload = _result(exact[0], source)
        scores = payload['score']['fulltime']
        if (record['home_goals'], record['away_goals']) != (scores['home'], scores['away']):
            raise ValueError('NATIVE_RESULT_NORMALIZED_SCORE_CONFLICT')
        sources.result(payload, {**target, **{k: row[k] for k in ('period','market_key','selection_key')}}, cutoff)
        measured = _leg_result(row, payload)
        if measured['outcome'] not in {'WON', 'LOST'}:
            continue
        available = max(utc(stored['ingested_at']), utc(record['observed_at']), utc(proof['times']['available_at']))
        receipts.append({'outcome': int(measured['outcome'] == 'WON'),
            'receipt_id': sha([VERSION, stored['version_id'], proof['receipt_id'], stored['ingested_at']]),
            'source_sha256': stored['version_id'], 'observed_at': record['observed_at'],
            'available_at': available.isoformat()})
    return receipts


def read_forecasts(root, *, as_of, max_seconds=30):
    if type(max_seconds) is not int or not 1 <= max_seconds <= 30:
        raise ValueError('INVALID_NATIVE_CAPTURE_READER_TIME_BUDGET')
    deadline = monotonic()+max_seconds
    root = Path(root).resolve(); cutoff = utc(as_of)
    folder = root/'data/arthur/native-calibration-capture'
    if not folder.resolve().is_relative_to(root):
        raise ValueError('NATIVE_FORECAST_READER_PATH_ESCAPE')
    paths = sorted(folder.iterdir()) if folder.is_dir() else []
    if len(paths) > 512:
        raise ValueError('NATIVE_CAPTURE_RUN_BUDGET')
    rows, refs, gaps, total = [], [], Counter(), 0
    path = root/'data/goal_analysis.sqlite3'
    if paths and path.is_file():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row; db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            for work in paths:
                try:
                    if monotonic() >= deadline:
                        raise ValueError('NATIVE_CAPTURE_READER_TIME_BUDGET')
                    _, output, safe = _paths(root, work.name)
                    if safe.resolve() != work.resolve():
                        raise ValueError('FORECAST_CAPTURE_READER_PATH_CONFLICT')
                    body, receipt = _bundle(root, work, cutoff)
                    total += sum(p.stat().st_size for p in work.glob('*.json'))
                    if total > 128_000_000:
                        raise ValueError('NATIVE_CAPTURE_TOTAL_BYTE_BUDGET')
                    analysis, summary, settings, _ = _inputs(root, output, work.name, body['report_sha256'])
                    source = Sources(root, db, dataset_root=output/'fit/datasets', deadline=deadline)
                    actual, _, _ = _extract(root, output, analysis, summary, settings, source)
                    if sha(actual) != sha(body['rows']):
                        raise ValueError('SEALED_NATIVE_FORECAST_SOURCE_DIFFERENCE')
                    batch = []
                    for original in actual:
                        if utc(original['kickoff']) <= utc(receipt['available_at'])+timedelta(minutes=15):
                            gaps['CAPTURED_AFTER_PREMATCH_LOCKOUT'] += 1
                            continue
                        row = {k: v for k, v in original.items() if k != 'target'}
                        row['saved_at'] = receipt['available_at']
                        row['source_receipt_ids'] = sorted(set(row['source_receipt_ids']+[receipt['receipt_id']]))
                        row['forecast_id'] = sha([receipt['receipt_id'], sha(original)])
                        row['result_receipts'] = _results(source, original, cutoff)
                        batch.append(row)
                    if len(rows)+len(batch) > 10000:
                        raise ValueError('NATIVE_FORECAST_READER_ROW_BUDGET')
                    rows.extend(batch)
                    ref = {'run_id': work.name, 'receipt_id': receipt['receipt_id'],
                        'capture_sha256': receipt['capture_sha256'], 'available_at': receipt['available_at']}
                    if receipt['basis'] == RECOVERY_BASIS:
                        ref['presence_recovered'] = True
                    refs.append(ref)
                except (KeyError, ValueError, TypeError, OSError, sqlite3.DatabaseError) as error:
                    if str(error).endswith('_BUDGET'):
                        raise
                    gaps[str(error) if isinstance(error, ValueError) else type(error).__name__] += 1
    dataset = snapshot(rows, role='native_captured_calibration_forecasts', as_of=cutoff,
        recipe={'version': VERSION, 'selection': POPULATION}, lineage={'captures': refs, 'gaps': dict(gaps)})
    return {'dataset': dataset, 'capture_receipts': refs, 'eligible_forecasts': len(rows),
        'labeled_forecasts': sum(bool(r['result_receipts']) for r in rows), 'gaps': dict(gaps),
        'external_requests': 0, 'ledger_writes': 0}
