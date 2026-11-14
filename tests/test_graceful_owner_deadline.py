"""New deadline propagation controls; no real child, provider, model or database."""
import importlib.util
import json
import sys
from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.config.portfolio import DEFAULT_SETTINGS
from goal_analysis.jobs.agent_snapshot import digest
from goal_analysis.jobs.ten_agent_normal import NormalAgentExecutor, make_normal_agent_executor
from goal_analysis.jobs.workshop_windows_job import human_phase_deadline

NOW = datetime(2026, 11, 14, 13, tzinfo=UTC)
JOB = 'a0000000-0000-4000-8000-000000000001'
REQUEST = 'b0000000-0000-4000-8000-000000000002'


class CapturedBusiness(BaseException):
    """Stop an actual entry point before any business or native invocation."""


def _script(name):
    path = Path(__file__).resolve().parents[1] / 'scripts' / name
    spec = importlib.util.spec_from_file_location('testonly_' + name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _worker(tmp_path, monkeypatch, policy=None):
    worker = _script('run-arthur-ten-job.py')
    root, owned = tmp_path / 'root', tmp_path / 'owned'
    root.mkdir(parents=True)
    owned.mkdir()
    settings = {'TESTONLY': True}
    job = {'job_id': JOB, 'request_id': REQUEST, 'state': 'RESERVED',
           'target_date': '2026-11-14', 'settings': settings, 'settings_sha256': digest(settings)}

    class Store:
        def __init__(self, path):
            assert path == root / 'data/arthur/workshop-launch.sqlite3'

        def get(self, identifier, **kwargs):
            assert identifier == JOB
            return deepcopy(job)

        def transition(self, identifier, *, expected, values):
            assert identifier == JOB and job['state'] == expected
            job.update(values)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(worker, 'ROOT', root)
    monkeypatch.setattr(worker, 'datetime', Clock)
    monkeypatch.setattr(worker, 'LaunchStore', Store)
    monkeypatch.setattr(worker, 'RunLock', lambda path: nullcontext())
    monkeypatch.setattr(worker, 'source_pins', lambda: {'TESTONLY': 'a' * 64})
    monkeypatch.setattr(worker, 'load_human_deadline', lambda value: policy)
    monkeypatch.setattr(worker, 'target_runs', lambda day: set())
    monkeypatch.setattr(worker, 'source_environment', dict)
    monkeypatch.setattr(worker.tempfile, 'gettempdir', lambda: str(owned))
    monkeypatch.setattr(sys, 'argv', ['run-arthur-ten-job.py', '--job-id', JOB])
    return worker, owned / ('arthur-ten-' + JOB), job


def test_new_business_limit_preserves_legacy_and_human_shortening():
    assert human_phase_deadline(NOW, seconds=7200, label='business') == NOW + timedelta(seconds=7200)
    assert human_phase_deadline(NOW, seconds=5400, label='business') == NOW + timedelta(seconds=5400)
    policy = {'status': 'HUMAN_WORKSHOP_DEADLINE_V1',
              'business_stop_at_utc': (NOW + timedelta(seconds=900)).isoformat(),
              'verification_stop_at_utc': (NOW + timedelta(seconds=1000)).isoformat()}
    assert human_phase_deadline(NOW, seconds=7200, label='business', policy=policy) == NOW + timedelta(seconds=900)
    assert human_phase_deadline(NOW, seconds=180, label='verification', policy=policy) == NOW + timedelta(seconds=180)
    with pytest.raises(ValueError, match='EXACT_ORIGINAL_PHASE_LIMIT'):
        human_phase_deadline(NOW, seconds=7201, label='business')
    with pytest.raises(ValueError, match='EXACT_ORIGINAL_PHASE_LIMIT'):
        human_phase_deadline(NOW, seconds=7200, label='verification')


def test_actual_worker_binds_same_reserved_deadline_to_owned_receipt_and_bootstrap(tmp_path, monkeypatch):
    for seconds in (7200, 1200):
        policy = None if seconds == 7200 else {
            'status': 'HUMAN_WORKSHOP_DEADLINE_V1',
            'business_stop_at_utc': (NOW + timedelta(seconds=seconds)).isoformat()}
        worker, output, _job = _worker(tmp_path / str(seconds), monkeypatch, policy)
        captured = []

        def capture(command, folder, *, _captured=captured, **kwargs):
            _captured.append((command, folder, kwargs))
            raise CapturedBusiness

        monkeypatch.setattr(worker, 'supervise', capture)
        with pytest.raises(CapturedBusiness):
            worker.main()
        command, folder, options = captured[0]
        receipt = json.loads((output / 'owner-deadline-binding.json').read_text('utf-8'))
        stamp = (NOW + timedelta(seconds=seconds - 300)).isoformat()
        assert receipt['owner_stop_at_utc'] == command[command.index('-OwnerDeadlineAt') + 1] == stamp
        assert receipt['business_stop_at_utc'] == (NOW + timedelta(seconds=seconds)).isoformat()
        assert receipt['job_id'] == JOB and receipt['request_id'] == REQUEST
        assert receipt['save_reserve_seconds'] == 300 and receipt['reserve_is_not_a_wall_time_guarantee'] is True
        assert options['seconds'] == 7200 and folder == output and len(captured) == 1
        assert not (output / 'business-child-start.json').exists()


def test_worker_refuses_new_business_when_only_save_reserve_remains(tmp_path, monkeypatch):
    policy = {'status': 'HUMAN_WORKSHOP_DEADLINE_V1',
              'business_stop_at_utc': (NOW + timedelta(seconds=300)).isoformat()}
    worker, output, job = _worker(tmp_path, monkeypatch, policy)

    def forbidden(*args, **kwargs):
        raise AssertionError('No business transport may start without an owner window')

    monkeypatch.setattr(worker, 'supervise', forbidden)
    assert worker.main() == 1
    assert job['state'] == 'STOPPED' and job['native_exit'] is None and job['tree_stopped'] is True
    assert not (output / 'business-child-start.json').exists()
    assert not (output / 'owner-deadline-binding.json').exists()
    assert json.loads((output / 'worker-stop.json').read_text('utf-8'))['status'] == 'STOPPED_NO_RETRY'


def test_actual_cli_parser_and_production_factory_keep_absolute_budget_and_legacy_defaults(tmp_path):
    cli = _script('run-arthur.py')
    deadline = NOW + timedelta(seconds=100)
    args = cli.parser().parse_args(['--live', '--date', '2026-11-14',
                                   '--owner-deadline-at', deadline.isoformat()])
    settings = deepcopy(DEFAULT_SETTINGS)
    settings['enabled_profiles'] = ['daily223', *AGENT_IDS]
    normal = make_normal_agent_executor(tmp_path, settings, clock=lambda: NOW,
                                       owner_deadline_at=args.owner_deadline_at)
    budget = normal._owner_budget()
    assert normal.enable_source_access is True and normal.timeout_seconds == 4800
    assert budget.deadline == deadline and budget.skip_expired_owners is True
    assert budget.used_requests == 0
    later = make_normal_agent_executor(tmp_path, settings, clock=lambda: NOW,
                                       owner_deadline_at=NOW + timedelta(seconds=6900))
    assert later._owner_budget().deadline == NOW + timedelta(seconds=4800)
    expired = make_normal_agent_executor(tmp_path, settings, clock=lambda: NOW,
                                         owner_deadline_at=NOW - timedelta(seconds=1))
    assert expired._owner_budget().deadline < NOW
    legacy = NormalAgentExecutor(host=lambda *args, **kwargs: None, journal_root=tmp_path,
                                 clock=lambda: NOW)
    assert legacy.timeout_seconds == 3600 and legacy._owner_budget().skip_expired_owners is False
    with pytest.raises(ValueError, match='FINITE_NORMAL_AGENT_DEADLINE_REQUIRED'):
        NormalAgentExecutor(host=lambda *args, **kwargs: None, journal_root=tmp_path,
                            clock=lambda: NOW, timeout_seconds=4800)
    with pytest.raises(ValueError, match='AWARE_UTC_OWNER_DEADLINE_REQUIRED'):
        make_normal_agent_executor(tmp_path, settings, clock=lambda: NOW,
                                   owner_deadline_at=NOW.replace(tzinfo=None))
    assert not list(tmp_path.iterdir())
