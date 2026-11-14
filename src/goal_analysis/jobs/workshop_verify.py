"""Bounded saved-cohort verification. No producer, predictor, simulator or provider call.

Physical correspondence is not provider authenticity, causal use or profit.
Old-ledger/index preservation is outside this verifier's declared scope.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.jobs.agent_snapshot import FAMILIES, digest, require
from goal_analysis.jobs.ten_agent_ticket_evidence import build_ten_agent_ticket_evidence
from goal_analysis.storage.training_datasets import DatasetStore

ARTIFACTS = ('independent_agent_snapshot', 'independent_agent_decisions', 'candidate_input',
    'numeric_models', 'candidate_analysis', 'history', 'candidate_context_inputs',
    'candidate_review_actual_packets', 'candidate_review_follow_up_packets',
    'candidate_review_response_batches', 'companion_producer_results')


class SavedVerificationLimit(RuntimeError):
    """A total resource bound is not an optional dataset/owner source gap."""


class SavedReader:
    def __init__(self, root, *, maximum_bytes=None, seconds=180, clock=time.monotonic):
        self.root = Path(root).resolve()
        self.uses_default_byte_limit = maximum_bytes is None
        self.maximum_bytes, self.read_bytes = 805306368 if maximum_bytes is None else maximum_bytes, 0
        self.clock, self.deadline = clock, clock() + seconds
        self.files = {}

    def check(self):
        if self.clock() > self.deadline:
            raise SavedVerificationLimit('SAVED_VERIFICATION_TIME_BOUND')

    def pin(self, path, *, limit=67108864, parse=True):
        self.check()
        path = Path(path)
        resolved = path.resolve()
        require(resolved.is_relative_to(self.root) and not path.is_symlink(), 'SAVED_PATH_OUTSIDE_ROOT')
        size = path.stat().st_size
        if size > limit or self.read_bytes + size > self.maximum_bytes:
            raise SavedVerificationLimit('SAVED_VERIFICATION_BYTE_BOUND')
        self.read_bytes += size
        hasher, chunks = hashlib.sha256(), []
        with path.open('rb') as stream:
            read = 0
            while block := stream.read(1048576):
                self.check()
                read += len(block)
                require(read <= size, 'SAVED_FILE_CHANGED_DURING_READ')
                hasher.update(block)
                if parse:
                    chunks.append(block)
        require(read == size and path.stat().st_size == size, 'SAVED_FILE_CHANGED_DURING_READ')
        self.files[str(resolved.relative_to(self.root)).replace('\\', '/')] = {
            'sha256': hasher.hexdigest(), 'bytes': size}
        self.check()
        if parse == 'text':
            return b''.join(chunks).decode('utf-8')
        return json.loads(b''.join(chunks)) if parse else hasher.hexdigest()


def _dataset_checks(root, models, reader):
    """Exact reference traversal, no whole archive scan and no data writes."""
    checked, gaps, seen = [], [], {}
    for profile, result in models.items():
        if not isinstance(result, dict):
            continue
        competitions = result.get('competition_results')
        groups = competitions.items() if isinstance(competitions, dict) else [(None, result)]
        found = set()
        for competition, value in groups:
            if not isinstance(value, dict):
                continue
            training = value.get('training_datasets', {})
            manifests = training.get('manifests', []) if isinstance(training, dict) else []
            for manifest in manifests:
                identifier = manifest['dataset_id']
                require(identifier not in seen or seen[identifier] == manifest, 'CONFLICTING_DATASET_MANIFEST')
                seen[identifier] = manifest
                found.add(identifier)
                parts = [] if competition is None else [str(competition), profile]
                require(all(Path(part).name == part and part not in {'.', '..'} for part in parts), 'DATASET_SCOPE_PATH_INVALID')
                directory = Path(root) / 'data/arthur/models'
                for part in parts:
                    directory /= part
                store = DatasetStore(directory / 'datasets')
                path = store.path(identifier)
                try:
                    physical = reader.pin(path)
                    # Existing pure full rows/lineage validator; same DatasetStore contract,
                    # with bounded pre-read and no second unbounded physical read.
                    from goal_analysis.normalization.training_datasets import validate
                    validated = validate(physical)
                    require(validated['manifest'] == manifest, 'PHYSICAL_DATASET_MANIFEST_CHANGED')
                    checked.append({'dataset_id': identifier, 'profile': profile,
                        'competition_id': competition, 'rows': len(validated['rows']),
                        'physical_sha256': reader.files[str(path.resolve().relative_to(reader.root)).replace('\\', '/')]['sha256']})
                except (OSError, ValueError, KeyError, TypeError) as error:
                    gaps.append({'reason': 'NAMED_DATASET_NOT_PHYSICALLY_BOUND',
                                 'dataset_id': identifier, 'error_type': type(error).__name__})
        top = result.get('training_datasets', {})
        for manifest in top.get('manifests', []) if isinstance(top, dict) else []:
            if manifest.get('dataset_id') not in found:
                gaps.append({'reason': 'UNRESOLVED_TOP_MODEL_DATASET', 'dataset_id': manifest.get('dataset_id')})
    return checked, gaps



def _v2_saved_source_limits(reader, journal, snapshot_id, *, default_reader):
    """Only the exact compressed v2 contract selects its distinct disk limits."""
    marker = journal / 'source-access-bundle.json'
    if not marker.is_file():
        return None
    receipt = reader.pin(marker, limit=16384)
    if receipt.get('storage_version') != 'SOURCE_BUNDLE_GZIP_FILES_V2':
        return None
    from goal_analysis.agents.profile_source_access import (
        BUNDLE_DECODED_LIMIT,
        BUNDLE_STORED_LIMIT,
        _bundle_parts,
    )

    path = journal / 'source-access/bundle.json'
    manifest = reader.pin(path, limit=16384)
    manifest_pin = reader.files[path.relative_to(reader.root).as_posix()]
    parts = _bundle_parts(manifest, path)
    decoded = sum(part['decoded_bytes'] for _, _, part in parts)
    stored = manifest_pin['bytes'] + sum(part['stored_bytes'] for _, _, part in parts)
    require(receipt['snapshot_id'] == manifest['snapshot_id'] == snapshot_id
        and receipt['view_sha256'] == manifest['view_sha256']
        and receipt['bundle_sha256'] == manifest_pin['sha256']
        and type(receipt['decoded_byte_limit']) is int
        and receipt['decoded_byte_limit'] == BUNDLE_DECODED_LIMIT
        and type(receipt['bundle_byte_limit']) is int
        and receipt['bundle_byte_limit'] == BUNDLE_STORED_LIMIT
        and receipt['decoded_bytes'] == decoded and receipt['bundle_bytes'] == stored
        and receipt['bundle_manifest_bytes'] == manifest_pin['bytes'],
        'EXACT_V2_SAVED_STORAGE_RECEIPT_REQUIRED')
    # This is aggregate streaming disk I/O, distinct from the codec decoded limit.
    # Explicit caller-supplied smaller readers retain their original policy.
    if default_reader:
        reader.maximum_bytes = 3 * 1024**3
    for _, part_path, part in parts:
        require(reader.pin(part_path, limit=BUNDLE_STORED_LIMIT, parse=False)
            == part['stored_sha256'] and part_path.stat().st_size == part['stored_bytes'],
            'SAVED_COMPRESSED_SOURCE_PART_CHANGED')
    return {'manifest_sha256': manifest_pin['sha256'], 'view_sha256': manifest['view_sha256'],
            'raw_bytes': manifest['parts']['raw']['decoded_bytes'],
            'raw_sha256': manifest['parts']['raw']['decoded_sha256'],
            'independent_physical_limit': 2 * 1024**3,
            'decoded_logical_limit': BUNDLE_DECODED_LIMIT}


def _saved_snapshot_pair(reader, journal, data, snapshot_id, storage):
    if storage is None:
        shared = reader.pin(journal / 'shared-data.json', limit=536870912)
        require(data == shared and digest(shared) == snapshot_id, 'PHYSICAL_SHARED_SNAPSHOT_CHANGED')
        return
    # Canonical raw bytes and the fully parsed pretty artifact have the same
    # canonical hash. A second large parsed tree is neither needed nor retained.
    path = journal / 'shared-data.json'
    require(path.stat().st_size == storage['raw_bytes'], 'V2_RAW_SOURCE_SIZE_CHANGED')
    require(reader.pin(path, limit=storage['raw_bytes'], parse=False)
        == storage['raw_sha256'] == snapshot_id and digest(data) == snapshot_id,
        'PHYSICAL_SHARED_SNAPSHOT_CHANGED')


def _saved_source_phase_binding(proof, storage):
    """Bind the saved native-phase envelope to the exact stored v2 manifest."""
    if storage is not None:
        require(proof['bundle_sha256'] == storage['manifest_sha256']
            and proof['view_sha256'] == storage['view_sha256'], 'PHYSICAL_PHASE_SOURCE_STORAGE_CHANGED')


def _saved_pair_native_phase(reader, journal, purpose, claim, outcome, proof):
    """Pair-only complete returned native text, bound to its actual private files."""
    from goal_analysis.agents.profile_source_host import _native_calls, observe_source_events

    phase = journal / 'source-access/p' / digest(purpose)[:32]
    document = reader.pin(phase / 'binding.json', limit=16384)
    binding_pin = reader.files[(phase / 'binding.json').relative_to(reader.root).as_posix()]
    require(document.get('version') == 'profile-source-host-phase-v1'
        and document.get('binding') == purpose and document.get('request_sha256') == claim['request_sha256']
        and document.get('bundle_sha256') == proof['bundle_sha256'], 'PAIR_NATIVE_BINDING_CHANGED')
    paths = {'owned_root': journal / 'source-access', 'phase_root': phase,
        'bundle_path': journal / 'source-access/bundle.json',
        'journal_path': phase / 'reads.jsonl', 'receipt_path': phase / 'receipt.json'}
    require(all(Path(document[key]).resolve() == path.resolve() for key, path in paths.items()),
            'PAIR_NATIVE_OWNED_PATH_CHANGED')
    for field, filename in (('server', 'profile_source_stdio.py'),
                            ('kernel', 'profile_source_access.py'), ('query', 'profile_source_query.py')):
        key = 'server_script' if field == 'server' else field + '_source'
        expected = reader.root / 'src/goal_analysis/agents' / filename
        require(Path(document[key]).resolve() == expected.resolve()
            and reader.pin(expected, parse=False) == document[field + '_sha256'], 'PAIR_NATIVE_SOURCE_PIN_CHANGED')
    native = reader.pin(phase / 'native-return.json', limit=16384)
    require(native.get('version') == 'private-source-native-return-v1'
        and native.get('status') == 'COMPLETE_PRIVATE_CAPTURE'
        and type(native.get('native_exit_code')) is int and native['native_exit_code'] == 0
        and native.get('binding_sha256') == binding_pin['sha256']
        and native.get('byte_limit') == 2 * 1024 * 1024
        and native.get('encoding') == 'UTF8_OF_RETURNED_PROCESS_TEXT_NOT_ORIGINAL_PIPE_BYTES'
        and set(native.get('streams', {})) == {'stdout', 'stderr'}, 'PAIR_COMPLETE_NATIVE_ZERO_REQUIRED')
    streams, total = {}, 0
    for name in ('stdout', 'stderr'):
        path = phase / ('native-return.' + name + '.utf8')
        streams[name] = reader.pin(path, limit=2 * 1024 * 1024, parse='text')
        physical = reader.files[path.relative_to(reader.root).as_posix()]
        saved = native['streams'][name]
        require(saved.get('truncated') is False
            and saved.get('returned_bytes') == saved.get('captured_bytes') == physical['bytes']
            and saved.get('returned_sha256') == saved.get('captured_sha256') == physical['sha256'],
            'PAIR_NATIVE_RETURN_TEXT_CHANGED')
        total += physical['bytes']
    require(total <= 2 * 1024 * 1024, 'PAIR_NATIVE_RETURN_COMBINED_BOUND')
    records = reader.pin(phase / 'reads.jsonl', limit=1048576, parse='text')
    require([json.loads(line) for line in records.splitlines() if line.strip()] == proof['journal_records'],
            'PAIR_PHYSICAL_READ_JOURNAL_CHANGED')
    if proof['version'] == 'profile-source-host-evidence-v1':
        require(reader.pin(phase / 'receipt.json', limit=16384) == proof['receipt'],
                'PAIR_PHYSICAL_RECEIPT_CHANGED')
    else:
        checkpoints = reader.pin(phase / 'receipt-checkpoints.jsonl', limit=262144, parse='text')
        require(checkpoints.endswith('\n') and all(checkpoints.splitlines())
            and [json.loads(line) for line in checkpoints.splitlines()] == proof['checkpoint_records'],
            'PAIR_PHYSICAL_CHECKPOINTS_CHANGED')
    events = observe_source_events(streams['stdout'], {})
    require(_native_calls(events) == proof['native_calls']
        and any(event.get('type') == 'turn.completed' for event in events), 'PAIR_PHYSICAL_NATIVE_CALLS_CHANGED')
    messages = [event['item'].get('text') for event in events
                if event.get('type') == 'item.completed'
                and isinstance(event.get('item'), dict) and event['item'].get('type') == 'agent_message']
    require(messages and isinstance(messages[-1], str), 'PAIR_PHYSICAL_RAW_REPLY_MISSING')
    reply = {key: value for key, value in outcome['reply'].items() if key not in {'host_usage', 'host_evidence'}}
    require(json.loads(messages[-1]) == reply, 'PAIR_PHYSICAL_RAW_REPLY_CHANGED')
    return {'status': 'SAVED_COMPLETE_NATIVE_ZERO_AND_RAW_REPLY_BOUND',
            'native_exit_code': native['native_exit_code'], 'captured_bytes': total}


def _saved_companion_pair(reader, journal, report, artifacts, audit, *, default_reader):
    """Reuse the core's physically checked raw tree; pair evidence is not retagged core proof."""
    from goal_analysis.agents.profile_roster import COMPANION_AGENT_IDS

    paired = journal / 'companion-agents'
    run = report['companion_agent_decisions']
    binding = audit['companion_snapshot_binding']
    require(binding['status'] == 'SAVED_COMPANION_SNAPSHOT_BOUND', 'PAIR_PURE_SHARED_SNAPSHOT_NOT_BOUND')
    require(reader.pin(paired / 'shared-data-reference.json', limit=16384)
        == artifacts['companion_agent_snapshot'], 'PAIR_PHYSICAL_SNAPSHOT_REFERENCE_CHANGED')
    require(reader.pin(paired / 'companion-agent-decisions.json') == run
        == artifacts['companion_agent_decisions'], 'PAIR_PHYSICAL_ENVELOPE_CHANGED')
    raw_run = reader.pin(paired / 'decision-run.json')
    require(raw_run == {key: value for key, value in run.items()
        if key not in {'runner_result_sha256', 'shared_data_binding', 'journal_subdirectory'}}
        and digest(raw_run) == run['runner_result_sha256'], 'PAIR_PHYSICAL_RUNNER_RESULT_CHANGED')
    require(reader.pin(paired / 'run.claim.json', limit=16384)
        == {'snapshot_id': run['snapshot_id'], 'automatic_retry': False}, 'PAIR_PHYSICAL_RUN_CLAIM_CHANGED')
    storage = _v2_saved_source_limits(reader, paired, run['snapshot_id'], default_reader=default_reader)
    reference = artifacts['companion_agent_source_storage']
    require(storage is not None and storage['raw_sha256'] == run['snapshot_id'] == binding['snapshot_id']
        and storage['view_sha256'] == binding['view_sha256'] == reference['view_sha256']
        and storage['manifest_sha256'] == reference['bundle_sha256'], 'PAIR_PHYSICAL_SOURCE_STORAGE_CHANGED')
    owners = run['owners']
    require([owner['agent_id'] for owner in owners] == list(COMPANION_AGENT_IDS), 'EXACT_PAIR_PHYSICAL_OWNERS_REQUIRED')
    checked, gaps = {}, []
    for owner in owners:
        identifier = owner['agent_id']
        reader.check()
        try:
            require(reader.pin(paired / (owner['session_id'] + '.owner.json')) == owner,
                    'PAIR_PHYSICAL_OWNER_OUTCOME_CHANGED')
            evidence = owner['decision_evidence']
            require(audit['owner_checks'][identifier]['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
                and set(evidence['source_access']) == {'PLAN', 'FINAL'}, 'PAIR_BOTH_VERIFIED_SOURCE_PHASES_REQUIRED')
            phases = {}
            for phase in ('PLAN', 'FINAL'):
                purpose = {key: owner[key] for key in ('agent_id', 'session_id', 'snapshot_id')}
                purpose['phase'] = phase
                key = digest(purpose)
                claim = reader.pin(paired / (key + '.claim.json'))
                outcome = reader.pin(paired / (key + '.outcome.json'))
                proof = reader.pin(paired / (key + '.source-access.json'))
                _saved_source_phase_binding(proof, storage)
                require(claim['purpose'] == purpose and claim['claim_key'] == key
                    and digest(claim['request']) == claim['request_sha256'] == proof['request_sha256']
                    and proof == evidence['source_access'][phase]
                    and outcome['status'] == 'HOST_COMPLETED' and outcome['claim_key'] == key
                    and outcome['request_sha256'] == claim['request_sha256']
                    and outcome['reply'] == evidence[phase.lower()], 'PAIR_PHYSICAL_PHASE_JOURNAL_CHANGED')
                phases[phase] = _saved_pair_native_phase(reader, paired, purpose, claim, outcome, proof)
            roles = set()
            for pair in evidence['tool_results']:
                request = pair['request']
                key = digest([owner['session_id'], request['request_id']])
                require(reader.pin(paired / (key + '.tool-full-result.json'))
                    == {**pair, 'raw_result_preserved_before_final': True}, 'PAIR_PHYSICAL_TOOL_RESULT_CHANGED')
                roles.add(request['role'])
            require(roles == {'candidate_scenarios', 'historical_strategy_evaluation'}, 'PAIR_BOTH_PHYSICAL_TOOLS_REQUIRED')
            checked[identifier] = {'status': 'PHYSICAL_AND_CONTRACT_BOUND',
                'final_decision_status': owner['decision']['status'], 'native_phases': phases,
                'tool_statuses': {pair['request']['role']: pair['result']['status'] for pair in evidence['tool_results']}}
        except (OSError, ValueError, KeyError, TypeError) as error:
            checked[identifier] = {'status': 'UNKNOWN', 'reason': 'PAIR_PHYSICAL_EVIDENCE_INCOMPLETE',
                                   'error_type': type(error).__name__}
            gaps.append({'owner_id': identifier, 'reason': 'PAIR_PHYSICAL_EVIDENCE_INCOMPLETE'})
    return checked, gaps, binding


def verify_new_cohort(root, report, *, reader=None):
    """Return explicit partial evidence instead of turning optional UNKNOWN into a veto."""
    root = Path(root).resolve()
    default_reader = reader is None or getattr(reader, 'uses_default_byte_limit', False)
    reader = reader or SavedReader(root)
    run_id = report['run_id']
    require(Path(run_id).name == run_id and run_id.startswith(report['date'] + '-'), 'EXACT_SAVED_RUN_ID_REQUIRED')
    directory = root / 'reports/arthur' / run_id
    journal = root / 'data/arthur/agent-decisions' / run_id
    result = {'version': 'workshop-saved-cohort-verification-v1', 'status': 'PARTIAL',
        'verification_scope': 'NEW_SAVED_COHORT_PHYSICAL_FILES_AND_EXISTING_PURE_CONTRACTS',
        'old_ledger_preservation_verified': False, 'provider_authenticity_proven': False,
        'all_raw_rows_read': False, 'causal_use_proven': False, 'profit_proven': False,
        'new_model_or_tool_calls': 0, 'gaps': [], 'owners': {}, 'datasets': [],
        'optional_family_states': {}, 'run_id': run_id, 'report_sha256': report.get('report_sha256')}
    artifacts = {}
    try:
        require(reader.pin(directory / 'report.json', limit=128 * 1024 * 1024) == report
            and digest({key: value for key, value in report.items() if key != 'report_sha256'}) == report.get('report_sha256'),
            'PHYSICAL_REPORT_OR_EMBEDDED_HASH_CHANGED')
        storage = _v2_saved_source_limits(reader, journal,
            report.get('ten_agent_decisions', {}).get('snapshot_id'), default_reader=default_reader)
        for name in ARTIFACTS:
            path = directory / (name + '.json')
            if path.is_file():
                limit = (storage['independent_physical_limit'] if storage else 536870912)
                artifacts[name] = reader.pin(path, limit=limit if name == 'independent_agent_snapshot'
                    else 128 * 1024 * 1024 if name == 'candidate_input' else 67108864)
        if 'companion_agent_decisions' in report:
            for name in ('companion_agent_snapshot', 'companion_agent_decisions', 'companion_agent_source_storage'):
                artifacts[name] = reader.pin(directory / (name + '.json'))
        data = artifacts['independent_agent_snapshot']
        _saved_snapshot_pair(reader, journal, data, report['ten_agent_decisions']['snapshot_id'], storage)
        require(set(data['families']) == set(FAMILIES), 'ALL_TWELVE_SAVED_FAMILIES_REQUIRED')
        for name, family in data['families'].items():
            require(digest(family.get('value')) == family['value_sha256'], 'PHYSICAL_FAMILY_HASH_CHANGED')
            result['optional_family_states'][name] = {'status': family['status'], 'reason': family.get('reason')}
        for reference, source in data['source_manifest'].items():
            if 'producer_value' in source:
                value = source['producer_value']
            else:
                value = data
                for field in source['snapshot_value_path'].split('/'):
                    value = value[field]
            require(digest(value) == source['canonical_value_sha256'], 'SAVED_SOURCE_VALUE_HASH_CHANGED')
            require(reference == 'normal-value:' + digest([run_id, source['producer_path'], value]), 'SAVED_SOURCE_REFERENCE_CHANGED')
        saved = reader.pin(journal / 'decision-run.json')
        require(saved == artifacts['independent_agent_decisions'], 'PHYSICAL_DECISION_RUN_CHANGED')
        audit = build_ten_agent_ticket_evidence(report, artifacts)
        require(audit['snapshot_binding']['status'] == 'SAVED_SNAPSHOT_BOUND', 'COHORT_SNAPSHOT_NOT_BOUND')
        require(audit['accounting']['canonical_cohort_sha256'] == digest(report['generated_tickets']), 'COHORT_CARDS_CHANGED')
        owners = report['ten_agent_decisions']['owners']
        require(len(owners) == 10 and {owner['agent_id'] for owner in owners} == set(AGENT_IDS), 'EXACT_TEN_SAVED_OWNERS_REQUIRED')
        for owner in owners:
            identifier = owner['agent_id']
            reader.check()
            try:
                require(reader.pin(journal / (owner['session_id'] + '.owner.json')) == owner, 'PHYSICAL_OWNER_OUTCOME_CHANGED')
                evidence = owner['decision_evidence']
                check = audit['owner_checks'][identifier]
                require(check['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND', 'OWNER_PURE_CONTRACT_NOT_BOUND')
                require(set(evidence['source_access']) == {'PLAN', 'FINAL'}, 'BOTH_SOURCE_PHASES_REQUIRED')
                for phase in ('PLAN', 'FINAL'):
                    purpose = {key: owner[key] for key in ('agent_id', 'session_id', 'snapshot_id')}
                    purpose['phase'] = phase
                    key = digest(purpose)
                    claim = reader.pin(journal / (key + '.claim.json'))
                    outcome = reader.pin(journal / (key + '.outcome.json'))
                    proof = reader.pin(journal / (key + '.source-access.json'))
                    _saved_source_phase_binding(proof, storage)
                    require(claim['purpose'] == purpose and claim['claim_key'] == key
                        and digest(claim['request']) == claim['request_sha256'] == proof['request_sha256']
                        and proof == evidence['source_access'][phase]
                        and outcome['status'] == 'HOST_COMPLETED' and outcome['claim_key'] == key
                        and outcome['request_sha256'] == claim['request_sha256']
                        and outcome['reply'] == evidence[phase.lower()], 'PHYSICAL_PHASE_JOURNAL_CHANGED')
                roles = set()
                for pair in evidence['tool_results']:
                    request = pair['request']
                    key = digest([owner['session_id'], request['request_id']])
                    require(reader.pin(journal / (key + '.tool-full-result.json')) == {**pair, 'raw_result_preserved_before_final': True}, 'PHYSICAL_TOOL_RESULT_CHANGED')
                    roles.add(request['role'])
                require(roles == {'candidate_scenarios', 'historical_strategy_evaluation'}, 'BOTH_PHYSICAL_SIMULATOR_ROLES_REQUIRED')
                result['owners'][identifier] = {'status': 'PHYSICAL_AND_CONTRACT_BOUND',
                    'final_decision_status': owner['decision']['status'],
                    'tool_statuses': {pair['request']['role']: pair['result']['status'] for pair in evidence['tool_results']}}
            except (OSError, ValueError, KeyError, TypeError) as error:
                result['owners'][identifier] = {'status': 'UNKNOWN', 'reason': 'OWN_PHYSICAL_EVIDENCE_INCOMPLETE',
                    'error_type': type(error).__name__}
                result['gaps'].append({'owner_id': identifier, 'reason': 'OWN_PHYSICAL_EVIDENCE_INCOMPLETE'})
        if 'companion_agent_decisions' in report:
            pair_owners, pair_gaps, pair_binding = _saved_companion_pair(
                reader, journal, report, artifacts, audit, default_reader=default_reader)
            result['owners'].update(pair_owners)
            result['gaps'].extend(pair_gaps)
            result['companion_source_binding'] = pair_binding
        else:
            for channel in report['companion_ticket_channels']['channels']:
                identifier = channel['channel_id']
                require(identifier in {'daily_portfolio', 'legacy_daily223'}, 'EXACT_COMPANION_JOURNAL_NAME')
                require(reader.pin(journal / (identifier + '.companion-claim.json')) == channel['claim']
                    and reader.pin(journal / (identifier + '.companion-result.json')) == channel,
                    'PHYSICAL_COMPANION_JOURNAL_CHANGED')
                odds = data['families']['odds_and_price_history']['value']
                inputs = {'candidate_input': artifacts['candidate_input'], 'history': artifacts.get('history', {}),
                          'enriched': odds['enriched'], 'settings': report['settings']}
                require(channel['input_hashes'] == {key: digest(value) for key, value in inputs.items()},
                        'COMPANION_PHYSICAL_INPUT_BINDING_CHANGED')
        result['datasets'], dataset_gaps = _dataset_checks(root, artifacts.get('numeric_models', {}), reader)
        result['gaps'].extend(dataset_gaps)
        for card in audit['cards']:
            # Companion-specific pure binding is performed by the caller's canonical check.
            original = report['generated_tickets'][card['location'][1]]
            if card.get('own_selection', {}).get('status') == 'UNKNOWN':
                result['gaps'].append({'ticket_id': original['ticket_id'], 'reason': 'OWN_CANONICAL_CARD_NOT_BOUND'})
        result['source_binding'] = audit['snapshot_binding']
    except SavedVerificationLimit as error:
        result['gaps'].append({'reason': str(error), 'error_type': type(error).__name__,
                               'verification_aborted': True})
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['gaps'].append({'reason': 'SAVED_COHORT_EVIDENCE_INCOMPLETE', 'error_type': type(error).__name__})
    try:
        reader.check()
    except SavedVerificationLimit as error:
        result['gaps'].append({'reason': str(error), 'error_type': type(error).__name__,
                               'verification_aborted': True})
    result['physical_files'] = reader.files
    result['physical_bytes_read'] = reader.read_bytes
    expected_owner_count = 12 if 'companion_agent_decisions' in report else 10
    result['status'] = 'VERIFIED' if not result['gaps'] and len(result['owners']) == expected_owner_count else 'PARTIAL'
    return result
