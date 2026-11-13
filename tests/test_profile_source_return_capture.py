"""TESTONLY returned text preservation; no child process or model is started."""
import hashlib
import json
import subprocess

import pytest

from goal_analysis.agents.astra_review import _ReviewError
from goal_analysis.agents.codex_review import _capture_profile_return


def prepared(tmp_path):
    path = tmp_path / 'binding.json'
    path.write_bytes(b'{"TESTONLY":"owned-source-phase"}')
    return {'binding_path': path, 'binding_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def test_nonzero_native_return_is_preserved_privately_without_overwrite(tmp_path):
    source = prepared(tmp_path)
    done = subprocess.CompletedProcess(['TESTONLY-no-process'], 17, 'first\n\u00e1r\n', 'private failure detail')
    capture = _capture_profile_return(source, done)
    assert capture['native_exit_code'] == 17 and capture['status'] == 'COMPLETE_PRIVATE_CAPTURE'
    for name, value in (('stdout', done.stdout), ('stderr', done.stderr)):
        raw = value.encode('utf-8')
        assert (tmp_path / f'native-return.{name}.utf8').read_bytes() == raw
        assert capture['streams'][name]['returned_sha256'] == hashlib.sha256(raw).hexdigest()
        assert capture['streams'][name]['truncated'] is False
    saved = (tmp_path / 'native-return.json').read_bytes()
    assert json.loads(saved) == capture
    with pytest.raises(_ReviewError, match='PRIVATE_SOURCE_RETURN_CAPTURE_FAILED:FileExistsError'):
        _capture_profile_return(source, subprocess.CompletedProcess([], 0, 'different', 'different'))
    assert (tmp_path / 'native-return.json').read_bytes() == saved
    assert (tmp_path / 'native-return.stderr.utf8').read_text('utf-8') == done.stderr


def test_overlarge_return_saves_bounded_evidence_and_cannot_be_accepted(tmp_path):
    source = prepared(tmp_path)
    stdout = 'a' * (2 * 1024 * 1024 + 31)
    stderr = 'TESTONLY independent stderr'
    with pytest.raises(_ReviewError, match='PRIVATE_SOURCE_RETURN_CAPTURE_LIMIT_EXCEEDED_NO_ACCEPTANCE'):
        _capture_profile_return(source, subprocess.CompletedProcess([], 0, stdout, stderr))
    saved = json.loads((tmp_path / 'native-return.json').read_text('utf-8'))
    assert saved['native_exit_code'] == 0 and saved['status'] == 'TRUNCATED_NO_ACCEPTANCE'
    assert sum(row['captured_bytes'] for row in saved['streams'].values()) == 2 * 1024 * 1024
    assert all(row['truncated'] is True for row in saved['streams'].values())
    assert saved['streams']['stdout']['returned_sha256'] == hashlib.sha256(stdout.encode()).hexdigest()
    assert saved['streams']['stderr']['returned_sha256'] == hashlib.sha256(stderr.encode()).hexdigest()
    assert (tmp_path / 'native-return.stderr.utf8').stat().st_size == 0
