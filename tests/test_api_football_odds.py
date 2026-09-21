"""Recorded-shape provider tests: all fixtures/prices below are synthetic."""

import json
from copy import deepcopy
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from test_daily_223_live import NOW, config, fixtures, setup
from test_portfolio_flow import RecordedReviewer

from goal_analysis.config.daily_223_live import validate_live_config
from goal_analysis.jobs.daily_223_candidates import assemble_daily223_candidates
from goal_analysis.jobs.daily_223_live import BudgetFootballClient
from goal_analysis.jobs.portfolio import run_portfolio
from goal_analysis.providers.api_football import ApiFootballClient
from goal_analysis.providers.api_football_odds import ApiFootballOddsCollector, normalize_odds
from goal_analysis.providers.base import ProviderError
from goal_analysis.providers.daily_223_odds import Daily223OddsFeed, UnavailableOddsFeed
from goal_analysis.storage.portfolio_ledger import PortfolioLedger


def odds_rows():
    rows = []
    for index, fixture in enumerate(fixtures()):

        def bet(identifier, name, values):
            return {
                "id": identifier,
                "name": name,
                "values": [{"value": v, "odd": p} for v, p in values],
            }

        rows.append(
            {
                "league": {"id": 78, "season": 2026},
                "fixture": {"id": 9000 + index, "date": fixture["kickoff"]},
                "update": NOW.isoformat(),
                "bookmakers": [
                    {
                        "id": 42,
                        "name": "Betano",
                        "bets": [
                            bet(
                                1,
                                "Match Winner",
                                [("Home", "3.10"), ("Draw", "3.20"), ("Away", "2.10")],
                            ),
                            bet(
                                5,
                                "Goals Over/Under",
                                [
                                    ("Over 2.5", "2.4"),
                                    ("Under 2.5", "2.1"),
                                    ("Over 2.0", "1.5"),
                                    ("Under 2.0", "2.5"),
                                ],
                            ),
                            bet(8, "Both Teams Score", [("Yes", "2.3"), ("No", "1.8")]),
                            bet(
                                6,
                                "Goals Over/Under First Half",
                                [("Over 0.5", "1.4"), ("Under 0.5", "3.0")],
                            ),
                            bet(
                                13,
                                "First Half Winner",
                                [("Home", "3.0"), ("Draw", "2.2"), ("Away", "2.5")],
                            ),
                        ],
                    }
                ],
            }
        )
    return rows


def calendar():
    return [dict(f, season=2026) for f in fixtures()]


def parsed(rows):
    events, issues = normalize_odds(rows, calendar(), NOW)
    cfg = config()
    cfg["extra_markets"] = ["btts", "totals_h1", "h2h_3_way_h1"]
    result = assemble_daily223_candidates(
        calendar(), events, cfg, NOW.date(), NOW, NOW, allow_stale_quotes=True
    )
    return result, issues


def test_exact_markets_half_time_and_provenance():
    result, issues = parsed(odds_rows())
    assert not issues and len(result["candidates"]) == 36
    assert {c["quote_provider"] for c in result["candidates"]} == {"api_football"}
    assert {c["bookmaker"] for c in result["candidates"]} == {"betano_api_football_42"}
    half = next(c for c in result["candidates"] if c["market_key"] == "totals_0_5")
    assert half["period"] == "FIRST_HALF" and half["quoted_at"] == NOW.isoformat()
    assert "bet=6" in half["quote_source_id"] and "bookmaker=42" in half["quote_source_id"]
    assert "totals_2_0" not in {c["market_key"] for c in result["candidates"]}


@pytest.mark.parametrize(
    "mutation", ["id", "league", "season", "kickoff", "started", "future", "expired", "no_update"]
)
def test_wrong_identity_and_time_never_create_prices(mutation):
    rows = odds_rows()[:1]
    row = rows[0]
    if mutation == "id":
        row["fixture"]["id"] = 99999
    elif mutation == "league":
        row["league"]["id"] = 45
    elif mutation == "season":
        row["league"]["season"] = 2025
    elif mutation == "kickoff":
        row["fixture"]["date"] = (NOW + timedelta(hours=6, seconds=61)).isoformat()
    elif mutation == "started":
        row["fixture"]["date"] = NOW.isoformat()
    elif mutation == "future":
        row["update"] = (NOW + timedelta(seconds=1)).isoformat()
    elif mutation == "expired":
        row["update"] = (NOW - timedelta(days=1, seconds=1)).isoformat()
    else:
        row.pop("update")
    assert not parsed(rows)[0]["candidates"]


@pytest.mark.parametrize("price", [True, "NaN", "Infinity", "0", None])
def test_invalid_quote_drops_affected_market_only(price):
    rows = odds_rows()[:1]
    rows[0]["bookmakers"][0]["bets"][0]["values"][0]["odd"] = price
    result, issues = parsed(rows)
    assert result["candidates"] and issues
    assert not any(
        c["market_key"] == "h2h" and c["period"] == "FULL_TIME" for c in result["candidates"]
    )


def test_duplicate_snapshots_and_unknown_semantics_not_silently_selected():
    row = odds_rows()[0]
    duplicate = deepcopy(row)
    duplicate["bookmakers"][0]["bets"][0]["values"][0]["odd"] = "9.5"
    assert not parsed([row, duplicate])[0]["candidates"]
    row["bookmakers"][0]["bets"] = [dict(row["bookmakers"][0]["bets"][0], name="To Qualify")]
    assert not parsed([row])[0]["candidates"]


def run(env, **settings):
    cfg, football, odds, cache, *_ = env
    return run_portfolio(
        cfg, settings, football, odds, cache, NOW.date(), RecordedReviewer(), clock=lambda: NOW
    )


@pytest.mark.parametrize("missing_key", [True, False])
def test_empty_primary_recovered_persisted_and_reviewed_without_new_service(tmp_path, missing_key):
    env = list(setup(tmp_path))
    env[5]["events"] = []
    env[5]["football_odds"] = odds_rows()
    if missing_key:
        env[2] = UnavailableOddsFeed("eu", env[0]["max_odds_credits"])
    bundle = run(env)
    report = bundle["artifacts"]["report"]
    assert report["tickets"] and report["astra"]["status"] == "COMPLETE"
    assert report["usage"]["football_calls"] == 3 and report["usage"]["football_odds_calls"] == 1
    assert report["odds_recovered_candidates"] == 27
    assert all(x["status"] == "COMPLETE" for x in report["odds_coverage"])
    assert all(l["quote_provider"] == "api_football" for t in report["tickets"] for l in t["legs"])
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: NOW)
    ledger.save_run(report)
    saved = ledger.list_tickets()
    assert saved and "api-sports.io/odds?fixture=" in saved[0]["legs"][0]["quote_source_id"]
    assert not any(t["played"] for t in saved)
    assert "football-secret" not in json.dumps(report)


def test_three_hour_prices_are_indicative_not_refreshed_to_now(tmp_path):
    env = setup(tmp_path)
    env[5]["events"] = []
    rows = odds_rows()
    stamp = (NOW - timedelta(hours=3)).isoformat()
    for row in rows:
        row["update"] = stamp
    env[5]["football_odds"] = rows
    report = run(env)["artifacts"]["report"]
    assert not report["tickets"]  # strict portfolio gates remain intact
    assert report["recommendations"]
    legs = [l for t in report["recommendations"] for l in t["legs"]]
    assert all(l["quoted_at"] == stamp for l in legs)
    assert all(
        any("régebbi" in w for w in t["quality_warnings"]) for t in report["recommendations"]
    )
    loose = run(env, strictness=0)["artifacts"]["report"]
    assert loose["tickets"] and all(t["status"] == "DRAFT" for t in loose["tickets"])


def test_missing_half_market_recovers_without_merging_same_named_bookmakers(tmp_path):
    env = setup(tmp_path)
    env[5]["football_odds"] = odds_rows()
    for event in env[5]["events"]:
        event["bookmakers"][0]["key"] = "betano"
    bundle = run(env)
    candidates = bundle["artifacts"]["candidate_input"]["candidates"]
    assert {c["bookmaker"] for c in candidates} == {"betano", "betano_api_football_42"}
    assert any(c["market_key"] == "totals_0_5" for c in candidates)
    assert all(
        len({l["bookmaker"] for l in t["legs"]}) == 1
        for t in bundle["artifacts"]["report"]["tickets"]
    )


def test_positive_and_empty_cache_avoid_repeat_calls_without_changing_timestamps(tmp_path):
    env = setup(tmp_path)
    env[5]["events"] = []
    env[5]["football_odds"] = odds_rows()
    first = run(env)["artifacts"]["report"]
    second = run(env)["artifacts"]["report"]
    assert first["usage"]["football_odds_calls"] == 1
    assert second["usage"]["football_odds_calls"] == 0
    assert any(q.get("from_cache") for q in second["odds_queries"])
    other = setup(tmp_path / "empty")
    other[5]["events"] = []
    run(other)
    assert run(other)["artifacts"]["report"]["usage"]["football_odds_calls"] == 0


def test_shared_budget_reserves_odds_before_history_and_never_exceeds_limit(tmp_path):
    env = setup(tmp_path)
    env[0]["max_football_calls"] = 2
    env[5]["events"] = []
    env[5]["football_odds"] = odds_rows()
    report = run(env, strictness=0)["artifacts"]["report"]
    assert report["tickets"] and report["usage"]["football_calls"] == 2
    assert report["usage"]["football_odds_calls"] == 1
    assert any(i["status"] == "HISTORY_UNAVAILABLE" for i in report["data_issues"])


def test_opt_out_and_full_coverage_do_not_call_odds(tmp_path):
    env = setup(tmp_path)
    env[0]["api_football_odds_max_calls"] = 0
    env[5]["events"] = []
    assert run(env)["artifacts"]["report"]["usage"]["football_odds_calls"] == 0
    env = setup(tmp_path / "covered")
    report = run(env, enabled_profiles=["daily223", "kronikas"])["artifacts"]["report"]
    assert report["usage"]["football_odds_calls"] == 0


def test_round_robin_pagination_cache_and_partial_quota(tmp_path):
    env = setup(tmp_path)
    requests = []

    def transport(url, headers):
        query = parse_qs(urlparse(url).query)
        requests.append(query)
        page = int(query["page"][0])
        return {
            "response": odds_rows()[page - 1 : page],
            "paging": {"current": page, "total": 3},
        }, {}

    budget = BudgetFootballClient(ApiFootballClient("fake", transport=transport), 2)
    collector = ApiFootballOddsCollector(budget, env[3], 2, lambda: NOW)
    rows, queries, issues = collector.collect(calendar(), NOW.date())
    assert len(rows) == 2 and [q["page"] for q in queries] == [1, 2, 3]
    assert collector.calls == 2 and issues[-1]["status"] == "API_FOOTBALL_ODDS_BUDGET_EXHAUSTED"
    budget = BudgetFootballClient(ApiFootballClient("fake", transport=transport), 2)
    collector = ApiFootballOddsCollector(budget, env[3], 2, lambda: NOW)
    assert len(collector.collect(calendar(), NOW.date())[0]) == 3
    assert collector.calls == 1


def test_failure_is_redacted_and_daily_quota_header_is_case_insensitive(tmp_path):
    env = setup(tmp_path)

    def fail(url, headers):
        raise ProviderError("account error football-secret")

    budget = BudgetFootballClient(ApiFootballClient("football-secret", transport=fail), 10)
    result = ApiFootballOddsCollector(budget, env[3], 1, lambda: NOW).collect(
        calendar(), NOW.date()
    )
    assert result[2] and "football-secret" not in json.dumps(result)

    def quota(url, headers):
        return {"response": [], "paging": {"current": 1, "total": 1}}, {
            "X-RateLimit-Requests-Remaining": "0"
        }

    client = ApiFootballClient("fake", transport=quota)
    client.request("fixtures", {})
    budget = BudgetFootballClient(client, 5)
    result = ApiFootballOddsCollector(budget, env[3], 5, lambda: NOW).collect(
        calendar(), NOW.date()
    )
    assert budget.calls == 0 and result[1][0]["status"] == "BUDGET_EXHAUSTED"


def test_zero_cost_primary_does_not_consume_reserved_budget():
    feed = Daily223OddsFeed(
        "fake",
        "eu",
        2,
        transport=lambda *a: ([], {"x-requests-last": "0", "x-requests-remaining": "500"}, b"[]"),
    )
    feed.bulk("soccer_fa_cup", NOW.date())
    feed.bulk("soccer_epl", NOW.date())
    assert feed.reserved_credits == 0 and feed.calls == 2


def test_malformed_primary_cannot_disable_good_secondary(tmp_path):
    env = setup(tmp_path)
    env[5]["events"][0]["bookmakers"] = ["bad shape"]
    env[5]["football_odds"] = odds_rows()
    report = run(env)["artifacts"]["report"]
    assert report["tickets"] and report["odds_recovered_candidates"] == 27
    assert any(x["status"] == "INVALID_PRIMARY_ODDS_RESPONSE" for x in report["data_issues"])


def test_malformed_secondary_preserves_good_primary(tmp_path):
    env = setup(tmp_path)
    original = env[1]._transport

    def broken(url, headers):
        if urlparse(url).path == "/odds":
            return {"response": "malformed", "paging": {"current": 1, "total": 1}}, {}
        return original(url, headers)

    env[1]._transport = broken
    report = run(env)["artifacts"]["report"]
    assert report["tickets"] and report["odds_recovered_candidates"] == 0
    assert any(x["status"] == "API_FOOTBALL_ODDS_INVALID_RESPONSE" for x in report["data_issues"])


def test_pagination_visits_other_leagues_before_second_page(tmp_path):
    env = setup(tmp_path)
    requested = []

    def transport(url, headers):
        query = parse_qs(urlparse(url).query)
        league, page = int(query["league"][0]), int(query["page"][0])
        requested.append((league, page))
        return {"response": odds_rows()[:1], "paging": {"current": page, "total": 2}}, {}

    budget = BudgetFootballClient(ApiFootballClient("fake", transport=transport), 2)
    collector = ApiFootballOddsCollector(budget, env[3], 2, lambda: NOW)
    collector.collect([*calendar(), dict(calendar()[0], api_football_league_id="79")], NOW.date())
    assert requested == [(78, 1), (79, 1)]


@pytest.mark.parametrize("value", [-1, True, 31, "12"])
def test_new_limit_validated_without_changing_existing_total(value):
    cfg = config()
    cfg["api_football_odds_max_calls"] = value
    with pytest.raises(ValueError):
        validate_live_config(cfg)
