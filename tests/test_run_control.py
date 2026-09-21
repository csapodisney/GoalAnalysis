from datetime import UTC, date, datetime

import pytest

from goal_analysis.engine import (
    CandidateState,
    EvidenceStatus,
    ExecutionStatus,
    FinalStatus,
    FootballStatus,
    PriceMode,
    PriceStatus,
    RunInput,
    RunMode,
    RunStatus,
    decide_candidate,
    evaluate_run_gate,
)

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)


def run_input(mode=RunMode.PREMATCH, **changes) -> RunInput:
    values = {
        "mode": mode,
        "analyzed_date": date(2026, 9, 20),
        "timezone_name": "Europe/Berlin",
        "decision_as_of": NOW,
        "competitions": ("DE1",),
        "markets": ("totals_2_5",),
        "bookmaker": "Tipico",
        "account_region": "DE",
        "currency": "EUR",
        "price_mode": PriceMode.EXECUTABLE,
        "model_artifact_id": "model-v1",
        "calibration_period": "2025-09-01/2026-08-31",
    }
    values.update(changes)
    return RunInput(**values)


def test_complete_prematch_allows_approval_path() -> None:
    result = evaluate_run_gate(run_input())
    assert result.status is RunStatus.COMPLETE
    assert result.approval_allowed is True


def test_missing_model_blocks_executable_approval() -> None:
    result = evaluate_run_gate(run_input(model_artifact_id=None, calibration_period=None))
    assert result.status is RunStatus.MODEL_INPUT_REQUIRED
    assert result.approval_allowed is False


@pytest.mark.parametrize(
    ("mode", "changes", "expected"),
    [
        (RunMode.FINALIZE, {}, RunStatus.INPUT_REQUIRED),
        (RunMode.AUDIT, {"parent_run_id": "run-1"}, RunStatus.INPUT_REQUIRED),
        (
            RunMode.PREMATCH,
            {"parent_run_id": "run-1", "audit_as_of": NOW},
            RunStatus.BLOCKED_VALIDATION,
        ),
    ],
)
def test_modes_cannot_mix_required_fields(mode, changes, expected) -> None:
    assert evaluate_run_gate(run_input(mode, **changes)).status is expected


def test_audit_requires_parent_and_later_audit_timestamp() -> None:
    valid = run_input(
        RunMode.AUDIT,
        parent_run_id="run-1",
        audit_as_of=datetime(2026, 9, 21, 10, 0, tzinfo=UTC),
    )
    assert evaluate_run_gate(valid).status is RunStatus.COMPLETE
    invalid = run_input(RunMode.AUDIT, parent_run_id="run-1", audit_as_of=NOW.replace(hour=9))
    assert evaluate_run_gate(invalid).status is RunStatus.BLOCKED_VALIDATION


def test_data_and_output_gates_are_explicit() -> None:
    assert evaluate_run_gate(run_input(), data_accessible=False).status is RunStatus.DATA_ACCESS_BLOCKED
    assert evaluate_run_gate(run_input(), output_complete=False).status is RunStatus.PARTIAL_OUTPUT


def test_candidate_precedence_is_fail_closed() -> None:
    incomplete = CandidateState(EvidenceStatus.INCOMPLETE, FootballStatus.PASS, PriceStatus.VALUE_OK)
    veto = CandidateState(EvidenceStatus.COMPLETE, FootballStatus.VETO, PriceStatus.VALUE_OK)
    assert decide_candidate(incomplete).final_status is FinalStatus.INCOMPLETE
    assert decide_candidate(veto).final_status is FinalStatus.VETO


def test_approval_requires_complete_pass_and_value_ok() -> None:
    state = CandidateState(EvidenceStatus.COMPLETE, FootballStatus.PASS, PriceStatus.VALUE_OK)
    assert decide_candidate(state).final_status is FinalStatus.APPROVED
    assert decide_candidate(
        CandidateState(EvidenceStatus.COMPLETE, FootballStatus.PASS, PriceStatus.NO_VALUE)
    ).final_status is FinalStatus.REJECTED_NO_VALUE


def test_conditional_needs_named_recheck_controls() -> None:
    without_controls = CandidateState(
        EvidenceStatus.CONDITIONAL, FootballStatus.PASS, PriceStatus.VALUE_OK
    )
    with_controls = CandidateState(
        EvidenceStatus.CONDITIONAL,
        FootballStatus.PASS,
        PriceStatus.VALUE_OK,
        conditional_controls_recorded=True,
    )
    assert decide_candidate(without_controls).final_status is FinalStatus.INCOMPLETE
    assert decide_candidate(with_controls).final_status is FinalStatus.CONDITIONAL


def test_new_data_withdraws_only_unplaced_decision() -> None:
    unplaced = CandidateState(
        EvidenceStatus.COMPLETE,
        FootballStatus.PASS,
        PriceStatus.VALUE_OK,
        previous_final=FinalStatus.APPROVED,
        invalidated_by_new_data=True,
    )
    placed = CandidateState(
        EvidenceStatus.COMPLETE,
        FootballStatus.PASS,
        PriceStatus.VALUE_OK,
        execution=ExecutionStatus.PLACED,
        previous_final=FinalStatus.APPROVED,
        invalidated_by_new_data=True,
    )
    assert decide_candidate(unplaced).final_status is FinalStatus.WITHDRAWN
    assert decide_candidate(placed).final_status is FinalStatus.APPROVED
    assert decide_candidate(placed).post_placement_risk_changed is True
