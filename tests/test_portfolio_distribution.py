from copy import deepcopy

import pytest

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import portfolio_distribution as module

NOW = "2026-10-22T07:00:00+00:00"


def prediction(fixture="f", **values):
    return {"fixture_id": fixture, "probabilities": {"home": .5, "draw": .25, "away": .25,
        "over25": .6, "under25": .4, "btts_yes": .5, **values}, "model_details": {"cutoff": NOW}}


def card(profile="one", fixture="f", market="totals_2_5", selection="over"):
    return {"profile_id": profile, "status": "READY", "legs": [{"fixture_id": fixture,
        "market_key": market, "selection_key": selection, "period": "FULL_TIME", "settlement": module.SETTLEMENT}],
        "combined_price": 20, "support_score": .99}


def run(cards, predictions=None):
    predictions = [prediction()] if predictions is None else predictions
    report = {"observed_at": NOW, "recommendations": cards}
    artifacts = {"numeric_models": {"gauss": {"status": "READY", "predictions": predictions}}}
    before = deepcopy((report, artifacts))
    result = module.build_portfolio_distribution(report, artifacts)
    assert (report, artifacts) == before
    return result["packages"]["recommendations"]["models"]["gauss"]


def test_one_numeric_scenario_preserves_marginal_no_inverse_odds_or_heuristic():
    result = run([card()])
    assert result["whole_portfolio_expected_winners"] == pytest.approx(.6)
    assert result["covered_winner_count_distribution"]["probability_mass"] == pytest.approx([.4, .6])


def test_identical_selections_in_different_personas_are_perfectly_dependent():
    result = run([card("one"), card("two")])
    assert result["whole_portfolio_expected_winners"] == pytest.approx(1.2)
    assert result["covered_winner_count_distribution"]["probability_mass"] == pytest.approx([.4, 0, .6])
    assert result["covered_winner_count_variance"] == pytest.approx(.96)


def test_distinct_ticket_ids_same_persona_are_counted_separately_with_shared_outcome():
    first, second = card(), card()
    first["ticket_id"], second["ticket_id"] = "ticket-one", "ticket-two"
    result = run([first, second])
    assert result["covered_card_count"] == 2
    assert result["covered_winner_count_distribution"]["probability_mass"] == pytest.approx([.4, 0, .6])


def test_opposite_same_match_choices_cannot_both_lose_or_win():
    result = run([card(), card("two", selection="under")])
    assert result["covered_winner_count_distribution"]["probability_mass"] == pytest.approx([0, 1, 0])
    assert result["covered_winner_count_variance"] == pytest.approx(0, abs=1e-10)


def test_shared_leg_combinations_use_one_shared_outcome():
    first, second = card(), card("two")
    first["legs"].append(card(fixture="g")["legs"][0])
    second["legs"].append(card(fixture="h")["legs"][0])
    result = run([first, second], [prediction(f) for f in "fgh"])
    assert result["whole_portfolio_expected_winners"] == pytest.approx(.72)
    # P(two winners)=P(f)*P(g)*P(h), not P(f)^2*P(g)*P(h).
    assert result["covered_winner_count_distribution"]["probability_mass"][2] == pytest.approx(.216)


def test_h2h_is_categorical_with_exactly_one_winner():
    result = run([card(s, market="h2h", selection=s) for s in ("home", "draw", "away")])
    assert result["covered_winner_count_distribution"]["probability_mass"] == pytest.approx([0, 1, 0, 0])


def test_joint_patterns_reject_impossible_draw_over_btts_no_combination():
    joint = module.joint_patterns(prediction(), module.FAMILIES)
    for pattern in joint["patterns"]:
        state = dict(zip(joint["families"], pattern, strict=True))
        assert not (state["h2h"] == "draw" and state["totals_2_5"] == "over" and state["btts"] == "no")
    for index, family in enumerate(joint["families"]):
        for value, expected in joint["marginals"][family].items():
            actual = sum(m for p, m in zip(joint["patterns"], joint["masses"], strict=True) if p[index] == value)
            assert actual == pytest.approx(expected, abs=1e-9)


def test_incompatible_market_constraints_explicitly_abstain():
    pred = prediction(home=0, draw=1, away=0, over25=1, under25=0, btts_yes=0)
    with pytest.raises(ValueError, match="INCOMPATIBLE"):
        module.joint_patterns(pred, module.FAMILIES)


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), -.1, 1.1, True])
def test_missing_invalid_probability_is_never_replaced_with_support_or_inverse_odds(bad):
    result = run([card()], [prediction(over25=bad)])
    assert result["status"] == "NOT_ESTIMABLE"
    assert result["whole_portfolio_expected_winners"] is None
    assert result["whole_portfolio_expectation_bounds"] == {"lower": 0, "upper": 1}


@pytest.mark.parametrize("market,period,settlement", [("totals_3_5", "FULL_TIME", module.SETTLEMENT),
    ("totals_2_5", "FIRST_HALF", module.SETTLEMENT), ("totals_2_5", "FULL_TIME", "INCLUDING_EXTRA_TIME")])
def test_unmodeled_products_stay_unestimated(market, period, settlement):
    ticket = card(market=market)
    ticket["legs"][0].update(period=period, settlement=settlement)
    result = run([ticket])
    assert result["covered_card_count"] == 0 and result["whole_portfolio_winner_count_distribution"] is None


@pytest.mark.parametrize("cutoff", [None, "naive", "2026-10-22T08:00:00+00:00", "2026-10-22T07:00:00"])
def test_missing_naive_or_future_model_cutoff_abstains(cutoff):
    pred = prediction()
    pred["model_details"]["cutoff"] = cutoff
    assert run([card()], [pred])["missing_cards"][0]["reason"] == "MISSING_OR_FUTURE_MODEL_CUTOFF"


def test_partial_package_has_only_covered_distribution_and_full_count_bounds():
    result = run([card(), card("missing", market="totals_3_5")])
    assert result["status"] == "PARTIAL_SCENARIO"
    assert result["whole_portfolio_expected_winners"] is None
    assert result["whole_portfolio_winner_count_distribution"] is None
    assert result["whole_portfolio_expectation_bounds"] == pytest.approx({"lower": .6, "upper": 1.6})


def test_exact_goal_tail_and_unestimated_count_bounds():
    result = run([card("a"), card("b"), card("c"), card("missing", market="totals_3_5")])
    assert result["covered_winner_count_distribution"]["probability_at_least_three"] == pytest.approx(.6)
    assert result["whole_probability_at_least_three_bounds"] == pytest.approx({"lower": .6, "upper": .6})


def test_missing_ambiguous_fixture_prediction_does_not_guess():
    for predictions in ([], [prediction(), prediction()]):
        result = run([card()], predictions)
        assert result["status"] == "NOT_ESTIMABLE"
        assert result["missing_cards"][0]["reason"] == "MISSING_OR_AMBIGUOUS_FIXTURE_PREDICTION"


def test_same_fixture_twice_on_one_ticket_is_not_multiplied():
    ticket = card()
    ticket["legs"] *= 2
    assert run([ticket])["missing_cards"][0]["reason"] == "UNSUPPORTED_PRODUCT_OR_REPEATED_FIXTURE"


@pytest.mark.parametrize("value", [0, 1])
def test_deterministic_margins_no_division_by_zero(value):
    result = run([card()], [prediction(over25=value, under25=1-value)])
    assert result["whole_portfolio_expected_winners"] == value


def test_empty_or_non_ready_cards_never_enter_estimates():
    report = {"observed_at": NOW, "tickets": [dict(card(), status="DRAFT"), {"status": "READY", "legs": []}]}
    result = module.build_portfolio_distribution(report, {})
    assert result["packages"]["tickets"]["status"] == "NO_READY_CARDS"


def test_main_and_recommendation_package_never_double_counted():
    report = {"observed_at": NOW, "tickets": [card()], "recommendations": [card(), card()]}
    result = module.build_portfolio_distribution(report, {"numeric_models": {"gauss": {"status": "READY", "predictions": [prediction()]}}})
    assert result["packages"]["tickets"]["ready_card_count"] == 1
    assert result["packages"]["recommendations"]["ready_card_count"] == 1
    assert result["packages"]["recommendations"]["duplicate_same_profile_records_not_counted_twice"] == 1


def test_bounded_seeded_simulation_reproducible_and_analytic_moment_unchanged(monkeypatch):
    tickets = [card(str(i), fixture=str(i)) for i in range(8)]
    predictions = [prediction(str(i)) for i in range(8)]
    exact = run(tickets, predictions)
    monkeypatch.setattr(module, "MAX_EXACT_STATES", 1)
    sampled = run(tickets, predictions)
    again = run(list(reversed(tickets)), list(reversed(predictions)))
    assert sampled["covered_winner_count_distribution"] == again["covered_winner_count_distribution"]
    assert sampled["covered_winner_count_distribution"]["method"] == "SEEDED_SHARED_FIXTURE_MONTE_CARLO"
    assert sampled["whole_portfolio_expected_winners"] == exact["whole_portfolio_expected_winners"]
    bins = sampled["covered_winner_count_distribution"]["probability_mass"]
    assert sum(bins) == 1
    assert sum(i * p for i, p in enumerate(bins)) == pytest.approx(4.8, abs=.06)


def test_profile_and_fixture_budgets_are_explicit_no_silent_truncation(monkeypatch):
    monkeypatch.setattr(module, "MAX_CARDS", 1)
    result = module.build_portfolio_distribution({"recommendations": [card(), card("two")]}, {})
    assert result["packages"]["recommendations"]["reason"] == "READY_CARD_BUDGET_EXCEEDED"
    monkeypatch.setattr(module, "MAX_CARDS", 30)
    monkeypatch.setattr(module, "MAX_FIXTURES", 0)
    assert run([card()])["reason"] == "FIXTURE_BUDGET_EXCEEDED"


def test_fixed_model_scenarios_remain_separate():
    report = {"observed_at": NOW, "recommendations": [card()]}
    artifacts = {"numeric_models": {"gauss": {"status": "READY", "predictions": [prediction()]},
        "laplace": {"status": "READY", "predictions": [prediction(over25=.4, under25=.6)]}}}
    models = module.build_portfolio_distribution(report, artifacts)["packages"]["recommendations"]["models"]
    assert models["gauss"]["whole_portfolio_expected_winners"] == pytest.approx(.6)
    assert models["laplace"]["whole_portfolio_expected_winners"] == pytest.approx(.4)
    assert models["kepler"]["whole_portfolio_expected_winners"] is None


def test_json_hash_and_shadow_no_input_mutation():
    report = {"observed_at": NOW, "tickets": [card()]}
    before = deepcopy(report)
    result = module.build_portfolio_distribution(report, {})
    assert report == before and result["mode"] == "SHADOW" and result["decision_effect"] == "NONE"
    digest = result.pop("sha256")
    assert canonical_sha256(result) == digest


def test_failed_model_cannot_reuse_leftover_predictions():
    report = {"observed_at": NOW, "recommendations": [card()]}
    artifacts = {"numeric_models": {"gauss": {"status": "MODEL_ERROR", "predictions": [prediction()]}}}
    result = module.build_portfolio_distribution(report, artifacts)["packages"]["recommendations"]["models"]["gauss"]
    assert result["missing_cards"][0]["reason"] == "MODEL_NOT_READY"
