"""Bounded offline temporal backfill, cursor resume and source preservation."""
import json
import socket
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs.temporal import backfill_temporal
from goal_analysis.normalization.historical_results import normalize_api_result
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.historical_results import HistoricalResults
from goal_analysis.storage.sports_news import NewsStore
from goal_analysis.storage.sports_weather import WeatherStore
from goal_analysis.storage.temporal import TemporalStore

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
RECEIVED = NOW - timedelta(days=1)
INGESTED = NOW - timedelta(hours=1)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("temporal backfill must remain entirely offline")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def sources(tmp_path):
    database = Database(tmp_path / "data/goal_analysis.sqlite3")
    database.initialize()
    snapshots = SnapshotStore(database, tmp_path / "data/raw")
    normalized = []
    for number in range(3):
        raw = {"fixture": {"id": number + 1,
            "date": (NOW - timedelta(days=3)).isoformat(), "status": {"short": "FT"}},
            "league": {"id": 39, "season": 2026},
            "teams": {"home": {"id": 10, "name": "Home"},
                      "away": {"id": 20, "name": "Away"}},
            "score": {"fulltime": {"home": 2, "away": 1},
                      "halftime": {"home": 1, "away": 0}}}
        url = f"https://v3.football.api-sports.io/fixtures?id={number + 1}"
        snapshot = snapshots.save("api_football", "fixtures", json.dumps(raw).encode(),
            "application/json", RECEIVED, url)
        normalized.append(normalize_api_result(raw, observed_at=RECEIVED,
            source_url=url, source_sha256=snapshot.content_hash))
    results = HistoricalResults(database, clock=lambda: INGESTED)
    results.initialize()
    results.ingest(normalized[:2])
    weather = WeatherStore(database)
    weather.initialize()
    kickoff = (NOW + timedelta(hours=2)).isoformat()
    weather.ingest({"fixture_id": "future-fixture", "provider": "open_meteo",
        "weather_kind": "FORECAST", "status": "SOURCED", "observed_at": RECEIVED.isoformat(),
        "forecast_time": kickoff, "source_url": "https://api.open-meteo.com/v1/forecast"},
        {"fixture_id": "future-fixture", "kickoff": kickoff}, source_run_id="existing-run")
    NewsStore(database).initialize()
    return tmp_path, database


def legacy_rows(database):
    with closing(database.connect()) as connection:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name!='temporal_receipts' ORDER BY name")]
        return {name: sorted(tuple(row) for row in connection.execute(f'SELECT * FROM "{name}"'))
                for name in names}


def test_missing_source_database_is_reported_without_creating_any_files(tmp_path):
    report = backfill_temporal(tmp_path, clock=lambda: NOW)
    assert report["status"] == "MISSING_SOURCE" and report["summary"] is None
    assert report["scanned"] == report["created"] == report["external_requests"] == 0
    assert list(tmp_path.iterdir()) == []


def test_invalid_budgets_and_cursors_fail_before_any_source_or_metadata_write(tmp_path):
    for values in ({"max_rows": True}, {"max_rows": 0}, {"max_rows": 1_000_001},
                   {"batch_size": False}, {"batch_size": 0}, {"batch_size": 10_001}):
        with pytest.raises(ValueError, match="INVALID_TEMPORAL_BACKFILL_BUDGET"):
            backfill_temporal(tmp_path, **values)
    for cursors in ([], {"unknown": 1}, {"raw": True}, {"raw": -1}, {"raw": 2**63}):
        with pytest.raises(ValueError, match="INVALID_TEMPORAL_BACKFILL_CURSORS"):
            backfill_temporal(tmp_path, cursors=cursors)
    assert list(tmp_path.iterdir()) == []


def test_shared_row_limit_resume_and_exact_source_preservation(sources):
    root, database = sources
    original = legacy_rows(database)
    bytes_before = {path: path.read_bytes() for path in (root / "data/raw").rglob("*.bin")}
    first = backfill_temporal(root, max_rows=3, batch_size=1, clock=lambda: NOW)
    assert first["status"] == "ROW_LIMIT" and first["pending"]
    assert first["scanned"] == first["created"] == 3
    assert first["by_namespace"]["result"]["scanned"] == 2
    assert first["by_namespace"]["weather"]["scanned"] == 1
    assert first["by_namespace"]["raw"]["scanned"] == 0
    assert first["pending_namespaces"] == ["raw", "news"]
    resume = dict(first["cursors"])
    second = backfill_temporal(root, max_rows=4, batch_size=2,
        cursors=resume, clock=lambda: NOW)
    assert second["status"] == "COMPLETE" and second["exhausted"] and not second["pending"]
    assert second["scanned"] == second["created"] == 3
    assert resume == first["cursors"]
    assert second["external_requests"] == 0 and legacy_rows(database) == original
    assert {path: path.read_bytes() for path in bytes_before} == bytes_before


def test_repeated_capture_creates_no_receipts_and_preserves_original_witness(sources):
    root, database = sources
    first = backfill_temporal(root, batch_size=2, clock=lambda: NOW)
    assert first["created"] == first["scanned"] == 6 and first["status"] == "COMPLETE"
    with closing(database.connect()) as connection:
        original = [tuple(row) for row in connection.execute("SELECT * FROM temporal_receipts ORDER BY receipt_id")]
    second = backfill_temporal(root, batch_size=1, clock=lambda: NOW + timedelta(hours=1))
    assert second["created"] == second["rejected"] == 0 and second["scanned"] == 6
    with closing(database.connect()) as connection:
        assert [tuple(row) for row in connection.execute(
            "SELECT * FROM temporal_receipts ORDER BY receipt_id")] == original
    assert second["summary"]["mode"] == "SHADOW" and second["external_requests"] == 0


def test_corrupt_raw_is_reported_while_valid_source_siblings_are_witnessed(sources):
    root, _ = sources
    raw = next((root / "data/raw").rglob("*.bin"))
    raw.write_bytes(b"different bytes with invalid content address")
    report = backfill_temporal(root, clock=lambda: NOW)
    assert report["status"] == "PARTIAL" and report["exhausted"]
    assert report["scanned"] == 6 and report["created"] == 5 and report["rejected"] == 1
    assert report["by_namespace"]["raw"]["rejection_counts"] == {
        "RAW_PAYLOAD_HASH_OR_SIZE_MISMATCH": 1}
    assert report["rejection_examples"][0]["namespace"] == "raw"
    assert report["external_requests"] == 0


def test_witness_does_not_invent_legacy_ingestion_or_publication_times(sources):
    root, database = sources
    backfill_temporal(root, clock=lambda: NOW)
    store = TemporalStore(database, root / "data/raw", clock=lambda: NOW)
    assert store.records("result", NOW - timedelta(microseconds=1)) == []
    assert store.records("weather", NOW - timedelta(microseconds=1)) == []
    results, weather, raw = (store.records(namespace, NOW) for namespace in ("result", "weather", "raw"))
    assert len(results) == 2 and len(weather) == 1 and len(raw) == 3
    assert all(row["times"]["ingested_at"] == INGESTED.isoformat(timespec="microseconds")
               and row["times"]["published_at"] is None for row in results)
    assert weather[0]["times"]["ingested_at"] is weather[0]["times"]["published_at"] is None
    assert datetime.fromisoformat(weather[0]["times"]["event_time"]) > NOW
    assert all(row["times"]["ingested_at"] is row["times"]["published_at"] is None for row in raw)


def test_wrapper_propagates_cross_instance_clock_rollback_without_rewriting_sources(sources):
    root, database = sources
    backfill_temporal(root, clock=lambda: NOW)
    original = legacy_rows(database)
    with pytest.raises(ValueError, match="CLOCK_MOVED_BACKWARDS"):
        backfill_temporal(root, clock=lambda: NOW - timedelta(seconds=1))
    assert legacy_rows(database) == original
