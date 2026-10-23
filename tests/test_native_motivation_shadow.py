"""Real news/table/source receipts and selector; shared forecasts synthetic."""
import socket
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock

import pytest
from test_motivation_specialist_shadow import NOW, environment, table

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import motivation_specialist_shadow as expert
from goal_analysis.jobs import native_options_shadow as module
from goal_analysis.normalization.training_datasets import validate


def setup(root, monkeypatch, **kwargs):
    data, models, db, *_ = environment(root, **kwargs)
    def chain(*_a, **_k):
        return {'numeric_models': deepcopy(models), 'source_view': {'fixtures': deepcopy(data['fixtures']),
            'dataset': {'manifest': {'dataset_id': '1'*64}}}}
    fit = Mock(side_effect=chain)
    news = Mock(wraps=expert.inspect_human_factors)
    stakes = Mock(wraps=expert.inspect_stakes)
    monkeypatch.setattr(module, 'replay_native_history', fit)
    monkeypatch.setattr(expert, 'inspect_human_factors', news)
    monkeypatch.setattr(expert, 'inspect_stakes', stakes)
    monkeypatch.setattr(socket, 'socket', Mock(side_effect=AssertionError('NO_NETWORK')))
    return data, models, db, fit, news, stakes


def test_source_bound_human_review_is_not_a_motivation_probability_or_live_ticket(tmp_path, monkeypatch):
    data, models, db, fit, news, stakes = setup(tmp_path, monkeypatch)
    table(tmp_path, db)
    original = deepcopy((data, models))
    result = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)
    human = result['options_shadow']['motivation_review']
    assert fit.call_count == news.call_count == stakes.call_count == 1
    assert (data, models) == original and human['native_models_sha256'] == canonical_sha256(models)
    assert human['status'] == 'SHADOW_REVIEW_CANDIDATE' and len(human['candidate_legs']) == 3
    assert human['combined_price'] == pytest.approx(2.3**3)
    assert human['probability'] is human['mental_motivation'] is None
    assert human['probability_adjustment'] == 0 and human['ranking_multiplier'] == 1
    assert human['new_external_requests'] == human['actual_new_shared_model_fits'] == human['production_writes'] == 0
    assert human['playable'] is human['selected_for_portfolio'] is human['live_decision_effect'] is False
    for dataset in human['source_datasets'].values():
        assert validate(dataset) == dataset
    for leg, binding in zip(human['candidate_legs'], human['source_bindings'], strict=True):
        context = leg['motivation_review_context']
        assert binding['leg_sha256'] == canonical_sha256(leg)
        assert binding['context_sha256'] == canonical_sha256(context)
        assert context['claim_truth_status'] == 'UNVERIFIED'
        assert context['mental_motivation'] is None and context['independent_review_required']
        assert context['standings_context']['table_status'] == 'DESCRIPTIVE_TABLE_ONLY'
        assert binding['news_receipt_ids'] and len(binding['news_row_indices']) == len(binding['stakes_row_indices']) == 1
        assert binding['claim_truth_status'] == 'UNVERIFIED' and binding['independent_review_required']
        assert leg['shared_control_probability'] == .6
        for profile in ('gauss', 'laplace'):
            prediction = next(p for p in models[profile]['predictions'] if p['fixture_id'] == leg['fixture_id'])
            assert binding['native_prediction_sha256'][profile] == canonical_sha256(prediction)
        for name, dataset in human['source_datasets'].items():
            assert binding['source_dataset_ids'][name] == dataset['manifest']['dataset_id']
            assert binding['source_lineage_sha256'][name] == dataset['manifest']['lineage_sha256']


@pytest.mark.parametrize('broken', ['no_news', 'old_news', 'receipt', 'future', 'nonhuman',
                                  'control', 'price', 'target_clock', 'duplicate', 'stale_target'])
def test_human_context_cannot_restore_a_failed_source_control_or_clock(tmp_path, monkeypatch, broken):
    kwargs = {'with_news': False} if broken == 'no_news' else {}
    if broken == 'old_news': kwargs['published_hours'] = 49
    if broken == 'receipt': kwargs['witness'] = False
    if broken == 'future': kwargs['available'] = NOW+timedelta(seconds=1)
    if broken == 'nonhuman': kwargs['kind'] = 'INJURY'
    data, models, _, fit, _, _ = setup(tmp_path, monkeypatch, **kwargs)
    if broken == 'control': models.pop('gauss')
    if broken == 'price': data['candidates'][0]['decimal_price'] = 3
    if broken == 'target_clock': data['fixtures'][0]['fixture_source']['source_url'] += '&wrong=1'
    if broken == 'duplicate': data['candidates'].append(deepcopy(data['candidates'][0]))
    cutoff = NOW+timedelta(minutes=31) if broken == 'stale_target' else NOW
    human = module.replay_native_options(tmp_path, {}, data, {}, as_of=cutoff)['options_shadow']['motivation_review']
    assert fit.call_count == 1 and human['status'] == 'ABSTAINED'
    assert human['candidate_legs'] == human['source_bindings'] == []
    assert human['mental_motivation'] is None and human['probability_adjustment'] == 0
    if broken == 'stale_target':
        assert len(human['fixture_evidence']) == 3  # coverage was not erased with stale price scope
        assert human['diagnostics']['human_fixtures'] == 3
        assert human['diagnostics']['target_issues']['STALE_TARGET_METADATA'] == 3


def test_missing_news_stays_unknown_for_each_original_source_bound_fixture(tmp_path, monkeypatch):
    data, _, db, _, _, _ = setup(tmp_path, monkeypatch, with_news=False)
    table(tmp_path, db)
    human = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)['options_shadow']['motivation_review']
    assert human['diagnostics']['news_statuses'] == {'NO_ACCEPTED_HUMAN_CLAIMS': 3}
    assert human['diagnostics']['witnessed_news_imports'] == 0
    assert len(human['fixture_evidence']) == 3 and human['status'] == 'ABSTAINED'
    assert all(v['empty_news_interpretation'] == 'UNKNOWN_COVERAGE_NOT_NO_RISK'
               for v in human['fixture_evidence'].values())


def test_expert_failure_cannot_mutate_native_player_cup_or_xg_outputs(tmp_path, monkeypatch):
    data, _, _, fit, _, _ = setup(tmp_path, monkeypatch)
    control = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)['options_shadow']
    monkeypatch.setattr(module, 'inspect_motivation_specialist', Mock(side_effect=ValueError('sensitive detail')))
    actual = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)['options_shadow']
    assert fit.call_count == 2 and actual['motivation_review']['status'] == 'UNAVAILABLE'
    assert actual['motivation_review']['candidate_legs'] == [] and 'sensitive' not in str(actual)
    for key in ('options', 'cards', 'option_source_bindings', 'player_review', 'cup_review', 'xg_review', 'gaps'):
        assert actual[key] == control[key]


def test_tampered_consumed_news_dataset_is_not_positive_review_evidence(tmp_path, monkeypatch):
    data, _, _, _, _, _ = setup(tmp_path, monkeypatch)
    original = module.inspect_motivation_specialist
    def corrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        result['source_datasets']['news']['rows'][0]['human_factors'][0]['claim'] = 'Invented change'
        return result
    monkeypatch.setattr(module, 'inspect_motivation_specialist', corrupt)
    human = module.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)['options_shadow']['motivation_review']
    assert human['status'] == 'UNAVAILABLE' and human['candidate_legs'] == human['source_bindings'] == []
