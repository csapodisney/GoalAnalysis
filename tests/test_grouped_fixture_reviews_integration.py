"""Grouped and legacy PLAN wire binding with fixture-only native events, not model calls."""
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
from goal_analysis.agents.profile_input import decision_view, decode_table, final_shared_reference
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.agents.profile_source_access import SourceBundle, build_source_seed
from goal_analysis.agents.profile_source_host import SourceAccessScope, canonical
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.jobs.agent_snapshot import digest
from goal_analysis.jobs.ten_agent_ticket_evidence import _owner_evidence


def _original_data(wire):
    data = deepcopy(wire)
    if 'coverage_data_projection' in data:
        marker = data.pop('coverage_data_projection')
        coverage = data['snapshot']['families']['sourced_news']['coverage']
        coverage['by_fixture_topic'] = decode_table(coverage['by_fixture_topic'])
        assert digest(data) == marker['source_data_sha256']
    if 'final_data_projection' not in data:
        return data
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

    original = {key: expand(value) for key, value in data.items()
                if key not in {'exact_values', 'final_data_projection'}}
    assert digest(original) == data['final_data_projection']['source_data_sha256']
    return original


def test_grouped_wire_audit_and_legacy_order(tmp_path):
    _, artifacts, _, _, snapshot, builder, proposals = inputs(owners=('kronikas',), variants=3)
    selected_source_ids = set(proposals['kronikas'][0]['source_candidate_ids'])
    selected_fixture_ids = {row['fixture_id'] for row in artifacts['candidate_input']['candidates']
                            if row['candidate_id'] in selected_source_ids}
    view = decision_view(snapshot)
    seed = build_source_seed(snapshot, view)
    bundle = SourceBundle.from_snapshot(snapshot, view)
    states = {state.agent_id: state for state in start_sessions(snapshot,
        {owner: get_agent_definition(owner).method_version for owner in AGENT_IDS})}
    source_final = source_final_reference(snapshot, seed)
    shared = {'states': states, 'source_bundle': bundle, 'source_seed': seed,
        'source_seed_sha256': digest(seed), 'source_final_shared': source_final,
        'source_final_shared_sha256': digest(source_final), 'plan_view_sha256': digest(view),
        'final_shared_sha256': digest(final_shared_reference(snapshot, view)),
        'manifest_sha256': digest(snapshot.data()['source_manifest'])}

    def file_sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    for grouped in (True, False):
        root = tmp_path / ('g' if grouped else 'l')
        root.mkdir()
        bundle_path = root / 'bundle.json'
        bundle_path.write_text(canonical(bundle.as_dict()) + '\n', encoding='utf-8')
        calls, raw_plans = [], []

        def host(request, *, timeout, current_mode=grouped, current_root=root, current_bundle=bundle_path,
                 current_calls=calls, current_plans=raw_plans):
            current_calls.append(deepcopy(request))
            identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
            data = _original_data(request['data'])
            if request['phase'] == 'PLAN':
                if current_mode:
                    contract = data['fixture_review_contract']
                    assert contract['fixture_order_sha256'] == digest(list(snapshot.fixture_ids))
                    reviews = {'version': contract['version'],
                        'fixture_order_sha256': contract['fixture_order_sha256'],
                        'groups': [
                            {'fixture_indices': [index for index, key in enumerate(snapshot.fixture_ids)
                                                 if key in selected_fixture_ids],
                             'reason': 'TESTONLY own selected and alternative evidence.', 'rejected': False},
                            {'fixture_indices': [index for index, key in enumerate(snapshot.fixture_ids)
                                                 if key not in selected_fixture_ids],
                             'reason': 'TESTONLY own counterevidence remains distinct.', 'rejected': True},
                        ]}
                else:
                    assert 'fixture_review_contract' not in data
                    reviews = [{'fixture_id': key, 'reason': 'TESTONLY original reverse-order review.',
                                'rejected': False} for key in reversed(snapshot.fixture_ids)]
                answer = {**identity, 'options': deepcopy(proposals['kronikas'][:1]),
                    'fixture_reviews': reviews,
                    'tool_requests': [{'role': role, 'parameters': {}} for role in SIMULATOR_ROLES]}
                reads = list(proposals['kronikas'][0]['source_candidate_ids'])
            else:
                assert data['own_plan'] == current_plans[0]
                selected = [row['option_id'] for row in data['validated_own_options']]
                assert len(selected) == 1
                answer = {**identity, 'option_ids': selected, 'reason': 'TESTONLY own supported variant.',
                    'tool_interpretations': [{'request_id': pair['request']['request_id'],
                        'interpretation': 'TESTONLY actual optional UNKNOWN is not zero or profit.'}
                        for pair in data['tool_results']],
                    'family_uses': [{'family': name, 'status': {'AVAILABLE': 'AVAILABLE_AND_CONSIDERED',
                        'UNKNOWN': 'MISSING', 'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}[family['status']],
                        'value_sha256': family['value_sha256'], 'option_ids': selected,
                        'reason': 'TESTONLY connected source considered with original uncertainty.',
                        'risk': 'TESTONLY optional history and news uncertainty retained.'}
                        for name, family in data['snapshot']['families'].items()]}
                reads = []
            server, kernel = Path(profile_source_stdio.__file__), Path(profile_source_access.__file__)
            scope = SourceAccessScope(current_root, current_bundle, file_sha(current_bundle), identity,
                Path(sys.executable), server, file_sha(server), kernel, file_sha(kernel))
            reply = finish_fake_source_reply(scope, request, reads, answer)
            if request['phase'] == 'PLAN':
                current_plans.append(reply.reply())
            return reply

        journal = DecisionJournal(root / 'j', snapshot)
        runner = ProfileDecisionRunner(host=host, tools=ProfileToolService(snapshot),
            option_builder=lambda owner, proposal, source: builder(owner, proposal, source),
            journal=journal, source_bundle=bundle, grouped_fixture_reviews=grouped,
            budget=DecisionBudget(2, NOW + timedelta(seconds=120), clock=lambda: NOW))
        owner = runner._owner(states['kronikas'], snapshot, view, seed)
        assert owner['status'] == 'DECISION_COMPLETED'
        before = deepcopy(owner)
        evidence = owner['decision_evidence']
        assert evidence['plan'] == raw_plans[0]
        assert evidence['source_access']['PLAN']['reply_sha256'] == digest(evidence['plan'])
        identity = {key: calls[0][key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        saved = json.loads((journal.directory / (digest(identity) + '.outcome.json')).read_text('utf-8'))
        assert saved['reply'] == raw_plans[0]
        checked = _owner_evidence(owner, snapshot, shared)
        assert checked['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
        assert checked['declared_fixture_count'] == 12 and checked['tool_count'] == 2
        assert checked['family_count'] == 12 and len(owner['decision']['selected_options']) == 1
        assert owner == before and [call['phase'] for call in calls] == ['PLAN', 'FINAL']
        if grouped:
            forged = deepcopy(owner)
            auxiliary = forged['decision_evidence']['plan_auxiliary_inputs']
            auxiliary['values'].pop('fixture_review_contract')
            auxiliary['sha256'] = digest(auxiliary['values'])
            with pytest.raises(ValueError, match='SOURCE_EVIDENCE_BINDING_CHANGED'):
                _owner_evidence(forged, snapshot, shared)
        else:
            assert [row['fixture_id'] for row in evidence['plan']['fixture_reviews']] == list(
                reversed(snapshot.fixture_ids))
