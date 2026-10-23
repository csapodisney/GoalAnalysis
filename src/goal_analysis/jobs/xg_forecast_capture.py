"""Prospective raw xG forecasts with actual committed-presence receipts.

Separate population/model; no imported predictions, odds, LLM, ensemble or LIVE.
The reader reconstructs consumed facts and recomputes the fixed mathematics.
"""
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from time import monotonic

from goal_analysis.jobs.native_forecast_capture import (
    MARKETS,
    _files,
    _new,
    _read,
    _results,
    _verify_files,
)
from goal_analysis.jobs.xg_shadow import _evidence, _normalize, inspect_xg_rates
from goal_analysis.jobs.xg_specialist_shadow import _targets
from goal_analysis.learning.calibration_sources import Sources
from goal_analysis.models.xg_ensemble import XG_VERSION, predict_xg
from goal_analysis.models.xg_rates import fit_xg_rates
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.leakage import require_known_before
from goal_analysis.normalization.training_datasets import sha, snapshot
from goal_analysis.storage.training_datasets import DatasetStore

VERSION = 'raw-xg-prospective-forecast-capture-v1'
POPULATION = 'ALL_NATIVE_RAW_XG_MARKETS'
POLICY = {'enabled': True, 'mode': 'SHADOW', 'max_seconds': 10}
MATH_FILES = ('models/xg_rates.py', 'models/xg_ensemble.py', 'normalization/training_datasets.py')


def enabled(root):
    path = Path(root)/'config/xg-forecast-capture.json'
    return path.is_file() and path.stat().st_size < 1000 and sha(json.loads(path.read_bytes())) == sha(POLICY)


def _math():
    root = Path(__file__).resolve().parents[1]
    return {name: sha256((root/name).read_bytes()).hexdigest() for name in MATH_FILES}


def _work(root, run_id):
    root = Path(root).resolve()
    if not isinstance(run_id, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,100}', run_id):
        raise ValueError('INVALID_XG_FORECAST_RUN')
    work = root/'data/arthur/xg-forecast-capture'/run_id
    if not work.resolve().is_relative_to(root):
        raise ValueError('XG_FORECAST_PATH_ESCAPE')
    return root, work


def _targets_by_id(root, fixtures, cutoff):
    if not isinstance(fixtures, list) or len(fixtures) > 1000:
        raise ValueError('XG_FORECAST_TARGET_BUDGET')
    by_id = {}
    for fixture in fixtures:
        if fixture['fixture_id'] in by_id:
            raise ValueError('DUPLICATE_XG_FORECAST_TARGET')
        by_id[fixture['fixture_id']] = fixture
    targets, proofs, issues = _targets(root, by_id, cutoff) if by_id else ({}, {}, {})
    targets = {fid: target for fid, target in targets.items()
               if utc(target['kickoff']) > cutoff+timedelta(minutes=15)}
    return by_id, targets, proofs, issues


def _bounded_prefix(root, cutoff):
    path = root/'data/goal_analysis.sqlite3'
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        count, total, largest = db.execute("""SELECT count(*),coalesce(sum(s.size_bytes),0),
            coalesce(max(s.size_bytes),0) FROM raw_snapshots s JOIN temporal_receipts t
            ON t.namespace='raw' AND t.source_key=s.content_hash
            WHERE s.provider='the_stats_api' AND julianday(s.fetched_at)<=julianday(?)
            AND julianday(t.available_at)<=julianday(?) AND (s.source_url LIKE '%/football/matches?%'
            OR s.source_url LIKE '%/football/matches/mt_%/stats')""", (cutoff.isoformat(),cutoff.isoformat())).fetchone()
    if count > 2000 or total > 128_000_000 or largest > 4*1024*1024:
        raise ValueError('XG_FORECAST_SOURCE_BUDGET')


def _rows(fit, by_id, targets, proofs, cutoff, completed):
    cohort = sha({'population': POPULATION, 'model_version': XG_VERSION,
                  'math': _math(), 'recipe': fit['recipe']})
    receipts = sorted({proof['receipt_id'] for line in fit['dataset']['lineage']
                       for proof in line['source_receipts']})
    rows, gaps = [], Counter()
    for fid, target in sorted(targets.items()):
        prediction = predict_xg(fit, target, as_of=cutoff)
        if prediction['status'] != 'RAW_XG_PROBABILITIES_SHADOW':
            gaps[prediction['status']] += 1
            continue
        for market, selection, key, complement in MARKETS:
            probability = prediction['probabilities'][key]
            rows.append({'profile_id': 'native_shadow_xg', 'strategy_version': 'raw-xg-'+cohort,
                'model_version': XG_VERSION, 'fixture_id': fid, 'market_key': market,
                'selection_key': selection, 'period': 'FULL_TIME',
                'settlement': 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES',
                'line': '2.5' if market == 'totals_2_5' else None,
                'probability': 1-probability if complement else probability,
                'probability_kind': 'ORIGINAL_NUMERIC_MODEL_ESTIMATE', 'calibrated': False,
                'model_as_of': cutoff.isoformat(), 'created_at': completed.isoformat(),
                'kickoff': by_id[fid]['kickoff'], 'dataset_ids': [fit['dataset']['manifest']['dataset_id']],
                'source_receipt_ids': sorted(set(receipts+[proofs[fid]['receipt_id']])),
                'run_sha256': fit['result_sha256'], 'population': POPULATION,
                'target': by_id[fid]})
    return rows, dict(gaps)


def _summary(body, receipt, created):
    eligible = sum(utc(r['kickoff']) > utc(receipt['available_at'])+timedelta(minutes=15) for r in body['rows'])
    return {'version': VERSION, 'status': 'CAPTURED_SHADOW' if eligible else 'GAPS',
        'new_forecasts': len(body['rows']) if created else 0, 'archived_forecasts': len(body['rows']),
        'prospectively_eligible_forecasts': eligible, 'receipt_id': receipt['receipt_id'],
        'available_at': receipt['available_at'], 'gaps': body['gaps'], 'population': POPULATION,
        'additional_xg_rate_fits': 1 if created else 0, 'additional_shared_model_fits': 0,
        'external_requests': 0, 'llm_requests': 0, 'ledger_writes': 0, 'live_decision_effect': False}


def capture(root, run_id, report_hash, fixtures, *, max_seconds=10, clock=lambda: datetime.now(UTC)):
    if type(max_seconds) is not int or not 1 <= max_seconds <= 10 or not re.fullmatch('[a-f0-9]{64}', str(report_hash)):
        raise ValueError('INVALID_XG_FORECAST_CAPTURE_SCOPE')
    root, work = _work(root, run_id); deadline = monotonic()+max_seconds
    if work.exists():
        if not (work/'receipt.json').is_file():
            return {'status': 'UNCERTAIN_PREVIOUS_CAPTURE', 'new_forecasts': 0, 'automatic_retry': False}
        body, receipt = _bundle(root, work, utc(clock()))
        if body['report_sha256'] != report_hash:
            raise ValueError('XG_FORECAST_REPORT_CONFLICT')
        return _summary(body, receipt, False)
    # Input identity validation happens before the durable claim.
    cutoff = utc(clock()); by_id, targets, proofs, target_issues = _targets_by_id(root, fixtures, cutoff)
    _bounded_prefix(root, cutoff)
    work.mkdir(parents=True, exist_ok=False)
    _new(work/'claim.json', {'version': VERSION, 'run_id': run_id, 'started_at': cutoff.isoformat()})
    view = inspect_xg_rates(root, as_of=cutoff)
    if view['status'] == 'LIMIT_REACHED':
        raise ValueError('XG_FORECAST_INCOMPLETE_SOURCE_BUDGET')
    fit = view['fit']; completed = utc(clock())
    rows, gaps = _rows(fit, by_id, targets, proofs, cutoff, completed)
    if completed < cutoff or monotonic() >= deadline:
        raise ValueError('XG_FORECAST_CAPTURE_CLOCK_OR_TIME_BUDGET')
    store = DatasetStore(work/'datasets'); store.put(fit['dataset'])
    _new(work/'inputs.json', {'fixtures': fixtures, 'envelopes': view['envelopes']})
    _new(work/'fit.json', fit)
    files = _files(root, [work/'inputs.json',work/'fit.json',store.path(fit['dataset']['manifest']['dataset_id'])])
    body = {'version': VERSION, 'run_id': run_id, 'report_sha256': report_hash,
        'as_of': cutoff.isoformat(), 'computed_at': completed.isoformat(), 'prepared_at': utc(clock()).isoformat(),
        'rows': rows, 'gaps': gaps, 'target_issues': target_issues,
        'dataset_id': fit['dataset']['manifest']['dataset_id'], 'math_sha256': _math(),
        'capture_implementation_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
        'files': files, 'population': POPULATION, 'live_decision_effect': False}
    _new(work/'capture.json', body)
    committed = _read(work/'capture.json'); _verify_files(root, files)
    available = utc(clock())
    if sha(committed) != sha(body) or not cutoff <= completed <= utc(body['prepared_at']) <= available or monotonic() >= deadline:
        raise ValueError('XG_FORECAST_COMMIT_CLOCK_OR_TIME_BUDGET')
    receipt = {'version': VERSION, 'run_id': run_id, 'capture_sha256': sha(committed),
        'available_at': available.isoformat(), 'basis': 'COMMITTED_FORECAST_PRESENCE_UPPER_BOUND'}
    receipt['receipt_id'] = sha(receipt); _new(work/'receipt.json', receipt)
    return _summary(body, receipt, True)


def _bundle(root, work, cutoff):
    body, receipt, claim = (_read(work/name) for name in ('capture.json','receipt.json','claim.json'))
    if (not body['version'] == receipt['version'] == claim['version'] == VERSION
            or not body['run_id'] == receipt['run_id'] == claim['run_id'] == work.name
            or sha(body) != receipt['capture_sha256']
            or receipt['receipt_id'] != sha({k:v for k,v in receipt.items() if k != 'receipt_id'})
            or receipt['basis'] != 'COMMITTED_FORECAST_PRESENCE_UPPER_BOUND'
            or body['population'] != POPULATION or body['live_decision_effect'] is not False
            or body['math_sha256'] != _math()
            or not utc(claim['started_at']) == utc(body['as_of']) <= utc(body['computed_at'])
            <= utc(body['prepared_at']) <= utc(receipt['available_at']) <= cutoff):
        raise ValueError('INVALID_XG_FORECAST_RECEIPT_OR_MATH')
    expected = {p.relative_to(root).as_posix() for p in (work/'inputs.json', work/'fit.json',
        DatasetStore(work/'datasets').path(body['dataset_id']))}
    if set(body['files']) != expected:
        raise ValueError('XG_FORECAST_FILE_MANIFEST_INCOMPLETE')
    _verify_files(root, body['files'])
    return body, receipt


def _reconstruct(root, db, work, body, cache, deadline):
    inputs = _read(work/'inputs.json'); original = _read(work/'fit.json'); cutoff = utc(body['as_of'])
    dataset = DatasetStore(work/'datasets').read(body['dataset_id'])
    if sha(dataset) != sha(original['dataset']) or not isinstance(inputs['envelopes'], list) or len(inputs['envelopes']) > 1000:
        raise ValueError('INVALID_OR_OVERSIZED_XG_FORECAST_DATASET')
    envelopes = []
    for envelope in inputs['envelopes']:
        if monotonic() >= deadline:
            raise ValueError('XG_FORECAST_READER_TIME_BUDGET')
        proofs = envelope['source_receipts']
        if not isinstance(proofs, list) or len(proofs) != 2:
            raise ValueError('EXACT_XG_SOURCE_PAIR_REQUIRED')
        data = []
        for proof in proofs:
            key = proof['source_key']
            if key not in cache:
                row = db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?', (key,)).fetchone()
                if row is None or not 0 < row['size_bytes'] <= 4*1024*1024 or len(cache) >= 2000:
                    raise ValueError('XG_FORECAST_READER_SOURCE_BUDGET')
                if sum(item[2] for item in cache.values())+row['size_bytes'] > 128_000_000:
                    raise ValueError('XG_FORECAST_READER_BYTE_BUDGET')
                raw, actual = _evidence(db, dict(row), root, cutoff)
                cache[key] = raw, actual, row['size_bytes']
            raw, actual, _ = cache[key]
            require_known_before(actual['times'], as_of=cutoff)
            if sha(actual) != sha(proof):
                raise ValueError('XG_FORECAST_SOURCE_RECEIPT_MISMATCH')
            data.append(raw)
        matches = data[0]
        if not isinstance(matches, list) or len(matches) > 10000:
            raise ValueError('XG_FORECAST_METADATA_ROW_BUDGET')
        exact = [m for m in matches if isinstance(m, dict) and m.get('id') == envelope['payload']['fixture_id']]
        if len(exact) != 1:
            raise ValueError('XG_FORECAST_MATCH_NOT_UNIQUE')
        rebuilt = _normalize(exact[0], data[1], *proofs)
        if sha(rebuilt) != sha(envelope):
            raise ValueError('XG_FORECAST_FACT_DIFFERS_FROM_PHYSICAL_BYTES')
        envelopes.append(rebuilt)
    fit = fit_xg_rates(envelopes, as_of=cutoff, recipe=original['recipe'])
    if sha(fit) != sha(original):
        raise ValueError('XG_FORECAST_RATE_FIT_DIFFERENCE')
    by_id, targets, proofs, _ = _targets_by_id(root, inputs['fixtures'], cutoff)
    rows, _ = _rows(fit, by_id, targets, proofs, cutoff, utc(body['computed_at']))
    if sha(rows) != sha(body['rows']):
        raise ValueError('XG_FORECAST_PREDICTION_DIFFERENCE')
    return rows


def read_forecasts(root, *, as_of, max_seconds=30):
    if type(max_seconds) is not int or not 1 <= max_seconds <= 30:
        raise ValueError('INVALID_XG_FORECAST_READER_BUDGET')
    root = Path(root).resolve(); cutoff = utc(as_of); deadline = monotonic()+max_seconds
    folder = root/'data/arthur/xg-forecast-capture'; paths = sorted(folder.iterdir()) if folder.is_dir() else []
    if not folder.resolve().is_relative_to(root) or len(paths) > 512:
        raise ValueError('XG_FORECAST_RUN_OR_PATH_BUDGET')
    rows, refs, gaps, cache, total = [], [], Counter(), {}, 0
    if paths:
        with closing(sqlite3.connect((root/'data/goal_analysis.sqlite3').as_uri()+'?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row; db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            sources = Sources(root, db, deadline=deadline)
            for work in paths:
                try:
                    if monotonic() >= deadline:
                        raise ValueError('XG_FORECAST_READER_TIME_BUDGET')
                    if _work(root, work.name)[1].resolve() != work.resolve():
                        raise ValueError('XG_FORECAST_READER_PATH_CONFLICT')
                    body, receipt = _bundle(root, work, cutoff)
                    total += sum(meta['bytes'] for meta in body['files'].values())+(work/'capture.json').stat().st_size
                    if total > 128_000_000:
                        raise ValueError('XG_FORECAST_ARCHIVE_BYTE_BUDGET')
                    actual = _reconstruct(root, db, work, body, cache, deadline)
                    batch = []
                    for original in actual:
                        if utc(original['kickoff']) <= utc(receipt['available_at'])+timedelta(minutes=15):
                            gaps['CAPTURED_AFTER_PREMATCH_LOCKOUT'] += 1
                            continue
                        row = {k:v for k,v in original.items() if k != 'target'}
                        row.update(saved_at=receipt['available_at'], forecast_id=sha([receipt['receipt_id'], sha(original)]),
                            source_receipt_ids=sorted(set(row['source_receipt_ids']+[receipt['receipt_id']])),
                            result_receipts=_results(sources, original, cutoff))
                        batch.append(row)
                    if len(rows)+len(batch) > 10000:
                        raise ValueError('XG_FORECAST_READER_ROW_BUDGET')
                    rows.extend(batch)
                    refs.append({'run_id':work.name, 'receipt_id':receipt['receipt_id'], 'available_at':receipt['available_at']})
                except (KeyError, ValueError, TypeError, OSError, sqlite3.DatabaseError) as error:
                    if str(error).endswith('_BUDGET'):
                        raise
                    gaps[str(error) if isinstance(error, ValueError) else type(error).__name__] += 1
    dataset = snapshot(rows, role='raw_xg_captured_calibration_forecasts', as_of=cutoff,
        recipe={'version':VERSION, 'population':POPULATION}, lineage={'captures':refs,'gaps':dict(gaps)})
    return {'dataset':dataset, 'capture_receipts':refs, 'eligible_forecasts':len(rows),
        'labeled_forecasts':sum(bool(r['result_receipts']) for r in rows), 'gaps':dict(gaps),
        'external_requests':0, 'ledger_writes':0}
