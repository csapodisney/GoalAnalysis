"""First saved independent-pair audit: TESTONLY native events and owned files."""
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_twelve_pair_composition as composition

from goal_analysis.agents.codex_review import _capture_profile_return
from goal_analysis.agents.profile_source_host import SourceAccessReply, finish_source_evidence
from goal_analysis.jobs.agent_snapshot import SharedSnapshot, digest, json_text
from goal_analysis.jobs.portfolio import write_portfolio_bundle
from goal_analysis.jobs.ten_agent_ticket_evidence import (
    _companion_evidence_context,
    build_ten_agent_ticket_evidence,
)
from goal_analysis.jobs.workshop_verify import (
    SavedReader,
    _saved_pair_native_phase,
    verify_new_cohort,
)

RUN = '2026-11-14-TESTONLY-pair-audit'


def _write(path, value):
    Path(path).write_text(json_text(value), encoding='utf-8')


def _saved(tmp_path, monkeypatch):
    """Actual producer and writer; only native host output is an inert fixture."""
    root = tmp_path / 'TESTONLY-root'
    root.mkdir()
    original_inputs = composition._inputs
    original_executor = composition.NormalAgentExecutor

    source_root = Path(__file__).resolve().parents[1] / 'src/goal_analysis/agents'
    sources = {}
    for field, filename in (
        ('server_script', 'profile_source_stdio.py'),
        ('kernel_source', 'profile_source_access.py'),
        ('query_source', 'profile_source_query.py'),
    ):
        target = root / 'src/goal_analysis/agents' / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source_root / filename).read_bytes())
        sources[field] = target

    def inputs():
        return original_inputs(run=RUN)

    def executor(**kwargs):
        kwargs['journal_root'] = root / 'data/arthur/agent-decisions'
        return original_executor(**kwargs)

    def finish(scope, request, reads, answer):
        for field, target in sources.items():
            original = Path(getattr(scope, field))
            assert target.read_bytes() == original.read_bytes()
        prepared = replace(scope, binding=scope.identity(), **sources).prepare(request)
        events, _ = composition._fake_source_files(prepared, reads)
        events.extend([
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json_text(answer)}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 1, 'output_tokens': 1}},
        ])
        stdout = ''.join(json_text(event) + '\n' for event in events)
        _capture_profile_return(prepared, SimpleNamespace(returncode=0, stdout=stdout, stderr=''))
        proof = finish_source_evidence(prepared, events, request, answer)
        return SourceAccessReply.create(answer, proof)

    monkeypatch.setattr(composition, '_inputs', inputs)
    monkeypatch.setattr(composition, 'NormalAgentExecutor', executor)
    monkeypatch.setattr(composition, 'finish_fake_source_reply', finish)
    report, artifacts, calls, _ = composition._execute(tmp_path)
    assert len(calls) == 24 and len(report['generated_tickets']) == 2
    report['report_sha256'] = digest({key: value for key, value in report.items() if key != 'report_sha256'})
    directory = root / 'reports/arthur' / RUN
    write_portfolio_bundle(directory, {'artifacts': {**artifacts, 'report': report}})
    return root, report, artifacts, root / 'data/arthur/agent-decisions' / RUN


def test_pair_actual_contract_and_saved_native_files_bind_twelve_owners_without_raw_duplication(tmp_path, monkeypatch):
    root, report, artifacts, journal = _saved(tmp_path, monkeypatch)
    pure = build_ten_agent_ticket_evidence(report, artifacts)
    assert pure['status'] == 'COMPLETE' and len(pure['owner_checks']) == 12
    assert all(row['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND' for row in pure['owner_checks'].values())
    assert all(row['own_selection']['status'] == 'EXACT_OWN_CANONICAL_SOURCE_BOUND' for row in pure['cards'])
    assert pure['companion_snapshot_binding']['core_snapshot_id'] == report['ten_agent_decisions']['snapshot_id']
    assert pure['companion_snapshot_binding']['snapshot_id'] != pure['companion_snapshot_binding']['core_snapshot_id']
    result = verify_new_cohort(root, report)
    assert result['status'] == 'VERIFIED' and result['gaps'] == [] and len(result['owners']) == 12
    for owner in ('daily_portfolio', 'legacy_daily223'):
        actual = result['owners'][owner]
        assert actual['status'] == 'PHYSICAL_AND_CONTRACT_BOUND' and actual['final_decision_status'] == 'FINAL_DECISION'
        assert set(actual['native_phases']) == {'PLAN', 'FINAL'}
        assert all(row['native_exit_code'] == 0 for row in actual['native_phases'].values())
        assert set(actual['tool_statuses']) == {'candidate_scenarios', 'historical_strategy_evaluation'}
    assert not (journal / 'companion-agents/shared-data.json').exists()
    assert result['new_model_or_tool_calls'] == 0 and result['causal_use_proven'] is False
    assert len([key for key in result['physical_files'] if '/companion-agents/' in key
                and key.endswith('/native-return.json')]) == 4
    assert all(card['status'] == 'DRAFT' and card['playable'] is False for card in report['generated_tickets'])


def test_pair_reference_and_final_proof_tampering_stay_local_and_core_history_is_not_retagged(tmp_path, monkeypatch):
    _, report, artifacts, _ = _saved(tmp_path, monkeypatch)
    core = SharedSnapshot(report['ten_agent_decisions']['snapshot_id'], json_text(artifacts['independent_agent_snapshot']))
    altered = deepcopy(artifacts)
    altered['companion_agent_snapshot']['identity_overrides']['owner_ids'].reverse()
    with pytest.raises(ValueError, match='REFERENCE_CHANGED'):
        _companion_evidence_context(report, altered, core)
    altered = deepcopy(artifacts)
    altered['companion_agent_decisions']['runner_result_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='SAVED_PAIR_RUN_CHANGED'):
        _companion_evidence_context(report, altered, core)
    changed = deepcopy(report)
    owner = changed['companion_agent_decisions']['owners'][0]
    owner['decision_evidence']['source_access']['FINAL']['request_sha256'] = 'f' * 64
    raw_run = {key: value for key, value in changed['companion_agent_decisions'].items()
               if key not in {'runner_result_sha256', 'shared_data_binding', 'journal_subdirectory'}}
    changed['companion_agent_decisions']['runner_result_sha256'] = digest(raw_run)
    altered = {**artifacts, 'companion_agent_decisions': deepcopy(changed['companion_agent_decisions'])}
    audit = build_ten_agent_ticket_evidence(changed, altered)
    assert audit['status'] == 'PARTIAL' and audit['owner_checks']['daily_portfolio']['status'] == 'UNKNOWN'
    assert audit['owner_checks']['legacy_daily223']['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
    historical = {key: deepcopy(value) for key, value in report.items() if not key.startswith('companion_agent_')}
    historical['generated_tickets'] = []
    historical_artifacts = {key: value for key, value in artifacts.items() if not key.startswith('companion_agent_')}
    legacy = build_ten_agent_ticket_evidence(historical, historical_artifacts)
    assert 'companion_snapshot_binding' not in legacy and len(legacy['owner_checks']) == 10
    assert legacy['owner_checks'] == {key: value for key, value in audit['owner_checks'].items()
                                      if key not in {'daily_portfolio', 'legacy_daily223'}}


def test_pair_physical_native_exit_raw_reply_and_missing_tool_cannot_be_accepted(tmp_path, monkeypatch):
    root, report, artifacts, journal = _saved(tmp_path, monkeypatch)
    paired = journal / 'companion-agents'
    owner = report['companion_agent_decisions']['owners'][0]
    purpose = {key: owner[key] for key in ('agent_id', 'session_id', 'snapshot_id')}
    purpose['phase'] = 'FINAL'
    key = digest(purpose)
    claim = json.loads((paired / (key + '.claim.json')).read_text('utf-8'))
    outcome = json.loads((paired / (key + '.outcome.json')).read_text('utf-8'))
    proof = owner['decision_evidence']['source_access']['FINAL']
    phase = paired / 'source-access/p' / key[:32]
    metadata_path = phase / 'native-return.json'
    native = json.loads(metadata_path.read_text('utf-8'))
    _write(metadata_path, {**native, 'native_exit_code': 1})
    with pytest.raises(ValueError, match='COMPLETE_NATIVE_ZERO'):
        _saved_pair_native_phase(SavedReader(root), paired, purpose, claim, outcome, proof)
    _write(metadata_path, native)
    stdout_path = phase / 'native-return.stdout.utf8'
    original_stdout = stdout_path.read_bytes()
    original_native = deepcopy(native)
    events = [json.loads(line) for line in original_stdout.decode('utf-8').splitlines()]
    message = next(row['item'] for row in events if row.get('item', {}).get('type') == 'agent_message')
    reply = json.loads(message['text'])
    reply['reason'] = 'TESTONLY forged raw reply'
    message['text'] = json_text(reply)
    text = ''.join(json_text(event) + '\n' for event in events)
    stdout_path.write_text(text, encoding='utf-8', newline='')
    raw = text.encode('utf-8')
    native['streams']['stdout'].update(returned_bytes=len(raw), captured_bytes=len(raw),
        returned_sha256=hashlib.sha256(raw).hexdigest(), captured_sha256=hashlib.sha256(raw).hexdigest())
    _write(metadata_path, native)
    with pytest.raises(ValueError, match='RAW_REPLY_CHANGED'):
        _saved_pair_native_phase(SavedReader(root), paired, purpose, claim, outcome, proof)
    stdout_path.write_bytes(original_stdout)
    _write(metadata_path, original_native)
    pair = owner['decision_evidence']['tool_results'][0]
    archive = paired / (digest([owner['session_id'], pair['request']['request_id']]) + '.tool-full-result.json')
    archive.unlink()
    result = verify_new_cohort(root, report)
    assert result['status'] == 'PARTIAL' and result['owners']['daily_portfolio']['status'] == 'UNKNOWN'
    assert any(row.get('owner_id') == 'daily_portfolio' for row in result['gaps'])
    assert len(report['generated_tickets']) == 2 and artifacts['companion_agent_decisions'] == report['companion_agent_decisions']
