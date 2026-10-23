"""Synthetic boundary tests, not performance evidence on real football outcomes."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from test_xg_rates import NOW, complete

from goal_analysis.models.xg_ensemble import (
    FAMILIES,
    RECIPE,
    XG_VERSION,
    fit_ensemble,
    predict_ensemble,
    predict_xg,
)
from goal_analysis.models.xg_rates import fit_xg_rates
from goal_analysis.normalization.leakage import LookAheadError
from goal_analysis.normalization.training_datasets import sha, validate

START = datetime(2026, 6, 1, 10, tzinfo=UTC)
ASOF = START + timedelta(days=90)
FAMILY = '1X2_FT_REGULATION'


def envelope(payload, when):
    return {'record_id': sha(payload), 'payload': payload, 'payload_sha256': sha(payload),
            'source_sha256': 'a'*64, 'receipt_id': 'b'*64,
            'times': {'observed_at': when.isoformat(), 'available_at': when.isoformat(),
                      'published_at': None, 'ingested_at': None,
                      'availability_basis': 'SOURCE_PRESENCE_UPPER_BOUND'}}


def forecast(i=100, when=ASOF+timedelta(days=1), count=18, family=FAMILY):
    keys = FAMILIES[family]
    goals = ('GAUSS', 'KEPLER', 'LAPLACE') if len(keys)==3 else ('GAUSS', 'LAPLACE')
    components = {}
    for name in (*goals, 'XG'):
        # Deliberately overconfident control; challenger supports different outcomes.
        p = [.8, .15, .05] if len(keys)==3 else [.8, .2]
        if name=='XG':
            p = [.25, .35, .4] if len(keys)==3 else [.4, .6]
        components[name] = {'probabilities': dict(zip(keys, p)),
                            'model_version': XG_VERSION if name=='XG' else name+'-test-v1',
                            'training_as_of': (when-timedelta(hours=1)).isoformat(),
                            'input_dataset_id': 'c'*64}
    return envelope({'fixture_id': f'mt_{i}', 'competition_id': 'comp_1', 'season_id': 'sn_1',
                     'issued_at': when.isoformat(), 'kickoff': (when+timedelta(hours=2)).isoformat(),
                     'family': family, 'league_xg_matches': count, 'components': components}, when)


def history(n=60, family=FAMILY):
    rows = []
    keys = FAMILIES[family]
    for i in range(n):
        when = START+timedelta(days=i)
        f = forecast(i+1, when, family=family)
        settled = when+timedelta(hours=5)
        rows.append(envelope({'forecast': f, 'outcome': keys[i % len(keys)],
                              'settled_at': settled.isoformat()}, settled))
    return rows


def fit(rows=None, family=FAMILY):
    return fit_ensemble(history(family=family) if rows is None else rows, as_of=ASOF,
                        family=family, competition_id='comp_1', season_id='sn_1')


def rehash(e):
    e['payload_sha256'] = sha(e['payload'])
    return e


def reseal(a):
    a['result_sha256'] = sha({k: v for k, v in a.items() if k!='result_sha256'})
    return a


def change_forecast(row, fn):
    fn(row['payload']['forecast']['payload'])
    rehash(row['payload']['forecast']); rehash(row)


def future_fixture():
    return {'fixture_id': 'mt_100', 'competition_id': 'comp_1', 'season_id': 'sn_1',
            'home_team_id': 'tm_1', 'away_team_id': 'tm_2', 'neutral_venue': False,
            'kickoff': (NOW+timedelta(hours=1)).isoformat()}


def test_real_rate_algorithm_becomes_separate_consistent_poisson_probabilities():
    rates = fit_xg_rates(complete(), as_of=NOW)
    result = predict_xg(rates, future_fixture(), as_of=NOW)
    assert result['lambdas']==pytest.approx({'home': 1.6, 'away': 1.1})
    assert result['status']=='RAW_XG_PROBABILITIES_SHADOW'
    assert result['calibrated'] is False and result['live_decision_effect'] is False
    import math
    assert result['probabilities']['under25']==pytest.approx(math.exp(-2.7)*(1+2.7+2.7**2/2))
    for keys in FAMILIES.values():
        assert sum(result['probabilities'][k] for k in keys)==pytest.approx(1)


@pytest.mark.parametrize('change', ['team', 'league', 'venue', 'thin'])
def test_xg_missing_evidence_is_none_never_a_baseline_probability(change):
    rates = fit_xg_rates(complete(), as_of=NOW)
    fixture = future_fixture()
    if change=='team': fixture['away_team_id']='tm_99'
    if change=='league': fixture['competition_id']='comp_99'
    if change=='venue': fixture['neutral_venue']=None
    if change=='thin': rates=fit_xg_rates(complete()[:2], as_of=NOW)
    assert predict_xg(rates, fixture, as_of=NOW)['probabilities'] is None


def test_xg_strength_product_uses_opposing_venue_defense():
    rates = fit_xg_rates(complete(), as_of=NOW)
    teams = {t['team_id']: t for t in rates['leagues'][0]['teams']}
    teams['tm_1']['home']['attack_strength']=2
    teams['tm_2']['away']['defense_strength']=.5
    teams['tm_2']['home']['defense_strength']=10
    assert predict_xg(reseal(rates), future_fixture(), as_of=NOW)['lambdas']['home']==pytest.approx(1.6)


def test_xg_tamper_and_future_training_fail():
    rates = fit_xg_rates(complete(), as_of=NOW)
    rates['leagues'][0]['matches']=100
    with pytest.raises(ValueError, match='HASH_MISMATCH'): predict_xg(rates, future_fixture(), as_of=NOW)
    rates = fit_xg_rates(complete(), as_of=NOW)
    with pytest.raises(ValueError, match='FUTURE_XG_FIT'):
        predict_xg(rates, future_fixture(), as_of=NOW-timedelta(seconds=1))


def test_target_reuse_and_impossible_score_rate_do_not_produce_a_probability():
    rates = fit_xg_rates(complete(), as_of=NOW)
    fixture = future_fixture(); fixture['fixture_id']='mt_1'
    with pytest.raises(ValueError, match='TARGET_ALREADY_IN_XG_FIT'):
        predict_xg(rates, fixture, as_of=NOW)
    rates['leagues'][0]['teams'][0]['home']['attack_strength']=1000
    with pytest.raises(ValueError, match='INVALID_ENSEMBLE_NUMBER'):
        predict_xg(reseal(rates), future_fixture(), as_of=NOW)


def test_missing_calibration_returns_no_fake_probabilities_and_zero_xg_weight():
    a = fit([])
    assert a['status']=='INSUFFICIENT_WITNESSED_CALIBRATION'
    result = predict_ensemble(a, forecast(), as_of=ASOF+timedelta(days=1))
    assert result['probabilities'] is None and result['weights']['XG']==0
    assert result['promotion_allowed'] is result['live_decision_effect'] is False


@pytest.mark.parametrize('family', list(FAMILIES))
def test_actual_temperature_optimization_and_purged_holdout_are_deterministic(family):
    a = fit(family=family)
    assert a==fit(list(reversed(history(family=family))), family=family)
    assert a['calibration_rows']==36 and a['validation_rows']==24 and a['purged_rows']==0
    assert a['parameters']['GAUSS']['temperature']==3
    assert a['parameters']['GAUSS']['calibration_log_loss'] > 0
    assert a['validation']['ensemble']['matches']==24
    assert validate(a['dataset'])==a['dataset']
    assert len(a['dataset']['lineage'])==60
    p = predict_ensemble(a, forecast(family=family), as_of=ASOF+timedelta(days=1))
    assert sum(p['probabilities'].values())==pytest.approx(1)
    assert not p['promotion_allowed'] and not p['live_decision_effect']


def test_density_scales_xg_share_and_under_three_exactly_zero():
    a = fit()
    weights = []
    for count in (0, 2, 3, 30, 300):
        p = predict_ensemble(a, forecast(count=count), as_of=ASOF+timedelta(days=1))
        assert sum(p['weights'].values())==pytest.approx(1)
        weights.append(p['weights']['XG'])
    assert weights[0]==weights[1]==0
    assert 0<weights[2]<weights[3]<weights[4]<1


def test_missing_xg_uses_calibrated_goal_components_without_changing_artifact():
    a = fit(); before = deepcopy(a)
    f = forecast(); del f['payload']['components']['XG']; rehash(f)
    p = predict_ensemble(a, f, as_of=ASOF+timedelta(days=1))
    assert p['weights']['XG']==0 and a==before
    assert set(p['weights'])=={'GAUSS', 'KEPLER', 'LAPLACE', 'XG'}


@pytest.mark.parametrize('kind', ['negative', 'nan', 'sum', 'heuristic', 'version', 'training', 'family', 'scope'])
def test_invalid_probability_or_scope_is_rejected_without_score_normalization(kind):
    rows = history()
    def change(f):
        c = f['components']['GAUSS']
        if kind=='negative': c['probabilities']['home']=-.1
        if kind=='nan': c['probabilities']['home']=float('nan')
        if kind=='sum': c['probabilities']['home']=.7
        if kind=='heuristic': f['components']['SPECIALIST']=deepcopy(c)
        if kind=='version': c['model_version']='changed-v2'
        if kind=='training': c['training_as_of']=ASOF.isoformat()
        if kind=='family': f['family']='TOTAL_2.5_FT_REGULATION'
        if kind=='scope': f['season_id']='sn_2'
    # NaN cannot even enter a canonical hash-bound envelope.
    if kind=='nan':
        with pytest.raises(ValueError): change_forecast(rows[1], change)
    else:
        change_forecast(rows[1], change)
        with pytest.raises(ValueError): fit(rows)


def test_future_or_unknown_receipt_and_tamper_fail_before_fitting():
    rows = history(); rows[0]['times']['available_at']=None
    with pytest.raises(LookAheadError): fit(rows)
    rows = history(); rows[0]['payload']['outcome']='away'
    with pytest.raises(LookAheadError): fit(rows)
    rows = history(); rows[0]['payload']['forecast']['times']['available_at']=ASOF.isoformat()
    rehash(rows[0])
    with pytest.raises(LookAheadError): fit(rows)


def test_training_purges_labels_unavailable_at_first_validation_forecast():
    rows = history()
    rows[0]['times']['available_at']=(START+timedelta(days=40)).isoformat()
    a = fit(rows)
    assert a['purged_rows']==1 and a['calibration_rows']==35


def test_insufficient_purged_history_does_not_fit_even_with_fifty_pairs():
    rows = history(50)
    rows[0]['times']['available_at']=(START+timedelta(days=40)).isoformat()
    a = fit(rows)
    assert a['parameters'] is None and a['status']=='INSUFFICIENT_PURGED_CALIBRATION_OR_CLASSES'


def test_validation_labels_never_change_fitted_temperatures_or_weights():
    rows = history(); a = fit(rows)
    for row in rows[36:]:
        row['payload']['outcome']='home'; rehash(row)
    b = fit(rows)
    assert a['parameters']==b['parameters'] and a['validation']!=b['validation']
    assert b['promotion_allowed'] is False


def test_bad_xg_holdout_is_visible_and_never_promoted():
    rows = history()
    for row in rows[36:]:
        row['payload']['outcome']='home'; rehash(row)
    a = fit(rows)
    assert a['validation']['ensemble']['log_loss'] > a['validation']['calibrated_goal_control']['log_loss']
    assert a['validation_improved'] is False and a['promotion_allowed'] is False


def test_duplicate_or_unsettled_calibration_is_rejected():
    rows = history(); rows.append(deepcopy(rows[0])); rows[-1]['record_id']='different'
    with pytest.raises(ValueError, match='DUPLICATE_CALIBRATION_FIXTURE'): fit(rows)
    rows = history(); rows[0]['payload']['settled_at']=START.isoformat(); rehash(rows[0])
    with pytest.raises(ValueError, match='INVALID_SETTLED_OUTCOME'): fit(rows)


def test_early_xg_count_and_missing_pair_do_not_enter_calibration():
    rows = history()
    change_forecast(rows[0], lambda f: f.update(league_xg_matches=2))
    change_forecast(rows[1], lambda f: f['components'].pop('XG'))
    a = fit(rows)
    assert a['paired_rows']==58 and a['issues']['NO_QUALIFIED_PAIRED_XG_FORECAST']==2


def test_prediction_rejects_new_version_target_reuse_and_retroactive_calibration():
    a = fit(); f = forecast()
    f['payload']['components']['GAUSS']['model_version']='unknown'; rehash(f)
    with pytest.raises(ValueError, match='UNCALIBRATED_COMPONENT_VERSION'):
        predict_ensemble(a, f, as_of=ASOF+timedelta(days=1))
    with pytest.raises(ValueError, match='TARGET_ALREADY_IN_CALIBRATION'):
        predict_ensemble(a, forecast(i=1), as_of=ASOF+timedelta(days=1))
    with pytest.raises(ValueError, match='ENSEMBLE_FIT_AFTER_FORECAST'):
        predict_ensemble(a, forecast(when=ASOF-timedelta(hours=1)), as_of=ASOF)


def test_lockout_is_rechecked_at_inference_and_exact_boundary_allowed():
    a = fit(); f = forecast(); kickoff = datetime.fromisoformat(f['payload']['kickoff'])
    assert predict_ensemble(a, f, as_of=kickoff-timedelta(minutes=15))['probabilities']
    with pytest.raises(ValueError, match='PREMATCH_LOCKOUT'):
        predict_ensemble(a, f, as_of=kickoff-timedelta(minutes=14, seconds=59))
    fixture = future_fixture()
    with pytest.raises(ValueError, match='PREMATCH_LOCKOUT'):
        predict_xg(fit_xg_rates(complete(), as_of=NOW), fixture, as_of=NOW+timedelta(minutes=46))


def test_artifacts_detach_recipe_and_input_mutations():
    rows = history(); a = fit(rows); before = deepcopy(a)
    rows[0]['payload']['outcome']='away'
    assert a==before
    a['recipe']['min_calibration']=1
    assert RECIPE['min_calibration']==30
    with pytest.raises(ValueError, match='HASH_MISMATCH'):
        predict_ensemble(a, forecast(), as_of=ASOF+timedelta(days=1))
