"""Page-local historical dual writes and durable, network-free crash recovery."""
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs import the_stats_history as history
from goal_analysis.jobs.historical_result_sink import HistoricalResultSink
from goal_analysis.storage import Database
from goal_analysis.storage.historical_results import HistoricalResults
from goal_analysis.storage.sports_archive import SportsArchive

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
RECEIPT = NOW - timedelta(hours=3)
KEY = "synthetic-test-only-not-a-real-key"


def match(number=1, **changes):
    return {"id": f"mt_{number}", "competition_id": "comp_4643", "season_id": "sn_1",
            "status": "finished", "utc_date": "2026-10-19T15:00:00Z",
            "home_team": {"id": "tm_1", "name": "Home FC"},
            "away_team": {"id": "tm_2", "name": "Away FC"},
            "score": {"regulation": {"home": 2, "away": 1}},
            "xg_available": True, "odds_available": True, **changes}


def plan():
    return history.prepare_plan(["comp_4643"], as_of=NOW,
                                date_from="2026-10-01", date_to="2026-10-19")


def page(rows=None):
    return {"data": [match()] if rows is None else rows,
            "meta": {"page": 1, "per_page": 100, "total_pages": 1}}


class Transport:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, headers, *, timeout):
        self.calls.append(url)
        assert self.responses, "A cached-page replay must not make another provider request"
        return self.responses.pop(0), {}, 200


def run(root, selected, transport, **changes):
    return history.run_history(root, selected, execute=True, daily_work_complete=True,
        api_key=KEY, transport=transport, clock=lambda: NOW, **changes)


def database(root):
    return Database(root / "data/goal_analysis.sqlite3")


def checkpoint(root, selected):
    return root / "data/arthur/the-stats-history" / selected["plan_sha256"] / "checkpoint.json"


def counts(db):
    with db.connect() as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        return {table: connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                for table in ("sports_archive_imports", "sports_archive_observations",
                              "historical_result_matches", "historical_result_versions",
                              "historical_result_links") if table in tables}


def setup_sink(tmp_path):
    archive = SportsArchive(Database(tmp_path / "results.sqlite3"))
    archive.initialize()
    return archive, HistoricalResultSink(archive, clock=lambda: NOW)


def dataset(rows=None):
    selected = plan()
    rows = [match()] if rows is None else rows
    records = [history._record(row, selected["queries"][0], NOW) for row in rows]
    return {"provider": "the_stats_api", "league_code": "comp_4643", "season_code": "sn_1",
            "records": records, "columns": sorted({key for row in rows for key in row})}


def source(data, *, observed=RECEIPT):
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return {"source_url": "https://api.thestatsapi.com/api/football/matches?competition_id=comp_4643",
            "fetched_at": observed, "content_hash": hashlib.sha256(raw).hexdigest(),
            "metadata": {"fixture": "synthetic-test-page"}}


def forbidden_scan(*args, **kwargs):
    raise AssertionError("The incremental sink must not scan or render the historical archive")


def test_page_is_paired_in_real_archives_without_any_full_scan(tmp_path, monkeypatch):
    archive, sink = setup_sink(tmp_path)
    monkeypatch.setattr(SportsArchive, "matches", forbidden_scan)
    monkeypatch.setattr(SportsArchive, "summary", forbidden_scan)
    monkeypatch.setattr(HistoricalResults, "view", forbidden_scan)
    data = dataset([match(1), match(2)])
    legacy = sink.ingest(data, **source(data))
    assert legacy["matches_created"] == 2
    assert counts(archive.database) == {
        "sports_archive_imports": 1, "sports_archive_observations": 2,
        "historical_result_matches": 2, "historical_result_versions": 2,
        "historical_result_links": 0}
    summary = sink.summary()
    assert summary["mode"] == "SHADOW"
    assert summary["effective_data_path"] == "LEGACY_UNCHANGED"
    assert summary["external_requests"] == 0
    assert summary["reconciliation"] == "NATIVE_IDS_ONLY_INCREMENTAL"
    assert summary["counts"] == {
        "matches_created": 2, "versions_created": 2, "quarantined_records": 0}


def test_replaying_identical_page_is_idempotent_on_both_sides(tmp_path):
    archive, sink = setup_sink(tmp_path)
    data = dataset()
    sink.ingest(data, **source(data))
    before = counts(archive.database)
    second = sink.ingest(data, **source(data))
    assert counts(archive.database) == before
    assert second["matches_created"] == second["observations_created"] == 0
    assert sink.summary()["counts"]["versions_created"] == 1


def test_original_source_receipt_and_actual_ingestion_remain_distinct(tmp_path):
    archive, sink = setup_sink(tmp_path)
    data = dataset()
    sink.ingest(data, **source(data))
    assert sink.results.view(as_of=NOW - timedelta(microseconds=1))["matches"] == []
    entry = sink.results.view(as_of=NOW)["matches"][0]["source_records"][0]
    assert datetime.fromisoformat(entry["observed_at"]) == RECEIPT
    assert datetime.fromisoformat(entry["ingested_at"]) == NOW
    assert entry["record"]["source_sha256"] == source(data)["content_hash"]
    with archive.database.connect() as connection:
        stored = connection.execute("SELECT fetched_at FROM sports_archive_imports").fetchone()[0]
    assert datetime.fromisoformat(stored) == RECEIPT


def test_run_history_explicit_flag_enables_same_page_local_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(SportsArchive, "matches", forbidden_scan)
    monkeypatch.setattr(HistoricalResults, "view", forbidden_scan)
    transport = Transport(page([match(1), match(2)]))
    selected = plan()
    report = run(tmp_path, selected, transport, unified_results=True)
    assert report["status"] == "COMPLETE"
    assert report["requests"] == len(transport.calls) == 1
    assert report["processed_pages"] == 1
    assert report["pages"][0]["accepted_rows"] == 2
    assert report["unified_results"]["counts"]["versions_created"] == 2
    assert counts(database(tmp_path))["historical_result_versions"] == 2
    assert history._read(checkpoint(tmp_path, selected))["query_index"] == 1


def test_default_legacy_flow_creates_no_unified_tables_or_summary(tmp_path):
    report = run(tmp_path, plan(), Transport(page()))
    assert report["status"] == "COMPLETE"
    assert "unified_results" not in report
    assert counts(database(tmp_path)) == {
        "sports_archive_imports": 1, "sports_archive_observations": 1}


def test_crash_between_legacy_and_unified_repairs_from_receipt_without_another_api_call(
        tmp_path, monkeypatch):
    selected, transport = plan(), Transport(page())
    original = HistoricalResults.ingest

    def fail_after_legacy(*args, **kwargs):
        raise OSError("crash after legacy commit")

    monkeypatch.setattr(HistoricalResults, "ingest", fail_after_legacy)
    with pytest.raises(OSError, match="after legacy"):
        run(tmp_path, selected, transport, unified_results=True)
    before = counts(database(tmp_path))
    assert before["sports_archive_observations"] == 1
    assert before["historical_result_versions"] == 0
    assert not checkpoint(tmp_path, selected).exists()
    monkeypatch.setattr(HistoricalResults, "ingest", original)
    repaired = run(tmp_path, selected, transport, max_requests=0, unified_results=True)
    assert repaired["status"] == "COMPLETE"
    assert repaired["requests"] == 0 and repaired["cached_pages"] == 1
    assert len(transport.calls) == 1
    after = counts(database(tmp_path))
    assert after["sports_archive_imports"] == after["sports_archive_observations"] == 1
    assert after["historical_result_matches"] == after["historical_result_versions"] == 1
    assert repaired["unified_results"]["counts"]["versions_created"] == 1


def test_crash_after_both_commits_before_cursor_replays_without_duplicate_results(
        tmp_path, monkeypatch):
    selected, transport = plan(), Transport(page())
    original = history._atomic

    def fail_checkpoint(path, value):
        if path.name == "checkpoint.json":
            raise OSError("crash before cursor commit")
        original(path, value)

    monkeypatch.setattr(history, "_atomic", fail_checkpoint)
    with pytest.raises(OSError, match="cursor"):
        run(tmp_path, selected, transport, unified_results=True)
    before = counts(database(tmp_path))
    assert before["sports_archive_observations"] == before["historical_result_versions"] == 1
    assert not checkpoint(tmp_path, selected).exists()
    monkeypatch.setattr(history, "_atomic", original)
    resumed = run(tmp_path, selected, transport, max_requests=0, unified_results=True)
    assert resumed["status"] == "COMPLETE"
    assert resumed["requests"] == 0 and resumed["cached_pages"] == 1
    assert len(transport.calls) == 1
    assert counts(database(tmp_path)) == before
    assert resumed["unified_results"]["counts"] == {
        "matches_created": 0, "versions_created": 0, "quarantined_records": 0}


def test_legacy_only_unproven_regulation_row_is_explicitly_quarantined_without_losing_good_row(
        tmp_path):
    archive, sink = setup_sink(tmp_path)
    data = dataset([match(1), match(2)])
    data["records"][1]["raw"]["score"] = {"home": 2, "away": 1, "went_to_penalties": True}
    sink.ingest(data, **source(data))
    assert counts(archive.database)["sports_archive_observations"] == 2
    assert counts(archive.database)["historical_result_versions"] == 1
    summary = sink.summary()
    assert summary["counts"]["quarantined_records"] == 1
    assert summary["rejection_counts"] == {"ARCHIVE_RAW_SCORE_MISMATCH": 1}
    assert sink.results.view(as_of=NOW)["matches"][0]["source_records"][0]["record"]["provider_id"] == "mt_1"


def test_current_collector_missing_regulation_retains_unknown_score_without_using_totals(tmp_path):
    rows = [match(1), match(2, score={"home": 5, "away": 4, "went_to_penalties": True})]
    report = run(tmp_path, plan(), Transport(page(rows)), unified_results=True)
    assert report["unified_results"]["counts"]["quarantined_records"] == 0
    assert counts(database(tmp_path))["historical_result_versions"] == 2
    store = HistoricalResults(database(tmp_path), clock=lambda: NOW)
    records = [entry["source_records"][0]["record"] for entry in store.view(as_of=NOW)["matches"]]
    unknown = next(row for row in records if row["provider_id"] == "mt_2")
    assert unknown["home_goals"] is unknown["away_goals"] is None
    assert unknown["score_scope"] == "REGULATION"
    assert unknown["raw"]["score"]["home"] == 5


def test_future_source_receipt_is_hard_failure_not_silent_record_quarantine(tmp_path):
    archive, sink = setup_sink(tmp_path)
    data = dataset()
    with pytest.raises(ValueError, match="FUTURE"):
        sink.ingest(data, **source(data, observed=NOW + timedelta(seconds=1)))
    assert counts(archive.database)["historical_result_versions"] == 0
    assert sink.summary()["counts"]["quarantined_records"] == 0


def test_future_cached_receipt_cannot_advance_cursor_or_make_a_replacement_request(
        tmp_path, monkeypatch):
    selected, transport = plan(), Transport(page())
    original = history._ingest

    def crash_before_normalization(*args, **kwargs):
        raise OSError("stop after durable receipt")

    monkeypatch.setattr(history, "_ingest", crash_before_normalization)
    with pytest.raises(OSError, match="durable"):
        run(tmp_path, selected, transport, unified_results=True)
    monkeypatch.setattr(history, "_ingest", original)
    cached = checkpoint(tmp_path, selected).parent / "page-00000-000001.json"
    receipt = history._read(cached)
    receipt["observed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    history._atomic(cached, receipt)
    before = counts(database(tmp_path))
    with pytest.raises(ValueError, match="PROVENANCE"):
        run(tmp_path, selected, transport, max_requests=0, unified_results=True)
    assert len(transport.calls) == 1
    assert counts(database(tmp_path)) == before
    assert not checkpoint(tmp_path, selected).exists()
