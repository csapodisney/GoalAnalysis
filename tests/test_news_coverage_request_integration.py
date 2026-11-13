"""New PLAN and FINAL coverage wire/proof/audit contract; all native events are explicit fixtures."""
import hashlib
import json
import sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from test_agent_portfolio import NOW, inputs
from test_profile_source_host import finish_fake_source_reply

from goal_analysis.agents import profile_source_access, profile_source_stdio
from goal_analysis.agents.profile_decision import SIMULATOR_ROLES, start_sessions
from goal_analysis.agents.profile_decision_runner import (
    DecisionBudget,
    DecisionJournal,
    ProfileDecisionRunner,
    source_final_reference,
)
from goal_analysis.agents.profile_input import (
    NEWS_COVERAGE_REQUEST_PROJECTION,
    decision_view,
    decode_table,
    final_shared_reference,
)
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.agents.profile_source_access import SourceBundle, build_source_seed
from goal_analysis.agents.profile_source_host import SourceAccessScope, canonical
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.jobs.agent_snapshot import build_normal_snapshot, digest
from goal_analysis.jobs.ten_agent_ticket_evidence import _owner_evidence


def _read_coverage(data):
    result = deepcopy(data)
    marker = result.pop('coverage_data_projection')
    assert marker['version'] == NEWS_COVERAGE_REQUEST_PROJECTION
    coverage = result['snapshot']['families']['sourced_news']['coverage']
    coverage['by_fixture_topic'] = decode_table(coverage['by_fixture_topic'])
    assert digest(result) == marker['source_data_sha256']
    return result


def _read_final(data):
    if 'final_data_projection' not in data:
        return deepcopy(data)
    table = data['exact_values']

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            if set(value) == {'__arthur_final_ref__'}:
                entry = table[value['__arthur_final_ref__']]
                original = expand(entry['value'])
                assert digest(original) == entry['source_value_sha256'] == value['__arthur_final_ref__']
                return original
            return {key: expand(item) for key, item in value.items()}
        return value

    result = {key: expand(value) for key, value in data.items()
              if key not in {'exact_values', 'final_data_projection'}}
    assert digest(result) == data['final_data_projection']['source_data_sha256']
    return result


def test_both_phase_coverage_private_proofs_rebind_and_unmarked_old_wires_remain_exact(tmp_path):
    report, artifacts, enriched, club, _, builder, proposals = inputs(owners=('kronikas',), variants=3)
    fixture_ids = [row['fixture_id'] for row in artifacts['fixtures']['records']]
    rows = [{'fixture_id': key, 'topic': topic, 'status': 'UNKNOWN', 'published_at': None}
            for key in fixture_ids for topic in ('coach', 'injury', 'lineup', 'team', 'venue')]
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
        snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched, club_enriched=club,
        news_coverage={'by_fixture_topic': rows, 'all_fixtures_independently_researched': False})
    view = decision_view(snapshot)
    seed = build_source_seed(snapshot, view)
    original_plan_data = {'snapshot': deepcopy(seed)}
    bundle = SourceBundle.from_snapshot(snapshot, view)
    roots = {}
    for label in ('current', 'legacy'):
        root = tmp_path / label
        root.mkdir()
        path = root / 'bundle.json'
        path.write_text(canonical(bundle.as_dict()) + '\n', encoding='utf-8')
        roots[label] = root, path
    calls, original_wires = [], {}

    def file_sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def scope_for(label, identity):
        root, path = roots[label]
        server, kernel = Path(profile_source_stdio.__file__), Path(profile_source_access.__file__)
        return SourceAccessScope(root, path, file_sha(path), identity, Path(sys.executable),
            server, file_sha(server), kernel, file_sha(kernel))

    def host(request, *, timeout):
        calls.append(deepcopy(request))
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        original_wires[request['phase']] = _read_coverage(request['data'])
        assert original_wires[request['phase']]['snapshot']['families']['sourced_news']['coverage'][
            'by_fixture_topic'] == rows
        if request['phase'] == 'PLAN':
            data = original_wires['PLAN']
            assert data == original_plan_data
            assert data['snapshot']['fixture_ids'] == list(snapshot.fixture_ids)
            answer = {**identity, 'options': deepcopy(proposals['kronikas'][:1]),
                'fixture_reviews': [{'fixture_id': key, 'rejected': False,
                    'reason': 'TESTONLY entire fixture set retained.'} for key in snapshot.fixture_ids],
                'tool_requests': [{'role': role, 'parameters': {}} for role in SIMULATOR_ROLES]}
            reads = list(proposals['kronikas'][0]['source_candidate_ids'])
        else:
            data = _read_final(original_wires['FINAL'])
            selected = [row['option_id'] for row in data['validated_own_options']]
            assert len(selected) == 1 and 'coverage_data_projection' not in data
            answer = {**identity, 'option_ids': selected,
                'reason': 'TESTONLY own source-bound decision without invented evidence.',
                'tool_interpretations': [{'request_id': pair['request']['request_id'],
                    'interpretation': 'TESTONLY missing history remains UNKNOWN, not zero ROI.'}
                    for pair in data['tool_results']],
                'family_uses': [{'family': name, 'status': {'AVAILABLE': 'AVAILABLE_AND_CONSIDERED',
                    'UNKNOWN': 'MISSING', 'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}[family['status']],
                    'value_sha256': family['value_sha256'], 'option_ids': selected,
                    'reason': 'TESTONLY source availability retained without causal-use claim.',
                    'risk': 'Unknown news publication and optional history remain limitations.'}
                    for name, family in data['snapshot']['families'].items()]}
            reads = []
        return finish_fake_source_reply(scope_for('current', identity), request, reads, answer)

    states = {state.agent_id: state for state in start_sessions(snapshot,
        {owner: get_agent_definition(owner).method_version for owner in AGENT_IDS})}
    journal = DecisionJournal(tmp_path / 'journal', snapshot)
    # Hide optional search/contract methods: this distinct case tests PLAN
    # representation and exact proof binding, not another strategy search.
    runner = ProfileDecisionRunner(host=host, tools=ProfileToolService(snapshot),
        option_builder=lambda owner, proposal, source: builder(owner, proposal, source),
        journal=journal, source_bundle=bundle,
        budget=DecisionBudget(2, NOW + timedelta(seconds=120), clock=lambda: NOW))
    owner = runner._owner(states['kronikas'], snapshot, view, seed)
    assert owner['status'] == 'DECISION_COMPLETED'
    assert len(owner['decision']['selected_options']) == 1
    assert owner['decision_evidence']['coverage_request_projection'] == NEWS_COVERAGE_REQUEST_PROJECTION
    assert [call['phase'] for call in calls] == ['PLAN', 'FINAL'] and runner.budget.used_requests == 2
    for actual in calls:
        identity = {key: actual[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        claim = json.loads((journal.directory / (digest(identity) + '.claim.json')).read_text('utf-8'))
        assert claim['request'] == actual and claim['request_sha256'] == digest(actual)
        assert digest({**actual, 'data': original_wires[actual['phase']]}) != digest(actual)
    source_final = source_final_reference(snapshot, seed)
    shared = {'states': states, 'source_bundle': bundle, 'source_seed': seed,
        'source_seed_sha256': digest(seed), 'source_final_shared': source_final,
        'source_final_shared_sha256': digest(source_final), 'plan_view_sha256': digest(view),
        'final_shared_sha256': digest(final_shared_reference(snapshot, view)),
        'manifest_sha256': digest(snapshot.data()['source_manifest'])}
    original_owner = deepcopy(owner)
    checked = _owner_evidence(owner, snapshot, shared)
    assert checked['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
    assert checked['source_access_validation']['PLAN']['read_count'] >= 4
    assert checked['declared_fixture_count'] == 12 and checked['tool_count'] == 2
    assert owner == original_owner and original_plan_data == {'snapshot': seed}

    # A marker cannot be removed to reinterpret the projected transport proof.
    legacy = deepcopy(owner)
    legacy['decision_evidence'].pop('coverage_request_projection')
    with pytest.raises(ValueError, match='SOURCE_EVIDENCE_BINDING_CHANGED'):
        _owner_evidence(legacy, snapshot, shared)
    # Separate explicit old-wire fixture, no historical proof mutation or model.
    for actual in calls:
        phase = actual['phase']
        identity = {key: actual[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        legacy_request = {**actual, 'data': original_wires[phase]}
        reads = list(proposals['kronikas'][0]['source_candidate_ids']) if phase == 'PLAN' else []
        legacy_reply = finish_fake_source_reply(scope_for('legacy', identity), legacy_request,
            reads, owner['decision_evidence'][phase.lower()])
        legacy['decision_evidence']['source_access'][phase] = legacy_reply.evidence()
    before = deepcopy(legacy)
    assert _owner_evidence(legacy, snapshot, shared)['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
    assert legacy == before and owner == original_owner
