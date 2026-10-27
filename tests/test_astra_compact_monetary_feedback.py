"""Pure public feedback controls; no database/provider/reviewer constructors."""
import hashlib
import json
from copy import deepcopy

import pytest

from goal_analysis.jobs.portfolio import _compact_feedback
from goal_analysis.storage.monetary_projection import project_recorded_cohort


class Ledger:
    def __init__(self, source):
        self.source = source

    def feedback_summary(self):
        return self.source


def record(outcome="LOST", source="USER_CONFIRMED", key="a"):
    return {"ticket_id": key, "outcome": outcome,
            "wager": {"ticket_id": key, "stake_eur": 1, "actual_combined_price": 12.6,
                      "actual_price_source": source, "recorded_at": "2026-01-01T12:00:00Z"},
            "settlement": {"outcome": outcome, "settlement_hash": "a" * 64,
                           "observed_at": "2026-01-02T12:00:00Z", "legs": [{"outcome": outcome}]}}


def source_for(records):
    projection = project_recorded_cohort(records)
    fields = ("stake_eur", "settled_stake_eur", "payout_eur", "net_eur", "open_stake_eur", "cash_flow_eur",
              "yield_pct", "known_subset_net_eur", "bank_cash_receipt_eur", "after_cost_system_net_eur")
    summary = {key: projection[key] for key in fields}
    summary.update(tickets=len(records), won=sum(row["outcome"] == "WON" for row in records),
                   lost=sum(row["outcome"] == "LOST" for row in records), monetary_projection=projection)
    return {"interpretation": "Descriptive only", "actual": {"summary": summary,
            "by_profile": [{"profile_id": "DAILY_223", **deepcopy(summary)}]},
            "research": {"summary": {"tickets": 17}, "by_profile": []},
            "recommendations": {"summary": {"tickets": 23}, "by_profile": []}, "recent_settled": []}


def test_no_ledger_retains_original_none_path():
    assert _compact_feedback(None) is None


def test_nonmonetary_and_legacy_path_remains_original_shape():
    data = {"interpretation": "old", "actual": {"summary": {"tickets": 1}, "by_profile": []},
            "research": {"summary": {"tickets": 2}, "by_profile": []},
            "recommendations": {"summary": {"tickets": 3}, "by_profile": []}, "recent_settled": []}
    assert _compact_feedback(Ledger(data)) == data
    data["actual"]["summary"]["monetary_projection"] = {"monetary_contract": "LEGACY_RECORDED_V1", "records": [{"old": True}]}
    assert _compact_feedback(Ledger(data)) == data


@pytest.mark.parametrize(("outcome", "payout", "net"), [("LOST", 0, -1), ("VOID", 1, 0)])
def test_known_loss_and_full_void_amounts_are_not_recomputed(outcome, payout, net):
    data = source_for([record(outcome)])
    result = _compact_feedback(Ledger(data))
    summary = result["actual"]["summary"]
    assert (summary["payout_eur"], summary["net_eur"]) == (payout, net)
    assert summary["bank_cash_receipt_eur"] is summary["after_cost_system_net_eur"] is None
    assert summary["monetary_projection"]["verified_cash"] is False


def test_estimated_win_unknown_stays_unknown_and_has_coverage():
    data = source_for([record("WON", "RESEARCH_QUOTE_ESTIMATE")])
    result = _compact_feedback(Ledger(data))["actual"]["summary"]
    assert result["payout_eur"] is result["net_eur"] is None
    assert result["stake_eur"] == 1
    assert result["monetary_projection"]["unknown_or_invalid_records"] == 1
    assert result["monetary_projection"]["complete_financial_coverage"] is False
    assert result["monetary_projection"]["missing_reasons"] == data["actual"]["summary"]["monetary_projection"]["missing_reasons"]


def test_full_projection_hash_record_count_and_all_profiles_preserved_without_mutation():
    data = source_for([record(key="a"), record("VOID", key="b")])
    original = deepcopy(data)
    result = _compact_feedback(Ledger(data))
    for before, after in zip([data["actual"]["summary"], *data["actual"]["by_profile"]],
                             [result["actual"]["summary"], *result["actual"]["by_profile"]], strict=True):
        projection = before["monetary_projection"]
        expected = hashlib.sha256(json.dumps(projection, sort_keys=True, ensure_ascii=False,
                                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        assert after["monetary_projection"]["record_evidence"] == {"rows": 2, "source_projection_sha256": expected}
        assert after["tickets"] == before["tickets"] and after["net_eur"] == before["net_eur"]
        assert "records" not in after["monetary_projection"]
    assert result["research"] == data["research"] and result["recommendations"] == data["recommendations"]
    assert data == original


def test_large_record_details_use_full_source_reference_not_firstN():
    data = source_for([record(key="wager-" + str(index)) for index in range(150)])
    result = _compact_feedback(Ledger(data))
    assert len(json.dumps(result, ensure_ascii=False)) <= 12000
    assert result["actual"]["summary"]["monetary_projection"]["record_evidence"]["rows"] == 150
    assert result["actual"]["summary"]["monetary_projection"]["known_closed_records"] == 150
    assert result["actual"]["summary"]["net_eur"] == -150


@pytest.mark.parametrize("bad", ["version", "records", "currency", "verified_cash", "costs_included", "projected_records", "net_eur"])
def test_invalid_contract_is_local_unknown_and_never_zero_cash(bad):
    data = source_for([record()])
    projection = data["actual"]["summary"]["monetary_projection"]
    projection[bad] = {"version": "unsupported", "records": {}, "currency": "USD", "verified_cash": True,
                       "costs_included": True, "projected_records": True, "net_eur": False}[bad]
    result = _compact_feedback(Ledger(data))
    summary = result["actual"]["summary"]
    assert summary["monetary_projection"]["status"] == "UNAVAILABLE_INVALID_MONETARY_FEEDBACK_CONTRACT"
    assert summary["net_eur"] is summary["payout_eur"] is None
    assert summary["bank_cash_receipt_eur"] is summary["after_cost_system_net_eur"] is None
    assert result["research"] == data["research"]


def test_current_typed_gross_return_keeps_30_29_arithmetic_without_cash_claim():
    item = record("WON")
    item["settlement"]["legs"] = [{"outcome": "VOID"}, {"outcome": "WON"}]
    item["settlement"]["bookmaker_return"] = {
        "ticket_id": item["ticket_id"], "settlement_hash": "a" * 64, "payout_eur": 30,
        "recorded_at": "2026-01-03T12:00:00Z", "provenance_confirmed_at": "2026-01-03T12:00:00Z",
        "payout_basis": "BOOKMAKER_CONFIRMED_GROSS_SPORTS_RETURN", "fee_tax_inclusion": "EXCLUDED",
        "contract_version": "bookmaker-gross-return-v1"}
    data = source_for([item])
    assert data["actual"]["summary"]["monetary_projection"]["records"][0]["gross_sports_return_eur"] == 30
    result = _compact_feedback(Ledger(data))["actual"]["summary"]
    assert (result["payout_eur"], result["net_eur"]) == (30, 29)
    assert result["bank_cash_receipt_eur"] is result["after_cost_system_net_eur"] is None
    assert result["monetary_projection"]["costs_included"] is False


def test_oversized_descriptive_extension_is_explicit_optional_gap_not_global_veto():
    data = source_for([record()])
    data["recommendations"]["summary"]["diagnostic"] = "x" * 15000
    result = _compact_feedback(Ledger(data))
    assert result["status"] == "UNAVAILABLE_DESCRIPTIVE_FEEDBACK_BOUND"
    assert len(result["source_feedback_sha256"]) == 64
    assert result["bank_cash_receipt_eur"] is result["after_cost_system_net_eur"] is None
    assert "stake_eur" not in result and len(json.dumps(result, ensure_ascii=False)) < 12000


@pytest.mark.parametrize("location", ["projection", "parent", "other_section"])
def test_nonfinite_optional_metadata_never_re_raises_the_core_feedback_failure(location):
    data = source_for([record()])
    if location == "projection":
        data["actual"]["summary"]["monetary_projection"]["extra"] = float("nan")
    elif location == "parent":
        data["actual"]["summary"]["net_eur"] = float("nan")
    else:
        data["research"]["summary"]["extra"] = float("nan")
    result = _compact_feedback(Ledger(data))
    assert len(json.dumps(result, ensure_ascii=False, allow_nan=False)) <= 12000
    if location == "other_section":
        assert result["status"] == "UNAVAILABLE_DESCRIPTIVE_FEEDBACK_BOUND"
        assert result["source_feedback_sha256"] is None
    else:
        assert result["actual"]["summary"]["net_eur"] is None
    assert result.get("bank_cash_receipt_eur") is None
