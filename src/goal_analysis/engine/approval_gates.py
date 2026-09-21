from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite

from .run_control import EvidenceStatus


class CalibrationMetric(StrEnum):
    BRIER_SCORE = "brier_score"
    LOG_LOSS = "log_loss"


class LineupSensitivity(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class LineupStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    UNAVAILABLE = "UNAVAILABLE"


class ExecutionBranch(StrEnum):
    EARLY_BET = "EARLY_BET"
    WAIT_XI = "WAIT_XI"


@dataclass(frozen=True, slots=True)
class CalibrationArtifact:
    artifact_id: str
    method_id: str
    method_version: str
    output_id: str
    calibration_period: str
    sample_size: int
    out_of_sample: bool
    time_backtested: bool
    metrics: dict[CalibrationMetric, float]


@dataclass(frozen=True, slots=True)
class CalibrationGateResult:
    ready_for_probability: bool
    ready_for_value: bool
    issue_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TeamNewsSnapshot:
    checked_at: datetime
    source_ids: tuple[str, ...]
    injuries_checked: bool
    suspensions_checked: bool
    rotation_checked: bool
    workload_checked: bool
    coach_and_tactics_checked: bool


@dataclass(frozen=True, slots=True)
class EvidenceGateResult:
    evidence_status: EvidenceStatus
    issue_codes: tuple[str, ...]
    approval_allowed: bool


@dataclass(frozen=True, slots=True)
class LineupCheck:
    sensitivity: LineupSensitivity
    status: LineupStatus
    execution_branch: ExecutionBranch
    checked_at: datetime
    primary_source_id: str | None = None
    rotation_robustness_documented: bool = False
    conditional_controls_recorded: bool = False


def evaluate_calibration(artifact: CalibrationArtifact | None) -> CalibrationGateResult:
    if artifact is None:
        return CalibrationGateResult(False, False, ("MODEL_ARTIFACT_REQUIRED",))
    issues: list[str] = []
    for value, code in (
        (artifact.artifact_id, "ARTIFACT_ID_REQUIRED"),
        (artifact.method_id, "METHOD_ID_REQUIRED"),
        (artifact.method_version, "METHOD_VERSION_REQUIRED"),
        (artifact.output_id, "FROZEN_OUTPUT_ID_REQUIRED"),
        (artifact.calibration_period, "CALIBRATION_PERIOD_REQUIRED"),
    ):
        if not value.strip():
            issues.append(code)
    if artifact.sample_size <= 0:
        issues.append("CALIBRATION_SAMPLE_REQUIRED")
    if not artifact.out_of_sample:
        issues.append("OUT_OF_SAMPLE_CALIBRATION_REQUIRED")
    if not artifact.time_backtested:
        issues.append("TIME_BACKTEST_REQUIRED")
    if not artifact.metrics:
        issues.append("CALIBRATION_METRIC_REQUIRED")
    for metric, value in artifact.metrics.items():
        if metric not in {CalibrationMetric.BRIER_SCORE, CalibrationMetric.LOG_LOSS}:
            issues.append("UNSUPPORTED_CALIBRATION_METRIC")
        if not isfinite(value) or value < 0:
            issues.append("INVALID_CALIBRATION_METRIC")
        if metric is CalibrationMetric.BRIER_SCORE and value > 1:
            issues.append("INVALID_CALIBRATION_METRIC")
    ready = not issues
    return CalibrationGateResult(ready, ready, tuple(dict.fromkeys(issues)))


def evaluate_team_news(
    snapshot: TeamNewsSnapshot | None,
    observed_at: datetime,
    maximum_age_hours: int = 48,
) -> EvidenceGateResult:
    if maximum_age_hours < 0:
        raise ValueError("maximum_age_hours must not be negative")
    if snapshot is None:
        return EvidenceGateResult(
            EvidenceStatus.INCOMPLETE, ("TEAM_NEWS_REQUIRED",), False
        )
    _require_aware(observed_at, "observed_at")
    _require_aware(snapshot.checked_at, "team-news checked_at")
    issues: list[str] = []
    age_seconds = (observed_at - snapshot.checked_at).total_seconds()
    if age_seconds < 0:
        issues.append("TEAM_NEWS_FROM_FUTURE")
    if age_seconds > maximum_age_hours * 3600:
        issues.append("TEAM_NEWS_STALE")
    if not snapshot.source_ids:
        issues.append("TEAM_NEWS_SOURCE_REQUIRED")
    coverage = {
        "INJURIES_NOT_CHECKED": snapshot.injuries_checked,
        "SUSPENSIONS_NOT_CHECKED": snapshot.suspensions_checked,
        "ROTATION_NOT_CHECKED": snapshot.rotation_checked,
        "WORKLOAD_NOT_CHECKED": snapshot.workload_checked,
        "COACH_TACTICS_NOT_CHECKED": snapshot.coach_and_tactics_checked,
    }
    issues.extend(code for code, checked in coverage.items() if not checked)
    status = EvidenceStatus.COMPLETE if not issues else EvidenceStatus.INCOMPLETE
    return EvidenceGateResult(status, tuple(issues), not issues)


def evaluate_lineup(
    check: LineupCheck,
    observed_at: datetime,
    *,
    team_news_ready: bool,
) -> EvidenceGateResult:
    _require_aware(observed_at, "observed_at")
    _require_aware(check.checked_at, "lineup checked_at")
    if check.checked_at > observed_at:
        return EvidenceGateResult(
            EvidenceStatus.INCOMPLETE, ("LINEUP_CHECK_FROM_FUTURE",), False
        )
    if not team_news_ready:
        return EvidenceGateResult(
            EvidenceStatus.INCOMPLETE, ("TEAM_NEWS_GATE_NOT_READY",), False
        )
    if check.execution_branch is ExecutionBranch.EARLY_BET:
        if check.sensitivity is not LineupSensitivity.LOW:
            return EvidenceGateResult(
                EvidenceStatus.INCOMPLETE, ("EARLY_BET_REQUIRES_LOW_SENSITIVITY",), False
            )
        if not check.rotation_robustness_documented:
            return EvidenceGateResult(
                EvidenceStatus.INCOMPLETE, ("ROTATION_ROBUSTNESS_REQUIRED",), False
            )
        return EvidenceGateResult(EvidenceStatus.COMPLETE, (), True)
    if check.status is LineupStatus.CONFIRMED:
        if not check.primary_source_id:
            return EvidenceGateResult(
                EvidenceStatus.INCOMPLETE, ("PRIMARY_LINEUP_SOURCE_REQUIRED",), False
            )
        return EvidenceGateResult(EvidenceStatus.COMPLETE, (), True)
    if check.status in {LineupStatus.PENDING, LineupStatus.UNAVAILABLE}:
        if check.conditional_controls_recorded:
            return EvidenceGateResult(
                EvidenceStatus.CONDITIONAL, ("OFFICIAL_LINEUP_RECHECK_REQUIRED",), False
            )
        return EvidenceGateResult(
            EvidenceStatus.INCOMPLETE, ("LINEUP_CONDITIONAL_CONTROLS_MISSING",), False
        )
    return EvidenceGateResult(EvidenceStatus.INCOMPLETE, ("LINEUP_STATUS_INVALID",), False)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
