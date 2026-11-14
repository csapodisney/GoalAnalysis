"""TESTONLY physical pretty JSON bounds; no DB, provider, process or real job."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from goal_analysis import companion_tickets
from goal_analysis.jobs import workshop_verify
from goal_analysis.jobs.agent_snapshot import digest, json_text

RUN = '2026-11-14-TESTONLY-physical-report'
MIB = 1024 * 1024


class ObservedAdmission(BaseException):
    pass


def _script(name):
    path = Path(__file__).resolve().parents[1] / 'scripts' / name
    spec = importlib.util.spec_from_file_location('TESTONLY_' + name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path


def _report():
    value = {'run_id': RUN, 'date': '2026-11-14', 'generated_tickets': [],
             'companion_ticket_channels': {}, 'ten_agent_decisions': {}}
    value['report_sha256'] = digest(value)
    return value


def _pretty_padding(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as stream:
        stream.write(json.dumps(value, indent=2).encode('utf-8'))
        block = b' ' * MIB
        for _ in range(65):
            stream.write(block)
        stream.write(b'\n')
    assert 64 * MIB < path.stat().st_size < 128 * MIB
    assert len(json_text(value).encode('utf-8')) < 64 * MIB


def _worker(monkeypatch, root, report):
    worker, _ = _script('run-arthur-ten-job.py')
    monkeypatch.setattr(worker, 'ROOT', root)
    monkeypatch.setattr(worker, 'target_runs', lambda day: {RUN})
    monkeypatch.setattr(worker, 'SavedWorkshop', lambda path: SimpleNamespace(report=lambda **kw: report))
    return worker


def test_actual_cli_verifier_and_worker_accept_pretty_above64_canonical_below64(tmp_path, monkeypatch):
    root, report = tmp_path / 'root', _report()
    path = root / 'reports/arthur' / RUN / 'report.json'
    _pretty_padding(path, report)
    reader = workshop_verify.SavedReader(root)
    admitted = []

    def after_report(*args, **kwargs):
        admitted.append(dict(reader.files))
        raise ObservedAdmission

    monkeypatch.setattr(workshop_verify, '_v2_saved_source_limits', after_report)
    cli, script = _script('run-arthur-workshop-verify.py')
    monkeypatch.setattr(cli, 'ROOT', root)
    monkeypatch.setattr(cli, 'SavedReader', lambda path: reader)
    owned = tmp_path / 'arthur-ten-TESTONLY-capacity'
    owned.mkdir()
    output = owned / 'physical-verification.json'
    monkeypatch.setattr(sys, 'argv', [str(script), '--run-id', RUN, '--output', str(output)])
    hooks = []
    # Only capture this extra CLI hook registration; the parent isolation guard remains active.
    monkeypatch.setattr(sys, 'addaudithook', hooks.append)
    with pytest.raises(ObservedAdmission):
        cli.main()
    key = path.relative_to(root).as_posix()
    assert len(hooks) == 1 and len(admitted) == 1 and not output.exists()
    assert admitted[0][key]['bytes'] == path.stat().st_size
    assert reader.read_bytes == 2 * path.stat().st_size  # actual CLI first pin + actual verifier first pin

    worker = _worker(monkeypatch, root, report)

    def reached_product_guard(value):
        assert value == report
        raise ObservedAdmission

    monkeypatch.setattr(companion_tickets, 'validate_companion_cards', reached_product_guard)
    with pytest.raises(ObservedAdmission):
        worker.verify_saved('2026-11-14', set())

    # The separately measured candidate_input is the only non-snapshot artifact with this new bound.
    candidate = path.parent / 'candidate_input.json'
    _pretty_padding(candidate, {'candidates': []})
    monkeypatch.setattr(workshop_verify, '_v2_saved_source_limits', lambda *args, **kwargs: None)
    artifact_reader = workshop_verify.SavedReader(root)
    partial = workshop_verify.verify_new_cohort(root, report, reader=artifact_reader)
    assert partial['status'] == 'PARTIAL'  # no saved snapshot/owners claimed by this focused fixture
    assert artifact_reader.files[candidate.relative_to(root).as_posix()]['bytes'] == candidate.stat().st_size
    assert not any(row.get('reason') == 'SAVED_VERIFICATION_BYTE_BOUND' for row in partial['gaps'])
    assert partial['new_model_or_tool_calls'] == 0


def test_physical_above128_and_explicit_aggregate_or_default64_refuse_before_read(tmp_path, monkeypatch):
    root, report = tmp_path / 'root', _report()
    path = root / 'reports/arthur' / RUN / 'report.json'
    path.parent.mkdir(parents=True)
    with path.open('wb') as stream:
        stream.seek(128 * MIB)
        stream.write(b' ')  # stat-only rejection; no giant payload allocation or parse
    reader = workshop_verify.SavedReader(root)
    result = workshop_verify.verify_new_cohort(root, report, reader=reader)
    assert reader.read_bytes == 0 and reader.files == {}
    assert result['status'] == 'PARTIAL'
    assert any(row.get('reason') == 'SAVED_VERIFICATION_BYTE_BOUND'
               and row.get('verification_aborted') is True for row in result['gaps'])
    worker = _worker(monkeypatch, root, report)
    with pytest.raises(ValueError, match='BOUNDED_NEW_PHYSICAL_REPORT_REQUIRED'):
        worker.verify_saved('2026-11-14', set())
    cli, script = _script('run-arthur-workshop-verify.py')
    monkeypatch.setattr(cli, 'ROOT', root)
    monkeypatch.setattr(cli, 'SavedReader', lambda path: reader)
    owned = tmp_path / 'arthur-ten-TESTONLY-reject'
    owned.mkdir()
    monkeypatch.setattr(sys, 'argv', [str(script), '--run-id', RUN,
        '--output', str(owned / 'physical-verification.json')])
    hooks = []
    monkeypatch.setattr(sys, 'addaudithook', hooks.append)
    with pytest.raises(workshop_verify.SavedVerificationLimit, match='SAVED_VERIFICATION_BYTE_BOUND'):
        cli.main()
    assert reader.read_bytes == 0 and len(hooks) == 1
    _pretty_padding(path, report)
    for restricted, kwargs in ((workshop_verify.SavedReader(root, maximum_bytes=64 * MIB), {'limit': 128 * MIB}),
                               (workshop_verify.SavedReader(root), {})):
        with pytest.raises(workshop_verify.SavedVerificationLimit, match='SAVED_VERIFICATION_BYTE_BOUND'):
            restricted.pin(path, **kwargs)
        assert restricted.read_bytes == 0 and restricted.files == {}
