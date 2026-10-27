"""Prepared, unrun public controls; owned external fixtures, no real provider/run.

Every timestamp is a controlled unit timeline, not actual UTC/PIT availability.
"""
import json
import socket
import sqlite3
import subprocess
import urllib.request
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

import goal_analysis
from goal_analysis.jobs.portfolio import RunLock
from goal_analysis.jobs.sports_context_archive import make_weather_provider
from goal_analysis.providers.open_meteo import OpenMeteoForecast
from goal_analysis.storage.db import Database
from goal_analysis.storage.sports_weather import WeatherStore
from goal_analysis.storage.weather_availability import WeatherCompletion

NOW = datetime(2026, 10, 28, 10, tzinfo=UTC)
PRODUCTION = Path(goal_analysis.__file__).resolve().parents[2]


def guarded_fixture_root(value):
    value = Path(value).resolve()
    if value.is_relative_to(PRODUCTION) or PRODUCTION.is_relative_to(value):
        raise ValueError("TESTONLY_FIXTURE_MUST_BE_OUTSIDE_PRODUCTION_BEFORE_CONSTRUCTOR")
    return value


@pytest.fixture(autouse=True)
def isolated_io(monkeypatch):
    allowed = set()
    original = sqlite3.connect

    def connect(value, *args, **kwargs):
        text = str(value)
        path = unquote(urlsplit(text).path) if text.startswith("file:") else text
        if len(path) > 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        path = Path(path).resolve()
        assert any(path.is_relative_to(root) and (root / "OWNED_TESTONLY.json").is_file() for root in allowed), "OWNED_EXTERNAL_TESTONLY_SQLITE_ONLY"
        return original(value, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("NO_REAL_NETWORK_PROCESS_PROVIDER_OR_MODEL")

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    from goal_analysis import models
    from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
    monkeypatch.setattr(models, "predict_models", forbidden)
    monkeypatch.setattr(TheStatsPrimaryClient, "__init__", forbidden)
    return allowed


@pytest.fixture
def root(tmp_path_factory, isolated_io):
    root = guarded_fixture_root(tmp_path_factory.mktemp("wa"))
    (root / "OWNED_TESTONLY.json").write_text(json.dumps({"TESTONLY": True,
        "scope": "current-normal-weather-provenance-consumer-gap",
        "timeline": "CONTROLLED_UNIT_NOT_ACTUAL_NOW_PIT_OR_FORECAST"}), encoding="utf-8")
    isolated_io.add(root)
    return root


class Clock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        self.value += timedelta(milliseconds=100)
        return self.value


def fixture(number=1, hour=16):
    return {"fixture_id": f"the_stats_api:mt_990000{number}", "source_provider": "the_stats_api",
        "provider_fixture_id": f"mt_990000{number}", "provider_league_id": "comp_990001",
        "provider_season_id": "sn_990001", "provider_home_team_id": "tm_990001",
        "provider_away_team_id": "tm_990002", "home_team_id": "the_stats_api:tm_990001",
        "away_team_id": "the_stats_api:tm_990002", "league_id": "the_stats_api:comp_990001",
        "home_team": "TESTONLY Home", "away_team": "TESTONLY Away", "venue_name": "TESTONLY Stadium",
        "venue_city": "Berlin", "venue_country": "Germany", "kickoff": NOW.replace(hour=hour).isoformat()}


def payload():
    return {"latitude": 52.49, "longitude": 13.39, "utc_offset_seconds": 0,
        "hourly": {"time": ["2026-10-28T16:00", "2026-10-28T18:00"], "temperature_2m": [12, 13],
            "precipitation_probability": [25, 30], "precipitation": [.2, .3], "weather_code": [2, 3],
            "wind_speed_10m": [10, 11], "wind_gusts_10m": [15, 16], "relative_humidity_2m": [60, 61]},
        "hourly_units": {"temperature_2m": "°C", "precipitation_probability": "%", "precipitation": "mm",
            "weather_code": "wmo code", "wind_speed_10m": "km/h", "wind_gusts_10m": "km/h", "relative_humidity_2m": "%"},
        "unmodeled_provider_field_retained": {"source_role": "TESTONLY_FULL_BODY"}}


def transport(body, calls):
    def fetch(url):
        calls.append(url)
        if "geocoding-api" in url:
            return {"results": [{"name": "Berlin", "country": "Germany", "latitude": 52.5, "longitude": 13.4}]}
        return deepcopy(body)
    return fetch


def store(root):
    db = Database(root / "data/goal_analysis.sqlite3")
    db.initialize()
    archive = WeatherStore(db)
    archive.initialize()
    return archive


def records(root, table):
    with sqlite3.connect(root / "data/goal_analysis.sqlite3") as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return []
        return [dict(row) for row in db.execute('SELECT * FROM "' + table + '"')]


def event(completion, clock, target=None, body=None):
    events, calls = [], []
    provider = OpenMeteoForecast(completion.transport(transport(body or payload(), calls)), clock=clock,
        recorder=lambda f, w, c: events.append((f, w, c)))
    provider.forecast(target or fixture(), coordinates=(52.5, 13.4))
    return provider, events, calls


def _record_event(completion, archive, recorded_event):
    fixture_value, weather_value, context_value = recorded_event
    return completion.record(archive, weather_value, fixture_value, context_value)


def test_fixture_root_guard_rejects_production_before_any_constructor():
    with pytest.raises(ValueError, match="BEFORE_CONSTRUCTOR"):
        guarded_fixture_root(PRODUCTION)


def test_factory_construction_has_no_schema_or_store_io(root):
    with RunLock(root / "data/arthur/run.lock") as lock:
        make_weather_provider(root, "TESTONLY-run", clock=Clock(), transport=transport(payload(), []), root_run_lock=lock)
        assert not (root / "data/goal_analysis.sqlite3").exists()


def test_real_normal_factory_new_snapshot_one_receipt_and_unchanged_strict_forecast_reader(root):
    from goal_analysis.jobs.native_weather_shadow import _forecast

    clock, calls = Clock(), []
    target = fixture()
    with RunLock(root / "data/arthur/run.lock") as lock:
        provider = make_weather_provider(root, "TESTONLY-normal", clock=clock,
            transport=transport(payload(), calls), root_run_lock=lock)
        weather = provider.forecast(target)
        assert weather["status"] == "SOURCED"
        assert weather["availability_status"]["status"] == "NEW_COMMITTED_SOURCE_RECEIPT_READ_BACK"
        receipts = records(root, "temporal_receipts")
        assert len(receipts) == 1 and receipts[0]["namespace"] == "weather"
        assert datetime.fromisoformat(receipts[0]["available_at"]) > datetime.fromisoformat(weather["observed_at"])
        assert weather["provider_issue_time"] is None
        with sqlite3.connect(root / "data/goal_analysis.sqlite3") as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            row = dict(db.execute("SELECT * FROM sports_weather_snapshots").fetchone())
            value, witness = _forecast(row, target, clock(), db)
        assert witness["receipt_id"] == receipts[0]["receipt_id"]
        assert value["temperature_c"] == 12
        stored = json.loads(row["snapshot_json"])
        assert stored["weather"]["precipitation_probability_pct"] == 25
        raw = stored["raw_payload"]
        selected = raw["hourly"]["time"].index("2026-10-28T16:00")
        assert raw["hourly"]["precipitation_probability"][selected] == 25
        assert raw["hourly_units"]["precipitation_probability"] == "%"
        assert datetime.fromisoformat(raw["hourly"]["time"][selected]).replace(tzinfo=UTC) == datetime.fromisoformat(row["target_time"])
        from goal_analysis.storage.canonical_registry import digest
        from goal_analysis.storage.temporal import _source_hash
        assert receipts[0]["source_key"] == row["snapshot_id"]
        assert receipts[0]["source_sha256"] == _source_hash(row)
        assert receipts[0]["receipt_id"] == digest(["weather", row["snapshot_id"], _source_hash(row)])
        assert row["raw_payload_hash"] == weather["raw_payload_hash"]
        assert len([url for url in calls if "/v1/forecast" in url]) == 1


def test_cached_current_response_two_fixtures_keeps_original_observed_and_one_transport(root):
    archive, clock = store(root), Clock()
    with RunLock(root / "data/arthur/run.lock") as lock:
        cap = WeatherCompletion(root, "TESTONLY-cache", lock, clock=clock)
        provider, events, calls = event(cap, clock)
        first, a = _record_event(cap, archive, events[-1])
        provider.forecast(fixture(2, 18), coordinates=(52.5, 13.4))
        second, b = _record_event(cap, archive, events[-1])
        assert first["snapshot_id"] != second["snapshot_id"] and a["created"] is b["created"] is True
        rows = records(root, "sports_weather_snapshots")
        assert len(rows) == 2 and rows[0]["observed_at"] == rows[1]["observed_at"]
        assert len(calls) == 1 and len(records(root, "temporal_receipts")) == 2
        assert events[-1][1]["temperature_c"] == 13


def test_exact_repeat_and_later_run_reuse_original_receipt_without_new_time(root):
    archive, clock = store(root), Clock()
    with RunLock(root / "data/arthur/run.lock") as lock:
        first = WeatherCompletion(root, "TESTONLY-first", lock, clock=clock)
        _provider, events, _calls = event(first, clock)
        saved, _ = _record_event(first, archive, events[-1])
        before_row, before_receipt = records(root, "sports_weather_snapshots"), records(root, "temporal_receipts")
        later = WeatherCompletion(root, "TESTONLY-later", lock, clock=clock)
        repeated, reuse = _record_event(later, archive, events[-1])
        assert repeated["snapshot_id"] == saved["snapshot_id"] and not repeated["inserted"]
        assert reuse["status"] == "REUSED_EXISTING_SOURCE_RECEIPT" and not reuse["new_time_assigned"]
        assert records(root, "sports_weather_snapshots") == before_row
        assert records(root, "temporal_receipts") == before_receipt
        assert len(records(root, "sports_weather_source_runs")) == 2


def test_retained_unwitnessed_row_adds_no_receipt_or_lazy_schema(root):
    archive, clock = store(root), Clock()
    with RunLock(root / "data/arthur/run.lock") as lock:
        original = WeatherCompletion(root, "TESTONLY-original", lock, clock=clock)
        _provider, events, _calls = event(original, clock)
        f, weather, context = events[-1]
        archive.ingest(weather, f, source_run_id="TESTONLY-old", raw_payload=context["raw_payload"])
        before = records(root, "sports_weather_snapshots")
        later = WeatherCompletion(root, "TESTONLY-new-run", lock, clock=clock)
        saved, gap = later.record(archive, weather, f, context)
        assert not saved["inserted"] and gap["reason"] == "RETAINED_UNWITNESSED_ROW_NOT_ADMITTED"
        assert records(root, "temporal_receipts") == [] and records(root, "sports_weather_snapshots") == before
        with sqlite3.connect(archive.database.path) as db:
            assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='temporal_receipts'").fetchone()


def test_unknown_null_target_has_no_temporal_read_or_schema(root, monkeypatch):
    archive, clock = store(root), Clock()
    with RunLock(root / "data/arthur/run.lock") as lock:
        cap = WeatherCompletion(root, "TESTONLY-unknown", lock, clock=clock)
        monkeypatch.setattr(cap, "_read", lambda *args: pytest.fail("UNKNOWN_MUST_NOT_READ_TEMPORAL"))
        weather = {"fixture_id": fixture()["fixture_id"], "provider": "open_meteo", "status": "UNKNOWN",
            "weather_kind": "FORECAST", "observed_at": clock().isoformat(), "forecast_time": None,
            "kickoff": fixture()["kickoff"], "time_basis": "LOOKUP_ATTEMPT_AT"}
        saved, gap = cap.record(archive, weather, fixture(), {"raw_payload": None})
        assert saved["inserted"] and gap["status"] == "NOT_SOURCED_WEATHER"
        assert records(root, "temporal_receipts") == []


@pytest.mark.parametrize("broken", ["probability_unit", "probability_value", "utc", "duplicate_hour", "grid", "target"])
def test_bad_full_source_never_gains_receipt_but_keeps_optional_forecast(root, broken):
    body = payload()
    if broken == "probability_unit": body["hourly_units"]["precipitation_probability"] = "fraction"
    if broken == "utc": body["utc_offset_seconds"] = 3600
    if broken == "duplicate_hour": body["hourly"]["time"][1] = body["hourly"]["time"][0]
    if broken == "grid": body["latitude"] = 1000
    clock, archive = Clock(), store(root)
    with RunLock(root / "data/arthur/run.lock") as lock:
        cap = WeatherCompletion(root, "TESTONLY-invalid", lock, clock=clock)
        _provider, events, _calls = event(cap, clock, body=body)
        f, weather, context = events[-1]
        if broken == "probability_value": weather["precipitation_probability_pct"] = 99
        if broken == "target": weather["forecast_time"] = NOW.replace(hour=17).isoformat()
        saved, gap = cap.record(archive, weather, f, context)
        assert saved["inserted"] and weather["status"] == "SOURCED"
        assert gap["created"] is False and records(root, "temporal_receipts") == []
        assert len(records(root, "sports_weather_snapshots")) == 1


def test_receipt_precommit_failure_preserves_source_and_sanitizes_error(root):
    from goal_analysis.storage.temporal import TemporalStore

    archive, clock = store(root), Clock()
    TemporalStore(archive.database, root / "data/raw").initialize()  # Fixture schema only; capture NEVER called.
    with sqlite3.connect(archive.database.path) as db:
        db.execute("CREATE TRIGGER block_fixture_receipt BEFORE INSERT ON temporal_receipts BEGIN SELECT RAISE(ABORT,'SECRET_fixture'); END")
    with RunLock(root / "data/arthur/run.lock") as lock:
        cap = WeatherCompletion(root, "TESTONLY-failed", lock, clock=clock)
        _provider, events, _calls = event(cap, clock)
        saved, gap = _record_event(cap, archive, events[-1])
        assert saved["inserted"] and gap["status"] == "NO_RECEIPT_ADMISSION_NO_RETRY"
        assert len(records(root, "sports_weather_snapshots")) == 1 and records(root, "temporal_receipts") == []
        assert "SECRET" not in json.dumps(gap)


def test_postcommit_readback_failure_is_partial_without_source_or_receipt_rollback(root):
    class FailReadback(WeatherCompletion):
        calls = 0
        def _read(self, key, deadline):
            self.calls += 1
            if self.calls == 2:
                raise OSError("SECRET_after_commit")
            return super()._read(key, deadline)

    archive, clock = store(root), Clock()
    with RunLock(root / "data/arthur/run.lock") as lock:
        cap = FailReadback(root, "TESTONLY-postcommit", lock, clock=clock)
        _provider, events, _calls = event(cap, clock)
        saved, gap = _record_event(cap, archive, events[-1])
        assert saved["inserted"] and gap["status"] == "PARTIAL_RECEIPT_COMMIT_UNPROVEN_NO_RETRY"
        assert gap["created"] is None and not gap["automatic_retry"]
        assert len(records(root, "temporal_receipts")) == len(records(root, "sports_weather_snapshots")) == 1
        assert "SECRET" not in json.dumps(gap)


def test_committed_run_link_cas_mismatch_writes_no_receipt_and_keeps_source(root):
    class ConcurrentRunLinkChange(WeatherCompletion):
        def _read(self, key, deadline):
            value = super()._read(key, deadline)
            if not getattr(self, "injected", False):
                self.injected = True
                # Deliberately simulate an out-of-band TESTONLY concurrent
                # change after committed read; never production source repair.
                with sqlite3.connect(self.path) as db:
                    db.execute("DROP TRIGGER sports_weather_source_runs_no_delete")
                    db.execute("DELETE FROM sports_weather_source_runs WHERE snapshot_id=? AND source_run_id=?", (key, self.run_id))
            return value

    archive, clock = store(root), Clock()
    with RunLock(root / "data/arthur/run.lock") as lock:
        cap = ConcurrentRunLinkChange(root, "TESTONLY-cas", lock, clock=clock)
        _provider, events, _calls = event(cap, clock)
        saved, gap = _record_event(cap, archive, events[-1])
        assert saved["inserted"] and gap["reason"] == "WEATHER_SOURCE_OR_RUN_CAS_MISMATCH"
        assert records(root, "temporal_receipts") == []
        assert len(records(root, "sports_weather_snapshots")) == 1


@pytest.mark.parametrize("broken", ["backward", "past_prematch_cutoff"])
def test_clock_failure_never_copies_observed_into_a_new_receipt(root, broken):
    archive, clock = store(root), Clock()
    with RunLock(root / "data/arthur/run.lock") as lock:
        cap = WeatherCompletion(root, "TESTONLY-clock", lock, clock=clock)
        _provider, events, _calls = event(cap, clock)
        observed = events[-1][1]["observed_at"]
        clock.value = NOW - timedelta(minutes=1) if broken == "backward" else NOW + timedelta(hours=8)
        saved, gap = _record_event(cap, archive, events[-1])
        assert saved["inserted"] and gap["created"] is False
        assert records(root, "temporal_receipts") == []
        assert events[-1][1]["observed_at"] == observed


@pytest.mark.parametrize(("kickoff", "expected"), [("2026-10-28T18:29:00+02:00", "2026-10-28T16:00:00+00:00"),
    ("2026-10-28T18:30:00+02:00", "2026-10-28T17:00:00+00:00"),
    ("2026-10-28T23:45:00+00:00", "2026-10-29T00:00:00+00:00")])
def test_aware_offset_and_midnight_rounding_keep_correct_utc_request_day(kickoff, expected):
    body, calls = payload(), []
    target = datetime.fromisoformat(expected)
    body["hourly"]["time"] = [target.strftime("%Y-%m-%dT%H:%M")]
    for key, values in body["hourly"].items():
        if key != "time": body["hourly"][key] = values[:1]
    provider = OpenMeteoForecast(transport(body, calls), clock=lambda: NOW)
    weather = provider.forecast({**fixture(), "kickoff": kickoff}, coordinates=(52.5, 13.4))
    assert weather["forecast_time"] == expected
    assert "start_date=" + target.date().isoformat() in calls[0] and "timezone=UTC" in calls[0]


def test_large_double_response_cannot_mint_live_origin_beyond_existing_500k_budget(root):
    body, archive, clock = payload(), store(root), Clock()
    body["oversized_TESTONLY_extra"] = "x" * 500_000
    with RunLock(root / "data/arthur/run.lock") as lock:
        cap = WeatherCompletion(root, "TESTONLY-bound", lock, clock=clock)
        _provider, events, _calls = event(cap, clock, body=body)
        saved, gap = _record_event(cap, archive, events[-1])
        assert saved["inserted"] and gap["created"] is False
        assert records(root, "temporal_receipts") == []


@pytest.mark.parametrize("schema", ["missing_pk", "wrong_columns"])
def test_existing_corrupt_receipt_schema_is_not_absent_or_auto_repaired(root, schema):
    archive, clock = store(root), Clock()
    sql = "CREATE TABLE temporal_receipts(receipt_id TEXT,namespace TEXT)" if schema == "wrong_columns" else "CREATE TABLE temporal_receipts(receipt_id TEXT,namespace TEXT,source_key TEXT,source_sha256 TEXT,observed_at TEXT,published_at TEXT,ingested_at TEXT,available_at TEXT,times_json TEXT)"
    with sqlite3.connect(archive.database.path) as db:
        db.execute(sql)
        before = db.execute("SELECT sql FROM sqlite_master WHERE name='temporal_receipts'").fetchone()[0]
    with RunLock(root / "data/arthur/run.lock") as lock:
        cap = WeatherCompletion(root, "TESTONLY-corrupt", lock, clock=clock)
        _provider, events, _calls = event(cap, clock)
        saved, gap = _record_event(cap, archive, events[-1])
        assert saved["inserted"] and not gap["created"]
    with sqlite3.connect(archive.database.path) as db:
        assert db.execute("SELECT sql FROM sqlite_master WHERE name='temporal_receipts'").fetchone()[0] == before
        assert db.execute("SELECT count(*) FROM temporal_receipts").fetchone()[0] == 0


@pytest.mark.parametrize("kickoff", ["2026-10-28T16:00:00", "invalid"])
def test_naive_or_invalid_clock_rejects_before_standalone_transport(kickoff):
    calls = []
    provider = OpenMeteoForecast(transport(payload(), calls))
    with pytest.raises(ValueError):
        provider.forecast({**fixture(), "kickoff": kickoff})
    assert calls == []


def test_closed_or_foreign_lock_is_rejected_without_store_constructor(root):
    lock = RunLock(root / "data/arthur/run.lock")
    with pytest.raises(ValueError, match="HELD_SAME_ROOT"):
        WeatherCompletion(root, "TESTONLY", lock, clock=Clock())
    assert not (root / "data/goal_analysis.sqlite3").exists()


def test_legacy_uncertified_factory_keeps_archival_behavior_without_receipt(root):
    provider = make_weather_provider(root, "TESTONLY-legacy", clock=Clock(), transport=transport(payload(), []))
    weather = provider.forecast({**fixture(), "fixture_id": "api_football:990001", "source_provider": "api_football"})
    assert weather["status"] == "SOURCED" and weather["archive_status"]["status"] == "ARCHIVED"
    assert "availability_status" not in weather and records(root, "temporal_receipts") == []


@pytest.mark.parametrize("broken", ["availability_before_observed", "times_json_conflict"])
def test_malformed_prior_receipt_is_not_healthy_reuse_or_repaired(root, broken):
    archive, clock = store(root), Clock()
    with RunLock(root / "data/arthur/run.lock") as lock:
        cap = WeatherCompletion(root, "TESTONLY-prior", lock, clock=clock)
        _provider, events, _calls = event(cap, clock)
        _record_event(cap, archive, events[-1])
        prior = records(root, "temporal_receipts")[0]
        with sqlite3.connect(archive.database.path) as db:
            db.execute("DROP TRIGGER temporal_receipts_no_update")
            if broken == "availability_before_observed":
                bad = (datetime.fromisoformat(prior["observed_at"]) - timedelta(seconds=1)).isoformat(timespec="microseconds")
                db.execute("UPDATE temporal_receipts SET available_at=?", (bad,))
            else:
                db.execute("UPDATE temporal_receipts SET times_json='{}'")
            db.execute("CREATE TRIGGER temporal_receipts_no_update BEFORE UPDATE ON temporal_receipts BEGIN SELECT RAISE(ABORT,'temporal receipts are append-only'); END")
        before = records(root, "temporal_receipts")
        later = WeatherCompletion(root, "TESTONLY-later-invalid", lock, clock=clock)
        saved, gap = _record_event(later, archive, events[-1])
        assert not saved["inserted"] and gap["status"] == "NO_RECEIPT_ADMISSION_NO_RETRY"
        assert gap["created"] is False and records(root, "temporal_receipts") == before


def test_new_physical_weather_enters_actual_offline_review_wire_without_probability_effect(root, monkeypatch):
    from goal_analysis.agents import astra_review, canonical_sha256
    from goal_analysis.agents.astra_review import AstraReviewer, _wire_json
    from goal_analysis.jobs.candidate_context_review import CATEGORIES, VERSION, _row

    clock, archive = Clock(), store(root)
    target = fixture()
    with RunLock(root / "data/arthur/run.lock") as lock:
        cap = WeatherCompletion(root, "TESTONLY-wire", lock, clock=clock)
        _provider, events, _calls = event(cap, clock, target=target)
        saved, availability = _record_event(cap, archive, events[-1])
        weather = {**events[-1][1], "availability_status": availability,
            "archive_status": {"snapshot_id": saved["snapshot_id"], "source_run_id": cap.run_id}}
        cutoff = clock().isoformat()
        category = _row("weather", weather, sources=[weather["source_url"]],
            fixture_id=target["fixture_id"], cutoff=cutoff)
        categories = [category if name == "weather" else _row(name,
            reason="TESTONLY_OPTIONAL_CATEGORY_NOT_SUPPLIED", fixture_id=target["fixture_id"], cutoff=cutoff)
            for name in CATEGORIES]
        context = {"version": VERSION, "fixture_id": target["fixture_id"], "as_of": cutoff, "categories": categories}
        context["context_id"] = canonical_sha256(context)
        packet = {"candidate_id": "TESTONLY-weather-wire", "fixture_id": target["fixture_id"],
            "home_team": target["home_team"], "away_team": target["away_team"], "market_key": "h2h",
            "selection_key": "home", "decimal_price": 2.1, "quoted_at": None, "support_score": 72,
            "evidence": [{"evidence_id": category["evidence_id"], "source_url": weather["source_url"]}],
            "evidence_ids": [category["evidence_id"]], "historical_context": {},
            "weather_forecast": weather, "decision_context": context}
        # Unit-only nonfuture clock relative to this controlled timeline; not actual NOW/PIT.
        monkeypatch.setattr(astra_review, "_utcnow", clock)
        prepared, _metadata = object.__new__(AstraReviewer)._prepare([packet], NOW.date().isoformat(), {})
        wire = _wire_json(prepared)
        assert availability["receipt_id"] in wire and weather["raw_payload_hash"] in wire
        assert category["evidence_id"] in wire
        assert "weather_model_probability" not in weather
        # This is an untransmitted unit wire, never an actual LLM response,
        # considered/cited consumer claim, challenger, wager or profit proof.


@pytest.mark.parametrize("broken", ["native_fixture", "context_url", "context_hash"])
def test_explicit_native_or_completion_context_contradiction_has_no_receipt(root, broken):
    archive, clock = store(root), Clock()
    with RunLock(root / "data/arthur/run.lock") as lock:
        cap = WeatherCompletion(root, "TESTONLY-context", lock, clock=clock)
        _provider, events, _calls = event(cap, clock)
        target, weather, context = deepcopy(events[-1])
        if broken == "native_fixture": target["provider_fixture_id"] = "mt_9900099"
        if broken == "context_url": context["source_url"] += "&contradiction=1"
        if broken == "context_hash": context["raw_payload_hash"] = "0" * 64
        saved, gap = cap.record(archive, weather, target, context)
        assert saved["inserted"] and gap["created"] is False
        assert records(root, "temporal_receipts") == []
