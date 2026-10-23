"""Read original selected forecasts and verified sources; fit a separate artifact.

No model refit, network, data admission, ledger migration, profile edit or LIVE
replacement. Legacy snapshots without original datasets remain NOT_FIT.
"""
import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from goal_analysis.jobs.ticket_evidence import PROBABILITY_KEYS
from goal_analysis.learning.calibration import GROUP, MAX_ROWS, POLICY, walk_forward
from goal_analysis.learning.calibration_sources import Sources
from goal_analysis.learning.forecast_quality import _numeric, _probability
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha, snapshot, validate
from goal_analysis.storage.portfolio_ledger import _hash, _leg_result
from goal_analysis.storage.training_datasets import DatasetStore

VERSION = 'ledger-calibration-shadow-v1'
SETTLEMENTS = {'REGULATION_TIME', 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES'}


def _original(card, originals, run_id):
    # The append-only ledger assigns IDs/run_id and a publication timestamp.
    # All decision content must still match the sealed original report exactly.
    generated = {'recommendation_id', 'run_id', 'created_at'}
    if card.get('run_id', run_id) != run_id:
        raise ValueError('RECOMMENDATION_RUN_CONFLICT')
    if card.get('recommendation_id') != _hash([run_id, card['profile_id']])[:24]:
        raise ValueError('RECOMMENDATION_ID_NOT_BOUND_TO_RUN_AND_PROFILE')
    decisions = {k: v for k, v in card.items() if k not in generated}
    matches = [o for o in originals if {k: v for k, v in o.items() if k not in generated} == decisions]
    unique = {sha(o): o for o in matches}
    if len(unique) != 1:
        raise ValueError('NOT_EXACT_ORIGINAL_RECOMMENDATION')
    return next(iter(unique.values()))


def _cohort(leg, profile, run):
    details = leg.get('model_details') or {}
    if not details.get('shared_model_probabilities'):
        return details.get('version', 'UNKNOWN_LEGACY')
    versions = {}
    for name in details['shared_model_probabilities']:
        matches = [p for p in run['numeric_models'][name]['predictions'] if p['fixture_id'] == leg['fixture_id']]
        if len(matches) != 1:
            raise ValueError('AMBIGUOUS_SHARED_MODEL_VERSION')
        versions[name] = matches[0]['model_details']['version']
    return 'shared-' + sha({'versions': versions, 'weights': details['shared_model_weights'], 'method': details['method']})


def _model(leg, profile, run, sources):
    details = leg.get('model_details') or {}
    p = leg.get('model_probability')
    if not _probability(p) or not _numeric({**leg, 'model_version': details.get('version')}):
        raise ValueError('HEURISTIC_OR_MISSING_ORIGINAL_NUMERIC_PROBABILITY')
    components = details.get('shared_model_probabilities') or {profile: p}
    key = PROBABILITY_KEYS.get((leg['market_key'], leg['selection_key']))
    ids, receipts, versions, cutoffs = set(), set(), {}, set()
    for name, expected in components.items():
        model = run['numeric_models'][name]
        matches = [v for v in model['predictions'] if v['fixture_id'] == leg['fixture_id']]
        if len(matches) != 1:
            raise ValueError('ORIGINAL_NUMERIC_PREDICTION_NOT_UNIQUE')
        prediction = matches[0]
        actual = prediction['probabilities'].get(key)
        if actual is None and key == 'btts_no':
            actual = 1 - prediction['probabilities']['btts_yes']
        if not _probability(actual) or abs(actual - expected) > 1e-12:
            raise ValueError('SELECTED_PROBABILITY_DIFFERS_FROM_ORIGINAL_MODEL')
        spec = prediction['model_details']
        version, cutoff = spec['version'], utc(spec['cutoff'])
        if not isinstance(version, str) or version.startswith('UNKNOWN'):
            raise ValueError('ORIGINAL_MODEL_VERSION_REQUIRED')
        dids, rids = sources.training(model, version, cutoff)
        ids.update(dids); receipts.update(rids); versions[name] = version; cutoffs.add(cutoff)
    if len(cutoffs) != 1:
        raise ValueError('MIXED_COMPONENT_MODEL_CUTOFFS')
    shared = bool(details.get('shared_model_probabilities'))
    version = _cohort(leg, profile, run)
    if not shared and details.get('version') != versions[profile]:
        raise ValueError('SELECTED_MODEL_VERSION_CONFLICT')
    return version, next(iter(cutoffs)), sorted(ids), sorted(receipts)


def admitted_forecasts(root, *, as_of):
    """Bounded RO snapshot of original recommendations, not today's relabeling."""
    root, cutoff = Path(root).resolve(), utc(as_of)
    path = root/'data/arthur/ledger.sqlite3'; sports = root/'data/goal_analysis.sqlite3'
    reasons, rows, source_refs, inspected = Counter(), [], [], 0
    first_seen, conflicts, inspected_cards = {}, set(), 0
    if not path.is_file() or not sports.is_file():
        reasons['MISSING_DATABASE'] += 1
    else:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as ledger, closing(
                sqlite3.connect(sports.as_uri()+'?mode=ro', uri=True)) as db:
            ledger.row_factory = db.row_factory = sqlite3.Row
            for connection in (ledger, db):
                connection.execute('PRAGMA query_only=ON'); connection.execute('BEGIN')
            sources = Sources(root, db)
            runs = ledger.execute('SELECT * FROM portfolio_runs ORDER BY julianday(saved_at),run_id LIMIT 2001')
            for index, stored in enumerate(runs):
                if index >= 2000:
                    raise ValueError('CALIBRATION_LEDGER_RUN_BUDGET')
                try:
                    if len(stored['snapshot_json'].encode('utf-8')) > 16_000_000:
                        raise ValueError('OVERSIZED_RUN')
                    run = json.loads(stored['snapshot_json'])
                    if _hash(run) != stored['snapshot_hash'] or run['run_id'] != stored['run_id']:
                        raise ValueError('CORRUPT_RUN')
                    if utc(stored['saved_at']) > cutoff:
                        continue
                    originals = [c for k in ('recommendations', 'tickets', 'preview_tickets')
                                 for c in run.get(k, []) if isinstance(c, dict)]
                except (KeyError, TypeError, ValueError):
                    reasons['CORRUPT_ORIGINAL_RUN'] += 1
                    continue
                cards = ledger.execute('SELECT * FROM portfolio_recommendations WHERE run_id=? ORDER BY rowid LIMIT 10001',
                                       (stored['run_id'],))
                for stored_card in cards:
                    inspected_cards += 1
                    if inspected_cards > 10000:
                        raise ValueError('CALIBRATION_CARD_BUDGET')
                    try:
                        if len(stored_card['snapshot_json'].encode('utf-8')) > 2_000_000:
                            raise ValueError('OVERSIZED_ORIGINAL_CARD')
                        card = json.loads(stored_card['snapshot_json'])
                        original_card = _original(card, originals, stored['run_id'])
                    except (ValueError, TypeError, KeyError):
                        reasons['CORRUPT_OR_UNBOUND_ORIGINAL_CARD'] += 1
                        continue
                    for leg in card.get('legs', []):
                        inspected += 1
                        if inspected > MAX_ROWS:
                            raise ValueError('CALIBRATION_LEDGER_LEG_BUDGET')
                        try:
                            version = card['strategy_version']; profile = card['profile_id']
                            manifest = run['strategy_manifest']
                            if (card['strategy_manifest_sha256'] != manifest['manifest_sha256']
                                    or sha({k: v for k, v in manifest.items() if k not in {'manifest_sha256', 'profiles'}}) != manifest['manifest_sha256']
                                    or manifest['profiles'][profile]['version_id'] != version):
                                raise ValueError('ORIGINAL_PROFILE_VERSION_NOT_BOUND')
                            expected_line = '2.5' if leg['market_key'] == 'totals_2_5' else None
                            identity = (profile, version, _cohort(leg, profile, run), leg['market_key'],
                                        leg['selection_key'], leg.get('period'), leg.get('settlement'),
                                        expected_line, leg['fixture_id'])
                            at = utc(stored['saved_at'])
                            signature = sha({k: leg.get(k) for k in ('model_probability', 'model_details', 'kickoff', 'fixture_source')})
                            if identity in first_seen:
                                previous, content = first_seen[identity]
                                if at == previous and signature != content:
                                    conflicts.add(identity)
                                reasons['DUPLICATE_OR_LATER_ORIGINAL_FORECAST'] += 1
                                continue
                            first_seen[identity] = at, signature
                            if (leg.get('period') != 'FULL_TIME' or leg.get('settlement') not in SETTLEMENTS
                                    or (leg.get('market_key'), leg.get('selection_key')) not in PROBABILITY_KEYS):
                                raise ValueError('UNSUPPORTED_OR_AMBIGUOUS_CALIBRATION_PRODUCT')
                            if leg.get('line') is not None and str(leg['line']) != expected_line:
                                raise ValueError('CALIBRATION_PRODUCT_LINE_MISMATCH')
                            model_version, model_time, ids, receipts = _model(leg, profile, run, sources)
                            created = max(utc(card['created_at']), utc(original_card['created_at']))
                            finished, saved, kickoff = (utc(v) for v in (
                                run['finished_at'], stored['saved_at'], leg['kickoff']))
                            if not utc(run['observed_at']) <= model_time <= created <= finished <= saved <= cutoff or saved >= kickoff-timedelta(minutes=15):
                                raise ValueError('FORECAST_NOT_ORIGINALLY_AVAILABLE_BEFORE_LOCKOUT')
                            target = sources.target(leg, model_time)
                            receipts.append(target['receipt_id'])
                            outcomes = []
                            observations = ledger.execute('''SELECT o.id AS observation_id,o.observed_at AS available_at,
                                r.* FROM portfolio_result_observations o JOIN portfolio_results r ON r.id=o.result_id
                                WHERE o.fixture_id=? ORDER BY o.id LIMIT 101''', (leg['fixture_id'],)).fetchall()
                            if len(observations) > 100:
                                raise ValueError('CALIBRATION_RESULT_BUDGET')
                            for result in observations:
                                if utc(result['available_at']) > cutoff or utc(result['observed_at']) > cutoff:
                                    continue
                                payload = json.loads(result['payload_json'])
                                if _hash(payload) != result['payload_hash'] or result['fixture_id'] != leg['fixture_id']:
                                    raise ValueError('CORRUPT_OR_MISMATCHED_RESULT')
                                measured = _leg_result(leg, payload)
                                if measured['outcome'] not in {'WON', 'LOST'}:
                                    continue
                                proof = sources.result(payload, leg, cutoff)
                                available = max(utc(result['available_at']), utc(result['observed_at']),
                                                utc(proof['times']['available_at']))
                                if utc(result['available_at']) < utc(result['observed_at']):
                                    raise ValueError('RESULT_OBSERVATION_PRECEDES_SOURCE')
                                outcomes.append({'outcome': int(measured['outcome'] == 'WON'),
                                    'receipt_id': sha({'ledger_observation_id': result['observation_id'],
                                        'payload_hash': result['payload_hash'], 'raw_receipt_id': proof['receipt_id']}),
                                    'source_sha256': result['payload_hash'],
                                    'observed_at': result['observed_at'], 'available_at': available.isoformat()})
                            row = dict(zip(GROUP, (profile, version, model_version, leg['market_key'],
                                leg['selection_key'], leg['period'], leg['settlement'], expected_line), strict=True))
                            row.update(fixture_id=leg['fixture_id'], kickoff=kickoff.isoformat(),
                                probability=leg['model_probability'], probability_kind='ORIGINAL_NUMERIC_MODEL_ESTIMATE',
                                model_as_of=model_time.isoformat(), created_at=created.isoformat(), saved_at=saved.isoformat(),
                                run_sha256=stored['snapshot_hash'], dataset_ids=ids,
                                source_receipt_ids=sorted(set(receipts)), result_receipts=outcomes)
                            row['forecast_id'] = sha({k: v for k, v in row.items() if k != 'result_receipts'})
                            rows.append(row)
                            source_refs.append({'run_id': stored['run_id'], 'run_sha256': stored['snapshot_hash'],
                                'card_sha256': _hash(card), 'forecast_id': row['forecast_id']})
                        except (KeyError, TypeError, ValueError, OSError, sqlite3.DatabaseError) as error:
                            reason = str(error) if isinstance(error, ValueError) else (
                                'MISSING_FIELD:' + str(error.args[0]) if isinstance(error, KeyError) else type(error).__name__)
                            reasons[reason] += 1
    if conflicts:
        rows = [r for r in rows if (*tuple(r[k] for k in GROUP), r['fixture_id']) not in conflicts]
        source_refs = [r for r in source_refs if r['forecast_id'] in {f['forecast_id'] for f in rows}]
        reasons['CONFLICTING_FIRST_ORIGINAL_FORECASTS'] += len(conflicts)
    dataset = snapshot(rows, role='admitted_calibration_forecasts', as_of=cutoff,
        recipe={'version': VERSION, 'selection': 'ALL_ORIGINAL_RECOMMENDATIONS_NOT_LATEST_ACTIVE'},
        lineage={'original_ledger_bindings': source_refs, 'exclusions': dict(reasons)})
    return {'dataset': dataset, 'inspected_original_legs': inspected, 'admitted_forecasts': len(rows),
            'excluded': dict(reasons), 'production_writes': 0, 'external_requests': 0}


def inspect_calibration(root, *, as_of, enabled=False, clock=lambda: datetime.now(UTC)):
    """Explicit optional SHADOW entry point; the stable daily path is unaffected."""
    if type(enabled) is not bool:
        raise ValueError('EXPLICIT_CALIBRATION_FLAG_REQUIRED')
    cutoff = utc(as_of)
    if cutoff > utc(clock()):
        raise ValueError('FUTURE_CALIBRATION_CUTOFF')
    if not enabled:
        return {'version': VERSION, 'status': 'DISABLED', 'mode': 'SHADOW', 'live_decision_effect': False}
    evidence = admitted_forecasts(root, as_of=cutoff)
    from goal_analysis.jobs.native_forecast_capture import read_forecasts

    native = read_forecasts(root, as_of=cutoff)
    from goal_analysis.jobs.xg_forecast_capture import read_forecasts as read_xg_forecasts

    xg = read_xg_forecasts(root, as_of=cutoff)
    if xg['capture_receipts'] or xg['gaps']:
        original = native['dataset']
        native = {**native, 'dataset': snapshot(original['rows']+xg['dataset']['rows'],
            role='separate_native_and_xg_calibration_forecasts', as_of=cutoff,
            recipe={'version':VERSION, 'selection':'SEPARATE_MODEL_POPULATIONS'},
            lineage={'native_dataset_id':original['manifest']['dataset_id'],
                     'xg_dataset_id':xg['dataset']['manifest']['dataset_id']}),
            'xg_capture':xg, 'capture_receipts':native['capture_receipts']+xg['capture_receipts'],
            'gaps':dict(Counter(native['gaps'])+Counter(xg['gaps'])),
            'eligible_forecasts':native['eligible_forecasts']+xg['eligible_forecasts'],
            'labeled_forecasts':native['labeled_forecasts']+xg['labeled_forecasts']}
    if native['capture_receipts'] or native['gaps']:
        published = evidence['dataset']
        rows = published['rows']+native['dataset']['rows']
        if len(rows) > MAX_ROWS:
            raise ValueError('COMBINED_CALIBRATION_FORECAST_ROW_BUDGET')
        combined = snapshot(rows, role='admitted_calibration_forecasts', as_of=cutoff,
            recipe={'version': VERSION, 'selection': 'PUBLISHED_LEGS_AND_SEPARATE_NATIVE_ALL_MARKET_COHORTS'},
            lineage={'published_dataset_id': published['manifest']['dataset_id'],
                     'native_dataset_id': native['dataset']['manifest']['dataset_id']})
        evidence = {**evidence, 'dataset': combined, 'admitted_forecasts': len(rows),
                    'native_capture': native}
    result = walk_forward(evidence['dataset'], as_of=cutoff)
    return {'version': VERSION, 'status': result['status'], 'mode': 'SHADOW',
        'source_admission': evidence, 'calibration': result,
        'live_decision_effect': False, 'production_writes': 0, 'external_requests': 0}


def archive_calibration(root, result):
    """Content-addressed own research artifact, never the active strategy config."""
    if result.get('version') != VERSION or result.get('mode') != 'SHADOW' or result.get('live_decision_effect') is not False:
        raise ValueError('SHADOW_CALIBRATION_ARTIFACT_REQUIRED')
    report = result['calibration']
    if report['report_sha256'] != sha({k: v for k, v in report.items() if k != 'report_sha256'}):
        raise ValueError('CALIBRATION_REPORT_HASH_MISMATCH')
    dataset = validate(result['source_admission']['dataset'])
    if (dataset['manifest']['dataset_id'] != report['input_dataset_id']
            or utc(dataset['manifest']['as_of']) != utc(report['as_of'])
            or report['policy'] != POLICY or report['live_decision_effect'] is not False
            or report['automatic_parameter_writes'] is not False):
        raise ValueError('CALIBRATION_ARTIFACT_INPUT_POLICY_OR_LIVE_CONFLICT')
    value = snapshot([result], role='calibration_candidate_report', as_of=report['as_of'],
        recipe={'version': VERSION, 'policy': POLICY},
        lineage={'input_dataset_id': result['source_admission']['dataset']['manifest']['dataset_id']})
    store = DatasetStore(Path(root)/'data/arthur/calibration-shadow')
    created = store.put(value)
    return {'created': created, 'dataset_id': value['manifest']['dataset_id'],
            'path': str(store.path(value['manifest']['dataset_id'])), 'live_promoted': False}
