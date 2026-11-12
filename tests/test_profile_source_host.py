"""TESTONLY source transport: actual pure kernel/validator, inert native events."""
import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from goal_analysis.agents import profile_source_access, profile_source_stdio
from goal_analysis.agents.codex_review import CodexReviewer
from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.agents.profile_source_access import (
    FAMILY_NAMES,
    SourceAccessKernel,
    SourceBundle,
)
from goal_analysis.agents.profile_source_host import (
    SourceAccessReply,
    SourceAccessScope,
    canonical,
    finish_source_evidence,
    observe_source_events,
    sha,
    source_environment,
    validate_source_evidence,
)

NOW = datetime(2026, 11, 12, 10, tzinfo=UTC)


def test_source_preparation_counts_inside_phase_deadline_before_any_host(tmp_path, monkeypatch):
    scope, request, _ = make_source_phase(tmp_path)
    calls = []
    reviewer = CodexReviewer(command=['TESTONLY:no-execution'],
                             runner=lambda *args, **kwargs: calls.append((args, kwargs)))
    ticks = iter((0.0, 6.0))
    monkeypatch.setattr('goal_analysis.agents.codex_review.time.monotonic', lambda: next(ticks))
    monkeypatch.setattr(SourceAccessScope, 'prepare', lambda self, body: {'TESTONLY': True})
    with pytest.raises(Exception, match='deadline reached during source preparation') as error:
        reviewer.request_profile_decision(request, timeout=5, source_access=scope)
    assert calls == []
    assert error.value.host_usage['requests'] == 0
    assert error.value.host_usage['input_tokens'] is None


def _write(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(canonical(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def make_source_phase(tmp_path, *, phase='PLAN'):
    root = tmp_path / 'source-root'
    root.mkdir()
    raw = {'version': 'TESTONLY', 'run_id': 'TESTONLY-source-host', 'target_date': '2026-11-12',
        'as_of': NOW.isoformat(), 'owner_ids': list(AGENT_IDS), 'fixture_ids': ['TESTONLY:f'],
        'families': {name: {'status': 'UNKNOWN', 'value': None, 'value_sha256': sha(None)}
                     for name in FAMILY_NAMES}}
    for name, value in {'calendar': [{'fixture_id': 'TESTONLY:f'}],
        'odds_and_price_history': {'candidate_input': {'candidates': [
            {'candidate_id': 'TESTONLY:c', 'fixture_id': 'TESTONLY:f', 'quoted_at': None,
             'news': 'TESTONLY injury uncertainty; not a real source.'}]}},
        'weather': {'temperature_c': -2.5, 'forecast': None}}.items():
        raw['families'][name] = {'status': 'AVAILABLE', 'value': value, 'value_sha256': sha(value)}
    view = {**deepcopy(raw), 'view': {'source_snapshot_id': sha(raw)}}
    bundle = SourceBundle(canonical(raw), canonical(view), sha(raw), sha(view))
    path = root / 'bundle.json'
    _write(path, bundle.as_dict())
    binding = {'agent_id': AGENT_IDS[0], 'session_id': 'TESTONLY-source-session',
               'snapshot_id': bundle.snapshot_id, 'phase': phase}
    server, kernel = Path(profile_source_stdio.__file__), Path(profile_source_access.__file__)
    scope = SourceAccessScope(root, path, hashlib.sha256(path.read_bytes()).hexdigest(), binding,
        Path(sys.executable), server, hashlib.sha256(server.read_bytes()).hexdigest(),
        kernel, hashlib.sha256(kernel.read_bytes()).hexdigest())
    request = {**binding, 'data': {'fixture_ids': raw['fixture_ids']}}
    return scope, request, bundle


def _fake_source_files(prepared, reads):
    """Actual kernel/fsynced TESTONLY files; native event objects are explicit fixtures."""
    events, records = [], []
    with prepared['journal_path'].open('x', encoding='utf-8', newline='\n') as journal:
        def append(record):
            records.append(deepcopy(record))
            journal.write(canonical(record) + '\n')
            journal.flush()
            os.fsync(journal.fileno())
            return sha(record)

        kernel = SourceAccessKernel(prepared['bundle'], prepared['document']['binding'], append, lambda: NOW)
        for read in reads:
            offset = 0
            while True:
                arguments = ({**prepared['document']['binding'], 'namespace': 'candidates',
                    'path': [read], 'mode': 'text', 'offset': offset, 'limit': 24_000}
                    if isinstance(read, str) else deepcopy(read))
                identifier = f'TESTONLY-native-{len(events)}'
                response = kernel.read_data(arguments, rpc_id=len(events) + 1)
                result = response['result']
                events.append({'type': 'item.completed', 'item': {'type': 'mcp_tool_call',
                    'id': identifier, 'server': 'arthur_data', 'tool': 'read_data',
                    'arguments': arguments, 'result': {'content': result['content'], 'structured_content': None},
                    'status': 'completed'}})
                payload = json.loads(result['content'][0]['text'])
                offset = payload.get('page', {}).get('next_offset')
                if not isinstance(read, str) or offset is None:
                    break
        receipt = {**kernel.receipt(), 'stdio_closed': True, 'stdio_outcome': 0,
            'protocol_error_type': None, 'forbidden_operations': [], 'stdio_protocol': '2025-06-18',
            'closed_at': NOW.isoformat()}
        _write(prepared['receipt_path'], receipt)
    return events, records


def finish_fake_source_reply(scope, request, reads, answer):
    """Support for other pure integration fixtures, never an actual CLI result.

    reads is a list of original candidate-ID strings (complete pages), or exact
    request dictionaries (one bounded query). Empty reads is valid for abstention
    or FINAL. The real host validator is always called; it is never mocked.
    """
    prepared = scope.prepare(request)
    events, _ = _fake_source_files(prepared, reads)
    proof = finish_source_evidence(prepared, events, request, answer)
    return SourceAccessReply.create(answer, proof)


def test_actual_kernel_native_fixture_and_host_reconcile_without_source_copy_in_reply(tmp_path):
    scope, request, bundle = make_source_phase(tmp_path)
    answer = {**scope.identity(), 'options': []}
    result = finish_fake_source_reply(scope, request, ['TESTONLY:c'], answer)
    assert isinstance(result, SourceAccessReply) and result.reply() == answer
    proof = result.evidence()
    assert validate_source_evidence(proof, request=request, reply=answer, bundle=bundle)['read_count'] == 1
    assert proof['journal_records'][0]['namespace'] == 'candidates'
    assert 'text' not in proof['journal_records'][0]['page']
    assert proof['validation']['causal_feature_use_proven'] is False


@pytest.mark.parametrize('defect', ['binding', 'native_payload', 'source_page', 'stdio_failure'])
def test_changed_source_or_native_proof_cannot_become_a_success(tmp_path, defect):
    scope, request, bundle = make_source_phase(tmp_path)
    answer = {**scope.identity(), 'options': []}
    proof = finish_fake_source_reply(scope, request, ['TESTONLY:c'], answer).evidence()
    if defect == 'binding':
        proof['binding']['session_id'] = 'TESTONLY-foreign'
    elif defect == 'native_payload':
        proof['native_calls'][0]['payload_sha256'] = '0' * 64
    elif defect == 'source_page':
        proof['journal_records'][0]['page']['returned'] -= 1
    else:
        proof['receipt']['stdio_outcome'] = 1
    with pytest.raises(ValueError):
        validate_source_evidence(proof, request=request, reply=answer, bundle=bundle)


def test_phase_once_and_empty_final_do_not_invent_reads(tmp_path):
    scope, request, bundle = make_source_phase(tmp_path, phase='FINAL')
    answer = {**scope.identity(), 'option_ids': []}
    proof = finish_fake_source_reply(scope, request, [], answer).evidence()
    assert validate_source_evidence(proof, request=request, reply=answer, bundle=bundle)['read_count'] == 0
    with pytest.raises(FileExistsError):
        scope.prepare(request)


def test_source_environment_has_no_service_credentials_but_keeps_login_paths(monkeypatch):
    for name in ('OPENAI_API_KEY', 'GITHUB_TOKEN', 'XAI_API_KEY', 'UNKNOWN_SERVICE_SECRET'):
        monkeypatch.setenv(name, 'TESTONLY-private-value')
    monkeypatch.setenv('CODEX_HOME', 'TESTONLY-auth-path')
    assert 'TESTONLY-private-value' not in source_environment().values()
    assert source_environment()['CODEX_HOME'] == 'TESTONLY-auth-path'
    assert 'CODEX_HOME' not in source_environment(child=True)


def test_known_current_secret_in_source_stops_before_phase_creation(tmp_path, monkeypatch):
    scope, request, _ = make_source_phase(tmp_path)
    monkeypatch.setenv('THE_STATS_API_KEY', 'TESTONLY injury uncertainty')
    with pytest.raises(ValueError, match='SOURCE_CONTAINS_CURRENT_CREDENTIAL'):
        scope.prepare(request)
    assert not (Path(scope.owned_root) / 'p').exists()


def test_multiple_turn_usage_is_observed_but_missing_token_field_is_unknown():
    events = [{'type': 'turn.completed', 'usage': {'input_tokens': 8, 'output_tokens': 2,
                'cached_input_tokens': 1, 'reasoning_output_tokens': 1}},
              {'type': 'turn.completed', 'usage': {'input_tokens': 9, 'output_tokens': 3,
                'cached_input_tokens': 2}}]
    usage = {}
    observe_source_events('\n'.join(map(canonical, events)), usage)
    assert usage['input_tokens'] == 17 and usage['output_tokens'] == 5 and usage['total_tokens'] == 22
    assert usage['reasoning_tokens'] is None and usage['observed_completed_turns'] == 2
    assert usage['internal_model_turn_count'] is None


class SourceCliFixture:
    def __init__(self, *, forbidden=False, failed=False):
        self.forbidden, self.failed, self.calls = forbidden, failed, []

    def __call__(self, command, *, prompt, cwd, timeout, env=None):
        self.calls.append((command, prompt, cwd, timeout, env))
        if command[-1] == '--version':
            return subprocess.CompletedProcess(command, 0, 'codex-cli 0.159.2', '')
        if command[-2:] == ['login', 'status']:
            return subprocess.CompletedProcess(command, 0, '', 'Logged in using ChatGPT')
        config = {command[index + 1].split('=', 1)[0]: json.loads(command[index + 1].split('=', 1)[1])
                  for index, part in enumerate(command[:-1]) if part == '-c'}
        args = config['mcp_servers.arthur_data.args']
        binding_path = Path(args[args.index('--binding') + 1])
        document = json.loads(binding_path.read_text('utf-8'))
        bundle = SourceBundle(**json.loads(Path(document['bundle_path']).read_text('utf-8')))
        prepared = {'document': document, 'bundle': bundle,
            'journal_path': Path(document['journal_path']), 'receipt_path': Path(document['receipt_path'])}
        events, _ = _fake_source_files(prepared, ['TESTONLY:c'])
        if self.forbidden:
            events.append({'type': 'item.completed', 'item': {'type': 'command_execution',
                'id': 'TESTONLY-forbidden', 'status': 'completed'}})
        events.append({'type': 'turn.completed', 'usage': {'input_tokens': 20, 'output_tokens': 4,
            'cached_input_tokens': 2}})
        answer = {**document['binding'], 'options': []}
        Path(command[command.index('--output-last-message') + 1]).write_text(canonical(answer), encoding='utf-8')
        return subprocess.CompletedProcess(command, 1 if self.failed else 0,
            '\n'.join(map(canonical, events)), 'TESTONLY-failure' if self.failed else '')


def test_existing_host_source_mode_config_and_receipt_are_private_and_bounded(tmp_path):
    scope, request, _ = make_source_phase(tmp_path)
    cli = SourceCliFixture()
    reviewer = CodexReviewer(command=['TESTONLY:no-execution'], runner=cli)
    result = reviewer.request_profile_decision(request, timeout=5, source_access=scope)
    assert isinstance(result, SourceAccessReply) and result.evidence()['validation']['read_count'] == 1
    command, prompt, _, timeout, env = cli.calls[-1]
    assert 'web_search="disabled"' in command and 'features.code_mode_host=false' in command
    assert 'mcp_servers.arthur_data.required=true' in command
    assert 'mcp_servers.arthur_data.enabled_tools=["read_data"]' in command
    assert all(name in command for name in ('shell_tool', 'apps', 'plugins', 'multi_agent'))
    assert 0 < timeout <= 5 and env == source_environment()
    assert result.reply()['host_usage']['total_tokens'] == 24
    assert result.reply()['host_usage']['cost_amount'] is None
    assert result.evidence()['validation']['causal_feature_use_proven'] is False
    assert str(scope.bundle_path) not in prompt and 'complete candidates namespace record' in prompt


@pytest.mark.parametrize('fault', ['forbidden', 'native_failure'])
def test_host_failure_preserves_observed_usage_and_never_returns_accepted_source_reply(tmp_path, fault):
    scope, request, _ = make_source_phase(tmp_path)
    cli = SourceCliFixture(forbidden=fault == 'forbidden', failed=fault == 'native_failure')
    reviewer = CodexReviewer(command=['TESTONLY:no-execution'], runner=cli)
    with pytest.raises(Exception) as error:
        reviewer.request_profile_decision(request, timeout=5, source_access=scope)
    assert error.value.host_usage['requests'] == 1
    assert error.value.host_usage['input_tokens'] == 20
    assert error.value.host_usage['reasoning_tokens'] is None
    assert len([call for call in cli.calls if 'exec' in call[0]]) == 1
