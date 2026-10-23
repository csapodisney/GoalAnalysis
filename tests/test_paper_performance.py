"""Exact hypothetical unit accounting, never actual cash or reconstructed prices."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents import canonical_sha256
from goal_analysis.storage.paper_performance import build_paper_performance

NOW = datetime(2026, 10, 11, 10, tzinfo=UTC)


def record(identifier="one", profile="daily223", prices=(2.0, 3.0), outcomes=None, when=NOW, **updates):
    legs, settled = [], []
    for n, price in enumerate(prices, 1):
        leg = {"fixture_id": f"api_football:{n}", "api_football_fixture_id": str(n),
               "home_team": f"Home {n}", "away_team": f"Away {n}",
               "kickoff": (when + timedelta(hours=3)).isoformat(), "market_key": "h2h",
               "selection_key": "home", "period": "FULL_TIME", "decimal_price": price,
               "bookmaker": "book_api_football_8", "region": "eu", "currency": "EUR",
               "quote_provider": "api_football", "provider_market_id": 1,
               "quote_source_id": f"https://v3.football.api-sports.io/odds?fixture={n}&bookmaker=8&bet=1",
               "quoted_at": when.isoformat()}
        legs.append(leg)
        outcome = (outcomes or ["WON"] * len(prices))[n - 1]
        settled.append({**{key: leg[key] for key in ("fixture_id", "market_key", "selection_key", "period")},
                        "outcome": outcome,
                        "reason": "BOOKMAKER_VOID_CONFIRMED" if outcome == "VOID" else "PERIOD_SCORE_CONFIRMED",
                        "note": "Explicit void confirmation" if outcome == "VOID" else None})
    statuses = {leg["outcome"] for leg in settled}
    outcome = next((status for status in ("LOST", "REVIEW", "PENDING") if status in statuses),
                   "VOID" if statuses == {"VOID"} else "WON")
    return {"recommendation_id": identifier, "run_id": "run-" + identifier, "profile_id": profile,
            "profile_name": profile, "date": when.date().isoformat(), "created_at": when.isoformat(),
            "publication_scope": "PAPER_AUDITION", "legs": legs, "active": True,
            "outcome": outcome, "settlement": {"outcome": outcome, "legs": settled}, **updates}


def test_exact_won_lost_pending_review_and_cash_versus_realized_profit():
    rows = [record("won"), record("lost", "gauss", outcomes=["WON", "LOST"]),
            record("pending", "kepler", outcomes=["WON", "PENDING"]),
            record("review", "laplace", outcomes=["WON", "REVIEW"])]
    result = build_paper_performance(rows)
    summary = result["summary"]
    assert summary["records"] == summary["eligible_records"] == 4
    assert summary["total_stake_eur"] == 4
    assert summary["settled_stake_eur"] == 2
    assert summary["total_returned_eur"] == 6
    assert summary["realized_net_eur"] == 4
    assert summary["cash_balance_eur"] == 2
    assert summary["open_stake_eur"] == 2
    assert summary["roi_pct"] == 200
    for row in result["records"]:
        if row["outcome"] in {"PENDING", "REVIEW"}:
            assert row["realized_net_eur"] is None
            assert row["returned_eur"] == 0 and row["open_stake_eur"] == 1
            assert row["cash_balance_eur"] == -1
            assert not row["settled"]


def test_every_pending_is_open_not_a_losing_result():
    result = build_paper_performance([record(outcomes=["PENDING", "PENDING"])])
    assert result["summary"]["realized_net_eur"] == 0
    assert result["summary"]["cash_balance_eur"] == -1
    assert result["summary"]["settled_records"] == result["summary"]["lost"] == 0
    assert result["summary"]["roi_pct"] is None
    assert result["records"][0]["realized_net_eur"] is None


@pytest.mark.parametrize("outcomes,expected", [(["VOID", "VOID"], ("VOID", 1.0, 0.0)),
                                            (["WON", "VOID"], ("WON", 2.0, 1.0)),
                                            (["VOID", "WON"], ("WON", 3.0, 2.0)),
                                            (["LOST", "VOID"], ("LOST", 0.0, -1.0)),
                                            (["LOST", "PENDING"], ("LOST", 0.0, -1.0)),
                                            (["LOST", "REVIEW"], ("LOST", 0.0, -1.0))])
def test_unambiguous_void_refund_partial_void_and_decisive_losing_leg(outcomes, expected):
    row = build_paper_performance([record(outcomes=outcomes)])["records"][0]
    assert (row["outcome"], row["returned_eur"], row["realized_net_eur"]) == expected
    assert row["original_combined_price"] == 6 and row["stake_eur"] == 1


def test_unconfirmed_void_and_cancelled_fixture_remain_review():
    item = record(outcomes=["VOID", "WON"])
    item["settlement"]["legs"][0]["reason"] = "BOOKMAKER_RULE_REVIEW_REQUIRED"
    row = build_paper_performance([item])["records"][0]
    assert row["outcome"] == "REVIEW" and row["realized_net_eur"] is None
    assert row["open_stake_eur"] == 1


def test_legacy_all_void_top_won_is_resolved_from_confirmed_individual_legs():
    item = record(outcomes=["VOID", "VOID"])
    item["outcome"] = item["settlement"]["outcome"] = "WON"
    row = build_paper_performance([item])["records"][0]
    assert row["outcome"] == "VOID" and row["returned_eur"] == 1


@pytest.mark.parametrize("price", [None, 0, 1, -2, True, "2.5", float("nan"), float("inf"), 1001])
@pytest.mark.parametrize("outcome", ["WON", "LOST"])
def test_unknown_invalid_price_not_even_a_known_losing_minus_one(price, outcome):
    item = record(prices=(price, 3), outcomes=[outcome, "WON"])
    result = build_paper_performance([item])
    row, summary = result["records"][0], result["summary"]
    assert not row["paper_eligible"]
    assert all(row[key] is None for key in ("stake_eur", "returned_eur", "realized_net_eur", "cash_balance_eur"))
    assert summary["eligible_records"] == 0 and summary["excluded_records"] == 1
    assert summary["all_record_assumed_stake_eur"] == summary["excluded_assumed_stake_eur"] == 1
    assert summary["full_all_record_realized_net_eur"] is None
    assert summary["complete_result_available"] is False
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("field,value", [("quote_source_id", "https://untrusted.example/odds"),
                                        ("provider_market_id", 8), ("api_football_fixture_id", "999"),
                                        ("quote_provider", "legacy_unknown"), ("quote_available", False)])
def test_unverified_original_quote_is_visible_but_not_monetized(field, value):
    item = record()
    item["legs"][0][field] = value
    result = build_paper_performance([item])
    row = result["records"][0]
    assert "UNVERIFIED_QUOTE" in row["exclusion_reasons"]
    assert row["original_combined_price"] == 6
    assert row["returned_eur"] is None


def test_the_odds_api_original_candidate_crosswalk_required():
    item = record()
    for leg in item["legs"]:
        event = "event" + leg["api_football_fixture_id"]
        leg.update(quote_provider="the_odds_api", bookmaker="some_book", sport_key="soccer_test",
                   quote_source_id=f"https://api.the-odds-api.com/v4/sports/soccer_test/events/{event}/odds")
        leg["source_candidate_id"] = "d223-" + canonical_sha256({
            "fixture_id": leg["fixture_id"], "event_id": event, "market": leg["market_key"],
            "selection": leg["selection_key"], "period": leg["period"], "bookmaker": leg["bookmaker"]})[:24]
    assert build_paper_performance([item])["summary"]["eligible_records"] == 1
    item["legs"][0]["quote_source_id"] = item["legs"][0]["quote_source_id"].replace("event1", "event999")
    assert build_paper_performance([item])["summary"]["excluded_records"] == 1


@pytest.mark.parametrize("field", ["bookmaker", "region", "currency"])
def test_mixed_account_context_is_not_a_fictional_combo(field):
    item = record()
    item["legs"][0][field] = "other"
    assert "INCOMPATIBLE_BOOKMAKERS" in build_paper_performance([item])["records"][0]["exclusion_reasons"]


@pytest.mark.parametrize("timestamp", [None, "invalid", "2000-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00"])
def test_quote_timestamp_never_vetoes_old_missing_or_future_quotes(timestamp):
    item = record()
    for leg in item["legs"]:
        leg["quoted_at"] = timestamp
    result = build_paper_performance([item])
    assert result["summary"]["total_returned_eur"] == 6
    assert result["summary"]["excluded_records"] == 0


def test_decimal_product_and_half_up_cent_rounding_not_binary_float_math():
    rows = [record("one", prices=(2.005,)), record("two", "gauss", prices=(1.1, 1.1, 1.1))]
    result = build_paper_performance(rows)
    by_id = {row["recommendation_id"]: row for row in result["records"]}
    assert by_id["one"]["returned_eur"] == 2.01
    assert by_id["two"]["original_combined_price_decimal"] == "1.33100"
    assert by_id["two"]["returned_eur"] == 1.33
    assert result["summary"]["total_returned_eur"] == 3.34
    assert result["summary"]["realized_net_eur"] == 1.34


def test_actual_wager_financial_values_never_enter_hypothetical_calculation():
    original = record()
    actual = deepcopy(original)
    actual.update(played=True, stake_eur=500, financial={"net_eur": 9999},
                  wager={"stake_eur": 5, "actual_combined_price": 999})
    actual["settlement"]["bookmaker_return"] = {"payout_eur": 9999}
    first, second = build_paper_performance([original]), build_paper_performance([actual])
    assert second["summary"] == first["summary"]
    assert second["records"][0]["stake_eur"] == 1
    assert actual["wager"]["stake_eur"] == 5


def test_canonical_daily_profile_only_not_duplicate_copies_archived_versions_or_no_leg_cards():
    row = record()
    archive = record("old", active=False)
    empty = {"profile_id": "tipster_bastya_bence", "date": row["date"], "status": "ABSTAINED", "legs": []}
    result = build_paper_performance([row, deepcopy(row), archive, empty])
    assert result["summary"]["records"] == result["summary"]["total_stake_eur"] == 1
    assert result["coverage"]["archived_versions_ignored"] == 1
    assert result["coverage"]["duplicate_copies_ignored"] == 1
    assert result["coverage"]["no_ticket_records_ignored"] == 1
    ambiguous = build_paper_performance([row, record("another")])
    assert ambiguous["summary"]["records"] == 1
    assert ambiguous["summary"]["total_stake_eur"] == 0
    assert "AMBIGUOUS_PROFILE_DAY" in ambiguous["records"][0]["exclusion_reasons"]


def test_month_cumulative_starts_at_zero_overall_summary_is_lifetime():
    rows = [record("past", when=NOW - timedelta(days=30)), record(),
            record("next", when=NOW + timedelta(days=1), outcomes=["LOST", "WON"])]
    result = build_paper_performance(rows, "2026-10")
    assert result["summary"]["records"] == 2
    assert result["overall_summary"]["records"] == 3
    assert result["by_day"][0]["cumulative_net_eur"] == 5
    assert result["by_day"][1]["cumulative_net_eur"] == 4
    assert result["by_day"][1]["cumulative_cash_balance_eur"] == 4
    assert result["by_month"][0]["month"] == "2026-10"
    assert result["by_month"][0]["total_stake_eur"] == 2
    assert len(build_paper_performance(rows)["by_month"]) == 2
    assert build_paper_performance(rows, "2020-01")["summary"]["records"] == 0


@pytest.mark.parametrize("month", ["2026-1", "2026-13", "2026-10-01", "wrong", 123])
def test_invalid_month_rejected(month):
    with pytest.raises(ValueError, match="month"):
        build_paper_performance([], month)


def test_late_publication_duplicate_fixture_and_wrong_day_are_excluded():
    for kind in ("late", "duplicate", "wrong_day"):
        item = record()
        if kind == "late":
            item["created_at"] = item["legs"][0]["kickoff"]
        elif kind == "duplicate":
            item["legs"][1] = deepcopy(item["legs"][0])
        else:
            item["date"] = "2026-10-10"
        assert not build_paper_performance([item])["records"][0]["paper_eligible"]


def test_settlement_cannot_change_market_identity_or_claim_unmatched_win():
    item = record()
    item["settlement"]["legs"][0]["selection_key"] = "away"
    row = build_paper_performance([item])["records"][0]
    assert row["paper_eligible"] and row["outcome"] == "REVIEW"
    assert row["returned_eur"] == 0 and row["realized_net_eur"] is None
    assert row["settlement_issue"] == "SETTLEMENT_IDENTITY_MISMATCH"
    item = record(outcomes=["PENDING", "WON"])
    item["outcome"] = item["settlement"]["outcome"] = "WON"
    assert build_paper_performance([item])["records"][0]["outcome"] == "REVIEW"


def test_input_not_mutated_and_disabled_suspended_profiles_remain_in_history():
    rows = [record(publication_scope="SUSPENDED", team_status="SUSPENDED", enabled=False)]
    original = deepcopy(rows)
    result = build_paper_performance(rows)
    assert result["summary"]["records"] == 1 and result["summary"]["total_stake_eur"] == 1
    assert rows == original
    assert result["records"][0]["run_id"] == rows[0]["run_id"]
    assert result["records"][0]["legs"][0]["quote_source_id"] == rows[0]["legs"][0]["quote_source_id"]
    json.dumps(result, allow_nan=False)


def test_dropped_malformed_input_never_reports_a_complete_whole_ledger():
    result = build_paper_performance([None, record()])
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["invalid_input_records"] == 1
    for totals in (result["summary"], result["overall_summary"]):
        assert totals["complete_result_available"] is False
        assert totals["full_all_record_realized_net_eur"] is None
        assert totals["all_records_settled"] is False
    assert result["summary"]["realized_net_eur"] == 5  # Explicit verified subset remains useful.
