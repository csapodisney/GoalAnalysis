import json
from copy import deepcopy

import pytest

from goal_analysis.agents.codex_review import CodexReviewer
from goal_analysis.agents.profile_decision import (
    SIMULATOR_ROLES,
    consider_family,
    consider_fixture,
    finalize_decisions,
    record_tool_result,
    request_tool,
    set_options,
    start_sessions,
)
from goal_analysis.agents.profile_decision_runner import profile_response_schema
from goal_analysis.agents.profile_roster import ACTIVE_AGENTS
from goal_analysis.jobs.agent_snapshot import build_snapshot
from goal_analysis.ticket_product_policy import product_contract


def test_each_persona_plan_gets_ranking_policy_with_exact_data_schema_and_no_extra_calls():
    reviewer = CodexReviewer.__new__(CodexReviewer)
    calls = []

    def inert(body, usage):
        calls.append(deepcopy(body))
        return {'output': [{'content': [{'type': 'output_text', 'text': '{}'}]}]}

    reviewer._request = inert
    for agent in ACTIVE_AGENTS:
        request = {'agent_id': agent.agent_id, 'phase': 'PLAN',
                   'session_id': 'TESTONLY-session-' + agent.agent_id, 'snapshot_id': 'TESTONLY-snapshot',
                   'persona': agent.as_dict(), 'data': {'snapshot': {'fixture_ids': ['TESTONLY-fixture']}}}
        original = deepcopy(request)
        reviewer.request_profile_decision(request, timeout=5)
        body = calls[-1]
        text = body['instructions']
        assert 'PLAN_BEST_LEGAL_RESEARCH_OPTIONS_V1' in text
        assert 'FINAL_BEST_LEGAL_RESEARCH_OPTION_V1' not in text
        assert 'precedence over qualitative abstention suggestions in persona.selection_path' in text
        assert 'alone must not make options empty or set fixture_reviews.rejected=true' in text
        assert 'Never mark a fixture rejected if you propose a leg from that fixture' in text
        assert 'Both tool requests remain mandatory' in text
        assert 'not a requirement for an AVAILABLE result' in text
        assert 'Never invent candidate IDs, prices or probabilities or waive a required own model' in text
        assert 'If no complete own option meets the mandatory rules, return an empty options list' in text
        assert json.loads(body['input']) == request == original
        assert body['text']['format']['schema'] == profile_response_schema('PLAN')
        assert body['_arthur_profile_timeout'] == 5
    assert len(calls) == len(ACTIVE_AGENTS) == 10


def _valid_product_state():
    ids = [f'TESTONLY-fixture-{number}' for number in range(7)]
    snapshot = build_snapshot(
        run_id='TESTONLY-unknown-tools-new-product', target_date='2026-11-14',
        as_of='2026-11-13T12:00:00+00:00', authoritative_fixture_ids=ids,
        calendar_receipt={'target_date': '2026-11-14', 'scope': 'FULL_PROVIDER_DAY',
                          'status': 'COMPLETE', 'fixture_ids': ids, 'source_refs': ['TESTONLY-calendar']},
        fixtures=[{'fixture_id': key} for key in ids], family_values={},
        source_manifest={'TESTONLY-calendar': 'a' * 64},
    )
    initial = next(row for row in start_sessions(snapshot, {
        agent.agent_id: agent.method_version for agent in ACTIVE_AGENTS}) if row.agent_id == 'ritmusor')
    legs = [{'fixture_id': key, 'market_key': 'btts', 'selection_key': 'yes',
             'period': 'FULL_TIME', 'decimal_price': 2.0} for key in ids[:4]]
    option = {'agent_id': 'ritmusor', 'option_id': 'TESTONLY-own-option',
              'reason': 'TESTONLY research selection with uncertainty; not a wager',
              'legs': deepcopy(legs), 'canonical_card': {
                  'profile_id': 'ritmusor', 'combined_price': 16.0, 'legs': legs,
                  'ticket_product_contract': product_contract(), 'status': 'DRAFT'}}
    state = set_options(initial, snapshot, [option])
    for fixture in ids:
        state = consider_fixture(state, snapshot, fixture,
                                 reason='Considered; optional context unknown', rejected=False)
    for family, value in snapshot.data()['families'].items():
        available = value['status'] == 'AVAILABLE'
        state = consider_family(state, snapshot, family,
                                status='AVAILABLE_AND_CONSIDERED' if available else 'MISSING',
                                reason='Actual fixture state retained', value_sha256=value['value_sha256'],
                                option_ids=[option['option_id']] if available else [],
                                risk='Not evidence of profit')
    return initial, state, snapshot, option


def test_unknown_tools_allow_new_product_selection_but_missing_receipts_and_product_guards_still_fail():
    initial, state, snapshot, option = _valid_product_state()
    with pytest.raises(ValueError, match='MISSING_SIMULATION_RECEIPT'):
        finalize_decisions(state, snapshot, option_ids=[option['option_id']], reason='TESTONLY')
    for role in SIMULATOR_ROLES:
        state, request = request_tool(state, snapshot, role)
        payload = request.data()
        result = {key: payload[key] for key in ('request_id', 'agent_id', 'snapshot_id', 'option_hash')}
        result.update(status='UNKNOWN', value=None, reason='TESTONLY_NO_ACTUAL_SIMULATOR_SUPPORT',
                      own_option_ids=payload['own_option_ids'])
        state = record_tool_result(state, snapshot, request, result,
                                   interpretation='Unknown support is a stated limitation, not invented probability')
    _, decision = finalize_decisions(state, snapshot, option_ids=[option['option_id']],
                                     reason='Best admitted research option; both actual tool values UNKNOWN')
    output = decision.data()
    assert output['status'] == 'FINAL_DECISION' and output['option_ids'] == [option['option_id']]
    assert output['selected_options'] == [option]
    assert output['selected_options'][0]['canonical_card']['status'] == 'DRAFT'
    assert all(row['status'] == 'UNKNOWN' and row['value'] is None for row in output['tool_receipts'])
    assert {row['role'] for row in output['tool_receipts']} == set(SIMULATOR_ROLES)
    assert all(row['status'] == 'MISSING' for name, row in output['family_uses'].items() if name != 'calendar')
    rejected = consider_fixture(state, snapshot, snapshot.fixture_ids[0],
                                reason='TESTONLY actual exclusion', rejected=True)
    with pytest.raises(ValueError, match='SELECTED_FIXTURE_REJECTED'):
        finalize_decisions(rejected, snapshot, option_ids=[option['option_id']], reason='TESTONLY')
    with pytest.raises(ValueError, match='FOREIGN_FINAL_OPTION'):
        finalize_decisions(state, snapshot, option_ids=['TESTONLY-foreign'], reason='TESTONLY')
    low = deepcopy(option)
    low['legs'][0]['decimal_price'] = low['canonical_card']['legs'][0]['decimal_price'] = 1.1
    low['canonical_card']['combined_price'] = 8.8
    with pytest.raises(ValueError, match='OUTSIDE_OWN_APPROVED_ODDS_RANGE'):
        set_options(initial, snapshot, [low])
    seven = deepcopy(option)
    seven['legs'] += [{'fixture_id': key, 'market_key': 'btts', 'selection_key': 'yes',
                       'period': 'FULL_TIME', 'decimal_price': 2.0} for key in snapshot.fixture_ids[4:]]
    seven['canonical_card']['legs'] = deepcopy(seven['legs'])
    seven['canonical_card']['combined_price'] = 128.0
    with pytest.raises(ValueError, match='TICKET_PRODUCT_LEG_COUNT'):
        set_options(initial, snapshot, [seven])
