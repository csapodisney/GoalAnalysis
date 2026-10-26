"""Final review consumption uses real pure binders with isolated wire fixtures."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_astra_review import KEY, Transport, response
from test_candidate_context_review import NOW, assessed
from test_profile_context_review import card, quote, witnessed
from test_profile_context_stage import execute, inputs

from goal_analysis.agents.astra_review import AstraReviewer
from goal_analysis.engine.recommendations import VERSION
from goal_analysis.jobs import profile_context_review as contract
from goal_analysis.jobs.review_completion import MISSING_REVIEW_WARNING, RETAINED_DRAFT_WARNING


@pytest.fixture(autouse=True)
def fixed_actual_wire_clock(monkeypatch):
    monkeypatch.setattr('goal_analysis.agents.astra_review._utcnow', lambda: NOW)


def classic(source):
    value = card([source], 'high_odds')
    value.update(strategy_version=VERSION, status='DRAFT', construction_verified=True,
                 odds_pending=False, playable=False, selected_for_portfolio=False,
                 confidence='LOW', probability=None, quality_warnings=[MISSING_REVIEW_WARNING])
    value['legs'][0]['missing_support'] = []
    return value


def materialize(value, source, *, now=NOW, covered=None, final_review=None):
    artifacts, sources = witnessed([source])
    bound, issues = contract.bindings(artifacts, sources, [source])
    assert not issues
    if covered is not None:
        bound = covered
    report = {'recommendations': [deepcopy(value)]}
    coverage = contract.audit(report, {}, [source], sources, bound, now=now)
    return contract.apply(report, {}, coverage, bound, mode='ACTIVE',
                          final_review=final_review or {'research_status': 'COMPLETE_WITH_SOURCES'})[0]['recommendations'][0]


def test_completed_exact_review_finishes_only_classic_review_state():
    source = quote()
    original = classic(source)
    result = materialize(original, source)
    assert result['status'] == 'REVIEWED'
    assert not result['playable'] and not result['selected_for_portfolio']
    assert result['probability'] is None and original['status'] == 'DRAFT'
    assert MISSING_REVIEW_WARNING not in result['quality_warnings']
    assert result['review_completion']['status'] == 'COMPLETE_EXACT_SOURCE_CONTEXT_BOUND'
    assert result['legs'][0]['model_probability'] == original['legs'][0]['model_probability']


@pytest.mark.parametrize('field,value', [('construction_verified', False),
                                      ('construction_verified', None), ('odds_pending', True)])
def test_review_completion_does_not_supply_missing_construction_or_price(field, value):
    source = quote()
    original = classic(source)
    original[field] = value
    result = materialize(original, source)
    assert result['status'] == 'DRAFT' and not result['playable']
    assert RETAINED_DRAFT_WARNING in result['quality_warnings']
    assert MISSING_REVIEW_WARNING not in result['quality_warnings']


def test_independent_model_status_probability_and_confidence_stay_independent():
    source = quote()
    original = classic(source)
    original.update(profile_id='gauss', strategy_version='numeric-model-v1', confidence='LOW')
    result = materialize(original, source)
    assert result['status'] == 'DRAFT' and result['confidence'] == 'LOW'
    assert result['legs'][0]['model_probability'] == original['legs'][0]['model_probability']
    assert result['review_completion']['reviewed_legs'] == 1


def test_genuinely_missing_review_keeps_missing_reason_and_other_valid_card_works():
    source = quote()
    value = classic(source)
    pending = materialize(value, source, covered={})
    assert pending['status'] == 'DRAFT'
    assert MISSING_REVIEW_WARNING in pending['quality_warnings']
    assert 'review_completion' not in pending
    assert materialize(value, source)['status'] == 'REVIEWED'


def test_lockout_after_answer_is_not_review_completion_or_publication():
    source = quote()
    result = materialize(classic(source), source, now=NOW + timedelta(hours=2))
    assert result['status'] == 'DRAFT' and 'review_completion' not in result
    assert result['profile_context_review']['legs'][0]['reason'] == 'PROFILE_REVIEW_PREMATCH_LOCKOUT'


@pytest.mark.parametrize('field,value', [('decimal_price', 8.0), ('bookmaker', 'foreign'),
                                      ('quote_source_sha256', 'b' * 64)])
def test_foreign_quote_cannot_clear_missing_review(field, value):
    source = quote()
    value_card = classic(source)
    value_card['legs'][0][field] = value
    result = materialize(value_card, source)
    assert result['status'] == 'DRAFT' and 'review_completion' not in result


def test_optional_unknown_context_is_consumed_without_a_new_global_veto():
    source = quote()
    result = materialize(classic(source), source)
    assert result['review_completion']['unknown_categories']
    assert result['status'] == 'REVIEWED' and not result['playable']


def test_other_quality_warnings_and_conservative_status_are_preserved():
    source = quote()
    original = classic(source)
    original.update(status='ODDS_PENDING')
    original['quality_warnings'].append('Valódi külön bizonytalanság.')
    result = materialize(original, source)
    assert result['status'] == 'ODDS_PENDING'
    assert result['quality_warnings'] == ['Valódi külön bizonytalanság.']


def test_late_supplement_reaches_normal_stage_once_and_consumes_final_research_status():
    values = list(inputs())
    source = values[4][1]
    values[0]['recommendations'] = [classic(source)]
    original_daily = deepcopy(values[0]['tickets'])
    calls = []

    def reviewer(packets, date, feedback=None):
        calls.append(deepcopy(packets))
        transport = Transport(response({'reviews': [assessed(packet) for packet in packets]}))
        return AstraReviewer(KEY, web_search=False, transport=transport).review(packets, date)

    report, _, review = execute(values, SimpleNamespace(review=reviewer))
    assert len(calls) == 1 and len(review['reviews']) == 2
    assert report['recommendations'][0]['status'] == 'REVIEWED'
    assert report['recommendations'][0]['confidence'] == 'MEDIUM'
    assert report['tickets'] == original_daily
    assert values[0]['recommendations'][0]['status'] == 'DRAFT'


def test_original_input_and_saved_answers_are_not_mutated_and_reapply_is_stable():
    source = quote()
    value = classic(source)
    before = deepcopy(value)
    first = materialize(value, source)
    again = materialize(first, source)
    assert value == before
    assert again == first


def test_shadow_does_not_consume_or_relabel_cards():
    source = quote()
    artifacts, sources = witnessed([source])
    bound, _ = contract.bindings(artifacts, sources, [source])
    report = {'recommendations': [classic(source)]}
    before = deepcopy(report)
    coverage = contract.audit(report, {}, [source], sources, bound, now=NOW)
    result, _ = contract.apply(report, {}, coverage, bound, mode='SHADOW')
    assert result is report and result == before
