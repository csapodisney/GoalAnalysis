"""Retired entrypoints and honest monitor diagnostics, using owned local fixtures."""

import ast
import hashlib
import json
import re
import socket
import sqlite3
import subprocess
import urllib.request
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from goal_analysis.jobs.odds_monitor import OddsMonitor, monitor_history, monitor_status
from goal_analysis.jobs.odds_monitor_providers import (
    MonitorFetchError,
    _safe_error,
    fetch_monitor_query,
)
from goal_analysis.providers import the_stats_api
from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 29, 10, tzinfo=UTC)
SENTINEL = "TheStats odds: QUOTA_TRACKING_UNAVAILABLE"


@pytest.fixture(autouse=True)
def owned_io(tmp_path, monkeypatch):
    owned = tmp_path.resolve()
    assert not owned.is_relative_to(ROOT)
    assert not owned.is_relative_to(Path("C:/AI-Work/GoalAnalysis").resolve())
    original_connect = sqlite3.connect

    def connect(database, *args, **kwargs):
        if str(database).startswith("file:"):
            uri = urlsplit(str(database))
            assert uri.netloc in ("", "localhost"), "LOCAL_FILE_URI_REQUIRED"
            value = urllib.request.url2pathname(uri.path)
        else:
            assert "://" not in str(database), "FILE_URI_OR_OWNED_PATH_REQUIRED"
            value = database
        assert Path(value).resolve().is_relative_to(owned), "OWNED_TESTONLY_DB_REQUIRED"
        return original_connect(database, *args, **kwargs)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("NO_NETWORK_OR_PROCESS")

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(the_stats_api, "_default_transport", forbidden)


def query(identifier):
    return {"provider": "api_football", "query_key": identifier,
            "fixture_ids": [identifier],
            "kickoff_by_fixture": {identifier: (NOW + timedelta(hours=8)).isoformat()},
            "estimated_cost": 1}


def success(job):
    return {"quotes": [{"fixture_id": job["fixture_ids"][0], "decimal_price": 2.1,
                        "quoted_at": NOW.isoformat(), "quote_source_id": "TESTONLY"}],
            "actual_cost": 1, "remaining_quota": 100}


def fail(_job):
    raise MonitorFetchError(reason="QUOTA_TRACKING_UNAVAILABLE")


@pytest.mark.parametrize(("name", "original_sha", "replacement"), [
    ("install-daily223-control-panel.ps1",
     "3c27001b0bd9a6e77629ea966a0bd5bb8ed66350df40337ec17eb689d95839be",
     "scripts/install-arthur.ps1"),
    ("start-daily223-dashboard.ps1",
     "b9a456b43f7a941fc65482552c07de3e5288442eab60b3c8e197fa9b77865c66",
     "scripts/start-arthur.ps1"),
])
def test_retired_entrypoint_stops_before_exact_historical_body(name, original_sha, replacement):
    raw = (ROOT / "scripts" / name).read_bytes()
    body = raw.index(b'$ErrorActionPreference = "Stop"')
    prefix = raw[:body].decode("utf-8")
    statements = [line.strip() for line in prefix.splitlines()
                  if line.strip() and not line.lstrip().startswith("#")]
    assert len(statements) == 1
    assert re.fullmatch(r"throw (['\"]).*\1", statements[0])
    assert replacement in statements[0]
    assert hashlib.sha256(raw[body:]).hexdigest() == original_sha


@pytest.mark.parametrize(("message", "expected"), [
    (SENTINEL, "QUOTA_TRACKING_UNAVAILABLE"),
    ("prefix " + SENTINEL, "PROVIDER_UNAVAILABLE"),
    (SENTINEL + " suffix", "PROVIDER_UNAVAILABLE"),
    ("QUOTA_TRACKING_UNAVAILABLE", "PROVIDER_UNAVAILABLE"),
    ("TheStats odds: USAGE_LIMIT_EXCEEDED", "PROVIDER_UNAVAILABLE"),
    ("private https://provider.invalid/?key=never-log", "PROVIDER_UNAVAILABLE"),
])
def test_only_exact_quota_message_crosses_sanitized_boundary(message, expected):
    error = _safe_error(RuntimeError(message), NOW)
    assert error.reason == str(error) == expected
    assert "https://" not in str(error)


def test_all_failed_queries_remain_no_change_with_explicit_failure_diagnostic(tmp_path):
    result = OddsMonitor(tmp_path, clock=lambda: NOW).tick([query("a"), query("b")], fail)
    assert result["status"] == "NO_CHANGE"
    assert result["provider_calls"] == result["query_failure_count"] == 2
    assert result["updated_or_cached_query_count"] == 0
    assert result["query_failure_scope"] == "FETCH_FAILURE_WITHOUT_UPDATED_OR_CACHED_QUERY"
    assert all(row["status"] == "FETCH_FAILED" for row in result["queries"])
    assert monitor_history(tmp_path, NOW) == []


@pytest.mark.parametrize("cached", [False, True])
def test_failed_query_does_not_veto_another_updated_or_cached_query(tmp_path, cached):
    monitor = OddsMonitor(tmp_path, clock=lambda: NOW)
    if cached:
        monitor.tick([query("b")], success)
    calls = []

    def fetch(job):
        calls.append(job["query_key"])
        return fail(job) if job["query_key"] == "a" else success(job)

    result = monitor.tick([query("a"), query("b")], fetch)
    assert result["status"] == ("NO_CHANGE" if cached else "UPDATED")
    assert result["query_failure_count"] == result["updated_or_cached_query_count"] == 1
    assert result["query_failure_scope"] == "PARTIAL_FETCH_FAILURE"
    assert result["queries"][1]["status"] == ("CACHED" if cached else "UPDATED")
    assert calls == (["a"] if cached else ["a", "b"])


def test_failure_preserves_previous_quote_and_reason_clears_only_after_success(tmp_path):
    clock = [NOW]
    monitor = OddsMonitor(tmp_path, clock=lambda: clock[0])
    monitor.tick([query("a")], success)
    original = deepcopy(monitor_history(tmp_path, NOW))
    clock[0] += timedelta(minutes=31)
    monitor.tick([query("a")], fail)
    failed = monitor_status(tmp_path, clock[0])["queries"][0]
    assert failed["failure_reason"] == "QUOTA_TRACKING_UNAVAILABLE"
    assert failed["last_success_at"] == NOW.isoformat()
    assert monitor_history(tmp_path, clock[0]) == original
    assert monitor.tick([query("a")], success)["queries"][0]["status"] == "BACKOFF"
    assert monitor_status(tmp_path, clock[0])["queries"][0]["failure_reason"]
    clock[0] += timedelta(minutes=5)
    assert monitor.tick([query("a")], success)["status"] == "UPDATED"
    assert "failure_reason" not in monitor_status(tmp_path, clock[0])["queries"][0]


@pytest.mark.parametrize(("phase", "http_count"), [("before", 0), ("after", 1)])
def test_same_quota_reason_does_not_prove_zero_transport(tmp_path, phase, http_count):
    calls = []

    def transport(url, _headers, *, timeout):
        assert timeout > 0
        calls.append(url)
        return {"data": {}}, {}, 200

    def unavailable(*_args):
        raise RuntimeError("private quota persistence path must not escape")

    if phase == "before":
        activation = tmp_path / "data/arthur/historical-background/activation.json"
        activation.parent.mkdir(parents=True)
        activation.write_text(json.dumps({"version": "INVALID"}), encoding="utf-8")
        observer = None
    else:
        observer = SimpleNamespace(begin=lambda: "TESTONLY-claim", finish=unavailable)
    client = TheStatsPrimaryClient(tmp_path, api_key="TESTONLY-not-a-live-key", transport=transport,
                                   quota_observer=observer, clock=lambda: NOW)
    fixture = {"fixture_id": "the_stats_api:mt_1", "source_provider": "the_stats_api",
               "provider_fixture_id": "mt_1", "provider_league_id": "comp_1",
               "provider_season_id": "sn_1", "provider_home_team_id": "tm_1",
               "provider_away_team_id": "tm_2", "provider_status": "NS",
               "sport_key": "soccer_the_stats_comp_1", "home_team": "Home", "away_team": "Away",
               "kickoff": (NOW + timedelta(hours=8)).isoformat()}
    planned = {"provider": "the_stats_api", "fixtures": [fixture],
               "params": {"match_id": "mt_1", "markets": ["h2h"]},
               "normalization_config": {"odds_region": "eu"}}
    with pytest.raises(MonitorFetchError, match="^QUOTA_TRACKING_UNAVAILABLE$") as caught:
        fetch_monitor_query(planned, football_client=client, now=NOW)
    assert caught.value.reason == "QUOTA_TRACKING_UNAVAILABLE"
    assert len(calls) == client.request_count == client.authenticated_request_count == http_count
    assert not (tmp_path / "data/arthur/the-stats-quota.sqlite3").exists()


def test_primary_cli_classifier_and_a06_source_reader_are_not_replaced_by_visibility():
    source = (ROOT / "scripts/run-arthur-odds-monitor.py").read_bytes()
    assert hashlib.sha256(source).hexdigest() == (
        "2550028ff794082beb7b894f4294add5ce8f818e6f1957d514956f63528b7e4d")
    node = next(item for item in ast.parse(source).body
                if isinstance(item, ast.FunctionDef) and item.name == "process_exit_code")
    result = node.body[-1].value
    assert isinstance(result, ast.IfExp)
    assert isinstance(result.body, ast.Constant) and result.body.value == 0
    assert isinstance(result.orelse, ast.Constant) and result.orelse.value == 1
    expected_gate = ast.parse(
        'isinstance(result, dict) and isinstance(result.get("status"), str) '
        'and result.get("status") in {"UPDATED", "NO_CHANGE", "NO_UNLOCKED_FIXTURES"}',
        mode="eval",
    ).body
    assert ast.dump(result.test, include_attributes=False) == ast.dump(
        expected_gate, include_attributes=False,
    )
    assert b'SKIPPED_PRIMARY_SOURCE_UNAVAILABLE' in source
