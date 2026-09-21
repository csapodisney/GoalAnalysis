"""Contract tests across real collection, construction, review, storage and HTTP layers."""

import json
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer

import pytest
from test_daily_223_live import NOW, setup

from goal_analysis.config.portfolio import validate_settings
from goal_analysis.jobs.portfolio import RunLock, run_portfolio, write_portfolio_bundle
from goal_analysis.portfolio_dashboard import ArthurState, make_handler
from goal_analysis.storage.portfolio_ledger import PortfolioLedger


class RecordedReviewer:
    def __init__(self, after=None):
        self.calls = []
        self.after = after

    def review(self, candidates, target_date, feedback=None):
        self.calls.append((candidates, target_date, feedback))
        if self.after:
            self.after()
        return {
            "status": "COMPLETE",
            "model": "gpt-6-astra",
            "usage": {"requests": 1, "input_tokens": 110, "output_tokens": 45},
            "reviews": [
                {
                    "candidate_id": c["candidate_id"],
                    "assessment": "support",
                    "support_arguments": ["Tesztben rögzített, forrásolt értékelés."],
                    "risk_notes": [],
                    "evidence_ids": [e["evidence_id"] for e in c["evidence"]],
                    "source_urls": [],
                }
                for c in candidates
            ],
        }


def environment(tmp_path):
    cfg, football, odds, cache, calls, state, db = setup(tmp_path)
    for event in state["events"]:
        for market in event["bookmakers"][0]["markets"]:
            if market["key"] == "totals":
                market["outcomes"][0]["price"] = 2.4
    return cfg, football, odds, cache, calls, state, db


def test_collection_to_two_distinct_reviewed_tickets_and_persistent_dashboard(tmp_path):
    cfg, football, odds, cache, _calls, _state, _db = environment(tmp_path)
    ledger = PortfolioLedger(tmp_path / "data/arthur/ledger.sqlite3", now=lambda: NOW)
    reviewer = RecordedReviewer()
    bundle = run_portfolio(
        cfg, {}, football, odds, cache, NOW.date(), reviewer, ledger, clock=lambda: NOW
    )
    report = bundle["artifacts"]["report"]
    assert len(report["tickets"]) == 2
    assert report["tickets"][0]["profile_id"] == "daily223"
    assert all(t["status"] == "READY" for t in report["tickets"])
    assert len(reviewer.calls) == 1 and report["usage"]["llm_calls"] == 1
    assert report["funnel"]["fixtures"] == 3
    assert reviewer.calls[0][0][0]["historical_context"]["profiles"]["home"]["observed_workload"]
    write_portfolio_bundle(tmp_path / "reports/arthur" / report["run_id"], bundle)
    ledger.save_run(report)
    ledger.save_run(report)
    view = ArthurState(tmp_path).snapshot(NOW.date().isoformat())
    assert len(view["tickets"]) == 2
    assert view["latest"]["run_id"] == report["run_id"]
    assert view["tickets"][0]["played"] is False
    assert ledger.performance(mode="actual")["summary"]["tickets"] == 0
    assert (tmp_path / "reports/arthur" / report["run_id"] / "report.md").exists()


def test_history_failure_still_reviews_all_profile_recommendations(tmp_path):
    cfg, football, odds, cache, _calls, state, _db = environment(tmp_path)
    state["history_failure"] = True
    reviewer = RecordedReviewer()
    report = run_portfolio(cfg, {}, football, odds, cache, NOW.date(), reviewer, clock=lambda: NOW)[
        "artifacts"
    ]["report"]
    assert report["construction_status"] == "CONSTRUCTION_INCOMPLETE"
    assert any(item.get("status") == "HISTORY_UNAVAILABLE" for item in report["data_issues"])
    assert report["funnel"]["priced_candidates"] > 0
    assert report["diagnostics"] and not report["tickets"] and reviewer.calls
    assert report["astra"]["status"] == "COMPLETE"
    assert len(report["recommendations"]) == 7


@pytest.mark.parametrize("elapsed,expected", [(timedelta(minutes=6), 2), (timedelta(hours=7), 0)])
def test_review_cannot_publish_started_matches_or_call_expired_quotes_ready(
    tmp_path, elapsed, expected
):
    cfg, football, odds, cache, _calls, _state, _db = environment(tmp_path)
    clock = [NOW]
    reviewer = RecordedReviewer(lambda: clock.__setitem__(0, NOW + elapsed))
    report = run_portfolio(
        cfg, {}, football, odds, cache, NOW.date(), reviewer, clock=lambda: clock[0]
    )["artifacts"]["report"]
    assert len(report["tickets"]) == expected
    assert all(t["status"] == "DRAFT" and t["requires_refresh"] for t in report["tickets"])


def test_process_lock_prevents_overlapping_scheduler_and_dashboard(tmp_path):
    path = tmp_path / "run.lock"
    with RunLock(path), pytest.raises(RuntimeError, match="Már fut"), RunLock(path):
        pass
    with RunLock(path):
        pass


def test_settings_reject_silent_stake_model_or_daily_cap_changes():
    for patch in (
        {"stake_eur": 10},
        {"max_ticket_count": 6},
        {"strictness": True},
        {"openai": {"model": "other"}},
        {"enabled_profiles": ["merlin"]},
    ):
        with pytest.raises(ValueError):
            validate_settings(patch)


def test_http_rejects_cross_origin_mutations_and_exports_no_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-private-key-not-for-export")
    state = ArthurState(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, "session-token"))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/api/status?date=2026-09-20") as reply:
            body = reply.read()
        assert b"test-private-key-not-for-export" not in body
        assert json.loads(body)["credentials"]["openai"] is False
        assert isinstance(json.loads(body)["credentials"]["codex_cli"], bool)
        request = urllib.request.Request(
            base + "/api/settings",
            data=b'{"strictness":20}',
            headers={
                "Content-Type": "application/json",
                "X-Arthur-Token": "session-token",
                "Origin": "https://malicious.example",
            },
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 403
        request.remove_header("Origin")
        with urllib.request.urlopen(request) as reply:
            assert json.load(reply)["settings"]["strictness"] == 20
        request = urllib.request.Request(
            base + "/api/run",
            data=b'{"date":"2020-01-01"}',
            headers={"Content-Type": "application/json", "X-Arthur-Token": "session-token"},
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 400 and not state.runtime["running"]
        with urllib.request.urlopen(base + "/api/export?date=2026-09-20") as reply:
            assert b"test-private-key-not-for-export" not in reply.read()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
