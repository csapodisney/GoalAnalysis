"""Owned pure protocol fixtures: no CLI, subprocess, provider, or inference."""
import json
import os
from io import StringIO

import pytest
from test_profile_source_host import NOW, finish_fake_source_reply, make_source_phase
from test_profile_source_stdio import _messages

from goal_analysis.agents.profile_source_access import SourceAccessKernel, require_candidate_reads
from goal_analysis.agents.profile_source_host import (
    canonical,
    finish_source_evidence,
    sha,
    source_config,
    validate_source_evidence,
)
from goal_analysis.agents.profile_source_stdio import _append_checkpoint, serve


def live_fixture(tmp_path):
    scope, request, bundle = make_source_phase(tmp_path)
    prepared = scope.prepare(request)
    output = StringIO()
    checkpoint_path = prepared['binding_path'].parent / 'receipt-checkpoints.jsonl'
    with prepared['journal_path'].open('x', encoding='utf-8', newline='\n') as journal, checkpoint_path.open(
            'x', encoding='utf-8', newline='\n') as checkpoints:
        def append(record):
            journal.write(canonical(record) + '\n')
            journal.flush()
            os.fsync(journal.fileno())
            return sha(record)

        kernel = SourceAccessKernel(bundle, scope.identity(), append, lambda: NOW)
        state = {'count': 0, 'bytes': 0, 'tail': None}

        def checkpoint(stage, ready):
            _append_checkpoint(checkpoints, kernel, state, stage, ready, [])

        kernel._stdio_checkpoint = checkpoint
        checkpoint('KERNEL_READY', False)
        arguments = {**scope.identity(), 'namespace': 'candidates', 'path': ['TESTONLY:c'],
                     'mode': 'text', 'offset': 0, 'limit': 24000}
        assert serve(_messages([
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {'_meta': {}}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
             'params': {'name': 'read_data', 'arguments': arguments}},
        ]), output, kernel) == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    events = [{'type': 'item.completed', 'item': {'id': 'TESTONLY-completed-call',
        'type': 'mcp_tool_call', 'server': 'arthur_data', 'tool': 'read_data', 'arguments': arguments,
        'status': 'completed', 'result': {'content': responses[-1]['result']['content'],
                                        'structured_content': None}}}]
    reply = {**scope.identity(), 'options': []}
    assert not prepared['receipt_path'].exists()
    return prepared, request, bundle, events, reply, checkpoint_path


def test_live_checkpoint_actual_kernel_pages_require_private_cli_zero_and_keep_exit_unknown(tmp_path):
    prepared, request, bundle, events, reply, _ = live_fixture(tmp_path)
    proof = finish_source_evidence(prepared, events, request, reply, cli_native_exit=0)
    assert proof['version'] == 'profile-source-host-evidence-v2'
    assert proof['lifecycle'] == {'mode': 'LIVE_CHECKPOINT', 'cli_native_exit': 0,
                                 'server_native_exit': None, 'server_exit_status': 'UNRECORDED'}
    assert 'stdio_closed' not in proof['receipt'] and 'stdio_outcome' not in proof['receipt']
    assert validate_source_evidence(proof, request=request, reply=reply, bundle=bundle)['read_count'] == 1
    assert require_candidate_reads(proof['journal_records'], candidate_ids=['TESTONLY:c'],
                                   bundle=bundle)['status'] == 'SELECTED_CANDIDATE_RECORDS_FULLY_RETURNED'


@pytest.mark.parametrize('native_exit', [None, 1, False])
def test_live_checkpoint_rejects_missing_failed_or_boolean_cli_outcome(tmp_path, native_exit):
    prepared, request, _, events, reply, _ = live_fixture(tmp_path)
    reply['cli_native_exit'] = 0  # A model-shaped assertion cannot supply private transport authority.
    with pytest.raises(ValueError, match='SOURCE_TRUSTED_CLI_ZERO_REQUIRED'):
        finish_source_evidence(prepared, events, request, reply, cli_native_exit=native_exit)


def test_live_checkpoint_partial_append_is_not_a_complete_proof(tmp_path):
    prepared, request, _, events, reply, path = live_fixture(tmp_path)
    with path.open('a', encoding='utf-8') as stream:
        stream.write('{"sequence":')
    with pytest.raises(ValueError, match='SOURCE_CHECKPOINT_PARTIAL_RECORD'):
        finish_source_evidence(prepared, events, request, reply, cli_native_exit=0)


def test_live_checkpoint_durable_error_cannot_be_hidden_by_cli_zero(tmp_path):
    prepared, request, _, events, reply, path = live_fixture(tmp_path)
    records = [json.loads(line) for line in path.read_text('utf-8').splitlines()]
    error = {**records[-1], 'sequence': len(records) + 1,
             'previous_checkpoint_sha256': sha(records[-1]), 'stage': 'FORBIDDEN_OPERATION',
             'protocol_ready': False, 'forbidden_operations': ['socket.connect']}
    with path.open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(canonical(error) + '\n')
    with pytest.raises(ValueError, match='SOURCE_CHECKPOINT_CHAIN_OR_ERROR'):
        finish_source_evidence(prepared, events, request, reply, cli_native_exit=0)


def test_original_closed_v1_remains_valid_and_failed_final_cannot_fall_back(tmp_path):
    (tmp_path / 'closed').mkdir()
    (tmp_path / 'failed-final').mkdir()
    scope, request, bundle = make_source_phase(tmp_path / 'closed')
    answer = {**scope.identity(), 'options': []}
    old = finish_fake_source_reply(scope, request, ['TESTONLY:c'], answer).evidence()
    assert old['version'] == 'profile-source-host-evidence-v1'
    assert validate_source_evidence(old, request=request, reply=answer, bundle=bundle)['read_count'] == 1
    prepared, request, _, events, reply, _ = live_fixture(tmp_path / 'failed-final')
    with prepared['receipt_path'].open('x', encoding='utf-8') as stream:
        stream.write(canonical({'binding': prepared['document']['binding'], 'status': 'OPEN',
            'stdio_closed': True, 'stdio_outcome': 1, 'protocol_error_type': 'ValueError',
            'forbidden_operations': []}))
    with pytest.raises(ValueError, match='SOURCE_PHASE_NOT_CLEANLY_CLOSED'):
        finish_source_evidence(prepared, events, request, reply, cli_native_exit=0)


def test_startup_capacity_bound_changes_only_startup_not_tool_timeout(tmp_path):
    scope, request, _ = make_source_phase(tmp_path)
    config = source_config(scope.prepare(request))
    assert config['mcp_servers.arthur_data.startup_timeout_sec'] == 60
    assert config['mcp_servers.arthur_data.tool_timeout_sec'] == 10
    assert config['mcp_servers.arthur_data.enabled_tools'] == ['read_data']
