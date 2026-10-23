"""A new batch must not spend around known authenticated response fences."""
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs import the_stats_history as history
from goal_analysis.jobs.history_quota import latest_quota, quota_block

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


def plan():
    return history.prepare_plan(["comp_4643"], as_of=NOW,
        date_from="2026-10-01", date_to="2026-10-19")


def page(number):
    return {"data": [{"id": "mt_" + str(number), "competition_id": "comp_4643",
        "season_id": "sn_1", "status": "finished", "utc_date": "2026-10-17T15:00:00Z",
        "home_team": {"id": "tm_1", "name": "Home"},
        "away_team": {"id": "tm_2", "name": "Away"},
        "score": {"regulation": {"home": 1, "away": 0}}}],
        "meta": {"page": number, "per_page": 100, "total_pages": 3}}


def run(root, transport, **kwargs):
    return history.run_history(root, plan(), execute=True, daily_work_complete=True,
        api_key="synthetic-not-real", transport=transport, **kwargs)


def test_per_plan_monthly_fence_survives_a_new_client_and_batch(tmp_path):
    calls = []

    def response(*args, **kwargs):
        calls.append(True)
        return page(1), {"X-Monthly-Quota-Remaining": "100"}, 200

    first = run(tmp_path, response, clock=lambda: NOW)
    assert first["requests"] == 1 and first["status"] == "QUOTA_RESERVED_FOR_DAILY_WORK"
    second = run(tmp_path, response, clock=lambda: NOW + timedelta(days=1))
    assert second["requests"] == 0 and second["status"] == "QUOTA_RESERVED_FOR_DAILY_WORK"
    assert len(calls) == 1


def test_minute_reset_releases_only_after_its_real_timestamp(tmp_path):
    calls = []

    def response(*args, **kwargs):
        number = len(calls) + 1
        calls.append(number)
        headers = {"X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(int((NOW + timedelta(minutes=1)).timestamp()))} if number == 1 else {}
        return page(number), headers, 200

    assert run(tmp_path, response, clock=lambda: NOW)["requests"] == 1
    assert run(tmp_path, response, clock=lambda: NOW + timedelta(seconds=59))["requests"] == 0
    after = run(tmp_path, response, clock=lambda: NOW + timedelta(seconds=61), max_requests=1)
    assert after["requests"] == 1 and calls == [1, 2]


def test_actual_page_budget_stops_a_single_plan_without_using_twenty_requests(tmp_path):
    calls = []

    def response(*args, **kwargs):
        calls.append(True)
        return page(len(calls)), {}, 200

    report = run(tmp_path, response, clock=lambda: NOW, max_pages=2)
    assert report["processed_pages"] == report["requests"] == len(calls) == 2
    assert report["status"] == "PAGE_BATCH_LIMIT" and report["cursor"]["page"] == 3


@pytest.mark.parametrize("cap", [True, False, 0, -1, 21, 1.5])
def test_invalid_page_budget_never_initializes_a_database(tmp_path, cap):
    with pytest.raises(ValueError, match="PAGE_BUDGET"):
        run(tmp_path, None, clock=lambda: NOW, max_pages=cap)
    assert not (tmp_path / "data").exists()


def test_new_partial_headers_preserve_known_month_fence_until_explicit_refresh():
    old = {"observed_at": NOW.isoformat(), "usage": {"monthly_remaining": 0}, "http_status": 200}
    partial = {"observed_at": (NOW + timedelta(seconds=1)).isoformat(),
               "usage": {"minute_remaining": 119}, "http_status": 200}
    state = latest_quota([old, partial], now=NOW + timedelta(seconds=2), reserve=100)
    assert quota_block(state, now=NOW + timedelta(seconds=2), reserve=100) == "QUOTA_RESERVED_FOR_DAILY_WORK"
    refreshed = {"observed_at": (NOW + timedelta(seconds=3)).isoformat(),
                 "usage": {"monthly_remaining": 500}, "http_status": 200}
    state = latest_quota([state, refreshed], now=NOW + timedelta(seconds=4), reserve=100)
    assert quota_block(state, now=NOW + timedelta(seconds=4), reserve=100) is None
    assert len(state.get("_receipts", [])) <= 4


def test_legacy_transport_error_summary_cannot_replace_an_older_known_fence():
    old = {"observed_at": NOW.isoformat(), "usage": {"monthly_remaining": 0}}
    failed = {"observed_at": (NOW + timedelta(seconds=1)).isoformat(),
        "requests": 1, "status": "PROVIDER_STOPPED", "error_code": "TRANSPORT_UNAVAILABLE",
        "http_status": None, "usage": {}}
    state = latest_quota([old, failed], now=NOW + timedelta(seconds=2), reserve=100)
    assert state["observed_at"] == NOW.isoformat()
    assert quota_block(state, now=NOW + timedelta(seconds=2), reserve=100) == "QUOTA_RESERVED_FOR_DAILY_WORK"
