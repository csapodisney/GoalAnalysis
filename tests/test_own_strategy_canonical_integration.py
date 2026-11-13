"""New source-bound own-search to canonical fixture; no paid/native host or DB."""
import hashlib
import json
import sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from test_agent_portfolio import NOW, SETTINGS, inputs
from test_profile_source_host import finish_fake_source_reply

from goal_analysis.agents import profile_source_access, profile_source_stdio
from goal_analysis.agents.profile_decision import SIMULATOR_ROLES, start_sessions
from goal_analysis.agents.profile_decision_runner import (
    DecisionBudget,
    DecisionJournal,
    ProfileDecisionRunner,
)
from goal_analysis.agents.profile_input import decision_view
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.agents.profile_source_access import SourceBundle, build_source_seed
from goal_analysis.agents.profile_source_host import SourceAccessScope, canonical
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.jobs.agent_portfolio import build_agent_option_builder, publish_agent_decisions
from goal_analysis.jobs.agent_snapshot import build_normal_snapshot, digest
from goal_analysis.jobs.ten_agent_ticket_evidence import build_ten_agent_ticket_evidence


def test_core_and_club_own_search_source_reads_tools_final_and_canonical_evidence(tmp_path):
    owners = ('kronikas', 'tipster_fek_tade')
    report, artifacts, enriched, club, _, _, _ = inputs(owners=owners)
    # Distinct fixture model values make the club's own original rank prefer
    # its home products; the application does not suppress cross-owner choices.
    for model in artifacts['numeric_models'].values():
        for prediction in model['predictions']:
            prediction['probabilities'].update(home=.70, draw=.20, away=.10)
            if 'over25' in prediction['probabilities']:
                prediction['probabilities'].update(over25=.60, under25=.40, btts_yes=.60)
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
        snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched, club_enriched=club)
    builder = build_agent_option_builder(report, artifacts, enriched, club, SETTINGS, NOW)
    original = digest((artifacts, enriched, club, snapshot.data()))
    view = decision_view(snapshot)
    seed = build_source_seed(snapshot, view)
    bundle = SourceBundle.from_snapshot(snapshot, view)
    source_root = tmp_path / 'source'
    source_root.mkdir()
    bundle_path = source_root / 'bundle.json'
    bundle_path.write_text(canonical(bundle.as_dict()) + '\n', encoding='utf-8')
    calls, chosen = [], {}

    def file_sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def host(request, *, timeout):
        calls.append(deepcopy(request))
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        owner, data = request['agent_id'], request['data']
        assert owner in owners
        if request['phase'] == 'PLAN':
            own = data['own_strategy_search']
            assert own['agent_id'] == owner and own['suggested_count'] >= 2
            assert own['full_search_result_read_by_host'] is False
            assert data['snapshot']['fixture_ids'] == list(snapshot.fixture_ids)
            assert len(snapshot.fixture_ids) == 16
            assert data['own_construction_contract']['full_candidate_catalog']['source_row_count'] == 16
            proposals = [{'option_id': row['search_option_id'],
                'source_candidate_ids': row['source_candidate_ids'],
                'reason': f'TESTONLY {owner} chooses its own complete ranked suggestion {index}.'}
                for index, row in enumerate(own['suggestions'][:2])]
            chosen[owner] = deepcopy(proposals)
            reads = list(dict.fromkeys(key for row in proposals for key in row['source_candidate_ids']))
            answer = {**identity, 'options': proposals, 'fixture_reviews': [
                {'fixture_id': key, 'reason': 'TESTONLY complete shared calendar considered.', 'rejected': False}
                for key in snapshot.fixture_ids], 'tool_requests': [
                {'role': role, 'parameters': {'window': None, 'policy_hash': None}} for role in SIMULATOR_ROLES]}
        else:
            assert data['own_option_admission']['rejected_options'] == []
            options = data['validated_own_options']
            assert [row['option_id'] for row in options] == [row['option_id'] for row in chosen[owner]]
            assert len(options) == 2 and all(row['agent_id'] == owner for row in options)
            tools = {row['request']['role']: row for row in data['tool_results']}
            assert set(tools) == set(SIMULATOR_ROLES)
            assert tools['candidate_scenarios']['result']['status'] == 'AVAILABLE'
            assert tools['historical_strategy_evaluation']['result']['status'] == 'UNKNOWN'
            selected = [row['option_id'] for row in options]
            answer = {**identity, 'option_ids': selected,
                'reason': 'TESTONLY explicit own choice after scenario evidence and honest historical gap.',
                'tool_interpretations': [{'request_id': row['request']['request_id'],
                    'interpretation': 'TESTONLY actual own tool result interpreted; historical UNKNOWN is not neutral.'}
                    for row in data['tool_results']],
                'family_uses': [{'family': name,
                    'status': ('AVAILABLE_AND_CONSIDERED' if family['status'] == 'AVAILABLE' else
                               {'UNKNOWN': 'MISSING', 'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}[family['status']]),
                    'value_sha256': family['value_sha256'], 'option_ids': selected,
                    'reason': 'TESTONLY own source availability considered without claiming causal influence.',
                    'risk': 'Historical producer and other missing context remain explicit uncertainty.'}
                    for name, family in data['snapshot']['families'].items()]}
            reads = []
        server, kernel = Path(profile_source_stdio.__file__), Path(profile_source_access.__file__)
        scope = SourceAccessScope(source_root, bundle_path, file_sha(bundle_path), identity,
            Path(sys.executable), server, file_sha(server), kernel, file_sha(kernel))
        return finish_fake_source_reply(scope, request, reads, answer)

    states = {row.agent_id: row for row in start_sessions(snapshot,
        {owner: get_agent_definition(owner).method_version for owner in AGENT_IDS})}
    journal = DecisionJournal(tmp_path / 'journal', snapshot)
    runner = ProfileDecisionRunner(host=host, tools=ProfileToolService(snapshot), option_builder=builder,
        journal=journal, source_bundle=bundle,
        budget=DecisionBudget(4, NOW + timedelta(seconds=120), clock=lambda: NOW))
    completed = {owner: runner._owner(states[owner], snapshot, view, seed) for owner in owners}
    for owner, value in completed.items():
        assert value['status'] == 'DECISION_COMPLETED'
        assert len(value['decision']['selected_options']) == 2
        record = json.loads((journal.directory / f'{states[owner].session_id}.strategy-search.json').read_text('utf-8'))
        assert record['result'] == builder.strategy_search(owner, snapshot)
        assert len(value['decision_evidence']['tool_results']) == 2
    outcomes = [completed.get(owner) or {'agent_id': owner, 'session_id': states[owner].session_id,
        'snapshot_id': snapshot.snapshot_id, 'status': 'INCOMPLETE',
        'reason': 'TESTONLY other eight owners deliberately not exercised.'} for owner in AGENT_IDS]
    result = {'version': 'ten-agent-decision-run-v1', 'snapshot_id': snapshot.snapshot_id,
        'agent_count': 10, 'owners': outcomes, 'status': 'PARTIAL', 'host_requests_reserved': 4,
        'distinct_decision_count': 4, 'minimum15_decision_count_met': False, 'wagers': 0}
    assert digest((artifacts, enriched, club, snapshot.data())) == original
    artifacts['independent_agent_snapshot'] = snapshot.data()
    artifacts['independent_agent_decisions'] = deepcopy(result)
    publish_agent_decisions(report, result, SETTINGS, NOW)
    cards = report['generated_tickets']
    assert len(cards) == 4 and report['construction_status'] == 'PARTIAL'
    assert {owner: sum(row['profile_id'] == owner for row in cards) for owner in owners} == dict.fromkeys(owners, 2)
    assert all(row['status'] == 'DRAFT' and row['publication_scope'] == 'RESEARCH'
               and not row['playable'] and row['stake_eur'] is None for row in cards)
    assert all(15 <= row['combined_price'] <= 40 for row in cards)
    evidence = build_ten_agent_ticket_evidence(report, artifacts)
    assert evidence['snapshot_binding']['status'] == 'SAVED_SNAPSHOT_BOUND'
    assert evidence['snapshot_binding']['fixture_count'] == 16
    for owner in owners:
        check = evidence['owner_checks'][owner]
        assert check['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
        assert check['declared_fixture_count'] == 16 and check['tool_count'] == 2
        assert check['source_access_validation']['PLAN']['read_count'] >= len({
            key for proposal in chosen[owner] for key in proposal['source_candidate_ids']})
    assert all(row['own_selection']['status'] == 'EXACT_OWN_CANONICAL_SOURCE_BOUND'
               and row['owner_evidence']['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
               for row in evidence['cards'])
    assert evidence['accounting']['canonical_card_count'] == evidence['accounting']['audited_card_count'] == 4
    assert len(calls) == runner.budget.used_requests == 4
    assert {row['agent_id'] for row in calls} == set(owners)
