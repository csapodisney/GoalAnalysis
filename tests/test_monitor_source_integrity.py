"""Primary source errors are visible; all physical inputs are owned TESTONLY."""
import ctypes
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import odds_monitor as monitor
from goal_analysis.jobs.odds_monitor_providers import build_monitor_queries

NOW = datetime(2026, 10, 27, 10, tzinfo=UTC)
PROJECT = Path(monitor.__file__).resolve().parents[3]
MARKER = "MONITOR-SOURCE-TESTONLY.json"


def owned(path, base):
    path, base = Path(path).resolve(), Path(base).resolve()
    assert not base.is_relative_to(PROJECT) and not PROJECT.is_relative_to(base)
    assert path.is_relative_to(base) and not path.is_relative_to(PROJECT)
    assert json.loads((base / MARKER).read_text(encoding="utf-8")) == {"TESTONLY": True, "root": str(base)}
    return path


@pytest.fixture(autouse=True)
def isolated_io(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    assert not base.is_relative_to(PROJECT) and not PROJECT.is_relative_to(base)
    (base / MARKER).write_text(json.dumps({"TESTONLY": True, "root": str(base)}), encoding="utf-8")
    original_connect, original_popen = sqlite3.connect, subprocess.Popen
    attempts = Mock(side_effect=AssertionError("FORBIDDEN_PROVIDER_DECRYPT_DB_NETWORK_PROCESS"))
    connections = []
    def connect(path, *args, **kwargs):
        from urllib.parse import unquote, urlparse
        raw = str(path)
        if raw.startswith("file:"):
            raw = unquote(urlparse(raw).path)
            if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
                raw = raw[1:]
        owned(raw, base)
        connections.append((str(path), kwargs.get("uri", False)))
        return original_connect(path, *args, **kwargs)
    def process(command, *args, **kwargs):
        if (isinstance(command, list) and len(command) == 4
                and Path(command[0]).resolve() == Path(sys.executable).resolve()
                and command[1] == "-B" and Path(command[2]).name == "native_cli_bootstrap.py"):
            owned(command[2], base)
            owned(command[3], base)
            return original_popen(command, *args, **kwargs)
        return attempts(command)
    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(subprocess, "Popen", process)
    monkeypatch.setattr(socket.socket, "connect", attempts)
    monkeypatch.setattr(socket, "create_connection", attempts)
    monkeypatch.setattr(urllib.request, "urlopen", attempts)
    from goal_analysis.jobs.the_stats_context import Resources
    from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
    from goal_analysis.storage import Database
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger
    from goal_analysis.storage.temporal import TemporalStore
    monkeypatch.setattr(Database, "__init__", attempts)
    monkeypatch.setattr(PortfolioLedger, "__init__", attempts)
    monkeypatch.setattr(TheStatsPrimaryClient, "__init__", attempts)
    monkeypatch.setattr(Resources, "__init__", attempts)
    monkeypatch.setattr(TemporalStore, "capture", attempts)
    if hasattr(ctypes, "windll"):
        monkeypatch.setattr(ctypes.windll.crypt32, "CryptUnprotectData", attempts)
    yield base, connections
    attempts.assert_not_called()


def fixture(kickoff=None):
    return {"fixture_id": "the_stats_api:mt_990001", "source_provider": "the_stats_api",
            "provider_fixture_id": "mt_990001", "provider_league_id": "comp_990001",
            "provider_home_team_id": "tm_990001", "provider_away_team_id": "tm_990002",
            "provider_season_id": "sn_990001", "season": 2026, "provider_status": "NS",
            "sport_key": "soccer_testonly", "home_team": "TESTONLY Home", "away_team": "TESTONLY Away",
            "kickoff": (kickoff or NOW + timedelta(hours=1)).isoformat(), "neutral_venue": None}


def report(*, identifier="run-1", fixtures=None, observed=None):
    rows = [fixture()] if fixtures is None else fixtures
    day = datetime.fromisoformat(rows[0]["kickoff"]).astimezone(ZoneInfo("Europe/Berlin")).date() if rows else NOW.date()
    return {"run_id": identifier, "date": day.isoformat(), "observed_at": (observed or NOW - timedelta(minutes=1)).isoformat(),
            "fixtures": rows, "recommendations": [{"ticket_id": "TESTONLY recommendation"}],
            "odds_history_snapshot": {"quotes": [{"quoted_at": None, "quote_observed_at": NOW.isoformat()}]}}


def seed(base, rows, *, create=True):
    path = owned(base / "data/arthur/ledger.sqlite3", base)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        if create:
            db.execute("CREATE TABLE portfolio_runs(run_id TEXT,date TEXT,observed_at TEXT,snapshot_json TEXT,snapshot_hash TEXT)")
            for item, changes in rows:
                row = {"run_id": item["run_id"], "date": item["date"], "observed_at": item["observed_at"],
                       "snapshot_json": json.dumps(item), "snapshot_hash": canonical_sha256(item)}
                row.update(changes)
                db.execute("INSERT INTO portfolio_runs VALUES (?,?,?,?,?)", tuple(row[key] for key in
                    ("run_id", "date", "observed_at", "snapshot_json", "snapshot_hash")))
    return path


def physical(path):
    return path.read_bytes()


def test_absent_store_and_existing_empty_are_distinct(tmp_path, isolated_io):
    missing = monitor.saved_monitor_input(tmp_path, NOW)
    assert missing["fixtures"] == [] and missing["source_read"]["status"] == "FIRST_STORE_MISSING"
    assert isolated_io[1] == []
    path = seed(tmp_path, [])
    before = physical(path)
    empty = monitor.saved_monitor_input(tmp_path, NOW)
    assert empty["fixtures"] == [] and empty["source_read"]["status"] == "AVAILABLE"
    assert physical(path) == before


@pytest.mark.parametrize("kind", ["sqlite-bytes", "missing-table", "directory"])
def test_existing_invalid_store_never_becomes_healthy_empty(tmp_path, kind):
    path = owned(tmp_path / "data/arthur/ledger.sqlite3", tmp_path)
    path.parent.mkdir(parents=True)
    if kind == "sqlite-bytes":
        path.write_bytes(b"TESTONLY_NOT_A_DATABASE")
    elif kind == "missing-table":
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE unrelated(value TEXT)")
    else:
        path.mkdir()
    before = physical(path) if path.is_file() else None
    with pytest.raises(monitor.MonitorSourceReadError) as caught:
        monitor.saved_monitor_input(tmp_path, NOW)
    assert caught.value.reason in {"PRIMARY_LEDGER_UNREADABLE", "PRIMARY_LEDGER_SCHEMA_INVALID"}
    assert before is None or physical(path) == before


@pytest.mark.parametrize("payload", ["{PRIVATE_SECRET", "null", "[]", '"PRIVATE_SECRET"', '{"x":1e309}'])
def test_bad_current_JSON_is_typed_and_sanitized(tmp_path, payload):
    path = seed(tmp_path, [(report(), {"snapshot_json": payload})])
    before = physical(path)
    with pytest.raises(monitor.MonitorSourceReadError) as caught:
        monitor.saved_monitor_input(tmp_path, NOW)
    assert caught.value.reason == "PRIMARY_RUN_JSON_INVALID"
    assert "PRIVATE_SECRET" not in str(caught.value) and physical(path) == before


@pytest.mark.parametrize("changes,reason", [
    ({"snapshot_hash": "0" * 64}, "PRIMARY_RUN_HASH_MISMATCH"),
    ({"run_id": "wrong"}, "PRIMARY_RUN_IDENTITY_MISMATCH"),
    ({"observed_at": "2026-10-27T09:59:00"}, "PRIMARY_RUN_CLOCK_INVALID"),
    ({"observed_at": (NOW + timedelta(seconds=1)).isoformat()}, "PRIMARY_RUN_CLOCK_INVALID"),
])
def test_current_hash_identity_and_clock_fail_before_planning(tmp_path, changes, reason):
    path = seed(tmp_path, [(report(), changes)])
    before = physical(path)
    with pytest.raises(monitor.MonitorSourceReadError) as caught:
        monitor.saved_monitor_input(tmp_path, NOW)
    assert caught.value.reason == reason and physical(path) == before


def test_valid_source_fields_quotes_and_unknown_are_exact(tmp_path, isolated_io):
    original = report()
    path = seed(tmp_path, [(original, {})])
    before = physical(path)
    isolated_io[1].clear()
    value = monitor.saved_monitor_input(tmp_path, NOW)
    assert value["fixtures"] == original["fixtures"]
    assert value["recommendations"] == original["recommendations"]
    assert value["odds_history_snapshot"] == original["odds_history_snapshot"]
    assert value["fixtures"][0]["neutral_venue"] is None
    assert value["source_read"]["row_limit"] == 100
    assert all(uri and "mode=ro" in path for path, uri in isolated_io[1])
    assert physical(path) == before
    assert len(build_monitor_queries(value, NOW, {"monitor_providers": ["the_stats_api"]})) == 1


@pytest.mark.parametrize("minutes,count", [(14, 0), (15, 0), (16, 1), (2880, 1), (2881, 0)])
def test_original_lockout_and_two_day_window_unchanged(tmp_path, minutes, count):
    value = report(fixtures=[fixture(NOW + timedelta(minutes=minutes))])
    seed(tmp_path, [(value, {})])
    assert len(monitor.saved_monitor_input(tmp_path, NOW)["fixtures"]) == count


def test_irrelevant_old_JSON_does_not_veto_current_source(tmp_path):
    old = report(identifier="old")
    old["date"] = "2020-01-01"
    current = report()
    seed(tmp_path, [(old, {"snapshot_json": "MALFORMED_OLD_OUTSIDE_DECLARED_SCOPE"}), (current, {})])
    value = monitor.saved_monitor_input(tmp_path, NOW)
    assert value["fixtures"] == current["fixtures"]
    assert value["source_read"]["counts"]["rows_outside_date_scope"] == 1


def test_100_row_bound_and_deterministic_latest_tie(tmp_path):
    rows = []
    for index in range(101):
        item = report(identifier=f"run-{index}", observed=NOW - timedelta(seconds=101-index))
        rows.append((item, {"snapshot_json": "BAD_OUTSIDE_FIRST100"} if index == 0 else {}))
    seed(tmp_path, rows)
    result = monitor.saved_monitor_input(tmp_path, NOW)
    assert result["source_read"]["counts"]["rows_fetched"] == 100
    assert result["source_read"]["counts"]["runs_selected"] == 1


def test_latest_empty_retained_fallback_policy_is_preserved_and_labelled(tmp_path):
    old = report(identifier="retained", observed=NOW - timedelta(minutes=2))
    latest = report(identifier="latest", fixtures=[], observed=NOW - timedelta(minutes=1))
    seed(tmp_path, [(old, {}), (latest, {})])
    value = monitor.saved_monitor_input(tmp_path, NOW)
    assert value["fixtures"] == old["fixtures"]
    assert value["source_read"]["counts"]["empty_report_fallbacks"] == 1
    assert value["source_read"]["retained_empty_report_fallback_policy_unchanged"] is True


def test_retired_or_missing_optional_mapping_remains_planner_local(tmp_path):
    item = report()
    item["fixtures"][0]["provider_status"] = "FT"
    seed(tmp_path, [(item, {})])
    value = monitor.saved_monitor_input(tmp_path, NOW)
    assert value["fixtures"] == item["fixtures"]
    assert build_monitor_queries(value, NOW, {"monitor_providers": ["the_stats_api"]}) == []


def test_bad_current_fixture_clock_is_not_valid_zero(tmp_path):
    item = report()
    item["fixtures"][0]["kickoff"] = "2026-10-27T11:00:00"
    seed(tmp_path, [(item, {})])
    with pytest.raises(monitor.MonitorSourceReadError, match="PRIMARY_FIXTURE_CLOCK_INVALID"):
        monitor.saved_monitor_input(tmp_path, NOW)


def test_ROOT_rejected_before_any_fixture_constructor(isolated_io):
    before = list(isolated_io[1])
    with pytest.raises(AssertionError):
        owned(PROJECT / "data/arthur/ledger.sqlite3", isolated_io[0])
        sqlite3.connect(PROJECT / "data/arthur/ledger.sqlite3")
    assert isolated_io[1] == before


NATIVE_BOOTSTRAP = '''import contextlib, ctypes, io, json, runpy, socket, sqlite3, sys, urllib.request
from pathlib import Path
from unittest.mock import patch
script=Path(sys.argv[1]).resolve();root=script.parents[1]
assert (root/"MONITOR-SOURCE-TESTONLY.json").is_file()
runpy.run_path(str(script),run_name="actual_cli_definition_only")
from goal_analysis.jobs import odds_monitor, prospective_odds
from goal_analysis.jobs.the_stats_context import Resources
from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
from goal_analysis.storage import Database
from goal_analysis.storage.portfolio_ledger import PortfolioLedger
from goal_analysis.storage.temporal import TemporalStore
blocked=[];shadow=[];connect=sqlite3.connect
def deny(*a,**k):blocked.append("FORBIDDEN_IO");raise AssertionError("FORBIDDEN_IO")
def ro(path,*a,**k):
 assert str(path).startswith((root/"data/arthur/ledger.sqlite3").as_uri()) and "mode=ro" in str(path) and k.get("uri") is True
 return connect(path,*a,**k)
def optional(*a,**k):shadow.append(1);return {"status":"TESTONLY_OPTIONAL_DOUBLE","llm_calls":0}
stream=io.StringIO();code=0
with contextlib.ExitStack() as stack:
 for obj,name,value in [(sqlite3,"connect",ro),(socket.socket,"connect",deny),(socket,"create_connection",deny),(urllib.request,"urlopen",deny),(Resources,"__init__",deny),(TheStatsPrimaryClient,"__init__",deny),(Database,"__init__",deny),(PortfolioLedger,"__init__",deny),(TemporalStore,"capture",deny),(prospective_odds,"tick",optional)]:stack.enter_context(patch.object(obj,name,value))
 if hasattr(ctypes,"windll"):stack.enter_context(patch.object(ctypes.windll.crypt32,"CryptUnprotectData",deny))
 try:
  with contextlib.redirect_stdout(stream):runpy.run_path(str(script),run_name="__main__")
 except SystemExit as error:code=error.code if type(error.code) is int else 0 if error.code is None else 1
assert not blocked
print(json.dumps({"native_exit":code,"result":json.loads(stream.getvalue()),"optional_calls":len(shadow),"forbidden_attempts":blocked}))
sys.exit(code)
'''


@pytest.mark.parametrize("kind,native,shadow", [("corrupt", 1, 0), ("absent", 0, 1)])
def test_actual_copied_cli_native_exit_and_optional_boundary(tmp_path, kind, native, shadow):
    root = owned(tmp_path / "cli", tmp_path)
    root.mkdir()
    (root / MARKER).write_text((tmp_path / MARKER).read_text(), encoding="utf-8")
    script = root / "scripts/run-arthur-odds-monitor.py"
    script.parent.mkdir()
    shutil.copy2(PROJECT / "scripts/run-arthur-odds-monitor.py", script)
    source_count = source_bytes = 0
    for path in (PROJECT / "src").rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        source_count += 1
        source_bytes += path.stat().st_size
        assert source_count <= 5000 and source_bytes <= 64 * 1024 * 1024
        target = root / path.relative_to(PROJECT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    (root / "config").mkdir()
    shutil.copy2(PROJECT / "config/daily223-live.json", root / "config/daily223-live.json")
    if kind == "corrupt":
        path = root / "data/arthur/ledger.sqlite3"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"TESTONLY_INVALID_SQLITE")
    bootstrap = root / "native_cli_bootstrap.py"
    bootstrap.write_text(NATIVE_BOOTSTRAP, encoding="utf-8")
    import os
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", THE_STATS_API_KEY="", XAI_API_KEY="", OPENAI_API_KEY="", PYTHONPATH="")
    completed = subprocess.run([sys.executable, "-B", str(bootstrap), str(script)], cwd=root,
                               env=env, capture_output=True, text=True, timeout=45, check=False)
    assert completed.returncode == native, completed.stderr
    proof = json.loads(completed.stdout)
    assert proof["native_exit"] == native and proof["optional_calls"] == shadow
    assert proof["forbidden_attempts"] == [] and proof["result"]["provider_calls"] == 0
    assert proof["result"]["status"] == ("MONITOR_UNAVAILABLE" if kind == "corrupt" else "NO_UNLOCKED_FIXTURES")
    assert "PRIVATE_SECRET" not in completed.stdout
    if kind == "corrupt":
        assert proof["result"]["reason"] == "PRIMARY_LEDGER_UNREADABLE"
        assert proof["result"]["prospective_shadow"]["status"] == "SKIPPED_PRIMARY_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("value", [{}, "", 0, False])
def test_falsy_nonlist_current_fixtures_are_not_healthy_empty(tmp_path, value):
    item = report()
    item["fixtures"] = value
    path = seed(tmp_path, [(item, {})])
    before = physical(path)
    with pytest.raises(monitor.MonitorSourceReadError) as caught:
        monitor.saved_monitor_input(tmp_path, NOW)
    assert caught.value.reason == "PRIMARY_RUN_SHAPE_INVALID"
    assert physical(path) == before
