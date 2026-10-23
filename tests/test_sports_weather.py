import hashlib
import json
import sqlite3
from datetime import UTC, datetime

import pytest

from goal_analysis.storage.db import Database
from goal_analysis.storage.sports_weather import WeatherStore


@pytest.fixture
def store(tmp_path):
    result = WeatherStore(Database(tmp_path / "weather.sqlite3"))
    result.initialize()
    return result


def fixture():
    return {"fixture_id": "api_football:123", "kickoff": "2026-10-18T18:30:00+00:00",
            "venue_name": "Stadium", "venue_city": "Berlin", "venue_country": "Germany"}


def weather(**changes):
    result = {
        "fixture_id": "api_football:123", "provider": "open_meteo", "status": "SOURCED",
        "weather_kind": "FORECAST", "observed_at": "2026-10-18T10:00:00+00:00",
        "provider_issue_time": None, "forecast_time": "2026-10-18T19:00:00+00:00",
        "latitude": 52.52, "longitude": 13.4, "temperature_c": 14.2,
        "precipitation_probability_pct": 60, "precipitation_mm": 0.5,
        "wind_speed_kmh": 18, "wind_gusts_kmh": 30, "weather_code": 61,
        "source_url": "https://api.open-meteo.com/v1/forecast?latitude=52.52",
    }
    return result | changes


def test_full_values_payload_and_provenance_roundtrip_and_repeat_idempotent(store):
    raw = {"hourly": {"time": ["2026-10-18T19:00"], "temperature_2m": [14.2]},
           "unknown_future_field": [1, None, "value"]}
    first = store.ingest(weather(), fixture(), source_run_id="run1", raw_payload=raw)
    second = store.ingest(weather(), fixture(), source_run_id="run2", raw_payload=raw)
    assert first["inserted"] is True
    assert second["inserted"] is False
    assert first["snapshot_id"] == second["snapshot_id"]
    rows = store.as_of("2026-10-18T10:00:00Z")
    assert len(rows) == 1
    assert rows[0]["weather"] == weather()
    assert rows[0]["fixture"] == fixture()
    assert rows[0]["raw_payload"] == raw
    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert rows[0]["raw_payload_hash"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert rows[0]["provider_issue_time"] is None
    assert store.summary()["source_run_links"] == 2
    raw["hourly"]["temperature_2m"][0] = 999
    assert store.as_of("2026-10-18T10:00:00Z")[0]["raw_payload"] != raw


def test_asof_excludes_later_retrievals_and_keeps_forecast_observation_separate(store):
    store.ingest(weather(), fixture(), source_run_id="old")
    store.ingest(weather(observed_at="2026-10-18T11:00:00Z", temperature_c=15),
                 fixture(), source_run_id="new")
    store.ingest(weather(weather_kind="OBSERVATION", observed_at="2026-10-18T20:00:00Z"),
                 fixture(), source_run_id="actual")
    assert store.as_of("2026-10-18T09:59:59Z") == []
    assert len(store.as_of("2026-10-18T10:30:00Z")) == 1
    future = store.as_of("2026-10-19T00:00:00Z")
    assert len(future) == 2
    assert future[0]["weather"]["temperature_c"] == 15
    actual = store.as_of("2026-10-19T00:00:00Z", weather_kind="OBSERVATION")
    assert len(actual) == 1
    assert actual[0]["weather_kind"] == "OBSERVATION"
    assert store.as_of("2026-10-19T00:00:00Z", fixture_id="other") == []


def test_legacy_timestamp_must_be_explicit_and_original(store):
    legacy = weather()
    legacy.pop("observed_at")
    with pytest.raises(ValueError, match="observed_at"):
        store.ingest(legacy, fixture(), source_run_id="old")
    result = store.ingest(legacy, fixture(), source_run_id="old",
                          observed_at=datetime(2026, 10, 18, 8, tzinfo=UTC))
    assert result["observed_at"] == "2026-10-18T08:00:00.000000+00:00"
    with pytest.raises(ValueError, match="disagrees"):
        store.ingest(weather(), fixture(), source_run_id="old",
                     observed_at="2026-10-18T08:00:00Z")


@pytest.mark.parametrize("changes,reason", [
    ({"fixture_id": "wrong"}, "fixture_id"),
    ({"weather_kind": None}, "weather_kind"),
    ({"observed_at": "2026-10-18T10:00:00"}, "timezone-aware"),
    ({"provider_issue_time": "2026-10-18T11:00:00Z"}, "issue time"),
    ({"weather_kind": "OBSERVATION"}, "observation target"),
    ({"forecast_time": None}, "requires forecast_time"),
    ({"latitude": 91}, "latitude"),
    ({"longitude": True}, "longitude"),
    ({"temperature_c": float("nan")}, "finite JSON"),
    ({"kickoff": "2026-10-19T18:00:00Z"}, "kickoff"),
])
def test_invalid_metadata_cannot_enter_archive(store, changes, reason):
    with pytest.raises(ValueError, match=reason):
        store.ingest(weather(**changes), fixture(), source_run_id="bad")
    assert store.summary()["snapshot_count"] == 0


def test_raw_hash_must_match_payload(store):
    with pytest.raises(ValueError, match="hash mismatch"):
        store.ingest(weather(raw_payload_hash="0" * 64), fixture(), source_run_id="bad",
                     raw_payload={"hourly": {}})


def test_unknown_weather_is_retained_without_inventing_payload_or_target(store):
    unknown = weather(status="UNKNOWN", forecast_time=None, source_url=None)
    store.ingest(unknown, fixture(), source_run_id="unknown")
    row = store.as_of("2026-10-18T10:00:00Z")[0]
    assert row["target_time"] is None
    assert row["raw_payload"] is None
    assert row["raw_payload_hash"] is None


@pytest.mark.parametrize("table", ["sports_weather_snapshots", "sports_weather_source_runs"])
@pytest.mark.parametrize("operation", ["DELETE FROM", "UPDATE"])
def test_history_is_immutable(store, table, operation):
    store.ingest(weather(), fixture(), source_run_id="run")
    sql = f"{operation} {table}"
    if operation == "UPDATE":
        sql += " SET snapshot_id='changed'"
    with pytest.raises(sqlite3.IntegrityError, match="immutable"), store.database.transaction() as db:
        db.execute(sql)
    assert store.summary()["snapshot_count"] == 1


def test_only_weather_tables_created_and_initialize_is_repeatable(store):
    store.initialize()
    with store.database.connect() as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {"sports_weather_snapshots", "sports_weather_source_runs"}


def test_reanalysis_remains_distinct_from_station_observations_and_forecasts(store):
    store.ingest(weather(weather_kind="REANALYSIS", observed_at="2026-10-25T10:00:00Z"),
                 fixture(), source_run_id="historical")
    assert store.as_of("2026-10-26T00:00:00Z") == []
    assert store.as_of("2026-10-26T00:00:00Z", weather_kind="OBSERVATION") == []
    rows = store.as_of("2026-10-26T00:00:00Z", weather_kind="REANALYSIS")
    assert len(rows) == 1
    assert rows[0]["weather_kind"] == "REANALYSIS"
    assert store.as_of("2026-10-24T00:00:00Z", weather_kind="REANALYSIS") == []


def test_report_upper_bound_and_timestamp_evidence_are_preserved(store):
    legacy = weather()
    legacy.pop("observed_at")
    result = store.ingest(legacy, fixture(), source_run_id="backfill",
                          observed_at="2026-10-18T11:00:00Z",
                          time_basis="REPORT_AVAILABLE_AT_UPPER_BOUND",
                          metadata={"source_report": "saved-report.json", "retrieval_time_known": False})
    assert result["time_basis"] == "REPORT_AVAILABLE_AT_UPPER_BOUND"
    assert store.as_of("2026-10-18T10:59:59Z") == []
    row = store.as_of("2026-10-18T11:00:00Z")[0]
    assert row["metadata"]["retrieval_time_known"] is False
    assert row["time_basis"] == "REPORT_AVAILABLE_AT_UPPER_BOUND"


def test_time_basis_cannot_be_silently_relabelled(store):
    with pytest.raises(ValueError, match="disagrees"):
        store.ingest(weather(time_basis="FETCHED_AT"), fixture(), source_run_id="bad",
                     time_basis="REPORT_AVAILABLE_AT_UPPER_BOUND")
    with pytest.raises(ValueError, match="only valid for UNKNOWN"):
        store.ingest(weather(), fixture(), source_run_id="bad", time_basis="LOOKUP_ATTEMPT_AT")
