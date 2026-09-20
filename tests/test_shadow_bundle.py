import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from goal_analysis.agents import Role
from goal_analysis.engine import (
    CalibrationArtifact,
    CalibrationMetric,
    ExecutionBranch,
    LineupCheck,
    LineupSensitivity,
    LineupStatus,
    PriceMode,
    RunInput,
    RunMode,
    TeamNewsSnapshot,
)
from goal_analysis.jobs import (
    FixtureApprovalContext,
    ShadowControlContext,
    complete_controlled_shadow_run,
    complete_shadow_run,
    write_shadow_bundle,
)
from goal_analysis.normalization.models import OddsQuote

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)


def screening() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "target_date": "2026-09-20",
        "generated_at": "2026-09-20T08:00:00+00:00",
        "input_count": 1,
        "accepted": [{
            "fixture_id": "fixture-1", "competition_id": "DE1",
            "home_team": "Home", "away_team": "Away",
            "kickoff": "2026-09-20T15:00:00+00:00", "screening_score": 80.0,
            "feature_version": "goal-shortlist-v1",
            "features": {
                "evidence_from": "2025-10-01T12:00:00+00:00",
                "evidence_to": "2026-09-10T12:00:00+00:00",
                "home_sample": 10, "away_sample": 10, "coverage": 0.7,
                "average_total_goals": 3.1, "over_2_5_rate": 0.7, "btts_rate": 0.6,
                "home_goals_for": 1.9, "home_goals_against": 1.1,
                "away_goals_for": 1.5, "away_goals_against": 1.4,
            },
            "ranking": {"score": 80.0},
        }],
        "rejected": [],
    }


class Runner:
    def __init__(self, select: bool = True) -> None:
        self.select = select
        self.calls = 0

    def run(self, role: Role, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls += 1
        selected = role is not Role.ARTHUR or self.select
        return {
            "role": role.value,
            "fixture_id": payload["fixture_id"],
            "verdict": "support" if selected else "oppose",
            "confidence": 0.7,
            "thesis": "Evidence review.",
            "evidence_sha256": payload["evidence_sha256"],
            "structural_veto": False,
            "market_key": "totals_2_5" if role is Role.ARTHUR else None,
            "selection_key": "over" if role is Role.ARTHUR else None,
        }


class RecordingOdds:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], list[str]]] = []

    def get_quotes(self, fixture_ids, market_keys, observed_at):
        self.calls.append((list(fixture_ids), list(market_keys)))
        return [OddsQuote("fixture-1", "book-a", "totals_2_5", "over", 1.9, observed_at, "test")]


def test_end_to_end_shadow_attaches_odds_after_selection_and_writes_bundle(tmp_path) -> None:
    odds = RecordingOdds()
    bundle = complete_shadow_run(screening(), Runner(), odds, NOW)

    assert odds.calls == [(["fixture-1"], ["totals_2_5"])]
    assert bundle["real_wager_placed"] is False
    assert bundle["artifacts"]["ticket_gate"]["ticket_ready"] is True
    assert bundle["artifacts"]["ticket_gate"]["football_selection_order"] == ["fixture-1"]
    assert all(len(value) == 64 for value in bundle["artifact_sha256"].values())

    write_shadow_bundle(tmp_path, bundle)
    assert {path.name for path in tmp_path.iterdir()} == {
        "screening.json", "fact_packet.json", "kerekasztal.json",
        "ticket_gate.json", "token_usage.json", "manifest.json",
    }
    assert "artifacts" not in json.loads((tmp_path / "manifest.json").read_text("utf-8"))


def test_no_selection_skips_live_odds() -> None:
    odds = RecordingOdds()
    bundle = complete_shadow_run(screening(), Runner(select=False), odds, NOW)

    assert odds.calls == []
    assert bundle["artifacts"]["ticket_gate"]["ticket_ready"] is False


def control(fixtures=True, calibration=True) -> ShadowControlContext:
    run_input = RunInput(
        RunMode.PREMATCH,
        date(2026, 9, 20),
        "Europe/Berlin",
        NOW,
        ("DE1",),
        ("totals_2_5",),
        "book-a",
        "DE",
        "EUR",
        PriceMode.EXECUTABLE,
        model_artifact_id="artifact-v1",
        calibration_period="2025/2026",
    )
    model = CalibrationArtifact(
        "artifact-v1",
        "goals-model",
        "1.0",
        "output-1",
        "2025/2026",
        500,
        True,
        True,
        {CalibrationMetric.BRIER_SCORE: 0.19},
    ) if calibration else None
    news = TeamNewsSnapshot(NOW, ("official-news",), True, True, True, True, True)
    lineup = LineupCheck(
        LineupSensitivity.HIGH,
        LineupStatus.CONFIRMED,
        ExecutionBranch.WAIT_XI,
        NOW,
        primary_source_id="official-lineup",
    )
    contexts = {"fixture-1": FixtureApprovalContext(news, lineup)} if fixtures else {}
    return ShadowControlContext(run_input, model, contexts)


def test_controlled_shadow_persists_gates_and_allows_only_ready_fixture() -> None:
    runner = Runner()
    odds = RecordingOdds()
    bundle = complete_controlled_shadow_run(screening(), runner, odds, NOW, control())

    assert runner.calls == 7
    assert odds.calls == [(["fixture-1"], ["totals_2_5"])]
    assert bundle["schema_version"] == 2
    assert bundle["approval_blocked"] is False
    assert bundle["artifacts"]["run_control"]["fixture_gates"]["fixture-1"][
        "approval_allowed"
    ] is True
    assert bundle["real_wager_placed"] is False


def test_global_model_gate_stops_llm_and_odds_calls() -> None:
    runner = Runner()
    odds = RecordingOdds()
    bundle = complete_controlled_shadow_run(
        screening(), runner, odds, NOW, control(calibration=False)
    )

    assert runner.calls == 0
    assert odds.calls == []
    assert bundle["approval_blocked"] is True
    assert bundle["run_status"] == "MODEL_INPUT_REQUIRED"
    assert bundle["gate_issue_codes"] == ["MODEL_ARTIFACT_REQUIRED"]
    assert set(bundle["artifacts"]) == {"screening", "run_control"}


def test_missing_fixture_context_runs_analysis_but_blocks_price_request() -> None:
    runner = Runner()
    odds = RecordingOdds()
    bundle = complete_controlled_shadow_run(
        screening(), runner, odds, NOW, control(fixtures=False)
    )

    assert runner.calls == 7
    assert odds.calls == []
    assert bundle["approval_blocked"] is True
    assert bundle["gate_issue_codes"] == ["FIXTURE_APPROVAL_CONTEXT_REQUIRED"]
    final = bundle["artifacts"]["kerekasztal"]["fixtures"][0]["final"]
    assert final["arthur_selected"] is True
    assert final["selected"] is False
    assert final["approval_gate_blocked"] is True
