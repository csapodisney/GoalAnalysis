"""Full-ticket alternatives, unit-scale support and strict contrarian evidence."""

import math
from copy import deepcopy
from datetime import timedelta

import pytest
from test_diversity_options import club_data
from test_model_recommendations import (
    NOW as MODEL_NOW,
)
from test_model_recommendations import (
    fixture as model_fixture,
)
from test_model_recommendations import (
    prediction,
)
from test_model_recommendations import (
    quote as model_quote,
)
from test_tipster_personas import NOW, enriched, model_results
from test_tipster_personas import (
    isolated_single_persona as isolated_single_persona,  # noqa: PLC0414
)

from goal_analysis.engine.model_recommendations import build_model_recommendations
from goal_analysis.engine.ticket_options import bounded_ticket_groups
from goal_analysis.engine.veto_overrides import veto_candidate_signature
from goal_analysis.tipsters.recommendations import (
    build_tipster_recommendations,
    club_veto_candidate_context,
    contrarian_candidate_context,
)


def numeric(quotes, predictions=None, fixtures=None, **settings):
    sink = {}
    cards = build_model_recommendations(
        {"kepler": {"status": "READY", "predictions": predictions or [prediction(1), prediction(2), prediction(3)]}},
        fixtures or [model_fixture(1), model_fixture(2), model_fixture(3)], quotes,
        {"enabled_profiles": ["kepler"], **settings}, MODEL_NOW, diversity_options=sink)
    return cards[0], sink["kepler"]


def test_numeric_full_options_keep_singles_and_strong_combos_and_raw_probabilities():
    quotes = [model_quote(n, price=2) for n in (1, 2, 3)]
    cards, options = numeric(quotes)
    assert {len(option["recommendation"]["legs"]) for option in options} == {1, 2, 3}
    assert len(cards["legs"]) == 3
    assert all(leg["model_probability"] == 0.6 for leg in cards["legs"])
    assert all(leg["estimated_edge"] == pytest.approx(0.2) for leg in cards["legs"])
    assert cards["probability"] is None
    assert cards["selection_diagnostics"]["scoring"]["calibrated_probability"] is False
    assert numeric(list(reversed(quotes)))[0] == cards


def test_numeric_options_never_mix_quotes_or_repeat_fixture_or_exceed_upper_price():
    quotes = [model_quote(1, price=2), model_quote(1, price=2.5, market="h2h", selection="draw"),
              model_quote(2, price=2, region="us"), model_quote(3, price=2, currency="GBP")]
    _, options = numeric(quotes, target_max_odds=3)
    assert options
    for option in options:
        legs = option["recommendation"]["legs"]
        assert len({leg["fixture_id"] for leg in legs}) == len(legs)
        assert len({(leg["bookmaker"], leg.get("region"), leg.get("currency")) for leg in legs}) == 1
        assert math.prod(leg["decimal_price"] for leg in legs) <= 3


def test_quality_bonus_cannot_bypass_existing_numeric_or_club_value_gates():
    _, options = numeric([model_quote(1, price=2), model_quote(2, price=1.01), model_quote(3, price=99)],
                         predictions=[prediction(1), prediction(2), prediction(3, home=0.39)])
    assert len(options) == 1
    assert options[0]["recommendation"]["legs"][0]["fixture_id"] == "api_football:1"
    data = club_data(5)
    data["candidates"][-1]["decimal_price"] = 1.01
    sink = {}
    build_tipster_recommendations(data, model_results(5), {"enabled_profiles": ["tipster_archiv_nora"]}, NOW,
                                 diversity_options=sink)
    assert sink["tipster_archiv_nora"]
    assert all(leg["fixture_id"] != "api_football:5" for option in sink["tipster_archiv_nora"]
               for leg in option["recommendation"]["legs"])


def test_bounded_search_keeps_middle_length_and_singleton_fallbacks():
    rows = [{"fixture": str(n), "score": 0.8, "price": 1.05} for n in range(10)]
    options, diagnostics = bounded_ticket_groups(
        rows, rank=lambda group: (-sum(row["score"] for row in group) / math.sqrt(len(group)),
                                  tuple(row["fixture"] for row in group)),
        fixture=lambda row: row["fixture"], cohort=lambda row: "book",
        price=lambda row: row["price"], identity=lambda row: row["fixture"],
        max_legs=6, max_price=40, beam_width=8, max_options=24)
    assert {len(group) for group in options} == set(range(1, 7))
    assert len(options) <= 24 and diagnostics["bounded_search"]


def contrarian_data(*, key="home", price=2.5, explanation=True):
    data = enriched(count=1, keys=[key])
    quote = data["candidates"][0]
    quote["decimal_price"] = price
    quote["odds_movement"] = {
        "status": "VERIFIED_RISE", "baseline_price": price / 1.25, "current_price": price,
        "relative_change": 0.25, "baseline_quoted_at": (NOW - timedelta(hours=30)).isoformat(),
        "current_quoted_at": NOW.isoformat(), "observed_as_of": NOW.isoformat(),
        "window_hours": 30, "source_run_id": "previous-verified-run", "source_snapshot_sha256": "a" * 64,
        "evidence_id": "odds-movement:verified-synthetic",
        "identity": {key: quote.get(key, "REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES"
                                    if key == "settlement" else None)
                     for key in ("fixture_id", "market_key", "selection_key", "period", "settlement",
                                 "bookmaker", "region", "currency", "quote_provider")},
    }
    if explanation:
        quote["contrarian_review"] = {
            "status": "COMPLETE", "candidate_id": quote["candidate_id"], "reviewed_at": NOW.isoformat(),
            "price_case": "Az összevethető forrásár emelkedett; az ok nem igazolt.",
            "math_case": "A modellbecslés és a jelenlegi ár szorzata meghaladja az 1,15-öt.",
            "cause_status": "UNKNOWN", "cause_source_urls": [],
        }
    return data


def contrarian(data, models=None, profile="tipster_ertek_milan"):
    return build_tipster_recommendations(data, model_results(1) if models is None else models,
                                        {"enabled_profiles": [profile]}, NOW)[0]


@pytest.mark.parametrize("profile,key,price", [
    ("tipster_ertek_milan", "home", 2.5), ("tipster_remi_doma", "draw", 6.0),
    ("tipster_meresz_hunor", "home", 2.5),
])
def test_three_contrarian_personas_require_verified_math_and_honest_astra_explanation(
    profile, key, price, isolated_single_persona,
):
    isolated_single_persona(profile)
    data = contrarian_data(key=key, price=price)
    original = deepcopy(data)
    card = contrarian(data, profile=profile)
    assert card["status"] == "READY" and len(card["legs"]) == 1
    case = card["legs"][0]["contrarian_analysis"]
    assert case["expected_return"] > 1.15 and case["relative_change"] == pytest.approx(0.25)
    assert set(case["supporting_models"]) == {"gauss", "laplace"}
    assert case["explanation"]["cause_status"] == "UNKNOWN"
    assert case["retail_sentiment_inferred"] is False and card["probability"] is None
    assert data == original


def test_prereview_context_bypasses_only_explanation_requirement(isolated_single_persona):
    isolated_single_persona("tipster_ertek_milan")
    data = contrarian_data(explanation=False)
    settings = {"enabled_profiles": ["tipster_ertek_milan"]}
    contexts = contrarian_candidate_context(data, model_results(1), settings, NOW)
    assert list(contexts) == [data["candidates"][0]["candidate_id"]]
    assert contexts[next(iter(contexts))]["expected_return"] > 1.15
    assert contrarian(data)["status"] == "ABSTAINED"
    assert contrarian_candidate_context(data, {"kepler": model_results(1)["kepler"]}, settings, NOW) == {}


@pytest.mark.parametrize("change", [
    "no_history", "wrong_identity", "outside_window", "future_current", "wrong_current_price",
    "rise_too_small", "no_explanation", "empty_math", "wrong_candidate", "future_review", "stale_review",
    "missing_model", "inconsistent_change",
])
def test_contrarian_missing_or_inconsistent_evidence_never_publishes(change, isolated_single_persona):
    isolated_single_persona("tipster_ertek_milan")
    data = contrarian_data()
    quote = data["candidates"][0]
    movement = quote["odds_movement"]
    models = model_results(1)
    if change == "no_history":
        quote.pop("odds_movement")
    elif change == "wrong_identity":
        movement["identity"]["selection_key"] = "away"
    elif change == "outside_window":
        movement["baseline_quoted_at"] = (NOW - timedelta(hours=49)).isoformat()
    elif change == "future_current":
        movement["current_quoted_at"] = (NOW + timedelta(hours=1)).isoformat()
    elif change == "wrong_current_price":
        movement["current_price"] = 2.6
    elif change == "rise_too_small":
        movement["baseline_price"] = movement["current_price"] / 1.09
    elif change == "no_explanation":
        quote.pop("contrarian_review")
    elif change == "empty_math":
        quote["contrarian_review"]["math_case"] = " "
    elif change == "wrong_candidate":
        quote["contrarian_review"]["candidate_id"] = "other"
    elif change == "future_review":
        quote["contrarian_review"]["reviewed_at"] = (NOW + timedelta(hours=1)).isoformat()
    elif change == "stale_review":
        quote["contrarian_review"]["reviewed_at"] = (NOW - timedelta(hours=1)).isoformat()
    elif change == "inconsistent_change":
        movement["relative_change"] = 0.8
    elif change == "missing_model":
        models = {"kepler": models["kepler"]}
    card = contrarian(data, models)
    assert card["status"] == "ABSTAINED" and card["legs"] == []


def test_contrarian_fifteen_percent_boundary_is_exclusive(isolated_single_persona):
    isolated_single_persona("tipster_ertek_milan")
    data = contrarian_data(price=2.3)
    models = model_results(1)
    for result in models.values():
        result["predictions"][0]["probabilities"].update(home=0.5, draw=0.25, away=0.25)
    assert contrarian(data, models)["status"] == "ABSTAINED"


def test_persona_components_are_unit_scaled_before_weighting_and_not_probabilities():
    data = club_data(4)
    card = build_tipster_recommendations(data, model_results(4), {"enabled_profiles": ["tipster_hazai_mark"]}, NOW)[0]
    assert card["legs"]
    for leg in card["legs"]:
        details = leg["model_details"]
        assert all(0 <= value <= 1 for value in details["normalized_score_components"].values())
        assert 0 <= details["selection_score"] <= 1
        assert details["selection_score"] != leg["model_probability"]
    assert card["selection_diagnostics"]["scoring"]["calibrated_probability"] is False


def attach_veto(quote, context):
    quote["veto_override_context"] = context
    url = "https://example.org/verified-match-evidence"
    ids = [evidence["evidence_id"] for evidence in quote["evidence"]]
    quote["astra_veto_review"] = {
        "status": "COMPLETE", "reviewed_at": NOW.isoformat(),
        "candidate_snapshot_sha256": veto_candidate_signature(quote),
        "sources": [{"url": url}],
        "reviews": [{"candidate_id": quote["candidate_id"], "assessment": "support",
                     "source_urls": [url], "evidence_ids": ids,
                     "veto_override": {"enabled": True, "gates": context["gates"],
                                       "justification": "Konkrét forrás támogatja a nevesített heurisztika kivételét.",
                                       "source_urls": [url], "evidence_ids": ids}}],
    }


def test_club_high_odds_requires_actual_bound_source_review_and_only_approves_singleton(isolated_single_persona):
    isolated_single_persona("tipster_archiv_nora", maximum=14)
    data = club_data(2)
    quote = data["candidates"][0]
    quote["decimal_price"] = 25
    settings = {"enabled_profiles": ["tipster_archiv_nora"]}
    models = model_results(2)
    contexts = club_veto_candidate_context(data, models, settings, NOW)
    assert contexts[quote["candidate_id"]]["gates"] == ["HIGH_ODDS"]
    quote["astra_veto_override"] = True  # A display flag alone never authorizes it.
    sink = {}
    build_tipster_recommendations(data, models, settings, NOW, diversity_options=sink)
    assert all(leg["fixture_id"] != quote["fixture_id"] for item in sink["tipster_archiv_nora"]
               for leg in item["recommendation"]["legs"])
    attach_veto(quote, contexts[quote["candidate_id"]])
    approved = {}
    build_tipster_recommendations(data, models, settings, NOW, diversity_options=approved)
    flagged = [item["recommendation"] for item in approved["tipster_archiv_nora"]
               if any(leg["fixture_id"] == quote["fixture_id"] for leg in item["recommendation"]["legs"])]
    assert len(flagged) == 1 and len(flagged[0]["legs"]) == 1
    assert flagged[0]["combined_price"] == 25
    assert flagged[0]["legs"][0]["veto_override"]["profile_upper_price"] == 14
    quote["decimal_price"] = 26  # Previously approved quote no longer matches.
    stale = {}
    build_tipster_recommendations(data, models, settings, NOW, diversity_options=stale)
    assert all(leg["fixture_id"] != quote["fixture_id"] for item in stale["tipster_archiv_nora"]
               for leg in item["recommendation"]["legs"])


def test_club_league_rate_override_preserves_generated_history_and_samples(isolated_single_persona):
    isolated_single_persona("tipster_archiv_nora")
    data = club_data(1)
    quote = data["candidates"][0]
    quote["api_football_league_id"] = "25"
    for evidence in quote["evidence"]:
        evidence["strength"] = 0.25
    analysis = {"candidate_id": quote["candidate_id"], "status": "CALCULATED", "period": "FULL_TIME",
                "history_league_id": "25", "profiles": {
                    side: {window: {"sample_size": 20} for window in ("historical_20", "venue_10")}
                    for side in ("home", "away")}}
    data["history_analysis"]["candidates"] = [analysis]
    settings = {"enabled_profiles": ["tipster_archiv_nora"], "strictness": 35}
    models = model_results(1)
    contexts = club_veto_candidate_context(data, models, settings, NOW)
    assert contexts[quote["candidate_id"]]["gates"] == ["LEAGUE_BASE_RATE"]
    quote["veto_override_context"] = contexts[quote["candidate_id"]]
    blocked = {}
    build_tipster_recommendations(data, models, settings, NOW, diversity_options=blocked)
    assert blocked["tipster_archiv_nora"] == []
    attach_veto(quote, contexts[quote["candidate_id"]])
    approved = {}
    build_tipster_recommendations(data, models, settings, NOW, diversity_options=approved)
    assert len(approved["tipster_archiv_nora"]) == 1
    leg = approved["tipster_archiv_nora"][0]["recommendation"]["legs"][0]
    assert leg["veto_override"]["gates"] == ["LEAGUE_BASE_RATE"]
    analysis["profiles"]["home"]["venue_10"]["sample_size"] = 2
    assert club_veto_candidate_context(data, models, settings, NOW) == {}
