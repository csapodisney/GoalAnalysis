"""Provider-native market meanings survive normalization and SHADOW storage."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.normalization.odds_snapshots import normalize_odds_snapshot
from goal_analysis.providers.the_stats_odds import FAMILIES, event_from_prices
from goal_analysis.storage import Database
from goal_analysis.storage.odds_snapshots import OddsSnapshots

NOW = datetime(2026, 10, 21, 21, tzinfo=UTC)
EVIDENCE = {"observed_at": NOW, "source_sha256": "a" * 64,
    "source_url": "https://api.thestatsapi.com/api/football/matches/mt_123/odds"}


def value():
    return {"opening": "2.100", "last_seen": "2.250"}


def payload(markets):
    return {"data": {"match_id": "mt_123", "bookmakers": [
        {"bookmaker": "Bet365", "markets": markets}]}}


def normalize(markets):
    return normalize_odds_snapshot("the_stats_api", payload(markets), **EVIDENCE)


def test_dnb_is_not_h2h_or_synthetic_zero_handicap_and_retains_both_prices():
    result = normalize({"draw_no_bet": {"home": value(), "away": value()}})
    assert not result["rejected"] and not result["unsupported"]
    assert len(result["records"]) == 4
    for row in result["records"]:
        assert row["market_key"] == "dnb" and row["provider_market_key"] == "draw_no_bet"
        assert row["line"] is None and row["line_basis"] == "NONE"
        assert row["payout_rule"] == "DRAW_REFUND" and row["eligibility"] == "SHADOW_ONLY"
        assert row["quoted_at"] is None and row["quote_time_status"] == "MISSING"
        assert row["settlement"] == "REGULATION"
    assert {r["price_kind"] for r in result["records"]} == {"OPENING", "LAST_SEEN"}


@pytest.mark.parametrize("line,rule", [("-1.25", "SPLIT_HALF_STAKES"),
    ("+0.75", "SPLIT_HALF_STAKES"), ("-1", "PUSH_ON_EQUAL"),
    ("+0.0", "PUSH_ON_EQUAL"), ("-0.5", "BINARY_NO_PUSH")])
@pytest.mark.parametrize("side", ["home", "away"])
def test_asian_line_is_relative_to_selected_team_and_no_opposite_is_invented(line, rule, side):
    result = normalize({"asian_handicap": {side: {line: value()}}})
    assert len(result["records"]) == 2 and not result["unsupported"]
    row = result["records"][1]
    assert row["selection_key"] == side and row["market_key"] == "asian_handicap"
    assert row["payout_rule"] == rule and row["line_basis"] == "SELECTED_TEAM"
    assert row["raw_path"] == f"/data/bookmakers/0/markets/asian_handicap/{side}/{line}/last_seen"
    from decimal import Decimal
    assert Decimal(row["line"]) == Decimal(line)


def test_three_way_handicap_keeps_home_line_for_all_three_outcomes():
    result = normalize({"handicap_result": {"-2": {s: value() for s in ("home", "draw", "away")}}})
    assert len(result["records"]) == 6 and not result["unsupported"]
    for row in result["records"]:
        assert row["market_key"] == "handicap_3_way" and row["line"] == "-2"
        assert row["payout_rule"] == "EUROPEAN_THREE_WAY" and row["line_basis"] == "HOME_TEAM"
    assert {r["selection_key"] for r in result["records"]} == {"home", "draw", "away"}


@pytest.mark.parametrize("native,period", [("total_goals", "FULL_TIME"),
    ("first_half_total_goals", "FIRST_HALF")])
@pytest.mark.parametrize("line,rule", [("2.25", "SPLIT_HALF_STAKES"),
    ("0", "PUSH_ON_EQUAL"), ("2.5", None)])
def test_integer_quarter_and_half_totals_remain_distinct(native, period, line, rule):
    result = normalize({native: {line: {s: value() for s in ("over", "under")}}})
    assert len(result["records"]) == 4 and not result["unsupported"]
    for row in result["records"]:
        assert row["period"] == period and row["line"] == line and row["market_key"] == "totals"
        assert row.get("payout_rule") == rule


@pytest.mark.parametrize("native,selections,market,period", [
    ("match_odds", ("home", "draw", "away"), "h2h", "FULL_TIME"),
    ("first_half_result", ("home", "draw", "away"), "h2h", "FIRST_HALF"),
    ("btts", ("yes", "no"), "btts", "FULL_TIME"),
    ("btts_first_half", ("yes", "no"), "btts", "FIRST_HALF")])
def test_existing_markets_keep_their_product_descriptors(native, selections, market, period):
    result = normalize({native: {s: value() for s in selections}})
    assert len(result["records"]) == 2 * len(selections)
    assert all(r["market_key"] == market and r["period"] == period
        and "eligibility" not in r for r in result["records"])


@pytest.mark.parametrize("bad", ["1.1", "NaN", "Infinity", "1e99", "line", "1,25"])
@pytest.mark.parametrize("native", ["asian_handicap", "handicap_result", "total_goals"])
def test_bad_ladder_line_is_diagnosed_without_losing_valid_dnb(native, bad):
    ladder = {bad: {"home": value()}}
    if native == "asian_handicap":
        ladder = {"home": {bad: value()}}
    result = normalize({native: ladder, "draw_no_bet": {"home": value()}})
    assert len(result["records"]) == 2 and result["unsupported"]
    assert all(r["market_key"] == "dnb" for r in result["records"])


def test_ambiguous_canonical_lines_are_not_arbitrarily_chosen():
    result = normalize({"asian_handicap": {"home": {"-0.0": value(), "+0": value(), "-1": value()}}})
    assert len(result["records"]) == 2
    assert all(r["line"] == "-1" for r in result["records"])
    assert [i["reason"] for i in result["unsupported"]] == ["AMBIGUOUS_CANONICAL_LINE"] * 2
    assert result["raw"] == payload({"asian_handicap": {"home": {"-0.0": value(), "+0": value(), "-1": value()}}})


def test_wrong_orientation_fractional_european_and_negative_totals_are_not_promoted():
    result = normalize({"asian_handicap": {"-1": {"home": value()}},
        "handicap_result": {"-0.5": {"draw": value()}},
        "total_goals": {"-1": {"over": value()}}})
    assert not result["records"] and len(result["unsupported"]) == 3


def test_unknown_half_handicap_and_qualify_never_become_regulation_dnb():
    result = normalize({"first_half_asian_handicap": {"home": {"-1": value()}},
        "to_qualify": {"home": value()}})
    assert all(r["mapping_status"] == "UNSUPPORTED" and r["period"] == "UNKNOWN"
        for r in result["records"])
    assert len(result["unsupported"]) == 2


def test_suspended_and_unpriced_outcomes_are_not_filled_from_opening_price():
    result = normalize({"draw_no_bet": {"home": {"opening": "2.10", "last_seen": None},
        "away": {"opening": None, "last_seen": "1.0"}, "draw": value()}})
    assert len(result["records"]) == 1 and result["records"][0]["price_kind"] == "OPENING"
    assert len(result["rejected"]) == 1 and len(result["unsupported"]) == 1


def test_new_products_roundtrip_existing_sqlite_schema_idempotently_and_as_of(tmp_path):
    markets = {"draw_no_bet": {"home": value()},
        "asian_handicap": {"away": {"-0.25": value()}},
        "handicap_result": {"-1": {"away": value()}},
        "first_half_total_goals": {"1.25": {"over": value()}}}
    original = deepcopy(markets)
    parsed = normalize(markets)
    assert markets == original
    store = OddsSnapshots(Database(tmp_path / "odds.sqlite3"), clock=lambda: NOW + timedelta(seconds=1))
    store.initialize()
    parent = {**EVIDENCE, "provider": "the_stats_api", "source_ref": "native-markets-v1"}
    first = store.ingest(parsed["records"], **parent)
    assert first["products_created"] == 4 and first["quotes_created"] == 8
    assert store.ingest(parsed["records"], **parent)["quotes_created"] == 0
    assert store.quotes(as_of=NOW) == []
    rows = store.quotes(as_of=NOW + timedelta(seconds=2))
    assert len(rows) == 8 and all(r["eligibility"] == "SHADOW_ONLY" for r in rows)
    assert {r["market_key"] for r in rows} == {"dnb", "asian_handicap", "handicap_3_way", "totals"}


def test_shadow_markets_do_not_appear_in_live_candidate_feed():
    assert not {"dnb", "asian_handicap", "handicap_3_way"} & FAMILIES.keys()
    fixture = {"fixture_id": "the_stats_api:mt_123", "source_provider": "the_stats_api",
        "provider_fixture_id": "mt_123", "provider_league_id": "comp_1", "provider_home_team_id": "tm_1",
        "provider_away_team_id": "tm_2", "provider_season_id": "sn_1", "sport_key": "soccer_epl",
        "home_team": "Home", "away_team": "Away", "kickoff": "2026-10-22T19:00:00Z"}
    raw = payload({"draw_no_bet": {"home": value(), "away": value()}})["data"]
    event = event_from_prices(raw, fixture,
        {"source_url": EVIDENCE["source_url"], "content_hash": "a" * 64, "observed_at": NOW.isoformat()},
        {"dnb", "asian_handicap", "handicap_3_way"})
    assert event["bookmakers"] == []
