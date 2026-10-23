"""Bounded, opt-in history runs after daily publication, never before it."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest
from test_nightly import setup_workflow
from test_the_stats_history import KEY, NOW, Transport, page, plan

from goal_analysis.jobs import the_stats_history as history
from goal_analysis.jobs.nightly import NightlyWorkflow


def job():
    return {"deadline": (NOW + timedelta(hours=2)).isoformat(),
            "stages": [{"name": "archive", "status": "SUCCESS"}]}


def activate(root, selected=None):
    path = root / "data/arthur/the-stats-history/active-plan.json"
    history.save_plan(path, selected or plan())
    return path


def test_missing_plan_incomplete_daily_or_short_window_never_calls(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(history, "run_history", lambda *a, **kw: calls.append(kw))
    workflow = NightlyWorkflow(tmp_path, clock=lambda: NOW)
    state = job()
    assert workflow._the_stats_history(state)["status"] == "SKIPPED_NO_ACTIVE_PLAN"
    activate(tmp_path)
    state["stages"][0]["status"] = "PARTIAL"
    assert workflow._the_stats_history(state)["status"] == "SKIPPED_DAILY_INCOMPLETE"
    state["stages"][0]["status"] = "SUCCESS"
    state["deadline"] = (NOW + timedelta(seconds=44)).isoformat()
    assert workflow._the_stats_history(state)["status"] == "SKIPPED_NO_SPARE_WINDOW"
    assert not calls and "the_stats_history" not in state
    assert not (tmp_path / "data/goal_analysis.sqlite3").exists()


def test_claim_once_and_bounded_deadline_leave_shared_workflow_reserve(tmp_path, monkeypatch):
    activate(tmp_path)
    saved, calls = [], []
    workflow = NightlyWorkflow(tmp_path, clock=lambda: NOW)
    monkeypatch.setattr(workflow, "_save", lambda state: saved.append(deepcopy(state)))

    def fake(root, selected, **kwargs):
        calls.append(kwargs)
        assert saved[0]["the_stats_history"]["status"] == "STARTED"
        assert selected == plan()
        return {"status": "PAGE_BATCH_LIMIT", "requests": 20}

    monkeypatch.setattr(history, "run_history", fake)
    state = job()
    state["deadline"] = (NOW + timedelta(seconds=90)).isoformat()
    assert workflow._the_stats_history(state)["requests"] == 20
    workflow._the_stats_history(state)
    assert len(calls) == 1
    assert calls[0]["execute"] is calls[0]["daily_work_complete"] is True
    assert calls[0]["max_requests"] == 20 and calls[0]["monthly_reserve"] == 100
    assert calls[0]["stop_at"] == NOW + timedelta(seconds=75)
    assert saved[-1]["the_stats_history"]["status"] == "PAGE_BATCH_LIMIT"


def test_invalid_plan_or_failure_is_sanitized_and_claim_not_replayed(tmp_path, monkeypatch):
    path = activate(tmp_path)
    workflow = NightlyWorkflow(tmp_path, clock=lambda: NOW)
    monkeypatch.setattr(workflow, "_save", lambda state: None)
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("Authorization: Bearer secret-that-must-not-leak")

    monkeypatch.setattr(history, "run_history", fail)
    state = job()
    result = workflow._the_stats_history(state)
    assert result == {"status": "FAILED", "error_code": "THE_STATS_HISTORY_UNAVAILABLE"}
    workflow._the_stats_history(state)
    state["the_stats_history"] = {"status": "STARTED"}
    workflow._the_stats_history(state)
    assert len(calls) == 1 and "secret" not in json.dumps(result)
    path.write_text("corrupt plan", encoding="utf-8")
    assert workflow._the_stats_history(job())["status"] == "FAILED"
    assert len(calls) == 1


@pytest.mark.parametrize(("http_status", "code"), [(403, "KEY_REVOKED"), (429, "RATE_LIMITED")])
def test_provider_stop_is_not_retried_by_nightly(tmp_path, monkeypatch, http_status, code):
    activate(tmp_path)
    workflow = NightlyWorkflow(tmp_path, clock=lambda: NOW)
    monkeypatch.setattr(workflow, "_save", lambda state: None)
    transport = Transport(({"error": {"code": code, "message": KEY}}, {}, http_status))
    original = history.run_history
    monkeypatch.setattr(history, "run_history", lambda *a, **kw: original(
        *a, api_key=KEY, transport=transport, **kw))
    state = job()
    result = workflow._the_stats_history(state)
    workflow._the_stats_history(state)
    assert result["status"] == "PROVIDER_STOPPED" and result["http_status"] == http_status
    assert result["requests"] == len(transport.calls) == 1
    assert KEY not in json.dumps(result)


def test_deadline_before_next_request_retains_first_page_and_resumes(tmp_path):
    clock = [NOW]
    calls = []

    def slow(url, headers, *, timeout):
        calls.append(url)
        assert timeout == 20
        clock[0] += timedelta(seconds=20)
        return page(1, 2), {}, 200

    result = history.run_history(
        tmp_path, plan(), execute=True, daily_work_complete=True, api_key=KEY,
        transport=slow, clock=lambda: clock[0], stop_at=NOW + timedelta(seconds=40),
    )
    assert result["status"] == "TIME_WINDOW_EXHAUSTED"
    assert result["requests"] == result["processed_pages"] == len(calls) == 1
    assert result["cursor"]["page"] == 2
    resumed = history.run_history(
        tmp_path, plan(), execute=True, daily_work_complete=True, api_key=KEY,
        transport=Transport(page(2, 2)), clock=lambda: clock[0],
        stop_at=NOW + timedelta(seconds=80),
    )
    assert resumed["status"] == "COMPLETE" and resumed["requests"] == 1


def test_expired_deadline_is_read_only_and_aware_timestamp_required(tmp_path):
    transport = Transport()
    options = {"execute": True, "daily_work_complete": True, "api_key": KEY,
               "transport": transport, "clock": lambda: NOW}
    result = history.run_history(tmp_path, plan(), stop_at=NOW, **options)
    assert result["status"] == "TIME_WINDOW_EXHAUSTED" and not transport.calls
    assert not (tmp_path / "data").exists()
    with pytest.raises(ValueError, match="AWARE_TIMESTAMP"):
        history.run_history(tmp_path, plan(), stop_at=NOW.replace(tzinfo=None), **options)


def test_full_workflow_freezes_before_history_and_preserves_published_report(tmp_path, monkeypatch):
    workflow, clock, calls = setup_workflow(tmp_path)
    activate(tmp_path, history.prepare_plan(["comp_4643"], as_of=clock[0]))
    monkeypatch.setattr(workflow, "_weather_history", lambda state: {"status": "COMPLETE"})
    published = {}

    def fake(root, selected, **kwargs):
        calls.append("history")
        freezes = workflow.ledger.list_freezes(date="2026-10-08")
        assert len(freezes) == 1
        saved = workflow.store.get("2026-10-08")
        assert workflow._stage(saved, "archive")["status"] == "SUCCESS"
        published["freeze"] = deepcopy(freezes)
        published["report"] = deepcopy(workflow.ledger.list_runs())
        assert "learning" in calls
        clock[0] += timedelta(minutes=5)
        return {"status": "COMPLETE", "requests": 20}

    monkeypatch.setattr(history, "run_history", fake)
    outcome = workflow.run()
    assert outcome["status"] == "COMPLETED" and calls[-1] == "history"
    assert workflow.ledger.list_freezes(date="2026-10-08") == published["freeze"]
    assert workflow.ledger.list_runs() == published["report"]
    result = workflow._stage(outcome["job"], "learning")["result"]
    assert result["the_stats_history"]["requests"] == 20
    assert workflow.run()["status"] == "ALREADY_FINISHED"
    assert calls.count("history") == 1
