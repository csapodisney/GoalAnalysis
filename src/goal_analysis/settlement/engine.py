from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from goal_analysis.agents import canonical_sha256


class SettlementError(ValueError):
    """Raised when a frozen ticket cannot be settled safely."""


class SettlementStatus(StrEnum):
    WIN = "win"
    LOSS = "loss"
    VOID = "void"


@dataclass(frozen=True, slots=True)
class FinalScore:
    fixture_id: str
    home_goals: int
    away_goals: int
    settled_at: datetime

    def __post_init__(self) -> None:
        if self.home_goals < 0 or self.away_goals < 0:
            raise ValueError("goals must not be negative")
        if self.settled_at.tzinfo is None or self.settled_at.utcoffset() is None:
            raise ValueError("settled_at must be timezone-aware")


def settle_ticket(
    ticket: Mapping[str, Any],
    scores: Sequence[FinalScore],
    settled_at: datetime,
) -> dict[str, Any]:
    """Settle supported regular-time markets against the unchanged ticket artifact."""

    if settled_at.tzinfo is None or settled_at.utcoffset() is None:
        raise SettlementError("settled_at must be timezone-aware")
    if ticket.get("gate_type") != "post_ranking_ticket_gate":
        raise SettlementError("expected post_ranking_ticket_gate artifact")
    if ticket.get("ticket_ready") is not True:
        raise SettlementError("ticket was not ready and cannot be settled")
    if not ticket.get("legs"):
        raise SettlementError("ticket has no legs")

    score_by_fixture = {score.fixture_id: score for score in scores}
    if len(score_by_fixture) != len(scores):
        raise SettlementError("duplicate final score fixture id")
    legs = []
    for leg in ticket["legs"]:
        score = score_by_fixture.get(leg["fixture_id"])
        if score is None:
            raise SettlementError(f"missing final score for {leg['fixture_id']}")
        status = _settle_leg(leg["market_key"], leg["selection_key"], score)
        legs.append(
            {
                "fixture_id": leg["fixture_id"],
                "market_key": leg["market_key"],
                "selection_key": leg["selection_key"],
                "decimal_price": leg["decimal_price"],
                "home_goals": score.home_goals,
                "away_goals": score.away_goals,
                "status": status.value,
                "score_settled_at": score.settled_at.isoformat(),
            }
        )

    statuses = [SettlementStatus(item["status"]) for item in legs]
    if SettlementStatus.LOSS in statuses:
        ticket_status = SettlementStatus.LOSS
        return_multiplier = Decimal(0)
    else:
        winning_prices = [
            Decimal(str(item["decimal_price"]))
            for item in legs
            if item["status"] == SettlementStatus.WIN.value
        ]
        return_multiplier = _product(winning_prices)
        ticket_status = SettlementStatus.WIN if winning_prices else SettlementStatus.VOID

    frozen_ticket_sha256 = canonical_sha256(ticket)
    return {
        "schema_version": 1,
        "record_type": "shadow_ticket_settlement",
        "settled_at": settled_at.isoformat(),
        "frozen_ticket_sha256": frozen_ticket_sha256,
        "ranking_integrity_sha256": ticket["ranking_integrity_sha256"],
        "bookmaker": ticket["bookmaker"],
        "ticket_status": ticket_status.value,
        "return_multiplier": float(return_multiplier),
        "stake_units": 1.0,
        "return_units": float(return_multiplier),
        "net_units": float(return_multiplier - Decimal(1)),
        "legs": legs,
        "rules": {
            "regular_time_only": True,
            "record_was_not_mutated": True,
            "research_is_not_real_betting": True,
        },
    }


def _settle_leg(market_key: str, selection_key: str, score: FinalScore) -> SettlementStatus:
    total = score.home_goals + score.away_goals
    if market_key.startswith("totals_"):
        try:
            line = Decimal(market_key.removeprefix("totals_").replace("_", "."))
        except Exception as error:
            raise SettlementError(f"invalid totals market: {market_key}") from error
        difference = Decimal(total) - line
        if difference == 0:
            return SettlementStatus.VOID
        if selection_key == "over":
            return SettlementStatus.WIN if difference > 0 else SettlementStatus.LOSS
        if selection_key == "under":
            return SettlementStatus.WIN if difference < 0 else SettlementStatus.LOSS
    elif market_key == "btts":
        happened = score.home_goals > 0 and score.away_goals > 0
        if selection_key == "yes":
            return SettlementStatus.WIN if happened else SettlementStatus.LOSS
        if selection_key == "no":
            return SettlementStatus.WIN if not happened else SettlementStatus.LOSS
    elif market_key == "match_result":
        actual = "home" if score.home_goals > score.away_goals else "away"
        if score.home_goals == score.away_goals:
            actual = "draw"
        if selection_key in {"home", "draw", "away"}:
            return SettlementStatus.WIN if selection_key == actual else SettlementStatus.LOSS
    raise SettlementError(f"unsupported market selection: {market_key}/{selection_key}")


def _product(values: Sequence[Decimal]) -> Decimal:
    result = Decimal(1)
    for value in values:
        result *= value
    return result.quantize(Decimal("0.000001"))
