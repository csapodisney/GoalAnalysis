from copy import deepcopy
from datetime import timedelta

from test_the_stats_context import NOW, Transport, report, run

from goal_analysis.jobs.source_resolution import build_source_resolution


def test_real_collection_contract_reaches_shadow_fields_without_mutations_or_fetches(tmp_path):
    transport = Transport()
    value = report()
    value["sports_supplement"] = {"the_stats_api": run(tmp_path, transport, value)}
    before, requests = deepcopy(value), len(transport.calls)
    result = build_source_resolution(value, {}, as_of=NOW + timedelta(minutes=1))
    assert value == before and len(transport.calls) == requests
    fields = {item["field"]: item["resolution"] for item in result["fields"]}
    assert fields["home.season.expected_goals_for"]["selected"]["provider"] == "the_stats_api"
    assert fields["home.season.expected_goals_for"]["selected"]["value"] == 8.4
    assert fields["home.availability.injuries"]["selected"]["value"] == []
    assert fields["lineup.confirmed"]["selected"]["value"] is False
    assert result["summary"]["mode"] == "SHADOW"
    assert result["summary"]["effective_data_path"] == "LEGACY_UNCHANGED"
    assert result["summary"]["external_requests"] == 0
    assert result["summary"]["deferred_categories"]["match_odds"] == 1


def test_missing_sources_remain_missing_and_one_bad_fixture_cannot_poison_another(tmp_path):
    value = report()
    value["sports_supplement"] = {"the_stats_api": run(tmp_path, Transport(), value)}
    other = {**value["fixtures"][0], "fixture_id": "api_football:2"}
    value["fixtures"].append(other)
    result = build_source_resolution(value, {}, as_of=NOW)
    secondary = [item for item in result["fields"] if item["fixture_id"] == other["fixture_id"]]
    assert secondary and all(item["resolution"]["status"] == "MISSING" for item in secondary)
    assert result["summary"]["by_provider"]["the_stats_api"] > 0


def test_quote_receipt_cannot_replace_timestamp_and_unscoped_product_is_not_coverage():
    value = report()
    candidate = {"fixture_id": "api_football:1", "quote_provider": "the_odds_api",
        "bookmaker": "test", "market_key": "h2h", "selection_key": "home", "period": "FULL_TIME",
        "settlement": "REGULATION_TIME", "region": "eu", "currency": "EUR", "quote_available": True,
        "decimal_price": 2.1, "quoted_at": None, "quote_source_id": "https://api.the-odds-api.com/v4/sports/soccer_epl/odds"}
    artifacts = {"candidate_input": {"observed_at": NOW.isoformat(), "candidates": [candidate]}}
    result = build_source_resolution(value, artifacts, as_of=NOW)
    assert result["summary"]["rejected_observations"]["MISSING_QUOTE_TIME"] == 1
    candidate["quoted_at"] = NOW.isoformat()
    result = build_source_resolution(value, artifacts, as_of=NOW)
    assert result["summary"]["by_provider"]["the_odds_api"] == 1
    candidate["bookmaker"] = None
    result = build_source_resolution(value, artifacts, as_of=NOW)
    assert result["summary"]["rejected_inputs"]["INVALID_QUOTE_PRODUCT"] == 1


def test_one_malformed_quote_or_news_row_does_not_discard_valid_observations():
    value = report()
    value["grok_review"] = {"claims": [None]}
    candidate = {"fixture_id": "api_football:1", "quote_provider": "the_odds_api",
        "bookmaker": "test", "market_key": "h2h", "selection_key": "home", "period": "FULL_TIME",
        "settlement": "REGULATION_TIME", "region": "eu", "currency": "EUR", "quote_available": True,
        "decimal_price": 2.1, "quoted_at": NOW.isoformat(),
        "quote_source_id": "https://api.the-odds-api.com/v4/sports/soccer_epl/odds"}
    artifacts = {"candidate_input": {"observed_at": NOW.isoformat(),
        "candidates": [None, {**candidate, "decimal_price": float("nan")}, candidate]}}
    result = build_source_resolution(value, artifacts, as_of=NOW)
    assert result["summary"]["by_provider"]["the_odds_api"] == 1
    assert result["summary"]["rejected_inputs"]["INVALID_QUOTE_OBSERVATION"] == 2
    assert result["summary"]["rejected_inputs"]["INVALID_NEWS_PROVENANCE"] == 1
