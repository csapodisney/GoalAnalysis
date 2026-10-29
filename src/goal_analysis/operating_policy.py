"""Current generation rules, separate from financial and legacy replay limits.

The profile catalogue remains an immutable historical rulebook. New runs apply
the approved operating contract to detached copies, never to saved tickets.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy

OPERATING_CONTRACT_VERSION = 2
MIN_DAILY_TICKETS = 15
MAX_GENERATED_TICKETS = 30
TARGET_MIN_ODDS = 15.0
TARGET_MAX_ODDS = 40.0
EXCEPTIONAL_MIN_ODDS = 5.0
DEFAULT_FINANCIAL_CAP = 5
CAUTIOUS_PROFILE_IDS = frozenset({
    "orszem", "tipster_bastya_bence", "tipster_csendes_mira",
    "tipster_fek_tade", "tipster_pajzs_dorka", "tipster_stabil_irma",
})


def current_contract(settings: Mapping) -> bool:
    """Unmarked historical input retains its original decision contract."""
    version = settings.get("operating_contract_version", 1)
    if type(version) is not int or version not in (1, OPERATING_CONTRACT_VERSION):
        raise ValueError("Unsupported operating contract version")
    return version == OPERATING_CONTRACT_VERSION


def generation_limits(settings: Mapping) -> tuple[int, int]:
    """Return the requested target and independent hard generation capacity."""
    current = current_contract(settings)
    low, high = (MIN_DAILY_TICKETS, MAX_GENERATED_TICKETS) if current else (1, 5)
    target = settings.get("target_ticket_count", MIN_DAILY_TICKETS if current else 2)
    maximum = settings.get("max_ticket_count", high)
    if (type(target) is not int or type(maximum) is not int
            or not low <= target <= maximum <= high):
        raise ValueError("Invalid generation target/capacity")
    if current and maximum != MAX_GENERATED_TICKETS:
        raise ValueError("Current generated-ticket capacity must be 30")
    return target, maximum


def generation_capacity(settings: Mapping) -> int:
    """The current minimum goal must not cut off other personalities' output."""
    target, maximum = generation_limits(settings)
    return maximum if current_contract(settings) else min(target, maximum)


def cautious_fallback_allowed(
    profile_id: str,
    settings: Mapping,
    primary_options: Sequence,
    *,
    all_daily_fixtures_considered: bool,
) -> bool:
    """An empty shortlist alone cannot authorize the lower-odds exception."""
    if type(all_daily_fixtures_considered) is not bool:
        raise ValueError("Full-day search coverage must be explicit")
    if not isinstance(primary_options, Sequence) or isinstance(primary_options, (str, bytes)):
        raise TypeError("Primary options must be an examined collection")
    return (current_contract(settings) and profile_id in CAUTIOUS_PROFILE_IDS
            and all_daily_fixtures_considered and not primary_options)


def effective_persona(
    persona: Mapping,
    settings: Mapping,
    *,
    cautious_fallback: bool = False,
) -> dict:
    """Apply current odds policy without changing a character's model maths."""
    if type(cautious_fallback) is not bool:
        raise ValueError("Fallback selection must be explicit")
    result = deepcopy(dict(persona))
    if not current_contract(settings):
        if cautious_fallback:
            raise ValueError("Current cautious fallback is unavailable in legacy replay")
        return result
    profile = result["profile_id"]
    if profile == "daily223":
        if cautious_fallback:
            raise ValueError("DAILY_223 has its own protected construction")
        return result
    if cautious_fallback and profile not in CAUTIOUS_PROFILE_IDS:
        raise ValueError("This personality cannot use cautious odds fallback")
    minimum = EXCEPTIONAL_MIN_ODDS if cautious_fallback else TARGET_MIN_ODDS
    maximum = TARGET_MIN_ODDS if cautious_fallback else TARGET_MAX_ODDS
    preferred = result.get("preferred_combined_price", math.sqrt(minimum * maximum))
    if type(preferred) not in (int, float) or not math.isfinite(preferred):
        raise ValueError("Invalid preferred combined price")
    result["target_odds_band"] = {"min": minimum, "max": maximum}
    result["preferred_combined_price"] = min(maximum, max(minimum, preferred))
    result["operating_contract"] = {
        "version": OPERATING_CONTRACT_VERSION,
        "primary_odds_range": {"min": TARGET_MIN_ODDS, "max": TARGET_MAX_ODDS},
        "cautious_fallback": cautious_fallback,
    }
    return result
