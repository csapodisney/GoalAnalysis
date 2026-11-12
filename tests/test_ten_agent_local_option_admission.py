"""TESTONLY actual pure admission/runner/audit; no external host or model fit."""
from copy import deepcopy
from datetime import timedelta

import pytest
import test_agent_portfolio as product_fixture
import test_ten_agent_runner as runner_fixture
import test_ten_agent_ticket_evidence as audit_fixture

from goal_analysis.agents.profile_decision import SIMULATOR_ROLES, start_sessions
from goal_analysis.agents.profile_decision_runner import (
    DecisionBudget,
    DecisionJournal,
    ProfileDecisionRunner,
)
from goal_analysis.agents.profile_input import decision_view
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.jobs.agent_portfolio import (
    OwnOptionAdmissionRejected,
    build_agent_option_builder,
    own_option_admission_partition,
)
from goal_analysis.jobs.agent_snapshot import (
    SharedSnapshot,
    build_normal_snapshot,
    digest,
    json_text,
)
from goal_analysis.jobs.ticket_evidence import build_ticket_evidence


def _owner_attempt(tmp_path, *, all_invalid=False, fault=None):
    # Only these two source methods are needed for this single-owner admission fixture.
    # Both complete fixture cohorts remain present; the ordinary host cap is unchanged.
    _, _, _, _, snapshot, builder, proposals = product_fixture.inputs(
        owners=('kronikas', 'ritmusor'), variants=1)
    owner = 'kronikas'
    valid = deepcopy(proposals[owner][0])
    rejected = {**deepcopy(proposals['ritmusor'][0]), 'option_id': 'TESTONLY-rejected-own-market',
                'reason': 'TESTONLY proposal with source-valid but wrong own specialist market'}
    plan_options = [rejected] if all_invalid else [valid, rejected]
    if fault == 'duplicate':
        plan_options.append(deepcopy(valid))
    elif fault == 'foreign':
        plan_options[0]['source_candidate_ids'] = ['TESTONLY-missing-source']
    calls = []

    def host(request, *, timeout):
        assert timeout > 0
        calls.append(deepcopy(request))
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        if request['phase'] == 'PLAN':
            return {**identity, 'options': deepcopy(plan_options),
                'fixture_reviews': [{'fixture_id': key, 'reason': 'TESTONLY full-calendar consideration',
                    'rejected': False} for key in snapshot.fixture_ids],
                'tool_requests': [{'role': role, 'parameters': {}} for role in SIMULATOR_ROLES]}
        data = request['data']
        accepted = data['own_option_admission']['accepted_option_ids']
        ids = [rejected['option_id']] if fault == 'select_rejected' else accepted
        return {**identity, 'option_ids': ids, 'reason': 'TESTONLY choose admitted own variants or abstain',
            'tool_interpretations': [{'request_id': row['request']['request_id'],
                'interpretation': 'TESTONLY UNKNOWN result remains a limitation'} for row in data['tool_results']],
            'family_uses': [{'family': name, 'status': {'AVAILABLE': 'AVAILABLE_AND_CONSIDERED',
                'UNKNOWN': 'MISSING', 'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}[family['status']],
                'value_sha256': family['value_sha256'],
                'reason': 'TESTONLY exact source family statement', 'option_ids': accepted,
                'risk': 'TESTONLY limitation also recorded for empty admitted option set'}
                for name, family in snapshot.decision_data()['families'].items()]}

    if fault == 'unexpected':
        def unexpected(*args):
            raise ValueError('OWN_PERSONA_MODEL_HISTORY_OR_PRODUCT_RULE')
        builder = unexpected
    tools = runner_fixture.FixtureTools()
    runner = ProfileDecisionRunner(host=host, tools=tools, option_builder=builder,
        journal=DecisionJournal(tmp_path / 'own-admission', snapshot),
        budget=DecisionBudget(max_requests=2, deadline=product_fixture.NOW + timedelta(hours=1),
                              clock=lambda: product_fixture.NOW))
    states = start_sessions(snapshot, {key: get_agent_definition(key).method_version for key in AGENT_IDS})
    state = next(row for row in states if row.agent_id == owner)
    before = snapshot.payload_json
    result = runner._owner(state, snapshot, decision_view(snapshot))
    assert snapshot.payload_json == before
    return result, calls, tools, plan_options


def test_one_invalid_own_sibling_keeps_the_valid_option_and_exact_original_plan(tmp_path):
    result, calls, tools, proposals = _owner_attempt(tmp_path)
    assert result['status'] == 'DECISION_COMPLETED'
    assert [row['phase'] for row in calls] == ['PLAN', 'FINAL']
    final = calls[-1]['data']
    admission = final['own_option_admission']
    assert final['own_plan']['options'] == proposals
    assert admission['accepted_option_ids'] == [proposals[0]['option_id']]
    assert admission['ordered_partition'] == [
        {'proposal_index': 0, 'option_id': proposals[0]['option_id'], 'status': 'ACCEPTED'},
        {'proposal_index': 1, 'option_id': proposals[1]['option_id'], 'status': 'REJECTED_OWN_OPTION'}]
    assert admission['rejected_options'][0]['reason'] == 'OWN_CORE_MARKET_OR_PRICE_RULE'
    assert all(row['own_option_ids'] == [proposals[0]['option_id']] for row in tools.calls)
    assert result['decision']['option_ids'] == [proposals[0]['option_id']]
    assert result['decision_evidence']['own_option_admission'] == admission


def test_all_rejected_options_reach_honest_abstention_with_empty_tools_not_filler(tmp_path):
    result, calls, tools, _ = _owner_attempt(tmp_path, all_invalid=True)
    assert result['status'] == 'DECISION_COMPLETED' and result['decision']['status'] == 'ABSTAIN'
    assert result['decision']['selected_options'] == []
    assert len(calls) == len(tools.calls) == 2
    assert calls[-1]['data']['own_option_admission']['accepted_option_ids'] == []
    assert all(row['own_option_ids'] == [] for row in tools.calls)


@pytest.mark.parametrize('fault', ['foreign', 'duplicate', 'unexpected'])
def test_untrusted_identity_or_arbitrary_value_error_remains_owner_stop(tmp_path, fault):
    result, calls, tools, _ = _owner_attempt(tmp_path, fault=fault)
    assert result['status'] == 'INCOMPLETE_NO_RETRY'
    assert [row['phase'] for row in calls] == ['PLAN'] and tools.calls == []


def test_final_cannot_select_a_locally_rejected_proposal(tmp_path):
    result, calls, _, _ = _owner_attempt(tmp_path, fault='select_rejected')
    assert result['status'] == 'INCOMPLETE_NO_RETRY' and len(calls) == 2


@pytest.mark.parametrize('fault', ['calendar_name', 'native_participant', 'future_quote', 'unavailable_quote'])
def test_club_source_or_clock_fault_cannot_be_relabelled_as_local_persona_rejection(fault):
    report, artifacts, enriched, club, _, _, proposals = product_fixture.inputs()
    owner = 'tipster_ertek_milan'
    proposal = proposals[owner][0]
    selected = proposal['source_candidate_ids'][0]
    for container in (artifacts['candidate_input'], enriched, club):
        row = next(item for item in container['candidates'] if item['candidate_id'] == selected)
        if fault == 'calendar_name':
            row['home_team'] = 'TESTONLY different source participant'
        elif fault == 'native_participant':
            row['api_football_home_team_id'] = '999999'
        elif fault == 'future_quote':
            row['quote_observed_at'] = (product_fixture.NOW + timedelta(minutes=1)).isoformat()
        else:
            row['quote_available'] = False
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
        snapshot_as_of=product_fixture.NOW.isoformat(), artifacts=artifacts,
        enriched=enriched, club_enriched=club)
    builder = build_agent_option_builder(report, artifacts, enriched, club,
                                         product_fixture.SETTINGS, product_fixture.NOW)
    with pytest.raises(ValueError) as stopped:
        builder(owner, proposal, snapshot)
    assert not isinstance(stopped.value, OwnOptionAdmissionRejected)


def _saved_partition():
    report, artifacts = audit_fixture.saved_frame()
    snapshot = SharedSnapshot(report['ten_agent_decisions']['snapshot_id'],
                              json_text(artifacts['independent_agent_snapshot']))
    source = snapshot.data()['families']['odds_and_price_history']['value']
    builder = build_agent_option_builder(report, artifacts, source['enriched'], source['club_enriched'],
                                         report['settings'], product_fixture.NOW)
    owner = report['ten_agent_decisions']['owners'][0]
    evidence = owner['decision_evidence']
    other = report['ten_agent_decisions']['owners'][1]['decision_evidence']['plan']['options'][0]
    proposal = {**deepcopy(other), 'option_id': 'TESTONLY-source-bound-rejected-sibling'}
    with pytest.raises(OwnOptionAdmissionRejected) as rejected:
        builder(owner['agent_id'], proposal, snapshot)
    evidence['plan']['options'].append(proposal)
    item = {**rejected.value.data(), 'proposal_index': 2, 'status': 'REJECTED_OWN_OPTION',
            'reason_authority': 'PRODUCER_REPORTED_EXISTING_ADMISSION_GATE'}
    evidence['own_option_admission'] = own_option_admission_partition(snapshot, owner['agent_id'],
        evidence['plan']['options'], evidence['validated_own_options'], [item])
    evidence['input_views']['FINAL']['admission_sha256'] = digest(evidence['own_option_admission'])
    artifacts['independent_agent_decisions']['owners'][0] = deepcopy(owner)
    return report, artifacts


def test_pure_audit_rebinds_rejection_partition_without_claiming_independent_gate_execution():
    report, artifacts = _saved_partition()
    audit = build_ticket_evidence(report, artifacts)
    assert audit['status'] == 'COMPLETE' and len(audit['cards']) == 20
    checked = audit['cards'][0]['owner_evidence']
    assert len(checked['own_option_admission']['rejected_options']) == 1
    assert checked['admission_reason_validation'] == 'PRODUCER_REPORTED_GATE_WITH_SOURCE_AND_PARTITION_BINDING'


@pytest.mark.parametrize('defect', ['index', 'source_hash', 'reason', 'partition'])
def test_corrupt_rejection_or_partition_is_local_unknown_in_consumer_audit(defect):
    report, artifacts = _saved_partition()
    for run in (report['ten_agent_decisions'], artifacts['independent_agent_decisions']):
        evidence = run['owners'][0]['decision_evidence']
        admission = evidence['own_option_admission']
        item = admission['rejected_options'][0]
        if defect == 'index':
            item['proposal_index'] = 0
        elif defect == 'source_hash':
            item['source_rows_sha256'] = '0' * 64
        elif defect == 'reason':
            item['reason'] = 'ARBITRARY_VALUE_ERROR'
        else:
            admission['accepted_option_ids'].pop()
        evidence['input_views']['FINAL']['admission_sha256'] = digest(admission)
    audit = build_ticket_evidence(report, artifacts)
    assert audit['status'] == 'PARTIAL' and len(audit['cards']) == 20
    assert all(row['owner_evidence']['status'] == 'UNKNOWN' for row in audit['cards'][:2])
    assert all(row['owner_evidence']['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
               for row in audit['cards'][2:])
