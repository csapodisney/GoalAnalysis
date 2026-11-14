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
    def __init__(self, root, *, maximum_bytes=805306368, seconds=180, clock=time.monotonic):
        self.root = Path(root).resolve()
        self.maximum_bytes, self.read_bytes = maximum_bytes, 0
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


def verify_new_cohort(root, report, *, reader=None):
    """Return explicit partial evidence instead of turning optional UNKNOWN into a veto."""
    root = Path(root).resolve()
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
        require(reader.pin(directory / 'report.json') == report
            and digest({key: value for key, value in report.items() if key != 'report_sha256'}) == report.get('report_sha256'),
            'PHYSICAL_REPORT_OR_EMBEDDED_HASH_CHANGED')
        for name in ARTIFACTS:
            path = directory / (name + '.json')
            if path.is_file():
                artifacts[name] = reader.pin(path, limit=536870912 if name == 'independent_agent_snapshot' else 67108864)
        data = artifacts['independent_agent_snapshot']
        shared = reader.pin(journal / 'shared-data.json', limit=536870912)
        require(data == shared and digest(shared) == report['ten_agent_decisions']['snapshot_id'], 'PHYSICAL_SHARED_SNAPSHOT_CHANGED')
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
    result['status'] = 'VERIFIED' if not result['gaps'] and len(result['owners']) == 10 else 'PARTIAL'
    return result
