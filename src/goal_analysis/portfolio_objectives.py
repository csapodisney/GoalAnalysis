"""Product objectives and honest pre-settlement capacity diagnostics."""

from __future__ import annotations

import math

VERSION = "arthur-portfolio-objectives-v1"
MIN_READY = 20
MAX_READY = 30
MIN_NON_DAILY_PRICE = 10.0
MAX_NON_DAILY_PRICE = 50.0
TARGET_DAILY_WINS = 3
STRETCH_DAILY_WINS = 4
STAKE_EUR = 1.0


def _price(item):
    value = item.get("combined_price")
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def assess_objectives(report):
    """Measure final recommendations without inventing future outcomes."""
    recommendations = [item for item in report.get("recommendations", [])
                       if isinstance(item, dict) and item.get("legs")]
    ready = [item for item in recommendations if item.get("status") == "READY"]
    non_daily = [item for item in ready if item.get("profile_id") != "daily223"]
    below, above, missing = [], [], []
    for item in non_daily:
        price = _price(item)
        identifier = str(item.get("profile_id", "unknown"))
        if price is None:
            missing.append(identifier)
        elif price < MIN_NON_DAILY_PRICE:
            below.append(identifier)
        elif price > MAX_NON_DAILY_PRICE:
            above.append(identifier)
    count = len(ready)
    return {
        "version": VERSION,
        "ready_recommendations": count,
        "non_ready_recommendations_with_legs": len(recommendations) - count,
        "target_ready_range": {"minimum": MIN_READY, "maximum": MAX_READY},
        "capacity_status": ("BELOW_TARGET" if count < MIN_READY else
                            "ABOVE_TARGET" if count > MAX_READY else "IN_TARGET"),
        "capacity_shortfall": max(0, MIN_READY - count),
        "non_daily_target_price_range": {
            "minimum": MIN_NON_DAILY_PRICE, "maximum": MAX_NON_DAILY_PRICE},
        "non_daily_below_price_range": sorted(below),
        "non_daily_above_price_range": sorted(above),
        "non_daily_missing_price": sorted(missing),
        "stake_eur_per_ticket": STAKE_EUR,
        "settlement_kpi": {"target_daily_winners": TARGET_DAILY_WINS,
                           "stretch_daily_winners": STRETCH_DAILY_WINS,
                           "status": "PENDING_SETTLEMENT"},
        "filler_generation_allowed": False,
        "daily223_protected": True,
    }
