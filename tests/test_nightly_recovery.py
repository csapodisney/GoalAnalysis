"""TESTONLY existing-run recovery: original clocks/quotes survive; AI never repeats."""
import importlib.util
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from test_daily_223_live import config
from test_portfolio_ledger import ticket

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.portfolio import DEFAULT_SETTINGS
from goal_analysis.jobs.background_history import publication_gate
from goal_analysis.jobs.nightly import JobStore, NightlyWorkflow, execution_deadline, schedule_plan
from goal_analysis.jobs.nightly_recovery import (
    RecoveryWorkflow,
    inspect_existing,
    recover_existing,
    recovery_deadline,
)
from goal_analysis.storage.portfolio_ledger import PortfolioLedger, _hash
from goal_analysis.strategy_provenance import attach_strategy_provenance
from goal_analysis.team.routing import generation_plan

NOW = datetime(2026, 10, 26, 9, tzinfo=UTC)
OBSERVED = NOW - timedelta(hours=10)


@pytest.fixture
def saved(tmp_path):
    cfg = tmp_path / 'config'
    cfg.mkdir()
    settings = {**deepcopy(DEFAULT_SETTINGS), 'enabled_profiles': ['daily223']}
    (cfg / 'arthur-settings.json').write_text(json.dumps(settings), encoding='utf-8')
    (cfg / 'daily223-live.json').write_text(json.dumps(config()), encoding='utf-8')
    issued = ticket('synthetic-recovery-ticket', 'daily223', date=NOW.date().isoformat(),
                    created_at=OBSERVED.isoformat())
    for i, leg in enumerate(issued['legs']):
        leg.update(fixture_id=f'the_stats_api:mt_{100 + i}', kickoff=(NOW + timedelta(hours=3)).isoformat(),
                   quoted_at=None, quote_observed_at=OBSERVED.isoformat())
    recommendation = {**deepcopy(issued), 'recommendation_id': 'synthetic-rec', 'status': 'REVIEWED',
                      'portfolio_ticket_id': issued['ticket_id'], 'selected_for_portfolio': True}
    report = {'schema_version': 3, 'run_id': 'synthetic-existing-normal', 'date': NOW.date().isoformat(),
        'observed_at': OBSERVED.isoformat(), 'finished_at': (OBSERVED + timedelta(minutes=1)).isoformat(),
        'construction_status': 'PARTIAL', 'tickets': [issued], 'recommendations': [recommendation],
        'astra': {'status': 'COMPLETE', 'test_only': True}, 'settings': settings,
        'team': generation_plan(settings['enabled_profiles']), 'usage': {'football_calls': 0},
        'grok_review': {'status': 'COMPLETE', 'test_only': True, 'mode': 'SHADOW'},
        'real_wager_placed': False, 'betting_approved': False}
    attach_strategy_provenance(report, settings)
    report['report_sha256'] = canonical_sha256(report)
    ledger = PortfolioLedger(tmp_path / 'data/arthur/ledger.sqlite3', now=lambda: NOW)
    ledger.save_run(report)
    archive = tmp_path / 'reports/arthur' / report['run_id']
    archive.mkdir(parents=True)
    path = archive / 'report.json'
    path.write_text(json.dumps(report), encoding='utf-8')
    kwargs = {'run_id': report['run_id'], 'expected_snapshot_hash': _hash(report),
              'expected_file_hash': sha256(path.read_bytes()).hexdigest()}
    return tmp_path, report, path, ledger, kwargs


def factory(monkeypatch, calls):
    monkeypatch.setattr('goal_analysis.jobs.nemotron.analyze', lambda *a, **k: {'status': 'DISABLED'})
    def create(root, clock):
        def forbidden(*a, **k):
            raise AssertionError('NEW_GENERATION_FORBIDDEN')
        def settle(job):
            calls.append('settlement')
            return {'errors': [], 'remaining_fixtures': 0, 'test_only': True}
        def learn(as_of):
            calls.append('learning')
            return {'report_id': 'synthetic-learning', 'llm': {'status': 'NO_DATA'}, 'as_of': as_of.isoformat()}
        return RecoveryWorkflow(root, clock=clock, generator=forbidden, settler=settle, learner=learn)
    return create


def test_actual_reuse_freeze_and_idempotence_keep_original_report(saved, monkeypatch):
    root, report, path, ledger, kwargs = saved
    original = path.read_bytes()
    calls = []
    create = factory(monkeypatch, calls)
    result = recover_existing(root, **kwargs, now=lambda: NOW, workflow_factory=create)
    assert result['status'] == 'COMPLETED'
    assert calls == ['settlement', 'learning']
    stage = next(s for s in result['job']['stages'] if s['name'] == 'generation')
    assert stage['run_id'] == report['run_id'] and stage['result']['generation_calls_in_recovery'] == 0
    assert stage['result']['execution_kind'] == 'REUSED_EXISTING_NORMAL_RUN'
    assert stage['result']['original_generation_finished_at'] == report['finished_at']
    assert result['job']['deadline'] == schedule_plan(NOW)['deadline']
    assert execution_deadline(result['job']) > NOW
    assert path.read_bytes() == original and ledger.list_runs()[0] == report
    assert ledger.list_freezes(date=report['date'])[0]['run_id'] == report['run_id']
    assert publication_gate(root, NOW, 55)['status'] == 'AVAILABLE'
    again = recover_existing(root, **kwargs, now=lambda: NOW, workflow_factory=create)
    assert again['status'] == 'ALREADY_RECOVERED' and calls == ['settlement', 'learning']


@pytest.mark.parametrize('field,value', [
    ('run_id', '../wrong'), ('expected_snapshot_hash', '0' * 64), ('expected_file_hash', '0' * 64),
    ('max_seconds', True), ('max_seconds', 59), ('max_seconds', 3601),
])
def test_invalid_admission_never_creates_job_or_freeze(saved, field, value):
    root, report, _path, ledger, kwargs = saved
    with pytest.raises(ValueError):
        inspect_existing(root, **{**kwargs, field: value}, now=NOW)
    assert not (root / 'data/arthur/automation.sqlite3').exists()
    assert ledger.list_freezes(date=report['date']) == []


def test_changed_archive_or_config_rejected_before_write(saved):
    root, report, path, _ledger, kwargs = saved
    path.write_text(json.dumps({**report, 'construction_status': 'COMPLETE'}), encoding='utf-8')
    with pytest.raises(ValueError, match='ARCHIVE_AND_LEDGER'):
        inspect_existing(root, **kwargs, now=NOW)
    assert not (root / 'data/arthur/automation.sqlite3').exists()


def test_existing_other_job_is_preserved_and_no_stage_runs(saved, monkeypatch):
    root, report, _path, ledger, kwargs = saved
    other = {'target_date': report['date'], 'updated_at': NOW.isoformat(), 'status': 'RUNNING',
             'stages': [], 'sentinel': 'DO_NOT_OVERWRITE'}
    JobStore(root).save(other)
    calls = []
    with pytest.raises(ValueError, match='EXISTING_WORKFLOW_JOB_PRESERVED'):
        recover_existing(root, **kwargs, now=lambda: NOW, workflow_factory=factory(monkeypatch, calls))
    assert JobStore(root).get(report['date']) == other and calls == []
    assert ledger.list_freezes(date=report['date']) == []


@pytest.mark.parametrize('field,value', [('deadline', '2026-10-26T20:00:00+00:00'),
                                       ('generation_calls_allowed', 1), ('source_snapshot_sha256', '0' * 64)])
def test_recovery_window_and_identity_cannot_expand(saved, field, value):
    root, _report, _path, _ledger, kwargs = saved
    job = inspect_existing(root, **kwargs, now=NOW)
    job['existing_run_recovery'][field] = value
    with pytest.raises(ValueError, match='INVALID_EXISTING_RUN_RECOVERY'):
        recovery_deadline(job)


def test_close_to_kickoff_and_later_target_do_not_get_recovery_window(saved):
    root, _report, _path, _ledger, kwargs = saved
    for at in (NOW + timedelta(hours=3, minutes=-14), NOW + timedelta(days=1)):
        with pytest.raises(ValueError):
            inspect_existing(root, **kwargs, now=at)


def test_pending_recovery_does_not_preclaim_success_or_relabel_source(saved):
    root, report, _path, _ledger, kwargs = saved
    plan = inspect_existing(root, **kwargs, now=NOW)
    assert all(stage['status'] == 'PENDING' and stage['attempts'] == 0 for stage in plan['stages'])
    assert plan['manifest_sha256'] == report['strategy_manifest']['manifest_sha256']
    assert plan['existing_run_recovery']['original_generation_observed_at'] == report['observed_at']
    assert NOW < recovery_deadline(plan) <= NOW + timedelta(minutes=30)
    assert not (root / 'data/arthur/automation.sqlite3').exists()


def test_lost_generation_checkpoint_blocks_without_fallback(saved, monkeypatch):
    root, report, _path, ledger, kwargs = saved
    job = inspect_existing(root, **kwargs, now=NOW)
    workflow = RecoveryWorkflow(root, clock=lambda: NOW, ledger=ledger)
    monkeypatch.setattr(workflow, '_saved_report', lambda run_id: None)
    workflow._execute_stage(job, 'generation')
    stage = next(s for s in job['stages'] if s['name'] == 'generation')
    assert stage['status'] == 'BLOCKED' and stage['run_id'] == report['run_id']
    assert stage['reason'] == 'IMMUTABLE_RECOVERY_GENERATION_UNAVAILABLE' and stage['attempts'] == 0


def test_normal_window_and_manual_tomorrow_rule_are_unchanged(saved):
    root, _report, _path, _ledger, _kwargs = saved
    assert schedule_plan(NOW)['due'] is False
    assert execution_deadline({'deadline': schedule_plan(NOW)['deadline']}) < NOW
    with pytest.raises(ValueError, match='must target tomorrow'):
        NightlyWorkflow(root, clock=lambda: NOW).run(scheduled=False, target_date=NOW.date())


def test_unknown_running_learning_cannot_replay(saved, monkeypatch):
    root, report, _path, _ledger, kwargs = saved
    job = inspect_existing(root, **kwargs, now=NOW)
    next(s for s in job['stages'] if s['name'] == 'learning')['status'] = 'RUNNING'
    JobStore(root).save(job)
    calls = []
    with pytest.raises(ValueError, match='UNKNOWN_RECOVERY_STAGE_EXECUTION'):
        recover_existing(root, **kwargs, now=lambda: NOW, workflow_factory=factory(monkeypatch, calls))
    assert calls == [] and JobStore(root).get(report['date']) == job


def test_pending_recovery_cannot_extend_its_original_budget(saved, monkeypatch):
    root, report, _path, _ledger, kwargs = saved
    job = inspect_existing(root, **kwargs, now=NOW, max_seconds=60)
    JobStore(root).save(job)
    calls = []
    with pytest.raises(ValueError, match='BUDGET_OR_EXECUTION_SOURCE_CHANGED'):
        recover_existing(root, **kwargs, now=lambda: NOW, max_seconds=1800,
                         workflow_factory=factory(monkeypatch, calls))
    assert calls == [] and JobStore(root).get(report['date']) == job


def test_conflicting_real_freeze_is_preserved(saved):
    root, report, _path, ledger, kwargs = saved
    other = deepcopy(report)
    other['run_id'] = 'synthetic-conflicting-normal'
    other['tickets'][0]['ticket_id'] = 'synthetic-other-ticket'
    other['recommendations'][0]['ticket_id'] = 'synthetic-other-ticket'
    other['recommendations'][0]['portfolio_ticket_id'] = 'synthetic-other-ticket'
    other['recommendations'][0]['recommendation_id'] = 'synthetic-other-rec'
    other.pop('report_sha256')
    other['report_sha256'] = canonical_sha256(other)
    ledger.save_run(other)
    ledger.freeze_run(other['run_id'])
    with pytest.raises(ValueError, match='CONFLICTING_DAILY_FREEZE_PRESERVED'):
        inspect_existing(root, **kwargs, now=NOW)
    assert ledger.list_freezes()[0]['run_id'] == other['run_id']
    assert not (root / 'data/arthur/automation.sqlite3').exists()


@pytest.mark.parametrize('arguments', [
    ['--recover-run', 'synthetic'],
    ['--scheduled', '--expected-file-sha256', '0' * 64],
    ['--recover-run', 'synthetic', '--date', '2026-10-26'],
])
def test_cli_requires_explicit_hashes_and_keeps_original_date_guard(arguments):
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / 'scripts/run-arthur-nightly.py'
    spec = importlib.util.spec_from_file_location('recovery_cli_invalid', path)
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    with pytest.raises(SystemExit) as error:
        command.main(arguments)
    assert error.value.code == 2


def test_cli_reuse_is_explicit_and_never_calls_normal_generator(saved, monkeypatch, capsys):
    root, _report, _path, _ledger, kwargs = saved
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / 'scripts/run-arthur-nightly.py'
    spec = importlib.util.spec_from_file_location('recovery_cli_explicit', path)
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    command.ROOT = root
    seen = []
    def recover(received_root, **options):
        seen.append((received_root, options))
        return {'status': 'ALREADY_RECOVERED'}
    monkeypatch.setattr('goal_analysis.jobs.nightly_recovery.recover_existing', recover)
    assert command.main(['--recover-run', kwargs['run_id'], '--expected-snapshot-sha256',
                         kwargs['expected_snapshot_hash'], '--expected-file-sha256', kwargs['expected_file_hash']]) == 0
    assert seen == [(root, kwargs)]
    assert json.loads(capsys.readouterr().out)['status'] == 'ALREADY_RECOVERED'
