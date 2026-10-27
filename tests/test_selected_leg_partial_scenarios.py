"""Selected-leg research contract through the public APP module; no external I/O."""

from copy import deepcopy

import pytest

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import portfolio_distribution as candidate

NOW = "2026-10-27T01:00:00+00:00"


def prediction(fixture="f", **probabilities):
    return {"fixture_id": fixture, "probabilities": {"home": .6, "draw": .25, "away": .15,
        "over25": .6, "under25": .4, "btts_yes": .55, **probabilities}, "model_details": {"cutoff": NOW}}


def leg(fixture="f", family="totals_2_5", selection="over"):
    return {"fixture_id": fixture, "market_key": family, "selection_key": selection,
            "period": "FULL_TIME", "settlement": candidate.SETTLEMENT}


def card(name="one", legs=None, **fields):
    return {"profile_id": name, "ticket_id": f"TESTONLY-{name}", "status": "READY",
            "legs": [leg()] if legs is None else legs, "stake_units": 1, **fields}


def run(cards, predictions=None, model_status="READY", model_name="gauss", group="tickets"):
    report = {"observed_at": NOW, group: cards, "run_id": "TESTONLY_SYNTHETIC_FUTURE"}
    artifacts = {"numeric_models": {model_name: {"status": model_status,
        "predictions": [prediction()] if predictions is None else predictions}}}
    before = deepcopy((report, artifacts))
    result = candidate.build_portfolio_distribution(report, artifacts)
    assert (report, artifacts) == before
    assert set(result) == {"version", "mode", "decision_effect", "run_id", "date", "packages",
                           "assumptions", "budgets", "external_requests", "sha256", "selected_leg_partial_scenarios"}
    assert result["version"] == "arthur-portfolio-distribution-shadow-v1"
    assert result["mode"] == "SHADOW" and result["decision_effect"] == "NONE"
    assert result["run_id"] == "TESTONLY_SYNTHETIC_FUTURE" and result["date"] is None
    assert result["external_requests"] == 0
    digest = result["sha256"]
    assert digest == canonical_sha256({k: v for k, v in result.items() if k != "sha256"})
    package = result["selected_leg_partial_scenarios"]["packages"][group]
    return result, package["models"].get(model_name), package


def assert_original_complete_case_contract(result, source_card, source_prediction, run_id):
    distribution = {"method": "EXACT_SHARED_FIXTURE_MASK_DP", "probability_mass": [.4, .6],
        "trials": None, "seed": None, "sampling_standard_error_upper_bound": 0.0,
        "probability_at_least_three": 0.0, "probability_at_least_four": 0.0}
    source_sha = canonical_sha256(source_card)
    missing = {"status": "NOT_ESTIMABLE", "covered_card_count": 0, "unestimated_card_count": 1,
        "missing_cards": [{"card_sha256": source_sha, "profile_id": "one", "reason": "MODEL_NOT_READY"}],
        "covered_cards": [], "covered_expected_winners": None, "covered_winner_count_variance": None,
        "whole_portfolio_expected_winners": None, "whole_portfolio_expectation_bounds": {"lower": 0.0, "upper": 1.0},
        "whole_probability_at_least_three_bounds": None, "covered_winner_count_distribution": None,
        "whole_portfolio_winner_count_distribution": None, "fixture_scenarios": {},
        "numeric_predictions_sha256": canonical_sha256([])}
    scenario = {"status": "COMPLETE_SCENARIO", "covered_card_count": 1, "unestimated_card_count": 0,
        "missing_cards": [], "covered_cards": [{"card_sha256": source_sha, "profile_id": "one", "scenario_win_probability": .6}],
        "covered_expected_winners": .6, "covered_winner_count_variance": .24, "whole_portfolio_expected_winners": .6,
        "whole_portfolio_expectation_bounds": {"lower": .6, "upper": .6},
        "whole_probability_at_least_three_bounds": {"lower": 0.0, "upper": 0.0},
        "covered_winner_count_distribution": distribution, "whole_portfolio_winner_count_distribution": distribution,
        "fixture_scenarios": {"f": {"families": ["totals_2_5"], "patterns": [["over"], ["under"]],
            "masses": [.6, .4], "marginals": {"totals_2_5": {"over": .6, "under": .4}},
            "iterations": 1, "max_marginal_residual": 0.0,
            "coupling": "IPF_UNIFORM_FEASIBLE_MARKET_PATTERN_PRIOR_NOT_ORIGINAL_MODEL_SCORE_GRID"}},
        "numeric_predictions_sha256": canonical_sha256([source_prediction])}
    expected = {"version": "arthur-portfolio-distribution-shadow-v1", "mode": "SHADOW", "decision_effect": "NONE",
        "run_id": run_id, "date": None,
        "packages": {"tickets": {"ready_card_count": 1, "duplicate_same_profile_records_not_counted_twice": 0,
            "status": "SCENARIOS_ONLY", "models": {"gauss": scenario, "kepler": missing, "laplace": missing}},
            "recommendations": {"ready_card_count": 0, "duplicate_same_profile_records_not_counted_twice": 0,
                "status": "NO_READY_CARDS", "models": {}}},
        "assumptions": ["UNCALIBRATED_MODEL_MARGINALS_NOT_HEURISTIC_SCORES_OR_INVERSE_ODDS",
            "INDEPENDENCE_ACROSS_DISTINCT_FIXTURES_CONDITIONAL_ON_A_FIXED_MODEL_SCENARIO",
            "ONE_SHARED_COHERENT_MARKET_PATTERN_PER_FIXTURE_ACROSS_ALL_TICKETS",
            "IPF_COUPLING_IS_AN_ASSUMPTION_NOT_THE_ORIGINAL_GAUSS_OR_LAPLACE_SCORE_GRID",
            "MODEL_SCENARIOS_SEPARATE_NO_ARBITRARY_ENSEMBLE_OR_INDEPENDENT_MODEL_VOTES",
            "TRAINING_INPUT_POINT_IN_TIME_AVAILABILITY_AND_MODEL_CALIBRATION_NOT_CERTIFIED",
            "MISSING_MARKETS_REMAIN_UNESTIMATED_NOT_ZERO_PROBABILITY",
            "COUNTS_ARE_PREDICTIVE_SCENARIOS_NOT_SETTLED_KPI_OR_RETURN_ESTIMATES"],
        "budgets": {"max_cards_per_package": 30, "max_fixtures": 180, "max_exact_states": 4096, "simulation_trials": 16384},
        "external_requests": 0}
    assert {key: value for key, value in result.items() if key not in ("sha256", "selected_leg_partial_scenarios")} == expected


def test_complete_case_original_all_fields_preserved():
    result, model, _ = run([card()])
    assert_original_complete_case_contract(result, card(), prediction(), "TESTONLY_SYNTHETIC_FUTURE")
    reference = model["cards"][0]
    assert reference["known_leg_event_probability"] == pytest.approx(.6)
    assert reference["scenario_model_full_card_probability"] == pytest.approx(.6)
    assert reference["scenario_model_full_card_probability_bounds"] == pytest.approx({"lower": .6, "upper": .6})
    assert reference["unknown_leg_count"] == 0


def test_partial_two_of_three_preserves_known_data_without_new_ticket():
    ticket = card(legs=[leg("f"), leg("g", selection="under"), leg("missing")])
    result, model, _ = run([ticket], [prediction("f"), prediction("g")])
    reference = model["cards"][0]
    assert result["packages"]["tickets"]["models"]["gauss"]["status"] == "NOT_ESTIMABLE"
    assert result["packages"]["tickets"]["models"]["gauss"]["fixture_scenarios"] == {}
    assert reference["known_leg_count"] == 2 and reference["unknown_leg_count"] == 1
    assert reference["known_leg_event_probability"] == pytest.approx(.24)
    assert reference["scenario_model_full_card_probability"] is None
    assert reference["scenario_model_full_card_probability_bounds"] == pytest.approx({"lower": 0, "upper": .24})
    assert reference["source_card_sha256"] == canonical_sha256(ticket)
    assert reference["source_card_identity"]["ticket_id"] == ticket["ticket_id"]
    assert reference["source_leg_count"] == 3 and not reference["research_event_is_new_ticket"]
    assert reference["leg_bindings"][2]["leg"]["fixture_id"] == "missing"
    assert reference["leg_bindings"][2]["binding"] == "MISSING_OR_AMBIGUOUS_FIXTURE_PREDICTION"
    assert set(reference["fixture_scenarios"]) == {"f", "g"}
    assert reference["bounds_interpretation"] == candidate.SCENARIO_BOUNDS_LABEL


def test_kepler_matching_fixture_does_not_create_btts_or_totals_probabilities():
    predictions = [{"fixture_id": "f", "probabilities": {"home": .6, "draw": .25, "away": .15},
                    "model_details": {"cutoff": NOW}},
                   {"fixture_id": "g", "probabilities": {"home": .6, "draw": .25, "away": .15},
                    "model_details": {"cutoff": NOW}}]
    _, model, _ = run([card(legs=[leg("f", "h2h", "draw"), leg("g", "btts", "yes"), leg("missing")])],
                      predictions, model_name="kepler")
    reference = model["cards"][0]
    assert reference["known_leg_count"] == 1 and reference["unknown_leg_count"] == 2
    assert reference["known_leg_event_probability"] == pytest.approx(.25)
    assert reference["leg_bindings"][1]["binding"] == "MISSING_OR_INVALID_MARKET_PROBABILITIES"


def test_zero_known_legs_has_no_empty_conjunction_estimate_or_default_bounds():
    _, model, _ = run([card(legs=[leg("missing")])], [])
    reference = model["cards"][0]
    assert reference["status"] == "NO_COVERED_LEGS_NOT_ESTIMABLE"
    assert reference["known_leg_event_probability"] is None
    assert reference["scenario_model_full_card_probability_bounds"] is None
    assert model["joint_known_leg_events"] is None


@pytest.mark.parametrize("bad", [None, True, -.1, 1.1, float("nan"), float("inf")])
def test_bad_probability_stays_unknown_but_other_fixture_survives(bad):
    _, model, _ = run([card(legs=[leg("bad"), leg("f")])], [prediction("bad", over25=bad), prediction()])
    reference = model["cards"][0]
    assert reference["known_leg_count"] == 1
    assert reference["known_leg_event_probability"] == pytest.approx(.6)
    assert reference["leg_bindings"][0]["binding"] == "MISSING_OR_INVALID_MARKET_PROBABILITIES"


@pytest.mark.parametrize("cutoff", [None, "naive", "2026-10-27T01:00:00", "2026-10-27T01:01:00+00:00"])
def test_bad_cutoff_is_local_unknown(cutoff):
    bad = prediction("bad")
    bad["model_details"]["cutoff"] = cutoff
    _, model, _ = run([card(legs=[leg("bad"), leg("f")])], [bad, prediction()])
    assert model["cards"][0]["known_leg_count"] == 1
    assert model["cards"][0]["leg_bindings"][0]["binding"] == "MISSING_OR_FUTURE_MODEL_CUTOFF"


@pytest.mark.parametrize("market,period,settlement", [
    ("totals_3_5", "FULL_TIME", candidate.SETTLEMENT),
    ("totals_2_5", "FIRST_HALF", candidate.SETTLEMENT),
    ("totals_2_5", "FULL_TIME", "INCLUDING_EXTRA_TIME")])
def test_unsupported_product_not_extrapolated(market, period, settlement):
    bad = dict(leg("bad"), market_key=market, period=period, settlement=settlement)
    _, model, _ = run([card(legs=[bad, leg("f")])], [prediction("bad"), prediction()])
    assert model["cards"][0]["known_leg_count"] == 1
    assert model["cards"][0]["leg_bindings"][0]["binding"] == "UNSUPPORTED_PRODUCT_OR_REPEATED_FIXTURE"


def test_repeated_fixture_both_occurrences_unknown_other_fixture_preserved():
    _, model, _ = run([card(legs=[leg("f"), leg("f", selection="under"), leg("g")])],
                      [prediction(), prediction("g")])
    reference = model["cards"][0]
    assert reference["known_leg_count"] == 1 and reference["known_leg_event_probability"] == pytest.approx(.6)
    assert [row["binding"] for row in reference["leg_bindings"][:2]] == [
        "UNSUPPORTED_PRODUCT_OR_REPEATED_FIXTURE"] * 2
    assert set(reference["fixture_scenarios"]) == {"g"}


def test_failed_model_does_not_use_leftover_predictions():
    _, model, _ = run([card()], model_status="MODEL_ERROR")
    assert model["cards"][0]["known_leg_count"] == 0
    assert model["cards"][0]["leg_bindings"][0]["binding"] == "MODEL_NOT_READY"


def test_ambiguous_prediction_stays_unknown():
    _, model, _ = run([card()], [prediction(), prediction()])
    assert model["cards"][0]["known_leg_event_probability"] is None
    assert model["cards"][0]["leg_bindings"][0]["binding"] == "MISSING_OR_AMBIGUOUS_FIXTURE_PREDICTION"


def test_shared_fixture_partial_events_remain_perfectly_dependent():
    tickets = [card("one", [leg(), leg("missing-a")]), card("two", [leg(), leg("missing-b")])]
    _, model, _ = run(tickets)
    distribution = model["joint_known_leg_events"]["satisfied_known_leg_event_count_distribution"]
    assert distribution["probability_mass"] == pytest.approx([.4, 0, .6])
    assert model["joint_known_leg_events"]["expected_satisfied_known_leg_events"] == pytest.approx(1.2)


def test_opposite_same_fixture_partial_events_cannot_both_win_or_lose():
    tickets = [card("one", [leg(), leg("missing-a")]),
               card("two", [leg(selection="under"), leg("missing-b")])]
    _, model, _ = run(tickets)
    assert model["joint_known_leg_events"]["satisfied_known_leg_event_count_distribution"]["probability_mass"] == pytest.approx([0, 1, 0])


def test_exact_duplicate_record_dedup_but_distinct_ticket_and_stake_retained():
    first = card("same", [leg(), leg("missing")])
    second = dict(first, ticket_id="TESTONLY-second", stake_units=2)
    _, model, package = run([first, deepcopy(first), second])
    assert package["exact_duplicate_records_not_counted_twice"] == 1
    assert len(model["cards"]) == 2
    assert {row["source_card_identity"]["stake_units"] for row in model["cards"]} == {1, 2}
    assert model["joint_known_leg_events"]["satisfied_known_leg_event_count_distribution"]["probability_mass"] == pytest.approx([.4, 0, .6])


def test_joint_incompatible_assumptions_do_not_remove_individual_events():
    predictions = [prediction(home=0, draw=1, away=0, over25=1, under25=0, btts_yes=0)]
    tickets = [card("h", [leg("f", "h2h", "draw"), leg("x")]),
               card("t", [leg("f"), leg("y")]),
               card("b", [leg("f", "btts", "no"), leg("z")])]
    _, model, _ = run(tickets, predictions)
    assert all(row["known_leg_event_probability"] == 1 for row in model["cards"])
    assert model["joint_known_leg_events"]["status"] == "UNAVAILABLE"
    assert model["joint_known_leg_events"]["reason"] == "INCOMPATIBLE_MARKET_MARGINALS"
    assert model["joint_known_leg_events"]["satisfied_known_leg_event_count_distribution"] is None


def test_deterministic_shared_fixture_seed_and_analytic_expectation(monkeypatch):
    monkeypatch.setattr(candidate, "MAX_EXACT_STATES", 1)
    tickets = [card(str(i), [leg(str(i)), leg(f"missing-{i}")]) for i in range(8)]
    predictions = [prediction(str(i)) for i in range(8)]
    _, first, _ = run(tickets, predictions)
    _, second, _ = run(list(reversed(tickets)), list(reversed(predictions)))
    first_joint = first["joint_known_leg_events"]
    assert first_joint["satisfied_known_leg_event_count_distribution"] == second["joint_known_leg_events"]["satisfied_known_leg_event_count_distribution"]
    assert first_joint["satisfied_known_leg_event_count_distribution"]["method"] == "SEEDED_SHARED_FIXTURE_MONTE_CARLO"
    assert first_joint["expected_satisfied_known_leg_events"] == pytest.approx(4.8)


@pytest.mark.parametrize("value", [0, 1])
def test_partial_deterministic_event_bounds_are_model_scenario_only(value):
    _, model, _ = run([card(legs=[leg(), leg("unknown")])], [prediction(over25=value, under25=1-value)])
    row = model["cards"][0]
    assert row["known_leg_event_probability"] == value
    assert row["scenario_model_full_card_probability"] is None
    assert row["scenario_model_full_card_probability_bounds"] == {"lower": 0.0, "upper": value}
    assert "NOT_EMPIRICAL_TRUE_PROBABILITY_OR_EDGE" in row["bounds_interpretation"]


def test_budgets_explicit_no_silent_truncation(monkeypatch):
    monkeypatch.setattr(candidate, "MAX_CARDS", 1)
    _, _, package = run([card(), card("two")])
    assert package["reason"] == "READY_CARD_BUDGET_EXCEEDED" and package["models"] == {}
    monkeypatch.setattr(candidate, "MAX_CARDS", 30)
    monkeypatch.setattr(candidate, "MAX_FIXTURES", 1)
    _, _, package = run([card(legs=[leg(), leg("unknown")])])
    assert package["reason"] == "FIXTURE_BUDGET_EXCEEDED" and package["models"] == {}


def test_leg_reference_budget_has_explicit_unit_and_never_truncates(monkeypatch):
    for module in (candidate,):
        monkeypatch.setattr(module, "MAX_CARDS", 1)
        monkeypatch.setattr(module, "MAX_FIXTURES", 2)
    _, _, package = run([card(legs=[leg(), leg(), leg()])])
    assert package["source_leg_reference_count"] == 3
    assert package["reason"] == "SELECTED_LEG_REFERENCE_BUDGET_EXCEEDED" and package["models"] == {}


def test_nonready_and_other_package_not_promoted_or_combined():
    result, model, package = run([card(status="REVIEWED"), card("draft", status="DRAFT")])
    assert model is None and package["status"] == "NO_READY_CARDS"
    assert result["selected_leg_partial_scenarios"]["packages"]["recommendations"]["status"] == "NO_READY_CARDS"


def test_new_component_exception_cannot_erase_original_complete_packages(monkeypatch):
    def fail(*_):
        raise ValueError("TESTONLY private detail")

    monkeypatch.setattr(candidate, "selected_leg_partial_scenarios", fail)
    report = {"observed_at": NOW, "tickets": [card()]}
    artifacts = {"numeric_models": {"gauss": {"status": "READY", "predictions": [prediction()]}}}
    result = candidate.build_portfolio_distribution(report, artifacts)
    assert_original_complete_case_contract(result, card(), prediction(), None)
    assert result["selected_leg_partial_scenarios"]["reason"] == "SELECTED_LEG_RESEARCH_COMPONENT_INVALID_INPUT"
    assert "private detail" not in str(result)
