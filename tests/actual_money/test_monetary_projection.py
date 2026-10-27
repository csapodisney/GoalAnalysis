"""Pure actual-money controls; no ledger/database/provider constructors."""
from copy import deepcopy

import pytest

from goal_analysis.storage.monetary_projection import (
    GROSS,
    LEGACY,
    project_recorded_cohort,
    project_recorded_wager,
)


def record(outcome="WON", *, source="USER_CONFIRMED", stake=1, price=12.6, key="a", void=False):
    return {"ticket_id": key, "outcome": outcome,
            "wager": {"ticket_id": key, "stake_eur": stake, "actual_combined_price": price,
                      "actual_price_source": source, "recorded_at": "2026-01-01T12:00:00Z"},
            "settlement": {"outcome": outcome, "settlement_hash": "a" * 64,
                           "observed_at": "2026-01-02T12:00:00Z",
                           "legs": [{"outcome": "VOID" if void else outcome}]}}


def returned(item, amount, *, typed=False):
    item["settlement"]["bookmaker_return"] = {
        "ticket_id": item["ticket_id"], "settlement_hash": "a" * 64,
        "payout_eur": amount, "recorded_at": "2026-01-03T12:00:00Z",
        **({"payout_basis": GROSS, "fee_tax_inclusion": "EXCLUDED",
            "provenance_confirmed_at": "2026-01-03T12:00:00Z",
            "contract_version": "bookmaker-gross-return-v1"} if typed else {}),
    }
    return item


def test_t01_confirmed_win_is_recorded_arithmetic_not_cash():
    result = project_recorded_wager(record())
    assert (result["payout_eur"], result["net_eur"]) == (12.6, 11.6)
    assert result["bank_cash_receipt_eur"] is result["after_cost_system_net_eur"] is None


def test_t02_known_loss_uses_original_stake():
    result = project_recorded_wager(record("LOST", price=None))
    assert (result["payout_eur"], result["net_eur"]) == (0, -1)


def test_t03_estimated_win_remains_unknown():
    item = record(source="RESEARCH_QUOTE_ESTIMATE")
    result = project_recorded_wager(item)
    assert result["status"] == "UNKNOWN"
    assert result["payout_eur"] is result["net_eur"] is None
    assert (result["ticket_id"], result["stake_eur"], result["sports_outcome"]) == ("a", 1, "WON")


def test_t04_estimated_or_unknown_loss_remains_known():
    for source in ("RESEARCH_QUOTE_ESTIMATE", "UNKNOWN_LEGACY"):
        assert project_recorded_wager(record("LOST", source=source))["net_eur"] == -1


def test_t05_full_void_refunds_without_winning_price():
    result = project_recorded_wager(record("VOID", source="RESEARCH_QUOTE_ESTIMATE", price=None))
    assert (result["payout_eur"], result["net_eur"], result["settled"]) == (1, 0, True)


def test_t06_partial_void_never_divides_combined_price():
    item = record(void=True)
    item["settlement"]["legs"][0]["decimal_price"] = 2
    result = project_recorded_wager(item)
    assert result["payout_eur"] is result["net_eur"] is None
    assert result["reason"] == "PARTIAL_VOID_REQUIRES_CURRENT_HASH_BOUND_RETURN"


def test_t07_new_typed_partial_void_return_preserves_30_and_29():
    result = project_recorded_wager(returned(record(void=True), 30, typed=True))
    assert (result["payout_eur"], result["net_eur"], result["gross_sports_return_eur"]) == (30, 29, 30)
    assert result["payout_basis"] == GROSS


def test_t08_old_revision_return_is_not_reused():
    item = returned(record(void=True), 30)
    item["settlement"]["settlement_hash"] = "b" * 64
    assert project_recorded_wager(item)["net_eur"] is None


def test_t09_foreign_wager_return_cannot_supply_target_amount():
    item = returned(record(void=True), 30)
    item["settlement"]["bookmaker_return"]["ticket_id"] = "foreign"
    assert project_recorded_wager(item)["net_eur"] is None


def test_t10_legacy_return_retains_arithmetic_without_gross_or_cash_claim():
    result = project_recorded_wager(returned(record(stake=5, void=True), 20))
    assert (result["reported_return_eur"], result["payout_eur"], result["net_eur"]) == (20, 20, 15)
    assert result["payout_basis"] == LEGACY
    assert result["gross_sports_return_eur"] is result["bank_cash_receipt_eur"] is None
    assert result["fee_tax_inclusion"] == "UNKNOWN"


def test_t11_open_stake_has_no_invented_realized_zero():
    item = record("PENDING", stake=5)
    result = project_recorded_wager(item)
    assert (result["status"], result["open_stake_eur"], result["cash_flow_eur"]) == ("OPEN", 5, -5)
    assert result["payout_eur"] is result["net_eur"] is None
    cohort = project_recorded_cohort([item])
    assert cohort["net_eur"] == 0  # Closed-to-date empty sum, not the open ticket result.
    assert cohort["open_records"] == 1


def test_t12_unknown_win_does_not_hide_negative_known_subset():
    cohort = project_recorded_cohort([record("LOST"), record(source="RESEARCH_QUOTE_ESTIMATE", key="b")])
    assert cohort["net_eur"] is cohort["yield_pct"] is None
    assert cohort["known_subset_net_eur"] == -1
    assert cohort["unknown_or_invalid_records"] == 1


def test_t13_distinct_tickets_with_shared_fixtures_keep_both_stakes():
    first, second = record("LOST", stake=5), record("LOST", stake=2, key="b")
    first["fixture_id"] = second["fixture_id"] = "shared"
    result = project_recorded_cohort([first, second])
    assert (result["unique_wager_keys"], result["stake_eur"], result["net_eur"]) == (2, 7, -7)


def test_t14_identical_references_are_one_wager():
    item = record("LOST")
    result = project_recorded_cohort([item, deepcopy(item)])
    assert (result["source_records"], result["unique_wager_keys"], result["net_eur"]) == (2, 1, -1)
    assert len(result["duplicate_references"]) == 1


def test_t15_conflicting_same_wager_never_chooses_favorable_version():
    result = project_recorded_cohort([record("LOST"), record("LOST", stake=2)])
    assert result["net_eur"] is result["stake_eur"] is None
    assert len(result["conflicting_wagers"]) == 1
    assert not result["records"]


def test_t16_unplayed_research_has_no_actual_money():
    item = record()
    del item["wager"]
    result = project_recorded_wager(item)
    assert result["status"] == "NOT_ACTUAL"
    assert result["stake_eur"] is result["net_eur"] is None


def test_t17_historical_stake_five_is_not_current_policy_one():
    assert project_recorded_wager(record("LOST", stake=5))["net_eur"] == -5


def test_t18_invalid_stakes_remain_in_cohort_accounting():
    values = (float("nan"), float("inf"), True, -1, None)
    rows = [record(stake=value, key=str(index)) for index, value in enumerate(values)]
    result = project_recorded_cohort(rows)
    assert result["unique_wager_keys"] == result["unknown_or_invalid_records"] == len(values)
    assert result["net_eur"] is None


def test_t19_missing_price_only_blocks_winning_money():
    for value in (None, False, float("nan"), 1, -10):
        assert project_recorded_wager(record("LOST", price=value))["net_eur"] == -1
        assert project_recorded_wager(record(price=value))["net_eur"] is None


def test_t20_half_up_rounding_once_and_exact_cent_cohort():
    assert project_recorded_wager(record(price=19.125))["payout_eur"] == 19.13
    rows = [record(price=1.1, key=str(index)) for index in range(10)]
    assert project_recorded_cohort(rows)["net_eur"] == 1


def test_t21_unknown_fee_inclusion_is_not_automatic_deduction():
    result = project_recorded_wager(returned(record(void=True), 30))
    assert result["net_eur"] == 29
    assert result["fee_tax_inclusion"] == "UNKNOWN"
    assert result["after_cost_system_net_eur"] is None


def test_t22_net_cash_basis_is_not_silently_gross_or_deducted_again():
    item = returned(record(void=True), 0.9)
    item["settlement"]["bookmaker_return"]["payout_basis"] = "NET_CASH_RETURN"
    item["fee_event"] = {"amount_eur": 0.1}
    result = project_recorded_wager(item)
    assert result["net_eur"] is result["bank_cash_receipt_eur"] is None
    assert "UNSUPPORTED_RETURN_BASIS_NO_CASH_OR_FEE_INFERENCE" in result["excluded_evidence"]


def test_t23_all_consumer_shapes_share_same_normalized_projection():
    rows = [record("LOST"), returned(record(stake=5, void=True, key="b"), 20)]
    copied = deepcopy(rows)
    for item in copied:
        item["wager"]["price_source"] = item["wager"].pop("actual_price_source")
    assert [project_recorded_wager(row)["net_eur"] for row in rows] == [-1, 15]
    assert [project_recorded_wager(row)["net_eur"] for row in copied] == [-1, 15]
    assert project_recorded_cohort(rows)["net_eur"] == project_recorded_cohort(copied)["net_eur"] == 14


def test_t24_unknown_is_typed_null_never_positive_or_zero():
    result = project_recorded_wager(record(source="RESEARCH_QUOTE_ESTIMATE"))
    assert result["net_eur"] is None
    assert result["status"] == "UNKNOWN" and not result["settled"]


def test_t25_cutoff_never_borrows_future_settlement_or_return():
    item = returned(record(void=True), 30, typed=True)
    item["as_of"] = "2026-01-02T13:00:00Z"
    assert project_recorded_wager(item)["net_eur"] is None
    item["as_of"] = "2026-01-01T13:00:00Z"
    assert project_recorded_wager(item)["status"] == "OPEN"
    item["as_of"] = "2026-01-01T11:00:00Z"
    assert project_recorded_wager(item)["status"] == "FUTURE"


def test_t26_complete_partition_preserves_unassigned_and_invalid():
    result = project_recorded_cohort([record("LOST"), {"ticket_id": "b"}, None], unassigned_source_records=2)
    assert result["source_records"] == 3
    assert (result["projected_records"], len(result["unkeyed_records"]), result["unassigned_source_records"]) == (2, 1, 2)
    assert result["net_eur"] is None
    assert result["known_subset_net_eur"] == -1


def test_t27_original_legacy_30_full_void_one_and_loss_arithmetic():
    rows = [returned(record(void=True), 30), record("VOID", key="b"), record("LOST", key="c")]
    assert [project_recorded_wager(row)["net_eur"] for row in rows] == [29, 0, -1]
    assert project_recorded_cohort(rows)["net_eur"] == 28


def test_t28_no_cost_or_bank_cash_claim_even_with_known_sports_result():
    result = project_recorded_cohort([record()])
    assert result["net_eur"] == 11.6
    assert result["bank_cash_receipt_eur"] is result["after_cost_system_net_eur"] is None
    assert result["costs_included"] is result["fee_tax_or_subscription_allocation_inferred"] is False


def test_contradictory_price_tags_local_unknown_win_loss_still_known():
    item = record()
    item["wager"]["price_source"] = "RESEARCH_QUOTE_ESTIMATE"
    assert project_recorded_wager(item)["net_eur"] is None
    item["outcome"] = item["settlement"]["outcome"] = "LOST"
    # This control isolates price-tag conflict, not contradictory sports evidence.
    for leg in item["settlement"]["legs"]:
        leg["outcome"] = "LOST"
    assert project_recorded_wager(item)["net_eur"] == -1


def test_malformed_provenance_is_not_healthy_legacy_confirmation():
    item = returned(record(void=True), 30)
    item["settlement"]["bookmaker_return"]["provenance_status"] = "INVALID_PROVENANCE"
    assert project_recorded_wager(item)["net_eur"] is None


def test_pure_inputs_not_mutated_and_invalid_cohort_shape_rejected():
    item = record()
    before = deepcopy(item)
    project_recorded_cohort([item])
    assert item == before
    with pytest.raises(TypeError):
        project_recorded_cohort(iter([item]))


def test_t32_typed_gross_below_stake_invalid_local_not_loss_or_net_cash():
    bad = returned(record(stake=1, void=True, key="bad"), 0.5, typed=True)
    value = project_recorded_wager(bad)
    assert value["status"] == "INVALID"
    assert value["reason"] == "TYPED_GROSS_BELOW_ORIGINAL_STAKE"
    assert value["payout_eur"] is value["net_eur"] is value["gross_sports_return_eur"] is None
    assert value["reported_return_eur"] == 0.5
    assert value["reported_return_arithmetic_net_eur"] == -0.5
    assert (value["stake_eur"], value["sports_outcome"]) == (1, "WON")
    assert value["bank_cash_receipt_eur"] is value["after_cost_system_net_eur"] is None
    assert value["payout_basis"] == GROSS  # Declared, rejected basis; never legacy or net cash.
    rows = [bad, record("LOST", stake=5, key="loss", source="RESEARCH_QUOTE_ESTIMATE"),
            record("VOID", stake=2, key="refund", source="RESEARCH_QUOTE_ESTIMATE"),
            record(price=2, key="valid")]
    cohort = project_recorded_cohort(rows)
    assert cohort["source_records"] == cohort["unique_wager_keys"] == 4
    assert cohort["stake_eur"] == 9
    assert cohort["net_eur"] is None
    assert cohort["known_subset_net_eur"] == -4
    assert cohort["known_closed_records"] == 3 and cohort["unknown_or_invalid_records"] == 1
    assert {row["ticket_id"]: row["net_eur"] for row in cohort["records"]} == {
        "bad": None, "loss": -5, "refund": 0, "valid": 1}
    assert cohort["missing_reasons"] == {"TYPED_GROSS_BELOW_ORIGINAL_STAKE": 1}
    for field, invalid in (("contract_version", "unknown-version"), ("fee_tax_inclusion", "INCLUDED")):
        wrong = returned(record(void=True), 30, typed=True)
        wrong["settlement"]["bookmaker_return"][field] = invalid
        invalid_result = project_recorded_wager(wrong)
        assert invalid_result["status"] == "INVALID"
        assert invalid_result["net_eur"] is invalid_result["gross_sports_return_eur"] is None
        assert invalid_result["reported_return_eur"] == 30
        assert invalid_result["payout_basis"] != LEGACY
