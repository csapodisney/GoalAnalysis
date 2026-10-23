"""Normalized model support permits strong combinations without weakening gates."""

from copy import deepcopy

import pytest
from test_model_recommendations import build, fixture, prediction, quote

from goal_analysis.engine.model_recommendations import _selection_rank


@pytest.mark.parametrize("profile", ["gauss", "kepler", "laplace"])
def test_strong_combinations_compete_with_singletons_without_mixing_bookmakers(profile):
    fixtures = [fixture(n) for n in range(1, 5)]
    predictions = [prediction(n) for n in range(1, 5)]
    quotes = [quote(1, "strong", price=2.0)] + [quote(n, "more_legs", price=1.9) for n in (2, 3, 4)]
    original = deepcopy([fixtures, predictions, quotes])
    card = build(profile, predictions, fixtures, quotes)
    assert {leg["fixture_id"] for leg in card["legs"]} == {f"api_football:{n}" for n in (2, 3, 4)}
    assert card["bookmaker"] == "more_legs" and card["combined_price"] == pytest.approx(1.9 ** 3)
    assert all(leg["estimated_edge"] == pytest.approx(0.14) for leg in card["legs"])
    assert card["selection_diagnostics"]["selected_min_estimated_edge"] == pytest.approx(0.14)
    assert card["selection_mode"] == "EVIDENCE_FIRST"
    assert card["target_odds_minimum_enforced"] is False
    assert card["strategy_version"] == "arthur-independent-model-recommendations-v3-normalized-tickets"
    assert [fixtures, predictions, quotes] == original


def test_lower_price_single_is_valid_only_when_existing_probability_and_edge_gates_pass():
    card = build(predictions=[prediction(home=0.9)], quotes=[quote(price=1.2)])
    assert card["status"] == "MODEL_READY" and card["combined_price"] == 1.2
    assert len(card["legs"]) == 1
    assert card["legs"][0]["estimated_edge"] == pytest.approx(0.08)
    blocked = build(predictions=[prediction(home=0.9)], quotes=[quote(price=1.1)])
    assert not blocked["legs"] and blocked["status"] == "NO_STABLE_SELECTION"


def test_same_product_uses_better_quote_even_when_other_book_is_preferred():
    card = build(quotes=[quote(book="preferred", price=1.9), quote(book="better", price=2.2)],
                 preferred=["preferred"])
    assert card["bookmaker"] == "better" and card["combined_price"] == 2.2
    tied = build(quotes=[quote(book="preferred", price=2.2), quote(book="other", price=2.2)],
                 preferred=["preferred"])
    assert tied["bookmaker"] == "preferred"


def test_unpriced_combinations_retain_raw_probabilities_without_inventing_joint_probability():
    card = build(predictions=[prediction(1, home=0.8), prediction(2, home=0.7)],
                 fixtures=[fixture(1), fixture(2)])
    assert card["status"] == "ODDS_PENDING" and len(card["legs"]) == 2
    assert [leg["model_probability"] for leg in card["legs"]] == [0.8, 0.7]
    assert card["selection_diagnostics"]["selection_basis"] == "MODEL_PROBABILITY"
    assert card["combined_price"] is card["probability"] is None


def test_normalized_ticket_objective_rewards_strong_pair_but_not_very_weak_padding():
    legs = [{**quote(n, price=(1 + edge) / probability), "model_probability": probability,
             "model_stability": 0.03, "estimated_edge": edge}
            for n, (probability, edge) in enumerate(((0.9, 0.8), (0.85, 0.7), (0.4, 0.03)), 1)]
    assert _selection_rank(legs[:2]) < _selection_rank(legs[:1])
    assert _selection_rank(legs[:2]) < _selection_rank(legs)
