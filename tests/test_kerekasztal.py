from collections.abc import Mapping
from typing import Any

import pytest

from goal_analysis.agents import (
    KerekasztalError,
    KerekasztalOrchestrator,
    Role,
    build_fact_packet,
)


def fact_packet() -> dict[str, Any]:
    screening = {
        "schema_version": 1,
        "target_date": "2026-09-20",
        "generated_at": "2026-09-20T08:00:00+00:00",
        "input_count": 1,
        "accepted": [
            {
                "fixture_id": "fixture-1",
                "competition_id": "DE1",
                "home_team": "Home",
                "away_team": "Away",
                "kickoff": "2026-09-20T15:00:00+00:00",
                "screening_score": 80.0,
                "feature_version": "goal-shortlist-v1",
                "features": {
                    "evidence_from": "2025-10-01T12:00:00+00:00",
                    "evidence_to": "2026-09-10T12:00:00+00:00",
                    "home_sample": 10,
                    "away_sample": 10,
                    "coverage": 0.7,
                    "average_total_goals": 3.1,
                    "over_2_5_rate": 0.7,
                    "btts_rate": 0.6,
                    "home_goals_for": 1.9,
                    "home_goals_against": 1.1,
                    "away_goals_for": 1.5,
                    "away_goals_against": 1.4,
                },
                "ranking": {"score": 80.0},
            }
        ],
        "rejected": [],
    }
    return build_fact_packet(screening)


class RecordingRunner:
    def __init__(self, veto_role: Role | None = None) -> None:
        self.veto_role = veto_role
        self.calls: list[tuple[Role, Mapping[str, Any]]] = []

    def run(self, role: Role, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((role, payload))
        veto = role is self.veto_role
        return {
            "role": role.value,
            "fixture_id": payload["fixture_id"],
            "verdict": "veto" if veto else "support",
            "confidence": 0.7,
            "thesis": "Structured evidence review.",
            "evidence_sha256": payload["evidence_sha256"],
            "structural_veto": veto,
        }


def test_roles_run_in_required_order_and_arthur_decides_last() -> None:
    runner = RecordingRunner()
    result = KerekasztalOrchestrator(runner).run(fact_packet())

    assert [role for role, _ in runner.calls] == [
        Role.KRONIKAS,
        Role.RITMUSOR,
        Role.PARHARCMESTER,
        Role.ORSZEM,
        Role.MERLIN,
        Role.DANIEL,
        Role.ARTHUR,
    ]
    assert result["fixtures"][0]["final"]["selected"] is True
    assert result["prices_attached"] is False


def test_specialists_are_independent_but_daniel_sees_all_views() -> None:
    runner = RecordingRunner()
    KerekasztalOrchestrator(runner).run(fact_packet())

    for _, payload in runner.calls[:5]:
        assert "specialist_opinions" not in payload
    assert len(runner.calls[5][1]["specialist_opinions"]) == 5
    assert "daniel" in runner.calls[6][1]


@pytest.mark.parametrize("veto_role", [Role.ORSZEM, Role.DANIEL])
def test_structural_veto_blocks_arthur_support(veto_role) -> None:
    runner = RecordingRunner(veto_role)

    result = KerekasztalOrchestrator(runner).run(fact_packet())

    final = result["fixtures"][0]["final"]
    assert final["selected"] is False
    assert final["blocked_by_structural_veto"] is True
    assert veto_role.value in final["veto_roles"]


def test_evidence_hash_mismatch_is_rejected() -> None:
    class BadRunner(RecordingRunner):
        def run(self, role, payload):
            response = dict(super().run(role, payload))
            response["evidence_sha256"] = "wrong"
            return response

    with pytest.raises(KerekasztalError, match="evidence hash mismatch"):
        KerekasztalOrchestrator(BadRunner()).run(fact_packet())


def test_role_response_cannot_inject_price_data() -> None:
    class PriceRunner(RecordingRunner):
        def run(self, role, payload):
            response = dict(super().run(role, payload))
            response["price"] = 1.8
            return response

    with pytest.raises(KerekasztalError, match="forbidden"):
        KerekasztalOrchestrator(PriceRunner()).run(fact_packet())
