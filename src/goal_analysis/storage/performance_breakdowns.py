"""Read-only ticket-cohort analytics using each existing accounting policy.

A multi-league/market ticket belongs once to each matching cohort. Its full
ticket result is conditional cohort performance, never allocated leg profit.
Overlapping tables must not be summed. No external metadata joins or decisions.
"""

from __future__ import annotations

import math
from collections import defaultdict

VERSION = "arthur-ticket-cohort-breakdowns-v1"
DIMENSIONS = ("profile", "market", "league", "competition_type", "odds_band", "leg_count")
UNKNOWN = "UNKNOWN"


def _label(value):
    if isinstance(value, str) and value.strip() and value.strip().upper() not in {"UNKNOWN", "UNSPECIFIED"}:
        return value.strip()
    return UNKNOWN


def _league(leg):
    provider, identifier = _label(leg.get("source_provider")), leg.get("provider_league_id")
    if provider != UNKNOWN and ((isinstance(identifier, str) and _label(identifier) != UNKNOWN)
                                or (type(identifier) is int and identifier > 0)):
        return f"{provider}:competition:{identifier}"
    identifier = leg.get("api_football_league_id")
    if ((isinstance(identifier, str) and _label(identifier) != UNKNOWN)
            or (type(identifier) is int and identifier > 0)):
        return f"api_football:competition:{identifier}"
    label = _label(leg.get("competition"))
    return "recorded-label:" + label if label != UNKNOWN else UNKNOWN


def _price(record, mode):
    if mode == "paper":
        return record.get("original_combined_price")
    if mode == "actual":
        wager = record.get("wager")
        return wager.get("actual_combined_price") if isinstance(wager, dict) else None
    return record.get("combined_price")


def odds_band(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 1:
        return UNKNOWN
    if value < 3:
        return "(1,3)"
    if value < 10:
        return "[3,10)"
    if value < 20:
        return "[10,20)"
    if value <= 50:
        return "[20,50]"
    return "(50,100]" if value <= 100 else "(100,+inf)"


def _members(record, mode):
    raw = record.get("legs")
    valid_legs = isinstance(raw, list) and all(isinstance(leg, dict) for leg in raw)
    legs = [leg if isinstance(leg, dict) else {} for leg in raw] if isinstance(raw, list) else []
    markets = set()
    for leg in legs:
        period, market = _label(leg.get("period")), _label(leg.get("market_key"))
        markets.add(period + ":" + market if UNKNOWN not in (period, market) else UNKNOWN)
    return {"profile": {_label(record.get("profile_id"))}, "market": markets or {UNKNOWN},
        "league": {_league(leg) for leg in legs} or {UNKNOWN},
        "competition_type": {_label(leg.get("competition_type")) for leg in legs} or {UNKNOWN},
        "odds_band": {odds_band(_price(record, mode))},
        "leg_count": {str(len(legs)) if valid_legs and legs else UNKNOWN}}


def build_breakdowns(records, summarize, *, mode):
    """Preserve actual/research/paper sample and accounting semantics exactly.

    `summarize` is the existing policy's pure aggregation. In paper mode a
    partially priced group has only priced-subset ROI; full-group ROI is null.
    Missing classification remains an UNKNOWN cohort, never an inferred league.
    """
    if mode not in {"actual", "research", "paper"}:
        raise ValueError("Unknown breakdown accounting mode")
    tables = {}
    assignments = [(record, _members(record, mode)) for record in records]
    for dimension in DIMENSIONS:
        groups = defaultdict(list)
        for record, members in assignments:
            for label in sorted(members[dimension]):
                groups[label].append(record)
        overlap = any(len(members[dimension]) > 1 for _, members in assignments)
        rows = []
        for label, items in sorted(groups.items()):
            summary = summarize(items)
            won, lost = summary.get("won", 0), summary.get("lost", 0)
            roi = summary.get("roi_pct") if mode == "paper" else summary.get("yield_pct")
            complete = summary.get("complete_result_available", True)
            rows.append({"label": label, "records_in_group": len(items),
                "record_ids": [row.get("ticket_id", row.get("recommendation_id")) for row in items],
                "decided_hit_rate_pct": 100 * won / (won + lost) if won + lost else None,
                "decided_hit_rate_count": won + lost,
                "roi_pct": roi if complete else None,
                "priced_subset_roi_pct": roi,
                "complete_financial_coverage": complete,
                "metrics": summary})
        tables[dimension] = {"records": len(records),
            "membership_count": sum(len(items) for items in groups.values()),
            "unknown_records": len(groups.get(UNKNOWN, [])), "groups_overlap": overlap,
            "group_totals_additive": not overlap, "rows": rows}
    return {"version": VERSION, "mode": mode, "decision_effect": "NONE",
        "record_count": len(records), "dimensions": tables,
        "roi_denominator": "SETTLED_STAKE_IN_EXISTING_ACCOUNTING_POLICY",
        "hit_rate_basis": "EXISTING_MODE_ELIGIBLE_WON_LOST_ROWS_EXCLUDING_PENDING_REVIEW_VOID",
        "odds_basis": "ACCEPTED_WAGER_PRICE" if mode == "actual" else
            "ORIGINAL_LEG_PRICE_PRODUCT" if mode == "paper" else "RECORDED_RESEARCH_TICKET_PRICE",
        "league_identity_basis": "RECORDED_PROVIDER_ID_OR_NAMESPACED_ORIGINAL_LABEL_NO_FUZZY_JOIN",
        "attribution": "FULL_TICKET_CONDITIONAL_COHORT_RESULTS_NOT_LEG_PROFIT",
        "cross_dimension_totals_additive": False,
        "notes": ["A mixed ticket appears once in every applicable market/league/type cohort.",
                  "Overlapping cohort stakes, returns and counts must not be added together.",
                  "Cohort ROI is descriptive full-ticket performance, not causal league or market profit.",
                  "Missing classification stays UNKNOWN; unavailable financial coverage stays null."]}


def safe_breakdowns(records, summarize, *, mode):
    """A failed additive audit cannot hide the existing outcome/cash series."""
    try:
        return build_breakdowns(records, summarize, mode=mode)
    except Exception:  # noqa: BLE001 - Optional read-only analytics boundary.
        return {"version": VERSION, "mode": mode, "status": "UNAVAILABLE",
                "reason": "PERFORMANCE_BREAKDOWN_AUDIT_FAILED", "decision_effect": "NONE"}
