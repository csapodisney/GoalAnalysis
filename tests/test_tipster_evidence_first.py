"""No odds/length padding; retain every persona's existing evidence boundary."""

from copy import deepcopy

from test_tipster_personas import build, enriched, model_results, quote
from test_tipster_personas import (
    isolated_single_persona as isolated_single_persona,  # noqa: PLC0414
)

from goal_analysis.tipsters.catalog import TIPSTER_BY_ID
from goal_analysis.tipsters.recommendations import _ticket_rank


def test_one_supported_pick_abstains_instead_of_padding_or_shortening_fixed_accumulator():
    data = enriched(keys=["home"])
    for candidate in data["candidates"]:
        candidate["decimal_price"] = 2.2 if candidate["fixture_id"] == "api_football:1" else 1.2
        for evidence in candidate["evidence"]:
            evidence["strength"] = 0.9 if candidate["fixture_id"] == "api_football:1" else 0.05
    card = build(data, model_results(), ["tipster_archiv_nora"])[0]
    assert card["status"] == "ABSTAINED" and card["legs"] == []
    assert card["reason_code"] == "NO_COMPLIANT_COMBINATION"
    assert card["selection_diagnostics"]["target_odds_minimum_enforced"] is True


def test_negative_value_cannot_override_a_positive_value_alternative(isolated_single_persona):
    isolated_single_persona("tipster_rovid_lotti")
    data = enriched(count=2, keys=["home"])
    for candidate in data["candidates"]:
        first = candidate["fixture_id"] == "api_football:1"
        candidate["decimal_price"] = 1.2 if first else 3.2
        candidate["region"] = "eu" if first else "us"
        for evidence in candidate["evidence"]:
            evidence["strength"] = 0.66 if first else 0.65
    card = build(data, model_results(2), ["tipster_rovid_lotti"])[0]
    assert [leg["fixture_id"] for leg in card["legs"]] == ["api_football:2"]
    assert card["combined_price"] == 3.2
    assert "price_preference" not in card["legs"][0]["model_details"]["selection_score_terms"]


def test_equal_exposure_uses_better_verified_quote_before_book_preference(isolated_single_persona):
    isolated_single_persona("tipster_archiv_nora")
    data = enriched(count=1, keys=["home"])
    data["preferred_bookmakers"] = ["preferred"]
    data["candidates"] = [quote(1, "home", 2.2, "preferred"), quote(1, "home", 2.5, "other")]
    card = build(data, model_results(1), ["tipster_archiv_nora"])[0]
    assert card["combined_price"] == 2.5 and card["bookmaker"] == "other"
    data["candidates"][0]["decimal_price"] = 2.5
    card = build(data, model_results(1), ["tipster_archiv_nora"])[0]
    assert card["bookmaker"] == "preferred"


def test_best_quote_cannot_be_lost_to_old_eight_bookmaker_cap(isolated_single_persona):
    isolated_single_persona("tipster_archiv_nora")
    data = enriched(count=1, keys=["home"])
    data["candidates"] = [quote(1, "home", 2.1 + n / 100, f"book{n:02}") for n in range(12)]
    card = build(data, model_results(1), ["tipster_archiv_nora"])[0]
    assert card["bookmaker"] == "book11" and len(card["legs"]) == 1
    assert card["model_summary"]["bookmaker_pools_searched"] == 12
    assert card["model_summary"]["combinations_examined"] == 12
    assert card["model_summary"]["bounded_search"] is False


def test_known_value_gate_and_named_model_requirements_still_block_bad_singles():
    data = enriched(count=1, keys=["home"])
    data["candidates"][0]["decimal_price"] = 1.01
    value = build(data, enabled=["tipster_ertek_milan"])[0]
    assert not value["legs"] and value["reason_code"] == "INSUFFICIENT_EVIDENCE"
    for profile in ("tipster_vandor_zora", "tipster_forma_soma", "tipster_golterkep_emese"):
        card = build(data, {}, [profile])[0]
        assert not card["legs"] and card["reason_code"] == "INSUFFICIENT_EVIDENCE"


def test_upper_price_caps_remain_effective_for_an_isolated_candidate_gate(isolated_single_persona):
    isolated_single_persona("tipster_archiv_nora", "tipster_meresz_hunor")
    data = enriched(count=1, keys=["home"])
    data["candidates"][0]["decimal_price"] = 100
    original = deepcopy(data)
    for profile in ("tipster_archiv_nora", "tipster_meresz_hunor"):
        card = build(data, model_results(1), [profile])[0]
        assert not card["legs"]
    assert data == original
    assert TIPSTER_BY_ID["tipster_meresz_hunor"]["target_odds_band"]["max"] == 50


def test_strong_combination_beats_single_but_very_weak_padding_does_not():
    rows = [{"score": score, "key": "home", "quote": quote(n + 1, "home", 1.2),
             "fixture": {"competition": f"league_{n}"}}
            for n, score in enumerate((0.8, 0.79, 0.78, 0.76, 0.05))]
    persona = TIPSTER_BY_ID["tipster_fuzo_oszkar"]
    assert _ticket_rank(rows[:4], persona) < _ticket_rank(rows[:1], persona)
    assert _ticket_rank(rows[:4], persona) < _ticket_rank(rows, persona)
