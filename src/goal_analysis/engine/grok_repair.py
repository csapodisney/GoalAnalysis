"""Pure, prepublication replacement of independently confirmed problem tickets.

The caller owns independent source verification and the final publication clock.
This module only selects complete options already built by the same persona.
It never gathers data, calls a model, edits DAILY_223, or persists a report.
"""

import math
from collections import Counter
from copy import deepcopy

from goal_analysis.engine.diversification import (
    VERSION,
    _against,
    _eligible,
    _fixtures,
    _option_order,
    _template,
    ready_exposure,
    ticket_signature,
)
from goal_analysis.engine.publication_window import allows_new_ticket

GROUPS = ("recommendations", "tickets", "profile_proposals", "preview_tickets")
GOVERNANCE = ("publication_scope", "team_status", "team_plan_sha256",
              "selected_for_portfolio", "stake_eur", "playable", "status")
READY = {"READY", "MODEL_READY", "REVIEWED", "DRAFT"}


def _daily(profile):
    return str(profile).replace("_", "").casefold() == "daily223"


def _rows(report, profile):
    return [(group, index, row) for group in GROUPS
            for index, row in enumerate(report.get(group, []))
            if isinstance(row, dict) and row.get("profile_id") == profile]


def _unique_package(report, exclude=None):
    # Recommendations are the complete package; main tickets only supplement
    # absent economic selections. Aliases cannot inflate the denominator.
    unique = {}
    for group in ("recommendations", "tickets"):
        for row in report.get(group, []):
            if (isinstance(row, dict) and row.get("legs")
                    and row.get("profile_id") != exclude):
                unique.setdefault(ticket_signature(row), row)
    return list(unique.values())


def _package_exposure(report):
    package = _unique_package(report)
    limit = max(1, math.floor(len(package) * .5)) if package else 0
    counts = Counter(fixture for ticket in package for fixture in _fixtures(ticket))
    return {"unique_ticket_count": len(package), "allowed_tickets_per_fixture": limit,
            "fixture_ticket_counts": dict(sorted(counts.items())),
            "compliant": all(value <= limit for value in counts.values())}


def _replacement(report, profile, option):
    """Update existing aliases only; preserve each alias's governance exactly."""
    rows = _rows(report, profile)
    needs_ticket = any(group != "recommendations" for group, _, _ in rows)
    needs_card = any(group == "recommendations" for group, _, _ in rows)
    if ((needs_ticket and not isinstance(option.get("ticket"), dict))
            or (needs_card and not isinstance(option.get("recommendation"), dict))):
        return None
    main = any(group == "tickets" for group, _, _ in rows)
    ticket_id = option.get("ticket", {}).get("ticket_id")
    if needs_ticket and (not isinstance(ticket_id, str) or not ticket_id):
        return None
    if not main and any(row.get("selected_for_portfolio") is True for _, _, row in rows):
        return None
    revised = deepcopy(report)
    for group, index, original in rows:
        template = option["recommendation" if group == "recommendations" else "ticket"]
        if template.get("profile_id") != profile or template.get("status") not in READY:
            return None
        required_statuses = {"READY": {"READY"}, "REVIEWED": {"REVIEWED", "READY"},
                             "MODEL_READY": {"MODEL_READY", "READY"}}
        if (original.get("status") in required_statuses
                and template.get("status") not in required_statuses[original["status"]]):
            # Preserving the old display status must not promote a less-vetted
            # replacement merely because its economic signature is valid.
            return None
        replacement = deepcopy(template)
        for key in GOVERNANCE:
            if key in original:
                replacement[key] = deepcopy(original[key])
            else:
                replacement.pop(key, None)
        if "portfolio_ticket_id" in original:
            replacement["portfolio_ticket_id"] = ticket_id if main else None
        elif group == "recommendations" and main:
            replacement["portfolio_ticket_id"] = ticket_id
        replacement["diversification"] = {
            "policy_version": VERSION, "unpenalized_score": option["quality"],
            "adjusted_score": option["adjusted_quality"], **option["similarity"],
        }
        revised[group][index] = replacement
    return revised


def repair(report, options, confirmed, now):
    """Return ``(deepcopied_report, audit)`` without changing any input.

    ``confirmed`` must come from the independent confirmation workflow, never
    directly from Grok. The caller must recheck time at actual publication.
    An unrepairable profile stays unchanged and is explicitly unresolved.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Repair requires an aware prepublication clock")
    result = deepcopy(report)
    audit = {"policy_version": "grok-scoped-repair-v1", "as_of": now.isoformat(),
             "changed": [], "changed_profiles": [], "unresolved": [], "protected_daily": []}
    grouped = {}
    for concern in confirmed:
        if not isinstance(concern, dict) or not isinstance(concern.get("profile_id"), str):
            audit["unresolved"].append({"profile_id": None, "reason_code": "INVALID_CONFIRMATION"})
            continue
        profile = concern["profile_id"]
        if _daily(profile):
            audit["protected_daily"].append(deepcopy(concern))
            continue
        grouped.setdefault(profile, []).append(deepcopy(concern))

    def unresolved(profile, reason, concerns):
        audit["unresolved"].append({"profile_id": profile, "reason_code": reason,
                                    "concerns": deepcopy(concerns)})

    for profile in sorted(grouped):
        concerns = grouped[profile]
        if any(not isinstance(c.get("fixture_id"), str) or not c["fixture_id"]
               or not isinstance(c.get("reason"), str) or not c["reason"].strip()
               or not isinstance(c.get("concern_id"), str) or not c["concern_id"]
               or not isinstance(c.get("source_urls"), list) or not c["source_urls"]
               or not all(isinstance(url, str) and url for url in c["source_urls"])
               for c in concerns):
            unresolved(profile, "INVALID_CONFIRMATION", concerns)
            continue
        rows = _rows(result, profile)
        if not rows:
            unresolved(profile, "PROFILE_NOT_FOUND", concerns)
            continue
        signatures = {ticket_signature(row) for _, _, row in rows}
        if len(signatures) != 1 or not all(row.get("legs") for _, _, row in rows):
            unresolved(profile, "INCONSISTENT_OR_EMPTY_ALIASES", concerns)
            continue
        original = rows[0][2]
        blocked = {c["fixture_id"] for c in concerns}
        if not blocked <= _fixtures(original):
            unresolved(profile, "CONCERN_NOT_IN_CURRENT_TICKET", concerns)
            continue
        if (original.get("status") not in READY
                or any(row.get("publication_scope") == "SUSPENDED"
                       or row.get("team_status") == "SUSPENDED" for _, _, row in rows)):
            unresolved(profile, "ORIGINAL_NOT_ELIGIBLE", concerns)
            continue
        if not all(allows_new_ticket(row, now) for _, _, row in rows):
            unresolved(profile, "PREMATCH_LOCKOUT", concerns)
            continue
        anchors = _unique_package(result, exclude=profile)
        eligible = []
        for raw in options.get(profile, []):
            if not isinstance(raw, dict):
                continue
            option = deepcopy(raw)
            try:
                template = _template(option)
                if (template.get("profile_id") != profile
                        or template.get("status") not in READY
                        or blocked & _fixtures(template)
                        or not _eligible(option, now, report["date"])
                        or any(not _eligible({"quality": option["quality"],
                                              "recommendation": alias}, now, report["date"])
                               for key in ("recommendation", "ticket")
                               if isinstance(alias := option.get(key), dict))):
                    continue
                adjusted = _against(option, anchors)
                if adjusted is not None:
                    eligible.append(adjusted)
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                continue
        for option in sorted(eligible, key=_option_order):
            candidate = _replacement(result, profile, option)
            if candidate is None:
                continue
            if (not all(allows_new_ticket(row, now) for _, _, row in _rows(candidate, profile))
                    or not _package_exposure(candidate)["compliant"]
                    or not ready_exposure(candidate.get("tickets", []))["compliant"]):
                continue
            result = candidate
            diversification = result.get("diversification")
            if isinstance(diversification, dict):
                assigned = diversification.setdefault("assigned", {})
                if isinstance(assigned, dict):
                    summary = assigned.get(profile)
                    summary = summary if isinstance(summary, dict) else {}
                    summary.update(fixture_ids=sorted(_fixtures(_template(option))),
                                   quality=option["quality"],
                                   adjusted_quality=option["adjusted_quality"],
                                   **deepcopy(option["similarity"]))
                    best = summary.get("own_best_quality")
                    if type(best) in (int, float) and math.isfinite(best) and best > 0:
                        summary["retained_own_quality_ratio"] = option["quality"] / best
                    assigned[profile] = summary
            audit["changed_profiles"].append(profile)
            audit["changed"].append({"profile_id": profile,
                "old_signature": ticket_signature(original),
                "new_signature": ticket_signature(_template(option)),
                "avoided_fixture_ids": sorted(blocked), "concerns": deepcopy(concerns),
                "quality": option["quality"], "adjusted_quality": option["adjusted_quality"],
                "similarity": deepcopy(option["similarity"])})
            break
        else:
            unresolved(profile, "NO_ELIGIBLE_REPLACEMENT", concerns)
    audit["package_exposure"] = _package_exposure(result)
    audit["ready_exposure"] = ready_exposure(result.get("tickets", []))
    if audit["changed"] and isinstance(result.get("diversification"), dict):
        result["diversification"]["ready_exposure"] = deepcopy(audit["ready_exposure"])
    return result, audit
