"""Pure market-specific SHADOW holdout contract; no fit, rank or LIVE promotion.

Readers must verify receipt/source hashes against their stores before supplying
envelopes. This boundary verifies their content binding and temporal coherence;
it cannot authenticate arbitrary caller data or prove model implementation.
"""
import math
import re
from collections import Counter
from datetime import timedelta

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.leakage import model_inputs
from goal_analysis.normalization.training_datasets import sha

VERSION = 'weather-market-holdout-shadow-v1'
FAMILIES = {
    'BTTS_FT': {'period': 'FULL_TIME', 'classes': ('yes', 'no')},
    '1X2_FT': {'period': 'FULL_TIME', 'classes': ('home', 'draw', 'away')},
    'BTTS_H1': {'period': 'FIRST_HALF', 'classes': ('yes', 'no')},
    '1X2_H1': {'period': 'FIRST_HALF', 'classes': ('home', 'draw', 'away')},
    'TOTAL_0.5_H1': {'period': 'FIRST_HALF', 'classes': ('over', 'under'), 'point': .5},
    'TOTAL_1.5_H1': {'period': 'FIRST_HALF', 'classes': ('over', 'under'), 'point': 1.5},
}
POLICY = {'minimum_training': 30, 'minimum_holdout': 20, 'minimum_each_class': 2,
          'minimum_brier_improvement_fraction': .02, 'lockout_minutes': 15,
          'maximum_rows': 10_000, 'log_loss_floor': 1e-15}


def _hash(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('CONTENT_BOUND_HASH_REQUIRED')


def _admit(envelopes, cutoff):
    if not isinstance(envelopes, list) or len(envelopes) > POLICY['maximum_rows']:
        raise ValueError('ENVELOPE_BUDGET_OR_TYPE')
    rows = model_inputs(envelopes, as_of=cutoff)
    for envelope in envelopes:
        _hash(envelope.get('source_sha256'))
        _hash(envelope.get('receipt_id'))
    return rows


def _scope(row, protocol):
    if (row.get('competition_id'), row.get('season_id')) != (
            protocol['competition_id'], protocol['season_id']):
        raise ValueError('MARKET_COMPETITION_SEASON_MISMATCH')
    if not isinstance(row.get('fixture_id'), str) or re.fullmatch(
            r'the_stats_api:mt_\d{1,30}', row['fixture_id']) is None:
        raise ValueError('EXACT_NATIVE_FIXTURE_REQUIRED')
    for key in ('home_team_id', 'away_team_id'):
        if not isinstance(row.get(key), str) or re.fullmatch(r'the_stats_api:tm_\d{1,30}', row[key]) is None:
            raise ValueError('EXACT_NATIVE_TEAM_ROLE_REQUIRED')
    if row['home_team_id'] == row['away_team_id']:
        raise ValueError('DISTINCT_HOME_AWAY_TEAMS_REQUIRED')


def market_outcome(row, family):
    """An outcome from its own explicit period; never from OU direction or odds."""
    spec = FAMILIES[family]
    if (row.get('result_period') != 'FULL_TIME_90_MINUTES'
            or row.get('status') not in {'FT', 'finished', 'FINISHED'}):
        raise ValueError('REGULATION_RESULT_REQUIRED')
    if spec['period'] == 'FIRST_HALF':
        if row.get('half_score_scope') != 'FIRST_HALF_45_MINUTES':
            raise ValueError('EXPLICIT_HALF_SCORE_SCOPE_REQUIRED')
        h, a = row.get('half_home_goals'), row.get('half_away_goals')
    else:
        h, a = row.get('home_goals'), row.get('away_goals')
    if any(type(value) is not int or value < 0 for value in (h, a)):
        raise ValueError('EXPLICIT_PERIOD_SCORE_PAIR_REQUIRED')
    if spec['period'] == 'FIRST_HALF' and any(
            type(row.get(side + '_goals')) is not int or value > row[side + '_goals']
            for side, value in (('home', h), ('away', a))):
        raise ValueError('HALF_SCORE_EXCEEDS_REGULATION')
    if family.startswith('BTTS'):
        return 'yes' if h > 0 and a > 0 else 'no'
    if family.startswith('1X2'):
        return 'home' if h > a else 'away' if a > h else 'draw'
    return 'over' if h + a > spec['point'] else 'under'


def _probabilities(value, classes):
    if not isinstance(value, dict) or set(value) != set(classes):
        raise ValueError('EXACT_MARKET_PROBABILITY_VECTOR_REQUIRED')
    p = [value[key] for key in classes]
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in p):
        raise ValueError('FINITE_MARKET_PROBABILITIES_REQUIRED')
    if not math.isclose(math.fsum(p), 1, abs_tol=1e-10, rel_tol=0):
        raise ValueError('MARKET_PROBABILITIES_MUST_SUM_TO_ONE')
    return p


def protocol_payload(p):
    """Pure declaration schema; future holdout targets are legal at registration.

    This establishes neither own-presence nor model/data provenance. The store
    separately reconstructs actual sources; evaluation still needs its own time.
    """
    if not isinstance(p, dict):
        raise TypeError('WEATHER_PROTOCOL_PAYLOAD_REQUIRED')
    if p.get('version') != VERSION or p.get('family') not in FAMILIES:
        raise ValueError('REGISTERED_MARKET_PROTOCOL_REQUIRED')
    for field, pattern in (('competition_id', r'the_stats_api:comp_\d{1,30}'),
                           ('season_id', r'sn_\d{1,30}')):
        if not isinstance(p.get(field), str) or re.fullmatch(pattern, p[field]) is None:
            raise ValueError('NATIVE_PROTOCOL_SCOPE_REQUIRED')
    train, start, end = (utc(p[key]) for key in ('training_end', 'validation_start', 'validation_end'))
    if not train < start < end:
        raise ValueError('FIXED_CHRONOLOGICAL_HOLDOUT_REQUIRED')
    _hash(p.get('training_envelopes_sha256'))
    for key in ('baseline_model_version', 'weather_model_version'):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ValueError('FIXED_MODEL_VERSION_REQUIRED')
    if p['baseline_model_version'] == p['weather_model_version']:
        raise ValueError('DISTINCT_BASELINE_AND_WEATHER_MODEL_REQUIRED')
    cohort = p.get('holdout_fixtures')
    if not isinstance(cohort, list) or not 1 <= len(cohort) <= POLICY['maximum_rows']:
        raise ValueError('FIXED_HOLDOUT_FIXTURE_COHORT_REQUIRED')
    seen = set()
    for fixture in cohort:
        _scope({**fixture, 'competition_id': p['competition_id'], 'season_id': p['season_id']}, p)
        if fixture['fixture_id'] in seen or not start <= utc(fixture['kickoff']) < end:
            raise ValueError('INVALID_FIXED_HOLDOUT_COHORT')
        seen.add(fixture['fixture_id'])
    return p


def _protocol(envelope, as_of):
    p = protocol_payload(_admit([envelope], as_of)[0])
    if utc(p['validation_end']) > utc(as_of):
        raise ValueError('FIXED_CHRONOLOGICAL_HOLDOUT_REQUIRED')
    _admit([envelope], p['training_end'])
    return p


def _training(training, p):
    if sha(training) != p['training_envelopes_sha256']:
        raise ValueError('REGISTERED_TRAINING_SET_CHANGED')
    rows = _admit(training, p['training_end'])
    seen, counts = set(), Counter()
    for envelope, row in zip(training, rows, strict=True):
        _scope(row, p)
        if row['fixture_id'] in seen or utc(row['kickoff']) >= utc(p['training_end']):
            raise ValueError('TRAINING_IDENTITY_OR_TIME_LEAK')
        if utc(row['kickoff']) >= utc(envelope['times']['observed_at']):
            raise ValueError('TRAINING_OUTCOME_OBSERVED_BEFORE_KICKOFF')
        seen.add(row['fixture_id'])
        counts[market_outcome(row, p['family'])] += 1
    return seen, counts


def _forecast(envelope, p, protocol_sha, seen, cohort):
    f = _admit([envelope], p['validation_end'])[0]
    _scope(f, p)
    if f.get('family') != p['family'] or f.get('protocol_sha256') != protocol_sha:
        raise ValueError('FORECAST_PROTOCOL_OR_MARKET_MISMATCH')
    if f['fixture_id'] in seen:
        raise ValueError('HOLDOUT_FIXTURE_ALREADY_IN_TRAINING_OR_FORECASTS')
    kickoff, decision, trained = (utc(f[key]) for key in ('kickoff', 'decision_at', 'training_as_of'))
    if not utc(p['validation_start']) <= kickoff < utc(p['validation_end']):
        raise ValueError('FORECAST_OUTSIDE_FIXED_HOLDOUT')
    expected = cohort.get(f['fixture_id'])
    if expected is None or utc(expected['kickoff']) != kickoff or any(
            expected.get(k) != f[k] for k in ('home_team_id', 'away_team_id')):
        raise ValueError('FORECAST_NOT_IN_FIXED_HOLDOUT_COHORT')
    if not utc(p['training_end']) <= decision <= kickoff - timedelta(minutes=POLICY['lockout_minutes']):
        raise ValueError('PREMATCH_LOCKOUT_OR_PRETRAIN_FORECAST')
    if trained != utc(p['training_end']):
        raise ValueError('HOLDOUT_REFITTING_NOT_ALLOWED')
    if any(f.get(key) != p[key] for key in ('baseline_model_version', 'weather_model_version')):
        raise ValueError('MODEL_VERSION_CHANGED_DURING_HOLDOUT')
    _admit([envelope], decision)
    weather = _admit([f['weather_envelope']], decision)[0]
    _scope(weather, p)
    if (any(weather[key] != f[key] for key in ('fixture_id', 'home_team_id', 'away_team_id'))
            or utc(weather['kickoff']) != kickoff
            or weather.get('weather_kind') != 'FORECAST' or weather.get('status') != 'SOURCED'):
        raise ValueError('MATCHED_PREMATCH_WEATHER_FORECAST_REQUIRED')
    if abs(utc(weather['target_time']) - kickoff) > timedelta(hours=1):
        raise ValueError('WEATHER_TARGET_MISMATCH')
    observed = utc(f['weather_envelope']['times']['observed_at'])
    if not kickoff - timedelta(hours=6) <= observed <= decision:
        raise ValueError('STALE_OR_FUTURE_PREMATCH_WEATHER')
    for key, low, high in (('temperature_c', -80, 60), ('wind_speed_kmh', 0, 300),
                           ('precipitation_mm', 0, 500)):
        value = weather.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('EXPLICIT_WEATHER_VALUES_REQUIRED')
    classes = FAMILIES[p['family']]['classes']
    return f, _probabilities(f.get('baseline'), classes), _probabilities(f.get('weather_model'), classes)


def _outcomes(outcomes, p):
    result = {}
    for envelope, row in zip(outcomes, _admit(outcomes, p['validation_end']), strict=True):
        _scope(row, p)
        if row['fixture_id'] in result:
            raise ValueError('DUPLICATE_HOLDOUT_OUTCOME')
        if utc(row['kickoff']) >= utc(envelope['times']['observed_at']):
            raise ValueError('OUTCOME_OBSERVED_BEFORE_KICKOFF')
        result[row['fixture_id']] = row
    return result


def validate_registered_weather_market(root, protocol_id, *, forecasts, outcomes, as_of):
    """Read a genuine protocol/training source before the pure loss contract.

    Forecast/outcome store authentication is a separate unfinished boundary.
    Therefore even a favorable supplied comparison cannot be market validation.
    """
    from goal_analysis.storage.weather_protocols import WeatherProtocols

    source = WeatherProtocols(root).read(protocol_id, as_of=as_of)
    comparison = validate_weather_market(source['envelope'], training=source['training'],
                                        forecasts=forecasts, outcomes=outcomes, as_of=as_of)
    return {'status': 'FORECAST_AND_OUTCOME_STORE_PROVENANCE_PENDING', 'mode': 'SHADOW',
        'protocol_store_verified': True, 'forecast_store_verified': False,
        'outcome_store_verified': False, 'comparison_contract_result': comparison,
        'model_implementation_verified': False, 'heldout_validation_complete': False,
        'live_promotion_allowed': False, 'live_weight_eligible': False,
        'rank_multiplier': 1.0, 'probability_adjustment': 0.0, 'live_decision_effect': False}


def validate_weather_market(protocol, *, training, forecasts, outcomes, as_of):
    """Compare already witnessed fixed-model predictions, without fitting on test.

    A positive result is a SHADOW loss comparison only. Team clustering,
    multiple comparisons, causal identification and executable odds need their
    own later evidence; no return value grants live ranking eligibility.
    """
    p = _protocol(protocol, as_of)
    trained, training_counts = _training(training, p)
    _admit(forecasts, p['validation_end'])
    actuals = _outcomes(outcomes, p)
    classes, pairs, seen, counts = FAMILIES[p['family']]['classes'], [], set(trained), Counter()
    protocol_sha = sha(protocol)
    cohort = {row['fixture_id']: row for row in p['holdout_fixtures']}
    for envelope in forecasts:
        f, baseline, challenger = _forecast(envelope, p, protocol_sha, seen, cohort)
        seen.add(f['fixture_id'])
        row = actuals.pop(f['fixture_id'], None)
        if (row is None or utc(row['kickoff']) != utc(f['kickoff'])
                or any(row[k] != f[k] for k in ('home_team_id', 'away_team_id'))):
            raise ValueError('EXACT_HELDOUT_OUTCOME_PAIR_REQUIRED')
        label = market_outcome(row, p['family'])
        counts[label] += 1
        pairs.append((baseline, challenger, classes.index(label)))
    if actuals:
        raise ValueError('UNPAIRED_OUTCOMES_CANNOT_BE_SELECTED_AFTER_RESULTS')
    if {row['fixture_id'] for row in p['holdout_fixtures']} != seen - trained:
        raise ValueError('FIXED_HOLDOUT_COHORT_INCOMPLETE')
    sufficient = (len(trained) >= POLICY['minimum_training'] and len(pairs) >= POLICY['minimum_holdout']
        and all(training_counts[k] >= POLICY['minimum_each_class']
                and counts[k] >= POLICY['minimum_each_class'] for k in classes))
    result = {'version': VERSION, 'mode': 'SHADOW', 'family': p['family'],
        'competition_id': p['competition_id'], 'season_id': p['season_id'], 'protocol_sha256': protocol_sha,
        'training_count': len(trained), 'holdout_count': len(pairs),
        'training_class_counts': dict(training_counts), 'holdout_class_counts': dict(counts),
        'status': 'INSUFFICIENT_DATA', 'metrics': None, 'policy': dict(POLICY),
        'models_fitted': 0, 'live_weight_eligible': False, 'live_promotion_allowed': False,
        'rank_multiplier': 1.0, 'probability_adjustment': 0.0,
        'uncertainty_or_multiple_comparison_certified': False,
        'causal_or_profitable_effect_certified': False, 'as_of': utc(as_of).isoformat()}
    if sufficient:
        def losses(index):
            return {'brier': math.fsum(math.fsum((v-int(i == y))**2 for i, v in enumerate(pair[index]))
                                       for *pair, y in pairs) / len(pairs),
                    'log_loss': math.fsum(-math.log(max(POLICY['log_loss_floor'], pair[index][y]))
                                         for *pair, y in pairs) / len(pairs)}
        baseline, challenger = losses(0), losses(1)
        improvement = (baseline['brier'] - challenger['brier']) / baseline['brier'] if baseline['brier'] else 0
        passed = (improvement >= POLICY['minimum_brier_improvement_fraction']
                  and challenger['log_loss'] < baseline['log_loss'])
        result.update(status='SHADOW_LOSS_IMPROVED' if passed else 'NO_HELDOUT_IMPROVEMENT',
            metrics={'baseline': baseline, 'weather_model': challenger, 'brier_improvement_fraction': improvement})
    result['result_sha256'] = sha(result)
    return result
