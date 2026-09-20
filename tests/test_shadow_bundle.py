import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from goal_analysis.agents import Role
from goal_analysis.jobs import complete_shadow_run, write_shadow_bundle
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

    def run(self, role: Role, payload: Mapping[str, Any]) -> Mapping[str, Any]:
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
