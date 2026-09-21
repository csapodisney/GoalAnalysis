"""Real coordinator + SQLite regression for all-profile, no-odds daily analysis."""

from copy import deepcopy
from datetime import timedelta

import pytest
from test_portfolio_flow import NOW, RecordedReviewer, environment
from test_portfolio_ledger import Football, fixture

from goal_analysis.config.portfolio import PROFILE_IDS
from goal_analysis.jobs.portfolio import run_portfolio
from goal_analysis.storage.portfolio_ledger import PortfolioLedger


def unpriced_run(tmp_path, strictness=100, reviewer=None):
    cfg, football, odds, cache, _, state, _ = environment(tmp_path)
    state["events"] = []
    state["history_failure"] = True
    reviewer = reviewer or RecordedReviewer()
    report = run_portfolio(
        cfg,
        {"strictness": strictness, "target_ticket_count": 2},
        football,
        odds,
        cache,
        NOW.date(),
        reviewer,
        clock=lambda: NOW,
    )["artifacts"]["report"]
    return report, reviewer


@pytest.mark.parametrize("strictness", [0, 35, 100])
def test_all_profiles_are_reviewed_with_no_odds_and_no_history(tmp_path, strictness):
    report, reviewer = unpriced_run(tmp_path, strictness)
    assert {r["profile_id"] for r in report["recommendations"]} == set(PROFILE_IDS)
    assert all(r["legs"] and r["confidence"] == "LOW" for r in report["recommendations"])
    assert len(reviewer.calls) == 1
    assert len(reviewer.calls[0][0]) <= 30
    reviewed = {r["candidate_id"] for r in report["astra"]["reviews"]}
    assert all(l["candidate_id"] in reviewed for r in report["recommendations"] for l in r["legs"])
    assert all(
        r["combined_price"] is None and r["probability"] is None for r in report["recommendations"]
    )
    assert len(report["tickets"]) <= 5


def test_unpriced_recommendations_survive_restart_and_settle_without_fake_profit(tmp_path):
    report, _ = unpriced_run(tmp_path)
    db = tmp_path / "local.sqlite3"
    ledger = PortfolioLedger(db, now=lambda: NOW)
    ledger.save_run(report)
    ledger.save_run(report)
    ledger = PortfolioLedger(db, now=lambda: NOW + timedelta(hours=8))
    records = ledger.list_recommendations()
    assert len(records) == 7
    ids = {int(l["fixture_id"].split(":")[-1]) for r in records for l in r["legs"]}
    football = Football({i: fixture(i, fulltime=(2, 1), halftime=(0, 0)) for i in ids})
    ledger.refresh_results(football)
    records = {r["profile_id"]: r for r in ledger.list_recommendations()}
    assert records["kronikas"]["outcome"] == "WON"
    assert records["ritmusor"]["outcome"] == "WON"
    assert records["orszem"]["outcome"] == "LOST"
    assert records["merlin"]["outcome"] == "LOST"
    assert records["h1_over05"]["outcome"] == "LOST"
    assert records["h1_over05"]["settlement"]["legs"][0]["home_goals"] == 0
    summary = ledger.performance(mode="recommendations")["summary"]
    assert summary["won"] + summary["lost"] == 7
    assert summary["hit_rate_pct"] is not None and "net_eur" not in summary
    assert all(r["financial"] is None for r in records.values())
    assert ledger.performance(mode="actual")["summary"]["stake_eur"] == 0
    assert len(football.calls) == len(ids)
    assert ledger.refresh_results(football)["football_calls"] == 0


def test_revisions_preserved_without_inflating_daily_hit_rate(tmp_path):
    report, _ = unpriced_run(tmp_path)
    ledger = PortfolioLedger(tmp_path / "local.sqlite3", now=lambda: NOW)
    ledger.save_run(report)
    revised = deepcopy(report)
    revised["run_id"] += "-second"
    revised["observed_at"] = revised["finished_at"] = (NOW + timedelta(minutes=1)).isoformat()
    ledger.save_run(revised)
    assert len(ledger.list_recommendations()) == 7
    assert len(ledger.list_recommendations(archive=True)) == 14
    assert {r["run_id"] for r in ledger.list_recommendations()} == {revised["run_id"]}
    assert ledger.performance(mode="recommendations")["summary"]["tickets"] == 7
    # A changed later fixture cannot hide an earlier selection once its match started.
    newer = deepcopy(revised)
    newer["run_id"] += "-third"
    newer["finished_at"] = (NOW + timedelta(hours=7)).isoformat()
    for r in newer["recommendations"]:
        for l in r["legs"]:
            l["kickoff"] = (NOW + timedelta(hours=10)).isoformat()
    ledger.save_run(newer)
    assert {r["run_id"] for r in ledger.list_recommendations()} == {revised["run_id"]}


def test_v34_preview_migration_uses_only_original_stored_selections(tmp_path):
    report, _ = unpriced_run(tmp_path, 0)
    report.pop("recommendations")
    ledger = PortfolioLedger(tmp_path / "local.sqlite3", now=lambda: NOW)
    ledger.save_run(report)
    # Recreate an authentic pre-v3.5 database by removing only the new indexes.
    with ledger._connection() as db:
        db.execute("DELETE FROM portfolio_recommendations")
        db.execute("DELETE FROM recommendation_imports")
    reopened = PortfolioLedger(ledger.path)
    assert len(reopened.list_recommendations()) == len(report["preview_tickets"])
    assert reopened.list_runs()[0] == report


def test_missing_one_market_does_not_hide_that_profile_for_priced_fixture(tmp_path):
    cfg, football, odds, cache, _, _, _ = environment(tmp_path)
    report = run_portfolio(
        cfg, {}, football, odds, cache, NOW.date(), RecordedReviewer(), clock=lambda: NOW
    )["artifacts"]["report"]
    profiles = {r["profile_id"]: r for r in report["recommendations"]}
    assert report["tickets"]
    assert profiles["h1_over05"]["legs"]
    assert all(l["period"] == "FIRST_HALF" for l in profiles["h1_over05"]["legs"])
