"""Actual raw/receipt/player readers; controlled model injection is synthetic."""
import socket
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock

import pytest
from test_player_specialist_shadow import NOW, environment

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.portfolio import validate_settings
from goal_analysis.jobs import native_options_shadow as module


def setup(root, monkeypatch):
    data, models, _, _ = environment(root)
    # The entry point has no supplied-model argument. Unit tests alone replace
    # the actual strict fitter; SQLite bytes/receipt/context readers stay real.
    run = {'mode': 'SHADOW', 'as_of': NOW.isoformat(), 'numeric_models': models,
           'source_view': {'fixtures': data['fixtures'], 'dataset': {'manifest': {'dataset_id': '1'*64}}}}
    fit = Mock(side_effect=lambda *_a, **_k: deepcopy(run))
    monkeypatch.setattr(module, 'replay_native_history', fit)
    return data, models, fit


def test_real_options_to_real_player_review_and_each_leg_bound_to_same_model_and_raw_receipts(tmp_path, monkeypatch):
    data, models, fit = setup(tmp_path, monkeypatch)
    original = deepcopy(data)
    report = {'recommendations': [{'profile_id': 'daily223', 'legs': []}], 'sentinel': 'LIVE_UNCHANGED'}
    with pytest.MonkeyPatch.context() as net:
        net.setattr(socket, 'socket', Mock(side_effect=AssertionError('NO_NETWORK')))
        result = module.replay_native_options(tmp_path, report, data,
                                               validate_settings({'operating_contract_version': 1}), as_of=NOW,
                                               profiles=('gauss', 'laplace'))
    options = result['options_shadow']
    assert fit.call_count == 1 and result['numeric_models'] == models and data == original
    assert report == {'recommendations': [{'profile_id': 'daily223', 'legs': []}], 'sentinel': 'LIVE_UNCHANGED'}
    assert options['summary']['complete_in_band_options'] > 0
    assert options['summary']['player_options_reviewed'] == options['summary']['complete_in_band_options']
    assert options['summary']['player_supported_selections'] == 3
    assert options['player_review']['summary']['hypothetically_weighted_options'] > 0
    for binding in options['option_source_bindings']:
        card = options['options'][binding['profile_id']][binding['option_index']]['recommendation']
        assert 10 <= card['combined_price'] <= 50 and len(card['legs']) == 3
        assert not card['playable'] and not card['selected_for_portfolio'] and card['stake_eur'] is None
        for leg, proof in zip(card['legs'], binding['legs']):
            assert leg['quoted_at'] is None and leg['model_probability'] == .6
            prediction = next(p for p in models[binding['profile_id']]['predictions']
                              if p['fixture_id'] == leg['fixture_id'])
            assert proof['native_prediction_sha256'] == canonical_sha256(prediction)
            assert proof['price_receipt']['receipt_id'] and proof['target_receipt']['receipt_id']
            assert proof['leg_sha256'] == canonical_sha256(leg)
    assert options['additional_model_fit_runs'] == options['external_requests'] == options['production_writes'] == 0


def test_same_actual_selector_builds_cup_on_strict_targets_without_second_fit(tmp_path, monkeypatch):
    data, models, fit = setup(tmp_path, monkeypatch)
    original = deepcopy((data, models))
    result = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)
    cup = result['options_shadow']['cup_review']
    assert fit.call_count == 1 and (data, models) == original
    assert cup['status'] == 'SHADOW_CANDIDATE' and len(cup['candidate_legs']) == 3
    assert cup['combined_price'] == pytest.approx(2.3**3)
    assert cup['native_models_sha256'] == canonical_sha256(result['numeric_models'])
    assert cup['additional_model_fits'] == cup['external_requests'] == cup['production_writes'] == 0
    assert cup['playable'] is cup['selected_for_portfolio'] is cup['live_decision_effect'] is False
    assert cup['diagnostics']['required_two_model_fixture_count'] == 3
    for leg, binding in zip(cup['candidate_legs'], cup['source_bindings'], strict=True):
        assert binding['leg_sha256'] == canonical_sha256(leg)
        assert binding['target_receipt']['receipt_id'] and binding['price_receipt']['receipt_id']
        assert len(binding['taxonomy_receipt_ids']) == 2
        assert binding['native_input_dataset_id'] == '1'*64
        for profile in ('gauss', 'laplace'):
            prediction = next(p for p in models[profile]['predictions'] if p['fixture_id'] == leg['fixture_id'])
            assert binding['native_prediction_sha256'][profile] == canonical_sha256(prediction)


@pytest.mark.parametrize('broken', ['price', 'target_clock', 'quote_clock', 'duplicate', 'model'])
def test_cup_cannot_restore_a_rejected_source_or_missing_control(tmp_path, monkeypatch, broken):
    data, models, fit = setup(tmp_path, monkeypatch)
    if broken == 'price':
        data['candidates'][0]['decimal_price'] = 2.8
    elif broken == 'target_clock':
        data['fixtures'][0]['fixture_source']['source_url'] += '&wrong=1'
    elif broken == 'quote_clock':
        data['candidates'][0]['quote_observed_at'] = NOW.isoformat()
    elif broken == 'duplicate':
        data['candidates'].append(deepcopy(data['candidates'][0]))
    else:
        models.pop('gauss')
        fit.side_effect = lambda *_a, **_k: {'numeric_models': deepcopy(models), 'source_view': {
            'fixtures': data['fixtures'], 'dataset': {'manifest': {'dataset_id': '1'*64}}}}
    result = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)
    assert fit.call_count == 1
    cup = result['options_shadow']['cup_review']
    assert cup['status'] == 'ABSTAINED' and cup['candidate_legs'] == cup['source_bindings'] == []


def test_cup_failure_is_local_and_cannot_change_native_cards_or_player_review(tmp_path, monkeypatch):
    data, _, fit = setup(tmp_path, monkeypatch)
    control = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)['options_shadow']
    monkeypatch.setattr(module, 'inspect_cup_specialist', Mock(side_effect=RuntimeError('sensitive detail')))
    actual = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)['options_shadow']
    assert fit.call_count == 2
    for key in ('cards', 'options', 'option_source_bindings', 'player_review', 'gaps'):
        assert actual[key] == control[key]
    assert actual['cup_review']['status'] == 'UNAVAILABLE'
    assert actual['cup_review']['candidate_legs'] == []
    assert 'sensitive' not in str(actual)


@pytest.mark.parametrize('broken', ['price', 'hash', 'target_clock', 'quote_clock', 'quoted_at', 'identity', 'duplicate'])
def test_wrong_stale_ambiguous_or_unproven_input_never_becomes_in_band_option(tmp_path, monkeypatch, broken):
    data, _, _ = setup(tmp_path, monkeypatch)
    if broken == 'price':
        data['candidates'][0]['decimal_price'] = 2.8
    elif broken == 'hash':
        data['candidates'][0]['quote_source_sha256'] = 'a'*64
    elif broken == 'target_clock':
        # Native reader's return is synthetic here, but the real target reader
        # still validates source bytes/metadata and cannot accept this alias.
        data['fixtures'][0]['fixture_source']['source_url'] += '&wrong=1'
    elif broken == 'quote_clock':
        data['candidates'][0]['quote_observed_at'] = NOW.isoformat()
    elif broken == 'quoted_at':
        data['candidates'][0]['quoted_at'] = NOW.isoformat()
    elif broken == 'identity':
        data['candidates'][0]['provider_home_team_id'] = 'tm_999'
    else:
        data['candidates'].append(deepcopy(data['candidates'][0]))
    result = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)
    assert result['options_shadow']['summary']['complete_in_band_options'] == 0
    assert not any(r['recommendation']['legs'] for rows in result['options_shadow']['options'].values() for r in rows)


@pytest.mark.parametrize('clock', [NOW+timedelta(minutes=31), NOW+timedelta(hours=6)-timedelta(minutes=15)])
def test_stale_or_locked_target_is_not_repaired_by_a_fresh_fit(tmp_path, monkeypatch, clock):
    data, _, _ = setup(tmp_path, monkeypatch)
    result = module.replay_native_options(tmp_path, {}, data, {}, as_of=clock)['options_shadow']
    assert result['summary']['fresh_source_bound_target_fixtures'] == 0
    assert result['summary']['complete_in_band_options'] == 0


def test_missing_price_never_appears_as_odds_pending_publishable_filler(tmp_path, monkeypatch):
    data, _, _ = setup(tmp_path, monkeypatch)
    data['candidates'] = []
    result = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)['options_shadow']
    assert result['summary']['complete_in_band_options'] == 0
    assert all(not card['legs'] and not card['playable'] for card in result['cards'])


def test_thin_control_can_supply_own_option_but_not_positive_two_model_player_vote(tmp_path, monkeypatch):
    data, models, _ = setup(tmp_path, monkeypatch)
    models.pop('gauss')
    run = {'numeric_models': models, 'source_view': {'fixtures': data['fixtures'],
            'dataset': {'manifest': {'dataset_id': '1'*64}}}}
    monkeypatch.setattr(module, 'replay_native_history', lambda *_a, **_k: deepcopy(run))
    result = module.replay_native_options(tmp_path, {}, data,
        validate_settings({'operating_contract_version': 1}), as_of=NOW)['options_shadow']
    assert result['summary']['complete_in_band_options'] > 0
    assert result['summary']['player_supported_selections'] == 0
    assert result['player_review']['summary']['hypothetically_weighted_options'] == 0


def test_oversized_input_rejected_before_any_fit_or_database_read(tmp_path, monkeypatch):
    fit = Mock()
    monkeypatch.setattr(module, 'replay_native_history', fit)
    with pytest.raises(ValueError, match='BOUNDED'):
        module.replay_native_options(tmp_path, {}, {'fixtures': [], 'candidates': [{}]*10001}, {}, as_of=NOW)
    fit.assert_not_called()


@pytest.mark.parametrize('flag', [None, 1, 'LIVE', True])
def test_invalid_or_unrooted_flag_fails_before_running_the_live_pipeline(flag):
    from goal_analysis.jobs.portfolio import run_portfolio

    with pytest.raises(ValueError, match='native_option_shadow'):
        run_portfolio(None, None, None, None, None, None, native_option_shadow=flag)


@pytest.mark.parametrize('enabled,fail', [(False, False), (True, False), (True, True)])
def test_real_coordinator_calls_one_branch_and_cannot_change_live_models_cards_or_daily(tmp_path, monkeypatch, enabled, fail):
    from test_model_integration import predictor_fixture
    from test_portfolio_flow import NOW as FLOW_NOW
    from test_portfolio_flow import RecordedReviewer
    from test_portfolio_flow import environment as flow_environment

    from goal_analysis.jobs import native_history_shadow
    from goal_analysis.jobs.portfolio import run_portfolio

    calls = []
    def chain(*args, **kwargs):
        calls.append((args, kwargs))
        if fail:
            raise ValueError('synthetic optional failure')
        return {'mode': 'SHADOW', 'options_shadow': {'decision_effect': 'NONE'}}
    monkeypatch.setattr(module, 'replay_native_options', chain)
    plain = Mock(return_value={'mode': 'SHADOW'})
    monkeypatch.setattr(native_history_shadow, 'replay_native_history', plain)
    def run(path, flag):
        cfg, football, odds, cache, _, _, _ = flow_environment(path)
        return run_portfolio(cfg, {}, football, odds, cache, FLOW_NOW.date(), RecordedReviewer(),
            clock=lambda: FLOW_NOW, model_predictor=predictor_fixture,
            native_history_shadow_root=tmp_path, native_option_shadow=flag)['artifacts']
    control, actual = run(tmp_path/'control', False), run(tmp_path/'actual', enabled)
    assert len(calls) == int(enabled) and plain.call_count == 2-int(enabled)
    assert control['numeric_models'] == actual['numeric_models']
    for group in ('tickets','recommendations','profile_proposals','preview_tickets'):
        assert control['report'].get(group) == actual['report'].get(group)
