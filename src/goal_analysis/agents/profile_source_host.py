"""Private, bounded transport binding for Arthur's sole immutable-source MCP tool.

No model, login, process or source I/O occurs on module import. A supplied-data
receipt is not physical authentication or proof of causal feature influence.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SERVER_NAME = 'arthur_data'
TOOL_NAME = 'read_data'
IDENTITY = ('agent_id', 'session_id', 'snapshot_id', 'phase')
TOKEN_FIELDS = {'input_tokens': 'input_tokens', 'output_tokens': 'output_tokens',
    'cached_tokens': 'cached_input_tokens', 'reasoning_tokens': 'reasoning_output_tokens'}
_OS_ENV = {'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC',
           'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'LANG', 'LC_ALL'}
_AUTH_PATH_ENV = {'USERPROFILE', 'HOME', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'CODEX_HOME'}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def sha(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def require(value, reason):
    if not value:
        raise ValueError(reason)


def source_environment(*, child=False):
    """Only OS resolution and, for Codex alone, existing saved-login paths."""
    names = _OS_ENV if child else _OS_ENV | _AUTH_PATH_ENV
    result = {key: value for key, value in os.environ.items() if key.upper() in names}
    result.update(PYTHONIOENCODING='utf-8', PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    return result


def _pin(path, expected, *, max_bytes=524_288, load=True):
    require(isinstance(expected, str) and re.fullmatch('[a-f0-9]{64}', expected), 'SOURCE_PIN_REQUIRED')
    source = Path(path)
    require(source.is_file() and 0 <= source.stat().st_size <= max_bytes, 'SOURCE_FILE_SIZE_BOUND_EXCEEDED')
    checksum, chunks, count = hashlib.sha256(), [], 0
    with source.open('rb') as stream:
        while chunk := stream.read(524_288):
            count += len(chunk)
            require(count <= max_bytes, 'SOURCE_FILE_SIZE_BOUND_EXCEEDED')
            checksum.update(chunk)
            if load:
                chunks.append(chunk)
    require(checksum.hexdigest() == expected, 'SOURCE_PIN_CHANGED')
    return b''.join(chunks) if load else None


def _inside(path, root):
    resolved, owner = Path(path).resolve(), Path(root).resolve()
    require(resolved != owner and resolved.is_relative_to(owner), 'SOURCE_PATH_OUTSIDE_OWNED_ROOT')
    return resolved


def _write_once(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(canonical(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


@dataclass(frozen=True)
class SourceAccessScope:
    owned_root: object
    bundle_path: object
    bundle_sha256: str
    binding: object
    python_executable: object
    server_script: object
    server_sha256: str
    kernel_source: object
    kernel_sha256: str

    def __post_init__(self):
        require(isinstance(self.binding, dict) and set(self.binding) == set(IDENTITY)
                and all(isinstance(self.binding[key], str) and self.binding[key] for key in IDENTITY)
                and self.binding['phase'] in {'PLAN', 'FINAL'}, 'EXACT_PRIVATE_SOURCE_BINDING_REQUIRED')
        object.__setattr__(self, 'binding', canonical(self.binding))

    def identity(self):
        return json.loads(self.binding)

    def prepare(self, request):
        binding = self.identity()
        require({key: request.get(key) for key in IDENTITY} == binding, 'FOREIGN_SOURCE_PHASE')
        root = Path(self.owned_root).resolve()
        require(root.is_dir() and Path(self.python_executable).is_absolute()
                and Path(self.python_executable).is_file(), 'SOURCE_OWNER_OR_INTERPRETER_REQUIRED')
        bundle_path = _inside(self.bundle_path, root)
        bundle_raw = _pin(bundle_path, self.bundle_sha256, max_bytes=536_870_912)
        # The same secret guard covers raw source backing, not only the seed.
        bundle_text = bundle_raw.decode('utf-8')
        secret_names = ('API_FOOTBALL_KEY', 'THE_ODDS_API_KEY', 'OPENAI_API_KEY', 'CODEX_API_KEY',
            'CODEX_ACCESS_TOKEN', 'XAI_API_KEY', 'XAI_MANAGEMENT_KEY', 'GROK_API_KEY',
            'THE_STATS_API_KEY', 'THESTATS_API_KEY', 'NVIDIA_API_KEY', 'NGC_API_KEY')
        secret_names = tuple(set(secret_names) | {name for name in os.environ
            if re.search(r'(?:API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)$', name, re.IGNORECASE)})
        require(not any(os.environ.get(name) and os.environ[name] in bundle_text for name in secret_names),
                'SOURCE_CONTAINS_CURRENT_CREDENTIAL')
        bundle = json.loads(bundle_text)
        require(set(bundle) == {'raw_json', 'view_json', 'snapshot_id', 'view_sha256'}
                and bundle['snapshot_id'] == binding['snapshot_id'], 'SOURCE_BUNDLE_BINDING_CHANGED')
        require(all(isinstance(bundle[key], str) for key in ('raw_json', 'view_json'))
                and not any(os.environ.get(name) and any(os.environ[name] in bundle[key]
                    for key in ('raw_json', 'view_json')) for name in secret_names),
                'SOURCE_CONTAINS_CURRENT_CREDENTIAL')
        from goal_analysis.agents.profile_source_access import SourceBundle

        verified_bundle = SourceBundle(**bundle)
        _pin(self.server_script, self.server_sha256)
        _pin(self.kernel_source, self.kernel_sha256)
        phase = _inside(root / 'p' / sha(binding)[:32], root)
        phase.mkdir(parents=True, exist_ok=False)
        journal, receipt = phase / 'reads.jsonl', phase / 'receipt.json'
        document = {'version': 'profile-source-host-phase-v1', 'binding': binding,
            'request_sha256': sha(request), 'owned_root': str(root), 'phase_root': str(phase),
            'bundle_path': str(bundle_path), 'bundle_sha256': self.bundle_sha256,
            'server_script': str(Path(self.server_script).resolve()), 'server_sha256': self.server_sha256,
            'kernel_source': str(Path(self.kernel_source).resolve()), 'kernel_sha256': self.kernel_sha256,
            'journal_path': str(journal), 'receipt_path': str(receipt)}
        binding_path = phase / 'binding.json'
        _write_once(binding_path, document)
        return {'document': document, 'binding_path': binding_path,
            'binding_sha256': hashlib.sha256(binding_path.read_bytes()).hexdigest(),
            'bundle_view_sha256': bundle['view_sha256'], 'journal_path': journal, 'receipt_path': receipt,
            'bundle': verified_bundle,
            'server_script': Path(self.server_script).resolve(),
            'python_executable': Path(self.python_executable).resolve()}


@dataclass(frozen=True)
class SourceAccessReply:
    reply_json: str
    evidence_json: str

    @classmethod
    def create(cls, reply, evidence):
        return cls(canonical(reply), canonical(evidence))

    def reply(self):
        return json.loads(self.reply_json)

    def evidence(self):
        return json.loads(self.evidence_json)


def source_config(prepared):
    """Documented explicit config only; no user config or generic tool list."""
    prefix = f'mcp_servers.{SERVER_NAME}'
    return {
        f'{prefix}.command': str(prepared['python_executable']),
        f'{prefix}.args': ['-I', '-B', '-X', 'utf8', str(prepared['server_script']),
            '--binding', str(prepared['binding_path']), '--binding-sha256', prepared['binding_sha256']],
        f'{prefix}.cwd': prepared['document']['phase_root'],
        f'{prefix}.env_vars': [], f'{prefix}.required': True,
        f'{prefix}.enabled_tools': [TOOL_NAME],
        f'{prefix}.startup_timeout_sec': 60, f'{prefix}.tool_timeout_sec': 10,
    }


def observe_source_events(stdout, usage):
    """Record documented per-turn observations before any later source failure."""
    events, observations, malformed = [], [], False
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            malformed = True
            continue
        if not isinstance(event, dict):
            malformed = True
            continue
        events.append(event)
        if event.get('type') == 'turn.completed':
            raw = event.get('usage')
            observations.append({field: raw.get(source) if isinstance(raw, dict)
                and type(raw.get(source)) is int and raw[source] >= 0 else None
                for field, source in TOKEN_FIELDS.items()})
    observed = []
    for field in TOKEN_FIELDS:
        values = [row[field] for row in observations]
        usage[field] = sum(values) if values and all(value is not None for value in values) else None
        if usage[field] is not None:
            observed.append(field)
    if usage['input_tokens'] is not None and usage['output_tokens'] is not None:
        usage['total_tokens'] = usage['input_tokens'] + usage['output_tokens']
        observed.append('total_tokens')
    else:
        usage['total_tokens'] = None
    usage['_profile_token_fields_observed'] = observed
    usage['observed_completed_turns'] = len(observations)
    usage['turn_usage_observations'] = observations
    usage['token_aggregation_basis'] = 'SUM_OF_DOCUMENTED_TURN_COMPLETED_USAGE_NOT_REQUEST_COUNT'
    usage['internal_model_turn_count'] = None
    require(not malformed, 'SOURCE_MODE_MALFORMED_CLI_EVENT')
    return events


def _native_calls(events):
    calls, started = {}, set()
    allowed_events = {'thread.started', 'turn.started', 'turn.completed',
                      'item.started', 'item.updated', 'item.completed'}
    for event in events:
        require(event.get('type') in allowed_events, 'SOURCE_MODE_UNKNOWN_OR_FAILED_CLI_EVENT')
        item = event.get('item')
        if item is None:
            continue
        require(isinstance(item, dict), 'SOURCE_MODE_INVALID_CLI_ITEM')
        kind = item.get('type')
        if kind in {'reasoning', 'agent_message'}:
            continue
        require(kind == 'mcp_tool_call' and item.get('server') == SERVER_NAME
                and item.get('tool') == TOOL_NAME and isinstance(item.get('arguments'), dict),
                'SOURCE_MODE_UNEXPECTED_TOOL')
        identifier = item.get('id')
        require(isinstance(identifier, str) and identifier, 'SOURCE_NATIVE_CALL_ID_REQUIRED')
        started.add(identifier)
        if event['type'] != 'item.completed':
            require(item.get('status') in {'in_progress', 'completed', 'failed'}, 'SOURCE_NATIVE_CALL_FAILED')
            continue
        require(identifier not in calls and item.get('status') in {'completed', 'failed'},
                'SOURCE_NATIVE_COMPLETION_CHANGED')
        require(item['status'] != 'completed' or item.get('error') is None, 'SOURCE_NATIVE_ERROR_ON_SUCCESS')
        result, payload_sha = item.get('result'), None
        if isinstance(result, dict):
            content = result.get('content')
            require(isinstance(content, list) and len(content) == 1
                    and isinstance(content[0], dict) and content[0].get('type') == 'text'
                    and isinstance(content[0].get('text'), str), 'SOURCE_NATIVE_RESULT_SHAPE_CHANGED')
            payload_sha = sha(json.loads(content[0]['text']))
        require(item['status'] == 'failed' or payload_sha is not None, 'SOURCE_NATIVE_RESULT_MISSING')
        calls[identifier] = {'id': identifier, 'request_sha256': sha(item['arguments']),
            'payload_sha256': payload_sha, 'status': item['status']}
    require(started == set(calls), 'SOURCE_NATIVE_CALL_INCOMPLETE')
    return list(calls.values())


def _validate_live_checkpoints(checkpoints, receipt, binding):
    require(isinstance(checkpoints, list) and 2 <= len(checkpoints) <= 165,
            'SOURCE_CHECKPOINT_CHAIN_REQUIRED')
    tail, previous_time = None, None
    for index, record in enumerate(checkpoints):
        require(isinstance(record, dict) and record.get('version') == 'profile-source-checkpoint-v1'
                and record.get('sequence') == index + 1
                and record.get('previous_checkpoint_sha256') == tail
                and record.get('forbidden_operations') == []
                and isinstance(record.get('receipt'), dict)
                and record['receipt'].get('binding') == binding and record['receipt'].get('status') == 'OPEN',
                'SOURCE_CHECKPOINT_CHAIN_OR_ERROR')
        stamp = datetime.fromisoformat(record['observed_at'])
        require(stamp.tzinfo is not None and stamp.utcoffset() is not None
                and (previous_time is None or stamp >= previous_time), 'SOURCE_CHECKPOINT_CLOCK')
        require(record.get('stage') == ('KERNEL_READY' if index == 0 else 'BEFORE_WIRE_FLUSH')
                and type(record.get('protocol_ready')) is bool
                and (index != 0 or record['protocol_ready'] is False), 'SOURCE_CHECKPOINT_STAGE')
        tail, previous_time = sha(record), stamp
    require(checkpoints[-1]['protocol_ready'] is True and checkpoints[-1]['receipt'] == receipt,
            'SOURCE_PROTOCOL_READY_CHECKPOINT_REQUIRED')
    require(not any(key in receipt for key in ('stdio_closed', 'stdio_outcome')),
            'SOURCE_LIVE_RECEIPT_MUST_NOT_CLAIM_EXIT')


def validate_source_evidence(evidence, *, request, reply, bundle):
    """Pure caller-envelope reconciliation; never authenticates causal model use."""
    binding = {key: request.get(key) for key in IDENTITY}
    request_sha256, reply_sha256 = sha(request), sha(reply)
    require(isinstance(evidence, dict) and evidence.get('version') in {
                'profile-source-host-evidence-v1', 'profile-source-host-evidence-v2'}
            and evidence.get('binding') == binding and evidence.get('request_sha256') == request_sha256
            and evidence.get('reply_sha256') == reply_sha256, 'SOURCE_EVIDENCE_BINDING_CHANGED')
    receipt, records, calls = evidence['receipt'], evidence['journal_records'], evidence['native_calls']
    if evidence['version'] == 'profile-source-host-evidence-v1':
        require(receipt['binding'] == binding and receipt['status'] == 'OPEN'
                and receipt.get('stdio_closed') is True and receipt.get('stdio_outcome') == 0
                and receipt.get('protocol_error_type') is None and receipt.get('forbidden_operations') == [],
                'SOURCE_PHASE_NOT_CLEANLY_CLOSED')
    else:
        lifecycle = evidence.get('lifecycle', {})
        require(lifecycle.get('mode') == 'LIVE_CHECKPOINT'
                and type(lifecycle.get('cli_native_exit')) is int and lifecycle['cli_native_exit'] == 0
                and lifecycle.get('server_native_exit') is None
                and lifecycle.get('server_exit_status') == 'UNRECORDED', 'SOURCE_TRUSTED_CLI_ZERO_REQUIRED')
        _validate_live_checkpoints(evidence.get('checkpoint_records'), receipt, binding)
    from goal_analysis.agents.profile_source_access import validate_read_journal

    source_summary = validate_read_journal(records, receipt, binding=binding, bundle=bundle)
    require(isinstance(records, list) and isinstance(calls, list)
            and len(calls) == len(records), 'SOURCE_CALL_ACCOUNTING_CHANGED')
    expected = Counter()
    for record in records:
        expected[(record['request_sha256'], record['payload_sha256'], record['status'])] += 1
    require(receipt['snapshot_id'] == binding['snapshot_id']
            and receipt['view_sha256'] == evidence['view_sha256'] == bundle.view_sha256
            and bundle.snapshot_id == binding['snapshot_id'], 'SOURCE_RECEIPT_SNAPSHOT_CHANGED')
    seen_ids = set()
    for call in calls:
        require(call['id'] not in seen_ids, 'DUPLICATE_NATIVE_SOURCE_CALL')
        seen_ids.add(call['id'])
        matches = [key for key in expected if expected[key] and key[0] == call['request_sha256']
                   and (key[1] == call['payload_sha256'] or call['status'] == 'failed' and key[2] == 'REFUSED')]
        require(len(matches) == 1 and (call['status'] == 'completed' or matches[0][2] == 'REFUSED'),
                'NATIVE_SOURCE_RESULT_NOT_IN_JOURNAL')
        expected[matches[0]] -= 1
    require(not any(expected.values()), 'UNOBSERVED_SOURCE_JOURNAL_RESULT')
    return {'status': 'NATIVE_CALLS_AND_SOURCE_JOURNAL_RECONCILED',
        'read_count': receipt['successful_reads'], 'source_validation': source_summary,
        'refused_count': len(records) - receipt['successful_reads'], 'response_bytes': receipt['response_bytes'],
        'all_raw_rows_read': False, 'causal_feature_use_proven': False}


def finish_source_evidence(prepared, events, request, reply, *, cli_native_exit=None):
    document = prepared['document']
    require(hashlib.sha256(prepared['binding_path'].read_bytes()).hexdigest()
            == prepared['binding_sha256'], 'SOURCE_PHASE_BINDING_FILE_CHANGED')
    _pin(document['bundle_path'], document['bundle_sha256'], max_bytes=536_870_912, load=False)
    _pin(document['server_script'], document['server_sha256'], load=False)
    _pin(document['kernel_source'], document['kernel_sha256'], load=False)
    bundle = prepared['bundle']
    journal = prepared['journal_path']
    require(journal.is_file() and journal.stat().st_size <= 1_048_576,
            'SOURCE_JOURNAL_MISSING_OR_OVERSIZE')
    records = [json.loads(line) for line in journal.read_text('utf-8').splitlines() if line.strip()]
    receipt_path = prepared['receipt_path']
    extra = {}
    if receipt_path.exists():
        require(receipt_path.is_file() and receipt_path.stat().st_size <= 16_384,
                'SOURCE_RECEIPT_MISSING_OR_OVERSIZE')
        receipt = json.loads(receipt_path.read_text('utf-8'))
        version = 'profile-source-host-evidence-v1'
    else:
        require(type(cli_native_exit) is int and cli_native_exit == 0, 'SOURCE_TRUSTED_CLI_ZERO_REQUIRED')
        checkpoint_path = prepared['binding_path'].parent / 'receipt-checkpoints.jsonl'
        require(checkpoint_path.is_file() and 0 < checkpoint_path.stat().st_size <= 262144,
                'SOURCE_CHECKPOINT_FILE_REQUIRED')
        raw_checkpoints = checkpoint_path.read_text('utf-8')
        require(raw_checkpoints.endswith('\n') and all(raw_checkpoints.splitlines()),
                'SOURCE_CHECKPOINT_PARTIAL_RECORD')
        checkpoints = [json.loads(line) for line in raw_checkpoints.splitlines()]
        receipt = checkpoints[-1]['receipt']
        version = 'profile-source-host-evidence-v2'
        extra = {'checkpoint_records': checkpoints, 'lifecycle': {'mode': 'LIVE_CHECKPOINT',
            'cli_native_exit': cli_native_exit, 'server_native_exit': None, 'server_exit_status': 'UNRECORDED'}}
    evidence = {'version': version, 'binding': document['binding'], **extra,
        'request_sha256': sha(request), 'reply_sha256': sha(reply),
        'bundle_sha256': document['bundle_sha256'], 'view_sha256': prepared['bundle_view_sha256'],
        'journal_records': records, 'receipt': receipt, 'native_calls': _native_calls(events),
        'source_boundary': 'OWNED_PINNED_TRANSPORT_NOT_CAUSAL_OR_PHYSICAL_MODEL_AUTHENTICATION'}
    evidence['validation'] = validate_source_evidence(evidence, request=request, reply=reply, bundle=bundle)
    return evidence
