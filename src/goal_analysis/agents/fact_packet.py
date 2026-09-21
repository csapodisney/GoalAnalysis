from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class FactPacketError(ValueError):
    """Raised when ranked screening data cannot form a safe Arthur packet."""


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_fact_packet(screening: Mapping[str, Any]) -> dict[str, Any]:
    """Turn frozen screening output into a compact, odds-free Kerekasztal input."""

    _reject_odds(screening)
    required = {"schema_version", "target_date", "generated_at", "accepted", "rejected"}
    missing = required - screening.keys()
    if missing:
        raise FactPacketError(f"screening packet missing fields: {', '.join(sorted(missing))}")

    fixtures = []
    for rank, item in enumerate(screening["accepted"], start=1):
        features = item.get("features") or {}
        ranking = item.get("ranking") or {}
        evidence = {
            "fixture_id": item["fixture_id"],
            "competition_id": item["competition_id"],
            "kickoff": item["kickoff"],
            "feature_version": item.get("feature_version"),
            "features": features,
            "ranking": ranking,
        }
        fixtures.append(
            {
                "rank": rank,
                "fixture_id": item["fixture_id"],
                "competition": item["competition_id"],
                "match": f"{item['home_team']} vs {item['away_team']}",
                "kickoff": item["kickoff"],
                "score": item["screening_score"],
                "samples": {
                    "home": features.get("home_sample"),
                    "away": features.get("away_sample"),
                    "coverage": features.get("coverage"),
                },
                "evidence_window": {
                    "from": features.get("evidence_from"),
                    "to": features.get("evidence_to"),
                    "source_generated_at": screening["generated_at"],
                },
                "goal_profile": {
                    "average_total": features.get("average_total_goals"),
                    "over_2_5": features.get("over_2_5_rate"),
                    "btts": features.get("btts_rate"),
                    "home_for": features.get("home_goals_for"),
                    "home_against": features.get("home_goals_against"),
                    "away_for": features.get("away_goals_for"),
                    "away_against": features.get("away_goals_against"),
                },
                "role_inputs": {
                    "kronikas": ["average_total", "over_2_5"],
                    "ritmusor": ["btts", "home_for", "away_for"],
                    "parharcmester": ["home_for", "home_against", "away_for", "away_against"],
                    "orszem": ["home_against", "away_against", "coverage"],
                    "merlin": ["btts", "home_for", "away_for"],
                    "daniel": ["coverage", "home_sample", "away_sample"],
                },
                "evidence_sha256": canonical_sha256(evidence),
            }
        )

    rejection_counts = Counter(item["code"] for item in screening["rejected"])
    source_projection = {
        "schema_version": screening["schema_version"],
        "target_date": screening["target_date"],
        "generated_at": screening["generated_at"],
        "accepted": screening["accepted"],
        "rejected": screening["rejected"],
    }
    return {
        "schema_version": 1,
        "packet_type": "arthur_fact_packet",
        "target_date": screening["target_date"],
        "source_generated_at": screening["generated_at"],
        "ranking_frozen_at": screening["generated_at"],
        "ranking_order": [item["fixture_id"] for item in screening["accepted"]],
        "source_sha256": canonical_sha256(source_projection),
        "fixture_count": len(fixtures),
        "fixtures": fixtures,
        "screening_audit": {
            "input_count": screening.get("input_count"),
            "accepted_count": len(fixtures),
            "rejected_count": len(screening["rejected"]),
            "rejection_codes": dict(sorted(rejection_counts.items())),
        },
        "constraints": {
            "odds_weight": 0,
            "ranking_frozen_before_prices": True,
            "missing_evidence_is_unknown": True,
        },
    }


def write_fact_packet(path: Path, packet: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(destination)


def _reject_odds(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if "odd" in str(key).lower() or "price" in str(key).lower():
                raise FactPacketError(
                    f"odds/price field is forbidden before ranking freeze: {path}.{key}"
                )
            _reject_odds(nested, f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, nested in enumerate(value):
            _reject_odds(nested, f"{path}[{index}]")
