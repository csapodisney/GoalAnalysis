from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from goal_analysis.agents import RoleRunner, canonical_sha256
from goal_analysis.engine import (
    CalibrationArtifact,
    EvidenceStatus,
    LineupCheck,
    RunInput,
    RunStatus,
    TeamNewsSnapshot,
    TicketGatePolicy,
    evaluate_calibration,
    evaluate_lineup,
    evaluate_run_gate,
    evaluate_team_news,
)
from goal_analysis.providers import OddsProvider

from .shadow import complete_shadow_run


@dataclass(frozen=True, slots=True)
class FixtureApprovalContext:
    team_news: TeamNewsSnapshot | None
    lineup: LineupCheck


@dataclass(frozen=True, slots=True)
class ShadowControlContext:
    run_input: RunInput
    calibration: CalibrationArtifact | None
    fixtures: Mapping[str, FixtureApprovalContext]
    maximum_team_news_age_hours: int = 48


def complete_controlled_shadow_run(
    screening: Mapping[str, Any],
    role_runner: RoleRunner,
    odds_provider: OddsProvider,
    observed_at: datetime,
    control: ShadowControlContext,
    gate_policy: TicketGatePolicy | None = None,
) -> dict[str, Any]:
    run_gate = evaluate_run_gate(control.run_input)
    calibration_gate = evaluate_calibration(control.calibration)
    run_control = {
        "run_gate": _gate_dict(run_gate),
        "calibration_gate": _gate_dict(calibration_gate),
        "fixture_gates": {},
    }
    global_issues = [*run_gate.issue_codes, *calibration_gate.issue_codes]
    if run_gate.status is not RunStatus.COMPLETE or not calibration_gate.ready_for_value:
        effective_status = (
            run_gate.status
            if run_gate.status is not RunStatus.COMPLETE
            else RunStatus.MODEL_INPUT_REQUIRED
        )
        run_control["effective_run_status"] = effective_status.value
        return _blocked_bundle(
            screening, observed_at, run_control, global_issues, effective_status
        )

    eligible: set[str] = set()
    for fixture in screening.get("accepted", []):
        fixture_id = str(fixture["fixture_id"])
        context = control.fixtures.get(fixture_id)
        if context is None:
            run_control["fixture_gates"][fixture_id] = {
                "approval_allowed": False,
                "evidence_status": EvidenceStatus.INCOMPLETE.value,
                "issue_codes": ["FIXTURE_APPROVAL_CONTEXT_REQUIRED"],
            }
            continue
        news = evaluate_team_news(
            context.team_news,
            observed_at,
            control.maximum_team_news_age_hours,
        )
        lineup = evaluate_lineup(
            context.lineup,
            observed_at,
            team_news_ready=news.approval_allowed,
        )
        allowed = news.approval_allowed and lineup.approval_allowed
        run_control["fixture_gates"][fixture_id] = {
            "approval_allowed": allowed,
            "team_news": _gate_dict(news),
            "lineup": _gate_dict(lineup),
        }
        if allowed:
            eligible.add(fixture_id)

    bundle = complete_shadow_run(
        screening,
        role_runner,
        odds_provider,
        observed_at,
        gate_policy,
        eligible_fixture_ids=eligible,
    )
    bundle["schema_version"] = 2
    bundle["run_status"] = run_gate.status.value
    bundle["approval_blocked"] = not bool(eligible)
    bundle["gate_issue_codes"] = sorted(
        {
            code
            for gate in run_control["fixture_gates"].values()
            for section in (gate.get("team_news", gate), gate.get("lineup", {}))
            for code in section.get("issue_codes", [])
        }
    )
    bundle["artifacts"]["run_control"] = run_control
    bundle["artifact_sha256"]["run_control"] = canonical_sha256(run_control)
    return bundle


def _blocked_bundle(
    screening: Mapping[str, Any],
    observed_at: datetime,
    run_control: Mapping[str, Any],
    issue_codes: list[str],
    effective_status: RunStatus,
) -> dict[str, Any]:
    artifacts = {"screening": dict(screening), "run_control": dict(run_control)}
    return {
        "schema_version": 2,
        "run_type": "end_to_end_shadow",
        "observed_at": observed_at.isoformat(),
        "run_status": effective_status.value,
        "approval_blocked": True,
        "gate_issue_codes": sorted(set(issue_codes)),
        "real_wager_placed": False,
        "artifacts": artifacts,
        "artifact_sha256": {
            name: canonical_sha256(payload) for name, payload in artifacts.items()
        },
    }


def _gate_dict(gate: Any) -> dict[str, Any]:
    payload = asdict(gate)
    for key, value in tuple(payload.items()):
        if hasattr(value, "value"):
            payload[key] = value.value
        elif isinstance(value, tuple):
            payload[key] = list(value)
    return payload
