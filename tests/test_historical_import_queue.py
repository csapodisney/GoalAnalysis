"""Shared offline history budgets and durable fences across immutable queues."""
import copy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs import historical_import_queue as queue_job
from goal_analysis.jobs import the_stats_history as history
from goal_analysis.providers.the_stats_api import TheStatsApiUsage

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
RETAINED_AT = NOW - timedelta(days=2)


def queue(as_of=NOW):
    main = history.prepare_plan(["comp_4643"], as_of=RETAINED_AT, years=5)
    return queue_job.prepare_queue(main, as_of=as_of)


def test_a_partial_window_cannot_claim_a_full_five_year_history():
    partial = history.prepare_plan(["comp_4643"], as_of=RETAINED_AT, years=5,
        date_from="2026-10-01", date_to="2026-10-17")
    with pytest.raises(ValueError, match="COMPLETE_FIVE_YEAR_MAIN_WINDOW_REQUIRED"):
        queue_job.prepare_queue(partial, as_of=NOW)


def result(status="COMPLETE", *, requests=0, pages=0, usage=None, http_status=None):
    return {"status": status, "requests": requests, "processed_pages": pages,
        "observed_at": NOW.isoformat(), "usage": asdict(usage or TheStatsApiUsage()),
        "http_status": http_status}


class Runner:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, root, plan, **kwargs):
        self.calls.append((plan, kwargs))
        return copy.deepcopy(self.results.pop(0))


@pytest.fixture
def no_sync(monkeypatch):
    from goal_analysis.jobs import historical_result_sync

    calls = []

    def synchronized(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "COMPLETE", "external_requests": 0}

    monkeypatch.setattr(historical_result_sync, "sync_stats_results", synchronized)
    return calls


def execute(root, selected, **kwargs):
    return queue_job.run_queue(root, selected, execute=True, daily_work_complete=True,
        clock=lambda: NOW, **kwargs)


def test_clock_rollback_between_cached_plans_stops_before_second_plan(tmp_path, monkeypatch, no_sync):
    current, calls = [NOW], []

    def cached(root, plan, **kwargs):
        calls.append(plan)
        current[0] = NOW - timedelta(seconds=1)
        return result()

    monkeypatch.setattr(history, "run_history", cached)
    with pytest.raises(ValueError, match="QUEUE_CLOCK_MOVED_BACKWARDS"):
        queue_job.run_queue(tmp_path, queue(), execute=True, daily_work_complete=True,
            clock=lambda: current[0])
    assert len(calls) == 1


def test_transport_failure_is_not_an_authenticated_quota_receipt(tmp_path, no_sync):
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(True)
        raise OSError("synthetic offline failure")

    selected = queue()
    report = execute(tmp_path, selected, api_key="synthetic-not-real", transport=unavailable)
    assert report["status"] == "PROVIDER_STOPPED" and report["requests"] == 1
    assert report["plans"][0]["error_code"] == "TRANSPORT_UNAVAILABLE"
    assert report["plans"][0]["authenticated_quota"] is None
    assert len(calls) == 1
    assert not (tmp_path / "data/arthur/historical-import-queue/quota-state.json").exists()
    delta = selected["recent_delta_plan"]["plan_sha256"]
    assert not (tmp_path / "data/arthur/the-stats-history" / delta / "quota-state.json").exists()


def test_recent_gap_is_first_and_retained_five_year_plan_is_detached_unchanged():
    main = history.prepare_plan(["comp_4643", "comp_8321"], as_of=RETAINED_AT, years=5)
    original = copy.deepcopy(main)
    selected = queue_job.prepare_queue(main, as_of=NOW)
    assert main == original == selected["retained_main_plan"]
    assert selected["horizon_start"] == "2021-10-20"
    assert selected["horizon_end"] == "2026-10-19"
    delta = selected["recent_delta_plan"]
    assert delta["date_from"] == "2026-10-18" and delta["date_to"] == "2026-10-19"
    assert delta["competition_ids"] == main["competition_ids"]
    ends = [row["date_to"] for row in delta["queries"]]
    assert ends == sorted(ends, reverse=True)
    main["competition_ids"].append("comp_9999")
    assert selected["retained_main_plan"] == original
    queue_job.validate_queue(selected)


def test_no_gap_keeps_only_main_and_rejects_wrong_horizon_or_future_plan():
    current = history.prepare_plan(["comp_4643"], as_of=NOW, years=5)
    assert queue_job.prepare_queue(current, as_of=NOW)["recent_delta_plan"] is None
    old = history.prepare_plan(["comp_4643"], as_of=RETAINED_AT, years=10)
    with pytest.raises(ValueError, match="RETAINED_PAST_FIVE_YEAR_PLAN_REQUIRED"):
        queue_job.prepare_queue(old, as_of=NOW)
    with pytest.raises(ValueError, match="RETAINED_PAST_FIVE_YEAR_PLAN_REQUIRED"):
        queue_job.prepare_queue(current, as_of=RETAINED_AT)


def test_plan_only_daily_gate_and_expired_window_do_not_touch_disk_or_providers(tmp_path, monkeypatch):
    runner = Runner()
    monkeypatch.setattr(history, "run_history", runner)
    selected = queue()
    assert queue_job.run_queue(tmp_path, selected, clock=lambda: NOW)["status"] == "PLAN_ONLY"
    assert queue_job.run_queue(tmp_path, selected, execute=True,
        clock=lambda: NOW)["status"] == "DAILY_WORK_HAS_PRIORITY"
    assert execute(tmp_path, selected, stop_at=NOW)["status"] == "TIME_WINDOW_EXHAUSTED"
    assert not tmp_path.joinpath("data").exists() and not runner.calls


def test_two_plans_share_actual_remaining_request_and_page_budgets(tmp_path, monkeypatch, no_sync):
    selected = queue()
    runner = Runner(result(requests=7, pages=8), result("REQUEST_LIMIT", requests=13, pages=12))
    monkeypatch.setattr(history, "run_history", runner)
    report = execute(tmp_path, selected, max_requests=20, max_pages=20)
    assert [row[0]["plan_sha256"] for row in runner.calls] == [
        selected["recent_delta_plan"]["plan_sha256"], selected["retained_main_plan"]["plan_sha256"]]
    assert [(row[1]["max_requests"], row[1]["max_pages"]) for row in runner.calls] == [(20, 20), (13, 12)]
    assert all(row[1]["unified_results"] is True for row in runner.calls)
    assert report["requests"] == report["processed_pages"] == 20
    assert len(no_sync) == 1 and no_sync[0][1]["max_imports"] == 20


def test_page_budget_caps_cached_replays_and_zero_request_budget_still_allows_cache(tmp_path, monkeypatch, no_sync):
    runner = Runner(result(pages=20))
    monkeypatch.setattr(history, "run_history", runner)
    report = execute(tmp_path / "full", queue(), max_requests=0)
    assert report["status"] == "PAGE_BATCH_LIMIT" and len(runner.calls) == 1
    assert runner.calls[0][1]["max_requests"] == 0
    runner = Runner(result(pages=2), result(pages=3))
    monkeypatch.setattr(history, "run_history", runner)
    report = execute(tmp_path / "cache", queue(), max_requests=0)
    assert report["status"] == "COMPLETE" and report["requests"] == 0
    assert report["processed_pages"] == 5
    assert [(row[1]["max_requests"], row[1]["max_pages"]) for row in runner.calls] == [(0, 20), (0, 18)]


@pytest.mark.parametrize(("usage", "reason"), [
    (TheStatsApiUsage(monthly_remaining=0), "QUOTA_RESERVED_FOR_DAILY_WORK"),
    (TheStatsApiUsage(minute_remaining=0, minute_reset=int((NOW + timedelta(minutes=5)).timestamp())),
        "QUOTA_RESERVED_FOR_DAILY_WORK"),
    (TheStatsApiUsage(retry_after=600), "PROVIDER_RETRY_AFTER"),
])
def test_provider_quota_fence_survives_delta_main_and_different_queue(tmp_path, monkeypatch, no_sync, usage, reason):
    runner = Runner(result(requests=1, pages=1, usage=usage), result("REQUEST_LIMIT"),
        result("REQUEST_LIMIT"))
    monkeypatch.setattr(history, "run_history", runner)
    first = execute(tmp_path, queue())
    assert first["status"] == reason
    assert [row[1]["max_requests"] for row in runner.calls] == [20, 0]
    later = NOW + timedelta(minutes=1)
    second = queue_job.run_queue(tmp_path, queue(later), execute=True, daily_work_complete=True,
        clock=lambda: later)
    assert second["status"] == reason and runner.calls[-1][1]["max_requests"] == 0
    saved = history._read(tmp_path / "data/arthur/historical-import-queue/quota-state.json")
    assert saved["observed_at"] == NOW.isoformat() and saved["usage"] == asdict(usage)


def test_authentication_response_stops_whole_queue_and_persists_access_fence(tmp_path, monkeypatch, no_sync):
    runner = Runner(result("PROVIDER_STOPPED", requests=1, http_status=403), result("REQUEST_LIMIT"))
    monkeypatch.setattr(history, "run_history", runner)
    assert execute(tmp_path, queue())["status"] == "PROVIDER_STOPPED"
    assert len(runner.calls) == 1
    second = execute(tmp_path, queue())
    assert second["status"] == "PROVIDER_ACCESS_REQUIRES_RECHECK"
    assert runner.calls[-1][1]["max_requests"] == 0
    saved = history._read(tmp_path / "data/arthur/historical-import-queue/quota-state.json")
    assert saved["http_status"] == 403 and saved["observed_at"] == NOW.isoformat()


def test_cached_only_results_cannot_erase_prior_authenticated_quota_state(tmp_path, monkeypatch, no_sync):
    work = tmp_path / "data/arthur/historical-import-queue"
    path = work / "quota-state.json"
    state = {"provider": history.PROVIDER, "observed_at": NOW.isoformat(),
        "usage": asdict(TheStatsApiUsage(monthly_remaining=100)), "status": "COMPLETE", "http_status": None}
    history._atomic(path, state)
    original = path.read_bytes()
    runner = Runner(result(pages=1), result(pages=2))
    monkeypatch.setattr(history, "run_history", runner)
    report = execute(tmp_path, queue(), monthly_reserve=100)
    assert report["status"] == "COMPLETE" and all(row[1]["max_requests"] == 0 for row in runner.calls)
    assert path.read_bytes() == original


@pytest.mark.parametrize("parent_state_name", ["quota-state.json", "last-batch.json"])
def test_newer_retained_plan_quota_blocks_delta_despite_older_global_state(
        tmp_path, monkeypatch, no_sync, parent_state_name):
    selected = queue()
    main = selected["retained_main_plan"]
    global_path = tmp_path / "data/arthur/historical-import-queue/quota-state.json"
    old_state = {"provider": history.PROVIDER,
        "observed_at": (NOW - timedelta(minutes=2)).isoformat(),
        "usage": asdict(TheStatsApiUsage(monthly_remaining=5000)),
        "status": "COMPLETE", "http_status": None}
    history._atomic(global_path, old_state)
    parent_path = tmp_path / "data/arthur/the-stats-history" / main["plan_sha256"] / parent_state_name
    newer_state = {"provider": history.PROVIDER, "plan_sha256": main["plan_sha256"],
        "observed_at": (NOW - timedelta(minutes=1)).isoformat(), "requests": 1,
        "usage": asdict(TheStatsApiUsage(monthly_remaining=0)),
        "status": "QUOTA_RESERVED_FOR_DAILY_WORK", "http_status": None}
    history._atomic(parent_path, newer_state)
    parent_before = parent_path.read_bytes()
    runner = Runner(result("REQUEST_LIMIT"))
    monkeypatch.setattr(history, "run_history", runner)
    report = execute(tmp_path, selected)
    assert runner.calls[0][0]["plan_sha256"] == selected["recent_delta_plan"]["plan_sha256"]
    assert runner.calls[0][1]["max_requests"] == 0
    assert report["status"] == "QUOTA_RESERVED_FOR_DAILY_WORK" and report["requests"] == 0
    assert parent_path.read_bytes() == parent_before


def test_invalid_delta_page_halts_without_starting_retained_main(tmp_path, monkeypatch, no_sync):
    runner = Runner(result("INVALID_PAGE", requests=1))
    monkeypatch.setattr(history, "run_history", runner)
    report = execute(tmp_path, queue())
    assert report["status"] == "INVALID_PAGE" and len(runner.calls) == 1
    assert report["requests"] == 1


def test_boolean_and_out_of_range_budgets_rejected_before_disk_mutation(tmp_path):
    selected = queue()
    for kwargs in ({"execute": 1}, {"daily_work_complete": 1}, {"max_requests": True},
                   {"max_requests": 21}, {"max_requests": -1}, {"max_pages": True},
                   {"max_pages": 0}, {"max_pages": 21}, {"monthly_reserve": False},
                   {"monthly_reserve": -1}):
        with pytest.raises(ValueError, match="INVALID_QUEUE_EXECUTION_BUDGET"):
            queue_job.run_queue(tmp_path, selected, clock=lambda: NOW, **kwargs)
    assert not (tmp_path / "data").exists()


def test_queue_manifest_and_existing_saved_queue_are_immutable(tmp_path):
    selected = queue()
    path = tmp_path / "queue.json"
    queue_job.save_queue(path, selected)
    original = path.read_bytes()
    damaged = copy.deepcopy(selected)
    damaged["order"] = "RETAINED_FIRST"
    with pytest.raises(ValueError, match="QUEUE_CHANGED_OR_DAMAGED"):
        queue_job.run_queue(tmp_path, damaged, clock=lambda: NOW)
    with pytest.raises(ValueError, match="EXISTING_QUEUE_MUST_NOT_CHANGE"):
        queue_job.save_queue(path, queue(NOW + timedelta(hours=1)))
    assert path.read_bytes() == original


def test_one_global_work_lock_covers_different_immutable_queue_versions(tmp_path, monkeypatch, no_sync):
    first, second = queue(), queue(NOW + timedelta(hours=1))
    assert first["queue_sha256"] != second["queue_sha256"]
    work = tmp_path / "data/arthur/historical-import-queue"
    work.mkdir(parents=True)
    runner = Runner()
    monkeypatch.setattr(history, "run_history", runner)
    with history._lock(work / "queue.lock"), pytest.raises(
            ValueError, match="HISTORY_IMPORT_ALREADY_RUNNING"):
        queue_job.run_queue(tmp_path, second, execute=True, daily_work_complete=True,
            clock=lambda: NOW + timedelta(hours=1))
    assert not runner.calls and not no_sync


def test_retained_completed_checkpoint_is_not_restarted_or_rewritten(tmp_path, no_sync):
    selected = queue()
    paths = []
    for plan in (selected["recent_delta_plan"], selected["retained_main_plan"]):
        checkpoint = tmp_path / "data/arthur/the-stats-history" / plan["plan_sha256"] / "checkpoint.json"
        history._atomic(checkpoint, {"plan_sha256": plan["plan_sha256"],
            "query_index": len(plan["queries"]), "page": 1, "total_pages": None})
        paths.append((checkpoint, checkpoint.read_bytes()))
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("completed historical plans must not consume provider calls")

    report = execute(tmp_path, selected, max_requests=0, api_key="synthetic-no-real-key", transport=forbidden)
    assert report["status"] == "COMPLETE" and report["requests"] == report["processed_pages"] == 0
    assert not calls and all(path.read_bytes() == original for path, original in paths)
