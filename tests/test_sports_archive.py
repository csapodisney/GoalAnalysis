import copy
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.sports_archive import SportsArchive

RECEIVED = datetime(2026, 10, 18, 12, tzinfo=UTC)
SOURCE = "https://www.football-data.co.uk/mmz4281/2526/E0.csv"


def dataset(provider="football_data_co_uk"):
    return {
        "provider": provider, "league_code": "E0", "season_code": "2526",
        "columns": ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "B365H",
                    "B365CH", "MaxH", "PSCH", "Unrecognized Future Field"],
        "records": [{
            "source_record_key": "E0|2526|2025-08-16|Home|Away",
            "provider": provider, "league_code": "E0", "season_code": "2526",
            "match_date": "2025-08-16", "home_team": "Home", "away_team": "Away",
            "full_time_home": 2, "full_time_away": 1, "full_time_result": "H",
            "half_time_home": 0, "half_time_away": 1, "half_time_result": "A",
            "kickoff_local_time": "15:00",
            "raw": {"Date": "16/08/2025", "HomeTeam": "Home", "AwayTeam": "Away",
                    "FTHG": "2", "FTAG": "1", "B365H": "2.15", "B365CH": "1.98",
                    "MaxH": "2.30", "PSCH": "2.01", "Unrecognized Future Field": "  zář  "},
            "odds_snapshot_types": {"B365H": "pre_closing", "B365CH": "closing",
                                    "MaxH": "pre_closing", "PSCH": "closing"},
        }],
        "rejections": [{"row": 3, "reason": "invalid_date"}],
        "rejection_counts": {"invalid_date": 1},
        "blank_rows_skipped": 2, "encoding": "utf-8-sig",
    }


@pytest.fixture
def archive(tmp_path):
    store = SportsArchive(Database(tmp_path / "archive.sqlite3"))
    store.initialize()
    return store


def ingest(archive, data=None, received=RECEIVED, **kwargs):
    data = data or dataset()
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return archive.ingest(data, source_url=SOURCE, fetched_at=received,
                          content_hash=digest, **kwargs)


def test_initialization_and_identical_import_are_idempotent(archive):
    archive.initialize()
    first, second = ingest(archive), ingest(archive)
    assert first["imports_created"] == first["observations_created"] == 1
    assert first["matches_created"] == first["records_linked"] == 1
    assert second["imports_created"] == second["observations_created"] == 0
    assert second["matches_created"] == second["records_linked"] == 0
    assert second["import_id"] == first["import_id"]
    assert second["observations_reused"] == 1
    summary = archive.summary()
    assert summary["total_unique_matches"] == summary["total_imports"] == 1
    assert summary["total_observations"] == 1
    assert summary["by_provider"] == {"football_data_co_uk": 1}
    assert summary["by_league_season"] == [{"provider": "football_data_co_uk",
        "league_code": "E0", "season_code": "2526", "unique_matches": 1}]


def test_new_fetch_preserves_provenance_without_duplicate_version(archive):
    ingest(archive)
    later = RECEIVED + timedelta(days=1)
    counts = ingest(archive, received=later)
    assert counts["imports_created"] == 1
    assert counts["observations_created"] == counts["matches_created"] == 0
    assert counts["observations_reused"] == counts["records_linked"] == 1
    assert archive.summary()["total_imports"] == 2
    assert archive.matches()[0]["observed_at"] == later.isoformat(timespec="microseconds")


def test_revisions_and_repeated_old_version_keep_all_history(archive):
    ingest(archive)
    revision = dataset()
    revision["records"][0]["raw"]["B365CH"] = "2.05"
    counts = ingest(archive, revision, RECEIVED + timedelta(days=1))
    assert counts["observations_created"] == 1
    assert counts["matches_created"] == 0
    assert archive.matches()[0]["raw"]["B365CH"] == "2.05"
    assert archive.matches(as_of=RECEIVED)[0]["raw"]["B365CH"] == "1.98"
    ingest(archive, received=RECEIVED + timedelta(days=2))
    assert archive.matches()[0]["raw"]["B365CH"] == "1.98"
    assert archive.summary()["total_observations"] == 2
    assert archive.summary()["total_unique_matches"] == 1


def test_provider_namespaces_never_collide(archive):
    ingest(archive)
    other = dataset("api_football")
    # Even identical source keys must remain provider-specific.
    ingest(archive, other)
    assert archive.summary()["total_unique_matches"] == 2
    assert len(archive.matches(provider="api_football")) == 1
    assert archive.matches(provider="api_football")[0]["provider"] == "api_football"


def test_raw_fields_rejections_and_snapshot_link_are_preserved(archive, tmp_path):
    archive.database.initialize()
    snapshot = SnapshotStore(archive.database, tmp_path / "raw").save(
        "football_data_co_uk", "historical_csv", b"original CSV bytes\r\n",
        "text/csv", RECEIVED, SOURCE,
    )
    data = dataset()
    archive.ingest(data, source_url=SOURCE, fetched_at=RECEIVED,
                   content_hash=snapshot.content_hash,
                   metadata={"license_status": "UNREVIEWED_COMMERCIAL_USE"})
    match = archive.matches()[0]
    assert match["raw"] == data["records"][0]["raw"]
    assert match["odds_snapshot_types"] == data["records"][0]["odds_snapshot_types"]
    assert match["raw_snapshot_hash"] == snapshot.content_hash
    assert match["kickoff_local_time"] == "15:00"
    assert "kickoff_utc" not in match
    with archive.database.connect() as connection:
        row = connection.execute("SELECT * FROM sports_archive_imports").fetchone()
        linked = connection.execute("""SELECT s.content_hash FROM sports_archive_imports i
            JOIN raw_snapshots s ON s.content_hash = i.raw_snapshot_hash""").fetchone()
    assert linked[0] == snapshot.content_hash
    assert json.loads(row["columns_json"]) == data["columns"]
    assert json.loads(row["rejections_json"]) == data["rejections"]
    assert json.loads(row["rejection_counts_json"]) == {"invalid_date": 1}
    assert row["rejection_count"] == 1
    assert row["blank_rows_skipped"] == 2
    assert row["fetched_at"] == RECEIVED.isoformat(timespec="microseconds")


def test_as_of_never_backdates_today_download_to_match_date(archive):
    ingest(archive)
    assert archive.matches(as_of=RECEIVED - timedelta(microseconds=1)) == []
    assert len(archive.matches(as_of=RECEIVED)) == 1
    assert archive.matches(as_of=datetime(2025, 8, 17, tzinfo=UTC)) == []
    with pytest.raises(ValueError, match="timezone-aware"):
        archive.matches(as_of=datetime(2026, 10, 20))  # noqa: DTZ001 - reject naive cutoff


def test_as_of_excludes_unknown_timezone_same_day_match(archive):
    data = dataset()
    data["records"][0]["match_date"] = "2026-10-18"
    ingest(archive, data)
    assert archive.matches(as_of=RECEIVED + timedelta(hours=5)) == []
    assert len(archive.matches(as_of=RECEIVED + timedelta(days=1))) == 1


def test_out_of_order_import_uses_receipt_time_not_insertion_order(archive):
    newer = dataset()
    newer["records"][0]["raw"]["B365CH"] = "2.05"
    ingest(archive, newer, RECEIVED + timedelta(days=1))
    ingest(archive)
    assert archive.matches()[0]["raw"]["B365CH"] == "2.05"
    assert archive.matches(as_of=RECEIVED)[0]["raw"]["B365CH"] == "1.98"


@pytest.mark.parametrize("change", [
    {"match_date": "2025-02-30"}, {"full_time_home": -1}, {"full_time_result": "A"},
    {"provider": "wrong"}, {"kickoff_local_time": "15:00+02:00"}, {"raw": []},
])
def test_invalid_row_rolls_back_entire_import(archive, change):
    data = dataset()
    invalid = copy.deepcopy(data["records"][0])
    invalid["source_record_key"] = "invalid-row"
    invalid.update(change)
    data["records"].append(invalid)
    with pytest.raises(ValueError):
        ingest(archive, data)
    assert archive.summary()["total_imports"] == 0
    assert archive.summary()["total_observations"] == 0


def test_conflicting_duplicate_keys_rejected_identical_rows_deduplicated(archive):
    data = dataset()
    data["records"].append(copy.deepcopy(data["records"][0]))
    data["records"][1]["raw"]["B365H"] = "3.00"
    with pytest.raises(ValueError, match="conflicting"):
        ingest(archive, data)
    assert archive.summary()["total_imports"] == 0
    data["records"][1] = copy.deepcopy(data["records"][0])
    counts = ingest(archive, data)
    assert counts["observations_created"] == counts["records_linked"] == 1


def test_sql_failure_rolls_back_import_and_preceding_observations(archive):
    with archive.database.connect() as connection:
        connection.execute("""CREATE TRIGGER fail_second_record BEFORE INSERT
            ON sports_archive_observations WHEN NEW.source_record_key = 'fail'
            BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    data = dataset()
    second = copy.deepcopy(data["records"][0])
    second["source_record_key"] = "fail"
    data["records"].append(second)
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        ingest(archive, data)
    assert archive.summary()["total_imports"] == 0
    assert archive.summary()["total_observations"] == 0


@pytest.mark.parametrize("table", ["imports", "observations", "import_records"])
@pytest.mark.parametrize("operation", ["DELETE", "UPDATE"])
def test_history_is_immutable(archive, table, operation):
    ingest(archive)
    query = (f"DELETE FROM sports_archive_{table}" if operation == "DELETE" else
             f"UPDATE sports_archive_{table} SET " +
             ("observation_id = observation_id" if table == "observations" else
              "import_id = import_id"))
    with archive.database.connect() as connection, pytest.raises(
        sqlite3.IntegrityError, match="append-only"
    ):
        connection.execute(query)


def test_date_correction_does_not_resurrect_outdated_version(archive):
    ingest(archive)
    revision = dataset()
    revision["records"][0]["match_date"] = "2026-10-19"
    later = RECEIVED + timedelta(days=1)
    ingest(archive, revision, later)
    assert archive.matches(as_of=later) == []
    assert archive.matches(as_of=RECEIVED)[0]["match_date"] == "2025-08-16"
