from datetime import UTC, datetime, timedelta

from goal_analysis.engine import (
    CalibrationArtifact,
    CalibrationMetric,
    EvidenceStatus,
    ExecutionBranch,
    LineupCheck,
    LineupSensitivity,
    LineupStatus,
    TeamNewsSnapshot,
    evaluate_calibration,
    evaluate_lineup,
    evaluate_team_news,
)

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)


def calibrated() -> CalibrationArtifact:
    return CalibrationArtifact(
        artifact_id="artifact-v1",
        method_id="goals-model",
        method_version="1.0",
        output_id="frozen-output-1",
        calibration_period="2025-09-01/2026-08-31",
        sample_size=500,
        out_of_sample=True,
        time_backtested=True,
        metrics={CalibrationMetric.BRIER_SCORE: 0.19},
    )


def news(age_hours=1, **changes) -> TeamNewsSnapshot:
    values = {
        "checked_at": NOW - timedelta(hours=age_hours),
        "source_ids": ("club-news-1",),
        "injuries_checked": True,
        "suspensions_checked": True,
        "rotation_checked": True,
        "workload_checked": True,
        "coach_and_tactics_checked": True,
    }
    values.update(changes)
    return TeamNewsSnapshot(**values)


def test_calibrated_time_backtested_artifact_opens_value_gate() -> None:
    result = evaluate_calibration(calibrated())
    assert result.ready_for_probability is True
    assert result.ready_for_value is True
    assert result.issue_codes == ()


def test_missing_or_narrative_only_model_fails_closed() -> None:
    assert evaluate_calibration(None).ready_for_value is False
    artifact = calibrated()
    invalid = CalibrationArtifact(
        artifact.artifact_id,
        artifact.method_id,
        artifact.method_version,
        artifact.output_id,
        artifact.calibration_period,
        artifact.sample_size,
        False,
        False,
        {},
    )
    result = evaluate_calibration(invalid)
    assert result.ready_for_value is False
    assert "OUT_OF_SAMPLE_CALIBRATION_REQUIRED" in result.issue_codes
    assert "TIME_BACKTEST_REQUIRED" in result.issue_codes


def test_invalid_calibration_metric_fails_closed() -> None:
    artifact = calibrated()
    invalid = CalibrationArtifact(
        artifact.artifact_id,
        artifact.method_id,
        artifact.method_version,
        artifact.output_id,
        artifact.calibration_period,
        artifact.sample_size,
        artifact.out_of_sample,
        artifact.time_backtested,
        {CalibrationMetric.BRIER_SCORE: float("nan")},
    )
    result = evaluate_calibration(invalid)
    assert result.ready_for_value is False
    assert result.issue_codes == ("INVALID_CALIBRATION_METRIC",)


def test_team_news_is_complete_at_exact_48_hour_boundary() -> None:
    result = evaluate_team_news(news(age_hours=48), NOW)
    assert result.evidence_status is EvidenceStatus.COMPLETE
    assert result.approval_allowed is True


def test_stale_or_incompletely_checked_team_news_is_incomplete() -> None:
    stale = evaluate_team_news(news(age_hours=49), NOW)
    incomplete = evaluate_team_news(news(rotation_checked=False), NOW)
    assert stale.evidence_status is EvidenceStatus.INCOMPLETE
    assert "TEAM_NEWS_STALE" in stale.issue_codes
    assert "ROTATION_NOT_CHECKED" in incomplete.issue_codes


def test_low_sensitivity_early_bet_needs_documented_rotation_robustness() -> None:
    base = {
        "sensitivity": LineupSensitivity.LOW,
        "status": LineupStatus.PENDING,
        "execution_branch": ExecutionBranch.EARLY_BET,
        "checked_at": NOW,
    }
    blocked = evaluate_lineup(LineupCheck(**base), NOW, team_news_ready=True)
    allowed = evaluate_lineup(
        LineupCheck(**base, rotation_robustness_documented=True),
        NOW,
        team_news_ready=True,
    )
    assert blocked.evidence_status is EvidenceStatus.INCOMPLETE
    assert allowed.evidence_status is EvidenceStatus.COMPLETE
    assert allowed.approval_allowed is True


def test_high_sensitivity_cannot_use_early_bet() -> None:
    check = LineupCheck(
        LineupSensitivity.HIGH,
        LineupStatus.PENDING,
        ExecutionBranch.EARLY_BET,
        NOW,
        rotation_robustness_documented=True,
    )
    result = evaluate_lineup(check, NOW, team_news_ready=True)
    assert result.evidence_status is EvidenceStatus.INCOMPLETE
    assert result.issue_codes == ("EARLY_BET_REQUIRES_LOW_SENSITIVITY",)


def test_wait_xi_pending_is_conditional_only_with_controls() -> None:
    base = {
        "sensitivity": LineupSensitivity.HIGH,
        "status": LineupStatus.PENDING,
        "execution_branch": ExecutionBranch.WAIT_XI,
        "checked_at": NOW,
    }
    incomplete = evaluate_lineup(LineupCheck(**base), NOW, team_news_ready=True)
    conditional = evaluate_lineup(
        LineupCheck(**base, conditional_controls_recorded=True), NOW, team_news_ready=True
    )
    assert incomplete.evidence_status is EvidenceStatus.INCOMPLETE
    assert conditional.evidence_status is EvidenceStatus.CONDITIONAL
    assert conditional.approval_allowed is False


def test_confirmed_lineup_requires_primary_source() -> None:
    check = LineupCheck(
        LineupSensitivity.HIGH,
        LineupStatus.CONFIRMED,
        ExecutionBranch.WAIT_XI,
        NOW,
    )
    assert evaluate_lineup(check, NOW, team_news_ready=True).approval_allowed is False
    confirmed = LineupCheck(
        LineupSensitivity.HIGH,
        LineupStatus.CONFIRMED,
        ExecutionBranch.WAIT_XI,
        NOW,
        primary_source_id="official-club-lineup",
    )
    assert evaluate_lineup(confirmed, NOW, team_news_ready=True).approval_allowed is True


def test_lineup_cannot_bypass_team_news_gate() -> None:
    check = LineupCheck(
        LineupSensitivity.LOW,
        LineupStatus.CONFIRMED,
        ExecutionBranch.WAIT_XI,
        NOW,
        primary_source_id="official-league-lineup",
    )
    result = evaluate_lineup(check, NOW, team_news_ready=False)
    assert result.evidence_status is EvidenceStatus.INCOMPLETE
    assert result.issue_codes == ("TEAM_NEWS_GATE_NOT_READY",)
