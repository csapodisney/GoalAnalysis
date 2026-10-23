"""Offline source-atomic odds archival, resumability and parent integrity."""
import copy
import json
import socket
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import odds_archive as job
from goal_analysis.providers.football_data_csv import parse_csv
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.odds_snapshots import OddsSnapshots
from goal_analysis.storage.sports_archive import SportsArchive

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
API_URL = "https://v3.football.api-sports.io/odds?fixture=123"
ODDS_URL = "https://api.the-odds-api.com/v4/sports/soccer_epl/odds?markets=totals"
FD_URL = "https://www.football-data.co.uk/mmz4281/2627/E0.csv"
CSV = (b"Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365CH\n"
       b"E0,17/10/26,HomeA,AwayA,2,1,H,2.50,2.25\n"
       b"E0,17/10/26,HomeB,AwayB,2,1,H,3.10,3.00\n")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("odds archive must not call external providers")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def api_payload():
    return {"errors": [], "response": [{"fixture": {"id": 123},
        "league": {"id": 39, "season": 2026}, "update": "2026-10-20T07:00:00Z",
        "bookmakers": [{"id": 7, "name": "Native Book",
            "bets": [{"id": 1, "name": "Match Winner", "values": [
                {"value": "Home", "odd": "2.50"}, {"value": "Draw", "odd": "3.25"},
                {"value": "Away", "odd": "3.00"}]},
                {"id": 999, "name": "Novel Native Product", "values": [
                    {"value": "Native selection", "odd": "4.50"}]}]}]}]}


def odds_payload():
    return [{"id": "native-event-1", "sport_key": "soccer_epl", "home_team": "Home",
        "away_team": "Away", "bookmakers": [{"key": "native_book", "title": "Native Book",
            "last_update": "2026-10-20T07:59:00Z", "markets": [{"key": "totals",
                "last_update": "2026-10-20T07:58:00Z", "outcomes": [
                    {"name": "Over", "price": 1.90, "point": 2.5},
                    {"name": "Under", "price": 2.10, "point": 2.5}]}]}]}]


def create_sources(root, *, include_fd=True, include_monitor=True):
    database = Database(root / "data/goal_analysis.sqlite3")
    database.initialize()
    snapshots = SnapshotStore(database, root / "data/raw")
    for provider, resource, content, url, hours in (
            ("api_football", "odds", json.dumps(api_payload()).encode(), API_URL, 4),
            ("the_odds_api", "daily223_odds", json.dumps(odds_payload()).encode(), ODDS_URL, 3)):
        snapshots.save(provider, resource, content, "application/json", NOW - timedelta(hours=hours), url)
    if include_fd:
        source = snapshots.save("football_data_co_uk", "season_csv", CSV, "text/csv",
            NOW - timedelta(hours=2), FD_URL)
        archive = SportsArchive(database)
        archive.initialize()
        archive.ingest(parse_csv(CSV, "E0", "2627", NOW.date()), source_url=FD_URL,
            fetched_at=NOW - timedelta(hours=2), content_hash=source.content_hash)
    if include_monitor:
        path = root / "data/arthur/odds-monitor.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        quotes = [{"fixture_id": "api_football:123", "bookmaker": "native_api_book_7",
            "market_key": "totals", "selection_key": "over", "period": "FULL_TIME",
            "settlement": "REGULATION", "quote_provider": "api_football", "line": "2.5",
            "decimal_price": 1.95, "quoted_at": "2026-10-20T10:00:00Z",
            "quote_source_id": API_URL, "region": "DE", "currency": "EUR"}]
        observed, key = (NOW - timedelta(hours=1)).isoformat(), "api_football:synthetic-query"
        identity = canonical_sha256(["api_football", key, observed, quotes])
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE monitor_snapshots (snapshot_id TEXT PRIMARY KEY, "
                "query_key TEXT, provider TEXT, observed_at TEXT, quotes_json TEXT)")
            connection.execute("INSERT INTO monitor_snapshots VALUES (?,?,?,?,?)",
                (identity, key, "api_football", observed, json.dumps(quotes, sort_keys=True)))
            connection.commit()
    return database


def originals(database):
    with closing(database.connect()) as connection:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sports_odds_%' ORDER BY name")]
        return {name: sorted(tuple(row) for row in connection.execute(f'SELECT * FROM "{name}"'))
                for name in names}


def run(root, **kwargs):
    return job.backfill_odds(root, clock=lambda: NOW, **kwargs)


def test_bounded_source_resume_and_idempotence_keep_all_native_prices_and_legacy_tables(tmp_path):
    database = create_sources(tmp_path)
    old = originals(database)
    first = run(tmp_path, max_sources=1)
    assert first["status"] == "SOURCE_LIMIT" and first["next_index"] == 1
    assert first["created"]["quotes_created"] == 4
    second = run(tmp_path, start_index=first["next_index"],
        expected_manifest_sha256=first["source_manifest_sha256"])
    assert second["status"] == "COMPLETE" and not second["pending"]
    assert second["planned_sources"] == 4 and second["processed_sources"] == 3
    assert second["created"]["quotes_created"] == 7
    third = run(tmp_path)
    assert third["created"].get("quotes_created", 0) == 0
    assert sum(third["reused_sources"].values()) == 4
    assert third["summary"]["sports_odds_quotes"] == 11
    assert third["external_requests"] == 0 and originals(database) == old
    values = OddsSnapshots(database).quotes(as_of=NOW)
    unknown = [value for value in values if value.get("provider_market_key") == "Novel Native Product"]
    assert len(unknown) == 1 and unknown[0]["decimal_price"] == "4.5"
    assert unknown[0]["mapping_status"] == "UNSUPPORTED"
    historical = [value for value in values if value["provider"] == "football_data_co_uk"]
    assert len(historical) == 4 and all(value["quoted_at"] is None for value in historical)


def test_missing_database_and_invalid_budgets_do_not_create_files(tmp_path):
    assert run(tmp_path)["status"] == "MISSING_SOURCE"
    for kwargs in ({"max_sources": True}, {"max_sources": 0}, {"start_index": True},
                   {"start_index": -1}, {"include_monitor": 1}):
        with pytest.raises(ValueError, match="INVALID_ODDS_ARCHIVE_BUDGET"):
            run(tmp_path, **kwargs)
    assert not list(tmp_path.iterdir())


def test_resume_rejects_changed_source_manifest_before_skipping_a_shifted_source(tmp_path):
    database = create_sources(tmp_path, include_fd=False, include_monitor=False)
    first = run(tmp_path, max_sources=1)
    new = copy.deepcopy(api_payload())
    new["response"][0]["fixture"]["id"] = 999
    SnapshotStore(database, tmp_path / "data/raw").save("api_football", "odds",
        json.dumps(new).encode(), "application/json", NOW - timedelta(hours=5), API_URL)
    with pytest.raises(ValueError, match="MANIFEST"):
        run(tmp_path, start_index=first["next_index"],
            expected_manifest_sha256=first["source_manifest_sha256"])


def test_corrupt_raw_snapshot_is_quarantined_without_discarding_other_sources(tmp_path):
    database = create_sources(tmp_path, include_fd=False, include_monitor=False)
    with closing(database.connect()) as connection:
        source = connection.execute("SELECT relative_path FROM raw_snapshots WHERE provider='api_football'").fetchone()
    (tmp_path / "data/raw" / source[0]).write_bytes(b"corrupt bytes")
    report = run(tmp_path)
    assert report["status"] == "PARTIAL" and len(report["rejected_sources"]) == 1
    assert report["rejected_sources"][0]["reason"] == "RAW_PAYLOAD_HASH_OR_SIZE_MISMATCH"
    assert report["created"]["quotes_created"] == 2
    assert report["external_requests"] == 0


def test_cached_source_revalidates_parent_bytes_before_reuse(tmp_path):
    database = create_sources(tmp_path, include_fd=False, include_monitor=False)
    run(tmp_path)
    with closing(database.connect()) as connection:
        source = connection.execute("SELECT relative_path FROM raw_snapshots WHERE provider='api_football'").fetchone()
    (tmp_path / "data/raw" / source[0]).write_bytes(b"changed after successful archival")
    report = run(tmp_path)
    assert report["status"] == "PARTIAL"
    assert report["reused_sources"] == {"the_odds_api": 1}
    assert report["created"].get("quotes_created", 0) == 0


def corrupt_second_fd_row(database):
    with database.transaction() as connection:
        connection.execute("DROP TRIGGER sports_archive_observations_no_update")
        connection.execute("UPDATE sports_archive_observations SET home_team='Corrupt' "
            "WHERE observation_id=(SELECT max(observation_id) FROM sports_archive_observations)")


def test_late_fd_row_hash_failure_rolls_back_the_entire_csv_source(tmp_path):
    database = create_sources(tmp_path, include_monitor=False)
    corrupt_second_fd_row(database)
    report = run(tmp_path)
    assert report["status"] == "PARTIAL"
    assert report["rejected_sources"][0]["reason"] == "FOOTBALL_DATA_ARCHIVE_ROW_HASH_MISMATCH"
    with closing(database.connect()) as connection:
        assert connection.execute("SELECT count(*) FROM sports_odds_sources WHERE provider='football_data_co_uk'").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM sports_odds_products WHERE provider='football_data_co_uk'").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM sports_odds_quotes").fetchone()[0] == 6


def test_cached_fd_receipt_does_not_hide_corrupted_normalized_archive_rows(tmp_path):
    database = create_sources(tmp_path, include_monitor=False)
    run(tmp_path)
    corrupt_second_fd_row(database)
    report = run(tmp_path)
    assert report["status"] == "PARTIAL"
    assert report["rejected_sources"][0]["reason"] == "FOOTBALL_DATA_ARCHIVE_ROW_HASH_MISMATCH"
    assert report["reused_sources"].get("football_data_co_uk", 0) == 0


def test_changed_monitor_payload_cannot_borrow_original_snapshot_identity(tmp_path):
    create_sources(tmp_path, include_fd=False)
    path = tmp_path / "data/arthur/odds-monitor.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute("SELECT snapshot_id,quotes_json FROM monitor_snapshots").fetchone()
        quotes = json.loads(row[1])
        quotes[0]["decimal_price"] = 9.99
        connection.execute("UPDATE monitor_snapshots SET quotes_json=? WHERE snapshot_id=?",
            (json.dumps(quotes), row[0]))
        connection.commit()
    report = run(tmp_path)
    assert report["status"] == "PARTIAL"
    assert any("MONITOR" in rejected["reason"] for rejected in report["rejected_sources"])
    assert report["created"]["quotes_created"] == 6


def test_parser_hash_changes_reprocess_sources_without_rewriting_existing_receipts(tmp_path, monkeypatch):
    database = create_sources(tmp_path, include_fd=False, include_monitor=False)
    first = run(tmp_path)
    monkeypatch.setattr(job, "_parser_hash", lambda module: "d" * 64)
    second = run(tmp_path)
    assert second["status"] == "COMPLETE" and second["reused_sources"] == {}
    assert second["created"]["sources_created"] == 2
    assert second["summary"]["sports_odds_sources"] == first["summary"]["sports_odds_sources"] + 2
    assert second["summary"]["sports_odds_quotes"] == 2 * first["summary"]["sports_odds_quotes"]
    assert len(OddsSnapshots(database).quotes(as_of=NOW - timedelta(seconds=1))) == 0


def test_resume_rejects_parser_revision_even_when_source_manifest_rows_are_unchanged(tmp_path, monkeypatch):
    database = create_sources(tmp_path, include_fd=False, include_monitor=False)
    first = run(tmp_path, max_sources=1)
    assert first["next_index"] == 1
    with closing(database.connect()) as connection:
        before = connection.execute("SELECT COUNT(*) FROM sports_odds_sources").fetchone()[0]
    monkeypatch.setattr(job, "_parser_hash", lambda module: "e" * 64)
    with pytest.raises(ValueError, match="SOURCE_MANIFEST_MISMATCH"):
        run(tmp_path, start_index=first["next_index"],
            expected_manifest_sha256=first["source_manifest_sha256"])
    with closing(database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sports_odds_sources").fetchone()[0] == before


def test_failed_source_stays_at_retry_cursor_while_valid_later_source_is_salvaged(tmp_path):
    database = create_sources(tmp_path, include_fd=False, include_monitor=False)
    with closing(database.connect()) as connection:
        relative = connection.execute(
            "SELECT relative_path FROM raw_snapshots WHERE provider='api_football'").fetchone()[0]
    damaged = tmp_path / "data/raw" / relative
    damaged.write_bytes(b"invalid immutable parent")
    first = run(tmp_path)
    assert first["status"] == "PARTIAL" and first["pending"]
    assert first["next_index"] == 0 and first["processed_sources"] == 2
    assert first["rejected_sources"][0]["source_index"] == 0
    assert first["created"]["quotes_created"] == 2
    retry = run(tmp_path, start_index=first["next_index"],
        expected_manifest_sha256=first["source_manifest_sha256"])
    assert retry["status"] == "PARTIAL" and retry["pending"]
    assert retry["next_index"] == 0
    assert retry["reused_sources"] == {"the_odds_api": 1}
    assert retry["created"].get("quotes_created", 0) == 0
    damaged.write_bytes(json.dumps(api_payload()).encode())
    recovered = run(tmp_path, start_index=retry["next_index"],
        expected_manifest_sha256=retry["source_manifest_sha256"])
    assert recovered["status"] == "COMPLETE" and not recovered["pending"]
    assert recovered["next_index"] == 2
    assert recovered["reused_sources"] == {"the_odds_api": 1}
    assert recovered["created"]["quotes_created"] == 4
    assert recovered["summary"]["sports_odds_quotes"] == 6


def test_partial_failure_takes_precedence_over_source_limit_without_losing_retry_index(tmp_path):
    database = create_sources(tmp_path, include_fd=False, include_monitor=False)
    with closing(database.connect()) as connection:
        relative = connection.execute(
            "SELECT relative_path FROM raw_snapshots WHERE provider='api_football'").fetchone()[0]
    (tmp_path / "data/raw" / relative).write_bytes(b"corrupt first source")
    report = run(tmp_path, max_sources=1)
    assert report["status"] == "PARTIAL"
    assert report["pending"] and report["next_index"] == 0
    assert report["processed_sources"] == 1
    assert report["summary"]["sports_odds_sources"] == 0
