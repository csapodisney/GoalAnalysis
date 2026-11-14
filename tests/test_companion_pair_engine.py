"""First pair boundaries; inert supplied host/product fixtures, no model or database."""
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import Lock

import pytest
import test_twelve_pair_composition as pair_fixture

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
from goal_analysis.agents.profile_decision_runner import (
    DecisionBudget,
    DecisionJournal,
    ProfileDecisionRunner,
)
from goal_analysis.agents.profile_input import decision_view
from goal_analysis.agents.profile_roster import (
    ACTIVE_AGENTS,
    AGENT_IDS,
    COMPANION_AGENT_IDS,
    COMPANION_AGENTS,
    COMPANION_DECISION_VERSION,
    agents_for_group,
    get_agent_definition,
)
from goal_analysis.agents.profile_source_access import (
    SourceAccessKernel,
    SourceBundle,
    build_source_seed,
)
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.jobs.agent_snapshot import (
    FAMILIES,
    SharedSnapshot,
    build_snapshot,
    companion_snapshot,
    digest,
    json_text,
)
from goal_analysis.ticket_product_policy import product_contract

NOW = datetime(2026, 11, 14, 1, tzinfo=UTC)


def _snapshot(*, with_candidates=False, **kwargs):
    ids = [f'TESTONLY-pair-fixture-{index}' for index in range(8)]
    families = {}
    if with_candidates:
        families['odds_and_price_history'] = {'status': 'AVAILABLE', 'source_refs': ['TESTONLY-quotes'],
            'value': {'candidate_input': {'candidates': [
                {'candidate_id': 'TESTONLY-candidate-' + key, 'fixture_id': key,
                 'market_key': 'totals', 'selection_key': 'over_2_5', 'period': 'FULL_TIME',
                 'decimal_price': 2.0} for key in ids]}}}
    return build_snapshot(run_id='TESTONLY-pair-run', target_date='2026-11-14', as_of=NOW.isoformat(),
        authoritative_fixture_ids=ids,
        calendar_receipt={'target_date': '2026-11-14', 'scope': 'FULL_PROVIDER_DAY', 'status': 'COMPLETE',
                          'fixture_ids': ids, 'source_refs': ['TESTONLY-calendar']},
        fixtures=[{'fixture_id': key} for key in ids], family_values=families,
        source_manifest={'TESTONLY-calendar': {'canonical_value_sha256': 'a' * 64}}, **kwargs)


def _states(snapshot):
    return start_sessions(snapshot, {row.agent_id: row.method_version
        for row in agents_for_group(snapshot.decision_data().get('decision_group', 'CORE_TEN'))})


def _option(owner, snapshot, number=0):
    start = number * 4
    legs = [{'candidate_id': 'TESTONLY-candidate-' + key, 'fixture_id': key,
             'market_key': 'totals', 'selection_key': 'over_2_5', 'period': 'FULL_TIME',
             'decimal_price': 2.0} for key in snapshot.fixture_ids[start:start + 4]]
    return {'agent_id': owner, 'option_id': f'{owner}:TESTONLY:{number}', 'reason': 'TESTONLY legal hypothesis',
            'legs': deepcopy(legs), 'canonical_card': {'profile_id': owner, 'legs': legs,
                'combined_price': 16.0, 'status': 'DRAFT', 'ticket_product_contract': product_contract()}}


def test_fixed_pair_identity_preserves_every_core_evidence_value_and_default():
    core = _snapshot()
    assert core.payload_json == _snapshot(owner_ids=AGENT_IDS, decision_group='CORE_TEN').payload_json
    assert agents_for_group() is ACTIVE_AGENTS and len(ACTIVE_AGENTS) == 10
    assert 'decision_group' not in core.data() and 'decision_group' not in core.decision_data()
    pair = companion_snapshot(core)
    expected = core.data()
    expected.update(version='agent-snapshot-contract-v1-companion-pair', decision_group='COMPANION_PAIR',
                    owner_ids=list(COMPANION_AGENT_IDS))
    assert pair.data() == expected and pair.snapshot_id == digest(expected) != core.snapshot_id
    assert _snapshot(decision_group='COMPANION_PAIR').payload_json == pair.payload_json
    assert len(_states(core)) == 10 and len(_states(pair)) == 2
    assert {row.agent_id for row in COMPANION_AGENTS} == {'daily_portfolio', 'legacy_daily223'}
    assert all(row.max_variants == 1 and row.method_version.startswith(COMPANION_DECISION_VERSION)
               for row in COMPANION_AGENTS)
    with pytest.raises(KeyError, match='UNKNOWN_ACTIVE_AGENT'):
        get_agent_definition('daily_portfolio')
    with pytest.raises(KeyError, match='UNKNOWN_ACTIVE_AGENT'):
        get_agent_definition('kronikas', decision_group='COMPANION_PAIR')
    with pytest.raises(ValueError, match='UNKNOWN_DECISION_GROUP'):
        agents_for_group('CALLER_ROSTER')
    with pytest.raises(ValueError, match='EXACT_COMPANION_PAIR_OWNERS_REQUIRED'):
        _snapshot(decision_group='COMPANION_PAIR', owner_ids=['daily_portfolio'])
    with pytest.raises(ValueError, match='CORE_TEN_SNAPSHOT_REQUIRED'):
        companion_snapshot(pair)
    changed = pair.data()
    changed.pop('decision_group')
    with pytest.raises(ValueError, match='EXPLICIT_COMPANION_GROUP_REQUIRED'):
        SharedSnapshot(digest(changed), json_text(changed))
    assert pair.data()['families'] == core.data()['families']


def test_pair_source_bundle_keeps_full_access_and_rejects_cross_group_bindings():
    core = _snapshot()
    pair = companion_snapshot(core)
    view = decision_view(pair)
    bundle = SourceBundle.from_snapshot(pair, view)
    assert SourceBundle.from_snapshot(core, decision_view(core)).data('raw') == core.data()
    seed = build_source_seed(pair, view)
    assert seed['decision_group'] == 'COMPANION_PAIR'
    assert seed['fixture_ids'] == list(core.fixture_ids) and set(seed['families']) == set(FAMILIES)
    state = _states(pair)[0]
    identity = {key: getattr(state, key) for key in ('agent_id', 'session_id', 'snapshot_id')}
    identity['phase'] = 'PLAN'
    journal = []
    def append(row):
        journal.append(row)
        return digest(row)

    kernel = SourceAccessKernel(bundle, identity, append, clock=lambda: NOW)
    response = kernel.read_data({**identity, 'namespace': 'raw', 'path': ['fixture_ids'],
                                 'mode': 'text', 'offset': 0, 'limit': 2000}, rpc_id=1)
    payload = json.loads(response['result']['content'][0]['text'])
    assert payload['status'] == 'OK' and kernel.successful_reads == 1 and len(journal) == 1
    assert bundle.data('raw') == pair.data()
    with pytest.raises(ValueError, match='FOREIGN_OWNER_BINDING'):
        SourceAccessKernel(bundle, {**identity, 'agent_id': 'kronikas'}, journal.append)
    changed = deepcopy(view)
    changed['owner_ids'] = list(AGENT_IDS)
    with pytest.raises(ValueError, match='EXACT_COMPANION_PAIR_OWNERS_REQUIRED'):
        SourceBundle.from_snapshot(pair, changed)
    changed = pair.data()
    changed['owner_ids'] = ['daily_portfolio', 'foreign']
    changed_view = deepcopy(view)
    changed_view['owner_ids'] = changed['owner_ids']
    changed_view['view']['source_snapshot_id'] = digest(changed)
    with pytest.raises(ValueError, match='EXACT_COMPANION_PAIR_OWNERS_REQUIRED'):
        SourceBundle(json_text(changed), json_text(changed_view), digest(changed), digest(changed_view))


def test_pair_one_variant_and_both_actual_tool_contracts_preserve_unknown_and_gates():
    snapshot = companion_snapshot(_snapshot())
    service = ProfileToolService(snapshot)
    initial = _states(snapshot)[0]
    options = [_option(initial.agent_id, snapshot, index) for index in range(2)]
    state = set_options(initial, snapshot, options)
    with pytest.raises(ValueError, match='OWNER_VARIANT_LIMIT_EXCEEDED'):
        finalize_decisions(state, snapshot, option_ids=[row['option_id'] for row in options], reason='TESTONLY')
    for fixture in snapshot.fixture_ids:
        state = consider_fixture(state, snapshot, fixture, reason='TESTONLY full calendar', rejected=False)
    for family, value in snapshot.data()['families'].items():
        state = consider_family(state, snapshot, family,
            status='AVAILABLE_AND_CONSIDERED' if value['status'] == 'AVAILABLE' else 'MISSING',
            reason='TESTONLY exact source status', value_sha256=value['value_sha256'],
            risk='TESTONLY unavailable evidence remains an explicit risk')
    with pytest.raises(ValueError, match='MISSING_SIMULATION_RECEIPT'):
        finalize_decisions(state, snapshot, option_ids=[options[0]['option_id']], reason='TESTONLY')
    for role in SIMULATOR_ROLES:
        state, request = request_tool(state, snapshot, role)
        result = service.evaluate(request, options)
        assert result['status'] == 'UNKNOWN' and result['value'] is None
        state = record_tool_result(state, snapshot, request, result, interpretation='TESTONLY honest UNKNOWN')
    _, result = finalize_decisions(state, snapshot, option_ids=[options[0]['option_id']], reason='TESTONLY')
    assert result.data()['version'] == 'profile-owner-decision-v1-companion-pair'
    assert result.data()['selected_options'] == options[:1]
    assert {row['role'] for row in result.data()['tool_receipts']} == set(SIMULATOR_ROLES)
    assert result.data()['selected_options'][0]['canonical_card']['status'] == 'DRAFT'
    bad = deepcopy(options[0])
    bad['canonical_card']['combined_price'] = 9.0
    with pytest.raises(ValueError, match='COMBINED_PRICE_MISMATCH'):
        set_options(initial, snapshot, [bad])
    foreign = deepcopy(options[0])
    foreign['agent_id'] = 'legacy_daily223'
    with pytest.raises(ValueError, match='NEW_PRODUCT_OPTION_LEGS_OR_OWNER_CHANGED'):
        set_options(initial, snapshot, [foreign])


def test_pair_runner_four_owned_phases_shared_absolute_deadline_and_no_retry(tmp_path):
    snapshot = companion_snapshot(_snapshot(with_candidates=True))
    deadline = NOW + timedelta(seconds=60)
    clock = [NOW]
    core_budget = DecisionBudget(20, deadline, clock=lambda: clock[0])
    budget = DecisionBudget(4, deadline, clock=lambda: clock[0], decision_group='COMPANION_PAIR')
    journal = DecisionJournal(tmp_path / 'TESTONLY-pair', snapshot)
    calls, lock = [], Lock()

    def host(request, *, timeout):
        assert 0 < timeout <= 60
        with lock:
            calls.append(deepcopy(request))
        owner, phase = request['agent_id'], request['phase']
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        assert request['persona'] == get_agent_definition(owner, decision_group='COMPANION_PAIR').as_dict()
        option = _option(owner, snapshot, COMPANION_AGENT_IDS.index(owner))
        if phase == 'PLAN':
            return {**identity, 'options': [{'option_id': option['option_id'], 'reason': option['reason'],
                        'source_candidate_ids': [row['candidate_id'] for row in option['legs']]}],
                'fixture_reviews': [{'fixture_id': key, 'reason': 'TESTONLY full calendar', 'rejected': False}
                                    for key in snapshot.fixture_ids],
                'tool_requests': [{'role': role, 'parameters': {}} for role in SIMULATOR_ROLES]}
        data = pair_fixture._expand_final(request['data'])
        return {**identity, 'option_ids': [option['option_id']], 'reason': 'TESTONLY own choice',
            'tool_interpretations': [{'request_id': row['request']['request_id'], 'interpretation': 'TESTONLY UNKNOWN'}
                                     for row in data['tool_results']],
            'family_uses': [{'family': key, 'value_sha256': value['value_sha256'],
                            'status': 'AVAILABLE_AND_CONSIDERED' if value['status'] == 'AVAILABLE' else 'MISSING',
                            'reason': 'TESTONLY source-bound state', 'option_ids': [], 'risk': 'TESTONLY uncertainty'}
                           for key, value in data['snapshot']['families'].items()]}

    def builder(owner, proposal, actual):
        assert actual is snapshot
        result = _option(owner, snapshot, COMPANION_AGENT_IDS.index(owner))
        assert proposal['source_candidate_ids'] == [row['candidate_id'] for row in result['legs']]
        return result

    runner = ProfileDecisionRunner(host=host, tools=ProfileToolService(snapshot), option_builder=builder,
        journal=journal, budget=budget, decision_group='COMPANION_PAIR')
    result = runner.run(snapshot)
    assert result['version'] == COMPANION_DECISION_VERSION and result['agent_count'] == 2
    assert result['status'] == 'COMPLETE' and result['both_companion_owners_selected'] is True
    assert result['host_requests_reserved'] == 4 and result['distinct_decision_count'] == 2
    assert {(row['agent_id'], row['phase']) for row in calls} == {
        (owner, phase) for owner in COMPANION_AGENT_IDS for phase in ('PLAN', 'FINAL')}
    assert len(calls) == 4 and len(list(journal.directory.glob('*.tool-full-result.json'))) == 4
    assert len(list(journal.directory.glob('*.owner.json'))) == 2
    assert core_budget.deadline == budget.deadline == deadline and core_budget.used_requests == 0
    with pytest.raises(FileExistsError):
        runner.run(snapshot)
    assert len(calls) == 4
    clock[0] = deadline
    with pytest.raises(ValueError, match='DECISION_BUDGET_EXHAUSTED'):
        core_budget.reserve(phase='PLAN', source_access=True)
    with pytest.raises(ValueError, match='FINITE_TEN_AGENT_REQUEST_BUDGET_REQUIRED'):
        DecisionBudget(5, deadline, decision_group='COMPANION_PAIR')
    with pytest.raises(ValueError, match='BUDGET_DECISION_GROUP_MISMATCH'):
        ProfileDecisionRunner(host=host, tools=ProfileToolService(snapshot), option_builder=builder,
                              journal=journal, budget=core_budget, decision_group='COMPANION_PAIR')
