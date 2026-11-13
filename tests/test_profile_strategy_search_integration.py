"""New own-search PLAN integration; fixture host only, no paid calls or DB."""
import json
from copy import deepcopy
from datetime import timedelta
from threading import Lock

import pytest
from test_agent_construction_contract import NOW, _inputs

from goal_analysis.agents.profile_decision import SIMULATOR_ROLES, start_sessions
from goal_analysis.agents.profile_decision_runner import (
    DecisionBudget,
    DecisionJournal,
    ProfileDecisionRunner,
)
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.jobs.agent_snapshot import digest
from goal_analysis.jobs.ten_agent_ticket_evidence import _plan_auxiliary_inputs


def _runner(tmp_path, snapshot, builder, host, *, requests=20, tools=None):
    return ProfileDecisionRunner(host=host, tools=tools or ProfileToolService(snapshot),
        option_builder=builder, journal=DecisionJournal(tmp_path, snapshot),
        budget=DecisionBudget(requests, NOW + timedelta(seconds=120), clock=lambda: NOW))


def test_actual_plan_gets_only_six_own_ranked_seeds_and_complete_journal(tmp_path):
    *_, snapshot, builder = _inputs()
    calls, lock = [], Lock()
    def host(request, *, timeout):
        with lock:
            calls.append(deepcopy(request))
        raise RuntimeError('TESTONLY_CAPTURE_NOT_PAID_REASONING')
    result = _runner(tmp_path, snapshot, builder, host).run(snapshot)
    assert len(calls) == 10 and result['host_requests_reserved'] == 10
    for call in calls:
        own = builder.strategy_search(call['agent_id'], snapshot)
        view = call['data']['own_strategy_search']
        assert view['suggestions'] == own['alternatives'][:6]
        assert view['full_result_count'] == len(own['alternatives'])
        assert view['full_result_sha256'] == digest(own)
        assert view['full_search_result_read_by_host'] is False
        assert view['actual_host_tool_invocation'] is False
        assert len(call['data']['snapshot']['fixture_ids']) == 80
        assert call['data']['own_construction_contract']['full_candidate_catalog']['source_row_count'] == 80
        record = json.loads((tmp_path / view['full_result_journal']['file']).read_text('utf8'))
        assert record['result'] == own and digest(record) == view['full_result_journal']['record_sha256']
    kronikas = next(row for row in calls if row['agent_id'] == 'kronikas')
    assert kronikas['data']['own_strategy_search']['full_result_count'] > 6
    assert result['distinct_decision_count'] == 0


def test_foreign_search_result_stops_before_any_host_or_request_budget(tmp_path):
    *_, snapshot, builder = _inputs()
    original = builder.strategy_search
    def foreign(owner, source):
        value = original(owner, source)
        value['agent_id'] = 'FOREIGN-owner'
        value['result_id'] = digest({key: item for key, item in value.items() if key != 'result_id'})
        return value
    builder.strategy_search = foreign
    calls = []
    def host(request, *, timeout):
        calls.append(request)
        raise AssertionError('FOREIGN SEARCH MUST NOT REACH HOST')
    result = _runner(tmp_path, snapshot, builder, host).run(snapshot, pilot_agent_id='kronikas')
    assert calls == [] and result['host_requests_reserved'] == 0
    assert result['owners'][0]['reason'] == 'OWN_STRATEGY_SEARCH_BINDING_MISMATCH'
    assert not list(tmp_path.glob('*.strategy-search.json'))


def test_agent_can_abstain_after_search_and_both_actual_tool_receipts_without_auto_card(tmp_path):
    *_, snapshot, builder = _inputs()
    calls = []
    def host(request, *, timeout):
        calls.append(deepcopy(request))
        identity = {key: request[key] for key in ('phase', 'agent_id', 'session_id', 'snapshot_id')}
        if request['phase'] == 'PLAN':
            assert request['data']['own_strategy_search']['suggestions']
            return {**identity, 'options': [], 'fixture_reviews': [
                {'fixture_id': key, 'reason': 'TESTONLY considered, explicitly abstain.', 'rejected': False}
                for key in snapshot.fixture_ids], 'tool_requests': [
                {'role': role, 'parameters': {'window': None, 'policy_hash': None}} for role in SIMULATOR_ROLES]}
        return {**identity, 'option_ids': [], 'reason': 'TESTONLY own abstention despite construction suggestions.',
            'tool_interpretations': [{'request_id': row['request']['request_id'],
                'interpretation': 'No own option; UNKNOWN is not a neutral result.'}
                for row in request['data']['tool_results']],
            'family_uses': [{'family': name, 'status': {'AVAILABLE': 'AVAILABLE_NOT_RELEVANT',
                'UNKNOWN': 'MISSING', 'CONFLICTING': 'CONFLICTING', 'STALE': 'STALE'}[value['status']],
                'reason': 'TESTONLY own no-play decision retains source state.',
                'value_sha256': value['value_sha256'], 'option_ids': [], 'risk': 'No selected option.'}
                for name, value in request['data']['snapshot']['families'].items()]}
    result = _runner(tmp_path, snapshot, builder, host, requests=2).run(snapshot, pilot_agent_id='kronikas')
    assert [row['phase'] for row in calls] == ['PLAN', 'FINAL']
    owner = result['owners'][0]
    assert owner['status'] == 'DECISION_COMPLETED' and owner['decision']['status'] == 'ABSTAIN'
    assert len(owner['decision_evidence']['tool_results']) == 2
    assert {row['request']['role'] for row in owner['decision_evidence']['tool_results']} == set(SIMULATOR_ROLES)
    assert result['distinct_decision_count'] == 0 and len(result['unattempted_agent_ids']) == 9
    evidence = owner['decision_evidence']
    state = next(row for row in start_sessions(snapshot,
        {key: get_agent_definition(key).method_version for key in AGENT_IDS}) if row.agent_id == 'kronikas')
    aux = _plan_auxiliary_inputs(evidence, state, snapshot)
    reconstructed = {**{key: calls[0][key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase', 'persona')},
                     'data': {'snapshot': calls[0]['data']['snapshot'], **aux}}
    assert digest(reconstructed) == digest(calls[0])
    tampered = deepcopy(evidence)
    tampered['plan_auxiliary_inputs']['values']['own_strategy_search']['suggestions'].clear()
    with pytest.raises(ValueError, match='SAVED_PLAN_AUXILIARY_INPUTS_CHANGED'):
        _plan_auxiliary_inputs(tampered, state, snapshot)
    foreign = deepcopy(evidence)
    foreign['plan_auxiliary_inputs']['values']['own_strategy_search']['agent_id'] = 'FOREIGN-owner'
    foreign['plan_auxiliary_inputs']['sha256'] = digest(foreign['plan_auxiliary_inputs']['values'])
    with pytest.raises(ValueError, match='FOREIGN_SAVED_STRATEGY_SEARCH_INPUT'):
        _plan_auxiliary_inputs(foreign, state, snapshot)
    assert _plan_auxiliary_inputs({}, state, snapshot) == {}


def test_existing_injected_builder_without_search_keeps_old_path(tmp_path):
    *_, snapshot, original = _inputs()
    def builder(owner, proposal, source):
        return original(owner, proposal, source)
    calls = []
    def host(request, *, timeout):
        calls.append(deepcopy(request))
        raise RuntimeError('TESTONLY old injected builder capture')
    result = _runner(tmp_path, snapshot, builder, host, requests=2).run(snapshot, pilot_agent_id='kronikas')
    assert len(calls) == 1 and 'own_strategy_search' not in calls[0]['data']
    assert not list(tmp_path.glob('*.strategy-search.json'))
    assert result['distinct_decision_count'] == 0


def test_two_real_kernel_source_phases_reconstruct_actual_claim_schema_and_reject_tamper(tmp_path):
    import hashlib
    import sys
    from pathlib import Path

    from test_agent_portfolio import NOW as SOURCE_NOW
    from test_agent_portfolio import inputs as source_inputs
    from test_profile_source_host import finish_fake_source_reply

    from goal_analysis.agents import profile_source_access, profile_source_stdio
    from goal_analysis.agents.profile_decision_runner import source_final_reference
    from goal_analysis.agents.profile_input import decision_view, final_shared_reference
    from goal_analysis.agents.profile_source_access import SourceBundle, build_source_seed
    from goal_analysis.agents.profile_source_host import SourceAccessScope, canonical
    from goal_analysis.jobs.ten_agent_ticket_evidence import _owner_evidence

    _, _, _, _, snapshot, builder, _ = source_inputs(owners=('kronikas', 'ritmusor'))
    view = decision_view(snapshot)
    seed = build_source_seed(snapshot, view)
    bundle = SourceBundle.from_snapshot(snapshot, view)
    source_root = tmp_path / 'source'
    source_root.mkdir()
    bundle_path = source_root / 'bundle.json'
    bundle_path.write_text(canonical(bundle.as_dict()) + '\n', encoding='utf8')
    bytehash = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    calls = []
    def host(request, *, timeout):
        calls.append(deepcopy(request))
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        data = request['data']
        if request['phase'] == 'PLAN':
            seeds = data['own_strategy_search']['suggestions']
            assert seeds and data['own_strategy_search']['agent_id'] == request['agent_id']
            answer = {**identity, 'options': [], 'fixture_reviews': [
                {'fixture_id': key, 'reason': 'TESTONLY independent full calendar abstention.', 'rejected': False}
                for key in snapshot.fixture_ids], 'tool_requests': [
                {'role': role, 'parameters': {}} for role in SIMULATOR_ROLES]}
            reads = [seeds[0]['source_candidate_ids'][0]]
        else:
            answer = {**identity, 'option_ids': [], 'reason': 'TESTONLY refuses own proposed seeds.',
                'tool_interpretations': [{'request_id': row['request']['request_id'],
                    'interpretation': 'TESTONLY empty own options remain UNKNOWN.'}
                    for row in data['tool_results']],
                'family_uses': [{'family': name, 'status': {'AVAILABLE': 'AVAILABLE_NOT_RELEVANT',
                    'UNKNOWN': 'MISSING', 'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}[family['status']],
                    'value_sha256': family['value_sha256'], 'reason': 'TESTONLY source state retained.',
                    'option_ids': [], 'risk': 'No selected option.'}
                    for name, family in data['snapshot']['families'].items()]}
            reads = []
        server, kernel = Path(profile_source_stdio.__file__), Path(profile_source_access.__file__)
        scope = SourceAccessScope(source_root, bundle_path, bytehash(bundle_path), identity,
            Path(sys.executable), server, bytehash(server), kernel, bytehash(kernel))
        return finish_fake_source_reply(scope, request, reads, answer)

    states = {state.agent_id: state for state in start_sessions(snapshot,
        {key: get_agent_definition(key).method_version for key in AGENT_IDS})}
    journal = DecisionJournal(tmp_path / 'journal', snapshot)
    runner = ProfileDecisionRunner(host=host, tools=ProfileToolService(snapshot), option_builder=builder,
        journal=journal, source_bundle=bundle,
        budget=DecisionBudget(4, SOURCE_NOW + timedelta(seconds=120), clock=lambda: SOURCE_NOW))
    final_shared = final_shared_reference(snapshot, view)
    source_final = source_final_reference(snapshot, seed)
    shared = {'states': states, 'source_bundle': bundle, 'source_seed': seed,
        'source_seed_sha256': digest(seed), 'source_final_shared': source_final,
        'source_final_shared_sha256': digest(source_final), 'plan_view_sha256': digest(view),
        'final_shared_sha256': digest(final_shared)}
    for agent_id in ('kronikas', 'ritmusor'):
        owner = runner._owner(states[agent_id], snapshot, view, seed)
        assert owner['status'] == 'DECISION_COMPLETED' and owner['decision']['status'] == 'ABSTAIN'
        assert owner['decision']['selected_options'] == []
        check = _owner_evidence(owner, snapshot, shared)
        assert check['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
        assert check['source_access_validation']['PLAN']['read_count'] >= 1
        assert check['tool_count'] == 2
        actual = next(row for row in calls if row['agent_id'] == agent_id and row['phase'] == 'PLAN')
        identity = {key: actual[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        claim = json.loads((journal.directory / (digest(identity) + '.claim.json')).read_text('utf8'))
        assert set(claim) == {'claim_key', 'purpose', 'request_sha256', 'request'}
        assert claim['request'] == actual and claim['request_sha256'] == digest(actual)
        assert digest({**actual, 'data': {'snapshot': seed}}) != claim['request_sha256']
        tampered = deepcopy(owner)
        envelope = tampered['decision_evidence']['plan_auxiliary_inputs']
        envelope['values']['own_strategy_search']['suggested_count'] += 1
        envelope['sha256'] = digest(envelope['values'])
        with pytest.raises(ValueError, match='SOURCE_EVIDENCE_BINDING_CHANGED'):
            _owner_evidence(tampered, snapshot, shared)
        missing = deepcopy(owner)
        missing['decision_evidence'].pop('plan_auxiliary_inputs')
        with pytest.raises(ValueError, match='SOURCE_EVIDENCE_BINDING_CHANGED'):
            _owner_evidence(missing, snapshot, shared)
    assert len(calls) == 4 and runner.budget.used_requests == 4
