"""Publication odds gate uses complete real allocator option templates."""

import math
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.config.portfolio import TIPSTER_PROFILE_IDS
from goal_analysis.engine.diversification import allocate
from goal_analysis.engine.publication_odds import (
    apply_publication_odds,
    publication_odds_issue,
)

NOW = datetime(2026, 10, 18, 10, tzinfo=UTC)
SETTINGS = {"target_min_odds": 10, "target_max_odds": 40,
            "enabled_profiles": ["daily223", "ritmusor", "gauss", "h1_over05"],
            "target_ticket_count": 2, "max_ticket_count": 5}


def card(profile, prices=(2, 2, 3), *, prefix="fixture", ticket=False):
    return {
        "profile_id": profile, "date": NOW.date().isoformat(), "status": "READY",
        "publication_scope": "LIVE", "combined_price": math.prod(prices),
        "quality_warnings": [], "selected_for_portfolio": True,
        "ticket_id" if ticket else "recommendation_id": f"{profile}-{prefix}",
        "legs": [{"fixture_id": f"{prefix}-{index}", "candidate_id": f"{profile}-{index}",
                  "kickoff": (NOW + timedelta(hours=3)).isoformat(),
                  "decimal_price": price, "market_key": "totals_2_5",
                  "selection_key": "over", "period": "FULL_TIME"}
                 for index, price in enumerate(prices)],
    }


def option(profile, prices=(2, 2, 3), *, prefix="fixture", quality=.8, main=True):
    result = {"quality": quality, "scoring": {"ticket_score": quality},
              "recommendation": card(profile, prices, prefix=prefix)}
    if main:
        result["ticket"] = card(profile, prices, prefix=prefix, ticket=True)
    return result


def report(options):
    daily = card("daily223", (1.11,), prefix="daily", ticket=True)
    daily["protected_sentinel"] = {"historical_object": True}
    cards = [deepcopy(rows[0]["recommendation"]) for rows in options.values() if rows]
    return {
        "date": NOW.date().isoformat(), "recommendations": [deepcopy(daily), *cards],
        "tickets": [deepcopy(daily)], "profile_proposals": [deepcopy(daily)],
        "preview_tickets": [deepcopy(daily)],
        "profiles": [{"profile_id": c["profile_id"]} for c in cards],
    }


def test_low_best_uses_qualified_next_alternative_before_allocation():
    options = {"ritmusor": [option("ritmusor", (1.5, 2), quality=.99, prefix="low"),
                            option("ritmusor", quality=.8, prefix="qualified")]}
    data = report(options)
    before = deepcopy((data, options))
    clean, candidates = apply_publication_odds(data, options, SETTINGS)
    assert clean["recommendations"][1]["status"] == "ABSTAINED"
    assert clean["recommendations"][1]["legs"] == []
    assert candidates["ritmusor"] == [options["ritmusor"][1]]
    allocate(clean, candidates, SETTINGS, NOW)
    selected = clean["recommendations"][1]
    assert selected["combined_price"] == 12
    assert all(leg["fixture_id"].startswith("qualified") for leg in selected["legs"])
    assert [ticket["combined_price"] for ticket in clean["tickets"]] == [1.11, 12]
    assert (data, options) == before


def test_h1_abstains_without_low_base_fallback_or_filler():
    options = {"h1_over05": [option("h1_over05", (1.5, 1.97))]}
    data = report(options)
    data["recommendations"][1]["probability"] = .72
    for group in ("tickets", "profile_proposals", "preview_tickets"):
        data[group].append(deepcopy(options["h1_over05"][0]["ticket"]))
    clean, candidates = apply_publication_odds(data, options, SETTINGS)
    assert candidates["h1_over05"] == []
    allocate(clean, candidates, SETTINGS, NOW)
    rejected = clean["recommendations"][1]
    assert rejected["status"] == "ABSTAINED"
    assert rejected["reason_code"] == "BELOW_PUBLICATION_ODDS_BAND"
    assert rejected["legs"] == [] and rejected["combined_price"] is None
    assert rejected["probability"] is None
    assert all(row["profile_id"] == "daily223" for group in
               ("tickets", "profile_proposals", "preview_tickets") for row in clean[group])
    detail = clean["publication_odds"]["profiles"]["h1_over05"]
    assert detail["rejected_options"] == {"BELOW_PUBLICATION_ODDS_BAND": 1}
    assert detail["removed_cards"] == {"recommendations": 1, "tickets": 1,
                                       "profile_proposals": 1, "preview_tickets": 1}


def test_all_daily_and_club_objects_preserved_exactly_even_outside_global_band():
    options = {"daily223": [option("daily223", (1.11,))]}
    options.update({profile: [option(profile, (2, 3), prefix=profile)]
                    for profile in TIPSTER_PROFILE_IDS})
    data = report(options)
    for group in ("tickets", "profile_proposals", "preview_tickets"):
        data[group].extend(deepcopy(rows[0]["ticket"]) for rows in options.values())
    clean, candidates = apply_publication_odds(data, options, SETTINGS)
    assert candidates == options
    assert all(clean[group] == data[group] for group in data)
    assert clean["publication_odds"]["profiles"] == {}
    assert publication_odds_issue(card("daily223", (1.11,)), SETTINGS) is None
    assert publication_odds_issue(card(TIPSTER_PROFILE_IDS[0], (2, 3)), SETTINGS) is None


@pytest.mark.parametrize(("prices", "issue"), [
    ((2, 5), None), ((2, 4, 5), None),
    ((2, 4.999), "BELOW_PUBLICATION_ODDS_BAND"),
    ((2, 4, 5.001), "ABOVE_PUBLICATION_ODDS_BAND"),
])
def test_actual_inclusive_product_bounds(prices, issue):
    assert publication_odds_issue(card("gauss", prices), SETTINGS) == issue


@pytest.mark.parametrize("bad", [None, True, 1, 0, -2, "2.5", math.nan, math.inf])
def test_invalid_leg_prices_are_not_publishable(bad):
    item = card("gauss")
    item["legs"][0]["decimal_price"] = bad
    assert publication_odds_issue(item, SETTINGS) == "INVALID_TICKET_ODDS"


def test_declared_total_cannot_disguise_low_odds_or_disagree_with_real_product():
    item = card("gauss", (1.5, 2))
    item["combined_price"] = 12
    assert publication_odds_issue(item, SETTINGS) == "TICKET_ODDS_MISMATCH"
    item = card("gauss", (2, 3, 2))
    item["combined_price"] += 1e-12
    assert publication_odds_issue(item, SETTINGS) is None


def test_both_ticket_and_recommendation_must_pass_and_profile_cannot_be_spoofed():
    valid = option("ritmusor")
    bad_ticket = deepcopy(valid)
    bad_ticket["ticket"] = card("ritmusor", (2, 2), ticket=True)
    wrong_profile = deepcopy(valid)
    wrong_profile["recommendation"]["profile_id"] = "daily223"
    options = {"ritmusor": [valid, bad_ticket, wrong_profile, {}]}
    clean, candidates = apply_publication_odds(report(options), options, SETTINGS)
    assert candidates["ritmusor"] == [valid]
    assert clean["publication_odds"]["profiles"]["ritmusor"]["rejected_options"] == {
        "BELOW_PUBLICATION_ODDS_BAND": 1, "OPTION_PROFILE_MISMATCH": 1,
        "INVALID_ODDS_OPTION": 1,
    }


def test_recommendation_only_numeric_options_keep_scores_and_individual_estimates():
    numeric = option("gauss", main=False)
    numeric["recommendation"]["status"] = "MODEL_READY"
    numeric["recommendation"]["legs"][0]["model_probability"] = .61
    options = {"gauss": [numeric, option("gauss", (2, 2), main=False)]}
    clean, candidates = apply_publication_odds(report(options), options, SETTINGS)
    assert candidates["gauss"] == [numeric]
    assert clean["recommendations"][1] == numeric["recommendation"]
    assert "publication_odds" not in candidates["gauss"][0]


def test_empty_failure_and_unknown_profiles_are_not_rewritten():
    failed = {"profile_id": "gauss", "status": "MODEL_ERROR", "legs": [], "reason": "history"}
    unknown = card("external_advisory", (1.2,))
    data = {"recommendations": [failed, unknown]}
    clean, candidates = apply_publication_odds(data, {}, SETTINGS)
    assert clean["recommendations"] == [failed, unknown]
    assert candidates == {}
    assert publication_odds_issue(failed, SETTINGS) is None


def test_default_and_custom_settings_are_explicit_without_weakening_quality():
    assert publication_odds_issue(card("gauss", (2, 3)), {}) == "BELOW_PUBLICATION_ODDS_BAND"
    assert publication_odds_issue(card("gauss", (2, 3)), {
        "target_min_odds": 6, "target_max_odds": 20}) is None
    assert publication_odds_issue(card("available", (1.5,)), {}) == "BELOW_PUBLICATION_ODDS_BAND"


@pytest.mark.parametrize("settings", [
    {"target_min_odds": True}, {"target_min_odds": math.nan},
    {"target_max_odds": math.inf}, {"target_min_odds": 41, "target_max_odds": 40},
    {"target_max_odds": 101}, {"target_min_odds": "10"},
])
def test_invalid_settings_fail_before_mutation(settings):
    data, options = report({}), {}
    before = deepcopy(data)
    with pytest.raises(ValueError, match="szorzósáv"):
        apply_publication_odds(data, options, settings)
    assert data == before
