"""Real catalogue acceptance: fixed shapes survive ranking and alternate allocation."""

import math
from copy import deepcopy

import pytest
from test_tipster_personas import NOW, enriched, model_results

from goal_analysis.engine.diversification import _against, _eligible
from goal_analysis.tipsters.catalog import TIPSTER_CATALOG
from goal_analysis.tipsters.contracts import valid_construction
from goal_analysis.tipsters.recommendations import build_tipster_recommendations


@pytest.fixture(scope="module")
def generated():
    options = {}
    cards = build_tipster_recommendations(
        enriched(), model_results(),
        {"enabled_profiles": [p["profile_id"] for p in TIPSTER_CATALOG]}, NOW,
        diversity_options=options,
    )
    return cards, options


def test_all_primary_and_alternate_club_tickets_keep_real_fixed_contracts(generated):
    cards, options = generated
    ready = [card for card in cards if card["legs"]]
    assert len(ready) >= 6  # A real, varied package, not a vacuous abstention.
    for card in ready:
        assert valid_construction(card, NOW)
        assert card["combined_price"] >= 6
        assert len(card["legs"]) >= 2
        assert all(leg["estimated_edge"] >= card["persona"]["selection_policy"]["min_edge"] - 1e-12
                   for leg in card["legs"])
    assert any(len(card["legs"]) >= 4 for card in ready)
    for pool in options.values():
        for option in pool:
            assert valid_construction(option["recommendation"], NOW)
            assert _eligible(option, NOW, "2026-10-11")


@pytest.mark.parametrize("change", ["shorten", "below_floor", "above_ceiling", "mixed_book",
                                    "duplicate_fixture", "false_product", "stale_version", "forged_waiver",
                                    "missing_contract"])
def test_allocator_rejects_alternatives_that_evade_the_live_profile_contract(generated, change):
    _, options = generated
    option = deepcopy(options["tipster_archiv_nora"][0])
    card = option["recommendation"]
    if change == "shorten":
        card["legs"] = card["legs"][:1]
    elif change == "below_floor":
        for leg in card["legs"]:
            leg["decimal_price"] = 1.1
        card["persona"]["target_odds_band"]["min"] = 1  # Forged relaxed snapshot has no authority.
    elif change == "above_ceiling":
        for leg in card["legs"]:
            leg["decimal_price"] = 10
    elif change == "mixed_book":
        card["legs"][0]["bookmaker"] = "another"
    elif change == "duplicate_fixture":
        card["legs"][0]["fixture_id"] = card["legs"][1]["fixture_id"]
    elif change == "stale_version":
        card["catalog_version"] = "old-single-policy"
    elif change == "missing_contract":
        card.pop("catalog_version")
        card.pop("persona")
    elif change == "forged_waiver":
        card["legs"] = card["legs"][:1]
        card["legs"][0].update(decimal_price=30, veto_override={"gates": ["HIGH_ODDS"]})
    card["combined_price"] = math.prod(leg["decimal_price"] for leg in card["legs"])
    if change == "false_product":
        card["combined_price"] += 2
    assert not valid_construction(card, NOW)
    assert not _eligible(option, NOW, "2026-10-11")


def test_over25_profile_stays_dedicated_and_short_profile_stays_short(generated):
    cards = {card["profile_id"]: card for card in generated[0]}
    over = cards["tipster_golvadasz_lili"]
    assert 4 <= len(over["legs"]) <= 6
    assert 10 <= over["combined_price"] <= 22
    assert all((leg["market_key"], leg["selection_key"]) == ("totals_2_5", "over")
               for leg in over["legs"])
    short = cards["tipster_rovid_lotti"]
    if short["legs"]:
        assert 2 <= len(short["legs"]) <= 3
        assert 10 <= short["combined_price"] <= 14
    else:
        assert short["status"] == "ABSTAINED"
        assert short["reason_code"] == "NO_COMPLIANT_COMBINATION"


def test_club_contract_cannot_change_daily_or_legacy_policy():
    daily = {"profile_id": "daily223", "legs": [{"decimal_price": 1.1}], "combined_price": 1.1}
    before = deepcopy(daily)
    assert valid_construction(daily, NOW)
    assert daily == before


def test_single_repeated_fixture_gets_soft_club_penalty_without_banning_overlap(generated):
    option = deepcopy(generated[1]["tipster_archiv_nora"][0])
    base = option["recommendation"]
    shared = base["legs"][0]
    anchors = []
    for n in range(5):
        anchors.append({"profile_id": f"other_{n}", "legs": [deepcopy(shared),
            {**deepcopy(shared), "fixture_id": f"distinct_{n}"}]})
    adjusted = _against(option, anchors)
    assert adjusted is not None
    assert adjusted["similarity"]["penalty_factor"] == 1  # Only one shared match.
    assert adjusted["similarity"]["club_max_prior_fixture_uses"] == 5
    assert 0 < adjusted["adjusted_quality"] < option["quality"]
    # Duplicate aliases of the same ticket do not multiply concentration.
    assert _against(option, anchors + anchors)["adjusted_quality"] == adjusted["adjusted_quality"]
    legacy = deepcopy(option)
    legacy["recommendation"]["profile_id"] = "kronikas"
    assert _against(legacy, anchors)["adjusted_quality"] == legacy["quality"]
