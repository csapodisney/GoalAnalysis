"""League expansion reaches the real collector without changing legacy selection.

Every fixture, quote and provider response here is synthetic and stays in tmp_path.
"""

import json
from copy import deepcopy
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pytest
from test_daily_223_history import raw_fixture, response
from test_daily_223_live import NOW, config, daily_rows, setup
from test_portfolio_flow import RecordedReviewer

from goal_analysis.jobs.daily_223_live import run_daily223_live
from goal_analysis.jobs.portfolio import run_portfolio, write_portfolio_bundle
from goal_analysis.providers.api_football import ApiFootballClient

SERIE_C = {138: "Serie C - Girone A", 942: "Serie C - Girone B", 943: "Serie C - Girone C"}


def calendar_row(identifier, league=78):
    row = deepcopy(daily_rows()[0])
    row["fixture"]["id"] = identifier
    row["league"].update(
        id=league,
        name=SERIE_C.get(league, "Bundesliga"),
        country="Italy" if league in SERIE_C else "Germany",
    )
    return row


def scope_environment(tmp_path, *, maximum=20, busy=False):
    cfg = config()
    cfg["max_football_calls"] = maximum
    cfg["api_football_odds_max_calls"] = 4
    _, _football, odds, cache, calls, state, _database = setup(tmp_path, cfg)
    base = [calendar_row(9000 + index) for index in range(15 if busy else 1)]
    state["daily"] = base + [calendar_row(9100 + index, league) for index, league in enumerate(SERIE_C)]
    # Keep only the real matching synthetic primary event. Serie C must use
    # fixture-identity recovery, never an invented The Odds API sport key.
    state["events"] = state["events"][:1]

    def football_transport(url, headers):
        calls["football"].append(url)
        path, query = urlparse(url).path, parse_qs(urlparse(url).query)
        if path == "/odds":
            league = int(query["league"][0])
            rows = []
            for fixture in state["daily"]:
                if fixture["league"]["id"] != league:
                    continue
                rows.append(
                    {
                        "fixture": {"id": fixture["fixture"]["id"], "date": fixture["fixture"]["date"]},
                        "league": {"id": league, "season": 2026},
                        "update": NOW.isoformat(),
                        "bookmakers": [
                            {
                                "id": 42,
                                "name": "Synthetic Book",
                                "bets": [
                                    {
                                        "id": 1,
                                        "name": "Match Winner",
                                        "values": [
                                            {"value": "Home", "odd": "2.10"},
                                            {"value": "Draw", "odd": "3.20"},
                                            {"value": "Away", "odd": "3.10"},
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                )
        elif "date" in query:
            rows = state["daily"]
        else:
            league, season = int(query["league"][0]), int(query["season"][0])
            rows = [
                raw_fixture(league * 100000 + season * 10 + index, home=1, away=2, days=index + 1, league=league)
                for index in range(6)
            ]
            for row in rows:
                row["league"]["season"] = season
        return response(rows), {"x-ratelimit-requests-remaining": "99"}

    football = ApiFootballClient("synthetic-football-key", transport=football_transport)
    return cfg, football, odds, cache, calls, state


def request_queries(calls, endpoint):
    return [
        parse_qs(urlparse(url).query)
        for url in calls["football"]
        if urlparse(url).path == "/" + endpoint
    ]


@pytest.mark.parametrize("legacy,selected", [(True, None), (False, [78])])
def test_calendar_scope_cannot_change_legacy_or_manual_selection(tmp_path, legacy, selected):
    cfg, football, odds, cache, calls, state, _db = setup(tmp_path)
    for row in state["daily"]:
        row["league"].update(name="Bundesliga", country="Germany")
    visited = []

    def forbidden_scope(*args):
        visited.append(args)
        raise AssertionError("Automatic fallback must not run on this route")

    bundle = run_daily223_live(
        cfg,
        football,
        odds,
        cache,
        clock=lambda: NOW,
        construct_legacy=legacy,
        selected_competition_ids=selected,
        calendar_scope=forbidden_scope,
    )
    assert not visited
    assert bundle["artifacts"]["config"]["leagues"] == cfg["leagues"]
    assert bundle["artifacts"]["report"]["construction_status"] != "DATA_BLOCKED", bundle["artifacts"]["report"]
    assert len([q for q in request_queries(calls, "fixtures") if "date" in q]) == 1


def test_automatic_fallback_reaches_saved_config_history_and_real_odds_recovery(tmp_path):
    cfg, football, odds, cache, calls, _state = scope_environment(tmp_path)
    original = deepcopy(cfg)
    bundle = run_portfolio(
        cfg,
        {},
        football,
        odds,
        cache,
        NOW.date(),
        RecordedReviewer(),
        clock=lambda: NOW,
        model_predictor=lambda *args, **kwargs: {},
    )
    artifacts = bundle["artifacts"]
    assert artifacts["report"]["construction_status"] != "DATA_BLOCKED", artifacts["report"]
    assert cfg == original, "Day-specific fallback must not mutate configured defaults"
    scoped = {league["api_football_id"]: league for league in artifacts["config"]["leagues"]}
    assert set(scoped) == {78, *SERIE_C}
    for identifier in SERIE_C:
        assert scoped[identifier]["history_seasons"] == [2025, 2026]
        assert scoped[identifier]["odds_sport_key"] == f"soccer_arthur_{identifier}"
    history_pairs = {
        (int(query["league"][0]), int(query["season"][0]))
        for query in request_queries(calls, "fixtures")
        if "date" not in query
    }
    assert history_pairs == {(78, 2026), *((league, year) for league in SERIE_C for year in (2025, 2026))}
    recovered = [
        candidate
        for candidate in artifacts["candidate_input"]["candidates"]
        if candidate.get("quote_provider") == "api_football"
    ]
    assert {int(candidate["api_football_league_id"]) for candidate in recovered} >= set(SERIE_C)
    assert {int(query["league"][0]) for query in request_queries(calls, "odds")} >= set(SERIE_C)
    assert len([q for q in request_queries(calls, "fixtures") if "date" in q]) == 1
    assert len(calls["football"]) <= cfg["max_football_calls"]
    assert odds.reserved_credits <= cfg["max_odds_credits"]
    assert all("soccer_arthur_" not in url for url in calls["odds"])
    destination = tmp_path / "reports" / artifacts["report"]["run_id"]
    write_portfolio_bundle(destination, bundle)
    saved = json.loads((destination / "config.json").read_text("utf-8"))
    assert {league["api_football_id"] for league in saved["leagues"]} == {78, *SERIE_C}


@pytest.mark.parametrize("maximum", [3, 8])
def test_fallback_uses_existing_shared_budget_and_only_one_calendar_request(tmp_path, maximum):
    from goal_analysis.jobs.league_scope import resolve_default_leagues

    cfg, football, odds, cache, calls, _state = scope_environment(tmp_path, maximum=maximum)
    artifacts = run_daily223_live(
        cfg,
        football,
        odds,
        cache,
        clock=lambda: NOW,
        construct_legacy=False,
        calendar_scope=resolve_default_leagues,
    )["artifacts"]
    assert artifacts["report"]["construction_status"] == "COLLECTION_COMPLETE", artifacts["report"]
    assert {row["api_football_league_id"] for row in artifacts["fixtures"]["records"]} == {
        "78", *(str(identifier) for identifier in SERIE_C)
    }
    assert artifacts["report"]["usage"]["football_calls"] == len(calls["football"]) <= maximum
    assert artifacts["odds_recovery"]["api_football_calls"] > 0
    assert len([q for q in request_queries(calls, "fixtures") if "date" in q]) == 1
    assert odds.reserved_credits <= cfg["max_odds_credits"]


def test_explicit_manual_serie_c_selection_survives_a_busy_base_calendar(tmp_path):
    cfg, football, odds, cache, calls, _state = scope_environment(tmp_path, busy=True)
    bundle = run_portfolio(
        cfg,
        {},
        football,
        odds,
        cache,
        NOW.date(),
        RecordedReviewer(),
        clock=lambda: NOW,
        selected_competition_ids=[138],
        model_predictor=lambda *args, **kwargs: {},
    )
    artifacts = bundle["artifacts"]
    assert artifacts["report"]["construction_status"] != "DATA_BLOCKED", artifacts["report"]
    assert [league["api_football_id"] for league in artifacts["config"]["leagues"]] == [138]
    assert {row["api_football_league_id"] for row in artifacts["fixtures"]["records"]} == {"138"}
    assert {int(query["league"][0]) for query in request_queries(calls, "odds")} == {138}
    assert len([q for q in request_queries(calls, "fixtures") if "date" in q]) == 1


@pytest.mark.parametrize("busy", [False, True])
def test_dashboard_defaults_match_fallback_while_manual_options_remain_available(tmp_path, monkeypatch, busy):
    import goal_analysis.portfolio_dashboard as dashboard

    cfg = config()
    config_path = tmp_path / "live-config.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    rows = [calendar_row(9000 + index) for index in range(15 if busy else 1)]
    rows.extend(calendar_row(9100 + index, league) for index, league in enumerate(SERIE_C))
    national = calendar_row(9999, 10)
    national["league"].update(name="Friendlies", country="World")
    rows.append(national)
    calendar_calls = []

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.astimezone(tz)

    from test_the_stats_primary import native

    from goal_analysis.providers.the_stats_leagues import LEAGUES

    def calendar_response(resources, target):
        calendar_calls.append(target)
        converted = []
        for old in rows:
            item = native(old)
            item['competition_id'] = LEAGUES[str(old['league']['id'])][0]
            converted.append(item)
        return converted, []

    monkeypatch.setattr(dashboard, "datetime", FixedClock)
    monkeypatch.setattr("goal_analysis.jobs.the_stats_live.calendar", calendar_response)
    state = dashboard.ArthurState(tmp_path, config_path=config_path)
    view = state.competitions(NOW.date().isoformat())
    options = {item["id"]: item for item in view["competitions"]}
    assert set(options) == {78, 10, *SERIE_C}
    assert options[78]["default_selected"] and options[10]["default_selected"]
    for identifier in SERIE_C:
        assert options[identifier]["default_selected"] is (not busy)
        assert options[identifier]["fallback"] is (not busy)
    assert view["serie_c_fallback_below"] == 15
    assert state.competitions(NOW.date().isoformat()) == view
    assert calendar_calls == [NOW.date()]
