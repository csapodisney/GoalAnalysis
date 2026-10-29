"""Prepared-only receipt regressions; no external test helpers or live operations."""

import ast
import hashlib
import json
import runpy
import socket
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from goal_analysis.jobs import monitor_attempt, odds_monitor, prospective_odds
from goal_analysis.providers import the_stats_api, the_stats_primary
from goal_analysis.storage.db import Database

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 29, 19, tzinfo=UTC)
FAILURE = {"status": "MONITOR_UNAVAILABLE", "source_read_failure": True,
           "reason": "PRIMARY_RUN_HASH_MISMATCH", "provider_calls": 0,
           "source_read": {"counts": {"rows_fetched": 2, "runs_selected": 1}}}


@pytest.fixture(autouse=True)
def owned_boundary(tmp_path, monkeypatch):
    owned = tmp_path.resolve()
    assert not owned.is_relative_to(ROOT)
    assert not owned.is_relative_to(Path("C:/AI-Work/GoalAnalysis").resolve())
    original = sqlite3.connect
    connections = []

    def connect(database, *args, **kwargs):
        uri = urlsplit(str(database)) if str(database).startswith("file:") else None
        if uri is not None:
            assert uri.netloc in ("", "localhost")
            value = urllib.request.url2pathname(uri.path)
        else:
            assert "://" not in str(database)
            value = database
        assert Path(value).resolve().is_relative_to(owned), "OWNED_TESTONLY_DB_REQUIRED"
        connections.append(str(value))
        return original(database, *args, **kwargs)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("NO_PROVIDER_NETWORK_PROCESS_OR_DDL")

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(Database, "initialize", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(the_stats_api, "_default_transport", forbidden)
    yield connections


def write(root, result=None, *, at=NOW, **kwargs):
    return monitor_attempt.record_monitor_attempt(
        root, FAILURE if result is None else result, started_at=at, finished_at=at,
        classified_exit_code=1 if result is None else int(result["status"] == "MONITOR_UNAVAILABLE"),
        **kwargs,
    )


def test_source_receipt_is_small_sanitized_and_not_an_os_completion_claim(tmp_path):
    record = write(tmp_path, attempt_id="a" * 32)
    raw = (tmp_path / monitor_attempt.RELATIVE_PATH).read_bytes()
    assert len(raw) <= monitor_attempt.MAX_BYTES
    assert record["source_read_failure"] is True and record["adapter_dispatch_count"] == 0
    assert record["source_counts"]["rows_fetched"] == 2
    assert record["source_counts"]["empty_report_fallbacks"] is None
    assert record["query_failure_count"] is None
    assert record["classified_exit_code"] == 1 and "native_exit_code" not in record
    assert monitor_attempt.monitor_attempt_status(tmp_path, NOW)["receipt"] == record


@pytest.mark.parametrize("status", ["UPDATED", "NO_CHANGE", "NO_UNLOCKED_FIXTURES"])
def test_complete_success_replaces_previous_source_failure_without_inheriting_fields(tmp_path, status):
    before = write(tmp_path)
    after = write(tmp_path, {"status": status, "provider_calls": 0}, at=NOW + timedelta(seconds=1))
    assert before["attempt_id"] != after["attempt_id"]
    assert after["source_read_failure"] is False and after["reason"] is None
    assert after["source_counts"] == {} and after["classified_exit_code"] == 0


def test_only_allowlisted_public_fields_and_typed_counts_are_persisted(tmp_path):
    private = "secret https://private.invalid/?token=NEVER_SAVE C:/private/keys SQL"
    result = {**FAILURE, "message": private, "traceback": private, "provider_calls": True,
              "source_read": {"counts": {"rows_fetched": True, "runs_selected": 0,
                                           "empty_report_fallbacks": 0.0, "secret": private}}}
    record = write(tmp_path, result)
    raw = (tmp_path / monitor_attempt.RELATIVE_PATH).read_text()
    assert private not in raw and "NEVER_SAVE" not in raw
    assert record["adapter_dispatch_count"] is None
    assert record["source_counts"]["rows_fetched"] is None
    assert record["source_counts"]["runs_selected"] == 0
    assert record["source_counts"]["empty_report_fallbacks"] is None
    unknown = write(tmp_path, {"status": "MONITOR_UNAVAILABLE", "reason": private})
    assert unknown["source_read_failure"] is None
    assert unknown["reason"] == "UNSPECIFIED_MONITOR_FAILURE"


@pytest.mark.parametrize("stage", ["fsync", "replace"])
def test_atomic_failure_retains_old_complete_receipt_and_only_cleans_own_temp(tmp_path, monkeypatch, stage):
    write(tmp_path)
    path = tmp_path / monitor_attempt.RELATIVE_PATH
    original = path.read_bytes()

    def unavailable(*_args, **_kwargs):
        raise OSError("TESTONLY injected storage failure")

    monkeypatch.setattr(monitor_attempt.os, stage, unavailable)
    with pytest.raises(OSError):
        write(tmp_path, {"status": "UPDATED"})
    assert path.read_bytes() == original
    assert list(path.parent.glob("." + path.name + ".*.tmp")) == []


def test_missing_projection_does_not_create_storage_and_staleness_is_local(tmp_path):
    before = list(tmp_path.iterdir())
    assert monitor_attempt.monitor_attempt_status(tmp_path, NOW) == {"status": "MISSING", "receipt": None}
    assert list(tmp_path.iterdir()) == before
    write(tmp_path)
    assert monitor_attempt.monitor_attempt_status(tmp_path, NOW + timedelta(minutes=6))["status"] == "STALE"
    assert monitor_attempt.monitor_attempt_status(tmp_path, NOW - timedelta(seconds=1))["status"] == "UNAVAILABLE"


def test_projection_reads_only_the_receipt_without_any_db_connection(tmp_path, owned_boundary):
    write(tmp_path)
    assert monitor_attempt.monitor_attempt_status(tmp_path, NOW)["status"] == "AVAILABLE"
    assert owned_boundary == []


def test_dashboard_only_adds_the_readonly_receipt_projection_to_existing_snapshot():
    tree = ast.parse((ROOT / "src/goal_analysis/portfolio_dashboard.py").read_bytes())
    owner = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "ArthurState")
    snapshot = next(item for item in owner.body if isinstance(item, ast.FunctionDef) and item.name == "snapshot")
    assignments = [item for item in snapshot.body if isinstance(item, ast.Assign)
                   and isinstance(item.value, ast.Call) and isinstance(item.value.func, ast.Name)
                   and item.value.func.id == "monitor_attempt_status"]
    assert len(assignments) == 1
    expected = ast.parse('runtime["odds_monitor"]["last_attempt"] = monitor_attempt_status(self.root, now)').body[0]
    assert ast.dump(assignments[0], include_attributes=False) == ast.dump(expected, include_attributes=False)
    snapshot.body = [item for item in snapshot.body if item is not assignments[0]
                     and not (isinstance(item, ast.ImportFrom)
                              and item.module == "goal_analysis.jobs.monitor_attempt")]
    assert hashlib.sha256(ast.dump(snapshot, include_attributes=False).encode()).hexdigest() == BASELINE_SNAPSHOT


@pytest.mark.parametrize("fault", ["json", "oversize", "version", "extra", "duplicate", "boolean_count"])
def test_bad_saved_receipts_are_local_unknown_and_never_repaired(tmp_path, fault):
    value = write(tmp_path)
    path = tmp_path / monitor_attempt.RELATIVE_PATH
    if fault == "json":
        raw = b"{private invalid JSON"
    elif fault == "oversize":
        raw = b" " * (monitor_attempt.MAX_BYTES + 1)
    elif fault == "duplicate":
        raw = json.dumps(value).replace('"version":', '"version":"duplicate","version":').encode()
    else:
        if fault == "version":
            value["version"] = "unknown"
        elif fault == "extra":
            value["private_error"] = "NEVER_RETURN"
        else:
            value["query_failure_count"] = True
        raw = json.dumps(value).encode()
    path.write_bytes(raw)
    assert monitor_attempt.monitor_attempt_status(tmp_path, NOW) == {"status": "UNAVAILABLE", "receipt": None}
    assert path.read_bytes() == raw


def test_reader_rejects_redirected_path_before_opening_target(tmp_path, monkeypatch):
    path = tmp_path / monitor_attempt.RELATIVE_PATH
    outside = tmp_path / "outside.json"
    outside.write_text("PRIVATE_MUST_NOT_READ")
    resolve = Path.resolve

    def redirected(self, *args, **kwargs):
        return outside if self == path else resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected)
    assert monitor_attempt.monitor_attempt_status(tmp_path, NOW)["status"] == "UNAVAILABLE"
    assert outside.read_text() == "PRIVATE_MUST_NOT_READ"


def run_owned_cli(tmp_path, monkeypatch):
    script = tmp_path / "scripts/run-arthur-odds-monitor.py"
    script.parent.mkdir(parents=True)
    script.write_bytes((ROOT / "scripts/run-arthur-odds-monitor.py").read_bytes())
    config = tmp_path / "config/daily223-live.json"
    config.parent.mkdir()
    config.write_text("{}")
    monkeypatch.setattr("goal_analysis.config.daily_223_live.validate_live_config", lambda value: value)
    original_path = list(sys.path)
    try:
        with pytest.raises(SystemExit) as caught:
            runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.path[:] = original_path
    return caught.value.code


def test_corrupt_source_reports_failure_without_new_db_or_provider(tmp_path, monkeypatch, capsys):
    ledger = tmp_path / "data/arthur/ledger.sqlite3"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"TESTONLY invalid SQLite")
    original = ledger.read_bytes()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("SOURCE_FAILURE_CANNOT_CONSTRUCT_OR_CALL_PROVIDER")

    monkeypatch.setattr(odds_monitor, "OddsMonitor", forbidden)
    monkeypatch.setattr(the_stats_primary, "TheStatsPrimaryClient", forbidden)
    monkeypatch.setattr(prospective_odds, "tick", forbidden)
    assert run_owned_cli(tmp_path, monkeypatch) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["source_read_failure"] is True
    assert result["prospective_shadow"]["status"] == "SKIPPED_PRIMARY_SOURCE_UNAVAILABLE"
    assert result["monitor_attempt_receipt"]["status"] == "SAVED"
    assert ledger.read_bytes() == original
    assert not (tmp_path / "data/arthur/odds-monitor.sqlite3").exists()
    assert not (tmp_path / "data/arthur/the-stats-quota.sqlite3").exists()


@pytest.mark.parametrize("receipt_failure", [False, True])
def test_optional_shadow_and_receipt_failures_cannot_change_main_exit(tmp_path, monkeypatch, capsys, receipt_failure):
    monkeypatch.setattr(odds_monitor, "saved_monitor_input", lambda *_args: {"fixtures": []})

    def unavailable(*_args, **_kwargs):
        raise OSError("private TESTONLY observer failure")

    monkeypatch.setattr(prospective_odds, "tick", unavailable)
    if receipt_failure:
        monkeypatch.setattr(monitor_attempt, "record_monitor_attempt", unavailable)
    assert run_owned_cli(tmp_path, monkeypatch) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "NO_UNLOCKED_FIXTURES"
    assert result["prospective_shadow"]["status"] == "PROSPECTIVE_UNAVAILABLE"
    assert result["monitor_attempt_receipt"]["status"] == ("UNAVAILABLE" if receipt_failure else "SAVED")


def test_unknown_http_count_is_not_invented_from_adapter_dispatch_count(tmp_path):
    for dispatched in (0, 1, None):
        record = write(tmp_path, {"status": "NO_CHANGE", "provider_calls": dispatched,
                                 "query_failure_count": 1, "updated_or_cached_query_count": 0,
                                 "query_failure_scope": "FETCH_FAILURE_WITHOUT_UPDATED_OR_CACHED_QUERY"})
        assert record["adapter_dispatch_count"] == dispatched
        assert record["query_failure_count"] == 1
        assert "actual_http_count" not in record and record["classified_exit_code"] == 0


def test_main_and_classifier_ast_are_the_approved_baseline_not_the_bridge():
    source = ast.parse((ROOT / "scripts/run-arthur-odds-monitor.py").read_bytes())
    signatures = {node.name: hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
                  for node in source.body if isinstance(node, ast.FunctionDef)}
    assert signatures == BASELINE_CLI_FUNCTIONS


BASELINE_CLI_FUNCTIONS = {'main': 'ce1e5690c011b7442de1e8b25f849b30eb94418f225519cab731022014a18ff0', 'process_exit_code': '575e4b573038b2c24c7ed1407cf4874b781485e856e8ea56bd48542a344fb092'}
BASELINE_SNAPSHOT = "7f84a116bfd38670ddb8f74cfc35d4df88b51867ede58346bf149e8c385d2f49"
