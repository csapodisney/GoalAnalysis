"""Synthetic fixed forecasts test the contract, never real football performance."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents.weather_market_validation import (
    FAMILIES,
    VERSION,
    market_outcome,
    validate_weather_market,
)
from goal_analysis.normalization.training_datasets import sha

TRAIN_END = datetime(2026, 10, 1, tzinfo=UTC)
START = TRAIN_END + timedelta(days=1)
END = datetime(2026, 11, 1, tzinfo=UTC)
NOW = END + timedelta(days=1)


def seal(envelope):
    envelope['payload_sha256'] = sha(envelope['payload'])
    return envelope


def envelope(identifier, payload, observed, *, available=None, kind='KICKOFF'):
    return seal({'record_id': identifier, 'payload': deepcopy(payload),
        'source_sha256': sha(identifier + ':source'), 'receipt_id': sha(identifier + ':receipt'),
        'times': {'observed_at': observed.isoformat(),
                  'available_at': (available or observed).isoformat(),
                  'published_at': None, 'ingested_at': None,
                  'availability_basis': 'SOURCE_PRESENCE_UPPER_BOUND',
                  'event_time_kind': kind, 'event_time': payload.get('kickoff')}})


def score(number, kickoff):
    h, a = ((2, 0), (1, 1), (0, 2), (0, 0), (2, 1), (1, 2))[number % 6]
    return {'fixture_id': f'the_stats_api:mt_{number}', 'competition_id': 'the_stats_api:comp_1',
            'home_team_id': 'the_stats_api:tm_1', 'away_team_id': 'the_stats_api:tm_2',
            'season_id': 'sn_1', 'kickoff': kickoff.isoformat(), 'status': 'FT',
            'result_period': 'FULL_TIME_90_MINUTES', 'home_goals': h, 'away_goals': a,
            'half_home_goals': min(h, 1), 'half_away_goals': min(a, 1),
            'half_score_scope': 'FIRST_HALF_45_MINUTES'}


def scenario(family='BTTS_FT', training_count=30):
    training = []
    for i in range(training_count):
        kickoff = datetime(2026, 8, 1, 15, tzinfo=UTC) + timedelta(days=i)
        value = score(i + 1, kickoff)
        training.append(envelope(f'train:{i}', value, kickoff + timedelta(hours=3)))
    holdout = [score(i + 101, START + timedelta(days=i, hours=15)) for i in range(24)]
    p = {'version': VERSION, 'family': family, 'competition_id': 'the_stats_api:comp_1',
         'season_id': 'sn_1', 'training_end': TRAIN_END.isoformat(), 'validation_start': START.isoformat(),
         'validation_end': END.isoformat(), 'training_envelopes_sha256': sha(training),
         'baseline_model_version': 'synthetic-constant-v1', 'weather_model_version': 'synthetic-pattern-v1',
         'holdout_fixtures': [{k: r[k] for k in ('fixture_id', 'kickoff', 'home_team_id', 'away_team_id')}
                              for r in holdout]}
    protocol = envelope('protocol:synthetic-only', p, TRAIN_END - timedelta(days=1), kind='FORECAST_TARGET')
    forecasts, outcomes = [], []
    for value in holdout:
        kickoff = datetime.fromisoformat(value['kickoff'])
        label, classes = market_outcome(value, family), FAMILIES[family]['classes']
        # Deliberately artificial pattern used solely to exercise proper losses.
        weather = {k: value[k] for k in ('fixture_id', 'competition_id', 'season_id', 'kickoff',
                                       'home_team_id', 'away_team_id')}
        weather.update(weather_kind='FORECAST', status='SOURCED', target_time=value['kickoff'],
                       temperature_c=10, wind_speed_kmh=12, precipitation_mm=.2)
        weather = envelope('weather:' + value['fixture_id'], weather, kickoff - timedelta(hours=1),
                           kind='FORECAST_TARGET')
        f = {k: value[k] for k in ('fixture_id', 'competition_id', 'season_id', 'kickoff',
                                 'home_team_id', 'away_team_id')}
        f.update(family=family, protocol_sha256=sha(protocol), training_as_of=TRAIN_END.isoformat(),
                 decision_at=(kickoff - timedelta(minutes=30)).isoformat(),
                 baseline_model_version=p['baseline_model_version'], weather_model_version=p['weather_model_version'],
                 weather_envelope=weather, baseline={key: 1/len(classes) for key in classes},
                 weather_model={key: .8 if key == label else .2/(len(classes)-1) for key in classes})
        forecasts.append(envelope('prediction:' + value['fixture_id'], f, kickoff - timedelta(minutes=40),
                                  kind='FORECAST_TARGET'))
        outcomes.append(envelope('outcome:' + value['fixture_id'], value, kickoff + timedelta(hours=3)))
    return protocol, training, forecasts, outcomes


def run(args):
    p, training, forecasts, outcomes = args
    return validate_weather_market(p, training=training, forecasts=forecasts, outcomes=outcomes, as_of=NOW)


@pytest.mark.parametrize('family', list(FAMILIES))
def test_each_market_has_its_own_target_and_proper_loss_but_no_live_eligibility(family):
    args = scenario(family)
    original = deepcopy(args)
    result = run(args)
    assert result['status'] == 'SHADOW_LOSS_IMPROVED'
    assert result['training_count'] == 30 and result['holdout_count'] == 24
    assert result['metrics']['weather_model']['brier'] < result['metrics']['baseline']['brier']
    assert result['metrics']['weather_model']['log_loss'] < result['metrics']['baseline']['log_loss']
    assert result['live_weight_eligible'] is result['live_promotion_allowed'] is False
    assert result['rank_multiplier'] == 1 and result['probability_adjustment'] == 0
    assert args == original


def test_full_and_half_targets_do_not_inherit_each_other():
    row = score(5, START)
    row.update(home_goals=2, away_goals=1, half_home_goals=0, half_away_goals=0)
    assert market_outcome(row, 'BTTS_FT') == 'yes'
    assert market_outcome(row, 'BTTS_H1') == 'no'
    assert market_outcome(row, '1X2_FT') == 'home'
    assert market_outcome(row, '1X2_H1') == 'draw'
    assert market_outcome(row, 'TOTAL_0.5_H1') == 'under'


def test_changed_holdout_outcomes_can_fail_without_any_training_refit():
    args = scenario()
    original_training = deepcopy(args[1])
    for outcome in args[3]:
        value = outcome['payload']
        value.update(home_goals=2, away_goals=0) if market_outcome(value, 'BTTS_FT') == 'yes' else value.update(home_goals=1, away_goals=1)
        seal(outcome)
    result = run(args)
    assert result['status'] == 'NO_HELDOUT_IMPROVEMENT' and result['models_fitted'] == 0
    assert args[1] == original_training


def test_insufficient_training_is_not_made_valid_by_good_test_predictions():
    result = run(scenario(training_count=6))
    assert result['status'] == 'INSUFFICIENT_DATA' and result['metrics'] is None


def test_post_result_cohort_cherry_picking_is_rejected_even_when_both_sides_dropped():
    args = scenario()
    args[2].pop()
    args[3].pop()
    with pytest.raises(ValueError, match='COHORT_INCOMPLETE'):
        run(args)


def test_training_set_cannot_change_after_protocol_registration():
    args = scenario()
    args[1].pop()
    with pytest.raises(ValueError, match='TRAINING_SET_CHANGED'):
        run(args)


@pytest.mark.parametrize('changes', [
    {'family': '1X2_FT'}, {'competition_id': 'the_stats_api:comp_2'},
    {'season_id': 'sn_2'}, {'weather_model_version': 'retuned-v2'},
    {'training_as_of': (TRAIN_END + timedelta(days=1)).isoformat()},
    {'baseline': {'yes': .7, 'no': .7}}, {'baseline': {'yes': True, 'no': 0}},
    {'baseline': {'over': .5, 'under': .5}},
    {'home_team_id': 'the_stats_api:tm_2', 'away_team_id': 'the_stats_api:tm_1'},
])
def test_wrong_market_scope_model_refit_or_non_probability_scores_rejected(changes):
    args = scenario()
    args[2][0]['payload'].update(changes)
    seal(args[2][0])
    with pytest.raises(ValueError):
        run(args)


def test_forecast_known_one_microsecond_after_decision_is_not_usable():
    args = scenario()
    first = args[2][0]
    first['times']['available_at'] = (datetime.fromisoformat(first['payload']['decision_at'])
                                    + timedelta(microseconds=1)).isoformat()
    with pytest.raises(ValueError):
        run(args)


def test_protocol_only_witnessed_after_training_cannot_register_old_holdout():
    args = scenario()
    args[0]['times']['available_at'] = START.isoformat()
    # Keep predicted protocol references coherent: the time proof itself must still fail.
    for f in args[2]:
        f['payload']['protocol_sha256'] = sha(args[0])
        seal(f)
    with pytest.raises(ValueError):
        run(args)


@pytest.mark.parametrize('changes', [
    {'weather_kind': 'REANALYSIS'}, {'fixture_id': 'the_stats_api:mt_999'},
    {'temperature_c': None}, {'target_time': START.isoformat()},
])
def test_post_event_or_other_fixture_weather_cannot_replace_prematch_source(changes):
    args = scenario()
    forecast = args[2][0]
    forecast['payload']['weather_envelope']['payload'].update(changes)
    seal(forecast['payload']['weather_envelope'])
    seal(forecast)
    with pytest.raises(ValueError):
        run(args)


@pytest.mark.parametrize('changes', [{'half_home_goals': None}, {'half_score_scope': 'ASSUMED_HALF'}])
def test_missing_or_unproven_half_time_cannot_be_filled_from_full_time(changes):
    args = scenario('BTTS_H1')
    args[3][0]['payload'].update(changes)
    seal(args[3][0])
    with pytest.raises(ValueError):
        run(args)


def test_missing_receipt_or_source_content_binding_is_rejected():
    args = scenario()
    args[2][0]['receipt_id'] = 'unverified'
    with pytest.raises(ValueError, match='CONTENT_BOUND_HASH'):
        run(args)


def test_outcome_home_away_orientation_cannot_reverse_prediction_roles():
    args = scenario('1X2_FT')
    outcome = args[3][0]
    outcome['payload'].update(home_team_id='the_stats_api:tm_2', away_team_id='the_stats_api:tm_1')
    seal(outcome)
    with pytest.raises(ValueError, match='OUTCOME_PAIR_REQUIRED'):
        run(args)


def test_duplicate_forecast_or_outcome_is_not_multiple_independent_evidence():
    args = scenario()
    args[3].append(deepcopy(args[3][0]))
    with pytest.raises(ValueError):
        run(args)
