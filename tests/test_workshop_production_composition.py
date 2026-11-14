"""Owned SQLite and synthetic source/host fixtures; no OS task or paid transport."""
import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_workshop_twelve_main_slots import _report

from goal_analysis.jobs.agent_snapshot import digest
from goal_analysis.jobs.workshop_launch import LaunchConflict, LaunchStore
from goal_analysis.jobs.workshop_schedule import FixedMorningTask
from goal_analysis.jobs.workshop_verify import SavedReader, _dataset_checks, verify_new_cohort
from goal_analysis.workshop_service import SavedWorkshop, production_workshop
from goal_analysis.workshop_views import main_slot_capacity

NOW = datetime(2026, 11, 14, 7, 5, tzinfo=UTC)


@pytest.fixture(autouse=True)
def owned_sqlite(tmp_path, monkeypatch):
    actual = sqlite3.connect

    def connect(path, *args, **kwargs):
        from pathlib import Path
        from urllib.parse import unquote, urlsplit
        value = unquote(urlsplit(str(path)).path) if str(path).startswith('file:') else str(path)
        if value.startswith('/') and len(value) > 2 and value[2] == ':':
            value = value[1:]
        assert Path(value).resolve().is_relative_to(tmp_path.resolve()), 'TESTONLY physical SQLite boundary'
        return actual(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', connect)


def test_shared_manual_success_consumes_morning_slot_without_dispatch_or_native_invention(tmp_path):
    store = LaunchStore(tmp_path / 'jobs.sqlite3')
    request = str(uuid4())
    job, reused = store.reserve(request_id=request, day='2026-11-14', trigger='MANUAL', settings={}, now=NOW)
    assert not reused
    morning, reused = store.reserve(request_id=str(uuid4()), day='2026-11-14', trigger='MORNING', settings={}, now=NOW)
    assert reused and morning['job_id'] == job['job_id']
    store.transition(job['job_id'], expected='RESERVED', values={'state': 'VERIFYING', 'native_exit': 0, 'tree_stopped': True})
    report = _report()
    capacity = main_slot_capacity(report)
    done = store.transition(job['job_id'], expected='VERIFYING', values={'state': 'SUCCEEDED',
        'run_id': report['run_id'], 'verification_state': 'VERIFIED', 'generated_ticket_count': 12,
        'target_met': True, 'capacity': capacity})
    repeat, reused = store.reserve(request_id=str(uuid4()), day='2026-11-14', trigger='MORNING', settings={}, now=NOW)
    assert reused and repeat == done and repeat['native_exit'] == 0
    # Separate store models manual completion before morning has claimed any slot.
    second = LaunchStore(tmp_path / 'fresh.sqlite3')
    own, _ = second.reserve(request_id=str(uuid4()), day='2026-11-14', trigger='MANUAL', settings={}, now=NOW)
    second.transition(own['job_id'], expected='RESERVED', values={'state': 'SUCCEEDED',
        'native_exit': 0, 'tree_stopped': True, 'run_id': report['run_id'],
        'verification_state': 'VERIFIED', 'generated_ticket_count': 12, 'target_met': True, 'capacity': capacity})
    consumed, reused = second.reserve(request_id=str(uuid4()), day='2026-11-14', trigger='MORNING', settings={}, now=NOW)
    assert reused and consumed['job_id'] == own['job_id']
    assert second.schedule(NOW)['last_slot']['state'] == 'CONSUMED_BY_VERIFIED_SHARED_JOB'


def test_schedule_pending_is_durable_before_os_and_failures_are_unknown_no_retry(tmp_path):
    store = LaunchStore(tmp_path / 'jobs.sqlite3')
    calls = []

    def failed(enabled):
        calls.append(enabled)
        observed = store.schedule(NOW)
        assert observed['enabled'] is None and observed['observation_status'] == 'PENDING_OS_CHANGE'
        raise OSError('TESTONLY uncertain OS response')

    result = store.change_schedule(None, True, failed, NOW)
    assert result['enabled'] is None and result['observation_status'] == 'UNKNOWN_OS_CHANGE_OUTCOME'
    with pytest.raises(LaunchConflict, match='REVISION'):
        store.change_schedule(None, True, failed, NOW)
    assert calls == [True]
    revision = result['revision']
    good = {'task_name': 'Arthur Ten Agents Morning', 'enabled': False,
            'observation_status': 'VERIFIED_FIXED_DEFINITION', 'legacy_tasks': [{'enabled': False}]}
    fixed = store.change_schedule(revision, False, lambda enabled: good, NOW)
    assert fixed['enabled'] is False and fixed['revision'] != revision


def test_fixed_schedule_adapter_uses_one_exact_command_and_checks_actual_readback(tmp_path):
    script = tmp_path / 'scripts/arthur-workshop-schedule.ps1'
    script.parent.mkdir()
    script.write_text('# TESTONLY source', encoding='utf-8')
    commands = []
    value = {'task_name': 'Arthur Ten Agents Morning', 'enabled': True,
        'timezone': 'Europe/Berlin', 'local_time': '08:00', 'observation_status': 'VERIFIED_FIXED_DEFINITION',
        'required_session': 'INTERACTIVE_USER_LOGGED_IN', 'wake_to_run': False, 'no_catchup': True}

    def run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(value).encode(), stderr=b'')

    assert FixedMorningTask(tmp_path, runner=run)(True)['enabled'] is True
    assert len(commands) == 1 and commands[0][0][-2:] == ['-EnabledValue', 'true']
    assert commands[0][1]['timeout'] == 45 and commands[0][1]['creationflags'] == 0x08000000
    value['enabled'] = False
    with pytest.raises(ValueError, match='READBACK'):
        FixedMorningTask(tmp_path, runner=run)(True)


def test_report_cache_avoids_reloading_full_snapshot_but_detects_database_mutation(tmp_path, monkeypatch):
    path = tmp_path / 'data/arthur/ledger.sqlite3'
    path.parent.mkdir(parents=True)
    report = {'run_id': '2026-11-14-12345678', 'date': '2026-11-14', 'observed_at': NOW.isoformat()}
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE portfolio_runs(run_id TEXT,date TEXT,observed_at TEXT,snapshot_json TEXT,snapshot_hash TEXT)')
        db.execute('INSERT INTO portfolio_runs VALUES(?,?,?,?,?)', (report['run_id'], report['date'], report['observed_at'], json.dumps(report), digest(report)))
    repository = SavedWorkshop(tmp_path)
    calls = []
    actual = json.loads
    monkeypatch.setattr('goal_analysis.workshop_service.json.loads', lambda value: (calls.append(len(value)), actual(value))[1])
    assert repository.report(day=report['date']) == report
    assert repository.report(day=report['date']) == report and len(calls) == 1
    with sqlite3.connect(path) as db:
        db.execute('UPDATE portfolio_runs SET snapshot_json=?', (json.dumps({**report, 'changed': True}),))
    with pytest.raises(ValueError, match='HASH_MISMATCH'):
        repository.report(day=report['date'])
    assert len(calls) == 2


def test_named_dataset_checks_hash_all_rows_and_do_not_scan_or_fit(tmp_path):
    from goal_analysis.normalization.training_datasets import snapshot
    from goal_analysis.storage.training_datasets import DatasetStore

    value = snapshot([{'fixture_id': 'TESTONLY:a', 'x': 0}], role='TESTONLY',
        as_of=NOW.isoformat(), recipe={'version': 'TESTONLY'}, lineage={})
    directory = tmp_path / 'data/arthur/models/TESTONLY/gauss/datasets'
    store = DatasetStore(directory)
    store.put(value)
    models = {'gauss': {'competition_results': {'TESTONLY': {'training_datasets': {'manifests': [value['manifest']]}}},
                        'training_datasets': {'manifests': [value['manifest']]}}}
    checked, gaps = _dataset_checks(tmp_path, models, SavedReader(tmp_path))
    assert len(checked) == 1 and gaps == [] and checked[0]['rows'] == 1
    path = store.path(value['manifest']['dataset_id'])
    damaged = deepcopy(value)
    damaged['rows'][0]['x'] = 1
    path.write_text(json.dumps(damaged), encoding='utf-8')
    checked, gaps = _dataset_checks(tmp_path, models, SavedReader(tmp_path))
    assert checked == [] and gaps[0]['reason'] == 'NAMED_DATASET_NOT_PHYSICALLY_BOUND'


def test_production_composition_is_inert_until_explicit_start_or_schedule(tmp_path):
    service = production_workshop(tmp_path, tmp_path / 'settings.json', route_capabilities={'financial_recording': True})
    assert not (tmp_path / 'data').exists()
    assert service.capabilities()['manual_generation'] is True
    assert service.capabilities()['schedule_mutation'] is True
    assert service.capabilities()['financial_recording'] is True
    assert service.capabilities()['explicit_probes'] is False


def test_physical_source_owner_and_two_tool_journals_are_checked_without_reexecution(tmp_path, monkeypatch):
    from test_companion_ticket_channels import NOW as SOURCE_NOW
    from test_companion_ticket_channels import SETTINGS, _frame
    from test_final_multioption_projection import _restore
    from test_profile_source_host import finish_fake_source_reply

    from goal_analysis.agents.profile_decision import SIMULATOR_ROLES
    from goal_analysis.agents.profile_tools import ProfileToolService
    from goal_analysis.jobs.ten_agent_normal import NormalAgentExecutor

    run = SOURCE_NOW.date().isoformat() + '-TESTONLYphysical'
    _, artifacts, enriched, _, _ = _frame(tmp_path / 'unused-original-frame', run=run)
    club = deepcopy(enriched)
    from goal_analysis.normalization.training_datasets import snapshot as dataset_snapshot
    from goal_analysis.storage.training_datasets import DatasetStore
    dataset = dataset_snapshot([{'fixture_id': 'TESTONLY:recorded-history', 'value': 0}],
        role='TESTONLY', as_of=SOURCE_NOW.isoformat(), recipe={'version': 'TESTONLY'}, lineage={})
    DatasetStore(tmp_path / 'data/arthur/models/datasets').put(dataset)
    artifacts['numeric_models'] = {'gauss': {'profile_id': 'gauss', 'status': 'INSUFFICIENT_HISTORY',
        'predictions': [], 'training_datasets': {'manifests': [dataset['manifest']], 'uses': [],
            'archive': {'status': 'ARCHIVED'}}}}
    calls = []

    def host(request, *, timeout, source_access):
        calls.append(request['phase'])
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        data = _restore(request['data'])
        if request['phase'] == 'PLAN':
            reply = {**identity, 'options': [], 'fixture_reviews': [
                {'fixture_id': key, 'reason': 'TESTONLY full calendar; explicit abstention', 'rejected': True}
                for key in data['snapshot']['fixture_ids']], 'tool_requests': [
                {'role': role, 'parameters': {}} for role in SIMULATOR_ROLES]}
        else:
            reply = {**identity, 'option_ids': [], 'reason': 'TESTONLY abstention without filler',
                'tool_interpretations': [{'request_id': row['request']['request_id'], 'interpretation': 'TESTONLY UNKNOWN remains a gap'}
                    for row in data['tool_results']],
                'family_uses': [{'family': name, 'status': {'AVAILABLE':'AVAILABLE_AND_CONSIDERED',
                    'UNKNOWN':'MISSING','CONFLICTING':'CONFLICTING','STALE':'STALE'}[row['status']],
                    'value_sha256': row['value_sha256'], 'option_ids': [], 'reason': 'TESTONLY considered',
                    'risk': 'TESTONLY unavailable inputs remain unknown'} for name, row in data['snapshot']['families'].items()]}
        reads = [artifacts['candidate_input']['candidates'][0]['candidate_id']] if request['phase'] == 'PLAN' else []
        return finish_fake_source_reply(source_access, request, reads, reply)

    executor = NormalAgentExecutor(host=host, journal_root=tmp_path / 'data/arthur/agent-decisions',
        clock=lambda: SOURCE_NOW, enable_source_access=True, enable_companion_channels=True,
        enable_ticket_product_contract=True)
    report = executor(run_id=run, target_date=SOURCE_NOW.date(), as_of=SOURCE_NOW,
        artifacts=artifacts, fixture_records=artifacts['fixtures']['records'], enriched=enriched,
        club_enriched=club, candidate_context={}, weather_by_fixture={}, historical_runs=[],
        feedback={}, settings=SETTINGS, team_plan={})
    report['report_sha256'] = digest({key: value for key, value in report.items() if key != 'report_sha256'})
    artifacts['report'] = report
    directory = tmp_path / 'reports/arthur' / run
    directory.mkdir(parents=True)
    for name, value in artifacts.items():
        (directory / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')
    assert len(calls) == 20
    assert report['ticket_product_contract']['version'] == 'TWELVE_MAIN_MIN10_SIX_FIXTURES_V1'
    assert len(report['generated_tickets']) == 2 and all(
        row['status'] == 'COMPLETE' for row in report['companion_ticket_channels']['channels'])
    monkeypatch.setattr(ProfileToolService, 'evaluate', lambda *args: pytest.fail('Verifier reran a tool'))
    physical = verify_new_cohort(tmp_path, report)
    assert physical['status'] == 'VERIFIED', physical['gaps']
    assert len(physical['owners']) == 10 and len(physical['datasets']) == 1
    assert physical['datasets'][0]['dataset_id'] == dataset['manifest']['dataset_id']
    assert {row['final_decision_status'] for row in physical['owners'].values()} == {'ABSTAIN'}
    assert physical['new_model_or_tool_calls'] == 0 and physical['old_ledger_preservation_verified'] is False
    assert main_slot_capacity(report)['target_met'] is False
    owner = report['ten_agent_decisions']['owners'][0]
    path = tmp_path / 'data/arthur/agent-decisions' / run / (owner['session_id'] + '.owner.json')
    path.write_text('{}', encoding='utf-8')
    broken = verify_new_cohort(tmp_path, report)
    assert broken['status'] == 'PARTIAL' and broken['owners'][owner['agent_id']]['status'] == 'UNKNOWN'
    assert sum(row['status'] == 'PHYSICAL_AND_CONTRACT_BOUND' for row in broken['owners'].values()) == 9


def test_actual_guarded_financial_routes_preserve_payload_and_reject_missing_token():
    from email.message import Message
    from io import BytesIO

    from goal_analysis.portfolio_dashboard import make_handler

    calls = []
    class Ledger:
        def record_wager(self, ticket_id, **values):
            calls.append(('wager', ticket_id, values))
            return True
        def get_ticket(self, ticket_id, **values):
            calls.append(('read', ticket_id, values))
            return {'ticket_id': ticket_id, 'bookmaker_return_contract': 'CURRENT_HASH_V2'}
        def confirm_bookmaker_return(self, ticket_id, amount, note, **values):
            calls.append(('return', ticket_id, amount, note, values))

    handler_type = make_handler(SimpleNamespace(ledger=Ledger()), 'TESTONLY-token')
    def invoke(path, payload, token='TESTONLY-token'):
        handler = handler_type.__new__(handler_type)
        handler.server = SimpleNamespace(server_port=8765)
        handler.headers = Message()
        raw = json.dumps(payload).encode()
        for key, value in {'Host': 'localhost:8765', 'Origin': 'http://localhost:8765',
            'Content-Type': 'application/json', 'Content-Length': str(len(raw)), 'X-Arthur-Token': token}.items():
            handler.headers[key] = value
        handler.path, handler.rfile = path, BytesIO(raw)
        responses = []
        handler._send = lambda value, status=200, **kwargs: responses.append((status, value))
        handler._reject_post = lambda value, status=403: responses.append((status, value))
        handler.do_POST()
        return responses[-1]

    assert invoke('/api/wager', {'ticket_id': 'TESTONLY:ticket', 'stake_eur': 1.25,
        'actual_combined_price': 17.5}, token='')[0] == 403
    assert calls == []
    assert invoke('/api/wager', {'ticket_id': 'TESTONLY:ticket', 'stake_eur': 1.25,
        'actual_combined_price': 17.5})[0] == 200
    assert calls[0] == ('wager', 'TESTONLY:ticket', {'stake_eur': 1.25, 'actual_combined_price': 17.5})
    before = len(calls)
    assert invoke('/api/bookmaker-return', {'ticket_id': 'TESTONLY:ticket', 'payout_eur': 10,
        'note': 'TESTONLY', 'expected_settlement_hash': 'a' * 64, 'payout_basis': 'NET_GUESS'})[0] == 400
    assert len(calls) == before
    assert invoke('/api/bookmaker-return', {'ticket_id': 'TESTONLY:ticket', 'payout_eur': 10,
        'note': 'TESTONLY', 'expected_settlement_hash': 'a' * 64,
        'payout_basis': 'BOOKMAKER_CONFIRMED_GROSS_SPORTS_RETURN'})[0] == 200
    assert calls[-2][4] == {'expected_settlement_hash': 'a' * 64,
        'payout_basis': 'BOOKMAKER_CONFIRMED_GROSS_SPORTS_RETURN'}


def test_actual_shared_service_and_dispatcher_use_one_fixed_worker_for_manual_and_morning(tmp_path, monkeypatch):
    from goal_analysis.agents.profile_roster import AGENT_IDS
    from goal_analysis.config.portfolio import DEFAULT_SETTINGS

    settings = deepcopy(DEFAULT_SETTINGS)
    settings['enabled_profiles'] = ['daily223', *AGENT_IDS]
    settings_path = tmp_path / 'config/arthur-settings.json'
    settings_path.parent.mkdir()
    settings_path.write_text(json.dumps(settings), encoding='utf-8')
    for name in ('scripts/run-arthur-ten-job.py', '.venv/Scripts/python.exe'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'TESTONLY no execution')
    calls = []
    def spawn(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(pid=12345)
    monkeypatch.setattr('goal_analysis.jobs.workshop_dispatch.subprocess.Popen', spawn)
    service = production_workshop(tmp_path, settings_path)
    service.clock = lambda: NOW
    service.schedule_adapter = lambda enabled: {'task_name': 'Arthur Ten Agents Morning',
        'enabled': enabled, 'observation_status': 'VERIFIED_FIXED_DEFINITION',
        'legacy_tasks': [{'enabled': False}]}
    service.update_schedule({'enabled': True, 'expected_revision': None})
    first = service.start({'date': '2026-11-14', 'request_id': str(uuid4()), 'mode': 'TEN_AGENT_FULL_DAY'})
    second = service.morning()
    assert first['job_id'] == second['job_id'] and second['reused'] is True
    assert len(calls) == 1 and calls[0][0][-2:] == ['--job-id', first['job_id']]
    assert calls[0][0][2] == str(tmp_path / 'scripts/run-arthur-ten-job.py')
    assert '--settle' not in calls[0][0] and calls[0][1]['creationflags'] & 0x08000000
    saved = service.store.get(first['job_id'])
    assert saved['native_exit'] is None and saved['state'] == 'RESERVED'
