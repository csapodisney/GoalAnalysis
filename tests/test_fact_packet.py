import json

import pytest

from goal_analysis.agents import FactPacketError, build_fact_packet, write_fact_packet


def screening() -> dict:
    return {
        "schema_version": 1,
        "target_date": "2026-09-20",
        "generated_at": "2026-09-20T08:00:00+00:00",
        "input_count": 3,
        "accepted": [
            {
                "fixture_id": "high",
                "competition_id": "DE1",
                "home_team": "Home",
                "away_team": "Away",
                "kickoff": "2026-09-20T12:00:00+00:00",
                "screening_score": 87.5,
                "feature_version": "goal-shortlist-v1",
                "features": {
                    "evidence_from": "2025-10-01T12:00:00+00:00",
                    "evidence_to": "2026-09-10T12:00:00+00:00",
                    "home_sample": 10,
                    "away_sample": 9,
                    "coverage": 0.625,
                    "average_total_goals": 3.2,
                    "over_2_5_rate": 0.7,
                    "btts_rate": 0.65,
                    "home_goals_for": 2.0,
                    "home_goals_against": 1.2,
                    "away_goals_for": 1.5,
                    "away_goals_against": 1.4,
                },
                "ranking": {"score": 87.5},
            }
        ],
        "rejected": [
            {"fixture_id": "late", "code": "already_started", "detail": "past"},
            {"fixture_id": "other", "code": "competition_not_allowed", "detail": "XX1"},
        ],
    }


def test_fact_packet_is_compact_frozen_and_auditable(tmp_path) -> None:
    packet = build_fact_packet(screening())
    output = tmp_path / "arthur.json"
    write_fact_packet(output, packet)
    persisted = json.loads(output.read_text("utf-8"))

    assert persisted["ranking_order"] == ["high"]
    assert persisted["ranking_frozen_at"] == "2026-09-20T08:00:00+00:00"
    assert len(persisted["source_sha256"]) == 64
    assert len(persisted["fixtures"][0]["evidence_sha256"]) == 64
    assert persisted["constraints"]["odds_weight"] == 0
    assert persisted["fixtures"][0]["evidence_window"]["to"] == "2026-09-10T12:00:00+00:00"
    assert persisted["screening_audit"]["rejection_codes"] == {
        "already_started": 1,
        "competition_not_allowed": 1,
    }
    assert "odds" not in json.dumps(persisted["fixtures"]).lower()


def test_same_input_produces_same_hashes() -> None:
    first = build_fact_packet(screening())
    second = build_fact_packet(screening())

    assert first["source_sha256"] == second["source_sha256"]
    assert first["fixtures"][0]["evidence_sha256"] == second["fixtures"][0]["evidence_sha256"]


@pytest.mark.parametrize("forbidden", ["odds", "decimal_price", "bestOdds"])
def test_pre_freeze_packet_rejects_odds_and_price_fields(forbidden) -> None:
    source = screening()
    source["accepted"][0][forbidden] = 1.8

    with pytest.raises(FactPacketError, match="forbidden"):
        build_fact_packet(source)


def test_packet_keeps_missing_evidence_as_null() -> None:
    source = screening()
    source["accepted"][0]["features"]["btts_rate"] = None

    packet = build_fact_packet(source)

    assert packet["fixtures"][0]["goal_profile"]["btts"] is None
