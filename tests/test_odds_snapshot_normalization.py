"""Native product identity, quote-clock and lossless archival boundaries."""

from copy import deepcopy
from datetime import UTC, datetime
from decimal import localcontext

import pytest

from goal_analysis.normalization.odds_snapshots import (
    normalize_odds_snapshot,
    normalize_saved_quotes,
)

NOW = datetime(2026, 10, 20, 12, 0, 0, 123456, tzinfo=UTC)
SHA = "a" * 64
EVIDENCE = {"observed_at": NOW, "source_url": None, "source_sha256": SHA}


def api_raw():
    return {"errors": [], "response": [{
        "fixture": {"id": "00123"}, "league": {"id": "039", "season": 2026},
        "update": "2026-10-20T13:00:00+02:00",
        "bookmakers": [{"id": "008", "name": "Bet365", "bets": [
            {"id": "005", "name": "Goals Over/Under", "values": [
                {"value": "Over 2.50", "odd": "2.500", "id": "007"},
                {"value": "Under 2.5", "odd": "1.80"}]}]}]}]}


def odds_raw():
    return [{"id": "event_001", "sport_key": "soccer_epl", "home_team": "Équipe-A",
             "away_team": "Team B", "bookmakers": [{"key": "book_se", "title": "Book (SE)",
             "last_update": "2026-10-20T11:00:00Z", "markets": [
                 {"key": "h2h", "outcomes": [{"name": "Équipe-A", "price": 2.5},
                                             {"name": "Draw", "price": 3.2}]}]}]}]


def stats_raw():
    return {"data": {"match_id": "mt_001", "competition_id": "comp_039",
                     "season_id": "sn_026", "bookmakers": [{"bookmaker": "Bet365", "id": 8,
                     "markets": {"match_odds": {
                         "home": {"id": 7, "opening": "2.10", "last_seen": "2.20"},
                         "draw": {"opening": "3.20"}, "away": 3.5},
                         "total_goals": {"-0.00": {"over": {"last_seen": "1.9"}}}}}]}}


def norm(provider, raw, **evidence):
    return normalize_odds_snapshot(provider, raw, **{**EVIDENCE, **evidence})


def saved_row():
    return {"fixture_id": "arthur:123", "quote_provider": "api_football",
            "quote_source_id": "https://v3.football.api-sports.io/odds?fixture=00123&bet=5",
            "market_key": "totals", "selection_key": "over_2.5", "period": "FULL_TIME",
            "settlement": "REGULATION", "bookmaker": "Bet365", "decimal_price": "2.50",
            "quoted_at": "2026-10-20T11:00:00Z", "region": None, "currency": None}


def saved(rows, provider="api_football", **evidence):
    return normalize_saved_quotes(provider, rows, **{**EVIDENCE, **evidence})


def test_api_exact_native_ids_and_decimal_equivalence_without_identity_rewrites():
    result = norm("api_football", api_raw())
    assert not result["rejected"] and not result["unsupported"]
    record, other = result["records"]
    assert (record["fixture_id"], record["competition_id"], record["season_id"],
            record["bookmaker"], record["provider_market_id"], record["provider_selection_id"]) == (
                "00123", "039", "2026", "008", "005", "007")
    assert (record["market_key"], record["selection_key"], record["line"], record["decimal_price"]) == (
        "totals", "over", "2.5", "2.5")
    assert other["line"] == "2.5"
    assert record["quoted_at"] == "2026-10-20T11:00:00.000000+00:00"
    assert record["quote_time_status"] == "KNOWN"
    assert record["settlement"] == "REGULATION"
    assert record["region"] is record["currency"] is None
    assert record["raw"]["odd"] == "2.500"


def test_numeric_native_json_ids_become_strings_and_not_boolean_aliases():
    raw = api_raw()
    row = raw["response"][0]
    row["fixture"]["id"] = 123
    row["bookmakers"][0]["id"] = 8
    result = norm("api_football", raw)
    assert result["records"][0]["fixture_id"] == "123"
    assert result["records"][0]["bookmaker"] == "8"
    row["fixture"]["id"] = True
    result = norm("api_football", raw)
    assert result["records"] == [] and len(result["rejected"]) == 1


@pytest.mark.parametrize("stamp,status", [(None, "MISSING"), ("2026-10-20T11:00:00", "INVALID"),
    (123, "INVALID"), ("nonsense", "INVALID"), ("2026-10-20T12:00:00.123457Z", "FUTURE")])
def test_bad_or_unknown_quote_time_preserves_prices_and_never_uses_receipt(stamp, status):
    raw = api_raw()
    raw["response"][0]["update"] = stamp
    result = norm("api_football", raw)
    assert len(result["records"]) == 2
    assert all(row["quoted_at"] is None and row["quote_time_status"] == status
               for row in result["records"])
    assert result["raw"]["response"][0]["update"] == stamp


def test_stats_opening_last_seen_scalar_raw_and_absent_quote_clock_remain_distinct():
    result = norm("the_stats_api", stats_raw())
    records = result["records"]
    assert len(records) == 5 and not result["rejected"]
    home = [row for row in records if row["selection_key"] == "home"]
    assert [(row["price_kind"], row["decimal_price"]) for row in home] == [
        ("OPENING", "2.1"), ("LAST_SEEN", "2.2")]
    assert all(row["quoted_at"] is None and row["quote_time_status"] == "MISSING" for row in records)
    assert all(row["bookmaker_id"] == "8" for row in records)
    assert all(row["provider_selection_id"] == "7" for row in home)
    away = next(row for row in records if row["selection_key"] == "away")
    assert away["raw"] == {"value": 3.5} and away["price_kind"] == "LAST_SEEN"
    assert next(row for row in records if row["market_key"] == "totals")["line"] == "0"


def test_stats_only_price_specific_explicit_timestamp_is_a_quote_time():
    raw = stats_raw()
    value = raw["data"]["bookmakers"][0]["markets"]["match_odds"]["home"]
    value.update(opening_at="2026-10-19T12:00:00Z", timestamp="2026-10-20T11:00:00Z")
    result = norm("the_stats_api", raw)
    opening, latest = result["records"][:2]
    assert opening["quoted_at"] == "2026-10-19T12:00:00.000000+00:00"
    assert latest["quoted_at"] is None


def test_odds_uses_book_timestamp_only_when_market_timestamp_is_absent():
    raw = odds_raw()
    assert norm("the_odds_api", raw)["records"][0]["quote_time_status"] == "KNOWN"
    raw[0]["bookmakers"][0]["markets"][0]["last_update"] = "bad"
    result = norm("the_odds_api", raw)
    assert len(result["records"]) == 2
    assert all(row["quote_time_status"] == "INVALID" for row in result["records"])


def test_odds_exact_teams_preserve_book_and_optional_ids_without_geography_inference():
    raw = odds_raw()
    market = raw[0]["bookmakers"][0]["markets"][0]
    market["id"] = "0007"
    market["outcomes"][0]["id"] = 9
    result = norm("the_odds_api", raw)
    record = result["records"][0]
    assert (record["fixture_id"], record["bookmaker"], record["selection_key"]) == (
        "event_001", "book_se", "home")
    assert record["provider_market_id"] == "0007" and record["provider_selection_id"] == "9"
    assert record["region"] is record["currency"] is None
    market["outcomes"][0]["name"] = "Équipe A"
    result = norm("the_odds_api", raw)
    assert [row["selection_key"] for row in result["records"]] == ["draw"]
    assert result["rejected"][0]["reason"] == "AMBIGUOUS_H2H_SELECTION"


@pytest.mark.parametrize("provider", ["api_football", "the_odds_api", "the_stats_api"])
def test_unknown_markets_retain_prices_or_explicit_raw_diagnostic_without_promotion(provider):
    raw = {"api_football": api_raw, "the_odds_api": odds_raw, "the_stats_api": stats_raw}[provider]()
    if provider == "api_football":
        raw["response"][0]["bookmakers"][0]["bets"][0]["name"] = "Novel Market"
    elif provider == "the_odds_api":
        raw[0]["bookmakers"][0]["markets"][0]["key"] = "player_novel"
    else:
        raw["data"]["bookmakers"][0]["markets"] = {"novel": {"special": {"last_seen": "2.50"}}}
    result = norm(provider, raw)
    assert result["raw"] == raw and result["unsupported"]
    assert result["records"]
    assert all(row["mapping_status"] == "UNSUPPORTED" and row["settlement"] == "UNKNOWN"
               and row["period"] == "UNKNOWN" for row in result["records"])


def test_non_soccer_h2h_is_not_silently_assigned_football_regulation_scope():
    raw = odds_raw()
    raw[0]["sport_key"] = "basketball_nba"
    result = norm("the_odds_api", raw)
    assert result["unsupported"] and result["records"][0]["settlement"] == "UNKNOWN"


@pytest.mark.parametrize("bad", [True, 1, 0, -2, "NaN", "Infinity", "bad", "1e51", "2e-51", "2" * 101])
def test_invalid_prices_reject_only_the_bad_outcome(bad):
    raw = api_raw()
    raw["response"][0]["bookmakers"][0]["bets"][0]["values"][0]["odd"] = bad
    result = norm("api_football", raw)
    assert len(result["records"]) == len(result["rejected"]) == 1
    assert result["records"][0]["selection_key"] == "under"


def test_decimal_formatting_is_exact_under_small_global_decimal_precision():
    raw = api_raw()
    raw["response"][0]["bookmakers"][0]["bets"][0]["values"][0]["odd"] = "2.123456789012345678900"
    with localcontext() as context:
        context.prec = 3
        result = norm("api_football", raw)
    assert result["records"][0]["decimal_price"] == "2.1234567890123456789"


def test_returned_raw_and_quote_fragments_are_detached_from_input_and_each_other():
    raw = api_raw()
    original = deepcopy(raw)
    result = norm("api_football", raw)
    raw["response"][0]["bookmakers"][0]["bets"][0]["values"][0]["odd"] = "99"
    assert result["raw"] == original
    result["records"][0]["raw"]["odd"] = "77"
    assert result["raw"]["response"][0]["bookmakers"][0]["bets"][0]["values"][0]["odd"] == "2.500"


@pytest.mark.parametrize("raw", [{"response": [], "bad": float("nan")},
                                  {"response": [], "bad": (1, 2)},
                                  {"response": [], "bad": {1: "value"}}])
def test_non_json_or_nonfinite_input_is_an_atomic_contract_error(raw):
    with pytest.raises(ValueError, match="FINITE_JSON_REQUIRED"):
        norm("api_football", raw)


@pytest.mark.parametrize("url", ["http://example.com/a", "https://name:pass@example.com/a",
    "https://example.com/a?api_key=secret", "https://example.com/a?token=",
    "https://example.com/a#fragment", "https://example.com/a b", "https://example.com:bad/a"])
def test_source_metadata_rejects_non_public_or_malformed_urls(url):
    with pytest.raises(ValueError, match="HTTPS"):
        norm("api_football", api_raw(), source_url=url)


@pytest.mark.parametrize("evidence", [{"observed_at": "2026-10-20T12:00:00"},
                                       {"observed_at": 123}, {"source_sha256": "bad"}])
def test_strict_receipt_and_raw_sha_contract(evidence):
    with pytest.raises(ValueError):
        norm("api_football", api_raw(), **evidence)


def test_saved_quotes_exact_url_identity_and_original_product_fields_are_retained():
    row = saved_row()
    result = saved([row])
    record = result["records"][0]
    assert not result["rejected"] and not result["unsupported"]
    assert record["fixture_id"] == "00123" and record["fixture_identity_status"] == "NATIVE_URL"
    assert record["selection_key"] == "over_2.5" and record["line"] is None
    assert record["settlement"] == row["settlement"] and record["period"] == row["period"]
    assert record["raw"]["quote_source_id"] == row["quote_source_id"]
    assert record["source_url"] is None and record["decimal_price"] == "2.5"


@pytest.mark.parametrize("source", [None, "https://evil.example/odds?fixture=123",
    "https://v3.football.api-sports.io/odds?fixture=123&fixture=124",
    "https://v3.football.api-sports.io/odds?fixture=123&api_key=secret"])
def test_saved_unproven_native_identity_remains_explicitly_unsupported(source):
    row = saved_row()
    row["quote_source_id"] = source
    result = saved([row])
    record = result["records"][0]
    assert record["fixture_id"] == "UNKNOWN_NATIVE_ID:arthur:123"
    assert record["fixture_identity_status"] == "UNKNOWN_NATIVE_ID"
    assert record["mapping_status"] == "UNSUPPORTED" and result["unsupported"]


@pytest.mark.parametrize("provider,url,native", [
    ("the_odds_api", "https://api.the-odds-api.com/v4/sports/soccer_epl/events/abc123/odds", "abc123"),
    ("the_stats_api", "https://api.thestatsapi.com/football/matches/mt_001/odds?bookmaker=bet365", "mt_001")])
def test_saved_provider_native_event_urls_are_exact_and_scoped(provider, url, native):
    row = saved_row()
    row.update(quote_provider=provider, quote_source_id=url)
    assert saved([row], provider)["records"][0]["fixture_id"] == native


def test_saved_invalid_row_is_isolated_and_quote_time_is_not_parent_receipt():
    good = saved_row()
    good["quoted_at"] = "2026-10-20T11:00:00"
    result = saved([None, {**saved_row(), "quote_provider": "the_odds_api"}, good])
    assert len(result["rejected"]) == 2 and len(result["records"]) == 1
    assert result["records"][0]["quote_time_status"] == "INVALID"
    assert result["records"][0]["quoted_at"] is None


def test_saved_unknown_market_retains_original_descriptor_without_supported_claim():
    row = saved_row()
    row.update(market_key="novel_player_product", selection_key="Player A over 2.25")
    result = saved([row])
    record = result["records"][0]
    assert record["market_key"] == row["market_key"] and record["selection_key"] == row["selection_key"]
    assert record["period"] == row["period"] and record["settlement"] == row["settlement"]
    assert record["mapping_status"] == "UNSUPPORTED"
    assert result["unsupported"][0]["reason"] == "UNSUPPORTED_MARKET"


def api_native_market(market_id, name, values):
    raw = api_raw()
    raw["response"][0]["bookmakers"][0]["bets"] = [
        {"id": market_id, "name": name, "values": values}]
    return raw


def test_real_numeric_exact_goals_selector_is_archived_without_canonical_mapping():
    raw = api_native_market(38, "Exact Goals Number", [{"value": 0, "odd": "17.00"}])
    result = norm("api_football", raw)
    record = result["records"][0]
    assert not result["rejected"]
    assert record["provider_market_id"] == "38" and record["selection_key"] == "0"
    assert record["provider_selection_type"] == "JSON_NUMBER"
    assert record["decimal_price"] == "17" and record["raw"] == {"value": 0, "odd": "17.00"}
    assert record["mapping_status"] == "UNSUPPORTED"
    assert record["period"] == record["settlement"] == "UNKNOWN"


def test_real_padded_player_selector_is_retained_exactly_without_trimming_or_type_churn():
    fragment = {"value": "Valdemar Andreasen - 1 ", "odd": "1.07"}
    raw = api_native_market(215, "Player Singles", [fragment])
    result = norm("api_football", raw)
    record = result["records"][0]
    assert not result["rejected"]
    assert record["selection_key"] == "Valdemar Andreasen - 1 "
    assert "provider_selection_type" not in record
    assert record["decimal_price"] == "1.07" and record["raw"] == fragment
    assert record["mapping_status"] == "UNSUPPORTED"
    assert record["raw_path"] == "/response/0/bookmakers/0/bets/0/values/0"


def test_numeric_and_string_native_labels_keep_separate_product_identity_descriptors():
    raw = api_native_market(38, "Exact Goals Number", [
        {"value": 0, "odd": "17.00"}, {"value": "0", "odd": "17.00"},
        {"value": 2.5, "odd": "4.00"}, {"value": "2.5", "odd": "4.00"}])
    records = norm("api_football", raw)["records"]
    assert [record["selection_key"] for record in records] == ["0", "0", "2.5", "2.5"]
    assert [record.get("provider_selection_type") for record in records] == [
        "JSON_NUMBER", None, "JSON_NUMBER", None]
    assert [record["raw"]["value"] for record in records] == [0, "0", 2.5, "2.5"]


@pytest.mark.parametrize("selector", [True, False, {}, [], {"name": "zero"}, [0]])
def test_unknown_boolean_or_structured_selectors_are_explicit_and_never_numeric_aliases(selector):
    fragment = {"value": selector, "odd": "17.00"}
    result = norm("api_football", api_native_market(38, "Exact Goals Number", [fragment]))
    assert result["records"] == [] and result["rejected"] == []
    issue = next(item for item in result["unsupported"]
                 if item["reason"] == "UNSUPPORTED_NATIVE_SELECTION_TYPE")
    assert issue["raw"] == fragment


@pytest.mark.parametrize("selector", [None, "", " ", "\t", "\n", "a\x00b", "a\nb"])
def test_unknown_missing_or_empty_native_label_is_still_rejected(selector):
    result = norm("api_football", api_native_market(38, "Exact Goals Number", [
        {"value": selector, "odd": "17.00"}]))
    assert result["records"] == []
    assert result["rejected"][0]["reason"] == "NONEMPTY_NATIVE_TEXT_REQUIRED"


def test_missing_native_label_field_is_still_rejected():
    result = norm("api_football", api_native_market(38, "Exact Goals Number", [{"odd": "17.00"}]))
    assert result["records"] == []
    assert result["rejected"][0]["reason"] == "NONEMPTY_NATIVE_TEXT_REQUIRED"


@pytest.mark.parametrize("selector", [0, "Over 2.5 ", " Over 2.5"])
def test_supported_product_selection_guards_are_unchanged(selector):
    raw = api_raw()
    raw["response"][0]["bookmakers"][0]["bets"][0]["values"][0]["value"] = selector
    result = norm("api_football", raw)
    assert len(result["records"]) == 1
    assert result["records"][0]["selection_key"] == "under"
    assert all("provider_selection_type" not in row for row in result["records"])


@pytest.mark.parametrize("field,value", [("fixture", " 123"), ("fixture", 0), ("fixture", True),
                                        ("bookmaker", " 8"), ("market", "38 ")])
def test_opaque_selection_recovery_does_not_relax_native_id_guards(field, value):
    raw = api_native_market(38, "Exact Goals Number", [{"value": 0, "odd": "17.00"}])
    row = raw["response"][0]
    if field == "fixture":
        row["fixture"]["id"] = value
    elif field == "bookmaker":
        row["bookmakers"][0]["id"] = value
    else:
        row["bookmakers"][0]["bets"][0]["id"] = value
    result = norm("api_football", raw)
    assert result["records"] == [] and result["rejected"]
