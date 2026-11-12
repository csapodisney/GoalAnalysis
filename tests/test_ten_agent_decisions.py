"""Ten-owner/multiple-variant contract checks, without actual inference or tools."""
from copy import deepcopy
from dataclasses import replace

import pytest

from goal_analysis.agents.profile_decision import (
    SIMULATOR_ROLES,
    consider_family,
    consider_fixture,
    finalize_decisions,
    option_exposure,
    record_tool_result,
    request_tool,
    set_options,
    start_sessions,
)
from goal_analysis.agents.profile_roster import ACTIVE_AGENTS
from goal_analysis.jobs.agent_snapshot import build_snapshot


def _snapshot(count=8):
    ids = [f'TESTONLY:fixture:{number}' for number in range(count)]
    return build_snapshot(
        run_id='TESTONLY-ten-owner-variants', target_date='2026-11-13',
        as_of='2026-11-12T15:00:00+00:00', authoritative_fixture_ids=ids,
        calendar_receipt={
            'target_date': '2026-11-13', 'scope': 'FULL_PROVIDER_DAY',
            'status': 'COMPLETE', 'fixture_ids': ids, 'source_refs': ['TESTONLY:calendar'],
        },
        fixtures=[{'fixture_id': key} for key in ids],
        family_values={
            'weather': {'status': 'AVAILABLE', 'value': {'wind_kmh': 12},
                        'source_refs': ['TESTONLY:weather']},
        },
        source_manifest={'TESTONLY:calendar': 'a' * 64, 'TESTONLY:weather': 'b' * 64},
    )


def _sessions(snapshot):
    return start_sessions(snapshot, {agent.agent_id: agent.method_version for agent in ACTIVE_AGENTS})


def _option(owner, number, fixture_ids):
    return {
        'agent_id': owner, 'option_id': f'{owner}:variant:{number}',
        'reason': f'TESTONLY alternative {number}; not strategy/quote approval',
        'legs': [{
            'fixture_id': key, 'market': 'totals_2_5', 'outcome': 'over',
            'period': 'FULL_TIME', 'decimal_price': 2.0,
        } for key in fixture_ids],
    }


def _options(state, snapshot, count=2, offset=0):
    return [_option(state.agent_id, number, snapshot.fixture_ids[offset + number * 2:offset + number * 2 + 2])
            for number in range(count)]


def _account(state, snapshot, options, *, omitted_fixture=None, omitted_family=None):
    for key in snapshot.fixture_ids:
        if key != omitted_fixture:
            state = consider_fixture(state, snapshot, key, reason='Own full-calendar fixture review')
    for family, value in snapshot.data()['families'].items():
        if family == omitted_family:
            continue
        available = value['status'] == 'AVAILABLE'
        state = consider_family(
            state, snapshot, family,
            status='AVAILABLE_AND_CONSIDERED' if available else 'MISSING',
            reason=f'Own examination of {family}', value_sha256=value['value_sha256'],
            option_ids=[row['option_id'] for row in options] if available else [],
            risk='Observed uncertainty retained for own alternatives' if available else None,
        )
    return state


def _tools(state, snapshot, *, roles=SIMULATOR_ROLES, status='UNKNOWN', covered=None):
    for role in roles:
        state, request = request_tool(state, snapshot, role)
        payload = request.data()
        result = {key: payload[key] for key in ('request_id', 'agent_id', 'snapshot_id', 'option_hash')}
        result.update(status=status, value=None, reason='TESTONLY_NO_EXECUTED_PIT_OR_SCENARIO_RESULT')
        if status != 'UNKNOWN':
            result.update(
                value={'test_fixture_observation': 'supplied'},
                result_id=f'TESTONLY:result:{role}', source_refs=[f'TESTONLY:source:{role}'],
                own_option_ids=payload['own_option_ids'] if covered is None else list(covered),
            )
            if status == 'CACHED':
                result['cached_from_result_id'] = f'TESTONLY:prior-result:{role}'
        state = record_tool_result(
            state, snapshot, request, result,
            interpretation='Own test-only result interpretation; no actual simulation/profit claim',
        )
    return state


def _prepared(snapshot, *, count=2):
    state = _sessions(snapshot)[0]
    options = _options(state, snapshot, count)
    state = _account(set_options(state, snapshot, options), snapshot, options)
    return _tools(state, snapshot), options


def test_ten_owners_can_finalize_twenty_distinct_own_variants_without_extra_sessions():
    snapshot = _snapshot(count=40)
    states = _sessions(snapshot)
    outputs = []
    for index, initial in enumerate(states):
        options = _options(initial, snapshot, offset=index * 4)
        state = _account(set_options(initial, snapshot, options), snapshot, options)
        state = _tools(state, snapshot)
        final, decision = finalize_decisions(
            state, snapshot, option_ids=[row['option_id'] for row in options],
            reason='TESTONLY own two variants; mathematical/quote/READY gates are separate',
        )
        value = decision.data()
        assert final.final_json == decision.payload_json
        assert value['option_id'] is None
        assert value['option_ids'] == [row['option_id'] for row in options]
        assert all(row['agent_id'] == initial.agent_id for row in value['selected_options'])
        assert value['execution_evidence'] == 'CONTRACT_ONLY_NOT_INFERENCE_OR_RUNTIME_PROOF'
        outputs.append(value)
    variants = [option for value in outputs for option in value['selected_options']]
    assert len(states) == len({value['session_id'] for value in outputs}) == 10
    assert len(variants) == len({option_exposure(option) for option in variants}) == 20
    assert all(state.transcript == () for state in states)


def test_three_variants_allowed_but_four_rejected_under_one_owner():
    snapshot = _snapshot()
    state, options = _prepared(snapshot, count=4)
    with pytest.raises(ValueError, match='OWNER_VARIANT_LIMIT_EXCEEDED'):
        finalize_decisions(state, snapshot, option_ids=[row['option_id'] for row in options], reason='Four')
    _, decision = finalize_decisions(
        state, snapshot, option_ids=[row['option_id'] for row in options[:3]], reason='Three own variants',
    )
    assert len(decision.data()['selected_options']) == 3


@pytest.mark.parametrize('variation', ['renamed', 'reordered_legs', 'refreshed_price'])
def test_equivalent_exposure_cannot_be_counted_as_another_variant(variation):
    snapshot = _snapshot()
    initial = _sessions(snapshot)[0]
    first = _options(initial, snapshot, count=1)[0]
    duplicate = deepcopy(first)
    duplicate['option_id'] += ':another-name'
    duplicate['reason'] = 'Different wording is not a different selection'
    if variation == 'reordered_legs':
        duplicate['legs'].reverse()
    elif variation == 'refreshed_price':
        duplicate['legs'][0]['decimal_price'] = 2.2
    options = [first, duplicate]
    state = _tools(_account(set_options(initial, snapshot, options), snapshot, options), snapshot)
    assert option_exposure(first) == option_exposure(duplicate)
    with pytest.raises(ValueError, match='DUPLICATE_SELECTED_EXPOSURE'):
        finalize_decisions(state, snapshot, option_ids=[row['option_id'] for row in options], reason='Duplicate')


def test_canonical_product_field_aliases_have_the_same_exposure():
    snapshot = _snapshot()
    initial = _sessions(snapshot)[0]
    original = _options(initial, snapshot, count=1)[0]
    alternate = deepcopy(original)
    alternate['agent_id'] = ACTIVE_AGENTS[1].agent_id
    alternate['option_id'] = 'TESTONLY:other-owner-label'
    for leg in alternate['legs']:
        leg['market_key'] = leg.pop('market')
        leg['selection_key'] = leg.pop('outcome')
    assert option_exposure(original) == option_exposure(alternate)


def test_different_outcome_period_or_line_is_not_the_same_economic_selection():
    snapshot = _snapshot()
    original = _options(_sessions(snapshot)[0], snapshot, count=1)[0]
    for key, value in (('outcome', 'under'), ('period', 'FIRST_HALF'), ('line', 3.5)):
        changed = deepcopy(original)
        changed['legs'][0][key] = value
        assert option_exposure(changed) != option_exposure(original)


def test_foreign_owner_state_and_option_cannot_be_finalized():
    snapshot = _snapshot()
    state, options = _prepared(snapshot)
    foreign = _options(_sessions(snapshot)[1], snapshot, count=1)[0]
    with pytest.raises(ValueError, match='FOREIGN_OR_EMPTY_OPTION'):
        set_options(state, snapshot, [foreign])
    with pytest.raises(ValueError, match='FOREIGN_FINAL_OPTION'):
        finalize_decisions(state, snapshot, option_ids=[foreign['option_id']], reason='Foreign')
    with pytest.raises(ValueError, match='FOREIGN_OWNER_OR_SNAPSHOT'):
        finalize_decisions(replace(state, agent_id=foreign['agent_id']), snapshot,
                           option_ids=[options[0]['option_id']], reason='Forged owner')


@pytest.mark.parametrize(('missing', 'error'), [
    ('fixture', 'FULL_CALENDAR_NOT_CONSIDERED'),
    ('family', 'ALL_FAMILY_STATES_REQUIRED'),
    ('tool', 'MISSING_SIMULATION_RECEIPT'),
])
def test_multivariant_final_requires_full_calendar_all_families_and_both_tools(missing, error):
    snapshot = _snapshot()
    initial = _sessions(snapshot)[0]
    options = _options(initial, snapshot)
    state = _account(
        set_options(initial, snapshot, options), snapshot, options,
        omitted_fixture=snapshot.fixture_ids[-1] if missing == 'fixture' else None,
        omitted_family='weather' if missing == 'family' else None,
    )
    state = _tools(state, snapshot, roles=SIMULATOR_ROLES[:1] if missing == 'tool' else SIMULATOR_ROLES)
    with pytest.raises(ValueError, match=error):
        finalize_decisions(state, snapshot, option_ids=[row['option_id'] for row in options], reason='Incomplete')


def test_changed_variant_needs_current_tool_hash_and_family_interpretation():
    snapshot = _snapshot()
    state, original = _prepared(snapshot)
    changed = deepcopy(original)
    changed[1]['legs'][0]['decimal_price'] = 2.3
    state = set_options(state, snapshot, changed)
    selected = [row['option_id'] for row in changed]
    with pytest.raises(ValueError, match='MISSING_SIMULATION_RECEIPT'):
        finalize_decisions(state, snapshot, option_ids=selected, reason='Old tool hash')
    state = _tools(state, snapshot)
    with pytest.raises(ValueError, match='STALE_FAMILY_OPTION_CONTENT'):
        finalize_decisions(state, snapshot, option_ids=selected, reason='Fresh tools, old family meaning')
    state = _account(state, snapshot, changed)
    _, decision = finalize_decisions(state, snapshot, option_ids=selected, reason='Both variants reconsidered')
    assert decision.data()['selected_options'] == changed


@pytest.mark.parametrize('status', ['AVAILABLE', 'CACHED'])
def test_available_result_for_first_variant_does_not_cover_unlinked_second_variant(status):
    snapshot = _snapshot()
    initial = _sessions(snapshot)[0]
    options = _options(initial, snapshot)
    accounted = _account(set_options(initial, snapshot, options), snapshot, options)
    state = _tools(accounted, snapshot, status=status, covered=[options[0]['option_id']])
    with pytest.raises(ValueError, match='SELECTED_VARIANT_SIMULATION_NOT_COVERED'):
        finalize_decisions(state, snapshot, option_ids=[row['option_id'] for row in options],
                           reason='Second selected option has no linked simulator result')
    complete = _tools(accounted, snapshot, status=status)
    _, decision = finalize_decisions(complete, snapshot,
                                    option_ids=[row['option_id'] for row in options],
                                    reason='Each selected option has both own result links')
    assert len(decision.data()['selected_options']) == 2


def test_each_selected_variant_needs_its_own_reason_not_only_a_batch_reason():
    snapshot = _snapshot()
    initial = _sessions(snapshot)[0]
    options = _options(initial, snapshot)
    options[1]['reason'] = '   '
    state = _tools(_account(set_options(initial, snapshot, options), snapshot, options), snapshot)
    with pytest.raises(ValueError, match='EACH_SELECTED_VARIANT_REASON_REQUIRED'):
        finalize_decisions(state, snapshot, option_ids=[row['option_id'] for row in options],
                           reason='A batch rationale cannot fill an unreasoned selected variant')


def test_risk_only_result_can_support_abstention_but_not_unlinked_selected_variants():
    snapshot = _snapshot()
    initial = _sessions(snapshot)[0]
    options = _options(initial, snapshot)
    state = _account(set_options(initial, snapshot, options), snapshot, options)
    for role in SIMULATOR_ROLES:
        state, request = request_tool(state, snapshot, role)
        payload = request.data()
        result = {key: payload[key] for key in ('request_id', 'agent_id', 'snapshot_id', 'option_hash')}
        result.update(
            status='AVAILABLE', value={'test_sample_size': 0},
            result_id=f'TESTONLY:risk:{role}', source_refs=[f'TESTONLY:risk-source:{role}'],
            risk='No acceptable source-grounded option result is present in this empty sample',
        )
        state = record_tool_result(state, snapshot, request, result,
                                   interpretation='Own explicit whole-batch abstention risk')
    with pytest.raises(ValueError, match='SELECTED_VARIANT_SIMULATION_NOT_COVERED'):
        finalize_decisions(state, snapshot, option_ids=[row['option_id'] for row in options],
                           reason='Risk-only evidence cannot authorize selected variants')
    _, decision = finalize_decisions(state, snapshot, option_ids=(), reason='Both tools show absent support')
    assert decision.data()['status'] == 'ABSTAIN'


def test_owner_can_abstain_without_filler_variants_after_accounting_for_all_inputs():
    snapshot = _snapshot()
    initial = _sessions(snapshot)[0]
    state = _tools(_account(initial, snapshot, []), snapshot)
    _, decision = finalize_decisions(state, snapshot, option_ids=(), reason='No supported own variant')
    value = decision.data()
    assert value['status'] == 'ABSTAIN'
    assert value['option_ids'] == value['selected_options'] == []
    assert value['option_id'] is None
    assert len(value['tool_receipts']) == 2
