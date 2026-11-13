"""TESTONLY fake processes and owned files; no CLI, provider or model invocation."""
import hashlib
import io
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from goal_analysis.agents import codex_review
from goal_analysis.agents.astra_review import _ReviewError, _usage


def prepared(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    binding = tmp_path / 'binding.json'
    binding.write_bytes(b'{"TESTONLY":"private-phase"}')
    return {'binding_path': binding, 'binding_sha256': hashlib.sha256(binding.read_bytes()).hexdigest(),
            'python_executable': 'TESTONLY-NOT-EXECUTED', 'server_script': 'TESTONLY-NOT-EXECUTED',
            'document': {'phase_root': str(tmp_path)}}


def interruption():
    return {'original_exception_type': 'TimeoutExpired', 'started_at': '2026-11-13T12:00:00+00:00',
            'observed_at': '2026-11-13T12:05:00+00:00', 'elapsed_seconds': 300.0,
            'timeout_seconds': 300.0, 'child_pid': 123,
            'native_exit_code': None, 'termination': {'wait_completed': False}}


def fake_process(monkeypatch, tmp_path, *, communication_error=None, cleanup_error=None):
    streams, processes, tree_requests = [], [], []

    def temporary_file():
        stream = (tmp_path / f'TESTONLY-stream-{len(streams)}.bin').open('w+b')
        streams.append(stream)
        return stream

    class Process:
        def __init__(self, command, **kwargs):
            self.command, self.kwargs = command, kwargs
            self.pid, self.returncode = 123, None
            processes.append(self)

        def communicate(self, *, input, timeout):
            self.kwargs['stdout'].write(b'{"type":"item.started","TESTONLY":true}\n')
            self.kwargs['stderr'].write(b'TESTONLY partial diagnostic\n')
            if communication_error is not None:
                raise communication_error
            raise subprocess.TimeoutExpired(self.command, timeout)

        def kill(self):
            if cleanup_error is not None:
                raise cleanup_error

        def wait(self):
            self.returncode = -9
            return self.returncode

    def tree_kill(command, **kwargs):
        tree_requests.append(command)
        return subprocess.CompletedProcess(command, 0)

    @contextmanager
    def temporary_directory(**kwargs):
        folder = tmp_path / 'TESTONLY-transport-cwd'
        folder.mkdir(exist_ok=True)
        yield str(folder)

    monkeypatch.setattr(codex_review.tempfile, 'TemporaryFile', temporary_file)
    monkeypatch.setattr(codex_review.tempfile, 'TemporaryDirectory', temporary_directory)
    monkeypatch.setattr(codex_review.subprocess, 'Popen', Process)
    monkeypatch.setattr(codex_review.subprocess, 'run', tree_kill)
    monkeypatch.setattr(codex_review.os, 'killpg', lambda pid, sig: tree_requests.append([pid, sig]), raising=False)
    return streams, processes, tree_requests


def test_actual_source_runner_timeout_keeps_private_logs_before_disposal(tmp_path, monkeypatch):
    source = prepared(tmp_path / 'phase')
    streams, processes, tree_requests = fake_process(monkeypatch, tmp_path)
    reviewer = codex_review.CodexReviewer(command=['TESTONLY-NO-CLI'], cache_dir=tmp_path / 'cache')
    reviewer.auth_checked = True
    body = {'instructions': 'TESTONLY supplied data', 'input': '{}',
            '_arthur_profile_timeout': 300.0, '_arthur_profile_source': source}
    with pytest.raises(_ReviewError, match='időtúllépés'):
        reviewer._request_with_slot(body, _usage())
    phase = Path(source['binding_path']).parent
    record = json.loads((phase / 'native-interruption.json').read_text('utf-8'))
    assert all(stream.closed for stream in streams)
    assert len(processes) == 1 and tree_requests
    assert record['status'] == 'INTERRUPTED_NO_ACCEPTANCE'
    assert record['original_exception_type'] == 'TimeoutExpired'
    assert 0 < record['timeout_seconds'] <= 300 and record['elapsed_seconds'] >= 0
    assert record['native_exit_code'] == -9 and record['termination']['wait_completed'] is True
    assert record['child_tree_stopped'] is None
    assert record['host_completion_proven'] is False and record['response_accepted'] is False
    assert record['binding_sha256'] == source['binding_sha256']
    assert (phase / 'native-interruption.stdout.bin').read_bytes() == b'{"type":"item.started","TESTONLY":true}\n'
    assert (phase / 'native-interruption.stderr.bin').read_bytes() == b'TESTONLY partial diagnostic\n'
    assert not (phase / 'native-return.json').exists()
    assert not (phase / 'receipt.json').exists()
    assert not ({'prompt', 'argv', 'environment'} & set(record))


def test_communication_and_cleanup_error_keep_unknown_native_exit(tmp_path, monkeypatch):
    source = prepared(tmp_path / 'phase')
    streams, _, _ = fake_process(monkeypatch, tmp_path,
        communication_error=OSError('TESTONLY communication failure'),
        cleanup_error=OSError('TESTONLY cleanup failure'))
    with pytest.raises(OSError, match='TESTONLY cleanup failure'):
        codex_review._run_process(['TESTONLY'], prompt='not persisted', cwd=tmp_path,
                                  timeout=300, source_capture=source)
    record = json.loads((tmp_path / 'phase/native-interruption.json').read_text('utf-8'))
    assert all(stream.closed for stream in streams)
    assert record['original_exception_type'] == 'OSError'
    assert record['termination']['error_type'] == 'OSError'
    assert record['termination']['wait_completed'] is False
    assert record['native_exit_code'] is None and record['child_tree_stopped'] is None
    assert record['response_accepted'] is False


def test_large_or_known_credential_partial_logs_are_withheld(tmp_path, monkeypatch):
    secret = 'TESTONLY-unique-credential-do-not-save-75dfe01e'
    monkeypatch.setenv('THE_STATS_API_KEY', secret)
    cases = [
        ('large', b'x' * (2 * 1024 * 1024 + 1), b'ordinary error', 'PARTIAL_LOG_BYTE_LIMIT_EXCEEDED'),
        ('secret', b'{"type":"item.started"}', secret.encode(), 'KNOWN_CREDENTIAL_VALUE_CAPTURE_WITHHELD'),
    ]
    for name, out, err, reason in cases:
        phase = tmp_path / name
        source = prepared(phase)
        result = codex_review._capture_profile_interruption(source, io.BytesIO(out), io.BytesIO(err),
                                                            interruption=interruption())
        assert result['content_withheld_reason'] == reason
        assert sum(item['captured_bytes'] for item in result['streams'].values()) == 0
        assert result['streams']['stdout']['partial_pipe_bytes'] == len(out)
        assert result['streams']['stderr']['partial_pipe_bytes'] == len(err)
        assert result['status'] == 'INTERRUPTED_NO_ACCEPTANCE' and result['response_accepted'] is False
        for path in phase.glob('native-interruption.*'):
            assert secret.encode() not in path.read_bytes()


def test_binding_and_once_boundaries_and_non_source_runner_are_unchanged(tmp_path, monkeypatch):
    phase = tmp_path / 'phase'
    source = prepared(phase)
    codex_review._capture_profile_interruption(source, io.BytesIO(b'first'), io.BytesIO(b'error'),
                                               interruption=interruption())
    old = {p.name: p.read_bytes() for p in phase.glob('native-interruption.*')}
    with pytest.raises(_ReviewError, match='PRIVATE_SOURCE_INTERRUPTION_CAPTURE_FAILED:FileExistsError'):
        codex_review._capture_profile_interruption(source, io.BytesIO(b'replacement'), io.BytesIO(),
                                                   interruption=interruption())
    assert {p.name: p.read_bytes() for p in phase.glob('native-interruption.*')} == old
    other = prepared(tmp_path / 'changed-binding')
    other['binding_path'].write_bytes(b'{"TESTONLY":"changed"}')
    with pytest.raises(_ReviewError, match='PRIVATE_SOURCE_INTERRUPTION_CAPTURE_FAILED:ValueError'):
        codex_review._capture_profile_interruption(other, io.BytesIO(b'not saved'), io.BytesIO(),
                                                   interruption=interruption())
    assert not list(other['binding_path'].parent.glob('native-interruption.*'))
    fake_process(monkeypatch, tmp_path)
    with pytest.raises(_ReviewError, match='időtúllépés'):
        codex_review._run_process(['TESTONLY'], prompt='not saved', cwd=tmp_path, timeout=300)
    assert not list(tmp_path.glob('native-interruption.*'))
