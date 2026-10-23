"""Read-only daily counts and sums of winning ticket odds, never a new bet.

Days are original ticket dates with current verified outcomes, not historical
as-of snapshots. Shared fixtures and the different accounting modes overlap.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, localcontext

from goal_analysis.agents import canonical_sha256

from .leg_error_analysis import build_leg_error_analysis

VERSION = "arthur-daily-ticket-kpis-v1"


def _odds(value, *, exact=False):
    if exact and isinstance(value, str) and len(value) <= 100:
        try:
            value = Decimal(value)
        except ArithmeticError:
            return None
    elif type(value) in (int, float):
        try:
            if not math.isfinite(value):
                return None
            value = Decimal(str(value))
        except (ArithmeticError, ValueError):
            return None
    else:
        return None
    return value if value.is_finite() and value > 1 and abs(value.as_tuple().exponent) <= 100 else None


def _wire(value):
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _prices(original, result, mode):
    """Original nominal odds and a confirmed effective winning multiplier.

    Accepted combo odds do not disclose accepted void-leg prices. A confirmed
    payout/stake ratio may include tax/bonus and is not silently called odds.
    """
    if mode == "paper":
        nominal = _odds(original.get("original_combined_price_decimal"), exact=True)
        if not original.get("paper_eligible"):
            return nominal, None, "PAPER_ORIGINAL_PRICE_NOT_ELIGIBLE"
        prices = [_odds(leg.get("decimal_price")) for leg in original.get("legs", [])
                  if leg.get("measured_outcome") == "WON"]
        if not prices or any(p is None for p in prices):
            return nominal, None, "MISSING_EFFECTIVE_WINNING_LEG_PRICE"
        return nominal, math.prod(prices, start=Decimal(1)), None
    if mode == "actual":
        wager = original.get("wager") or {}
        if wager.get("actual_price_source") != "USER_CONFIRMED":
            return None, None, "ACCEPTED_WAGER_PRICE_NOT_VERIFIED"
        nominal = _odds(wager.get("actual_combined_price"))
    else:
        nominal = _odds(original.get("combined_price"))
    if nominal is None:
        return None, None, "MISSING_OR_INVALID_ORIGINAL_COMBINED_PRICE"
    if result["leg_outcome_counts"]["VOID"]:
        return nominal, None, "PARTIAL_VOID_EFFECTIVE_ODDS_NOT_VERIFIED"
    return nominal, nominal, None


def _summary(rows):
    counts = Counter(row["outcome"] for row in rows)
    winners = [row for row in rows if row["outcome"] == "WON"]
    known = [row["effective_winning_odds"] for row in winners if row["effective_winning_odds"] is not None]
    nominal = [row["original_winning_odds"] for row in winners if row["original_winning_odds"] is not None]
    full = len(known) == len(winners)
    known_sum = sum(known, start=Decimal(0))
    return {"records": len(rows), "confirmed_winning_tickets": len(winners),
            "confirmed_losing_tickets": counts["LOST"], "void_tickets": counts["VOID"],
            "pending_tickets": counts["PENDING"], "review_tickets": counts["REVIEW"],
            "day_results_complete": not counts["PENDING"] and not counts["REVIEW"],
            "winning_combined_odds_sum": _wire(known_sum) if full else None,
            "winning_combined_odds_sum_decimal": format(known_sum, "f") if full else None,
            "known_winning_odds_sum": _wire(known_sum),
            "known_winning_odds_sum_decimal": format(known_sum, "f"),
            "winning_odds_complete": full, "priced_winning_tickets": len(known),
            "missing_winning_odds_tickets": len(winners)-len(known),
            "winning_original_nominal_odds_sum": _wire(sum(nominal, start=Decimal(0)))
            if len(nominal) == len(winners) else None,
            "void_affected_winners": sum(row["void_affected"] for row in winners),
            "missing_winning_odds_reasons": dict(Counter(row["price_issue"] for row in winners if row["price_issue"]))}


def build_daily_ticket_kpis(records, *, mode, leg_analysis=None):
    if mode not in {"actual", "research", "recommendations", "paper"}:
        raise ValueError("Unknown daily KPI mode")
    analysis = leg_analysis if leg_analysis is not None else build_leg_error_analysis(records, mode=mode)
    if analysis.get("status") == "UNAVAILABLE" or analysis.get("mode") != mode:
        raise ValueError("Exact result analysis unavailable")
    originals = {canonical_sha256(record): record for record in records}
    if {row['source_record_sha256'] for row in analysis['records']} != set(originals):
        raise ValueError("Cached leg analysis does not cover this exact cohort")
    rows, unassigned = [], Counter()
    with localcontext() as context:
        context.prec = 100
        for result in analysis["records"]:
            original = originals[result["source_record_sha256"]]
            day = original.get("date")
            try:
                if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                    raise ValueError("Invalid original ticket day")
            except ValueError:
                unassigned["INVALID_ORIGINAL_TICKET_DAY"] += 1
                continue
            outcome = result["ticket_outcome_recorded"]
            if not result["ticket_leg_outcomes_consistent"] or result["cross_record_result_conflict"]:
                outcome = "REVIEW"
            elif result["leg_outcome_counts"]["VOID"] == result["leg_count"] and result["leg_count"]:
                outcome = "VOID"  # legacy all-void WON is a refund, not a winner
            nominal, effective, reason = _prices(original, result, mode) if outcome == "WON" else (None, None, None)
            rows.append({"record_id": result["record_id"], "date": day, "outcome": outcome,
                         "profile_id": original.get("profile_id"),
                         "source_record_sha256": result["source_record_sha256"],
                         "original_winning_odds": nominal, "effective_winning_odds": effective,
                         "void_affected": bool(result["leg_outcome_counts"]["VOID"]),
                         "price_issue": reason,
                         "paper_financially_eligible": original.get("paper_eligible") if mode == "paper" else None})
        days = defaultdict(list)
        for row in rows:
            days[row["date"]].append(row)
        output = {"version": VERSION, "mode": mode, "status": "AVAILABLE", "decision_effect": "NONE",
                  "basis": "ORIGINAL_TICKET_DATE_WITH_CURRENT_RESULTS_NOT_HISTORICAL_ASOF",
                  "odds_sum_definition": "SUM_OF_EFFECTIVE_WINNING_TICKET_DECIMAL_ODDS_NOT_PRODUCT_OR_NET_PROFIT",
                  "price_basis": "USER_CONFIRMED_ACCEPTED_COMBO_PRICE" if mode == "actual" else
                  "VERIFIED_ORIGINAL_LEG_PRODUCT_VOID_LEGS_MULTIPLIER_ONE" if mode == "paper" else
                  "RECORDED_INDICATIVE_COMBO_PRICE_NOT_ACTUAL_CASH",
                  "cohorts_additive": False, "shared_fixtures_are_independent": False,
                  "input_records": len(records), "distinct_records": analysis["summary"]["distinct_records"],
                  "duplicate_exact_copies_ignored": analysis["summary"]["duplicate_exact_copies_ignored"],
                  "unassigned_records": dict(unassigned),
                  "days": [{"date": day, **_summary(items)} for day, items in sorted(days.items())],
                  "observed_records_summary": _summary(rows),
                  "all_input_days_assigned": not unassigned}
        output["records"] = [{**row, "original_winning_odds": _wire(row["original_winning_odds"]),
                              "effective_winning_odds": _wire(row["effective_winning_odds"])} for row in rows]
        return output


def safe_daily_ticket_kpis(records, *, mode, leg_analysis=None):
    try:
        return build_daily_ticket_kpis(records, mode=mode, leg_analysis=leg_analysis)
    except Exception:  # noqa: BLE001 - Optional measurements must not hide settlement views.
        return {"version": VERSION, "mode": mode, "status": "UNAVAILABLE",
                "reason": "DAILY_TICKET_KPI_ANALYSIS_FAILED", "decision_effect": "NONE"}
