"""Regression scenarios for real prices with missing background information."""

from copy import deepcopy
from datetime import timedelta

import pytest
from test_daily_223 import candidate
from test_daily_223_history import raw_fixture, response
from test_portfolio import source
from test_portfolio_flow import NOW, RecordedReviewer, environment

from goal_analysis.engine.portfolio import build_portfolio
from goal_analysis.features.daily_223_history import validate_history
from goal_analysis.jobs.portfolio import run_portfolio, write_portfolio_bundle
from goal_analysis.providers.api_football_history import ApiFootballHistoryCollector
from goal_analysis.providers.base import ProviderError
from goal_analysis.storage.portfolio_ledger import PortfolioLedger


@pytest.mark.parametrize("strictness", [0, 30])
def test_missing_history_does_not_veto_an_available_single(strictness):
    row = candidate("only", 1.8, evidence=[])
    original = deepcopy(row)
    report = build_portfolio(source([row]), {"strictness": strictness})
    assert len(report["tickets"]) == 1
    ticket = report["tickets"][0]
    assert ticket["fallback"] and ticket["status"] == "DRAFT"
    assert ticket["combined_price"] == 1.8 and len(ticket["legs"]) == 1
    assert ticket["support_score"] == 0 and ticket["data_quality"] == "LIMITED"
    assert ticket["quality_warnings"] and not report["policy"]["data_veto_enabled"]
    assert row == original
    assert not build_portfolio(source([row]), {"strictness": 100})["tickets"]


def test_low_odds_combination_is_shown_without_claiming_daily223_or_target():
    rows = [candidate(str(i), 1.4, evidence=[]) for i in range(3)]
    report = build_portfolio(source(rows), {"strictness": 0})
    ticket = report["tickets"][0]
    assert ticket["profile_id"] == "kronikas" and ticket["fallback"]
    assert len(ticket["legs"]) == 3 and ticket["combined_price"] == pytest.approx(1.4**3)
    assert report["profiles"][0]["construction_status"] == "CONSTRUCTION_INCOMPLETE"


def test_available_market_outside_specialist_profiles_gets_explicit_fallback():
    row = candidate("other-line", 1.8, market_key="totals_3_5", evidence=[])
    ticket = build_portfolio(source([row]), {"strictness": 0})["tickets"][0]
    assert ticket["profile_id"] == "available" and ticket["fallback"]
    assert ticket["legs"][0]["market_key"] == "totals_3_5"


@pytest.mark.parametrize(
    "changes",
    [
        {"decimal_price": 1},
        {"decimal_price": float("nan")},
        {"quote_available": False},
        {"kickoff": (NOW - timedelta(minutes=1)).isoformat()},
        {"event_status": "CANCELLED"},
    ],
)
def test_veto_off_still_rejects_invalid_prices_or_unavailable_events(changes):
    row = candidate("bad", 2, evidence=[], **changes)
    assert not build_portfolio(source([row]), {"strictness": 0})["tickets"]


def test_half_time_without_samples_stays_an_explicit_experimental_draft():
    row = candidate("h1", 1.8, period="FIRST_HALF", market_key="totals_0_5", evidence=[])
    ticket = build_portfolio(source([row]), {"strictness": 0, "enabled_profiles": ["h1_over05"]})[
        "tickets"
    ][0]
    assert ticket["experimental"] and ticket["status"] == "DRAFT"
    assert any("félidős történeti" in message for message in ticket["quality_warnings"])


@pytest.mark.parametrize("offset", [0, 6])
def test_full_flow_keeps_quotes_and_tickets_when_history_fails_today_or_saturday(tmp_path, offset):
    cfg, football, odds, cache, _calls, state, _db = environment(tmp_path)
    state["history_failure"] = True
    target = NOW.date() + timedelta(days=offset)
    kickoff = NOW + timedelta(days=offset, hours=6)
    for fixture in state["daily"]:
        fixture["fixture"]["date"] = kickoff.isoformat()
    for event in state["events"]:
        event["commence_time"] = kickoff.isoformat()
        event["bookmakers"][0]["last_update"] = (NOW - timedelta(hours=6)).isoformat()
    reviewer = RecordedReviewer()
    bundle = run_portfolio(
        cfg, {"strictness": 0}, football, odds, cache, target, reviewer, clock=lambda: NOW
    )
    report = bundle["artifacts"]["report"]
    assert len(report["tickets"]) == 2 and report["funnel"]["fixtures"] == 3
    assert len(reviewer.calls) == 1 and reviewer.calls[0][0]
    assert all(
        ticket["status"] == "DRAFT" and not ticket["requires_refresh"] and ticket["quality_warnings"]
        for ticket in report["tickets"]
    )
    assert all(ticket["date"] == target.isoformat() for ticket in report["tickets"])
    assert any(issue.get("status") == "HISTORY_UNAVAILABLE" for issue in report["data_issues"])
    # Store and retrieve the actual generated warning fields, not just engine output.
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: NOW)
    ledger.save_run(report)
    assert all(t["quality_warnings"] for t in ledger.list_tickets(target.isoformat()))
    write_portfolio_bundle(tmp_path / "report", bundle)
    assert "Figyelmeztetés: Hiányos" in (tmp_path / "report/report.md").read_text("utf-8")


def test_history_budget_exhaustion_preserves_the_fixture_query(tmp_path):
    cfg, football, odds, cache, calls, _state, _db = environment(tmp_path)
    cfg["max_football_calls"] = 1
    report = run_portfolio(
        cfg, {"strictness": 0}, football, odds, cache, NOW.date(), clock=lambda: NOW
    )["artifacts"]["report"]
    assert report["tickets"] and report["usage"]["football_calls"] == 1
    assert len(calls["football"]) == 1 and "date=" in calls["football"][0]


def test_partial_history_keeps_successful_league_records(tmp_path):
    _cfg, _football, _odds, cache, _calls, _state, _db = environment(tmp_path)

    class Client:
        class last_usage:
            remaining_day = 50

        def request(self, endpoint, params):
            if params["league"] == 39:
                raise ProviderError("test provider unavailable")
            return response([raw_fixture(1, league=78)])

    snapshot = ApiFootballHistoryCollector(Client(), cache, clock=lambda: NOW).collect(
        [(39, 2026), (78, 2026)], max_calls=2, allow_partial=True
    )
    assert len(validate_history(snapshot)) == 1
    assert snapshot["api_calls"] == 2 and snapshot["cache_hits"] == 0
    assert snapshot["data_issues"][0]["league_id"] == "39"


def test_no_quote_feed_produces_previews_without_fabricating_prices(tmp_path):
    cfg, football, odds, cache, _calls, state, _db = environment(tmp_path)
    state["events"] = []
    reviewer = RecordedReviewer()
    report = run_portfolio(
        cfg, {"strictness": 0}, football, odds, cache, NOW.date(), reviewer, clock=lambda: NOW
    )["artifacts"]["report"]
    assert not report["tickets"] and len(reviewer.calls) == 1
    assert len(report["preview_tickets"]) == 2
    assert report["construction_status"] == "ODDS_PENDING"
    assert all(t["combined_price"] is None and not t["playable"] for t in report["preview_tickets"])
    assert all(leg["decimal_price"] is None for t in report["preview_tickets"] for leg in t["legs"])
    assert report["funnel"]["fixtures"] == 3 and report["funnel"]["priced_candidates"] == 0
