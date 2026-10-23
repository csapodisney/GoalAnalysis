"""One explicit extra review is auditable; schedules and dollar caps stay bounded."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from test_grok_advisory import NOW, Advisor, configure, leg, recommendation, report

from goal_analysis.jobs import grok_advisory as job
from goal_analysis.jobs import grok_gate as gate


def packet(index):
    return report(recommendation(legs=[leg(1, decimal_price=2.3 + index / 10), leg(2)]),
                  run_id=f"manual-test-{index}")


def exhaust_normal(root, advisor=None):
    advisor = advisor or Advisor()
    for index in (0, 1):
        result = job.analyze(root, packet(index), advisor=advisor, now=NOW + timedelta(seconds=index))
        assert result["status"] == "COMPLETE"
    return advisor


def test_one_manual_extra_is_durable_automatic_and_fourth_request_remain_blocked(tmp_path):
    configure(tmp_path)
    advisor = exhaust_normal(tmp_path)
    now = NOW + timedelta(minutes=1)
    assert job.analyze(tmp_path, packet(2), advisor=advisor, now=now)["status"] == "BUDGET"
    third = job.analyze(tmp_path, packet(2), advisor=advisor, now=now, manual_review=True)
    assert third["status"] == "COMPLETE" and third["manual_extra_review"] is True
    assert third["coverage"]["manual_extra_review"] is True
    for manual in (False, True):
        blocked = job.analyze(tmp_path, packet(3), advisor=advisor, now=now, manual_review=manual)
        assert blocked["status"] == "BUDGET"
    cached = job.analyze(tmp_path, packet(2), advisor=advisor, now=now, manual_review=True)
    assert cached["status"] == "CACHED" and cached["request_id"] == third["request_id"]
    usage = job.snapshot(tmp_path, "2026-10-18")["usage"]
    assert usage["max_daily_requests"] == 2 and usage["day_requests"] == 3
    assert usage["manual_extra_review_used"] is True
    assert len(advisor.packets) == 3
    with sqlite3.connect(job._path(tmp_path)) as db:
        assert db.execute("SELECT count(*) FROM manual_review_requests").fetchone()[0] == 1
        for statement in ("DELETE FROM manual_review_requests", "UPDATE manual_review_requests SET budget_day='tomorrow'"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute(statement)


@pytest.mark.parametrize(("cost", "config"), [(.5, {}), (.15, {"monthly_budget_usd": .3}), (None, {})])
def test_manual_permission_cannot_bypass_daily_monthly_or_unknown_cost_reservation(tmp_path, cost, config):
    configure(tmp_path, **config)
    advisor = Advisor(cost)
    job.analyze(tmp_path, packet(0), advisor=advisor, now=NOW)
    job.analyze(tmp_path, packet(1), advisor=advisor, now=NOW + timedelta(seconds=1))
    before = len(advisor.packets)
    result = job.analyze(tmp_path, packet(2), advisor=advisor, now=NOW + timedelta(minutes=1), manual_review=True)
    assert result["status"] == "BUDGET" and len(advisor.packets) == before


def test_concurrent_manual_requests_share_one_atomic_daily_allowance(tmp_path):
    configure(tmp_path)
    exhaust_normal(tmp_path)
    entered, release = Event(), Event()

    class Blocking(Advisor):
        def review(self, data):
            entered.set()
            assert release.wait(5)
            return super().review(data)

    advisor = Blocking()
    now = NOW + timedelta(minutes=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(job.analyze, tmp_path, packet(2), advisor=advisor, now=now, manual_review=True)
        assert entered.wait(5)
        try:
            second = pool.submit(job.analyze, tmp_path, packet(3), advisor=advisor, now=now, manual_review=True)
            assert second.result(timeout=3)["status"] == "BUSY"
        finally:
            release.set()
        assert first.result(timeout=3)["status"] == "COMPLETE"
    assert len(advisor.packets) == 1
    assert job.analyze(tmp_path, packet(3), advisor=advisor, now=now, manual_review=True)["status"] == "BUDGET"


def test_manual_flag_before_normal_limit_does_not_spend_extra_allowance(tmp_path):
    configure(tmp_path)
    advisor = Advisor()
    first = job.analyze(tmp_path, packet(0), advisor=advisor, now=NOW, manual_review=True)
    assert first["manual_extra_review"] is False
    job.analyze(tmp_path, packet(1), advisor=advisor, now=NOW)
    third = job.analyze(tmp_path, packet(2), advisor=advisor, now=NOW, manual_review=True)
    assert third["manual_extra_review"] is True


def test_non_boolean_cannot_accidentally_authorize_manual_review(tmp_path):
    configure(tmp_path)
    advisor = Advisor()
    result = job.analyze(tmp_path, packet(0), advisor=advisor, now=NOW, manual_review="true")
    assert result["error_code"] == "INVALID_MANUAL_REVIEW" and not advisor.packets


def test_gate_passes_manual_flag_only_when_explicitly_requested(tmp_path, monkeypatch):
    flags = []

    def analyze(*args, **kwargs):
        flags.append(kwargs["manual_review"])
        return {"status": "BUDGET"}

    monkeypatch.setattr(gate, "analyze", analyze)
    gate.review_and_repair(tmp_path, packet(0), {}, clock=lambda: NOW)
    gate.review_and_repair(tmp_path, packet(0), {}, clock=lambda: NOW, manual_review=True)
    assert flags == [False, True]
