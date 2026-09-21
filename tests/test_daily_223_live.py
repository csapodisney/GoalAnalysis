import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from test_daily_223_history import NOW, raw_fixture, response

from goal_analysis.config.daily_223_live import validate_live_config
from goal_analysis.jobs.daily_223_candidates import (
    assemble_daily223_candidates,
    match_daily_events,
    merge_extra_event,
    normalize_daily_fixtures,
)
from goal_analysis.jobs.daily_223_live import run_daily223_live, write_daily223_bundle
from goal_analysis.providers.api_football import ApiFootballClient
from goal_analysis.providers.base import ProviderError
from goal_analysis.providers.daily_223_odds import Daily223OddsFeed
from goal_analysis.storage import CacheStore, Database, SnapshotStore


def config():
    return validate_live_config(
        {
            "schema_version": 1,
            "odds_region": "eu",
            "preferred_bookmakers": ["synthetic_book"],
            "account_region": "DE",
            "currency": "EUR",
            "max_football_calls": 8,
            "max_odds_credits": 20,
            "extra_markets": [],
            "team_aliases": {},
            "leagues": [
                {
                    "competition_id": "DE1",
                    "api_football_id": 78,
                    "season": 2026,
                    "history_seasons": [2026],
                    "odds_sport_key": "soccer_germany_bundesliga",
                    "competition_type": "LEAGUE",
                }
            ],
        }
    )


def daily_rows():
    rows = []
    for index in range(3):
        item = raw_fixture(9000 + index, home=2 * index + 1, away=2 * index + 2, status="NS")
        item["fixture"]["date"] = (NOW + timedelta(hours=6)).isoformat()
        item["teams"]["home"]["name"] = f"Home {index}"
        item["teams"]["away"]["name"] = f"Away {index}"
        item["score"] = {
            "fulltime": {"home": None, "away": None},
            "halftime": {"home": None, "away": None},
        }
        rows.append(item)
    return rows


def history_rows():
    rows = []
    for team in range(1, 7):
        for index in range(6):
            home, away = (team, 100 + team) if team % 2 else (100 + team, team)
            rows.append(raw_fixture(team * 100 + index, home=home, away=away, days=index * 3 + 1))
    return rows


def events():
    result = []
    for index, price in enumerate((2, 2, 3)):
        result.append(
            {
                "id": f"event-{index}",
                "sport_key": "soccer_germany_bundesliga",
                "commence_time": (NOW + timedelta(hours=6)).isoformat(),
                "home_team": f"Home {index}",
                "away_team": f"Away {index}",
                "bookmakers": [
                    {
                        "key": "synthetic_book",
                        "last_update": NOW.isoformat(),
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": f"Home {index}", "price": price},
                                    {"name": f"Away {index}", "price": 1.5},
                                    {"name": "Draw", "price": 3.2},
                                ],
                            },
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": 2.0, "point": 2.5},
                                    {"name": "Under", "price": 1.5, "point": 2.5},
                                ],
                            },
                        ],
                    }
                ],
            }
        )
    return result


def fixtures():
    return normalize_daily_fixtures(response(daily_rows()), config()["leagues"][0], NOW.date())


def setup(tmp_path, cfg=None):
    cfg = cfg or config()
    database = Database(tmp_path / "db.sqlite3")
    database.initialize()
    snapshots = SnapshotStore(database, tmp_path / "raw")
    calls = {"football": [], "odds": []}
    state = {
        "daily": daily_rows(),
        "events": events(),
        "extra_failure": False,
        "extra_conflict": False,
        "history_failure": False,
        "football_odds": [],
    }

    def football_transport(url, headers):
        calls["football"].append(url)
        query = parse_qs(urlparse(url).query)
        if urlparse(url).path == "/odds":
            payload = response(state["football_odds"])
        elif "date" in query:
            payload = response(state["daily"])
        else:
            payload = response(history_rows())
            if state["history_failure"]:
                payload["errors"] = {"plan": "season unavailable"}
        return payload, {"x-ratelimit-requests-remaining": "99"}

    def odds_transport(url, headers):
        calls["odds"].append(url)
        path, query = urlparse(url).path, parse_qs(urlparse(url).query)
        if "/events/" in path:
            if state["extra_failure"]:
                raise ProviderError("synthetic optional endpoint failure")
            identifier = path.split("/events/")[1].split("/")[0]
            event = deepcopy(next(item for item in state["events"] if item["id"] == identifier))
            event["bookmakers"] = [
                {
                    "key": "synthetic_book",
                    "markets": [
                        {
                            "key": "btts_h1",
                            "last_update": NOW.isoformat(),
                            "outcomes": [
                                {"name": "Yes", "price": 1.5},
                                {"name": "No", "price": 3.1},
                            ],
                        }
                    ],
                }
            ]
            if state["extra_conflict"]:
                event["away_team"] = "Different opponent"
            payload = event
        else:
            payload = state["events"]
        cost = len(query["markets"][0].split(","))
        return (
            payload,
            {"x-requests-last": str(cost), "x-requests-remaining": "99"},
            json.dumps(payload).encode(),
        )

    football = ApiFootballClient(
        "football-secret", transport=football_transport, snapshot_store=snapshots
    )
    odds = Daily223OddsFeed(
        "odds-secret", cfg["odds_region"], cfg["max_odds_credits"], snapshots, odds_transport
    )
    return cfg, football, odds, CacheStore(database), calls, state, database


def execute(env):
    cfg, football, odds, cache, *_ = env
    return run_daily223_live(cfg, football, odds, cache, clock=lambda: NOW)


def test_pipeline_constructs_realistic_independent_bundle_without_llm(tmp_path):
    env = setup(tmp_path)
    bundle = execute(env)
    report = bundle["artifacts"]["report"]
    assert report["construction_status"] == "COMPLETE", report
    assert len(report["legs"]) == 3
    assert report["combined_price"] >= 12
    assert len(report["matching"]) == 3
    assert report["usage"]["football_calls"] == 2
    assert report["usage"]["odds_calls"] == 1
    assert report["usage"]["llm_calls"] == 0
    assert report["betting_approved"] is report["real_wager_placed"] is False
    assert report["context_review_pending"]
    assert report["bookmaker_account_and_combination_verified"] is False
    assert report["bookmaker_selection"]["selected"] == "synthetic_book"
    assert report["bookmaker_selection"]["method"] == "PREFERRED_COMPLETE"
    odds_query = parse_qs(urlparse(env[4]["odds"][0]).query)
    assert odds_query["regions"] == ["eu"]
    assert "bookmakers" not in odds_query
    serialized = json.dumps(bundle)
    assert "football-secret" not in serialized and "odds-secret" not in serialized
    with env[-1].connect() as connection:
        urls = [row[0] for row in connection.execute("SELECT source_url FROM raw_snapshots")]
    assert all("apiKey" not in url and "odds-secret" not in url for url in urls)


def test_target_date_accepts_next_week_but_refuses_past_and_distant_requests(tmp_path):
    env = setup(tmp_path)
    cfg, football, odds, cache, _, state, _ = env
    tomorrow = NOW.date() + timedelta(days=1)
    for row in state["daily"]:
        row["fixture"]["date"] = (NOW + timedelta(days=1, hours=6)).isoformat()
    for event in state["events"]:
        event["commence_time"] = (NOW + timedelta(days=1, hours=6)).isoformat()
    report = run_daily223_live(cfg, football, odds, cache, target_date=tomorrow, clock=lambda: NOW)[
        "artifacts"
    ]["report"]
    assert report["date"] == tomorrow.isoformat()
    assert report["construction_status"] == "COMPLETE"
    for days in (-1, 8):
        bad_env = setup(tmp_path / str(days))
        cfg, football, odds, cache, calls, *_ = bad_env
        report = run_daily223_live(
            cfg,
            football,
            odds,
            cache,
            target_date=NOW.date() + timedelta(days=days),
            clock=lambda: NOW,
        )["artifacts"]["report"]
        assert report["construction_status"] == "DATA_BLOCKED"
        assert report["blocked_stage"] == "configuration"
        assert calls == {"football": [], "odds": []}


def test_assembler_retains_lower_price_markets_and_busy_bookmaker():
    football, odds = [], []
    for i in range(50):
        fixture = deepcopy(fixtures()[0])
        fixture.update(
            fixture_id=f"api_football:{10000 + i}",
            api_football_fixture_id=str(10000 + i),
            home_team=f"Home {i}",
            away_team=f"Away {i}",
        )
        event = deepcopy(events()[0])
        event.update(
            id=f"event-{i}", home_team=fixture["home_team"], away_team=fixture["away_team"]
        )
        outcomes = event["bookmakers"][0]["markets"][0]["outcomes"]
        outcomes[0]["name"], outcomes[1]["name"] = fixture["home_team"], fixture["away_team"]
        football.append(fixture)
        odds.append(event)
    report = assemble_daily223_candidates(football, odds, config(), NOW.date(), NOW, NOW)
    assert len(report["candidates"]) == 250
    assert any(row["decimal_price"] < 1.96 for row in report["candidates"])
    assert not any(
        row["status"] == "BOOKMAKER_CANDIDATE_CAPACITY_EXCEEDED" for row in report["data_issues"]
    )


def test_optional_markets_use_available_partial_budget_instead_of_skipping_all(tmp_path):
    cfg = config()
    cfg.update(extra_markets=["btts_h1"], max_odds_credits=4)
    report = execute(setup(tmp_path, cfg))["artifacts"]["report"]
    assert report["construction_status"] == "COMPLETE"
    assert report["usage"]["odds_calls"] == 3  # bulk + two of three optional event queries
    assert "btts_h1" in report["market_coverage"]
    partial = next(
        item
        for item in report["data_issues"]
        if item["status"] == "OPTIONAL_MARKETS_PARTIAL_COVERAGE"
    )
    assert partial["retrieved_event_markets"] == 2 and partial["requested_event_markets"] == 3


def test_portfolio_collector_skips_legacy_repeated_enrichment(tmp_path, monkeypatch):
    def no_legacy_builder(*args, **kwargs):
        raise AssertionError("portfolio collection must not construct legacy per-book tickets")

    monkeypatch.setattr(
        "goal_analysis.jobs.daily_223_live._build_for_one_bookmaker", no_legacy_builder
    )
    cfg, football, odds, cache, *_ = setup(tmp_path)
    bundle = run_daily223_live(
        cfg, football, odds, cache, clock=lambda: NOW, construct_legacy=False
    )
    artifacts = bundle["artifacts"]
    assert artifacts["report"]["construction_status"] == "COLLECTION_COMPLETE"
    assert artifacts["report"]["follow_up"] == []
    assert artifacts["candidate_input"]["candidates"]
    assert artifacts["history"]["records"]
    assert artifacts["report"]["usage"]["football_calls"] == 2


def test_betano_prefix_is_preferred_when_it_can_build_the_whole_ticket(tmp_path):
    cfg = config()
    cfg["preferred_bookmakers"] = ["betano"]
    env = setup(tmp_path, cfg)
    for event in env[5]["events"]:
        betano = deepcopy(event["bookmakers"][0])
        betano["key"] = "betano_uk"
        event["bookmakers"].append(betano)
    report = execute(env)["artifacts"]["report"]
    assert report["construction_status"] == "COMPLETE"
    assert report["bookmaker_selection"]["selected"] == "betano_uk"
    assert report["bookmaker_selection"]["preferred"] is True
    assert {leg["bookmaker"] for leg in report["legs"]} == {"betano_uk"}


def test_incomplete_betano_coverage_falls_back_to_one_complete_bookmaker(tmp_path):
    cfg = config()
    cfg["preferred_bookmakers"] = ["betano"]
    env = setup(tmp_path, cfg)
    for event in env[5]["events"][:2]:
        betano = deepcopy(event["bookmakers"][0])
        betano["key"] = "betano_uk"
        event["bookmakers"].append(betano)
    report = execute(env)["artifacts"]["report"]
    assert report["construction_status"] == "COMPLETE"
    assert report["bookmaker_selection"]["selected"] == "synthetic_book"
    assert report["bookmaker_selection"]["method"] == "EVIDENCE_RANKED_FALLBACK"
    assert {leg["bookmaker"] for leg in report["legs"]} == {"synthetic_book"}


def test_prices_from_different_bookmakers_are_never_mixed(tmp_path):
    cfg = config()
    cfg["preferred_bookmakers"] = ["betano"]
    env = setup(tmp_path, cfg)
    for index, event in enumerate(env[5]["events"]):
        event["bookmakers"][0]["key"] = f"book_{index}"
    report = execute(env)["artifacts"]["report"]
    assert report["construction_status"] == "CONSTRUCTION_INCOMPLETE"
    assert not report["legs"]
    assert len(report["bookmaker_selection"]["evaluated"]) == 3


def test_history_cache_reused_but_fixtures_and_prices_refreshed(tmp_path):
    env = setup(tmp_path)
    execute(env)
    bundle = execute(env)
    assert bundle["artifacts"]["history"]["api_calls"] == 0
    assert bundle["artifacts"]["report"]["usage"]["football_calls"] == 1
    assert len(env[4]["odds"]) == 2


def test_config_aliases_are_explicit_and_ambiguity_rejected():
    cfg = config()
    cfg["team_aliases"] = {"1": ["Club A"], "2": [" club  a "]}
    with pytest.raises(ValueError, match="two teams"):
        validate_live_config(cfg)
    cfg = config()
    cfg["api_key"] = "do-not-store-secrets"
    with pytest.raises(ValueError, match="environment"):
        validate_live_config(cfg)


def test_template_prefers_betano_but_does_not_require_it():
    example = json.loads(Path("config/daily223-live.example.json").read_text("utf-8"))
    validated = validate_live_config(example)
    assert validated["odds_region"] == "eu"
    assert validated["preferred_bookmakers"] == ["betano"]


def test_expanded_template_covers_supported_leagues_cups_and_uefa():
    example = validate_live_config(
        json.loads(Path("config/daily223-live.example.json").read_text("utf-8"))
    )
    assert len(example["leagues"]) == 18
    assert {
        item["competition_id"] for item in example["leagues"] if item["competition_type"] == "CUP"
    } == {
        "EN_FA_CUP",
        "EN_EFL_CUP",
        "DE_DFB_POKAL",
        "ES_COPA_DEL_REY",
        "IT_COPPA_ITALIA",
        "FR_COUPE_DE_FRANCE",
    }
    assert {
        item["competition_id"] for item in example["leagues"] if item["competition_type"] == "UEFA"
    } == {"UEFA_CL", "UEFA_EL", "UEFA_ECL"}
    assert example["max_football_calls"] >= 3 * len(example["leagues"])
    assert example["max_odds_credits"] >= 2 * len(example["leagues"])


def test_phase20_bookmaker_config_migrates_without_manual_edit():
    legacy = config()
    legacy["bookmaker_key"] = "old_book"
    legacy.pop("odds_region")
    legacy.pop("preferred_bookmakers")
    validated = validate_live_config(legacy)
    assert validated["odds_region"] == "eu"
    assert validated["preferred_bookmakers"] == ["old_book"]
    assert "bookmaker_key" not in validated


@pytest.mark.parametrize(
    "mutation", ["swapped", "wrong_sport", "wrong_time", "wrong_name", "already_started"]
)
def test_mapping_never_guesses_incompatible_events(mutation):
    data = events()
    event = data[0]
    if mutation == "swapped":
        event["home_team"], event["away_team"] = event["away_team"], event["home_team"]
    elif mutation == "wrong_sport":
        event["sport_key"] = "soccer_england_efl_cup"
    elif mutation == "wrong_time":
        event["commence_time"] = (NOW + timedelta(hours=6, seconds=61)).isoformat()
    elif mutation == "already_started":
        event["commence_time"] = NOW.isoformat()
    else:
        event["home_team"] = "Unrelated club"
    matched, issues = match_daily_events(fixtures(), data, {}, NOW.date(), NOW)
    assert len(matched) == 2
    assert any(item["fixture_id"] == "api_football:9000" for item in issues)


def test_explicit_alias_and_equivalent_timezone_match():
    data = events()
    data[0]["home_team"] = "Known Home Alias"
    data[0]["commence_time"] = "2026-09-20T18:00:00+02:00"
    matched, issues = match_daily_events(
        fixtures(), data, {"1": ["known home alias"]}, NOW.date(), NOW
    )
    assert len(matched) == 3 and not issues


def test_duplicate_or_many_to_one_mapping_is_not_accepted():
    data = events()
    data.append(deepcopy(data[0]))
    assert len(match_daily_events(fixtures(), data, {}, NOW.date(), NOW)[0]) == 2
    data[-1].pop("commence_time")
    assert len(match_daily_events(fixtures(), data, {}, NOW.date(), NOW)[0]) == 2
    football = fixtures()
    duplicate = dict(football[0], fixture_id="api_football:9999", api_football_fixture_id="9999")
    assert len(match_daily_events([*football, duplicate], events(), {}, NOW.date(), NOW)[0]) == 2


@pytest.mark.parametrize("status", ["1H", "TBD", "PST", "CANC", "FT"])
def test_only_confirmed_not_started_football_fixtures_are_pending(status):
    football = fixtures()
    football[0]["provider_status"] = status
    assert len(match_daily_events(football, events(), {}, NOW.date(), NOW)[0]) == 2


def test_quote_age_is_informational_but_fixture_snapshot_is_checked():
    data = events()
    data[0]["bookmakers"][0]["last_update"] = (NOW - timedelta(seconds=300)).isoformat()
    candidate_input = assemble_daily223_candidates(fixtures(), data, config(), NOW.date(), NOW, NOW)
    assert any(item["api_football_fixture_id"] == "9000" for item in candidate_input["candidates"])
    data[0]["bookmakers"][0]["last_update"] = (NOW - timedelta(seconds=301)).isoformat()
    candidate_input = assemble_daily223_candidates(fixtures(), data, config(), NOW.date(), NOW, NOW)
    retained = [c for c in candidate_input["candidates"] if c["api_football_fixture_id"] == "9000"]
    assert retained and all(c["quote_timestamp_status"] == "OLDER" for c in retained)
    with pytest.raises(ProviderError, match="stale"):
        assemble_daily223_candidates(
            fixtures(), events(), config(), NOW.date(), NOW, NOW - timedelta(seconds=301)
        )


def test_three_way_h2h_required_not_two_way_qualification():
    data = events()
    data[0]["bookmakers"][0]["markets"][0]["outcomes"].pop()
    result = assemble_daily223_candidates(fixtures(), data, config(), NOW.date(), NOW, NOW)
    assert not any(
        item["api_football_fixture_id"] == "9000" and item["market_key"] == "h2h"
        for item in result["candidates"]
    )


def test_duplicate_market_or_product_cannot_fall_back_to_a_favorable_price():
    data = events()
    duplicate = deepcopy(data[0]["bookmakers"][0])
    duplicate["last_update"] = (NOW - timedelta(hours=1)).isoformat()
    data[0]["bookmakers"].append(duplicate)
    result = assemble_daily223_candidates(fixtures(), data, config(), NOW.date(), NOW, NOW)
    assert not any(item["api_football_fixture_id"] == "9000" for item in result["candidates"])
    data = events()
    outcomes = data[0]["bookmakers"][0]["markets"][1]["outcomes"]
    outcomes.append({"name": "Over", "price": 1.5, "point": 2.5})
    result = assemble_daily223_candidates(fixtures(), data, config(), NOW.date(), NOW, NOW)
    assert not any(
        item["api_football_fixture_id"] == "9000" and item["market_key"] == "totals_2_5"
        for item in result["candidates"]
    )


@pytest.mark.parametrize("bad_price", [True, float("inf"), float("nan"), 0, "2.3"])
def test_invalid_price_cannot_partially_validate_a_market(bad_price):
    data = events()
    data[0]["bookmakers"][0]["markets"][1]["outcomes"][1]["price"] = bad_price
    result = assemble_daily223_candidates(fixtures(), data, config(), NOW.date(), NOW, NOW)
    assert not any(
        item["api_football_fixture_id"] == "9000" and item["market_key"] == "totals_2_5"
        for item in result["candidates"]
    )


def test_extra_half_time_market_reaches_constructor(tmp_path):
    cfg = config()
    cfg["extra_markets"] = ["btts_h1"]
    bundle = execute(setup(tmp_path, cfg))
    report = bundle["artifacts"]["report"]
    assert report["construction_status"] == "COMPLETE"
    assert "btts_h1" in report["market_coverage"]
    assert any(
        item["period"] == "FIRST_HALF"
        for item in bundle["artifacts"]["candidate_input"]["candidates"]
    )
    assert report["usage"]["odds_calls"] == 4


@pytest.mark.parametrize("mode", ["no_budget", "endpoint_failure"])
def test_optional_market_failure_preserves_valid_base_markets(tmp_path, mode):
    cfg = config()
    cfg["extra_markets"] = ["btts_h1"]
    if mode == "no_budget":
        cfg["max_odds_credits"] = 2
    env = setup(tmp_path, cfg)
    env[5]["extra_failure"] = mode == "endpoint_failure"
    report = execute(env)["artifacts"]["report"]
    assert report["construction_status"] == "COMPLETE"
    assert "btts_h1" not in report["market_coverage"]
    assert "btts_h1" in report["requested_market_coverage"]
    assert any("OPTIONAL_MARKETS" in item["status"] for item in report["data_issues"])


def test_conflicting_extra_event_identity_never_reuses_old_event(tmp_path):
    cfg = config()
    cfg["extra_markets"] = ["btts_h1"]
    env = setup(tmp_path, cfg)
    env[5]["extra_conflict"] = True
    report = execute(env)["artifacts"]["report"]
    assert report["construction_status"] == "CONSTRUCTION_INCOMPLETE"
    assert not report["legs"]


def test_global_budgets_stop_before_requests_and_plan_error_is_reported(tmp_path):
    cfg = config()
    cfg["max_odds_credits"] = 1
    env = setup(tmp_path, cfg)
    report = execute(env)["artifacts"]["report"]
    assert report["construction_status"] == "DATA_BLOCKED"
    assert not env[4]["football"] and not env[4]["odds"]
    env = setup(tmp_path / "plan-error")
    env[5]["history_failure"] = True
    report = execute(env)["artifacts"]["report"]
    assert report["blocked_stage"] == "history"
    assert "plan" in report["reason"]
    assert not env[4]["odds"]


def test_no_fixtures_and_nonpreferred_bookmaker_still_produce_daily_report(tmp_path):
    env = setup(tmp_path)
    env[5]["daily"] = []
    report = execute(env)["artifacts"]["report"]
    assert report["daily_report_required"] is True
    assert report["construction_status"] == "CONSTRUCTION_INCOMPLETE"
    assert not env[4]["odds"]
    env = setup(tmp_path / "no-book")
    for item in env[5]["events"]:
        item["bookmakers"][0]["key"] = "different_book"
    report = execute(env)["artifacts"]["report"]
    assert report["construction_status"] == "COMPLETE"
    assert report["bookmaker_selection"]["selected"] == "different_book"
    assert report["bookmaker_selection"]["method"] == "EVIDENCE_RANKED_FALLBACK"
    assert {leg["bookmaker"] for leg in report["legs"]} == {"different_book"}


def test_transport_exception_does_not_expose_key():
    def fail(url, headers):
        raise ProviderError(url)

    feed = Daily223OddsFeed("special key/+", "eu", 2, transport=fail)
    with pytest.raises(ProviderError) as raised:
        feed.bulk("soccer_germany_bundesliga", NOW.date())
    assert "special key/+" not in str(raised.value)
    assert "special+key%2F%2B" not in str(raised.value)


def test_bundle_files_are_readable_and_not_overwritten(tmp_path):
    bundle = execute(setup(tmp_path))
    output = tmp_path / "run"
    output.mkdir()
    write_daily223_bundle(output, bundle)
    original = (output / "report.json").read_bytes()
    assert "Önálló napi" in (output / "report.md").read_text("utf-8")
    with pytest.raises(FileExistsError):
        write_daily223_bundle(output, bundle)
    assert (output / "report.json").read_bytes() == original


def test_cli_check_and_missing_key_never_make_network_calls(tmp_path):
    settings = tmp_path / "config.json"
    settings.write_text(json.dumps(config()), "utf-8")
    script = Path("scripts/run-daily-223-live.py").resolve()
    env = dict(
        os.environ, PYTHONPATH=str(Path("src").resolve()), API_FOOTBALL_KEY="", THE_ODDS_API_KEY=""
    )
    args = [sys.executable, str(script), "--config", str(settings)]
    check = subprocess.run(
        [*args, "--check-config"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 2
    assert "THE_ODDS_API_KEY" in check.stdout
    assert not (tmp_path / "data").exists()
    output = tmp_path / "live-run"
    run = subprocess.run(
        [*args, "--live", "--output-dir", str(output)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 2
    assert (
        json.loads((output / "report.json").read_text("utf-8"))["construction_status"]
        == "DATA_BLOCKED"
    )
    rerun = subprocess.run(
        [*args, "--live", "--output-dir", str(output)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rerun.returncode == 1


def test_extra_identity_check_includes_sport_and_kickoff():
    base = events()[0]
    other = deepcopy(base)
    other["commence_time"] = (NOW + timedelta(hours=7)).isoformat()
    with pytest.raises(ProviderError, match="identity changed"):
        merge_extra_event(base, other)


def test_malformed_bookmaker_data_becomes_a_blocked_report(tmp_path):
    env = setup(tmp_path)
    env[5]["events"][0]["bookmakers"] = ["invalid record"]
    report = execute(env)["artifacts"]["report"]
    assert report["construction_status"] == "DATA_BLOCKED"
    assert report["blocked_stage"] == "construction"


def test_feed_stops_when_remaining_quota_is_exhausted():
    calls = []

    def transport(url, headers):
        calls.append(url)
        return [], {"x-requests-remaining": "0", "x-requests-last": "2"}, b"[]"

    feed = Daily223OddsFeed("fake", "eu", 10, transport=transport)
    feed.bulk("soccer_germany_bundesliga", NOW.date())
    with pytest.raises(ProviderError, match="remaining quota"):
        feed.bulk("soccer_germany_bundesliga", NOW.date())
    assert len(calls) == 1


def test_unexpected_provider_cost_halts_all_later_calls():
    calls = []

    def transport(url, headers):
        calls.append(url)
        return [], {"x-requests-remaining": "99", "x-requests-last": "5"}, b"[]"

    feed = Daily223OddsFeed("fake", "eu", 10, transport=transport)
    with pytest.raises(ProviderError, match="exceeded the reserved"):
        feed.bulk("soccer_germany_bundesliga", NOW.date())
    with pytest.raises(ProviderError, match="halted"):
        feed.bulk("soccer_germany_bundesliga", NOW.date())
    assert len(calls) == 1
