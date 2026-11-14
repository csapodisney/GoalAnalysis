"""FIRST human-deadline fixtures: no native process, provider, DB or model."""
import hashlib
import importlib.util
import io
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from goal_analysis.jobs.agent_snapshot import digest
from goal_analysis.jobs.workshop_windows_job import (
    human_deadline_policy,
    human_phase_deadline,
    resource_cancel_decision,
)

NOW = datetime(2026, 11, 14, 11, 0, tzinfo=UTC)
REQUEST = 'a0000000-0000-4000-8000-000000000001'
JOB = 'b0000000-0000-4000-8000-000000000002'
SOURCE = {'relative_path': 'artifacts/TESTONLY/release/SOURCE-MAP.json',
          'sha256': 'a' * 64, 'source_commit': 'b' * 40, 'source_files_sha256': 'c' * 64}


def _authority(source=None):
    return {'status': 'HUMAN_WORKSHOP_DEADLINE_V1', 'requested_at_utc': NOW.isoformat(),
        'request_id': REQUEST, 'target_date': '2026-11-14', 'source_map': deepcopy(source or SOURCE),
        'business_stop_at_utc': (NOW + timedelta(hours=1)).isoformat(),
        'verification_stop_at_utc': (NOW + timedelta(hours=1, minutes=3)).isoformat(),
        'human_stop_at_utc': (NOW + timedelta(hours=1, minutes=7, seconds=41)).isoformat(),
        'human_authority_sha256': 'd' * 64}


def _raw(value):
    return json.dumps(value, sort_keys=True).encode('utf-8')


def _worker():
    path = Path(__file__).resolve().parents[1] / 'scripts/run-arthur-ten-job.py'
    spec = importlib.util.spec_from_file_location('testonly_human_deadline_worker', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_policy_clamps_original_limits_and_rejects_wrong_authority():
    value = _authority()
    raw = _raw(value)
    policy = human_deadline_policy(raw, NOW, request_id=REQUEST, target_date='2026-11-14', source_binding=SOURCE)
    assert policy['authority_sha256'] == hashlib.sha256(raw).hexdigest()
    assert human_phase_deadline(NOW, seconds=5400, label='business', policy=policy) == NOW + timedelta(hours=1)
    assert human_phase_deadline(NOW, seconds=180, label='verification', policy=policy) == NOW + timedelta(seconds=180)
    assert human_phase_deadline(NOW, seconds=5400, label='business') == NOW + timedelta(seconds=5400)
    assert resource_cancel_decision(None, NOW) is None
    old = _raw({'status': 'HUMAN_RESOURCE_HARDSTOP_5_PERCENT', 'requested_at_utc': NOW.isoformat()})
    assert resource_cancel_decision(old, NOW) == {'requested_at_utc': NOW.isoformat(),
                                                'request_sha256': hashlib.sha256(old).hexdigest()}
    for field, replacement in (
        ('request_id', JOB), ('target_date', '2026-11-15'), ('status', 'HUMAN_RESOURCE_HARDSTOP_5_PERCENT'),
        ('requested_at_utc', (NOW + timedelta(seconds=1)).isoformat()),
        ('requested_at_utc', (NOW - timedelta(seconds=601)).isoformat()),
        ('business_stop_at_utc', NOW.isoformat()),
        ('verification_stop_at_utc', (NOW + timedelta(minutes=59)).isoformat()),
        ('human_stop_at_utc', (NOW + timedelta(minutes=62)).isoformat()),
        ('business_stop_at_utc', '2026-11-14T12:00:00'),
        ('business_stop_at_utc', '2026-11-14T13:00:00+01:00'),
        ('human_authority_sha256', False),
    ):
        changed = {**value, field: replacement}
        with pytest.raises((ValueError, TypeError)):
            human_deadline_policy(_raw(changed), NOW, request_id=REQUEST,
                target_date='2026-11-14', source_binding=SOURCE)
    changed = deepcopy(value)
    changed['source_map']['sha256'] = 'e' * 64
    with pytest.raises(ValueError, match='SOURCE_MISMATCH'):
        human_deadline_policy(_raw(changed), NOW, request_id=REQUEST,
            target_date='2026-11-14', source_binding=SOURCE)
    changed['source_map'] = {**SOURCE, 'relative_path': 'artifacts/../SOURCE-MAP.json'}
    with pytest.raises(ValueError, match='SOURCE_MAP_PATH'):
        human_deadline_policy(_raw(changed), NOW, request_id=REQUEST,
            target_date='2026-11-14', source_binding=changed['source_map'])


def test_actual_worker_loader_binds_request_job_and_physical_sources_once(tmp_path, monkeypatch):
    worker = _worker()
    root, owned = tmp_path / 'root', tmp_path / 'owned'
    root.mkdir()
    owned.mkdir()
    monkeypatch.setattr(worker, 'ROOT', root)
    monkeypatch.setattr(worker, 'OUTPUT', owned)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(worker, 'datetime', Clock)
    job = {'job_id': JOB, 'request_id': REQUEST, 'target_date': '2026-11-14'}
    assert worker.load_human_deadline(job) is None
    code = root / 'src/goal_analysis/TESTONLY.py'
    code.parent.mkdir(parents=True)
    code.write_text('# TESTONLY source bytes\n', encoding='utf-8')
    actual = {'src/goal_analysis/TESTONLY.py': hashlib.sha256(code.read_bytes()).hexdigest()}
    project = root / 'pyproject.toml'
    project.write_text('[project]\nname = \"TESTONLY\"\n', encoding='utf-8')
    actual['pyproject.toml'] = hashlib.sha256(project.read_bytes()).hexdigest()
    source_path = root / SOURCE['relative_path']
    source_path.parent.mkdir(parents=True)
    source_path.write_text(json.dumps({'source_commit': SOURCE['source_commit'],
        'files': [{'path': name, 'worktree_sha256': value} for name, value in actual.items()]}), encoding='utf-8')
    source = {**SOURCE, 'sha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
              'source_files_sha256': digest(actual)}
    path = root / 'data/arthur/workshop-deadlines' / (REQUEST + '.json')
    path.parent.mkdir(parents=True)
    raw = _raw(_authority(source))
    path.write_bytes(raw)
    policy = worker.load_human_deadline(job)
    assert (owned / 'human-deadline-authority.json').read_bytes() == raw
    receipt = json.loads((owned / 'human-deadline-binding.json').read_text('utf-8'))
    assert receipt['job_id'] == JOB and receipt['request_id'] == REQUEST and receipt['source_map'] == source
    assert receipt['authority_sha256'] == policy['authority_sha256'] == hashlib.sha256(raw).hexdigest()
    assert receipt['read_once_no_periodic_sentinel'] is True
    code.write_text('# changed source\n', encoding='utf-8')
    with pytest.raises(ValueError, match='SOURCE_BYTES_CHANGED'):
        worker.load_human_deadline(job)
    assert (owned / 'human-deadline-authority.json').read_bytes() == raw
    assert not (owned / 'business-child-start.json').exists()


def test_actual_supervisor_expired_verification_does_not_spawn(tmp_path, monkeypatch):
    worker = _worker()
    monkeypatch.setattr(worker, 'ROOT', tmp_path / 'root')
    monkeypatch.setattr(worker, 'OUTPUT', tmp_path.resolve())
    policy = {**_authority(), 'authority_sha256': 'e' * 64}
    monkeypatch.setattr(worker, 'HUMAN_DEADLINE', policy)
    verification_stop = datetime.fromisoformat(policy['verification_stop_at_utc'])
    monkeypatch.setattr(worker, 'DEADLINE', verification_stop)
    monkeypatch.setattr(worker, 'resource_hardstop', lambda: None)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return verification_stop + timedelta(seconds=1)

    monkeypatch.setattr(worker, 'datetime', Clock)

    def forbidden(*args, **kwargs):
        raise AssertionError('No job or child may be created after the phase deadline')

    result = worker.supervise(['TESTONLY'], tmp_path, seconds=180, label='verification',
        spawn=forbidden, job_factory=forbidden)
    assert result['status'] == 'HUMAN_PHASE_DEADLINE_BEFORE_SPAWN'
    assert result['native_exit_code'] is None and result['child_tree_stopped'] is True
    assert result['child_started'] is False and result['automatic_retry'] is False
    assert (tmp_path / 'verification-outcome.json').is_file()
    assert not (tmp_path / 'verification-child-start.json').exists()


def test_actual_supervisor_stops_owned_business_then_allows_separate_verifier(tmp_path, monkeypatch):
    worker = _worker()
    monkeypatch.setattr(worker, 'ROOT', tmp_path / 'root')
    monkeypatch.setattr(worker, 'OUTPUT', tmp_path.resolve())
    policy = {**_authority(), 'business_stop_at_utc': (NOW + timedelta(seconds=1)).isoformat(),
              'verification_stop_at_utc': (NOW + timedelta(minutes=3)).isoformat(), 'authority_sha256': 'e' * 64}
    monkeypatch.setattr(worker, 'HUMAN_DEADLINE', policy)
    monkeypatch.setattr(worker, 'DEADLINE', NOW + timedelta(seconds=1))
    monkeypatch.setattr(worker, 'resource_hardstop', lambda: None)

    class Clock(datetime):
        calls = 0

        @classmethod
        def now(cls, tz=None):
            cls.calls += 1
            return NOW if cls.calls == 1 else NOW + timedelta(seconds=2)

    monkeypatch.setattr(worker, 'datetime', Clock)
    jobs, children = [], []

    class Child:
        pid = 123

        def __init__(self, completed):
            self.returncode = 0 if completed else None
            self.stdout, self.stderr = io.BytesIO(b'TESTONLY\n'), io.BytesIO()
            children.append(self)

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            return self.returncode

    class Job:
        def __init__(self):
            self.child = None
            self.terminations = 0
            self.closed = False
            jobs.append(self)

        def assign(self, child):
            self.child = child

        def resume(self, child):
            assert child is self.child

        def active(self):
            return int(self.child.returncode is None)

        def terminate(self):
            self.terminations += 1
            self.child.returncode = 124

        def close(self):
            self.closed = True

    def spawn(*args, **kwargs):
        return Child(completed=len(children) == 1)

    stopped = worker.supervise(['TESTONLY'], tmp_path, seconds=5400, spawn=spawn, job_factory=Job)
    assert stopped['status'] == 'HUMAN_PHASE_DEADLINE_STOP' and stopped['native_exit_code'] == 124
    assert stopped['child_tree_stopped'] and stopped['log_pumps_stopped']
    assert jobs[0].terminations == 1 and jobs[0].closed and stopped['automatic_retry'] is False
    monkeypatch.setattr(worker, 'DEADLINE', human_phase_deadline(NOW + timedelta(seconds=2),
        seconds=180, label='verification', policy=policy))
    verified = worker.supervise(['TESTONLY'], tmp_path, seconds=180, label='verification',
        spawn=spawn, job_factory=Job)
    assert verified['status'] == 'CHILD_STOPPED_NATIVE' and verified['native_exit_code'] == 0
    assert verified['child_tree_stopped'] and verified['log_pumps_stopped']
    assert jobs[1].terminations == 0 and jobs[1].closed and len(children) == 2
    assert json.loads((tmp_path / 'business-outcome.json').read_text('utf-8'))['native_exit_code'] == 124
