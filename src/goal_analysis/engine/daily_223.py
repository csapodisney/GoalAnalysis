"""Independent evidence-ranked research tickets. No execution or calibrated value claim."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.quote_metadata import quote_metadata

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
    search_candidates, pruning = diversified_daily223_pool(eligible, policy.tolerance)
    selected = _choose(search_candidates, Decimal(0))
    band = "STRICT"
    if selected is None and policy.tolerance:
        selected = _choose(search_candidates, Decimal(str(policy.tolerance)))
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
        "search_candidate_count": len(search_candidates),
        "search_diagnostics": pruning,
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


def diversified_daily223_pool(
    eligible: Sequence[dict[str, Any]], tolerance: float = 0.02, maximum: int = 120
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Bound combination search without dropping an entire busy bookmaker.

    Prices define slot eligibility, never the evidence ranking. Round-robin
    across market/period/price-role buckets protects specialist coverage; the
    first round takes different fixtures before further products of one match.
    This is a documented heuristic search pool, not a global-optimality claim.
    """
    grouped = defaultdict(list)
    floor = float(Decimal(2) * (1 - Decimal(str(tolerance))))
    for item in eligible:
        if item["decimal_price"] >= floor:
            grouped[(item["bookmaker"], item["region"], item["currency"])].append(item)
    result, diagnostics = [], []
    for group, items in sorted(grouped.items()):
        if len(items) <= maximum:
            result.extend(items)
            continue
        buckets = defaultdict(list)
        for item in sorted(items, key=lambda row: (-row["support_score"], row["candidate_id"])):
            price = Decimal(str(item["decimal_price"]))
            role = "three" if price >= 3 else "two" if price >= 2 else "near"
            buckets[(item["period"], item["market_key"], item["selection_key"], role)].append(item)
        selected, seen_ids, seen_fixtures = [], set(), set()
        for unique_fixtures in (True, False):
            while len(selected) < maximum:
                added = False
                for key in sorted(buckets):
                    choice = next(
                        (
                            item
                            for item in buckets[key]
                            if item["candidate_id"] not in seen_ids
                            and (not unique_fixtures or item["fixture_id"] not in seen_fixtures)
                        ),
                        None,
                    )
                    if choice is not None:
                        selected.append(choice)
                        seen_ids.add(choice["candidate_id"])
                        seen_fixtures.add(choice["fixture_id"])
                        added = True
                    if len(selected) == maximum:
                        break
                if not added:
                    break
        result.extend(selected)
        diagnostics.append(
            {
                "status": "EVIDENCE_DIVERSIFIED_SEARCH_POOL",
                "bookmaker": group[0],
                "region": group[1],
                "currency": group[2],
                "eligible_price_role_candidates": len(items),
                "searched_candidates": len(selected),
                "deferred_candidates": len(items) - len(selected),
                "method": "MARKET_PERIOD_PRICE_ROLE_ROUND_ROBIN_WITH_FIXTURE_DIVERSITY",
            }
        )
    return sorted(result, key=lambda item: item["candidate_id"]), diagnostics


def _prepare(
    candidate: Mapping[str, Any],
    target_date: date,
    now: datetime,
    policy: Daily223Policy,
    *,
    require_history: bool = True,
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
    for name in ("quote_provider", "bookmaker_name", "provider_market_id"):
        if name in candidate:
            item[name] = candidate[name]
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
    item.update(
        quote_metadata(
            candidate.get("quote_timestamp_raw", candidate.get("quoted_at")),
            now,
            policy.quote_max_age_seconds,
        )
    )
    components, evidence = _score_evidence(candidate["evidence"], now, policy)
    if require_history and (not components["historical"] or not components["venue_form"]):
        raise ValueError("HISTORICAL_AND_VENUE_SUPPORT_REQUIRED")
    item.update(
        kickoff=kickoff.isoformat(),
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
    minimum_product = Decimal(12) * (1 - tolerance)
    prices = {item["candidate_id"]: Decimal(str(item["decimal_price"])) for item in candidates}
    for group in grouped.values():
        ranked = sorted(group, key=lambda item: (-item["support_score"], item["candidate_id"]))
        for i, first in enumerate(ranked[:-2]):
            if best_key is not None and first["support_score"] < -best_key[0]:
                break
            for j in range(i + 1, len(ranked) - 1):
                second = ranked[j]
                if best_key is not None and second["support_score"] < -best_key[0]:
                    break
                if first["fixture_id"] == second["fixture_id"]:
                    continue
                for third in ranked[j + 1 :]:
                    if best_key is not None and third["support_score"] < -best_key[0]:
                        break
                    scores = (
                        first["support_score"],
                        second["support_score"],
                        third["support_score"],
                    )
                    if (
                        best_key is not None
                        and scores[2] == -best_key[0]
                        and sum(scores) < -best_key[1]
                    ):
                        break
                    if third["fixture_id"] in {first["fixture_id"], second["fixture_id"]}:
                        continue
                    quote_prices = sorted(
                        prices[item["candidate_id"]] for item in (first, second, third)
                    )
                    if any(
                        price < floor for price, floor in zip(quote_prices, floors, strict=True)
                    ):
                        continue
                    if quote_prices[0] * quote_prices[1] * quote_prices[2] < minimum_product:
                        continue
                    ordered = sorted(
                        (first, second, third),
                        key=lambda item: (item["decimal_price"], item["candidate_id"]),
                    )
                    key = (
                        -scores[2],
                        -sum(scores),
                        tuple(item["candidate_id"] for item in ordered),
                    )
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
