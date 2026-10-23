"""Keep established publication odds bands without weakening source selection.

This is a publication gate, not a ticket builder. Apply it to complete
alternatives before diversification so an eligible next choice can be selected.
DAILY_223 and club personas retain their own existing construction contracts.
"""

from __future__ import annotations

import math
from collections import Counter
from copy import deepcopy
from decimal import Decimal, localcontext

from goal_analysis.config.portfolio import (
    DEFAULT_SETTINGS,
    LEGACY_PROFILE_IDS,
    MODEL_PROFILE_IDS,
    TIPSTER_PROFILE_IDS,
)

VERSION = "publication-odds-band-v1"
STAGE = "PUBLICATION_ODDS_BAND"
CONTROLLED_PROFILES = frozenset(
    (*LEGACY_PROFILE_IDS, *MODEL_PROFILE_IDS, "available")
) - {"daily223", *TIPSTER_PROFILE_IDS}


def _band(settings):
    values = [settings.get(key, DEFAULT_SETTINGS[key])
              for key in ("target_min_odds", "target_max_odds")]
    if (any(type(value) not in (int, float) or not math.isfinite(value)
            or not 2 <= value <= 100 for value in values) or values[0] > values[1]):
        raise ValueError("Érvénytelen publikálási szorzósáv.")
    return tuple(Decimal(str(value)) for value in values)


def _issue(item, band):
    legs = item.get("legs")
    # An abstention/error without selections is not a low-odds publication.
    if not legs:
        return None
    if not isinstance(legs, list):
        return "INVALID_TICKET_ODDS"
    prices = [leg.get("decimal_price") if isinstance(leg, dict) else None for leg in legs]
    combined = item.get("combined_price")
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 1
           for value in [*prices, combined]):
        return "INVALID_TICKET_ODDS"
    with localcontext() as context:
        context.prec = 64
        product = math.prod(Decimal(str(price)) for price in prices)
    if not product.is_finite() or not math.isclose(
        float(product), combined, rel_tol=1e-9, abs_tol=1e-9
    ):
        return "TICKET_ODDS_MISMATCH"
    if product < band[0]:
        return "BELOW_PUBLICATION_ODDS_BAND"
    if product > band[1]:
        return "ABOVE_PUBLICATION_ODDS_BAND"
    return None


def publication_odds_issue(item: dict, settings: dict) -> str | None:
    """Return a publication rejection code; protected/persona cards are exempt.

    The actual leg-price product determines the inclusive band, and the declared
    total must agree with that product. Empty abstentions/errors are preserved.
    Missing settings use the configured DEFAULT_SETTINGS, currently 10–50.
    """
    if item.get("profile_id") not in CONTROLLED_PROFILES:
        return None
    return _issue(item, _band(settings))


def _option_issue(option, profile, band):
    if not isinstance(option, dict):
        return "INVALID_ODDS_OPTION"
    templates = [option[key] for key in ("ticket", "recommendation") if key in option]
    if not templates:
        return "INVALID_ODDS_OPTION"
    for item in templates:
        if not isinstance(item, dict) or item.get("profile_id") != profile:
            return "OPTION_PROFILE_MISMATCH"
        if not item.get("legs"):
            return "INVALID_ODDS_OPTION"
        if issue := _issue(item, band):
            return issue
    return None


def _abstain(item, issue, band):
    result = deepcopy(item)
    reason = (f"A meglévő minőségi feltételekkel nincs kiválasztott szelvény a "
              f"{float(band[0]):g}–{float(band[1]):g} szorzósávban. "
              "Csak sávon belüli, teljes alternatíva választható; "
              "a rendszer nem ad hozzá töltelékmeccset.")
    result.update(status="ABSTAINED", legs=[], combined_price=None, probability=None, bookmaker=None,
                  playable=False, selected_for_portfolio=False, portfolio_ticket_id=None,
                  stake_eur=None, confidence="LOW", construction_verified=False,
                  reason_code=issue, abstention_reason=reason,
                  publication_odds={"version": VERSION, "stage": STAGE, "reason_code": issue,
                                    "minimum": float(band[0]), "maximum": float(band[1])})
    result.pop("recommendation_id", None)
    result.pop("ticket_id", None)
    warnings = result.setdefault("quality_warnings", [])
    if reason not in warnings:
        warnings.append(reason)
    if isinstance(result.get("model_summary"), dict):
        result["model_summary"]["selected_legs"] = 0
    for key in list(result.get("selection_diagnostics", {})):
        if key.startswith("selected_"):
            result["selection_diagnostics"].pop(key)
    return result


def apply_publication_odds(report: dict, options: dict, settings: dict) -> tuple[dict, dict]:
    """Return detached report/options, filtering complete alternatives pre-allocate.

    An invalid base card is always cleared, including when a valid alternative
    exists. ``allocate`` can rebuild that card from the retained pool; abandoning
    allocation cannot accidentally expose the old low-odds base. No scores,
    probabilities, prices, selected legs or profile contracts are adjusted.
    """
    band = _band(settings)
    result, filtered = deepcopy(report), deepcopy(options)
    profiles = {}
    for profile, pool in filtered.items():
        if profile not in CONTROLLED_PROFILES:
            continue
        counts, kept = Counter(), []
        for option in pool:
            issue = _option_issue(option, profile, band)
            if issue:
                counts[issue] += 1
            else:
                kept.append(option)
        filtered[profile] = kept
        profiles[profile] = {"input_options": len(pool), "eligible_options": len(kept),
                             "rejected_options": dict(sorted(counts.items())),
                             "removed_cards": {}}
    for group in ("recommendations", "tickets", "profile_proposals", "preview_tickets"):
        if group not in result:
            continue
        rows = []
        for item in result[group]:
            profile = item.get("profile_id")
            issue = _issue(item, band) if profile in CONTROLLED_PROFILES else None
            if not issue:
                rows.append(item)
                continue
            detail = profiles.setdefault(profile, {
                "input_options": 0, "eligible_options": 0,
                "rejected_options": {}, "removed_cards": {},
            })
            detail["removed_cards"][group] = detail["removed_cards"].get(group, 0) + 1
            if group == "recommendations":
                rows.append(_abstain(item, issue, band))
        result[group] = rows
    result["publication_odds"] = {
        "version": VERSION, "stage": STAGE,
        "minimum": float(band[0]), "maximum": float(band[1]),
        "scope": "EXISTING_NON_CLUB_SPECIALIST_AND_NUMERIC_PROFILES",
        "protected_daily_unchanged": True, "club_persona_bands_unchanged": True,
        "new_legs_added": False, "quality_thresholds_relaxed": False,
        "profiles": profiles,
    }
    return result, filtered
