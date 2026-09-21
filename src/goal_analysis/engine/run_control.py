from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class RunMode(StrEnum):
    PREMATCH = "PREMATCH"
    FINALIZE = "FINALIZE"
    AUDIT = "AUDIT"


class RunStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL_OUTPUT = "PARTIAL_OUTPUT"
    INPUT_REQUIRED = "INPUT_REQUIRED"
    MODEL_INPUT_REQUIRED = "MODEL_INPUT_REQUIRED"
    DATA_ACCESS_BLOCKED = "DATA_ACCESS_BLOCKED"
    BLOCKED_VALIDATION = "BLOCKED_VALIDATION"


class PriceMode(StrEnum):
    EXECUTABLE = "EXECUTABLE"
    REFERENCE_ONLY = "REFERENCE_ONLY"


class EvidenceStatus(StrEnum):
    COMPLETE = "COMPLETE"
    CONDITIONAL = "CONDITIONAL"
    INCOMPLETE = "INCOMPLETE"
    CONFLICTED = "CONFLICTED"


class FootballStatus(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    UNRESOLVED = "UNRESOLVED"
    PASS = "PASS"
    VETO = "VETO"


class PriceStatus(StrEnum):
    NOT_CHECKED = "NOT_CHECKED"
    VALUE_OK = "VALUE_OK"
    MARGINAL = "MARGINAL"
    NO_VALUE = "NO_VALUE"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class FinalStatus(StrEnum):
    SCREENED_OUT = "SCREENED_OUT"
    INCOMPLETE = "INCOMPLETE"
    VETO = "VETO"
    CONDITIONAL = "CONDITIONAL"
    NOT_APPROVED = "NOT_APPROVED"
    REJECTED_NO_VALUE = "REJECTED_NO_VALUE"
    APPROVED = "APPROVED"
    WITHDRAWN = "WITHDRAWN"


class ExecutionStatus(StrEnum):
    NOT_PLACED = "NOT_PLACED"
    PLACED = "PLACED"
    SIMULATED = "SIMULATED"


@dataclass(frozen=True, slots=True)
class RunInput:
    mode: RunMode
    analyzed_date: date
    timezone_name: str
    decision_as_of: datetime
    competitions: tuple[str, ...]
    markets: tuple[str, ...]
    bookmaker: str
    account_region: str
    currency: str
    price_mode: PriceMode
    parent_run_id: str | None = None
    audit_as_of: datetime | None = None
    model_artifact_id: str | None = None
    calibration_period: str | None = None


@dataclass(frozen=True, slots=True)
class RunGateResult:
    status: RunStatus
    issue_codes: tuple[str, ...]
    approval_allowed: bool


@dataclass(frozen=True, slots=True)
class CandidateState:
    evidence: EvidenceStatus
    football: FootballStatus
    price: PriceStatus
    execution: ExecutionStatus = ExecutionStatus.NOT_PLACED
    previous_final: FinalStatus | None = None
    invalidated_by_new_data: bool = False
    screened_out: bool = False
    conditional_controls_recorded: bool = False


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    final_status: FinalStatus
    reason_code: str
    post_placement_risk_changed: bool = False


def evaluate_run_gate(
    run_input: RunInput,
    *,
    data_accessible: bool = True,
    output_complete: bool = True,
) -> RunGateResult:
    contradictions, missing = _validate_run_input(run_input)
    if contradictions:
        return RunGateResult(RunStatus.BLOCKED_VALIDATION, tuple(contradictions), False)
    if missing:
        return RunGateResult(RunStatus.INPUT_REQUIRED, tuple(missing), False)
    if run_input.price_mode is PriceMode.EXECUTABLE and (
        not run_input.model_artifact_id or not run_input.calibration_period
    ):
        issues = []
        if not run_input.model_artifact_id:
            issues.append("MODEL_ARTIFACT_REQUIRED")
        if not run_input.calibration_period:
            issues.append("CALIBRATION_PERIOD_REQUIRED")
        return RunGateResult(RunStatus.MODEL_INPUT_REQUIRED, tuple(issues), False)
    if not data_accessible:
        return RunGateResult(RunStatus.DATA_ACCESS_BLOCKED, ("REQUIRED_DATA_UNAVAILABLE",), False)
    if not output_complete:
        return RunGateResult(RunStatus.PARTIAL_OUTPUT, ("OUTPUT_INCOMPLETE",), False)
    return RunGateResult(RunStatus.COMPLETE, (), run_input.price_mode is PriceMode.EXECUTABLE)


def decide_candidate(state: CandidateState) -> CandidateDecision:
    if state.invalidated_by_new_data and state.execution is ExecutionStatus.NOT_PLACED:
        return CandidateDecision(FinalStatus.WITHDRAWN, "NEW_DATA_INVALIDATED_DECISION")
    if state.invalidated_by_new_data and state.execution is ExecutionStatus.PLACED:
        preserved = state.previous_final or FinalStatus.INCOMPLETE
        return CandidateDecision(preserved, "POST_PLACEMENT_RISK_CHANGED", True)
    if state.screened_out:
        return CandidateDecision(FinalStatus.SCREENED_OUT, "SCREENING_EXCLUDED")
    if state.football is FootballStatus.VETO:
        return CandidateDecision(FinalStatus.VETO, "ACTIVE_STRUCTURAL_VETO")
    if state.evidence in {EvidenceStatus.INCOMPLETE, EvidenceStatus.CONFLICTED}:
        return CandidateDecision(FinalStatus.INCOMPLETE, "EVIDENCE_NOT_COMPLETE")
    if state.evidence is EvidenceStatus.CONDITIONAL:
        if state.conditional_controls_recorded:
            return CandidateDecision(FinalStatus.CONDITIONAL, "PENDING_NAMED_CONDITION")
        return CandidateDecision(FinalStatus.INCOMPLETE, "CONDITIONAL_CONTROLS_MISSING")
    if state.football is FootballStatus.NOT_EVALUATED:
        return CandidateDecision(FinalStatus.INCOMPLETE, "FOOTBALL_NOT_EVALUATED")
    if state.football is FootballStatus.UNRESOLVED:
        return CandidateDecision(FinalStatus.NOT_APPROVED, "FOOTBALL_UNRESOLVED")
    if state.price is PriceStatus.VALUE_OK:
        return CandidateDecision(FinalStatus.APPROVED, "COMPLETE_PASS_VALUE_OK")
    if state.price in {PriceStatus.NO_VALUE, PriceStatus.MARGINAL}:
        if state.price is PriceStatus.MARGINAL and state.conditional_controls_recorded:
            return CandidateDecision(FinalStatus.CONDITIONAL, "MARGINAL_PRICE_RECHECK")
        return CandidateDecision(FinalStatus.REJECTED_NO_VALUE, "PRICE_GATE_FAILED")
    if state.price in {PriceStatus.STALE, PriceStatus.UNAVAILABLE}:
        if state.conditional_controls_recorded:
            return CandidateDecision(FinalStatus.CONDITIONAL, "PRICE_RECHECK_REQUIRED")
        return CandidateDecision(FinalStatus.INCOMPLETE, "PRICE_NOT_VERIFIABLE")
    return CandidateDecision(FinalStatus.INCOMPLETE, "PRICE_NOT_CHECKED")


def _validate_run_input(run_input: RunInput) -> tuple[list[str], list[str]]:
    contradictions: list[str] = []
    missing: list[str] = []
    if run_input.decision_as_of.tzinfo is None or run_input.decision_as_of.utcoffset() is None:
        contradictions.append("DECISION_AS_OF_NOT_TIMEZONE_AWARE")
    if run_input.audit_as_of is not None and (
        run_input.audit_as_of.tzinfo is None or run_input.audit_as_of.utcoffset() is None
    ):
        contradictions.append("AUDIT_AS_OF_NOT_TIMEZONE_AWARE")
    try:
        ZoneInfo(run_input.timezone_name)
    except ZoneInfoNotFoundError:
        missing.append("TIMEZONE_INVALID")
    if not run_input.competitions:
        missing.append("COMPETITIONS_REQUIRED")
    if not run_input.markets:
        missing.append("MARKETS_REQUIRED")
    for value, code in (
        (run_input.bookmaker, "BOOKMAKER_REQUIRED"),
        (run_input.account_region, "ACCOUNT_REGION_REQUIRED"),
        (run_input.currency, "CURRENCY_REQUIRED"),
    ):
        if not value.strip():
            missing.append(code)
    if run_input.mode is RunMode.PREMATCH:
        if run_input.parent_run_id is not None:
            contradictions.append("PREMATCH_PARENT_FORBIDDEN")
        if run_input.audit_as_of is not None:
            contradictions.append("PREMATCH_AUDIT_AS_OF_FORBIDDEN")
    elif run_input.mode is RunMode.FINALIZE:
        if not run_input.parent_run_id:
            missing.append("FINALIZE_PARENT_REQUIRED")
        if run_input.audit_as_of is not None:
            contradictions.append("FINALIZE_AUDIT_AS_OF_FORBIDDEN")
    elif run_input.mode is RunMode.AUDIT:
        if not run_input.parent_run_id:
            missing.append("AUDIT_PARENT_REQUIRED")
        if run_input.audit_as_of is None:
            missing.append("AUDIT_AS_OF_REQUIRED")
        elif (
            run_input.decision_as_of.tzinfo is not None
            and run_input.audit_as_of.tzinfo is not None
            and run_input.audit_as_of < run_input.decision_as_of
        ):
            contradictions.append("AUDIT_BEFORE_DECISION")
    return contradictions, missing
