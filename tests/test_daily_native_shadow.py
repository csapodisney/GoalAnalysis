"""Post-freeze durability, hard worker boundary and read-only source contracts."""
import json
import socket
import sqlite3
import subprocess
import sys
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from time import monotonic
from unittest.mock import Mock

import pytest
from test_nightly import setup_workflow

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import daily_native_shadow as module
from goal_analysis.jobs.nightly import NightlyWorkflow
from goal_analysis.storage import Database

NOW = datetime.now(UTC)-timedelta(seconds=5)


def staged(root):
    run_id = 'nightly-native-test'
    report = {'run_id': run_id, 'date': NOW.date().isoformat(), 'recommendations': []}
    folder = root/'data/arthur/nightly-staging'
    folder.mkdir(parents=True)
    path = folder/f'{run_id}.json'
    path.write_text(json.dumps({'artifacts': {'report': report,
        'fixtures': {'records': []}, 'candidate_input': {'candidates': []}}}), 'utf-8')
    database = Database(root/'data/goal_analysis.sqlite3'); database.initialize()
    return run_id, report, path, database


def activate(root):
    folder = root/'config'; folder.mkdir(exist_ok=True)
    (folder/'native-shadow.json').write_text(json.dumps(module.POLICY), 'utf-8')


def job(report):
    return {'deadline': (NOW+timedelta(hours=2)).isoformat(), 'settings': {'enabled_profiles': []},
        'stages': [{'name': 'archive', 'status': 'SUCCESS'},
                   {'name': 'generation', 'status': 'SUCCESS', 'run_id': report['run_id']}]}


def test_native_observer_runs_once_after_freeze_before_learning_and_never_rewrites_report(tmp_path, monkeypatch):
    workflow, _clock, calls = setup_workflow(tmp_path)
    activate(tmp_path)
    observed = []

    def sidecar(root, run_id, report_hash, settings, **kwargs):
        state = workflow.store.get('2026-10-08')
        assert state['native_shadow']['status'] == 'STARTED'
        assert workflow.ledger.list_freezes()[0]['run_id'] == run_id
        report = workflow._saved_report(run_id)
        assert canonical_sha256(report) == report_hash
        observed.append(deepcopy(report))
        assert 'learning' not in calls
        return {'status': 'OBSERVED', 'mode': 'SHADOW', 'external_requests': 0}

    monkeypatch.setattr(module, 'run', sidecar)
    result = workflow.run()
    assert result['job']['native_shadow']['status'] == 'OBSERVED'
    assert len(observed) == 1 and calls == ['settlement', 'generation', 'learning']
    assert workflow._saved_report(observed[0]['run_id']) == observed[0]
    assert workflow.run()['status'] == 'ALREADY_FINISHED' and len(observed) == 1


@pytest.mark.parametrize('existing', ['STARTED', 'TIMEOUT', 'UNAVAILABLE', 'OBSERVED', 'GAPS'])
def test_any_durable_claim_prevents_automatic_repeat(tmp_path, monkeypatch, existing):
    activate(tmp_path)
    workflow = NightlyWorkflow(tmp_path, clock=lambda: NOW)
    state = {'native_shadow': {'status': existing}}
    runner = Mock(); monkeypatch.setattr(module, 'run', runner)
    assert workflow._daily_native_shadow(state) == {'status': existing}
    runner.assert_not_called()


@pytest.mark.parametrize('change,expected', [
    ({'archive': 'PARTIAL'}, 'SKIPPED_DAILY_INCOMPLETE'),
    ({'remaining': 44}, 'SKIPPED_NO_SPARE_WINDOW'),
    ({'report': None}, 'SKIPPED_NO_SAVED_REPORT')])
def test_missing_freeze_or_time_or_report_never_spends_work(tmp_path, monkeypatch, change, expected):
    _, report, _, _ = staged(tmp_path); activate(tmp_path)
    workflow = NightlyWorkflow(tmp_path, clock=lambda: NOW)
    monkeypatch.setattr(workflow, '_saved_report', lambda _: change.get('report', report))
    runner = Mock(); monkeypatch.setattr(module, 'run', runner)
    state = job(report)
    if 'archive' in change: state['stages'][0]['status'] = change['archive']
    if 'remaining' in change: state['deadline'] = (NOW+timedelta(seconds=change['remaining'])).isoformat()
    assert workflow._daily_native_shadow(state)['status'] == expected
    runner.assert_not_called()
    assert 'native_shadow' not in state


def test_failure_and_persistence_failure_are_isolated_and_sensitive_error_not_exposed(tmp_path, monkeypatch):
    _, report, _, _ = staged(tmp_path); activate(tmp_path)
    workflow = NightlyWorkflow(tmp_path, clock=lambda: NOW)
    monkeypatch.setattr(workflow, '_saved_report', lambda _: report)
    monkeypatch.setattr(workflow, '_save', Mock(side_effect=OSError('sensitive details')))
    runner = Mock(); monkeypatch.setattr(module, 'run', runner)
    result = workflow._daily_native_shadow(job(report))
    assert result['status'] == 'UNAVAILABLE' and result['persistence_status'] == 'UNAVAILABLE'
    assert 'sensitive' not in json.dumps(result)
    runner.assert_not_called()


def test_worker_failure_cannot_change_nightly_publication_or_learning(tmp_path, monkeypatch):
    workflow, _, calls = setup_workflow(tmp_path)
    activate(tmp_path)
    monkeypatch.setattr(module, 'run', Mock(side_effect=ValueError('sensitive provider detail')))
    result = workflow.run()
    assert result['status'] == 'COMPLETED' and calls[-1] == 'learning'
    assert result['job']['native_shadow']['reason'] == 'DAILY_NATIVE_SHADOW_FAILED'
    assert len(workflow.ledger.list_freezes()) == 1
    assert 'sensitive' not in json.dumps(result)


@pytest.mark.parametrize('policy', [None, {'enabled': True, 'mode': 'LIVE', 'max_seconds': 180},
    {'enabled': True, 'mode': 'SHADOW', 'max_seconds': 181}])
def test_disabled_or_wrong_policy_does_not_enable_worker(tmp_path, policy):
    if policy is not None:
        activate(tmp_path)
        (tmp_path/'config/native-shadow.json').write_text(json.dumps(policy), 'utf-8')
    assert module.enabled(tmp_path) is False


def test_hard_subprocess_timeout_and_secret_free_environment(tmp_path, monkeypatch):
    run_id, report, _, _ = staged(tmp_path)
    monkeypatch.setenv('THE_STATS_API_KEY', 'synthetic-secret')
    monkeypatch.setenv('XAI_API_KEY', 'synthetic-secret')
    observed = []

    def execute(command, **kwargs):
        assert 0 < kwargs['timeout'] <= 2 and kwargs['stdin'] == subprocess.DEVNULL
        assert kwargs['stderr'] == kwargs['stdout'] == subprocess.DEVNULL
        assert 'THE_STATS_API_KEY' not in kwargs['env'] and 'XAI_API_KEY' not in kwargs['env']
        assert kwargs['env']['OMP_NUM_THREADS'] == '1'
        assert command[0] == getattr(sys, '_base_executable', sys.executable)
        assert '--report-hash' in command and 'synthetic-secret' not in str(command)
        observed.append(command)
        raise subprocess.TimeoutExpired(command, 2)

    monkeypatch.setattr(subprocess, 'run', execute)
    assert module.run(tmp_path, run_id, canonical_sha256(report), {'enabled_profiles': []},
                      as_of=NOW, max_seconds=2)['status'] == 'TIMEOUT'
    assert len(observed) == 1
    with pytest.raises(FileExistsError):
        module.run(tmp_path, run_id, canonical_sha256(report), {'enabled_profiles': []}, as_of=NOW)


@pytest.mark.parametrize('budget', [0, 181, True, '180'])
def test_invalid_budget_or_report_hash_or_path_rejected_before_process(tmp_path, monkeypatch, budget):
    run_id, report, _, _ = staged(tmp_path)
    runner = Mock(); monkeypatch.setattr(subprocess, 'run', runner)
    with pytest.raises(ValueError):
        module.run(tmp_path, run_id, canonical_sha256(report), {}, as_of=NOW, max_seconds=budget)
    with pytest.raises(ValueError):
        module.run(tmp_path, '../elsewhere', canonical_sha256(report), {}, as_of=NOW)
    with pytest.raises(ValueError, match='REPORT_HASH_MISMATCH'):
        module.run(tmp_path, run_id, 'a'*64, {}, as_of=NOW)
    runner.assert_not_called()


def test_actual_child_process_on_empty_inputs_is_gap_not_false_ready(tmp_path):
    run_id, report, staging, database = staged(tmp_path)
    original = staging.read_bytes()
    with closing(database.connect()) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    before = database.path.read_bytes()
    result = module.run(tmp_path, run_id, canonical_sha256(report), {'enabled_profiles': []},
                         as_of=NOW, max_seconds=30)
    assert result['status'] == 'GAPS' and result['eligible_options_at_completion'] == 0
    assert result['external_requests'] == result['llm_requests'] == result['ledger_writes'] == 0
    assert staging.read_bytes() == original and database.path.read_bytes() == before
    analysis = json.loads((tmp_path/result['artifact']).read_text('utf-8'))
    assert analysis['options_shadow']['summary']['complete_in_band_options'] == 0
    assert analysis['options_shadow']['options'] == {}
    assert analysis['options_shadow']['cup_review']['reason'] == 'MISSING_NATIVE_INPUT_DATASET'
    assert result['cup_completion_check']['eligible_at_completion'] is False
    assert analysis['options_shadow']['xg_review']['reason'] == 'MISSING_NATIVE_INPUT_DATASET'
    assert result['xg_completion_check']['eligible_at_completion'] is False
    assert analysis['options_shadow']['motivation_review']['reason'] == 'MISSING_NATIVE_INPUT_DATASET'
    assert result['motivation_completion_check']['publication_eligible'] is False


def test_actual_direct_child_timeout_is_bounded(tmp_path, monkeypatch):
    run_id, report, _, _ = staged(tmp_path)
    original = subprocess.run

    def blocking(command, **kwargs):
        # A single real direct interpreter, without a venv launcher/child tree.
        return original([command[0], '-c', 'import time; time.sleep(15)'], **kwargs)

    monkeypatch.setattr(subprocess, 'run', blocking)
    started = monotonic()
    result = module.run(tmp_path, run_id, canonical_sha256(report), {'enabled_profiles': []},
                        as_of=NOW, max_seconds=1)
    assert result['status'] == 'TIMEOUT' and monotonic()-started < 5


def test_actual_nightly_hook_accepts_immutable_ledger_and_staging_hash(tmp_path):
    workflow, _, _ = setup_workflow(tmp_path)
    activate(tmp_path)
    result = workflow.run()
    assert result['status'] == 'COMPLETED'
    assert result['job']['native_shadow']['status'] == 'GAPS'
    assert result['job']['native_shadow']['eligible_options_at_completion'] == 0
    assert len(workflow.ledger.list_freezes()) == 1


def test_oversized_native_input_rejected_before_process(tmp_path, monkeypatch):
    run_id, report, path, _ = staged(tmp_path)
    bundle = json.loads(path.read_text('utf-8'))
    bundle['artifacts']['fixtures']['records'] = [{}]*1001
    path.write_text(json.dumps(bundle), 'utf-8')
    runner = Mock(); monkeypatch.setattr(subprocess, 'run', runner)
    with pytest.raises(ValueError, match='BOUNDED_DAILY_NATIVE'):
        module.run(tmp_path, run_id, canonical_sha256(report), {}, as_of=NOW)
    runner.assert_not_called()


def test_worker_output_cannot_lie_about_report_or_analysis_hash(tmp_path, monkeypatch):
    run_id, report, _, _ = staged(tmp_path)

    def altered(command, **kwargs):
        output = tmp_path/'data/arthur/native-shadow'/run_id
        (output/'analysis.json').write_text('{}', 'utf-8')
        (output/'summary.json').write_text(json.dumps({'analysis_sha256': 'a'*64}), 'utf-8')
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, 'run', altered)
    with pytest.raises(ValueError, match='RESULT_MISMATCH'):
        module.run(tmp_path, run_id, canonical_sha256(report), {}, as_of=NOW)


def test_worker_calls_native_fit_once_forces_database_read_only_and_blocks_external_io(tmp_path, monkeypatch):
    from goal_analysis.jobs import native_options_shadow

    run_id, report, staging, database = staged(tmp_path)
    output = tmp_path/'data/arthur/native-shadow'/run_id; output.mkdir(parents=True)
    settings_path = output/'settings.json'
    settings_path.write_text(json.dumps({'enabled_profiles': ['gauss', 'laplace']}), 'utf-8')
    shadow_path = tmp_path/'data/arthur/prospective-odds/shadow-monitor.sqlite3'
    shadow_path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(shadow_path)) as db:
        db.execute('CREATE TABLE monitor_snapshots (snapshot_id TEXT)')
        db.commit()
    called = []

    def chain(root, report_input, enriched, settings, **kwargs):
        assert report_input == report and enriched['fixtures'] == []
        assert kwargs['profiles'] == ('gauss', 'laplace')
        with closing(sqlite3.connect(database.path)) as db, \
                pytest.raises(sqlite3.OperationalError, match='readonly'):
            db.execute('CREATE TABLE forbidden(x)')
        with pytest.raises(ValueError, match='UNEXPECTED_SHADOW_DATABASE'):
            sqlite3.connect(tmp_path/'other.sqlite3')
        with pytest.raises(ValueError, match='UNEXPECTED_SHADOW_DATABASE'):
            sqlite3.connect(tmp_path/'data/arthur/odds-monitor.sqlite3')
        with closing(sqlite3.connect(shadow_path)) as db:
            assert db.execute('select count(*) from monitor_snapshots').fetchone()[0] == 0
            with pytest.raises(sqlite3.OperationalError, match='readonly'):
                db.execute('INSERT INTO monitor_snapshots VALUES (\'forbidden\')')
        with pytest.raises(RuntimeError, match='EXTERNAL_IO_FORBIDDEN'):
            socket.socket()
        with pytest.raises(RuntimeError, match='EXTERNAL_IO_FORBIDDEN'):
            subprocess.Popen(['forbidden'])
        called.append(1)
        return {'source_view': {'status': 'GAPS', 'gaps': {'MISSING_HISTORY': 1}},
            'options_shadow': {'option_source_bindings': [], 'options': {}, 'summary': {}, 'gaps': {}}}

    monkeypatch.setattr(native_options_shadow, 'replay_native_options', chain)
    result = module.worker(tmp_path, run_id, canonical_sha256(report), NOW, settings_path)
    assert len(called) == 1 and result['source_gaps'] == {'MISSING_HISTORY': 1}
    assert json.loads(staging.read_text('utf-8'))['artifacts']['report'] == report


@pytest.mark.parametrize('minutes,age,fresh', [(15, 30, False), (16, 30, True), (16, 30.01, False)])
def test_completion_rechecks_lockout_and_receipt_age(minutes, age, fresh):
    card = {'recommendation': {'legs': [{'kickoff': (NOW+timedelta(minutes=minutes)).isoformat()}]}}
    receipt = {'times': {'observed_at': (NOW-timedelta(minutes=age)).isoformat()}}
    analysis = {'options_shadow': {'options': {'laplace': [card]}, 'option_source_bindings': [
        {'profile_id': 'laplace', 'option_index': 0,
         'legs': [{'price_receipt': receipt, 'target_receipt': receipt}]}]}}
    assert module._completion_checks(analysis, NOW)[0]['eligible_at_completion'] is fresh


@pytest.mark.parametrize('minutes,age,fresh', [(15, 30, False), (16, 30, True), (16, 30.01, False),
                                            (16, -0.01, False)])
@pytest.mark.parametrize('specialist', ['cup', 'xg', 'motivation'])
def test_specialist_completion_has_own_lockout_and_receipt_age_gate(minutes, age, fresh, specialist):
    legs = [{'kickoff': (NOW+timedelta(minutes=minutes)).isoformat()} for _ in range(3)]
    receipt = {'times': {'observed_at': (NOW-timedelta(minutes=age)).isoformat()}}
    bindings = [{'leg_sha256': canonical_sha256(leg), 'price_receipt': receipt,
                 'target_receipt': receipt} for leg in legs]
    status = 'SHADOW_REVIEW_CANDIDATE' if specialist == 'motivation' else 'SHADOW_CANDIDATE'
    analysis = {'options_shadow': {specialist+'_review': {'status': status,
        'candidate_legs': legs, 'source_bindings': bindings}}}
    check = getattr(module, '_'+specialist+'_completion_check')
    assert check(analysis, NOW)['eligible_at_completion'] is fresh
    bindings.pop()
    assert not check(analysis, NOW)['eligible_at_completion']


@pytest.mark.parametrize('status', ['ABSTAINED', 'UNAVAILABLE', 'DISABLED'])
@pytest.mark.parametrize('specialist', ['cup', 'xg', 'motivation'])
def test_specialist_abstention_is_not_a_complete_option(status, specialist):
    check = getattr(module, '_'+specialist+'_completion_check')
    result = check({'options_shadow': {specialist+'_review': {
        'status': status, 'candidate_legs': [], 'source_bindings': [], 'reason': 'SOURCE_GAP'}}}, NOW)
    assert result['status'] == status and result['reason'] == 'SOURCE_GAP'
    assert result['eligible_at_completion'] is False


def test_human_completion_can_only_validate_review_eligibility():
    legs = [{'kickoff': (NOW+timedelta(hours=2)).isoformat()} for _ in range(3)]
    receipt = {'times': {'observed_at': NOW.isoformat()}}
    bindings = [{'leg_sha256': canonical_sha256(leg), 'price_receipt': receipt,
                 'target_receipt': receipt} for leg in legs]
    review = {'status': 'SHADOW_REVIEW_CANDIDATE', 'candidate_legs': legs, 'source_bindings': bindings}
    result = module._motivation_completion_check({'options_shadow': {'motivation_review': review}}, NOW)
    assert result['eligible_at_completion'] and result['independent_review_required']
    assert result['publication_eligible'] is False
    review['status'] = 'SHADOW_CANDIDATE'
    assert not module._motivation_completion_check({'options_shadow': {'motivation_review': review}}, NOW)['eligible_at_completion']
