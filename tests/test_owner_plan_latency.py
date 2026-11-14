"""New TESTONLY phase bounds and private timing; no process/provider/model/DB."""
import hashlib
import io
import json
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_profile_source_host import SourceCliFixture, make_source_phase
from test_ten_agent_runner import _runner

from goal_analysis.agents import codex_review, profile_decision_runner
from goal_analysis.agents.astra_review import _ReviewError
from goal_analysis.agents.profile_decision_runner import (
    DecisionBudget,
    DecisionJournal,
    ProfileDecisionRunner,
)
from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.agents.profile_source_host import (
    SourceAccessScope,
    canonical,
    source_timing_observation,
)

NOW = datetime(2026, 11, 14, 13, tzinfo=UTC)


def _state(owner='kronikas'):
    return SimpleNamespace(agent_id=owner, session_id='TESTONLY-' + owner,
                           snapshot_id='TESTONLY-snapshot', transcript=())


def _journal(tmp_path):
    snapshot = SimpleNamespace(snapshot_id='TESTONLY-snapshot',
                               data=lambda: {'run_id': 'TESTONLY-latency'})
    return DecisionJournal(tmp_path, snapshot)


def _prepared(tmp_path, *, checkpoints=True):
    tmp_path.mkdir(parents=True)
    identity = {'agent_id': 'kronikas', 'session_id': 'TESTONLY-session',
                'snapshot_id': 'TESTONLY-snapshot', 'phase': 'PLAN'}
    binding = tmp_path / 'binding.json'
    binding.write_text(canonical(identity), encoding='utf-8')
    prepared = {'binding_path': binding,
                'binding_sha256': hashlib.sha256(binding.read_bytes()).hexdigest(),
                'document': {'binding': identity}}
    if checkpoints:
        records = []
        for sequence, seconds, count in [(1, 30, 0), (2, 40, 1), (3, 200, 2), (4, 210, 2)]:
            records.append({'stage': 'KERNEL_READY' if sequence == 1 else 'BEFORE_WIRE_FLUSH',
                            'observed_at': (NOW + timedelta(seconds=seconds)).isoformat(),
                            'receipt': {'binding': identity, 'journal_count': count}})
        (tmp_path / 'receipt-checkpoints.jsonl').write_text(
            ''.join(canonical(row) + '\n' for row in records), encoding='utf-8')
    return prepared


def test_source_plan_cap_is_600_final_and_legacy_stay_300_and_remaining_clamps():
    budget = DecisionBudget(4, NOW + timedelta(seconds=4800), clock=lambda: NOW)
    assert budget.reserve(phase='PLAN', source_access=True) == 600
    assert budget.reserve(phase='FINAL', source_access=True) == 300
    assert budget.reserve(phase='PLAN', source_access=False) == 300
    assert budget.reserve() == 300
    with pytest.raises(ValueError, match='DECISION_BUDGET_EXHAUSTED'):
        budget.reserve(phase='PLAN', source_access=True)
    assert budget.used_requests == 4
    shorter = DecisionBudget(2, NOW + timedelta(seconds=121), clock=lambda: NOW)
    with pytest.raises(ValueError, match='EXACT_PROFILE_TIMEOUT_SCOPE'):
        shorter.reserve(phase='OTHER', source_access=True)
    assert shorter.used_requests == 0
    assert shorter.reserve(phase='PLAN', source_access=True) == 121
    assert shorter.reserve(phase='FINAL', source_access=True) == 121


def test_actual_runner_passes_phase_cap_once_and_preserves_failed_claim(tmp_path):
    calls = []

    def host(request, *, timeout):
        calls.append((request['phase'], timeout))
        raise RuntimeError('TESTONLY stopped at inert host boundary')

    journal = _journal(tmp_path / 'journal')
    runner = ProfileDecisionRunner(host=host, tools=object(), option_builder=lambda *a: None,
        journal=journal, budget=DecisionBudget(20, NOW + timedelta(seconds=4800), clock=lambda: NOW),
        source_bundle=object())
    for phase in ('PLAN', 'FINAL'):
        with pytest.raises(RuntimeError, match='TESTONLY stopped'):
            runner._call(_state(), phase, {}, source_records={})
    assert calls == [('PLAN', 600), ('FINAL', 300)]
    assert runner.budget.used_requests == 2
    outcomes = [json.loads(path.read_text('utf-8')) for path in journal.directory.glob('*.outcome.json')]
    assert len(outcomes) == 2 and all(row['status'] == 'FAILED_NO_RETRY' for row in outcomes)
    with pytest.raises(FileExistsError):
        runner._call(_state(), 'PLAN', {}, source_records={})
    assert len(calls) == 2


def test_expired_queued_owners_keep_all_identities_without_builder_or_host(tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('Expired queued owner must not enter strategy, tools, or host')

    journal = _journal(tmp_path / 'journal')
    budget = DecisionBudget(20, NOW, clock=lambda: NOW, skip_expired_owners=True)
    runner = ProfileDecisionRunner(host=forbidden, tools=object(), option_builder=forbidden,
                                   journal=journal, budget=budget)
    rows = [runner._owner(_state(owner), None, None) for owner in AGENT_IDS]
    assert len(rows) == 10 and {row['agent_id'] for row in rows} == set(AGENT_IDS)
    assert all(row['status'] == 'NOT_STARTED_DEADLINE' and row['host_requests_started'] == 0
               and row['observed_host_usage'] == [] and 'decision' not in row for row in rows)
    assert len(list(journal.directory.glob('*.owner.json'))) == 10
    assert not list(journal.directory.glob('*.claim.json')) and budget.used_requests == 0
    assert DecisionBudget(20, NOW, clock=lambda: NOW).owner_stop_reason() is None
    stopped = DecisionBudget(20, NOW + timedelta(seconds=60), clock=lambda: NOW,
                             stop_requested=lambda: True, skip_expired_owners=True)
    assert stopped.owner_stop_reason() == 'HUMAN_OR_ACCOUNT_STOP_BEFORE_OWNER_START'


def test_actual_public_host_source_plan_reaches_600_process_cap_final_remains_300(tmp_path):
    for phase, cap in [('PLAN', 600), ('FINAL', 300)]:
        folder = tmp_path / phase
        folder.mkdir()
        scope, request, _ = make_source_phase(folder, phase=phase)
        cli = SourceCliFixture()
        reviewer = codex_review.CodexReviewer(command=['TESTONLY-NO-NATIVE'], runner=cli)
        answer = reviewer.request_profile_decision(request, timeout=cap, source_access=scope)
        calls = [row for row in cli.calls if 'exec' in row[0]]
        assert len(calls) == 1 and cap - 10 < calls[0][3] <= cap
        assert answer.evidence()['validation']['status'] == 'NATIVE_CALLS_AND_SOURCE_JOURNAL_RECONCILED'
        assert answer.evidence()['validation']['read_count'] == 1
        capture = next(scope.owned_root.glob('p/*/native-return.json'))
        timing = json.loads(capture.read_text('utf-8'))['timing']
        assert timing['timer_origin'] == 'HOST_RUNNER_INVOCATION'
        assert timing['native_elapsed_seconds'] >= 0


def test_completed_selected_decisions_survive_expiry_of_remaining_owners(tmp_path, monkeypatch):
    clock = [NOW]
    budget = DecisionBudget(20, NOW + timedelta(seconds=60), clock=lambda: clock[0],
                            skip_expired_owners=True)
    snapshot, host, tools, journal, runner = _runner(tmp_path, budget=budget)

    class SerialExecutor:
        def __init__(self, *, max_workers):
            assert max_workers == 2

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def map(self, function, states):
            for index, state in enumerate(states):
                outcome = function(state)
                if index == 0:
                    clock[0] = NOW + timedelta(seconds=61)
                yield outcome

    monkeypatch.setattr(profile_decision_runner, 'ThreadPoolExecutor', SerialExecutor)
    result = runner.run(snapshot)
    assert result['status'] == 'PARTIAL' and result['agent_count'] == 10
    assert result['owners'][0]['status'] == 'DECISION_COMPLETED'
    assert len(result['owners'][0]['decision']['selected_options']) == 2
    assert all(row['status'] == 'NOT_STARTED_DEADLINE' for row in result['owners'][1:])
    assert len(result['distinct_decisions']) == 2 and result['host_requests_reserved'] == 2
    assert len(host.calls) == 2 and len(tools.calls) == 2
    assert all(row['publication_status'] == 'REQUIRES_CANONICAL_PRODUCT_VALIDATION'
               for row in result['distinct_decisions'])
    assert 'unattempted_agent_ids' not in result
    assert len(list(journal.directory.glob('*.owner.json'))) == 10
    assert json.loads((journal.directory / 'decision-run.json').read_text('utf-8')) == result


def test_invalid_phase_caps_and_source_preparation_never_start_an_extra_host(tmp_path, monkeypatch):
    scope, request, _ = make_source_phase(tmp_path)
    calls = []
    reviewer = codex_review.CodexReviewer(command=['TESTONLY-NO-NATIVE'],
                                        runner=lambda *a, **k: calls.append((a, k)))
    for phase, seconds, capability in [('PLAN', 601, scope), ('FINAL', 301, scope), ('PLAN', 301, None)]:
        with pytest.raises(_ReviewError, match='Invalid bounded profile request'):
            reviewer.request_profile_decision({**request, 'phase': phase}, timeout=seconds,
                                               source_access=capability)
    ticks = iter((0.0, 601.0))
    monkeypatch.setattr(codex_review.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(SourceAccessScope, 'prepare', lambda self, body: {})
    with pytest.raises(_ReviewError, match='deadline reached during source preparation') as error:
        reviewer.request_profile_decision(request, timeout=600, source_access=scope)
    assert calls == [] and error.value.host_usage['requests'] == 0


def test_native_return_timing_preserves_private_output_before_acceptance(tmp_path):
    prepared = _prepared(tmp_path / 'phase')
    done = subprocess.CompletedProcess(['TESTONLY'], 0, 'TESTONLY output', 'TESTONLY stderr')
    record = codex_review._capture_profile_return(prepared, done, started_at=NOW.isoformat(),
        observed_at=(NOW + timedelta(seconds=275)).isoformat(), elapsed_seconds=274.81)
    timing = record['timing']
    assert timing['status'] == 'OBSERVED' and timing['start_to_kernel_ready_seconds'] == 30
    assert timing['last_source_read_to_return_seconds'] == 75
    assert timing['native_elapsed_seconds'] == 274.81 and timing['journal_count_observed'] == 2
    assert timing['evidence_authority'] == 'DIAGNOSTIC_ONLY_BEFORE_SOURCE_PROOF_VALIDATION'
    assert record['native_exit_code'] == 0 and record['status'] == 'COMPLETE_PRIVATE_CAPTURE'
    assert (tmp_path / 'phase/native-return.stdout.utf8').read_text('utf-8') == done.stdout
    assert not (tmp_path / 'phase/receipt.json').exists()


def test_interrupted_native_timing_keeps_unknown_completion_and_native_code(tmp_path):
    prepared = _prepared(tmp_path / 'phase')
    interruption = {'original_exception_type': 'TimeoutExpired', 'started_at': NOW.isoformat(),
        'observed_at': (NOW + timedelta(seconds=600)).isoformat(), 'elapsed_seconds': 599.9,
        'timeout_seconds': 600, 'child_pid': 123, 'native_exit_code': None,
        'termination': {'wait_completed': False}}
    codex_review._capture_profile_interruption(prepared, io.BytesIO(b'TESTONLY partial'),
                                               io.BytesIO(b''), interruption=interruption)
    record = json.loads((tmp_path / 'phase/native-interruption.json').read_text('utf-8'))
    assert record['timing']['start_to_kernel_ready_seconds'] == 30
    assert record['timing']['last_source_read_to_return_seconds'] == 400
    assert record['timing']['timer_origin'] == 'NATIVE_PROCESS_START'
    assert record['native_exit_code'] is None and record['response_accepted'] is False
    assert record['host_completion_proven'] is False and record['status'] == 'INTERRUPTED_NO_ACCEPTANCE'
    assert not (tmp_path / 'phase/native-return.json').exists()


def test_missing_malformed_or_foreign_timing_stays_unknown_without_fake_zero(tmp_path):
    for fault in ('missing', 'malformed', 'foreign', 'future'):
        prepared = _prepared(tmp_path / fault, checkpoints=fault != 'missing')
        path = prepared['binding_path'].parent / 'receipt-checkpoints.jsonl'
        if fault == 'malformed':
            path.write_text('not-json\n', encoding='utf-8')
        elif fault in {'foreign', 'future'}:
            rows = [json.loads(line) for line in path.read_text('utf-8').splitlines()]
            if fault == 'foreign':
                rows[-1]['receipt']['binding']['agent_id'] = 'FOREIGN'
            else:
                rows[-1]['observed_at'] = (NOW + timedelta(seconds=700)).isoformat()
            path.write_text(''.join(canonical(row) + '\n' for row in rows), encoding='utf-8')
        observed = source_timing_observation(prepared, started_at=NOW.isoformat(),
            observed_at=(NOW + timedelta(seconds=600)).isoformat(), elapsed_seconds=599.9,
            timer_origin='TESTONLY')
        assert observed['status'] == 'UNKNOWN' and observed['reason']
        assert observed['start_to_kernel_ready_seconds'] is None
        assert observed['last_source_read_to_return_seconds'] is None
        assert observed['native_elapsed_seconds'] == 599.9
