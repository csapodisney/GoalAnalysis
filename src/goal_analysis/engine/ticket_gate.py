from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from goal_analysis.agents import canonical_sha256
from goal_analysis.normalization.models import OddsQuote


class TicketGateError(ValueError):
    """Raised when post-ranking price attachment breaks a frozen invariant."""


@dataclass(frozen=True, slots=True)
class TicketGatePolicy:
    maximum_legs: int = 6
    maximum_quote_age_seconds: int = 600
    target_combined_price: float | None = None

    def __post_init__(self) -> None:
        if self.maximum_legs < 1:
            raise ValueError("maximum_legs must be positive")
        if self.maximum_quote_age_seconds < 0:
            raise ValueError("maximum_quote_age_seconds must not be negative")
        if self.target_combined_price is not None and self.target_combined_price <= 1:
            raise ValueError("target_combined_price must be greater than 1")


def evaluate_ticket(
    kerekasztal_run: Mapping[str, Any],
    quotes: Sequence[OddsQuote],
    observed_at: datetime,
    policy: TicketGatePolicy | None = None,
) -> dict[str, Any]:
    """Attach fresh prices after football selection without changing its order."""

    policy = policy or TicketGatePolicy()
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise TicketGateError("observed_at must be timezone-aware")
    _validate_frozen_run(kerekasztal_run)

    selected = [item for item in kerekasztal_run["fixtures"] if item["final"]["selected"]]
    candidates = selected[: policy.maximum_legs]
    capped = selected[policy.maximum_legs :]
    requests = [_market_request(item) for item in candidates]
    request_keys = {
        (item["fixture_id"], item["market_key"], item["selection_key"]) for item in requests
    }

    valid_by_bookmaker: dict[str, dict[tuple[str, str, str], OddsQuote]] = defaultdict(dict)
    rejected_quotes: list[dict[str, str]] = []
    for quote in quotes:
        key = (quote.fixture_id, quote.market_key, quote.selection_key)
        if key not in request_keys:
            rejected_quotes.append(_quote_rejection(quote, "not_in_frozen_selection"))
            continue
        age = quote.age_seconds(observed_at)
        if age < 0:
            rejected_quotes.append(_quote_rejection(quote, "quote_from_future"))
            continue
        if age > policy.maximum_quote_age_seconds:
            rejected_quotes.append(_quote_rejection(quote, "stale_quote"))
            continue
        existing = valid_by_bookmaker[quote.bookmaker].get(key)
        if existing is None or (quote.quoted_at, quote.decimal_price) > (
            existing.quoted_at,
            existing.decimal_price,
        ):
            valid_by_bookmaker[quote.bookmaker][key] = quote

    complete: list[tuple[Decimal, str, list[OddsQuote]]] = []
    for bookmaker, bookmaker_quotes in valid_by_bookmaker.items():
        if all(key in bookmaker_quotes for key in request_keys):
            ordered = [
                bookmaker_quotes[(item["fixture_id"], item["market_key"], item["selection_key"])]
                for item in requests
            ]
            combined = _combined_price(ordered)
            complete.append((combined, bookmaker, ordered))
    complete.sort(key=lambda item: (-item[0], item[1]))

    chosen = complete[0] if complete else None
    combined_price = float(chosen[0]) if chosen else None
    target_reached = (
        combined_price >= policy.target_combined_price
        if combined_price is not None and policy.target_combined_price is not None
        else None
    )
    ready = bool(requests) and chosen is not None
    return {
        "schema_version": 1,
        "gate_type": "post_ranking_ticket_gate",
        "observed_at": observed_at.isoformat(),
        "ranking_frozen_at": kerekasztal_run["ranking_frozen_at"],
        "ranking_order": list(kerekasztal_run["ranking_order"]),
        "ranking_integrity_sha256": canonical_sha256(
            {
                "fact_packet_sha256": kerekasztal_run["fact_packet_sha256"],
                "ranking_frozen_at": kerekasztal_run["ranking_frozen_at"],
                "ranking_order": kerekasztal_run["ranking_order"],
            }
        ),
        "football_selection_order": [item["fixture_id"] for item in selected],
        "prices_attached_after_selection": True,
        "ticket_ready": ready,
        "bookmaker": chosen[1] if chosen else None,
        "combined_price": combined_price,
        "target_combined_price": policy.target_combined_price,
        "target_reached": target_reached,
        "legs": [_priced_leg(request, quote) for request, quote in zip(requests, chosen[2])]
        if chosen
        else [],
        "unpriced_requests": requests if chosen is None else [],
        "capped_fixture_ids": [item["fixture_id"] for item in capped],
        "rejected_quotes": rejected_quotes,
        "constraints": {
            "same_bookmaker": True,
            "maximum_legs": policy.maximum_legs,
            "maximum_quote_age_seconds": policy.maximum_quote_age_seconds,
            "no_backfill": True,
            "odds_weight_in_football_selection": 0,
        },
    }


def _validate_frozen_run(run: Mapping[str, Any]) -> None:
    if run.get("run_type") != "kerekasztal_shadow":
        raise TicketGateError("expected a completed kerekasztal_shadow run")
    if run.get("prices_attached") is not False:
        raise TicketGateError("prices were attached before the ticket gate")
    fixture_order = [item["fixture_id"] for item in run.get("fixtures", [])]
    frozen = run.get("ranking_order", [])
    if fixture_order != frozen:
        raise TicketGateError("fixture order differs from frozen ranking")
    for item in run.get("fixtures", []):
        if item["final"].get("selected") and item["final"].get("blocked_by_structural_veto"):
            raise TicketGateError("structural-veto fixture cannot be selected")


def _market_request(fixture: Mapping[str, Any]) -> dict[str, str]:
    arthur = fixture["arthur"]
    market_key = arthur.get("market_key")
    selection_key = arthur.get("selection_key")
    if not market_key or not selection_key:
        raise TicketGateError(f"Arthur selection lacks market for fixture {fixture['fixture_id']}")
    return {
        "fixture_id": fixture["fixture_id"],
        "market_key": market_key,
        "selection_key": selection_key,
    }


def _combined_price(quotes: Sequence[OddsQuote]) -> Decimal:
    result = Decimal(1)
    for quote in quotes:
        result *= Decimal(str(quote.decimal_price))
    return result.quantize(Decimal("0.000001"))


def _priced_leg(request: Mapping[str, str], quote: OddsQuote) -> dict[str, Any]:
    return {
        **request,
        "bookmaker": quote.bookmaker,
        "decimal_price": quote.decimal_price,
        "quoted_at": quote.quoted_at.isoformat(),
        "provider": quote.provider,
    }


def _quote_rejection(quote: OddsQuote, reason: str) -> dict[str, str]:
    return {
        "fixture_id": quote.fixture_id,
        "bookmaker": quote.bookmaker,
        "market_key": quote.market_key,
        "selection_key": quote.selection_key,
        "reason": reason,
    }
