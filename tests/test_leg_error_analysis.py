from copy import deepcopy

import pytest
from test_paper_performance import record

from goal_analysis.storage import leg_error_analysis as module
from goal_analysis.storage.paper_performance import build_paper_performance


def ticket(identifier="one", outcomes=("WON", "LOST")):
    original = record(identifier, outcomes=list(outcomes), prices=tuple(2 for _ in outcomes))
    original["ticket_id"] = original.pop("recommendation_id")
    return original


def analyze(rows, mode="research"):
    before = deepcopy(rows)
    result = module.build_leg_error_analysis(rows, mode=mode)
    assert rows == before
    return result


def test_strict_one_loss_is_identified_with_exact_original_losing_product():
    result = analyze([ticket()])
    row = result["records"][0]
    assert row["exactly_one_loss_all_other_legs_won"]
    assert row["classification"] == "ONE_LOSS_ALL_OTHER_LEGS_WON"
    assert row["legs"][1]["observed_outcome"] == "LOST"
    assert row["legs"][1]["fixture_id"] == "api_football:2"
    assert result["summary"]["exactly_one_loss_all_other_legs_won"] == 1
    assert result["decision_effect"] == "NONE"


@pytest.mark.parametrize("outcomes,classification", [
    (("LOST",), "SINGLE_LOST_SELECTION"), (("LOST", "PENDING"), "ONE_LOSS_OTHER_LEGS_UNRESOLVED"),
    (("LOST", "REVIEW"), "ONE_LOSS_OTHER_LEGS_UNRESOLVED"), (("LOST", "VOID"), "ONE_LOSS_WITH_VOID_LEGS"),
    (("LOST", "LOST"), "MULTIPLE_CONFIRMED_LOSSES"), (("WON", "WON"), "ALL_LEGS_WON"),
    (("VOID", "VOID"), "VOID_AFFECTED"), (("WON", "PENDING"), "NOT_RESOLVED"),
    (("WON", "VOID"), "VOID_AFFECTED")])
def test_incomplete_void_single_or_multiple_losses_are_not_strict_near_misses(outcomes, classification):
    result = analyze([ticket(outcomes=outcomes)])
    assert result["records"][0]["classification"] == classification
    assert not result["records"][0]["exactly_one_loss_all_other_legs_won"]


def test_settlement_order_does_not_change_original_leg_alignment():
    first = ticket()
    second = deepcopy(first)
    second["settlement"]["legs"].reverse()
    a, b = analyze([first]), analyze([second])
    assert a["summary"] == b["summary"]
    assert a["records"][0]["legs"] == b["records"][0]["legs"]


@pytest.mark.parametrize("field,value", [("fixture_id", "wrong"), ("period", "FIRST_HALF"),
    ("market_key", "totals_2_5"), ("selection_key", "away")])
def test_wrong_market_period_selection_or_match_cannot_inherit_another_loss(field, value):
    original = ticket()
    original["settlement"]["legs"][0][field] = value
    result = analyze([original])
    assert result["records"][0]["classification"] == "RESULT_REVIEW"
    assert not result["records"][0]["result_identity_bound"]
    assert all(leg["observed_outcome"] == "REVIEW" for leg in result["records"][0]["legs"])


@pytest.mark.parametrize("part", ["original", "settlement"])
def test_duplicate_original_fixture_or_result_row_is_explicit_review(part):
    original = ticket()
    rows = original["legs"] if part == "original" else original["settlement"]["legs"]
    rows.append(deepcopy(rows[0]))
    assert analyze([original])["records"][0]["classification"] == "RESULT_REVIEW"


def test_absent_upcoming_results_remain_pending_not_confirmed_errors():
    original = ticket(outcomes=("PENDING", "PENDING"))
    original.pop("settlement")
    row = analyze([original])["records"][0]
    assert row["classification"] == "NOT_RESOLVED"
    assert not row["result_identity_bound"] and row["ticket_leg_outcomes_consistent"]
    assert row["leg_outcome_counts"]["PENDING"] == 2


def test_missing_results_for_claimed_loss_never_generate_a_confirmed_lost_leg():
    original = ticket()
    original.pop("settlement")
    result = analyze([original])
    assert result["summary"]["leg_outcome_counts"]["LOST"] == 0
    assert result["records"][0]["classification"] == "RESULT_REVIEW"


def test_exact_duplicate_copy_does_not_become_a_second_observation():
    original = ticket()
    result = analyze([original, deepcopy(original)])
    assert result["summary"]["duplicate_exact_copies_ignored"] == 1
    assert result["summary"]["distinct_records"] == 1


def test_conflicting_same_record_id_is_not_silently_treated_as_another_ticket():
    first, second = ticket(), ticket(outcomes=("LOST", "WON"))
    result = analyze([first, second])
    assert result["summary"]["ambiguous_record_ids"] == 1
    assert result["summary"]["exactly_one_loss_all_other_legs_won"] == 0
    assert result["summary"]["unique_exact_products"] == 0


def test_distinct_tickets_sharing_lost_product_remain_two_dependent_exposures():
    first, second = ticket(), ticket("two")
    second["profile_id"] = "another"
    result = analyze([first, second])
    losses = [p for p in result["exact_product_results"] if p["same_product_shared_loss"]]
    assert len(losses) == 1
    assert losses[0]["selection_occurrences"] == 2
    assert losses[0]["confirmed_losing_record_ids"] == ["one", "two"]
    assert result["summary"]["unique_exact_products"] == 2


def test_different_selections_on_same_match_are_not_conflated_as_conflicting_results():
    first, second = ticket(), ticket("two", outcomes=("LOST", "WON"))
    for part in ("legs",):
        second[part][0]["selection_key"] = "away"
    second["settlement"]["legs"][0]["selection_key"] = "away"
    # Change the second fixture too so only the first match is shared.
    second["legs"][1]["fixture_id"] = "other"
    second["settlement"]["legs"][1]["fixture_id"] = "other"
    result = analyze([first, second])
    assert result["summary"]["products_with_conflicting_decided_results"] == 0


def test_contradictory_exact_product_results_remain_visible_not_majority_voted():
    result = analyze([ticket(), ticket("two", outcomes=("LOST", "WON"))])
    assert result["summary"]["products_with_conflicting_decided_results"] == 2
    assert result["summary"]["exactly_one_loss_all_other_legs_won"] == 0
    assert all(row["classification"] == "CROSS_RECORD_RESULT_REVIEW" for row in result["records"])
    assert all(not p["confirmed_losing_record_ids"] for p in result["exact_product_results"])


def test_claimed_ticket_win_cannot_conceal_a_lost_original_leg():
    original = ticket()
    original["outcome"] = "WON"
    row = analyze([original])["records"][0]
    assert "TICKET_OUTCOME_CONFLICT" in row["issues"]
    assert not row["exactly_one_loss_all_other_legs_won"]


def test_unconfirmed_void_does_not_become_a_won_or_safe_leg():
    original = ticket(outcomes=("WON", "VOID"))
    original["settlement"]["legs"][1]["reason"] = "CANCELLED"
    row = analyze([original])["records"][0]
    assert row["legs"][1]["observed_outcome"] == "REVIEW"
    assert not row["exactly_one_loss_all_other_legs_won"]


def test_actual_partial_void_payout_review_is_not_a_predicted_market_loss():
    original = ticket(outcomes=("WON", "VOID"))
    original["outcome"] = "REVIEW"
    original["settlement"]["reason"] = "ACTUAL_PARTIAL_VOID_RETURN_REQUIRES_BOOKMAKER_CONFIRMATION"
    row = analyze([original], mode="actual")["records"][0]
    assert row["classification"] == "BOOKMAKER_RETURN_REVIEW"
    assert row["ticket_leg_outcomes_consistent"]
    assert row["leg_outcome_counts"]["LOST"] == 0


def test_paper_uses_existing_measured_outcomes_without_inventing_missing_price():
    original = record(outcomes=["WON", "LOST"])
    original["legs"][0]["decimal_price"] = None
    paper = build_paper_performance([original])
    row = paper["leg_error_analysis"]["records"][0]
    assert row["financially_eligible"] is False
    assert row["exactly_one_loss_all_other_legs_won"]
    assert row["legs"][0]["decimal_price"] is None
    assert paper["summary"]["full_all_record_realized_net_eur"] is None


def test_paper_identity_mismatch_does_not_become_pending_or_a_near_miss():
    original = record(outcomes=["WON", "LOST"])
    original["settlement"]["legs"][0]["fixture_id"] = "wrong"
    result = build_paper_performance([original])["leg_error_analysis"]
    assert result["summary"]["record_result_review"] == 1
    assert result["summary"]["exactly_one_loss_all_other_legs_won"] == 0


def test_no_causal_or_probability_claim_is_generated_from_a_high_confidence_loss():
    original = ticket()
    original["legs"][1]["model_probability"] = .95
    result = analyze([original])
    assert result["records"][0]["legs"][1]["model_probability_recorded"] == .95
    assert result["records"][0]["legs"][1]["forecast_score_not_assessed_here"]
    assert "NOT_CAUSAL_ERRORS" in result["semantics"]


def test_optional_failure_keeps_explicit_unavailable_and_invalid_mode_rejected(monkeypatch):
    with pytest.raises(ValueError):
        analyze([], mode="all")
    def fail(*args, **kwargs):
        raise ValueError("bad optional data")
    monkeypatch.setattr(module, "build_leg_error_analysis", fail)
    assert module.safe_leg_error_analysis([], mode="actual")["status"] == "UNAVAILABLE"


def test_empty_analysis_has_no_phantom_losses_or_near_misses():
    result = analyze([])
    assert result["records"] == [] and result["exact_product_results"] == []
    assert result["summary"]["exactly_one_loss_all_other_legs_won"] == 0
