import hashlib
import json
from datetime import UTC, datetime

import pytest

from goal_analysis.jobs import the_stats_history as history
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.sports_archive import SportsArchive

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)
KEY = "synthetic-key-never-real"


def plan(**kwargs):
    return history.prepare_plan(["comp_4643"], as_of=NOW,
                                date_from="2026-10-01", date_to="2026-10-17", **kwargs)


def match(number=1, **updates):
    return {"id": f"mt_{number}", "competition_id": "comp_4643", "season_id": "sn_1",
            "status": "finished", "utc_date": "2026-10-17T15:00:00Z",
            "home_team": {"id": "tm_1", "name": "Home"},
            "away_team": {"id": "tm_2", "name": "Away"},
            "score": {"regulation": {"home": 2, "away": 1}},
            "xg_available": True, "odds_available": True, **updates}


def page(number=1, total=1, rows=None):
    return {"data": rows if rows is not None else [match(number)],
            "meta": {"page": number, "per_page": 100, "total_pages": total}}


class Transport:
    def __init__(self, *responses, headers=None):
        self.responses, self.calls = list(responses), []
        self.headers = headers or {}

    def __call__(self, url, headers, *, timeout):
        self.calls.append(url)
        row = self.responses.pop(0)
        return row if isinstance(row, tuple) else (row, self.headers, 200)


def run(root, selected, transport, **kwargs):
    return history.run_history(root, selected, execute=True, daily_work_complete=True,
        api_key=KEY, transport=transport, clock=lambda: NOW, **kwargs)


def matches(root, **kwargs):
    return SportsArchive(Database(root / "data/goal_analysis.sqlite3")).matches(**kwargs)


def test_plan_only_and_daily_priority_never_call_or_initialize_database(tmp_path):
    transport = Transport()
    assert history.run_history(tmp_path, plan(), transport=transport,
                               clock=lambda: NOW)["status"] == "PLAN_ONLY"
    assert history.run_history(tmp_path, plan(), execute=True,
        transport=transport, clock=lambda: NOW)["status"] == "DAILY_WORK_HAS_PRIORITY"
    assert not transport.calls and not (tmp_path / "data").exists()


def test_paginated_resume_no_duplicate_results_and_actual_source_hash(tmp_path):
    first_raw = json.dumps(page(1, 2), indent=3).encode()
    transport = Transport((first_raw, {}, 200), page(2, 2, [match(1), match(2)]))
    selected = plan()
    first = run(tmp_path, selected, transport, max_requests=1)
    assert first["status"] == "REQUEST_LIMIT" and first["requests"] == 1
    assert first["cursor"]["page"] == 2
    assert first["pages"][0]["content_hash"] == hashlib.sha256(first_raw).hexdigest()
    second = run(tmp_path, selected, transport, max_requests=1)
    assert second["status"] == "COMPLETE" and second["requests"] == 1
    assert "page=2" in transport.calls[-1]
    assert len(matches(tmp_path)) == 2
    third = run(tmp_path, selected, transport)
    assert third["requests"] == 0 and len(transport.calls) == 2
    db = Database(tmp_path / "data/goal_analysis.sqlite3")
    assert SnapshotStore(db, tmp_path / "data/raw").read(
        first["pages"][0]["content_hash"]) == first_raw


def test_crash_after_ingest_before_checkpoint_replays_cache_without_api(tmp_path, monkeypatch):
    original = history._atomic
    def crash(path, value):
        if path.name == "checkpoint.json":
            raise OSError("simulated crash")
        original(path, value)
    monkeypatch.setattr(history, "_atomic", crash)
    transport = Transport(page())
    with pytest.raises(OSError, match="simulated"):
        run(tmp_path, plan(), transport)
    assert len(matches(tmp_path)) == 1
    monkeypatch.setattr(history, "_atomic", original)
    resumed = run(tmp_path, plan(), transport, max_requests=0)
    assert resumed["status"] == "COMPLETE"
    assert resumed["requests"] == 0 and resumed["cached_pages"] == 1
    assert len(transport.calls) == 1
    with Database(tmp_path / "data/goal_analysis.sqlite3").connect() as connection:
        assert connection.execute("SELECT count(*) FROM sports_archive_imports").fetchone()[0] == 1


@pytest.mark.parametrize(("status", "code"), [(403, "KEY_REVOKED"), (429, "RATE_LIMITED")])
def test_access_and_rate_errors_stop_without_retry_or_raw_secret_storage(tmp_path, status, code):
    transport = Transport(({"error": {"code": code, "message": KEY}}, {"Retry-After": "60"}, status))
    result = run(tmp_path, plan(), transport)
    assert result["status"] == "PROVIDER_STOPPED" and result["http_status"] == status
    assert result["requests"] == len(transport.calls) == 1
    assert KEY not in json.dumps(result)
    assert not list((tmp_path / "data/raw").rglob("*.bin"))


def test_twenty_request_cap_and_quota_reserve(tmp_path):
    transport = Transport(*(page(n, 30) for n in range(1, 21)))
    result = run(tmp_path, plan(), transport)
    assert result["requests"] == result["processed_pages"] == 20
    assert result["cursor"]["page"] == 21 and result["status"] == "PAGE_BATCH_LIMIT"
    with pytest.raises(ValueError, match="BUDGET"):
        run(tmp_path, plan(), transport, max_requests=21)
    quota = Transport(page(1, 2), headers={"X-Monthly-Quota-Remaining": "100"})
    result = run(tmp_path / "quota", plan(), quota)
    assert result["status"] == "QUOTA_RESERVED_FOR_DAILY_WORK" and len(quota.calls) == 1


def test_frozen_asof_unknown_publication_time_and_regulation_only(tmp_path):
    rows = [match(1, score={"home": 3, "away": 2,
                           "regulation": {"home": 1, "away": 1},
                           "after_extra_time": {"home": 3, "away": 2},
                           "went_to_extra_time": True}),
            match(2, score={"home": 5, "away": 4, "went_to_penalties": True})]
    result = run(tmp_path, plan(), Transport(page(rows=rows)))
    assert result["status"] == "COMPLETE"
    records = matches(tmp_path)
    assert (records[0]["full_time_home"], records[0]["full_time_away"]) == (1, 1)
    assert records[1]["full_time_home"] is None
    assert records[0]["kickoff_local_time"] is None
    assert records[0]["raw"]["utc_date"].endswith("Z")
    assert records[0]["odds_snapshot_types"] == {}
    assert records[0]["observed_at"].startswith("2026-10-18T12:00:00")
    assert matches(tmp_path, as_of=datetime(2026, 10, 18, 11, tzinfo=UTC)) == []
    db = Database(tmp_path / "data/goal_analysis.sqlite3")
    with db.connect() as connection:
        metadata = json.loads(connection.execute(
            "SELECT metadata_json FROM sports_archive_imports").fetchone()[0])
    assert metadata["historical_published_at"] is None
    assert metadata["cross_provider_identity"] == "UNMAPPED"


def test_bad_rows_are_quarantined_raw_preserved_and_exact_duplicates_not_added(tmp_path):
    rows = [match(), match(), match(2, status="scheduled"),
            match(3, competition_id="comp_999"), match(4, utc_date="2026-10-19T12:00:00Z"),
            match(5, home_team={"id": "api_football:1", "name": "Home"})]
    result = run(tmp_path, plan(), Transport(page(rows=rows)))
    assert result["pages"][0]["accepted_rows"] == 1
    assert result["pages"][0]["rejected_rows"] == 4
    assert len(matches(tmp_path)) == 1


def test_invalid_pagination_preserved_raw_but_not_advanced(tmp_path):
    result = run(tmp_path, plan(), Transport(page(2, 2)))
    assert result["status"] == "INVALID_PAGE"
    assert result["cursor"]["page"] == 1 and result["requests"] == 1
    again = run(tmp_path, plan(), Transport(), max_requests=0)
    assert again["status"] == "INVALID_PAGE" and again["requests"] == 0


def test_changed_page_total_stops_before_normalized_import(tmp_path):
    first = run(tmp_path, plan(), Transport(page(1, 3)), max_requests=1)
    assert first["cursor"]["total_pages"] == 3
    result = run(tmp_path, plan(), Transport(page(2, 4)))
    assert result["error_code"] == "PAGINATION_CHANGED_RESTART_WINDOW_REQUIRED"
    assert len(matches(tmp_path)) == 1


def test_damaged_cache_fails_closed_without_network(tmp_path, monkeypatch):
    original = history._ingest
    monkeypatch.setattr(history, "_ingest", lambda *a: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        run(tmp_path, plan(), Transport(page()))
    raw = next((tmp_path / "data/raw").rglob("*.bin"))
    raw.write_bytes(b"changed")
    monkeypatch.setattr(history, "_ingest", original)
    transport = Transport()
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        run(tmp_path, plan(), transport)
    assert not transport.calls


def test_explicit_ids_frozen_plan_save_and_leap_year_bounds(tmp_path):
    selected = history.prepare_plan(["comp_4643", "comp_8321"],
        as_of=datetime(2024, 2, 29, 12, tzinfo=UTC))
    assert selected["date_from"] == "2019-02-28"
    assert selected["years"] == 5
    assert [q["competition_id"] for q in selected["queries"][:2]] == ["comp_4643", "comp_8321"]
    path = tmp_path / "plan.json"
    history.save_plan(path, selected)
    assert history.load_plan(path) == selected
    with pytest.raises(ValueError, match="EXPLICIT"):
        history.prepare_plan(["78"], as_of=NOW)
    selected["queries"][0]["competition_id"] = "comp_999"
    with pytest.raises(ValueError, match="PLAN_CHANGED"):
        history.run_history(tmp_path, selected)


def test_five_year_default_is_newest_first_and_preserves_explicit_old_plan(tmp_path):
    selected = history.prepare_plan(["comp_4643", "comp_8321"], as_of=NOW)
    assert selected["date_from"] == "2021-10-18"
    assert selected["date_to"] == "2026-10-17"
    assert selected["queries"][0]["date_to"] == "2026-10-17"
    ends = [q["date_to"] for q in selected["queries"]]
    assert ends == sorted(ends, reverse=True)
    assert selected["queries"][-1]["date_from"] == "2021-10-18"
    older = history.prepare_plan(["comp_4643"], as_of=NOW, years=10)
    path = tmp_path / "retained-old-plan.json"
    history.save_plan(path, older)
    assert history.load_plan(path) == older
    assert older["date_from"] == "2016-10-18"
