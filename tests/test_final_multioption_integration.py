"""Actual fixture runner/wire/proof/auditor integration; no paid host or DB."""
import hashlib
import json
import sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from test_agent_portfolio import NOW, inputs
from test_profile_source_host import finish_fake_source_reply
from test_ten_agent_ticket_evidence import saved_frame

from goal_analysis.agents import profile_source_access, profile_source_stdio
from goal_analysis.agents.profile_decision import SIMULATOR_ROLES, start_sessions
from goal_analysis.agents.profile_decision_runner import (
    DecisionBudget,
    DecisionJournal,
    ProfileDecisionRunner,
    source_final_reference,
)
from goal_analysis.agents.profile_input import decision_view, final_shared_reference
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.agents.profile_source_access import SourceBundle, build_source_seed
from goal_analysis.agents.profile_source_host import SourceAccessScope, canonical
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.jobs.agent_snapshot import digest
from goal_analysis.jobs.ten_agent_ticket_evidence import (
    _owner_evidence,
    build_ten_agent_ticket_evidence,
)


def _decode_final(data):
    """Independent decoder for the explicit wire table, not its encoder."""
    table = data['exact_values']

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            if set(value) == {'__arthur_final_ref__'}:
                key = value['__arthur_final_ref__']
                entry = table[key]
                original = expand(entry['value'])
                assert entry['source_value_sha256'] == key == digest(original)
                return original
            return {key: expand(item) for key, item in value.items()}
        return value

    original = {key: expand(value) for key, value in data.items()
                if key not in {'exact_values', 'final_data_projection'}}
    assert digest(original) == data['final_data_projection']['source_data_sha256']
    return original


def test_three_own_options_actual_projected_wire_private_proof_and_legacy_audit(tmp_path):
    _, _, _, _, snapshot, builder, proposals = inputs(owners=('kronikas',), variants=3)
    view = decision_view(snapshot)
    seed = build_source_seed(snapshot, view)
    bundle = SourceBundle.from_snapshot(snapshot, view)
    source_root = tmp_path / 'source'
    source_root.mkdir()
    bundle_path = source_root / 'bundle.json'
    bundle_path.write_text(canonical(bundle.as_dict()) + '\n', encoding='utf-8')
    calls, final_data = [], []

    def file_sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def host(request, *, timeout):
        calls.append(deepcopy(request))
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        assert identity['agent_id'] == 'kronikas'
        if request['phase'] == 'PLAN':
            assert request['data']['snapshot']['fixture_ids'] == list(snapshot.fixture_ids)
            assert len(snapshot.fixture_ids) == 12
            answer = {**identity, 'options': deepcopy(proposals['kronikas']),
                'fixture_reviews': [{'fixture_id': key, 'rejected': False,
                    'reason': 'TESTONLY complete calendar considered.'} for key in snapshot.fixture_ids],
                'tool_requests': [{'role': role, 'parameters': {}} for role in SIMULATOR_ROLES]}
            reads = [key for row in proposals['kronikas'] for key in row['source_candidate_ids']]
        else:
            wire = request['data']
            assert wire['final_data_projection']['version'] == 'FINAL_EXACT_SUBTREES_V1'
            assert wire['exact_values']
            data = _decode_final(wire)
            final_data.append(data)
            options = data['validated_own_options']
            assert len(options) == 3
            assert [row['option_id'] for row in options] == [row['option_id'] for row in proposals['kronikas']]
            assert all(row['agent_id'] == 'kronikas' for row in options)
            tools = data['tool_results']
            assert {row['request']['role'] for row in tools} == set(SIMULATOR_ROLES)
            assert len(tools) == 2
            assert data['own_option_admission']['rejected_options'] == []
            selected = [row['option_id'] for row in options]
            answer = {**identity, 'option_ids': selected,
                'reason': 'TESTONLY three independent own products retain all source/tool evidence.',
                'tool_interpretations': [{'request_id': row['request']['request_id'],
                    'interpretation': 'TESTONLY actual own tool result interpreted without making UNKNOWN neutral.'}
                    for row in tools],
                'family_uses': [{'family': name,
                    'status': ('AVAILABLE_AND_CONSIDERED' if family['status'] == 'AVAILABLE' else
                               {'UNKNOWN': 'MISSING', 'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}[family['status']]),
                    'value_sha256': family['value_sha256'], 'option_ids': selected,
                    'reason': 'TESTONLY own input availability retained; causal effect is not claimed.',
                    'risk': 'Missing history and optional evidence remain explicit uncertainty.'}
                    for name, family in data['snapshot']['families'].items()]}
            reads = []
        server, kernel = Path(profile_source_stdio.__file__), Path(profile_source_access.__file__)
        scope = SourceAccessScope(source_root, bundle_path, file_sha(bundle_path), identity,
            Path(sys.executable), server, file_sha(server), kernel, file_sha(kernel))
        # The real transport validator binds the original projected request,
        # not the fake host's independently expanded readable working value.
        return finish_fake_source_reply(scope, request, reads, answer)

    states = {state.agent_id: state for state in start_sessions(snapshot,
        {owner: get_agent_definition(owner).method_version for owner in AGENT_IDS})}
    journal = DecisionJournal(tmp_path / 'journal', snapshot)
    runner = ProfileDecisionRunner(host=host, tools=ProfileToolService(snapshot), option_builder=builder,
        journal=journal, source_bundle=bundle,
        budget=DecisionBudget(2, NOW + timedelta(seconds=120), clock=lambda: NOW))
    owner = runner._owner(states['kronikas'], snapshot, view, seed)
    assert owner['status'] == 'DECISION_COMPLETED'
    assert len(owner['decision']['selected_options']) == 3
    assert owner['decision_evidence']['final_request_projection'] == 'FINAL_EXACT_SUBTREES_V1'
    assert [request['phase'] for request in calls] == ['PLAN', 'FINAL']
    assert len(final_data) == 1 and runner.budget.used_requests == 2
    actual = calls[1]
    purpose = {key: actual[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
    claim = json.loads((journal.directory / (digest(purpose) + '.claim.json')).read_text('utf-8'))
    assert claim['request'] == actual and claim['request_sha256'] == digest(actual)
    assert digest({**actual, 'data': final_data[0]}) != claim['request_sha256']
    final_shared = final_shared_reference(snapshot, view)
    source_final = source_final_reference(snapshot, seed)
    shared = {'states': states, 'source_bundle': bundle, 'source_seed': seed,
        'source_seed_sha256': digest(seed), 'source_final_shared': source_final,
        'source_final_shared_sha256': digest(source_final), 'plan_view_sha256': digest(view),
        'final_shared_sha256': digest(final_shared),
        'manifest_sha256': digest(snapshot.data()['source_manifest'])}
    checked = _owner_evidence(owner, snapshot, shared)
    assert checked['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
    assert checked['tool_count'] == 2 and checked['declared_fixture_count'] == 12
    assert checked['source_access_validation']['PLAN']['read_count'] >= 12

    # Existing saved unprojected envelopes are still interpreted as their
    # actual old wire; no new projection marker or hash is retrofitted.
    old_report, old_artifacts = saved_frame()
    before = deepcopy((old_report, old_artifacts))
    assert all('final_request_projection' not in row['decision_evidence']
               for row in old_report['ten_agent_decisions']['owners'])
    audit = build_ten_agent_ticket_evidence(old_report, old_artifacts)
    assert (old_report, old_artifacts) == before
    assert audit['accounting']['canonical_card_count'] == audit['accounting']['audited_card_count'] == 20
    assert all(row['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
               for row in audit['owner_checks'].values())
