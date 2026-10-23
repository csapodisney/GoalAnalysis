"""Bounded legacy-writer catch-up without API calls or legacy data mutations."""

import json
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from goal_analysis.jobs import historical_result_sync as sync
from goal_analysis.jobs.the_stats_history import _read, _record
from goal_analysis.normalization.historical_results import normalize_archive_result
from goal_analysis.providers.football_data_csv import parse_csv
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.historical_results import HistoricalResults
from goal_analysis.storage.sports_archive import SportsArchive

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
URL = "https://api.thestatsapi.com/api/football/matches?competition_id=comp_4643"
QUERY = {"competition_id": "comp_4643", "date_from": "2026-10-01", "date_to": "2026-10-19"}


def setup(root):
    database = Database(root / "data/goal_analysis.sqlite3")
    database.initialize()
    archive = SportsArchive(database)
    archive.initialize()
    return database, archive, SnapshotStore(database, root / "data/raw")


def match(number):
    return {"id": f"mt_{number}", "competition_id": "comp_4643", "season_id": "sn_1",
        "status": "finished", "utc_date": "2026-10-18T15:00:00Z",
        "home_team": {"id": "tm_1", "name": "Home"},
        "away_team": {"id": "tm_2", "name": "Away"},
        "score": {"regulation": {"home": 2, "away": 1}}, "retained": [False, 0, None]}


def add_import(database, archive, snapshots, numbers, *, receipt=None, invalid_score=False):
    receipt = receipt or NOW - timedelta(hours=1)
    raw = [match(number) for number in numbers]
    if invalid_score:
        raw[-1]["score"]["regulation"]["home"] = "bad"
    body = json.dumps({"data": raw, "meta": {"page": 1, "per_page": 100, "total_pages": 1}}).encode()
    snapshot = snapshots.save("the_stats_api", "historical_matches", body,
                              "application/json", receipt, URL)
    records = [_record(row, QUERY, NOW) for row in raw]
    archive.ingest({"provider": "the_stats_api", "league_code": "comp_4643",
        "season_code": "sn_1", "records": records, "columns": sorted(raw[0])},
        source_url=URL, fetched_at=receipt, content_hash=snapshot.content_hash)
    with database.connect() as db:
        source = dict(db.execute("SELECT * FROM sports_archive_imports ORDER BY import_id DESC LIMIT 1").fetchone())
    return {"source": source, "records": records, "snapshot": snapshot}


def version_count(database):
    with database.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='historical_result_versions'").fetchone():
            return 0
        return db.execute("SELECT count(*) FROM historical_result_versions").fetchone()[0]


def legacy_rows(database):
    with database.connect() as db:
        return {name: [tuple(row) for row in db.execute(f'SELECT * FROM "{name}" ORDER BY 1')]
                for name in ("raw_snapshots", "sports_archive_imports",
                             "sports_archive_observations", "sports_archive_import_records")}


def watermark(root):
    return root / "data/arthur/historical-results-sync/watermark.json"


def run(root, **kwargs):
    return sync.sync_stats_results(root, **{"clock": lambda: NOW, **kwargs})


def test_bounded_ordered_catchup_of_already_backfilled_imports_is_zero_write_and_zero_api(tmp_path, monkeypatch):
    database, archive, snapshots = setup(tmp_path)
    csv = b"Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nE0,18/10/26,Home,Away,2,1,H\n"
    fd = snapshots.save("football_data_co_uk", "historical_csv", csv, "text/csv",
        NOW - timedelta(hours=2), "https://www.football-data.co.uk/mmz4281/2627/E0.csv")
    archive.ingest(parse_csv(csv, "E0", "2627", NOW.date()), source_url=fd.source_url,
                   fetched_at=fd.fetched_at, content_hash=fd.content_hash)
    imports = [add_import(database, archive, snapshots, [i]) for i in (1, 2, 3)]
    store = HistoricalResults(database, clock=lambda: NOW)
    store.initialize()
    for item in imports:
        store.ingest([normalize_archive_result(row, row["raw"], observed_at=item["source"]["fetched_at"],
            source_url=URL, source_sha256=item["source"]["content_hash"]) for row in item["records"]])
    before, raw_bytes = legacy_rows(database), {path: path.read_bytes() for path in (tmp_path / "data/raw").rglob("*.bin")}
    protected = tmp_path / "data/arthur/the-stats-history/checkpoint.json"
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"existing checkpoint unchanged")

    def forbidden(*args, **kwargs):
        raise AssertionError("sync must not call a provider or scan the full unified view")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(HistoricalResults, "view", forbidden)
    first = run(tmp_path, max_imports=2)
    assert first["status"] == "BATCH_LIMIT" and first["processed_imports"] == 2
    assert first["pending_imports"] == 1
    assert first["after_import_id"] == imports[1]["source"]["import_id"]
    assert first["counts"] == {"matches_created": 0, "versions_created": 0, "accepted_rows": 2, "rejected_rows": 0}
    second = run(tmp_path, max_imports=2)
    assert second["status"] == "COMPLETE" and second["processed_imports"] == 1
    assert second["pending_imports"] == 0 and second["counts"]["versions_created"] == 0
    assert run(tmp_path)["processed_imports"] == 0
    assert version_count(database) == 3 and second["external_requests"] == 0
    assert legacy_rows(database) == before
    assert all(path.read_bytes() == data for path, data in raw_bytes.items())
    assert protected.read_bytes() == b"existing checkpoint unchanged"
    assert _read(watermark(tmp_path))["after_import_id"] == imports[-1]["source"]["import_id"]


def test_original_receipt_and_actual_sink_ingestion_have_separate_point_in_time_boundaries(tmp_path):
    database, archive, snapshots = setup(tmp_path)
    item = add_import(database, archive, snapshots, [1])
    result = run(tmp_path)
    assert result["status"] == "COMPLETE" and result["counts"]["versions_created"] == 1
    store = HistoricalResults(database)
    assert store.view(as_of=NOW - timedelta(microseconds=1))["matches"] == []
    saved = store.view(as_of=NOW)["matches"][0]["source_records"][0]
    assert datetime.fromisoformat(saved["observed_at"]) == datetime.fromisoformat(item["source"]["fetched_at"])
    assert datetime.fromisoformat(saved["ingested_at"]) == NOW
    assert saved["record"]["raw"] == item["records"][0]["raw"]


def test_crash_after_sink_before_watermark_retries_without_new_rows_or_receipts(tmp_path, monkeypatch):
    database, archive, snapshots = setup(tmp_path)
    add_import(database, archive, snapshots, [1])
    before = legacy_rows(database)
    atomic = sync._atomic

    def crash(path, value):
        if Path(path).name == "watermark.json":
            raise OSError("crash after sink")
        atomic(path, value)

    monkeypatch.setattr(sync, "_atomic", crash)
    with pytest.raises(OSError, match="crash after sink"):
        run(tmp_path)
    assert version_count(database) == 1 and not watermark(tmp_path).exists()
    monkeypatch.setattr(sync, "_atomic", atomic)
    resumed = run(tmp_path, clock=lambda: NOW + timedelta(hours=1))
    assert resumed["status"] == "COMPLETE" and resumed["processed_imports"] == 1
    assert resumed["counts"]["versions_created"] == resumed["counts"]["matches_created"] == 0
    assert version_count(database) == 1 and legacy_rows(database) == before


@pytest.mark.parametrize("damage,reason", [
    ("bytes", "CORRUPT_RAW_SNAPSHOT"), ("future", "FUTURE_SOURCE_PROOF"),
    ("raw_time", "ARCHIVE_RECEIPT_PRECEDES_RAW_RECEIPT"), ("path", "RAW_PATH_ESCAPES_ARCHIVE"),
    ("provider", "MISSING_RAW_SOURCE_LINK"), ("normalized", "CORRUPT_ARCHIVE_OBSERVATION"),
])
def test_invalid_source_stops_in_import_order_without_advancing_past_it(tmp_path, damage, reason):
    database, archive, snapshots = setup(tmp_path)
    first = add_import(database, archive, snapshots, [1])
    second = add_import(database, archive, snapshots, [2])
    add_import(database, archive, snapshots, [3])
    source = second["source"]
    with database.transaction() as db:
        if damage == "bytes":
            (tmp_path / "data/raw" / second["snapshot"].relative_path).write_bytes(b"changed")
        elif damage == "future":
            db.execute("DROP TRIGGER sports_archive_imports_no_update")
            db.execute("UPDATE sports_archive_imports SET fetched_at=? WHERE import_id=?",
                       ((NOW + timedelta(seconds=1)).isoformat(), source["import_id"]))
        elif damage == "raw_time":
            db.execute("UPDATE raw_snapshots SET fetched_at=? WHERE content_hash=?",
                       ((NOW - timedelta(minutes=1)).isoformat(), source["content_hash"]))
        elif damage == "path":
            db.execute("UPDATE raw_snapshots SET relative_path='../outside.json' WHERE content_hash=?",
                       (source["content_hash"],))
        elif damage == "provider":
            db.execute("UPDATE raw_snapshots SET provider='api_football' WHERE content_hash=?",
                       (source["content_hash"],))
        else:
            db.execute("DROP TRIGGER sports_archive_observations_no_update")
            db.execute("UPDATE sports_archive_observations SET home_team='Changed' WHERE source_record_key='mt_2'")
    before = legacy_rows(database)
    result = run(tmp_path)
    assert result["status"] == "INVALID_SOURCE" and result["error_code"] == reason
    assert result["processed_imports"] == 1 and result["pending_imports"] == 2
    assert result["after_import_id"] == first["source"]["import_id"]
    assert result["invalid_import_id"] == second["source"]["import_id"]
    assert version_count(database) == 1 and legacy_rows(database) == before
    again = run(tmp_path)
    assert again["processed_imports"] == 0 and again["error_code"] == reason
    assert again["after_import_id"] == first["source"]["import_id"]


def test_semantically_invalid_row_is_durably_quarantined_while_valid_sibling_advances(tmp_path):
    database, archive, snapshots = setup(tmp_path)
    item = add_import(database, archive, snapshots, [1, 2], invalid_score=True)
    before = legacy_rows(database)
    result = run(tmp_path)
    assert result["status"] == "COMPLETE" and result["processed_imports"] == 1
    assert result["counts"] == {"matches_created": 1, "versions_created": 1, "accepted_rows": 1, "rejected_rows": 1}
    assert result["rejections"] == {"INVALID_SCORE": 1}
    audit = _read(tmp_path / "data/arthur/historical-results-sync/rejections" / f"import-{item['source']['import_id']}.json")
    assert audit["source_sha256"] == item["source"]["content_hash"]
    assert audit["rejections"][0]["source_record_key"] == "mt_2"
    assert audit["rejections"][0]["reason"] == "INVALID_SCORE"
    assert version_count(database) == 1 and legacy_rows(database) == before
    assert run(tmp_path)["processed_imports"] == 0


def test_clock_rollback_after_sink_cannot_commit_a_watermark_and_is_retryable(tmp_path, monkeypatch):
    database, archive, snapshots = setup(tmp_path)
    add_import(database, archive, snapshots, [1])
    current = [NOW]
    ingest = HistoricalResults.ingest

    def rollback_after_commit(store, records):
        result = ingest(store, records)
        current[0] -= timedelta(microseconds=1)
        return result

    monkeypatch.setattr(HistoricalResults, "ingest", rollback_after_commit)
    result = run(tmp_path, clock=lambda: current[0])
    assert result["status"] == "INVALID_SOURCE" and result["error_code"] == "CLOCK_MOVED_BACKWARDS"
    assert result["after_import_id"] == result["processed_imports"] == 0
    assert not watermark(tmp_path).exists() and version_count(database) == 1
    monkeypatch.setattr(HistoricalResults, "ingest", ingest)
    current[0] = NOW + timedelta(hours=1)
    assert run(tmp_path, clock=lambda: current[0])["counts"]["versions_created"] == 0
    saved = watermark(tmp_path).read_bytes()
    current[0] -= timedelta(microseconds=1)
    backwards = run(tmp_path, clock=lambda: current[0])
    assert backwards["status"] == "INVALID_SOURCE" and backwards["error_code"] == "CLOCK_MOVED_BACKWARDS"
    assert backwards["after_import_id"] == _read(watermark(tmp_path))["after_import_id"]
    assert watermark(tmp_path).read_bytes() == saved


def test_watermark_checksum_or_restored_target_cannot_silently_skip_work(tmp_path):
    database, archive, snapshots = setup(tmp_path)
    add_import(database, archive, snapshots, [1])
    assert run(tmp_path)["status"] == "COMPLETE"
    original = watermark(tmp_path).read_bytes()
    damaged = json.loads(original)
    damaged["data"]["after_import_id"] += 1
    watermark(tmp_path).write_text(json.dumps(damaged), encoding="utf-8")
    assert run(tmp_path)["error_code"] == "CHECKPOINT_DAMAGED"
    assert version_count(database) == 1
    watermark(tmp_path).write_bytes(original)
    with database.transaction() as db:
        db.execute("DROP TRIGGER historical_result_versions_no_delete")
        db.execute("DELETE FROM historical_result_versions")
    assert run(tmp_path)["error_code"] == "SYNC_WATERMARK_TARGET_MISMATCH"
    assert watermark(tmp_path).read_bytes() == original


def test_missing_database_and_invalid_budget_never_create_a_legacy_database(tmp_path):
    result = run(tmp_path)
    assert result["status"] == "INVALID_SOURCE" and result["external_requests"] == 0
    assert not (tmp_path / "data").exists()
    for value in (0, -1, True, 1.5, 1001):
        with pytest.raises(ValueError, match="MAX_IMPORTS"):
            run(tmp_path, max_imports=value)
