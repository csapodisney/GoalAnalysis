"""Real-source availability receipts must not backfill historical knowledge."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.historical_results import HistoricalResults
from goal_analysis.storage.sports_news import NewsStore
from goal_analysis.storage.sports_weather import WeatherStore
from goal_analysis.storage.temporal import TemporalStore

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
RECEIPT = NOW - timedelta(days=3)


def stamp(value):
    return value.isoformat(timespec="microseconds")


def setup_sources(tmp_path):
    database = Database(tmp_path / "sports.sqlite3")
    database.initialize()
    results = HistoricalResults(database, clock=lambda: RECEIPT)
    results.initialize()
    weather = WeatherStore(database)
    weather.initialize()
    news = NewsStore(database)
    news.initialize()
    raw = SnapshotStore(database, tmp_path / "raw")
    temporal = TemporalStore(database, tmp_path / "raw", clock=lambda: NOW)
    temporal.initialize()
    return database, results, weather, news, raw, temporal


def result(number=1):
    kickoff = RECEIPT - timedelta(days=1)
    return {
        "provider": "the_stats_api", "provider_id": f"mt_{number}",
        "competition_id": "comp_1", "season_id": "sn_1", "home_id": "tm_1", "away_id": "tm_2",
        "home_team": "Home FC", "away_team": "Away FC", "kickoff": stamp(kickoff),
        "match_date": kickoff.date().isoformat(), "kickoff_local_time": None,
        "status": "finished", "score_scope": "REGULATION", "home_goals": 2, "away_goals": 1,
        "half_home_goals": None, "half_away_goals": None,
        "observed_at": stamp(RECEIPT), "source_url": "https://api.thestatsapi.com/api/football/matches",
        "source_sha256": "a" * 64, "raw": {"id": f"mt_{number}", "retained": True},
    }


def add_weather(weather, *, fixture_id="fixture-1"):
    target = NOW + timedelta(days=1)
    fixture = {"fixture_id": fixture_id, "kickoff": stamp(target)}
    source = {"fixture_id": fixture_id, "provider": "open_meteo", "weather_kind": "FORECAST",
              "status": "SOURCED", "observed_at": stamp(RECEIPT),
              "forecast_time": stamp(target), "provider_issue_time": stamp(RECEIPT - timedelta(hours=1)),
              "source_url": "https://api.open-meteo.com/v1/forecast"}
    return weather.ingest(source, fixture, source_run_id="archived-run", raw_payload={"hourly": [1, 2]})


def add_news(news, *, request_id="request-1", observed=RECEIPT):
    published = RECEIPT - timedelta(hours=1)
    snowflake = str((int(published.timestamp() * 1000) - 1288834974657) << 22)
    url = "https://x.com/football_news/status/" + snowflake
    claim = {"claim_id": "claim-1", "source_url": url, "author": "football_news",
             "published_at": stamp(published), "fetched_at": stamp(RECEIPT - timedelta(minutes=30)),
             "fixture_id": "fixture-1", "kind": "INJURY", "claim": "A sourced model summary.",
             "content_status": "UNVERIFIED", "provenance_status": "CITATION_AND_TIMESTAMP_CHECKED"}
    return news.ingest_grok_result({"status": "AVAILABLE", "claims": [claim], "citations": [url]},
                                  request_id=request_id, observed_at=observed)


def add_raw(raw, *, content=b"abcdef"):
    return raw.save("provider", "fixtures", content, "application/json", RECEIPT,
                    "https://data.example.test/fixtures")


def receipt_rows(database):
    with database.connect() as connection:
        return [tuple(row) for row in connection.execute(
            "SELECT * FROM temporal_receipts ORDER BY namespace,source_key,source_sha256")]


def legacy_rows(database):
    with database.connect() as connection:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'temporal_%' ORDER BY name")]
        return {name: sorted(tuple(row) for row in connection.execute("SELECT * FROM " + name))
                for name in names}


def test_old_result_only_becomes_proven_available_after_actual_capture(tmp_path):
    database, results, _, _, _, temporal = setup_sources(tmp_path)
    results.ingest([result()])
    before = legacy_rows(database)
    assert temporal.records("result", NOW) == []
    report = temporal.capture("result")
    assert report == {"scanned": 1, "created": 1, "rejected": [], "next_after": 1, "exhausted": True}
    assert temporal.records("result", NOW - timedelta(microseconds=1)) == []
    entry = temporal.records("result", NOW)[0]
    assert len(entry["source_sha256"]) == 64
    assert entry["times"]["observed_at"] == stamp(RECEIPT)
    assert entry["times"]["ingested_at"] == stamp(RECEIPT)
    assert entry["times"]["available_at"] == stamp(NOW)
    assert entry["times"]["published_at"] is None
    assert legacy_rows(database) == before


def test_legacy_weather_raw_news_never_receive_synthesized_original_ingestion(tmp_path):
    _, _, weather, news, raw, temporal = setup_sources(tmp_path)
    add_weather(weather)
    add_news(news)
    add_raw(raw)
    for namespace in ("weather", "raw", "news"):
        assert temporal.capture(namespace)["created"] == 1
        assert temporal.records(namespace, RECEIPT) == []
        times = temporal.records(namespace, NOW)[0]["times"]
        assert times["ingested_at"] is None
        assert times["available_at"] == stamp(NOW)
        assert "ingested_at" in times["missing_times"]
    weather_times = temporal.records("weather", NOW)[0]["times"]
    assert datetime.fromisoformat(weather_times["event_time"]) > NOW
    assert weather_times["event_time_kind"] == "FORECAST_TARGET"
    assert weather_times["published_at"] == stamp(RECEIPT - timedelta(hours=1))
    assert temporal.records("raw", NOW)[0]["times"]["published_at"] is None


def test_repeat_capture_preserves_first_receipt_and_all_legacy_bytes(tmp_path):
    database, results, _, _, _, temporal = setup_sources(tmp_path)
    results.ingest([result()])
    before = legacy_rows(database)
    assert temporal.capture("result")["created"] == 1
    first_receipt = receipt_rows(database)
    temporal.clock = lambda: NOW + timedelta(hours=1)
    assert temporal.capture("result")["created"] == 0
    assert receipt_rows(database) == first_receipt
    assert legacy_rows(database) == before
    assert temporal.records("result", NOW)[0]["times"]["available_at"] == stamp(NOW)
    assert temporal.summary()["mode"] == "SHADOW"
    assert temporal.summary()["sources"][0]["receipts"] == 1


def test_pagination_is_native_rowid_bounded_and_idempotent(tmp_path):
    _, results, _, _, _, temporal = setup_sources(tmp_path)
    results.ingest([result(1), result(2), result(3)])
    first = temporal.capture("result", limit=2)
    assert first == {"scanned": 2, "created": 2, "rejected": [], "next_after": 2, "exhausted": False}
    second = temporal.capture("result", after=first["next_after"], limit=2)
    assert second == {"scanned": 1, "created": 1, "rejected": [], "next_after": 3, "exhausted": True}
    assert temporal.capture("result", after=3, limit=2)["scanned"] == 0
    assert temporal.capture("result", limit=2)["created"] == 0
    assert len(temporal.records("result", NOW, limit=2)) == 2
    assert len(temporal.records("result", NOW)) == 3


def test_source_with_future_original_ingestion_is_rejected_without_backdating(tmp_path):
    database, results, _, _, _, temporal = setup_sources(tmp_path)
    results.clock = lambda: NOW + timedelta(seconds=1)
    results.ingest([result()])
    before = legacy_rows(database)
    report = temporal.capture("result")
    assert report["scanned"] == 1 and report["created"] == 0
    assert len(report["rejected"]) == 1 and report["rejected"][0]["reason"]
    assert temporal.records("result", NOW + timedelta(days=1)) == []
    assert receipt_rows(database) == []
    assert legacy_rows(database) == before


def test_legacy_future_publication_is_rejected_while_valid_weather_remains(tmp_path):
    database, _, weather, _, _, temporal = setup_sources(tmp_path)
    add_weather(weather)
    with database.transaction() as connection:
        original = dict(connection.execute("SELECT * FROM sports_weather_snapshots").fetchone())
        invalid = json.loads(original["snapshot_json"])
        invalid["provider_issue_time"] = stamp(NOW + timedelta(hours=1))
        original["snapshot_json"] = json.dumps(invalid, sort_keys=True, separators=(",", ":"))
        original["snapshot_id"] = hashlib.sha256(original["snapshot_json"].encode()).hexdigest()
        original["provider_issue_time"] = invalid["provider_issue_time"]
        connection.execute("INSERT INTO sports_weather_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           tuple(original.values()))
    report = temporal.capture("weather")
    assert report["scanned"] == 2 and report["created"] == 1
    assert len(report["rejected"]) == 1
    assert report["rejected"][0]["source_key"] == original["snapshot_id"]
    assert len(temporal.records("weather", NOW)) == 1


@pytest.mark.parametrize("damage", ("missing", "hash", "size", "escape"))
def test_raw_capture_requires_real_contained_file_with_verified_hash_and_size(tmp_path, damage):
    database, _, _, _, raw, temporal = setup_sources(tmp_path)
    saved = add_raw(raw)
    path = raw.root / saved.relative_path
    if damage == "missing":
        path.unlink()
    elif damage == "hash":
        path.write_bytes(b"tamper")
    else:
        if damage == "escape":
            outside = tmp_path / "outside.bin"
            outside.write_bytes(b"abcdef")
            field, value = "relative_path", "../outside.bin"
        else:
            field, value = "size_bytes", saved.size_bytes + 1
        with database.transaction() as connection:
            connection.execute("UPDATE raw_snapshots SET " + field + "=? WHERE content_hash=?",
                               (value, saved.content_hash))
    before = legacy_rows(database)
    report = temporal.capture("raw")
    assert report["scanned"] == 1 and report["created"] == 0
    assert report["rejected"][0]["source_key"] == saved.content_hash
    assert report["rejected"][0]["reason"]
    assert temporal.records("raw", NOW) == []
    assert legacy_rows(database) == before


@pytest.mark.parametrize("damage", ("metadata", "file"))
def test_source_mutation_cannot_borrow_an_earlier_availability_receipt(tmp_path, damage):
    database, _, _, _, raw, temporal = setup_sources(tmp_path)
    saved = add_raw(raw)
    assert temporal.capture("raw")["created"] == 1
    old_receipt = receipt_rows(database)
    assert len(temporal.records("raw", NOW)) == 1
    if damage == "file":
        (raw.root / saved.relative_path).write_bytes(b"tamper")
    else:
        with database.transaction() as connection:
            connection.execute("UPDATE raw_snapshots SET source_url='https://changed.example.test' "
                               "WHERE content_hash=?", (saved.content_hash,))
    assert temporal.records("raw", NOW) == []
    assert receipt_rows(database) == old_receipt


def test_new_source_digest_needs_its_own_later_witness(tmp_path):
    database, _, _, _, raw, temporal = setup_sources(tmp_path)
    saved = add_raw(raw)
    temporal.capture("raw")
    with database.transaction() as connection:
        connection.execute("UPDATE raw_snapshots SET source_url='https://changed.example.test' "
                           "WHERE content_hash=?", (saved.content_hash,))
    later = NOW + timedelta(hours=1)
    temporal.clock = lambda: later
    assert temporal.capture("raw")["created"] == 1
    assert len(receipt_rows(database)) == 2
    assert temporal.records("raw", NOW) == []
    assert temporal.records("raw", later)[0]["times"]["available_at"] == stamp(later)


def test_news_same_assertion_under_two_imports_has_two_distinct_provenance_receipts(tmp_path):
    database, _, _, news, _, temporal = setup_sources(tmp_path)
    first = add_news(news)
    second = add_news(news, request_id="request-2", observed=RECEIPT + timedelta(minutes=10))
    assert first["assertions_created"] == 1 and second["assertions_reused"] == 1
    assert temporal.capture("news")["created"] == 2
    entries = temporal.records("news", NOW)
    keys = [json.loads(entry["source_key"]) for entry in entries]
    assert {key[0] for key in keys} == {first["import_id"], second["import_id"]}
    assert all(key[1] == "claim-1" for key in keys)
    assert len({key[2] for key in keys}) == 1
    assert all(entry["times"]["ingested_at"] is None for entry in entries)
    assert all(entry["times"]["published_at"] == stamp(RECEIPT - timedelta(hours=1))
               for entry in entries)
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sports_news_assertions").fetchone()[0] == 1


@pytest.mark.parametrize("operation", ("UPDATE", "DELETE"))
def test_temporal_receipts_are_database_enforced_append_only(tmp_path, operation):
    database, results, _, _, _, temporal = setup_sources(tmp_path)
    results.ingest([result()])
    temporal.capture("result")
    before = receipt_rows(database)
    command = ("UPDATE temporal_receipts SET available_at=available_at" if operation == "UPDATE"
               else "DELETE FROM temporal_receipts")
    with database.connect() as connection, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(command)
    assert receipt_rows(database) == before


def test_receipt_transaction_failure_rolls_back_entire_batch_and_retry_is_safe(tmp_path, monkeypatch):
    database, results, _, _, _, temporal = setup_sources(tmp_path)
    results.ingest([result(1), result(2)])
    before = legacy_rows(database)
    original = database.transaction

    @contextmanager
    def abort_receipt_commit():
        with original() as connection:
            yield connection
            raise OSError("simulated receipt commit failure")

    monkeypatch.setattr(database, "transaction", abort_receipt_commit)
    with pytest.raises(OSError, match="commit failure"):
        temporal.capture("result")
    assert receipt_rows(database) == []
    assert legacy_rows(database) == before
    monkeypatch.setattr(database, "transaction", original)
    assert temporal.capture("result")["created"] == 2
    assert len(temporal.records("result", NOW)) == 2


def test_uncommitted_source_rows_are_never_witnessed(tmp_path):
    database, _, _, _, raw, temporal = setup_sources(tmp_path)
    content = b"uncommitted-source"
    source_hash = hashlib.sha256(content).hexdigest()
    relative = "provider/fixtures/" + source_hash + ".bin"
    target = raw.root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    with pytest.raises(OSError, match="rollback source"), database.transaction() as connection:
        connection.execute("INSERT INTO raw_snapshots VALUES (?,?,?,?,?,?,?,?)",
            (source_hash, "provider", "fixtures", stamp(RECEIPT), "application/json", relative,
             len(content), "https://data.example.test/uncommitted"))
        report = temporal.capture("raw")
        assert report["scanned"] == report["created"] == 0
        raise OSError("rollback source")
    assert temporal.records("raw", NOW) == []
    assert receipt_rows(database) == []


def test_clock_rollback_does_not_create_retimed_receipts(tmp_path):
    database, results, _, _, _, temporal = setup_sources(tmp_path)
    results.ingest([result()])
    temporal.capture("result")
    before = receipt_rows(database)
    temporal.clock = lambda: NOW - timedelta(seconds=1)
    with pytest.raises(ValueError, match="CLOCK_MOVED_BACKWARDS"):
        temporal.capture("result")
    assert receipt_rows(database) == before


@pytest.mark.parametrize("namespace", ("weather", "raw"))
def test_clock_floor_survives_restart_even_when_original_ingestion_is_unknown(tmp_path, namespace):
    database, _, weather, _, raw, temporal = setup_sources(tmp_path)
    if namespace == "weather":
        add_weather(weather)
    else:
        add_raw(raw)
    temporal.capture(namespace)
    before = receipt_rows(database)
    assert temporal.records(namespace, NOW)[0]["times"]["ingested_at"] is None
    restarted = TemporalStore(database, raw.root, clock=lambda: NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="CLOCK_MOVED_BACKWARDS"):
        restarted.capture(namespace)
    assert receipt_rows(database) == before


@pytest.mark.parametrize("after", (2 ** 63, 10 ** 100))
def test_pagination_cursor_outside_sqlite_signed_integer_range_fails_cleanly(tmp_path, after):
    database, results, _, _, _, temporal = setup_sources(tmp_path)
    results.ingest([result()])
    with pytest.raises(ValueError):
        temporal.capture("result", after=after)
    assert receipt_rows(database) == []
    largest = 2 ** 63 - 1
    assert temporal.capture("result", after=largest) == {
        "scanned": 0, "created": 0, "rejected": [], "next_after": largest, "exhausted": True}
