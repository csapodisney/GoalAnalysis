"""Transient alternatives preserve source gates and exact default contracts."""

import json
import math
from copy import deepcopy

import pytest
from test_model_recommendations import NOW as MODEL_NOW
from test_model_recommendations import fixture as model_fixture
from test_model_recommendations import prediction
from test_model_recommendations import quote as model_quote
from test_tipster_personas import NOW, enriched, model_results, quote
from test_tipster_personas import (
    isolated_single_persona as isolated_single_persona,  # noqa: PLC0414
)

from goal_analysis.engine.model_recommendations import build_model_recommendations
from goal_analysis.tipsters.recommendations import build_tipster_recommendations


def club_data(count=2):
    data = enriched(count=count, keys=["home"])
    for candidate in data["candidates"]:
        candidate["decimal_price"] = 2.2
        complete_history(candidate)
    return data


def complete_history(candidate):
    candidate.update(competition_type="LEAGUE", settlement="REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES",
                     sensitivity_note="Synthetic history evidence")
    for evidence in candidate["evidence"]:
        evidence.update(statement="Synthetic history observation", claim_type="FACT")
    return candidate


def club(data, models=None, profiles=None, strictness=35, sink=None):
    fixture_count = len(data.get("fixtures", []))
    return build_tipster_recommendations(
        data, model_results(fixture_count) if models is None else models,
        {"enabled_profiles": profiles or ["tipster_archiv_nora"], "strictness": strictness},
        NOW, diversity_options=sink)


def numeric(profile="kepler", *, quotes=None, predictions=None, fixtures=None, sink=None, **result):
    return build_model_recommendations(
        {profile: {"profile_id": profile, "status": "READY", "predictions": predictions or [prediction(1), prediction(2)],
                   **result}}, fixtures or [model_fixture(1), model_fixture(2)], quotes or [],
        {"enabled_profiles": [profile]}, MODEL_NOW, diversity_options=sink)


def test_club_sink_does_not_change_default_or_embed_options_and_is_detached():
    data, sink = club_data(4), {}
    original = deepcopy(data)
    expected = club(data)
    assert club(data, sink=sink) == expected
    options = sink["tipster_archiv_nora"]
    assert len(options) == 4 and all(set(option) == {"quality", "scoring", "recommendation"} for option in options)
    for option in options:
        card = option["recommendation"]
        assert option["quality"] == option["scoring"]["ticket_score"]
        assert card["status"] == "READY" and card["reason_code"] is None
        assert card["selection_diagnostics"]["selected_fixture_ids"] == sorted(l["fixture_id"] for l in card["legs"])
        assert card["combined_price"] == math.prod(l["decimal_price"] for l in card["legs"])
        assert len(card["legs"]) == 3
        assert card["persona"]["target_odds_band"]["min"] <= card["combined_price"] <= card["persona"]["target_odds_band"]["max"]
        assert card["selection_diagnostics"]["history_only_weighted_support"] is None
        assert all("modellvalószínűségük ismeretlen" not in warning for warning in card["quality_warnings"])
    assert len({option["recommendation"]["recommendation_id"] for option in options}) == 4
    options[0]["recommendation"]["legs"][0]["decimal_price"] = 999
    assert data == original and club(data) == expected
    assert "diversity_options" not in json.dumps(expected)


def test_model_value_gate_runs_before_fixture_consolidation(isolated_single_persona):
    isolated_single_persona("tipster_archiv_nora")
    data = club_data(count=1)
    weak = data["candidates"][0]
    weak["decimal_price"] = 1.5
    alternative = complete_history(quote(1, "draw", 4.5, "other_book"))
    data["candidates"].append(alternative)
    sink = {}
    default = club(data, sink=sink)[0]
    assert default["legs"][0]["source_candidate_id"] == alternative["candidate_id"]
    option = sink["tipster_archiv_nora"][0]["recommendation"]
    assert option["legs"][0]["source_candidate_id"] == alternative["candidate_id"]


@pytest.mark.parametrize("change", ["missing_models", "negative_edge", "forged_model_cutoff"])
def test_options_require_current_models_and_positive_value(change, isolated_single_persona):
    isolated_single_persona("tipster_archiv_nora")
    data = club_data(count=1)
    candidate = data["candidates"][0]
    models = model_results(1)
    valid = {}
    club(data, models, sink=valid)
    assert valid["tipster_archiv_nora"]
    if change == "missing_models":
        models = {}
    elif change == "negative_edge":
        candidate["decimal_price"] = 1.01
    else:
        for result in models.values():
            result["predictions"][0]["model_details"]["cutoff"] = "2099-01-01T00:00:00+00:00"
    sink = {}
    club(data, models, sink=sink)
    assert sink["tipster_archiv_nora"] == []


def test_club_modeled_options_keep_model_gates_without_generic_history_override():
    data, sink = club_data(4), {}
    for candidate in data["candidates"]:
        candidate["evidence"] = []
    club(data, model_results(4), ["tipster_bastya_bence", "tipster_golterkep_emese"], sink=sink)
    assert len(sink["tipster_bastya_bence"]) == 4
    assert all(o["recommendation"]["selection_diagnostics"]["history_only_weighted_support"] is None
               for o in sink["tipster_bastya_bence"])
    missing = {}
    club(data, {}, ["tipster_bastya_bence", "tipster_golterkep_emese"], sink=missing)
    assert missing == {"tipster_bastya_bence": [], "tipster_golterkep_emese": []}


def test_club_compares_all_quote_sources_before_one_fixture_option(isolated_single_persona):
    isolated_single_persona("tipster_archiv_nora")
    data, sink = club_data(count=1), {}
    data["candidates"] = [complete_history(quote(1, "home", 2.1 + n / 100, f"book{n:02}"))
                          for n in range(12)]
    club(data, sink=sink)
    options = sink["tipster_archiv_nora"]
    assert len(options) == 1
    assert options[0]["recommendation"]["bookmaker"] == "book11"
    assert options[0]["recommendation"]["combined_price"] == pytest.approx(2.21)


def test_numeric_templates_have_exact_own_identity_quote_context_and_diagnostics():
    quotes = [model_quote(1, price=2), model_quote(2, price=2.1)]
    sink = {}
    expected = numeric(quotes=quotes)
    assert numeric(quotes=quotes, sink=sink) == expected
    options = sink["kepler"]
    assert len(options) == 3
    for option in options:
        card, leg = option["recommendation"], option["recommendation"]["legs"][0]
        assert card["status"] == "MODEL_READY" and not card["odds_pending"]
        assert card["combined_price"] == math.prod(l["decimal_price"] for l in card["legs"])
        assert card["selection_diagnostics"]["selected_min_estimated_edge"] == min(l["estimated_edge"] for l in card["legs"])
        assert option["quality"] == option["scoring"]["ticket_score"]
        assert all(0 <= value <= 1 for value in option["scoring"]["normalized_leg_scores"])
        assert card["selection_diagnostics"]["selected_min_model_probability"] == min(l["model_probability"] for l in card["legs"])
        assert leg["fixture_context"] == []
    assert len({o["recommendation"]["recommendation_id"] for o in options}) == 3
    assert "diversity_options" not in json.dumps(expected)


def test_numeric_priced_cohort_and_existing_edge_probability_stability_gates():
    sink = {}
    # Valid unpriced fixture 2 must not mix probability with priced edge quality.
    numeric(quotes=[model_quote(1, price=2)], sink=sink)
    assert [o["recommendation"]["legs"][0]["fixture_id"] for o in sink["kepler"]] == ["api_football:1"]
    blocked = {}
    all_quotes = [model_quote(n, price=1.01, market=market, selection=selection)
                  for n in (1, 2) for market, selection in (("h2h", "home"), ("h2h", "draw"),
                    ("h2h", "away"), ("totals_2_5", "over"), ("totals_2_5", "under"), ("btts", "yes"))]
    numeric(quotes=all_quotes, sink=blocked)
    assert blocked["kepler"] == []
    unstable = {}
    numeric("gauss", predictions=[prediction(1, stability={"home": 0.081})], sink=unstable)
    assert unstable["gauss"] == []


@pytest.mark.parametrize("status", ["MODEL_ERROR", "INSUFFICIENT_HISTORY", "NO_FIXTURES"])
def test_numeric_error_options_are_empty(status):
    sink = {}
    numeric(status=status, sink=sink)
    assert sink == {"kepler": []}


def test_numeric_unpriced_options_are_honest_and_best_quote_sources_not_truncated():
    pending = {}
    numeric(sink=pending)
    assert len(pending["kepler"]) == 3
    for option in pending["kepler"]:
        card = option["recommendation"]
        assert option["quality"] == pytest.approx(
            sum(l["model_probability"] for l in card["legs"]) / math.sqrt(len(card["legs"])))
        assert card["status"] == "ODDS_PENDING" and card["combined_price"] is None
        assert "nincs ellenőrzött" in card["quality_warnings"][-1]
    priced = {}
    numeric(quotes=[model_quote(1, f"book{n:02}", 2 + n / 100) for n in range(15)], sink=priced)
    assert len(priced["kepler"]) == 1
    assert priced["kepler"][0]["recommendation"]["bookmaker"] == "book14"
