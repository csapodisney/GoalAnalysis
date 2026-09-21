from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from .fact_packet import FactPacketError, canonical_sha256


class Role(StrEnum):
    KRONIKAS = "kronikas"
    RITMUSOR = "ritmusor"
    PARHARCMESTER = "parharcmester"
    ORSZEM = "orszem"
    MERLIN = "merlin"
    DANIEL = "daniel"
    ARTHUR = "arthur"


SPECIALISTS = (
    Role.KRONIKAS,
    Role.RITMUSOR,
    Role.PARHARCMESTER,
    Role.ORSZEM,
    Role.MERLIN,
)


class Verdict(StrEnum):
    SUPPORT = "support"
    OPPOSE = "oppose"
    UNKNOWN = "unknown"
    VETO = "veto"


class KerekasztalError(ValueError):
    """Raised when a role breaks the orchestration contract."""


class RoleRunner(Protocol):
    def run(self, role: Role, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RoleOpinion:
    role: Role
    fixture_id: str
    verdict: Verdict
    confidence: float
    thesis: str
    evidence_sha256: str
    structural_veto: bool = False
    market_key: str | None = None
    selection_key: str | None = None
    run_metadata: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "role": self.role.value,
            "fixture_id": self.fixture_id,
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "thesis": self.thesis,
            "evidence_sha256": self.evidence_sha256,
            "structural_veto": self.structural_veto,
            "market_key": self.market_key,
            "selection_key": self.selection_key,
        }
        if self.run_metadata is not None:
            result["run_metadata"] = dict(self.run_metadata)
        return result


ROLE_FIELDS: dict[Role, tuple[str, ...]] = {
    Role.KRONIKAS: ("average_total", "over_2_5"),
    Role.RITMUSOR: ("btts", "home_for", "away_for"),
    Role.PARHARCMESTER: ("home_for", "home_against", "away_for", "away_against"),
    Role.ORSZEM: ("home_against", "away_against"),
    Role.MERLIN: ("btts", "home_for", "away_for"),
}


class KerekasztalOrchestrator:
    """Run independent specialists, mandatory Dániel review, then Arthur."""

    SCHEMA_VERSION = 1

    def __init__(self, runner: RoleRunner) -> None:
        self.runner = runner

    def run(self, fact_packet: Mapping[str, Any]) -> dict[str, Any]:
        _validate_fact_packet(fact_packet)
        fixture_results = [self._run_fixture(item) for item in fact_packet["fixtures"]]
        return {
            "schema_version": self.SCHEMA_VERSION,
            "run_type": "kerekasztal_shadow",
            "target_date": fact_packet["target_date"],
            "fact_packet_sha256": canonical_sha256(fact_packet),
            "ranking_frozen_at": fact_packet["ranking_frozen_at"],
            "ranking_order": list(fact_packet["ranking_order"]),
            "prices_attached": False,
            "fixtures": fixture_results,
        }

    def _run_fixture(self, fixture: Mapping[str, Any]) -> dict[str, Any]:
        opinions = [self._run_specialist(role, fixture) for role in SPECIALISTS]
        daniel = self._run_reviewer(fixture, opinions)
        arthur = self._run_arthur(fixture, opinions, daniel)
        vetoes = [
            opinion.role.value
            for opinion in [*opinions, daniel]
            if opinion.structural_veto or opinion.verdict is Verdict.VETO
        ]
        selected = arthur.verdict is Verdict.SUPPORT and not vetoes
        return {
            "fixture_id": fixture["fixture_id"],
            "rank": fixture["rank"],
            "evidence_sha256": fixture["evidence_sha256"],
            "specialists": [item.to_dict() for item in opinions],
            "daniel": daniel.to_dict(),
            "arthur": arthur.to_dict(),
            "final": {
                "selected": selected,
                "blocked_by_structural_veto": bool(vetoes),
                "veto_roles": vetoes,
            },
        }

    def _run_specialist(self, role: Role, fixture: Mapping[str, Any]) -> RoleOpinion:
        goal_profile = fixture["goal_profile"]
        facts = {key: goal_profile.get(key) for key in ROLE_FIELDS[role]}
        if role is Role.ORSZEM:
            facts["coverage"] = fixture["samples"].get("coverage")
        payload = {
            "task": "independent_specialist_review",
            "role": role.value,
            "fixture_id": fixture["fixture_id"],
            "match": fixture["match"],
            "facts": facts,
            "evidence_window": fixture["evidence_window"],
            "evidence_sha256": fixture["evidence_sha256"],
            "rules": _rules(role),
        }
        return _parse_opinion(self.runner.run(role, payload), role, fixture)

    def _run_reviewer(
        self,
        fixture: Mapping[str, Any],
        opinions: Sequence[RoleOpinion],
    ) -> RoleOpinion:
        payload = {
            "task": "mandatory_adversarial_review",
            "role": Role.DANIEL.value,
            "fixture_id": fixture["fixture_id"],
            "facts": {
                "samples": fixture["samples"],
                "goal_profile": fixture["goal_profile"],
                "evidence_window": fixture["evidence_window"],
            },
            "specialist_opinions": [item.to_dict() for item in opinions],
            "evidence_sha256": fixture["evidence_sha256"],
            "rules": _rules(Role.DANIEL),
        }
        return _parse_opinion(self.runner.run(Role.DANIEL, payload), Role.DANIEL, fixture)

    def _run_arthur(
        self,
        fixture: Mapping[str, Any],
        opinions: Sequence[RoleOpinion],
        daniel: RoleOpinion,
    ) -> RoleOpinion:
        payload = {
            "task": "final_evidence_synthesis",
            "role": Role.ARTHUR.value,
            "fixture_id": fixture["fixture_id"],
            "frozen_rank": fixture["rank"],
            "specialist_opinions": [item.to_dict() for item in opinions],
            "daniel": daniel.to_dict(),
            "evidence_sha256": fixture["evidence_sha256"],
            "rules": _rules(Role.ARTHUR),
        }
        return _parse_opinion(self.runner.run(Role.ARTHUR, payload), Role.ARTHUR, fixture)


def _parse_opinion(
    response: Mapping[str, Any], role: Role, fixture: Mapping[str, Any]
) -> RoleOpinion:
    _reject_odds(response)
    required = {"role", "fixture_id", "verdict", "confidence", "thesis", "evidence_sha256"}
    missing = required - response.keys()
    if missing:
        raise KerekasztalError(f"{role.value} response missing: {', '.join(sorted(missing))}")
    if response["role"] != role.value:
        raise KerekasztalError(f"role mismatch: expected {role.value}")
    if response["fixture_id"] != fixture["fixture_id"]:
        raise KerekasztalError(f"fixture mismatch for {role.value}")
    if response["evidence_sha256"] != fixture["evidence_sha256"]:
        raise KerekasztalError(f"evidence hash mismatch for {role.value}")
    confidence = float(response["confidence"])
    if not 0.0 <= confidence <= 1.0:
        raise KerekasztalError(f"confidence outside 0..1 for {role.value}")
    thesis = str(response["thesis"]).strip()
    if not thesis:
        raise KerekasztalError(f"empty thesis for {role.value}")
    verdict = Verdict(response["verdict"])
    structural_veto = bool(response.get("structural_veto", False))
    if verdict is Verdict.VETO:
        structural_veto = True
    market_key = response.get("market_key")
    selection_key = response.get("selection_key")
    if market_key is not None and not isinstance(market_key, str):
        raise KerekasztalError(f"invalid market_key for {role.value}")
    if selection_key is not None and not isinstance(selection_key, str):
        raise KerekasztalError(f"invalid selection_key for {role.value}")
    return RoleOpinion(
        role=role,
        fixture_id=fixture["fixture_id"],
        verdict=verdict,
        confidence=confidence,
        thesis=thesis,
        evidence_sha256=fixture["evidence_sha256"],
        structural_veto=structural_veto,
        market_key=market_key,
        selection_key=selection_key,
        run_metadata=response.get("_meta"),
    )


def _validate_fact_packet(packet: Mapping[str, Any]) -> None:
    _reject_odds(packet)
    if packet.get("packet_type") != "arthur_fact_packet":
        raise FactPacketError("expected arthur_fact_packet")
    if packet.get("constraints", {}).get("ranking_frozen_before_prices") is not True:
        raise FactPacketError("football ranking must be frozen before orchestration")
    fixture_order = [item["fixture_id"] for item in packet.get("fixtures", [])]
    if fixture_order != packet.get("ranking_order"):
        raise FactPacketError("fixture order differs from frozen ranking")


def _rules(role: Role) -> list[str]:
    common = [
        "Use only supplied evidence.",
        "Missing evidence is unknown, never zero.",
        "Do not use or infer prices or odds.",
        "Return one concise thesis.",
    ]
    responsibility = {
        Role.KRONIKAS: "Assess the path to total goals and the third goal.",
        Role.RITMUSOR: "Assess two-sided scoring and response behaviour.",
        Role.PARHARCMESTER: "Assess relative attacking and defensive strength.",
        Role.ORSZEM: "Find chance restriction and structural under risks.",
        Role.MERLIN: "Assess parity and equalising potential.",
        Role.DANIEL: "State the strongest counter-case; issue structural veto when required.",
        Role.ARTHUR: "Integrate all views; never override a structural veto.",
    }[role]
    return [*common, responsibility]


def _reject_odds(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
    if '"odds"' in serialized or '"price"' in serialized or '"decimal_price"' in serialized:
        raise KerekasztalError("odds/price data is forbidden during football orchestration")
