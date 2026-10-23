"""Real xG bytes/receipts/rate fit and selector; shared model stub is synthetic."""
import socket
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock

import pytest
from test_xg_specialist_shadow import NOW, environment

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import native_options_shadow as module
from goal_analysis.jobs import xg_specialist_shadow as expert
from goal_analysis.models.xg_ensemble import _verify
from goal_analysis.normalization.training_datasets import validate


def setup(root, monkeypatch, **kwargs):
    data, models, db, meta = environment(root, **kwargs)
    def chain(*_a, **_k):
        return {'numeric_models': deepcopy(models), 'source_view': {'fixtures': deepcopy(data['fixtures']),
            'dataset': {'manifest': {'dataset_id': '1'*64}}}}
    fit = Mock(side_effect=chain)
    rates = Mock(wraps=expert.inspect_xg_rates)
    monkeypatch.setattr(module, 'replay_native_history', fit)
    monkeypatch.setattr(expert, 'inspect_xg_rates', rates)
    monkeypatch.setattr(socket, 'socket', Mock(side_effect=AssertionError('NO_NETWORK')))
    return data, models, fit, rates, db, meta


def test_actual_xg_prefix_drives_separate_leg_predictions_with_one_rate_fit(tmp_path, monkeypatch):
    data, models, fit, rates, _, _ = setup(tmp_path, monkeypatch)
    original = deepcopy((data, models))
    result = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)
    xg = result['options_shadow']['xg_review']
    assert fit.call_count == rates.call_count == 1 and (data, models) == original
    assert xg['status'] == 'SHADOW_CANDIDATE' and len(xg['candidate_legs']) == 3
    assert xg['combined_price'] == pytest.approx(2.3**3)
    assert xg['actual_new_shared_model_fits'] == xg['new_external_requests'] == xg['production_writes'] == 0
    assert xg['playable'] is xg['selected_for_portfolio'] is xg['live_decision_effect'] is False
    assert result['options_shadow']['descriptive_xg_rate_fit_runs'] == 1
    assert result['options_shadow']['summary']['xg_accepted_matches'] == 30
    fit_result = xg['xg_fit']
    _verify(fit_result)
    assert validate(fit_result['dataset']) == fit_result['dataset']
    assert fit_result['dataset']['lineage']
    assert xg['native_models_sha256'] == canonical_sha256(models)
    for leg, binding in zip(xg['candidate_legs'], xg['source_bindings'], strict=True):
        assert .45 < leg['xg_probability'] < .55 and leg['shared_control_probability'] == .6
        assert binding['leg_sha256'] == canonical_sha256(leg)
        assert binding['xg_prediction_sha256'] == canonical_sha256(leg['xg_prediction'])
        assert binding['xg_fit_sha256'] == fit_result['result_sha256']
        assert binding['xg_dataset_id'] == fit_result['dataset']['manifest']['dataset_id']
        assert binding['xg_consumed_row_indices'] == list(range(30))
        assert binding['xg_lineage_sha256'] == fit_result['dataset']['manifest']['lineage_sha256']
        assert binding['target_receipt']['receipt_id'] and binding['price_receipt']['receipt_id']
        assert leg['quoted_at'] is None and leg['score_kind'].endswith('NOT_PROBABILITY')
        for profile in ('gauss', 'laplace'):
            prediction = next(p for p in models[profile]['predictions'] if p['fixture_id'] == leg['fixture_id'])
            assert binding['native_prediction_sha256'][profile] == canonical_sha256(prediction)


@pytest.mark.parametrize('broken', ['price', 'hash', 'clock', 'duplicate', 'control', 'xg_sample',
                                  'neutral', 'cancelled', 'stale'])
def test_no_xg_substitution_for_failed_hard_gate(tmp_path, monkeypatch, broken):
    kwargs = {'history_teams': []} if broken == 'xg_sample' else {}
    if broken == 'neutral': kwargs['target_neutral'] = True
    if broken == 'cancelled': kwargs['status'] = 'cancelled'
    data, models, fit, rates, _, _ = setup(tmp_path, monkeypatch, **kwargs)
    cutoff = NOW
    if broken == 'price': data['candidates'][0]['decimal_price'] = 2.8
    if broken == 'hash': data['candidates'][0]['quote_source_sha256'] = 'a'*64
    if broken == 'clock': data['candidates'][0]['quote_observed_at'] = NOW.isoformat()
    if broken == 'duplicate': data['candidates'].append(deepcopy(data['candidates'][0]))
    if broken == 'control': models.pop('gauss')
    if broken == 'stale': cutoff += timedelta(minutes=31)
    xg = module.replay_native_options(tmp_path, {}, data, {}, as_of=cutoff)['options_shadow']['xg_review']
    assert fit.call_count == rates.call_count == 1
    assert xg['status'] == 'ABSTAINED' and xg['candidate_legs'] == xg['source_bindings'] == []


def test_xg_failure_does_not_change_numeric_cup_or_player_branches(tmp_path, monkeypatch):
    data, _, fit, _, _, _ = setup(tmp_path, monkeypatch)
    control = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)['options_shadow']
    monkeypatch.setattr(module, 'inspect_xg_specialist', Mock(side_effect=ValueError('sensitive detail')))
    actual = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)['options_shadow']
    assert fit.call_count == 2 and actual['xg_review']['status'] == 'UNAVAILABLE'
    for key in ('options', 'cards', 'option_source_bindings', 'player_review', 'cup_review', 'gaps'):
        assert actual[key] == control[key]
    assert 'sensitive' not in str(actual)


def test_changed_xg_fit_cannot_supply_positive_legs(tmp_path, monkeypatch):
    data, _, _, _, _, _ = setup(tmp_path, monkeypatch)
    original = module.inspect_xg_specialist
    def corrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        result['xg_fit']['dataset']['rows'][0]['home_xg'] = 9
        return result
    monkeypatch.setattr(module, 'inspect_xg_specialist', corrupt)
    xg = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)['options_shadow']['xg_review']
    assert xg['status'] == 'UNAVAILABLE' and xg['candidate_legs'] == xg['source_bindings'] == []
