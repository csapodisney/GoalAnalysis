"""Selected match universe and prospective package snapshots."""

from datetime import date, timedelta

import pytest
from test_portfolio_ledger import NOW, Football, fixture, report, ticket

from goal_analysis.jobs.portfolio_calendar import (
    available_competitions,
    collect_calendar,
    selected_leagues,
)
from goal_analysis.storage.portfolio_ledger import PortfolioLedger


def calendar_row(fixture_id, league_id, name, country):
    return {
        "fixture": {
            "id": fixture_id,
            "date": (NOW + timedelta(hours=3)).isoformat(),
            "status": {"short": "NS"},
        },
        "league": {"id": league_id, "name": name, "country": country, "season": 2026},
        "teams": {
            "home": {"id": fixture_id * 10, "name": f"Home {fixture_id}"},
            "away": {"id": fixture_id * 10 + 1, "name": f"Away {fixture_id}"},
        },
    }


def test_date_options_include_national_and_selected_scope_is_enforced():
    payload = {"response": [
        calendar_row(101, 39, "Premier League", "England"),
        calendar_row(102, 10, "Friendlies", "World"),
    ]}
    configured = [{
        "competition_id": "EN1", "api_football_id": 39, "season": 2026,
        "history_seasons": [2025, 2026], "odds_sport_key": "soccer_epl",
        "competition_type": "LEAGUE",
    }]
    day = date(2026, 9, 20)
    options = available_competitions(payload, day, configured)
    assert {item["id"] for item in options} == {10, 39}
    assert next(item for item in options if item["id"] == 10)["type"] == "NATIONAL"
    leagues = selected_leagues(payload, day, configured, [10])
    assert leagues[0]["competition_type"] == "NATIONAL"
    fixtures, _, calendar = collect_calendar(None, leagues, day, payload)
    assert [item["api_football_fixture_id"] for item in fixtures] == ["102"]
    assert calendar["configured_leagues"] == 1
    with pytest.raises(ValueError, match="nem szerepel"):
        selected_leagues(payload, day, configured, [999])


def test_frozen_package_survives_rerun_and_gains_results(tmp_path):
    clock = [NOW]
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: clock[0])
    first = report(ticket())
    ledger.save_run(first)
    frozen = ledger.freeze_run(first["run_id"])
    assert frozen["recommendation_count"] == 1
    second = report(ticket("next-ticket"), identifier="run-2")
    ledger.save_run(second)
    with pytest.raises(ValueError, match="már van rögzített"):
        ledger.freeze_run(second["run_id"])
    archive = ledger.list_freezes(date="2026-09-20")
    assert archive[0]["run_id"] == first["run_id"]
    assert archive[0]["recommendations"][0]["portfolio_ticket_id"] == "ticket-1"
    clock[0] = NOW + timedelta(hours=6)
    ledger.refresh_results(Football({i: fixture(i) for i in (1, 2, 3)}))
    archive = ledger.list_freezes(date="2026-09-20")
    assert archive[0]["outcome"] == "WON"
    assert ledger.performance(month="2026-09", mode="freezes")["summary"]["won"] == 1
