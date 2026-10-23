from copy import deepcopy

import pytest
from test_paper_performance import record

from goal_analysis.storage import paper_performance as paper
from goal_analysis.storage import performance_breakdowns as module
from goal_analysis.storage.portfolio_ledger import _summary


def row(identifier="one", outcome="WON", **updates):
    return {"ticket_id": identifier, "profile_id": "club", "combined_price": 12.0,
        "outcome": outcome, "legs": [{"fixture_id": "f", "period": "FULL_TIME", "market_key": "h2h",
            "source_provider": "the_stats_api", "provider_league_id": "co_one", "competition_type": "LEAGUE"}],
        "financial": {"stake_eur": 1., "payout_eur": 12. if outcome == "WON" else 0.,
            "net_eur": 11. if outcome == "WON" else -1. if outcome == "LOST" else 0.,
            "open_stake_eur": 0. if outcome in {"WON", "LOST", "VOID"} else 1.,
            "cash_flow_eur": 11. if outcome == "WON" else -1.,
            "settled": outcome in {"WON", "LOST", "VOID"}}, **updates}


def build(rows, mode="research"):
    return module.build_breakdowns(rows, _summary, mode=mode)


def groups(result, name):
    return {g["label"]: g for g in result["dimensions"][name]["rows"]}


@pytest.mark.parametrize("price,label", [(1.11, "(1,3)"), (2.99, "(1,3)"), (3, "[3,10)"),
    (9.99, "[3,10)"), (10, "[10,20)"), (19.99, "[10,20)"), (20, "[20,50]"),
    (50, "[20,50]"), (50.01, "(50,100]"), (100, "(50,100]"), (100.01, "(100,+inf)")])
def test_price_band_boundaries(price, label):
    assert module.odds_band(price) == label


@pytest.mark.parametrize("price", [None, True, "12", float("nan"), float("inf"), 0, 1, -1])
def test_missing_or_invalid_price_stays_unknown(price):
    assert module.odds_band(price) == "UNKNOWN"


def test_all_six_dimensions_are_pure_and_same_ticket_financial_policy_is_reused():
    rows = [row(), row("lost", "LOST")]
    before = deepcopy(rows)
    result = build(rows)
    assert rows == before and set(result["dimensions"]) == set(module.DIMENSIONS)
    assert result["decision_effect"] == "NONE"
    for dimension in module.DIMENSIONS:
        table = result["dimensions"][dimension]
        assert table["records"] == 2
        assert table["group_totals_additive"] is True
        assert table["rows"][0]["roi_pct"] == 500
        assert table["rows"][0]["decided_hit_rate_pct"] == 50


def test_accepted_actual_price_and_original_research_price_use_different_bands():
    item = row(wager={"actual_combined_price": 50.01})
    assert set(groups(build([item], "actual"), "odds_band")) == {"(50,100]"}
    assert set(groups(build([item]), "odds_band")) == {"[10,20)"}


def test_mixed_league_market_type_cohorts_are_explicitly_nonadditive():
    item = row()
    item["legs"].append({"fixture_id": "g", "period": "FIRST_HALF", "market_key": "totals_0_5",
        "source_provider": "the_stats_api", "provider_league_id": "co_two", "competition_type": "CUP"})
    result = build([item])
    for dimension in ("league", "market", "competition_type"):
        table = result["dimensions"][dimension]
        assert table["membership_count"] == 2 and table["groups_overlap"]
        assert table["group_totals_additive"] is False
        assert len(table["rows"]) == 2
        assert all(g["metrics"]["stake_eur"] == 1 and g["metrics"]["net_eur"] == 11 for g in table["rows"])
    assert result["attribution"] == "FULL_TICKET_CONDITIONAL_COHORT_RESULTS_NOT_LEG_PROFIT"
    assert result["cross_dimension_totals_additive"] is False


def test_repeated_league_and_market_on_same_ticket_count_once():
    item = row()
    item["legs"].append({**item["legs"][0], "fixture_id": "g"})
    result = build([item])
    assert result["dimensions"]["league"]["membership_count"] == 1
    assert result["dimensions"]["market"]["membership_count"] == 1
    assert groups(result, "leg_count")["2"]["records_in_group"] == 1


def test_unknown_leg_metadata_is_visible_beside_known_league_without_fuzzy_inference():
    item = row()
    item["legs"].append({"fixture_id": "g", "home_team": "Arsenal", "away_team": "Chelsea"})
    result = build([item])
    assert set(groups(result, "league")) == {"the_stats_api:competition:co_one", "UNKNOWN"}
    assert result["dimensions"]["league"]["unknown_records"] == 1
    assert groups(result, "market")["UNKNOWN"]["records_in_group"] == 1


def test_legacy_original_label_is_namespaced_not_joined_to_current_provider_id():
    item = row()
    item["legs"] = [{"competition": "GB1", "period": "FULL_TIME", "market_key": "h2h"}]
    result = build([item])
    assert set(groups(result, "league")) == {"recorded-label:GB1"}
    assert set(groups(result, "competition_type")) == {"UNKNOWN"}


@pytest.mark.parametrize("bad", [None, {}, [], "UNKNOWN", True])
def test_invalid_classification_is_unknown_not_a_guessed_league(bad):
    item = row()
    item["legs"][0].update(source_provider=bad, provider_league_id=bad, competition=bad)
    assert set(groups(build([item]), "league")) == {"UNKNOWN"}


def test_only_known_settled_won_lost_rows_enter_hit_rate():
    rows = [row("won"), row("lost", "LOST"), row("pending", "PENDING"), row("review", "REVIEW")]
    result = groups(build(rows), "profile")["club"]
    assert result["records_in_group"] == 4 and result["decided_hit_rate_count"] == 2
    assert result["decided_hit_rate_pct"] == 50
    assert result["metrics"]["settled_stake_eur"] == 2


def test_no_settled_rows_do_not_manufacture_zero_roi_or_zero_hit_rate():
    result = groups(build([row(outcome="PENDING")]), "profile")["club"]
    assert result["roi_pct"] is None and result["decided_hit_rate_pct"] is None


def test_paper_preserves_original_classification_price_and_partial_coverage():
    good, bad = record("good"), record("bad", "other")
    for original in (good, bad):
        for leg in original["legs"]:
            leg.update(competition="GB1", competition_type="LEAGUE")
    bad["legs"][0]["decimal_price"] = None
    result = paper.build_paper_performance([good, bad])["breakdowns"]
    league = groups(result, "league")["recorded-label:GB1"]
    assert league["records_in_group"] == 2 and league["roi_pct"] is None
    assert league["priced_subset_roi_pct"] == 500
    assert league["complete_financial_coverage"] is False
    assert league["metrics"]["excluded_records"] == 1
    assert groups(result, "odds_band")["[3,10)"]["records_in_group"] == 1
    assert groups(result, "odds_band")["UNKNOWN"]["records_in_group"] == 1


def test_paper_actual_cash_fields_cannot_change_bands_or_roi():
    original = record()
    first = paper.build_paper_performance([original])["breakdowns"]
    original.update(wager={"actual_combined_price": 999, "stake_eur": 999}, financial={"net_eur": 999})
    assert paper.build_paper_performance([original])["breakdowns"] == first


@pytest.mark.parametrize("legs", [None, "invalid", [None]])
def test_optional_bad_metadata_does_not_break_existing_financial_summary(legs):
    result = build([row(legs=legs)])
    assert set(groups(result, "league")) == {"UNKNOWN"}
    assert set(groups(result, "leg_count")) == {"UNKNOWN"}


def test_optional_breakdown_failure_returns_unavailable_not_wrong_totals(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("broken optional grouping")
    monkeypatch.setattr(module, "build_breakdowns", fail)
    assert module.safe_breakdowns([row()], _summary, mode="research")["status"] == "UNAVAILABLE"


def test_empty_tables_and_invalid_mode_are_explicit():
    result = build([])
    assert all(not t["rows"] and t["records"] == 0 for t in result["dimensions"].values())
    with pytest.raises(ValueError):
        module.build_breakdowns([], _summary, mode="all")
