"""Regression for the user's five fixtures / zero charged odds response."""

from copy import deepcopy
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from test_portfolio_flow import NOW, RecordedReviewer, environment

from goal_analysis.agents.codex_review import CodexReviewer
from goal_analysis.jobs.portfolio import run_portfolio, write_portfolio_bundle
from goal_analysis.providers.daily_223_odds import UnavailableOddsFeed
from goal_analysis.storage.portfolio_ledger import PortfolioLedger


@pytest.mark.parametrize("missing_key", [False, True])
def test_five_fixture_preview_survives_empty_odds_or_missing_key(tmp_path, missing_key):
    cfg, football, odds, cache, calls, state, _ = environment(tmp_path)
    for i in range(2):
        row = deepcopy(state["daily"][i])
        row["fixture"]["id"] = 9010 + i
        state["daily"].append(row)
    state["events"] = []
    state["history_failure"] = True
    if missing_key:
        odds = UnavailableOddsFeed(cfg["odds_region"], cfg["max_odds_credits"])
    else:
        original = odds.transport

        def zero_cost(url, headers):
            body, _headers, raw = original(url, headers)
            return (
                body,
                {"x-requests-last": "0", "x-requests-remaining": "500", "x-requests-used": "0"},
                raw,
            )

        odds.transport = zero_cost
    reviewer = RecordedReviewer()
    bundle = run_portfolio(
        cfg,
        {"strictness": 0, "target_ticket_count": 5},
        football,
        odds,
        cache,
        NOW.date(),
        reviewer,
        clock=lambda: NOW,
    )
    report = bundle["artifacts"]["report"]
    assert len(report["fixtures"]) == 5 and len(report["preview_tickets"]) == 5
    assert report["tickets"] == [] and len(reviewer.calls) == 1
    assert len(reviewer.calls[0][0]) <= 30
    packed, _digest = CodexReviewer(command=["codex"])._prepare(
        reviewer.calls[0][0], NOW.date().isoformat(), None
    )
    assert all(c["odds_pending"] and c["decimal_price"] is None for c in packed["candidates"])
    assert all(
        t["status"] == "ODDS_PENDING" and t["stake_eur"] is None for t in report["preview_tickets"]
    )
    assert all(l["decimal_price"] is None for t in report["preview_tickets"] for l in t["legs"])
    assert all(t["quality_warnings"] for t in report["preview_tickets"])
    assert report["usage"]["odds_calls"] == (0 if missing_key else 1)
    if not missing_key:
        assert report["odds_queries"][0]["returned_events"] == 0
        assert report["usage"]["odds_provider_usage"][0]["last_cost"] == 0
    queries = [parse_qs(urlparse(url).query) for url in calls["football"] if "date=" in url]
    assert len(queries) == 1 and "league" not in queries[0] and "season" not in queries[0]
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: NOW)
    ledger.save_run(report)
    assert ledger.list_tickets() == []
    assert len(ledger.list_runs()[0]["preview_tickets"]) == 5
    write_portfolio_bundle(tmp_path / "report", bundle)
    assert "Szorzóra váró előzetes" in (tmp_path / "report/report.md").read_text()


def test_all_competitions_use_one_calendar_call_with_season_and_date_checks(tmp_path):
    cfg, football, odds, cache, calls, state, _ = environment(tmp_path)
    league = cfg["leagues"][0]
    for i in range(17):
        cfg["leagues"].append(
            {
                **league,
                "api_football_id": 1000 + i,
                "competition_id": f"OTHER_{i}",
                "odds_sport_key": f"soccer_other_{i}",
            }
        )
    cfg["max_football_calls"] = 1
    state["daily"][0]["league"]["season"] = 2027
    outside = deepcopy(state["daily"][0])
    outside["fixture"]["id"] = 9999
    outside["fixture"]["date"] = (NOW + timedelta(days=1)).isoformat()
    state["daily"].append(outside)
    report = run_portfolio(
        cfg, {"strictness": 0}, football, odds, cache, NOW.date(), clock=lambda: NOW
    )["artifacts"]["report"]
    assert len(calls["football"]) == 1
    assert len(report["fixtures"]) == 3
    assert report["fixtures"][0]["season"] == 2027
    assert any(i["status"] == "PROVIDER_SEASON_USED" for i in report["data_issues"])
    assert report["tickets"]


def test_previews_do_not_survive_kickoff_during_review(tmp_path):
    cfg, football, odds, cache, _calls, state, _ = environment(tmp_path)
    state["events"] = []
    clock = [NOW]
    reviewer = RecordedReviewer(lambda: clock.__setitem__(0, NOW + timedelta(hours=7)))
    report = run_portfolio(
        cfg, {"strictness": 0}, football, odds, cache, NOW.date(), reviewer, clock=lambda: clock[0]
    )["artifacts"]["report"]
    assert report["preview_tickets"] == [] and report["tickets"] == []
    assert report["fixtures"]  # The collected calendar remains inspectable.


def test_calendar_error_keeps_specific_cause_but_redacts_provider_key(tmp_path):
    cfg, football, odds, cache, _calls, _state, _ = environment(tmp_path)
    football._transport = lambda *args: (
        {"errors": {"requests": "Rate limit: football-secret"}, "response": []},
        {},
    )
    report = run_portfolio(
        cfg, {"strictness": 0}, football, odds, cache, NOW.date(), clock=lambda: NOW
    )["artifacts"]["report"]
    assert "Rate limit" in report["reason"]
    assert "football-secret" not in str(report)
