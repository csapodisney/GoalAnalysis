"""Pure current core selectors: complete input receipts, own markets, protected DAILY."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.engine.daily_223 import WEIGHTS
from goal_analysis.engine.portfolio import build_portfolio
from goal_analysis.engine.specialist_contracts import (
    SPECIALIST_CONTRACTS,
    valid_specialist_construction,
)

NOW = datetime(2026, 10, 28, 10, tzinfo=UTC)
CORE = ("daily223", "kronikas", "ritmusor", "parharcmester", "orszem", "merlin", "h1_over05")
PRODUCTS = (("totals_2_5", "over", "FULL_TIME"), ("btts", "yes", "FULL_TIME"),
            ("h2h", "home", "FULL_TIME"), ("totals_2_5", "under", "FULL_TIME"),
            ("h2h", "draw", "FULL_TIME"), ("totals_0_5", "over", "FIRST_HALF"))


def event(index):
    return {"fixture_id": f"TESTONLY:{index}", "kickoff": (NOW + timedelta(hours=6)).isoformat(),
            "status": "ROUTED", "competition_name": "TESTONLY League",
            "source_id": "TESTONLY-calendar", "provider_league_id": "TESTONLY-league"}


def quote(index, product=PRODUCTS[0], price=2.0, *, evidence=False):
    item = {**event(index), "candidate_id": f"TESTONLY-{index}-{'-'.join(product)}",
            "home_team": f"TESTONLY Home {index}", "away_team": f"TESTONLY Away {index}",
            "competition": "TESTONLY League", "competition_type": "LEAGUE",
            "event_status": "SCHEDULED", "market_key": product[0], "selection_key": product[1],
            "period": product[2], "settlement": "REGULATION_90_PLUS_STOPPAGE",
            "bookmaker": "TESTONLY-book", "region": "eu", "currency": "EUR",
            "quote_source_id": "TESTONLY-quote-source", "quote_available": True,
            "decimal_price": price, "quoted_at": NOW.isoformat(),
            "sensitivity_note": "TESTONLY source snapshot; optional background may be unknown.",
            "evidence": []}
    if evidence:
        item["evidence"] = [{"evidence_id": f"{item['candidate_id']}-{category}",
                             "category": category, "statement": f"TESTONLY sourced {category}",
                             "claim_type": "FACT", "source_id": "TESTONLY-history",
                             "observed_at": NOW.isoformat(), "strength": .8, "reliability": 1.0}
                            for category in WEIGHTS]
    return item


def payload(rows=None, *, universe=None, half_history=True):
    rows = [quote(index, product) for index in range(4) for product in PRODUCTS] if rows is None else rows
    universe = [event(index) for index in range(45)] if universe is None else universe
    analyses = [{"candidate_id": row["candidate_id"], "status": "CALCULATED", "period": "FIRST_HALF",
                 "profiles": {side: {window: {"sample_size": 3} for window in ("historical_20", "venue_10")}
                              for side in ("home", "away")}}
                for row in rows if row["period"] == "FIRST_HALF"] if half_history else []
    return {"date": NOW.date().isoformat(), "observed_at": NOW.isoformat(), "candidates": rows,
            "history_analysis": {"candidates": analyses}, "calendar_universe": universe,
            "calendar_scope_receipt": {"scope": "ACCESSIBLE_PROVIDER_FOOTBALL_DAY", "status": "COMPLETE",
                                       "provider_calendar_status": "COMPLETE", "full_product_run": True,
                                       "provider_fixture_ids": [item["fixture_id"] for item in universe]}}


def build(source, profiles=CORE, *, version=2, strictness=100, review=None):
    settings = {"enabled_profiles": list(profiles), "strictness": strictness}
    if version is not None:
        settings["operating_contract_version"] = version
    return build_portfolio(source, settings, astra_review=review)


def proposal(report, profile):
    return next(card for card in report["profile_proposals"] if card["profile_id"] == profile)


def profile(report, name):
    return next(row for row in report["profiles"] if row["profile_id"] == name)


def test_six_own_markets_receive_all45_and_issue_drafts_without_shared_history_gate():
    source = payload()
    source["calendar_universe"][-1].update(status="UNMAPPED_COMPETITION", competition_name="TESTONLY unmapped cup")
    source["calendar_scope_receipt"].update(status="PARTIAL", full_product_run=False)
    before = deepcopy(source)
    current = build(source)
    assert {card["profile_id"] for card in current["profile_proposals"]} == set(CORE) - {"daily223"}
    assert not build(source, version=1)["profile_proposals"]
    assert current["construction_status"] == "PARTIAL"
    assert current["policy"]["data_veto_enabled"] is False
    assert current["policy"]["fallback_enabled"] is False
    expected_ids = sorted(item["fixture_id"] for item in source["calendar_universe"])
    for name in CORE:
        trace = profile(current, name)["selection_diagnostics"]
        assert trace["input_fixture_ids"] == expected_ids and trace["input_fixture_count"] == 45
        assert not trace["all_daily_fixtures_considered"]
        missing = next(row for row in trace["fixture_decisions"] if row["fixture_id"] == "TESTONLY:44")
        assert {"UNMAPPED_COMPETITION", "NO_VALID_QUOTES"} <= set(missing["reasons"])
    for name, product in zip(CORE[1:], PRODUCTS, strict=True):
        card = proposal(current, name)
        assert card["status"] == "DRAFT" and 15 <= card["combined_price"] <= 40
        assert card["reasoning"] and card["minimum_legs"] == (3 if name == "kronikas" else 2)
        assert {(leg["market_key"], leg["selection_key"], leg["period"]) for leg in card["legs"]} == {product}
        assert all(leg["weighted_support_score"] == 0 for leg in card["legs"])
        assert all("model_probability" not in leg for leg in card["legs"])
        assert card["selection_diagnostics"]["input_fixture_ids"] == expected_ids
    assert source == before


def test_daily_original_role_solver_and_legs_have_legacy_parity():
    rows = [quote(index, price=price, evidence=True) for index, price in enumerate((2, 2, 3))]
    source = payload(rows, universe=[event(index) for index in range(3)])
    current, legacy = build(source, ("daily223",), strictness=35), build(source, ("daily223",), version=1, strictness=35)
    new, old = proposal(current, "daily223"), proposal(legacy, "daily223")
    assert new["legs"] == old["legs"]
    assert new["combined_price"] == old["combined_price"] == 12
    assert new["odds_band"] == old["odds_band"] == "STRICT"
    assert new["selection_diagnostics"]["search_method"] == "PROTECTED_DAILY223_ORIGINAL_ROLE_SOLVER"
    assert "selection_diagnostics" not in old
    assert build(source, version=1, strictness=35) == build(source, version=None, strictness=35)


@pytest.mark.parametrize("prices", [(2, 2, 3.75), (2, 4, 5)])
def test_kronikas_current15_and40_boundaries_override_metadata_without_global_mutation(prices):
    before = deepcopy(SPECIALIST_CONTRACTS)
    rows = [quote(index, price=price) for index, price in enumerate(prices)]
    result = build(payload(rows, universe=[event(index) for index in range(3)]), ("kronikas",))
    card = proposal(result, "kronikas")
    assert card["combined_price"] in {15, 40} and len(card["legs"]) == 3
    assert card["construction_contract"]["min_price"] == card["minimum_combined_price"] == 15
    assert card["construction_contract"]["max_price"] == card["maximum_combined_price"] == 40
    assert valid_specialist_construction(card)
    assert SPECIALIST_CONTRACTS == before and SPECIALIST_CONTRACTS["kronikas"]["min_price"] == 10


def test_historical12_kronikas_is_not_reissued_as_current_primary():
    rows = [quote(index, price=price, evidence=True) for index, price in enumerate((2, 2, 3))]
    source = payload(rows, universe=[event(index) for index in range(3)])
    assert proposal(build(source, ("kronikas",), version=1, strictness=35), "kronikas")["combined_price"] == 12
    assert "kronikas" not in {card["profile_id"] for card in build(source, ("kronikas",))["profile_proposals"]}


@pytest.mark.parametrize("restriction", ["PARTIAL", "MANUAL_RESEARCH", "ID_MISMATCH", "NO_UNIVERSE", "NO_PAGE_PROOF"])
def test_cautious_fallback_requires_exact_complete_full_day_receipt(restriction):
    rows = [quote(index, PRODUCTS[3], 2.5) for index in range(2)]
    source = payload(rows, universe=[event(index) for index in range(2)])
    if restriction == "NO_UNIVERSE":
        source.pop("calendar_universe")
    elif restriction == "ID_MISMATCH":
        source["calendar_scope_receipt"]["provider_fixture_ids"].append("TESTONLY:missing")
    elif restriction == "NO_PAGE_PROOF":
        source["calendar_scope_receipt"].pop("provider_calendar_status")
    else:
        source["calendar_scope_receipt"]["status"] = restriction
    result = build(source, ("orszem",))
    assert "orszem" not in {card["profile_id"] for card in result["profile_proposals"]}
    assert profile(result, "orszem")["selection_diagnostics"]["odds_exception"]["status"] == "NOT_AUTHORIZED"


def test_only_orszem_gets_proven_cautious_fallback_and_primary_always_wins():
    rows = [quote(index, PRODUCTS[3], 2.5) for index in range(2)]
    source = payload(rows, universe=[event(index) for index in range(2)])
    source["scope_receipt"] = source.pop("calendar_scope_receipt")
    card = proposal(build(source, ("orszem",)), "orszem")
    proof = card["selection_diagnostics"]["odds_exception"]
    assert card["combined_price"] == 6.25 and card["fallback"] and card["status"] == "DRAFT"
    assert proof["status"] == "APPLIED" and proof["full_day_coverage"] and proof["primary_absence_proven"]
    assert proof["primary_options_count"] == 0 and proof["reason"] == "NO_OWN_15_40_OPTION_AFTER_FULL_DAY_SEARCH"
    assert "CAUTIOUS_ODDS_EXCEPTION_AFTER_FULL_DAY_SEARCH" in card["quality_warnings"]
    rows = [quote(index, PRODUCTS[3]) for index in range(4)]
    primary = proposal(build(payload(rows, universe=[event(index) for index in range(4)]), ("orszem",)), "orszem")
    assert primary["combined_price"] == 16 and not primary["fallback"]
    assert primary["selection_diagnostics"]["odds_exception"]["status"] == "PRIMARY_RANGE"


def test_profile_local_h1_history_cannot_be_substituted_with_full_time_samples():
    source = payload(half_history=False)
    result = build(source, ("h1_over05", "ritmusor"))
    assert proposal(result, "ritmusor")["legs"]
    assert "h1_over05" not in {card["profile_id"] for card in result["profile_proposals"]}
    trace = profile(result, "h1_over05")["selection_diagnostics"]
    assert "FIRST_HALF_HISTORY_REQUIRED" in trace["candidate_rejections"].values()
    source["history_analysis"]["candidates"] = [{"candidate_id": row["candidate_id"], "status": "CALCULATED", "period": "FULL_TIME"}
                                                    for row in source["candidates"]]
    assert "h1_over05" not in {card["profile_id"] for card in build(source, ("h1_over05",))["profile_proposals"]}


def test_hard_quote_lockout_and_calendar_identity_errors_remain_explicit_local_rejections():
    rows = [quote(index, PRODUCTS[3]) for index in range(7)]
    universe = [event(index) for index in range(7)]
    rows[4]["quote_available"] = False
    rows[5]["kickoff"] = universe[5]["kickoff"] = (NOW + timedelta(minutes=15)).isoformat()
    rows[6]["kickoff"] = (NOW + timedelta(hours=7)).isoformat()
    result = build(payload(rows, universe=universe), ("orszem",))
    card = proposal(result, "orszem")
    rejected = card["selection_diagnostics"]["candidate_rejections"]
    assert rejected[rows[4]["candidate_id"]] == "QUOTE_UNAVAILABLE"
    assert rejected[rows[5]["candidate_id"]] == "PREMATCH_LOCKOUT"
    assert rejected[rows[6]["candidate_id"]] == "CALENDAR_KICKOFF_MISMATCH"
    assert {leg["fixture_id"] for leg in card["legs"]} == {f"TESTONLY:{index}" for index in range(4)}


def test_optional_review_does_not_create_ready_or_available_pseudoprofile():
    source = payload()
    reviewed = {"status": "COMPLETE", "reviews": [{"candidate_id": row["candidate_id"], "assessment": "support"}
                                                   for row in source["candidates"]]}
    result = build(source, review=reviewed)
    assert all(card["status"] == "DRAFT" for card in result["profile_proposals"])
    assert all(card["quality_warnings"] for card in result["profile_proposals"])
    one = payload([quote(0, PRODUCTS[4], 1.1)], universe=[event(0)])
    assert not build(one, ("merlin",), strictness=0)["profile_proposals"]
    assert "available" not in {row["profile_id"] for row in build(one, ("merlin",), strictness=0)["profiles"]}
