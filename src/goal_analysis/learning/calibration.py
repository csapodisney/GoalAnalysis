"""Purged expanding-window calibration of admitted prospective binary forecasts.

This pure SHADOW layer never fits the football model or changes a profile.
Rows must come from the physical ledger/source adapter, not reconstructed bets.
"""
import math
from collections import Counter, defaultdict
from datetime import timedelta

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha, snapshot, validate

VERSION = 'profile-walk-forward-calibration-shadow-v1'
GROUP = ('profile_id', 'strategy_version', 'model_version', 'market_key',
         'selection_key', 'period', 'settlement', 'line')
POLICY = {'min_train_fixtures': 100, 'min_train_days': 21,
          'min_eval_fixtures': 5, 'min_eval_days': 3,
          'fold_days': 7, 'fold_count': 3, 'embargo_hours': 24}
TEMPERATURES = (1., .5, .75, 1.25, 1.5, 2., 3.)
BIASES = (0., -.75, -.5, -.25, .25, .5, .75)
EPS = 1e-6
LOG_FLOOR = 1e-15
MAX_ROWS = 10000


def _probability(p):
    return type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1


def _hash_id(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def group(row):
    values = tuple(row[k] for k in GROUP)
    if (any(not isinstance(v, str) or not v or v.startswith('UNKNOWN') for v in values[:-1])
            or values[-1] is not None and not isinstance(values[-1], str)):
        raise ValueError('EXPLICIT_PROFILE_MODEL_AND_PRODUCT_REQUIRED')
    return values


def _transform(p, temperature, bias):
    if temperature == 1 and bias == 0:
        return p
    clipped = min(1 - EPS, max(EPS, p))
    z = (math.log(clipped / (1 - clipped)) + bias) / temperature
    return 1 / (1 + math.exp(-z))


def _scores(rows, parameters=None):
    if not rows:
        return {'count': 0, 'brier': None, 'log_loss': None}
    values = [(r['probability'] if parameters is None else _transform(
        r['probability'], parameters['temperature'], parameters['bias']), r['outcome']) for r in rows]
    return {'count': len(values),
        'brier': math.fsum((p - y) ** 2 for p, y in values) / len(values),
        'log_loss': math.fsum(-math.log(max(LOG_FLOOR, p if y else 1 - p))
                             for p, y in values) / len(values)}


def _fit(rows):
    # Fixed preregistered finite grid; holdout labels never select parameters.
    best = None
    for temperature in TEMPERATURES:
        for bias in BIASES:
            parameters = {'temperature': temperature, 'bias': bias}
            objective = _scores(rows, parameters)['log_loss'] + .01 * (
                math.log(temperature) ** 2 + bias ** 2)
            if best is None or objective < best[0] - 1e-12:
                best = objective, parameters
    return {**best[1], 'training_regularized_log_loss': best[0]}


def _label(row, cutoff):
    known = [r for r in row['result_receipts'] if utc(r['available_at']) <= cutoff]
    if not known:
        return None
    last = max(utc(r['available_at']) for r in known)
    chosen = [r for r in known if utc(r['available_at']) == last]
    if len({r['outcome'] for r in chosen}) != 1:
        return None
    receipt = min(chosen, key=lambda r: r['receipt_id'])
    return {**row, 'result_receipts': known, 'outcome': receipt['outcome'], 'label_receipt_id': receipt['receipt_id'],
            'label_available_at': receipt['available_at']}


def _row(row, cutoff):
    group(row)
    if not _hash_id(row['forecast_id']) or not _hash_id(row['run_sha256']):
        raise ValueError('FORECAST_RUN_HASH_REQUIRED')
    if (not isinstance(row['fixture_id'], str) or not row['fixture_id'].startswith('the_stats_api:mt_')
            or not _probability(row['probability'])
            or row['probability_kind'] != 'ORIGINAL_NUMERIC_MODEL_ESTIMATE'):
        raise ValueError('NATIVE_ORIGINAL_NUMERIC_FORECAST_REQUIRED')
    model, created, saved, kickoff = (utc(row[k]) for k in (
        'model_as_of', 'created_at', 'saved_at', 'kickoff'))
    if not model <= created <= saved <= cutoff or saved >= kickoff - timedelta(minutes=15):
        raise ValueError('INVALID_FORECAST_AVAILABILITY_OR_LOCKOUT')
    if (not isinstance(row['dataset_ids'], list) or not row['dataset_ids']
            or any(not _hash_id(i) for i in row['dataset_ids'])
            or not isinstance(row['source_receipt_ids'], list) or not row['source_receipt_ids']
            or any(not _hash_id(i) for i in row['source_receipt_ids'])):
        raise ValueError('DATASET_AND_PHYSICAL_SOURCE_RECEIPTS_REQUIRED')
    if not isinstance(row['result_receipts'], list) or len(row['result_receipts']) > 100:
        raise ValueError('BOUNDED_RESULT_RECEIPTS_REQUIRED')
    for result in row['result_receipts']:
        if (type(result['outcome']) is not int or result['outcome'] not in (0, 1)
                or not _hash_id(result['receipt_id']) or not _hash_id(result['source_sha256'])
                or not kickoff < utc(result['observed_at']) <= utc(result['available_at']) <= cutoff):
            raise ValueError('INVALID_BINARY_RESULT_RECEIPT')
    return row


def _select(rows, cutoff):
    first, ambiguous, excluded = {}, set(), Counter()
    # Dedup before probability/source eligibility; later successful forecasts
    # cannot replace an earlier malformed forecast of the same product/version.
    def order(row):
        try:
            return utc(row['saved_at']).timestamp(), str(row.get('forecast_id', ''))
        except (KeyError, ValueError, TypeError):
            return math.inf, str(row.get('forecast_id', ''))

    for row in sorted(rows, key=order):
        try:
            key = (*group(row), row['fixture_id'])
            stamp = utc(row['saved_at'])
            if stamp > cutoff:
                excluded['FUTURE_FORECAST'] += 1
                continue
            previous = first.get(key)
            if previous is None or stamp < utc(previous['saved_at']):
                first[key] = row
            elif stamp == utc(previous['saved_at']) and sha(row) != sha(previous):
                # Same estimate in multiple cards is a duplicate, not a conflict.
                fields = ('probability', 'model_as_of', 'dataset_ids', 'source_receipt_ids',
                          'result_receipts', 'kickoff')
                if any(sha(row.get(k)) != sha(previous.get(k)) for k in fields):
                    ambiguous.add(key)
        except (ValueError, TypeError, KeyError):
            excluded['INVALID_PRODUCT_OR_CLOCK'] += 1
    accepted = []
    for key, row in first.items():
        try:
            if key in ambiguous:
                raise ValueError('CONFLICTING_FIRST_FORECASTS')
            accepted.append(_row(row, cutoff))
        except (ValueError, KeyError, TypeError) as error:
            excluded[str(error)] += 1
    excluded['DUPLICATE_OR_LATER_FORECASTS'] = len(rows) - len(first) - excluded['FUTURE_FORECAST'] - excluded['INVALID_PRODUCT_OR_CLOCK']
    return accepted, dict(excluded)


def _enough(rows, fixtures, days):
    return (len({r['fixture_id'] for r in rows}) >= fixtures
            and len({utc(r['saved_at']).date() for r in rows}) >= days
            and {r['outcome'] for r in rows} == {0, 1})


def walk_forward(dataset, *, as_of, policy=None):
    """Return versioned candidate artifacts, never fitted football predictions.

    The dataset is a hash-verified output of the read-only physical adapter.
    A hash alone is not proof of historical availability or independent trials.
    """
    validate(dataset)
    cutoff = utc(as_of)
    if dataset['manifest']['role'] != 'admitted_calibration_forecasts' or utc(dataset['manifest']['as_of']) != cutoff:
        raise ValueError('EXACT_CALIBRATION_INPUT_DATASET_REQUIRED')
    rows = dataset['rows']
    if len(rows) > MAX_ROWS:
        raise ValueError('CALIBRATION_ROW_BUDGET')
    policy = dict(POLICY if policy is None else policy)
    if (set(policy) != set(POLICY) or any(type(v) is not int or not 1 <= v <= 10000 for v in policy.values())
            or policy['fold_count'] > 12 or policy['fold_days'] > 30 or policy['embargo_hours'] > 168):
        raise ValueError('INVALID_CALIBRATION_POLICY')
    accepted, excluded = _select(rows, cutoff)
    batches = defaultdict(list)
    for row in accepted:
        batches[group(row)].append(row)
    if len(batches) > 256:
        raise ValueError('CALIBRATION_GROUP_BUDGET')
    end = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    first = end - timedelta(days=policy['fold_days'] * policy['fold_count'])
    results = []
    for key, batch in sorted(batches.items(), key=lambda pair: repr(pair[0])):
        folds, all_predictions = [], []
        for ordinal in range(policy['fold_count']):
            start = first + timedelta(days=ordinal * policy['fold_days'])
            stop = start + timedelta(days=policy['fold_days'])
            train_cutoff = start - timedelta(hours=policy['embargo_hours'])
            evaluation = [r for r in batch if start <= utc(r['saved_at']) < stop]
            held_fixtures = {r['fixture_id'] for r in evaluation}
            training = [v for r in batch if utc(r['saved_at']) < train_cutoff
                        and r['fixture_id'] not in held_fixtures
                        and (v := _label(r, train_cutoff)) is not None]
            evaluated = [v for r in evaluation if (v := _label(r, cutoff)) is not None]
            fold = {'ordinal': ordinal, 'start': start.isoformat(), 'stop': stop.isoformat(),
                'training_labels_available_by': train_cutoff.isoformat(),
                'train_count': len(training), 'evaluation_count': len(evaluated),
                'unsettled_or_ambiguous_evaluation': len(evaluation) - len(evaluated),
                'status': 'NOT_FIT', 'parameters': None}
            if (_enough(training, policy['min_train_fixtures'], policy['min_train_days'])
                    and _enough(evaluated, policy['min_eval_fixtures'], policy['min_eval_days'])):
                parameters = _fit(training)
                # Persist exactly the contemporaneously available labels used.
                train = snapshot(training, role='calibration_fold_training', as_of=train_cutoff,
                    recipe={'version': VERSION, 'policy': policy, 'group': list(key)},
                    lineage={'parent_dataset_id': dataset['manifest']['dataset_id']})
                predictions = [{
                    'forecast_id': r['forecast_id'], 'fixture_id': r['fixture_id'],
                    'label_receipt_id': r['label_receipt_id'], 'outcome': r['outcome'],
                    'original_probability': r['probability'],
                    'calibrated_probability': _transform(r['probability'], parameters['temperature'], parameters['bias'])
                } for r in evaluated]
                fold.update(status='FITTED_SHADOW', parameters=parameters, training_dataset=train,
                    original_scores=_scores(evaluated), calibrated_scores=_scores(evaluated, parameters),
                    predictions=predictions)
                all_predictions.extend(predictions)
            folds.append(fold)
        enough = all(f['status'] == 'FITTED_SHADOW' for f in folds)
        original = _scores([{'probability': r['original_probability'], 'outcome': r['outcome']} for r in all_predictions])
        calibrated = _scores([{'probability': r['calibrated_probability'], 'outcome': r['outcome']} for r in all_predictions])
        improves = enough and all(calibrated[k] <= original[k] + 1e-12 for k in ('brier', 'log_loss'))
        candidate = None
        if enough:
            candidate = {'version': VERSION, 'group': dict(zip(GROUP, key, strict=True)),
                'mode': 'SHADOW', 'trained_before': folds[-1]['training_labels_available_by'],
                'training_dataset_id': folds[-1]['training_dataset']['manifest']['dataset_id'],
                'parameters': folds[-1]['parameters'], 'input_dataset_id': dataset['manifest']['dataset_id'],
                'policy_sha256': sha(policy), 'logit_clip': EPS, 'live_approved': False}
            candidate['candidate_id'] = sha(candidate)
        results.append({'group': dict(zip(GROUP, key, strict=True)), 'forecasts': len(batch),
            'unique_fixtures': len({r['fixture_id'] for r in batch}), 'folds': folds,
            'status': 'CANDIDATE_SHADOW' if improves else 'REJECTED_SHADOW' if enough else 'NOT_FIT',
            'candidate': candidate, 'original_scores': original, 'calibrated_scores': calibrated,
            'release_allowed': False})
    report = {'version': VERSION, 'as_of': cutoff.isoformat(), 'mode': 'SHADOW',
        'status': 'COMPLETE' if results and all(r['status'] != 'NOT_FIT' for r in results) else 'NOT_FIT',
        'policy': policy, 'policy_sha256': sha(policy), 'input_dataset_id': dataset['manifest']['dataset_id'],
        'accepted_unique_forecasts': len(accepted), 'excluded': excluded, 'groups': results,
        'method': 'FIXED_GRID_LOGIT_TEMPERATURE_AND_BIAS_L2_0.01', 'log_loss_floor': LOG_FLOOR,
        'selection_basis': dataset['manifest']['recipe'].get('selection', 'ORIGINAL_SELECTED_LEGS_ONLY_NOT_ALL_CANDIDATES'),
        'correlation_note': 'Products and profiles share fixtures; counts are not independent trials.',
        'automatic_parameter_writes': False, 'live_decision_effect': False, 'parent_30_day_gate_complete': False}
    report['report_sha256'] = sha(report)
    return report


def shadow_probability(candidate, row, *, as_of):
    """Explicit research-only application, bound to the exact fitted cohort."""
    if (candidate.get('candidate_id') != sha({k: v for k, v in candidate.items() if k != 'candidate_id'})
            or candidate.get('version') != VERSION
            or candidate.get('logit_clip') != EPS
            or candidate['mode'] != 'SHADOW' or candidate['live_approved'] is not False
            or candidate['group'] != dict(zip(GROUP, group(row), strict=True))):
        raise ValueError('CALIBRATION_CANDIDATE_COHORT_OR_HASH_MISMATCH')
    now = utc(as_of)
    if (utc(candidate['trained_before']) >= now or now >= utc(row['kickoff']) - timedelta(minutes=15)
            or utc(row['model_as_of']) > now or not _probability(row['probability'])):
        raise ValueError('CALIBRATION_SHADOW_CLOCK_OR_PROBABILITY')
    p = candidate['parameters']
    if p['temperature'] not in TEMPERATURES or p['bias'] not in BIASES:
        raise ValueError('INVALID_CALIBRATION_PARAMETERS')
    return {'mode': 'SHADOW', 'candidate_id': candidate['candidate_id'],
        'probability': _transform(row['probability'], p['temperature'], p['bias']),
        'live_decision_effect': False}
