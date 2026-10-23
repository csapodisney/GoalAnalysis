"""Provenance and fixture-scope regressions for the SHADOW audit adapter."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs.source_resolution import build_source_resolution

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
KICKOFF = NOW + timedelta(hours=5)
FIXTURE_ID = "api_football:1"
ODDS_SOURCES = {
    "the_odds_api": "https://api.the-odds-api.com/v4/sports/soccer_epl/events/test/odds",
    "api_football": "https://v3.football.api-sports.io/odds?fixture=1",
    "the_stats_api": "https://api.thestatsapi.com/api/football/matches/mt_1/odds",
}


def fixture(**changes):
    return {"fixture_id": FIXTURE_ID, "api_football_fixture_id": "1",
            "api_football_home_team_id": "10", "api_football_away_team_id": "20",
            "api_football_league_id": "39", "home_team": "Home United",
            "away_team": "Away City", "kickoff": KICKOFF.isoformat(), **changes}


def report(**fixture_changes):
    return {"fixtures": [fixture(**fixture_changes)]}


def quote(**changes):
    return {"fixture_id": FIXTURE_ID, "quote_provider": "the_odds_api",
            "bookmaker": "test", "market_key": "h2h", "selection_key": "home",
            "period": "FULL_TIME", "settlement": "REGULATION_TIME", "region": "DE",
            "currency": "EUR", "quote_available": True, "decimal_price": 2.1,
            "quoted_at": NOW.isoformat(), "quote_source_id": ODDS_SOURCES["the_odds_api"],
            **changes}


def artifacts(*quotes):
    return {"candidate_input": {"observed_at": NOW.isoformat(), "candidates": list(quotes)}}


def weather(**changes):
    return {"status": "SOURCED", "provider": "open_meteo", "fixture_id": FIXTURE_ID,
            "kickoff": KICKOFF.isoformat(), "observed_at": NOW.isoformat(),
            "raw_payload_hash": "a" * 64,
            "source_url": "https://api.open-meteo.com/v1/forecast", "temperature_c": 12,
            **changes}


def claim(*, fetched_at=None, published_at=None, fixture_id=FIXTURE_ID):
    published = NOW - timedelta(minutes=1) if published_at is None else published_at
    snowflake = (int(published.timestamp() * 1000) - 1288834974657) << 22
    return {"claim_id": "claim1", "fixture_id": fixture_id,
            "source_url": f"https://x.com/TestAuthor/status/{snowflake}",
            "author": "TestAuthor", "published_at": published.isoformat(),
            "fetched_at": (NOW if fetched_at is None else fetched_at).isoformat(),
            "kind": "INJURY", "claim": "Sourced model summary"}


def fields_for(result, domain):
    return [item for item in result["fields"] if item["domain"] == domain]


@pytest.mark.parametrize("provider,source", list(ODDS_SOURCES.items()))
def test_each_known_quote_provider_accepts_its_matching_source_host(provider, source):
    result = build_source_resolution(report(), artifacts(quote(quote_provider=provider,
                                     quote_source_id=source)), as_of=NOW)
    assert result["summary"]["by_provider"][provider] == 1
    resolved = fields_for(result, "odds")
    assert len(resolved) == 1 and resolved[0]["resolution"]["status"] == "RESOLVED"
    assert resolved[0]["resolution"]["selected"]["source_url"] == source


@pytest.mark.parametrize("provider,source", [
    ("the_odds_api", "https://unrelated.example.test/no-evidence"),
    ("the_odds_api", ODDS_SOURCES["api_football"]),
    ("api_football", ODDS_SOURCES["the_odds_api"]),
    ("the_stats_api", ODDS_SOURCES["the_odds_api"]),
    ("the_odds_api", "https://api.the-odds-api.com.unrelated.example/odds"),
])
def test_wrong_quote_source_host_is_rejected_without_discarding_valid_quote(provider, source):
    bad = quote(quote_provider=provider, quote_source_id=source)
    result = build_source_resolution(report(), artifacts(bad, quote()), as_of=NOW)
    assert result["summary"]["rejected_inputs"]["INVALID_QUOTE_SOURCE"] == 1
    assert result["summary"]["by_provider"]["the_odds_api"] == 1
    assert len(fields_for(result, "odds")) == 1


@pytest.mark.parametrize("field,bad", [
    ("api_football_fixture_id", "999"), ("api_football_home_team_id", "999"),
    ("api_football_away_team_id", "999"), ("api_football_league_id", "999"),
    ("home_team", "Wrong United"), ("away_team", "Wrong City"),
    ("kickoff", (KICKOFF + timedelta(seconds=1)).isoformat()),
    ("kickoff", KICKOFF.replace(tzinfo=None).isoformat()),
])
def test_optional_quote_identity_mismatch_is_missing_link_and_isolated(field, bad):
    result = build_source_resolution(report(), artifacts(quote(**{field: bad}), quote()), as_of=NOW)
    assert result["summary"]["rejected_inputs"]["MISSING_LINK"] == 1
    assert result["summary"]["by_provider"]["the_odds_api"] == 1
    selected = fields_for(result, "odds")[0]["resolution"]["selected"]
    assert selected["value"] == 2.1 and selected["identity_verified"] is True


def test_matching_supplied_quote_identity_is_preserved_and_not_mutated():
    supplied = {key: value for key, value in fixture().items() if key != "fixture_id"}
    supplied["kickoff"] = "2026-10-20T19:00:00+02:00"
    value, inputs = report(), artifacts(quote(**supplied))
    before_value, before_inputs = deepcopy(value), deepcopy(inputs)
    result = build_source_resolution(value, inputs, as_of=NOW)
    assert result["summary"]["by_provider"]["the_odds_api"] == 1
    assert value == before_value and inputs == before_inputs
    assert result["summary"]["external_requests"] == 0


def test_absent_optional_quote_identity_fields_do_not_become_a_false_mismatch():
    result = build_source_resolution(report(), artifacts(quote()), as_of=NOW)
    assert result["summary"]["by_provider"]["the_odds_api"] == 1
    assert result["summary"]["rejected_inputs"].get("MISSING_LINK", 0) == 0


@pytest.mark.parametrize("changes", [
    {"fixture_id": "api_football:999"},
    {"kickoff": (KICKOFF + timedelta(hours=1)).isoformat()},
    {"kickoff": KICKOFF.replace(tzinfo=None).isoformat()},
])
def test_weather_for_another_fixture_or_instant_cannot_be_verified(changes):
    first = fixture(weather=weather(**changes))
    second = fixture(fixture_id="api_football:2", api_football_fixture_id="2",
                     weather=weather(fixture_id="api_football:2"))
    result = build_source_resolution({"fixtures": [first, second]}, {}, as_of=NOW)
    resolved = {item["fixture_id"]: item["resolution"] for item in fields_for(result, "weather")}
    assert resolved[FIXTURE_ID]["status"] == "MISSING"
    assert resolved["api_football:2"]["status"] == "RESOLVED"
    assert result["summary"]["rejected_inputs"]["MISSING_LINK"] == 1
    assert result["summary"]["by_provider"]["open_meteo"] == 1


@pytest.mark.parametrize("with_optional_identity", [True, False])
def test_matching_or_absent_optional_weather_identity_is_retained(with_optional_identity):
    forecast = weather(kickoff="2026-10-20T19:00:00+02:00")
    if not with_optional_identity:
        del forecast["fixture_id"]
        del forecast["kickoff"]
    value = report(weather=forecast)
    before = deepcopy(value)
    result = build_source_resolution(value, {}, as_of=NOW)
    assert result["summary"]["by_provider"]["open_meteo"] == 1
    assert fields_for(result, "weather")[0]["resolution"]["selected"]["value"] == forecast
    assert value == before


@pytest.mark.parametrize("cutoff", [NOW, NOW.replace(microsecond=123456)])
def test_news_fetched_at_exact_cutoff_is_visible_with_or_without_fractional_seconds(cutoff):
    assertion = claim(fetched_at=cutoff, published_at=cutoff - timedelta(minutes=1))
    value = report()
    value["grok_review"] = {"claims": [assertion]}
    before = deepcopy(value)
    result = build_source_resolution(value, {}, as_of=cutoff)
    assert result["summary"]["by_provider"]["grok"] == 1
    selected = fields_for(result, "news")[0]["resolution"]["selected"]
    assert selected["value"]["content_status"] == "UNVERIFIED"
    assert selected["value"]["source_url"] == assertion["source_url"]
    assert value == before


def test_future_news_receipt_is_rejected_while_a_valid_cutoff_receipt_survives():
    value = report()
    value["grok_review"] = {"claims": [claim(fetched_at=NOW + timedelta(microseconds=1)), claim()]}
    result = build_source_resolution(value, {}, as_of=NOW)
    assert result["summary"]["rejected_inputs"]["INVALID_NEWS_PROVENANCE"] == 1
    assert result["summary"]["by_provider"]["grok"] == 1
