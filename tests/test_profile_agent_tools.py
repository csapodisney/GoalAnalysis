"""Detached TESTONLY values; no provider, DB, fit or historical producer invocation."""
from copy import deepcopy

import pytest

from goal_analysis.agents.profile_decision import (
    ToolRequest,
    record_tool_result,
    request_tool,
    set_options,
    start_sessions,
)
from goal_analysis.agents.profile_roster import get_agent_definition
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.jobs.agent_snapshot import OWNER_IDS, build_snapshot, digest, json_text

NOW = '2026-11-12T18:00:00+00:00'
WINDOW = {'start': '2026-11-01T00:00:00+00:00', 'end': '2026-11-10T00:00:00+00:00'}
POLICY = 'e' * 64


def frame(*, model_state='AVAILABLE', extra_prediction=None, admit=True,
          odds_state='AVAILABLE', extra_quote=None, quote_clock='2026-11-12T17:59:00+00:00',
          invalid_other_models=False):
    fixture = {'fixture_id': 'the_stats_api:mt_1', 'kickoff': '2026-11-13T19:00:00+00:00',
        'provider_fixture_id': 'mt_1', 'provider_league_id': 'comp_test',
        'provider_season_id': 'sn_test', 'provider_home_team_id': 'home_test',
        'provider_away_team_id': 'away_test'}
    quote = {**fixture, 'candidate_id': 'TESTONLY-quote-1', 'market_key': 'totals_2_5',
        'selection_key': 'over', 'period': 'FULL_TIME',
        'settlement': 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES',
        'decimal_price': 2.1, 'bookmaker': 'TESTONLY', 'region': 'eu', 'currency': 'EUR',
        'quote_provider': 'TESTONLY', 'quote_source_id': 'TESTONLY:quote',
        'quote_source_sha256': 'a' * 64, 'quote_observed_at': quote_clock,
        'quoted_at': None}
    prediction = {'fixture_id': fixture['fixture_id'], 'probabilities': {
        'home': .5, 'draw': .25, 'away': .25, 'over25': .6, 'under25': .4, 'btts_yes': .5},
        'model_details': {'cutoff': '2026-11-12T17:00:00+00:00', 'version': 'TESTONLY-model-v1'}}
    predictions = [prediction] + ([] if extra_prediction is None else [extra_prediction])
    models = {'gauss': {'status': 'READY', 'predictions': predictions}}
    if invalid_other_models:
        models.update(kepler=False, unsupported={'status': 'READY', 'predictions': [prediction]})
    producer_value = {'prediction': deepcopy(prediction)}
    manifest = {'TESTONLY:calendar': {'kind': 'TESTONLY'}, 'TESTONLY:quote': {'kind': 'TESTONLY'},
        'TESTONLY:model': {'producer_value': producer_value, 'canonical_value_sha256': digest(producer_value)}}
    outcomes = {'home': ('FULL_TIME:h2h', 'home'), 'draw': ('FULL_TIME:h2h', 'draw'),
        'away': ('FULL_TIME:h2h', 'away'), 'over25': ('FULL_TIME:totals_2_5', 'over'),
        'under25': ('FULL_TIME:totals_2_5', 'under'), 'btts_yes': ('FULL_TIME:btts', 'yes')}
    probabilities = {key: {'fixture_id': fixture['fixture_id'], 'model_id': 'gauss',
        'market': market, 'outcome': outcome, 'value': prediction['probabilities'][key],
        'source_refs': ['TESTONLY:model']} for key, (market, outcome) in outcomes.items()} if admit else {}
    candidates = {'candidates': [quote] + ([] if extra_quote is None else [extra_quote])}
    families = {
        'odds_and_price_history': {'status': odds_state, 'value': {'candidate_input': candidates},
                                  'source_refs': ['TESTONLY:quote'], 'reason': 'TESTONLY diagnostic'},
        'numeric_models': {'status': model_state, 'value': {'results': models},
                          'source_refs': ['TESTONLY:model'], 'reason': 'TESTONLY diagnostic'},
    }
    fixtures = [fixture, {'fixture_id': 'the_stats_api:mt_2'}, {'fixture_id': 'the_stats_api:mt_3'}]
    ids = [row['fixture_id'] for row in fixtures]
    snapshot = build_snapshot(run_id='TESTONLY-ten-agent-tools', target_date='2026-11-13', as_of=NOW,
        authoritative_fixture_ids=ids, calendar_receipt={'target_date': '2026-11-13',
            'scope': 'FULL_PROVIDER_DAY', 'status': 'COMPLETE', 'fixture_ids': ids,
            'source_refs': ['TESTONLY:calendar']}, fixtures=fixtures,
        family_values=families, source_manifest=manifest, model_probabilities=probabilities)
    options = [{'option_id': 'TESTONLY-own-1', 'agent_id': OWNER_IDS[0], 'legs': [deepcopy(quote)]}]
    return snapshot, options


def own_request(snapshot, options, role='candidate_scenarios', *, parameters=None):
    sessions = start_sessions(snapshot, {owner: get_agent_definition(owner).method_version for owner in OWNER_IDS})
    state = set_options(sessions[0], snapshot, options)
    return request_tool(state, snapshot, role, parameters=parameters)


def rehash_request(request, **changes):
    payload = request.data() | changes
    payload.pop('request_id')
    payload['request_id'] = digest(payload)
    return ToolRequest(json_text(payload))


def injected_scenario(report, artifacts, cards, *, as_of, enabled):
    assert report['run_id'] == 'TESTONLY-ten-agent-tools'
    assert as_of == NOW and enabled is True
    assert len(artifacts['fixtures']) == 3
    assert len(artifacts['numeric_models']['gauss']['predictions']) == 1
    assert set(artifacts['numeric_models']) == {'gauss'}
    p = artifacts['numeric_models']['gauss']['predictions'][0]['probabilities']['over25']
    return {'status': 'PARTIAL', 'assumptions': ['TESTONLY uncalibrated scenario'], 'profiles': [
        {'profile_id': row['profile_id'], 'source_card_sha256': digest(row),
         'models': {'gauss': {'known_leg_event_probability': p}},
         'risk_facts': ['Historical profitability is unknown.']} for row in cards]}


def envelope(kind, payload, available_at):
    return {'kind': kind, 'payload': payload, 'sha256': digest(payload), 'available_at': available_at}


def history_record(request):
    payload = request.data()
    decision, settled = '2026-11-03T12:00:00+00:00', '2026-11-03T15:00:00+00:00'
    selection, model = 'TESTONLY-historical-selection', 'b' * 64
    sources = {
        'features': envelope('PIT_FEATURES', {'selection_id': selection, 'model_sha256': model},
                             '2026-11-03T11:00:00+00:00'),
        'quote': envelope('HISTORICAL_PROVIDER_QUOTE', {'selection_id': selection,
            'quoted_at': '2026-11-03T11:59:00+00:00', 'decimal_odds': 2., 'currency': 'EUR'},
            '2026-11-03T11:59:01+00:00'),
        'decision': envelope('HISTORICAL_STRATEGY_DECISION', {
            'selection_id': selection, 'model_sha256': model, 'decision_at': decision,
            'agent_id': payload['agent_id'], 'method_version': payload['method_version'],
            'policy_hash': POLICY}, decision),
        'settlement': envelope('SETTLEMENT', {'selection_id': selection, 'settled_at': settled,
            'stake': 1., 'gross_return': 2., 'currency': 'EUR'}, '2026-11-03T15:01:00+00:00'),
    }
    return {key: payload[key] for key in ('agent_id', 'method_version', 'option_hash', 'own_option_ids')} | {
        'evaluation_id': 'TESTONLY-chronological-evaluation', 'evaluation_as_of': '2026-11-11T00:00:00+00:00',
        'window': deepcopy(WINDOW), 'policy_hash': POLICY, 'sources': sources, 'observations': [
            {'observation_id': 'TESTONLY-observation', 'selection_id': selection, 'decision_at': decision,
             'kickoff': '2026-11-03T13:00:00+00:00', 'settled_at': settled, 'model_sha256': model,
             'feature_source': 'features', 'quote_source': 'quote', 'decision_source': 'decision',
             'settlement_source': 'settlement'}]}


def historical():
    snapshot, options = frame()
    _, request = own_request(snapshot, options, 'historical_strategy_evaluation',
        parameters={'window': WINDOW, 'policy_hash': POLICY})
    return snapshot, options, request, history_record(request)


def test_candidate_actual_values_and_receipt_are_bound_to_own_options():
    snapshot, options = frame()
    state, request = own_request(snapshot, options)
    before = deepcopy(options)
    result = ProfileToolService(snapshot, scenario_builder=injected_scenario).evaluate(request, options)
    assert result['status'] == 'AVAILABLE'
    value = result['value']['by_option'][options[0]['option_id']]
    assert value['models']['gauss']['known_leg_event_probability'] == .6
    assert result['value']['coverage_status'] == 'PARTIAL'
    assert result['value']['historical_profit_or_calibration_claim'] is False
    assert options == before
    updated = record_tool_result(state, snapshot, request, result,
        interpretation='Own test option has a conditional scenario value, with unresolved history risk.')
    assert len(updated.receipts) == 1


def test_existing_pure_scenario_kernel_receives_exact_retained_source_and_model():
    snapshot, options = frame()
    _, request = own_request(snapshot, options)
    result = ProfileToolService(snapshot).evaluate(request, options)
    assert result['status'] == 'AVAILABLE'
    facts = result['value']['by_option'][options[0]['option_id']]['models']['gauss']
    assert facts['scenario_model_full_card_probability'] == pytest.approx(.6)
    assert facts['leg_bindings'][0]['source_binding']['provider_quote_clock_known'] is False
    assert result['physical_source_validation'] == 'NOT_PERFORMED'


def test_unknown_model_is_not_synthesized_from_odds_or_half_probability():
    snapshot, options = frame(admit=False)
    _, request = own_request(snapshot, options)
    result = ProfileToolService(snapshot).evaluate(request, options)
    assert result['status'] == 'UNKNOWN'
    assert result['value'] is None


@pytest.mark.parametrize('change', ['price', 'option_id', 'owner'])
def test_changed_or_foreign_options_cannot_reuse_a_request(change):
    snapshot, options = frame()
    _, request = own_request(snapshot, options)
    if change == 'price':
        options[0]['legs'][0]['decimal_price'] += .1
    elif change == 'option_id':
        options[0]['option_id'] = 'another-option'
    else:
        options[0]['agent_id'] = OWNER_IDS[1]
    with pytest.raises(ValueError, match='STALE_TOOL_OPTIONS'):
        ProfileToolService(snapshot).evaluate(request, options)


@pytest.mark.parametrize('defect,reason', [
    ('owner', 'FOREIGN_OWNER_OR_SNAPSHOT'), ('cutoff', 'FOREIGN_TOOL_CUTOFF'),
    ('method', 'UNBOUND_TOOL_METHOD_VERSION'), ('hash', 'TOOL_REQUEST_HASH_MISMATCH'),
])
def test_owner_method_cutoff_and_request_integrity_are_checked(defect, reason):
    snapshot, options = frame()
    _, request = own_request(snapshot, options)
    if defect == 'owner':
        request = rehash_request(request, agent_id=OWNER_IDS[1])
    elif defect == 'cutoff':
        request = rehash_request(request, as_of='2026-11-12T19:00:00+00:00')
    elif defect == 'method':
        method = 'unregistered-method'
        request = rehash_request(request, method_version=method,
            session_id=digest([OWNER_IDS[0], method, snapshot.snapshot_id]))
    else:
        request = ToolRequest(json_text(request.data() | {'request_id': '0' * 64}))
    with pytest.raises(ValueError, match=reason):
        ProfileToolService(snapshot).evaluate(request, options)


def test_unrelated_future_quote_is_local_and_preserves_valid_own_scenario():
    unrelated = {'candidate_id': 'TESTONLY-unrelated', 'fixture_id': 'the_stats_api:mt_2',
                 'quote_observed_at': '2030-01-01T00:00:00+00:00'}
    snapshot, options = frame(odds_state='CONFLICTING', extra_quote=unrelated)
    _, request = own_request(snapshot, options)
    result = ProfileToolService(snapshot).evaluate(request, options)
    assert result['status'] == 'AVAILABLE'
    assert result['value']['odds_family_diagnostic']['status'] == 'CONFLICTING'
    assert result['value']['by_option'][options[0]['option_id']]['models']['gauss'][
        'scenario_model_full_card_probability'] == pytest.approx(.6)
    assert len(snapshot.fixture_ids) == 3


@pytest.mark.parametrize('defect', ['future', 'naive', 'duplicate_with_one_valid_row'])
def test_selected_invalid_clock_or_ambiguous_quote_cannot_become_a_scenario(defect):
    if defect == 'duplicate_with_one_valid_row':
        _, first = frame()
        duplicate = deepcopy(first[0]['legs'][0])
        duplicate['quote_observed_at'] = '2030-01-01T00:00:00+00:00'
        snapshot, options = frame(odds_state='CONFLICTING', extra_quote=duplicate)
    else:
        snapshot, options = frame(odds_state='CONFLICTING', quote_clock=
            '2030-01-01T00:00:00+00:00' if defect == 'future' else '2026-11-12T17:59:00')
    _, request = own_request(snapshot, options)
    result = ProfileToolService(snapshot).evaluate(request, options)
    assert result['status'] == 'UNKNOWN' and result['value'] is None


def test_unadmitted_raw_prediction_cannot_reenter_the_scenario_model():
    rejected = {'fixture_id': 'the_stats_api:mt_1', 'probabilities': {'over25': .99},
                'model_details': {'cutoff': '2030-01-01T00:00:00+00:00'}}
    snapshot, options = frame(model_state='CONFLICTING', extra_prediction=rejected)
    _, request = own_request(snapshot, options)
    result = ProfileToolService(snapshot, scenario_builder=injected_scenario).evaluate(request, options)
    assert result['value']['by_option'][options[0]['option_id']]['models']['gauss'][
        'known_leg_event_probability'] == .6
    assert result['value']['numeric_family_diagnostic']['status'] == 'CONFLICTING'


def test_malformed_or_unsupported_other_model_does_not_veto_admitted_baseline():
    snapshot, options = frame(invalid_other_models=True)
    _, request = own_request(snapshot, options)
    result = ProfileToolService(snapshot, scenario_builder=injected_scenario).evaluate(request, options)
    assert result['status'] == 'AVAILABLE'
    assert result['value']['by_option'][options[0]['option_id']]['models']['gauss'][
        'known_leg_event_probability'] == .6


def test_detached_result_cache_cannot_be_mutated_by_a_previous_caller():
    snapshot, options = frame()
    _, request = own_request(snapshot, options)
    service = ProfileToolService(snapshot, scenario_builder=injected_scenario)
    first = service.evaluate(request, options)
    identity = first['result_id']
    first['value']['by_option'].clear()
    cached = service.evaluate(request, options)
    assert cached['status'] == 'CACHED' and cached['cached_from_result_id'] == identity
    assert options[0]['option_id'] in cached['value']['by_option']


def test_missing_historical_producer_returns_unknown_without_fake_roi():
    snapshot, options, request, _ = historical()
    result = ProfileToolService(snapshot).evaluate(request, options)
    assert result['status'] == 'UNKNOWN' and result['value'] is None


def test_original_price_history_keeps_gross_and_unknown_cost_separate():
    snapshot, options, request, record = historical()
    service = ProfileToolService(snapshot, historical_evaluations=[record])
    record['sources']['settlement']['payload']['gross_return'] = 999
    result = service.evaluate(request, options)
    value = result['value']
    assert result['status'] == 'AVAILABLE'
    assert value['gross_return'] == 2 and value['net_before_cost'] == 1 and value['roi_before_cost'] == 1
    assert value['allocated_cost'] is value['net_after_cost'] is value['roi_after_cost'] is None
    assert value['cost_status'] == 'UNKNOWN_COST_OR_FX'
    assert value['source_evidence_boundary'].endswith('NOT_PHYSICALLY_AUTHENTICATED')


@pytest.mark.parametrize('defect', ['future_quote', 'future_evaluation', 'early_evaluation',
    'early_settlement_receipt', 'duplicate', 'same_settlement', 'verified_only', 'changed_option'])
def test_unbound_or_nonchronological_history_is_not_profit_evidence(defect):
    snapshot, options, request, record = historical()
    if defect == 'future_quote':
        record['sources']['quote']['available_at'] = '2026-11-13T00:00:00+00:00'
    elif defect == 'future_evaluation':
        record['evaluation_as_of'] = '2026-11-13T00:00:00+00:00'
    elif defect == 'early_evaluation':
        record['evaluation_as_of'] = WINDOW['start']
    elif defect == 'early_settlement_receipt':
        record['sources']['settlement']['available_at'] = '2026-11-03T14:00:00+00:00'
    elif defect in {'duplicate', 'same_settlement'}:
        repeated = deepcopy(record['observations'][0])
        if defect == 'same_settlement':
            repeated['observation_id'] = 'TESTONLY-second-observation'
        record['observations'].append(repeated)
    elif defect == 'verified_only':
        record.update(verified=True, sources={})
    else:
        record['option_hash'] = '0' * 64
    result = ProfileToolService(snapshot, historical_evaluations=[record]).evaluate(request, options)
    assert result['status'] == 'UNKNOWN' and result['value'] is None


@pytest.mark.parametrize('defect', ['future', 'wrong_allocation', 'missing_fx', 'future_fx', 'wrong_fx_clock'])
def test_optional_cost_or_fx_error_preserves_valid_before_cost_history(defect):
    snapshot, options, request, record = historical()
    row = record['observations'][0]
    row['cost_source'] = 'cost'
    cost = {'amount': .1, 'currency': 'USD', 'observation_id': row['observation_id'],
            'selection_id': row['selection_id'], 'fx_conversion_at': '2026-11-04T00:00:00+00:00'}
    record['sources']['cost'] = envelope('ALLOCATED_COST', cost, '2026-11-04T00:01:00+00:00')
    if defect == 'future':
        record['sources']['cost']['available_at'] = '2030-01-01T00:00:00+00:00'
    elif defect == 'wrong_allocation':
        cost['observation_id'] = 'another-observation'
        record['sources']['cost']['sha256'] = digest(cost)
    elif defect in {'future_fx', 'wrong_fx_clock'}:
        row['fx_source'] = 'fx'
        fx = {'base_currency': 'USD', 'quote_currency': 'EUR', 'rate': .9,
              'quoted_at': '2026-11-04T00:00:00+00:00'}
        if defect == 'wrong_fx_clock':
            fx['quoted_at'] = '2026-11-03T00:00:00+00:00'
        record['sources']['fx'] = envelope('HISTORICAL_FX', fx,
            '2030-01-01T00:00:00+00:00' if defect == 'future_fx' else '2026-11-04T00:01:00+00:00')
    result = ProfileToolService(snapshot, historical_evaluations=[record]).evaluate(request, options)
    assert result['status'] == 'AVAILABLE'
    assert result['value']['net_before_cost'] == 1
    assert result['value']['net_after_cost'] is None


def test_exact_allocated_cost_and_historical_fx_keep_their_source_clocks():
    snapshot, options, request, record = historical()
    row = record['observations'][0]
    row.update(cost_source='cost', fx_source='fx')
    at = '2026-11-04T00:00:00+00:00'
    record['sources']['cost'] = envelope('ALLOCATED_COST', {'amount': .1, 'currency': 'USD',
        'observation_id': row['observation_id'], 'selection_id': row['selection_id'], 'fx_conversion_at': at}, at)
    record['sources']['fx'] = envelope('HISTORICAL_FX', {
        'base_currency': 'USD', 'quote_currency': 'EUR', 'rate': .9, 'quoted_at': at}, at)
    result = ProfileToolService(snapshot, historical_evaluations=[record]).evaluate(request, options)
    assert result['status'] == 'AVAILABLE'
    assert result['value']['allocated_cost'] == pytest.approx(.09)
    assert result['value']['net_after_cost'] == pytest.approx(.91)
