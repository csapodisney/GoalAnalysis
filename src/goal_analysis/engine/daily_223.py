"""Independent evidence-ranked research tickets. No execution or calibrated value claim."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from itertools import combinations
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256

WEIGHTS = {
    "historical": 30,
    "venue_form": 25,
    "competition_context": 20,
    "load_and_squad": 15,
    "documented_motivation": 10,
}
FLOORS = (Decimal(2), Decimal(2), Decimal(3))


@dataclass(frozen=True, slots=True)
class Daily223Policy:
    tolerance: float = 0.02
    quote_max_age_seconds: int = 300
    context_max_age_hours: int = 48
    timezone_name: str = "Europe/Berlin"

    def __post_init__(self) -> None:
        _number(self.tolerance, "tolerance", 0, 0.02)
        for name in ("quote_max_age_seconds", "context_max_age_hours"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        ZoneInfo(self.timezone_name)


def build_daily_223(
    candidates: Sequence[Mapping[str, Any]],
    target_date: date,
    observed_at: datetime,
    policy: Daily223Policy | None = None,
) -> dict[str, Any]:
    """Price-constrained independent construction, not an APPROVED betting decision.

    Candidates must be independently sourced, NOT other tickets' selected/rejected output.
    This pure function does not fetch data, place wagers, or calculate win probabilities.
    """
    policy = policy or Daily223Policy()
    _aware(observed_at)
    if type(target_date) is not date:
        raise ValueError("target_date must be a date")
    if len(candidates) > 200:
        raise ValueError(
            "Daily223 accepts at most 200 pre-analysed candidates; none were truncated"
        )
    if any(not isinstance(item, Mapping) for item in candidates):
        raise ValueError("each candidate must be an object")
    identifiers = [_text(item, "candidate_id") for item in candidates]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("candidate_id must be unique")
    products = Counter(_product_key(item) for item in candidates)

    rejected: list[dict[str, str]] = []
    eligible: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            if products[_product_key(candidate)] > 1:
                raise ValueError("DUPLICATE_PRODUCT_REQUIRES_SINGLE_CURRENT_QUOTE")
            eligible.append(_prepare(candidate, target_date, observed_at, policy))
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            rejected.append({"candidate_id": candidate["candidate_id"], "reason": str(error)})
    eligible.sort(key=lambda item: item["candidate_id"])
    selected = _choose(eligible, Decimal(0))
    band = "STRICT"
    if selected is None and policy.tolerance:
        selected = _choose(eligible, Decimal(str(policy.tolerance)))
        band = "NEAR_TARGET"
    legs = [] if selected is None else selected
    product = _product(legs) if legs else None
    result = {
        "schema_version": 1,
        "module": "DAILY_223",
        "prompt_version": "arthur-pentagram-v2.1",
        "weight_version": "daily-223-evidence-v1",
        "date": target_date.isoformat(),
        "timezone": policy.timezone_name,
        "observed_at": observed_at.isoformat(),
        "construction_status": "COMPLETE" if legs else "CONSTRUCTION_INCOMPLETE",
        "selection_status": "RESEARCH_ONLY",
        "betting_approved": False,
        "real_wager_placed": False,
        "daily_report_required": True,
        "independent_of_other_tickets": True,
        "input_candidate_count": len(candidates),
        "eligible_candidate_count": len(eligible),
        "weights": dict(WEIGHTS),
        "tolerance": policy.tolerance,
        "odds_band": band if legs else None,
        "target_combined_price": 12.0,
        "minimum_relaxed_price": float(Decimal(12) * (1 - Decimal(str(policy.tolerance)))),
        "combined_price": float(product) if product is not None else None,
        "combined_price_kind": "THEORETICAL_PRODUCT_NOT_BOOKMAKER_COMBO_QUOTE",
        "target_shortfall": float(max(Decimal(0), 12 - product)) if product else None,
        "legs": [
            dict(item, slot=index + 1, target_price=float(FLOORS[index]))
            for index, item in enumerate(legs)
        ],
        "candidate_shortlist": sorted(
            eligible, key=lambda item: (-item["support_score"], item["candidate_id"])
        ),
        "rejected_candidates": sorted(rejected, key=lambda item: item["candidate_id"]),
        "follow_up": []
        if legs
        else [
            "Expand the independently researched daily match and market universe.",
            "Refresh missing evidence/quotes; do not invent a third leg or hide this report.",
        ],
        "risk_notes": _risk_notes(legs),
    }
    result["report_sha256"] = canonical_sha256(result)
    return result


def _prepare(
    candidate: Mapping[str, Any],
    target_date: date,
    now: datetime,
    policy: Daily223Policy,
) -> dict[str, Any]:
    names = (
        "candidate_id",
        "fixture_id",
        "home_team",
        "away_team",
        "competition",
        "competition_type",
        "market_key",
        "selection_key",
        "period",
        "settlement",
        "bookmaker",
        "region",
        "currency",
        "quote_source_id",
        "sensitivity_note",
    )
    item = {name: _text(candidate, name) for name in names}
    if item["competition_type"] not in {"LEAGUE", "CUP", "UEFA", "OTHER"}:
        raise ValueError("INVALID_COMPETITION_TYPE")
    kickoff = _timestamp(candidate["kickoff"])
    if kickoff <= now:
        raise ValueError("EVENT_STARTED")
    if kickoff.astimezone(ZoneInfo(policy.timezone_name)).date() != target_date:
        raise ValueError("NOT_TARGET_DATE")
    if candidate.get("event_status") != "SCHEDULED":
        raise ValueError("EVENT_NOT_SCHEDULED")
    if candidate.get("quote_available") is not True:
        raise ValueError("QUOTE_UNAVAILABLE")
    price = _number(candidate["decimal_price"], "decimal_price", 1)
    if price <= 1:
        raise ValueError("INVALID_DECIMAL_PRICE")
    quoted_at = _timestamp(candidate["quoted_at"])
    age = (now - quoted_at).total_seconds()
    if not 0 <= age <= policy.quote_max_age_seconds:
        raise ValueError("QUOTE_STALE_OR_FROM_FUTURE")
    components, evidence = _score_evidence(candidate["evidence"], now, policy)
    if not components["historical"] or not components["venue_form"]:
        raise ValueError("HISTORICAL_AND_VENUE_SUPPORT_REQUIRED")
    item.update(
        kickoff=kickoff.isoformat(),
        quoted_at=quoted_at.isoformat(),
        decimal_price=price,
        score_components=components,
        support_score=round(sum(components.values()), 6),
        missing_support=[name for name, value in components.items() if value == 0],
        evidence=evidence,
    )
    return item


def _score_evidence(
    observations: Any,
    now: datetime,
    policy: Daily223Policy,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    if not isinstance(observations, list):
        raise TypeError("evidence must be a list")
    scores = {name: 0.0 for name in WEIGHTS}
    evidence = []
    seen: set[str] = set()
    for observation in observations:
        if not isinstance(observation, Mapping):
            raise TypeError("evidence item must be an object")
        record = {
            name: _text(observation, name)
            for name in (
                "evidence_id",
                "category",
                "statement",
                "claim_type",
                "source_id",
            )
        }
        key, category = record["evidence_id"], record["category"]
        if key in seen:
            raise ValueError("DUPLICATE_EVIDENCE_ID")
        seen.add(key)
        if category not in WEIGHTS:
            raise ValueError("UNKNOWN_EVIDENCE_CATEGORY")
        if record["claim_type"] not in {"FACT", "INFERENCE"}:
            raise ValueError("CLAIM_TYPE_MUST_BE_FACT_OR_INFERENCE")
        stamp = _timestamp(observation["observed_at"])
        if stamp > now:
            raise ValueError("EVIDENCE_FROM_FUTURE")
        strength = _number(observation["strength"], "strength", 0, 1)
        reliability = _number(observation["reliability"], "reliability", 0, 1)
        fresh = (
            category in {"historical", "venue_form"}
            or (now - stamp).total_seconds() <= policy.context_max_age_hours * 3600
        )
        contribution = WEIGHTS[category] * strength * reliability if fresh else 0.0
        scores[category] = max(scores[category], round(contribution, 6))
        record.update(
            observed_at=stamp.isoformat(), strength=strength, reliability=reliability, fresh=fresh
        )
        evidence.append(record)
    return scores, sorted(evidence, key=lambda item: item["evidence_id"])


def _choose(candidates: list[dict[str, Any]], tolerance: Decimal) -> list[dict[str, Any]] | None:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[(candidate["bookmaker"], candidate["region"], candidate["currency"])].append(
            candidate
        )
    best = None
    best_key = None
    floors = [value * (1 - tolerance) for value in FLOORS]
    for group in grouped.values():
        for triple in combinations(group, 3):
            if len({leg["fixture_id"] for leg in triple}) != 3:
                continue
            # Sorted prices make the slot test independent of the input order.
            ordered = sorted(triple, key=lambda item: (item["decimal_price"], item["candidate_id"]))
            if any(
                Decimal(str(leg["decimal_price"])) < floor
                for leg, floor in zip(ordered, floors, strict=True)
            ):
                continue
            if _product(ordered) < Decimal(12) * (1 - tolerance):
                continue
            scores = [item["support_score"] for item in ordered]
            key = (-min(scores), -sum(scores), tuple(item["candidate_id"] for item in ordered))
            if best_key is None or key < best_key:
                best_key, best = key, ordered
    return best


def _risk_notes(legs: list[dict[str, Any]]) -> list[str]:
    notes = ["Evidence points are not probabilities; this is not a calibrated value approval."]
    if legs:
        competitions = Counter(item["competition"] for item in legs)
        notes.extend(
            f"Shared competition: {name}"
            for name, count in sorted(competitions.items())
            if count > 1
        )
        notes.append("Cross-match dependence is not quantified; joint win probability is unknown.")
    return notes


def _product(legs: Sequence[Mapping[str, Any]]) -> Decimal:
    result = Decimal(1)
    for item in legs:
        result *= Decimal(str(item["decimal_price"]))
    return result


def _product_key(item: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(item.get(name, ""))
        for name in (
            "fixture_id",
            "market_key",
            "selection_key",
            "period",
            "settlement",
            "bookmaker",
            "region",
            "currency",
        )
    )


def _number(value: Any, name: str, low: float, high: float | None = None) -> float:
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < low or (high is not None and value > high):
        raise ValueError(f"{name} outside allowed range")
    return float(value)


def _text(item: Mapping[str, Any], name: str) -> str:
    value = item.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value.strip()


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp must be an ISO string")
    result = datetime.fromisoformat(value)
    _aware(result)
    return result


def _aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
