"""Calendar rolling cohorts with explicit missing days and current result labels.

Descriptive only: no fitted reliability, historical availability certificate,
stake recommendation, parameter change, or cross-cohort bankroll aggregation.
"""
from __future__ import annotations

import calendar
import math
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256

from .daily_ticket_kpis import build_daily_ticket_kpis

VERSION = "arthur-rolling-ticket-kpis-v1"
BERLIN = ZoneInfo("Europe/Berlin")
WINDOWS = (7, 30, 90)


def _anchor(as_of, month):
    value = as_of if as_of is not None else datetime.now(UTC)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Aware observation clock required")
        value = value.astimezone(BERLIN).date()
    if type(value) is not date:
        raise ValueError("Observation day required")
    if month is None:
        return value, False
    start = date.fromisoformat(month + "-01")
    if start.strftime("%Y-%m") != month:
        raise ValueError("month must be YYYY-MM")
    end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
    return min(value, end), start > value


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _money_admission(original, row, mode):
    if original.get("outcome", "PENDING") != row["outcome"]:
        return "OUTCOME_IDENTITY_REVIEW"
    if mode == "paper":
        if original.get("paper_eligible") is not True:
            return "PAPER_PRICE_NOT_ELIGIBLE"
        if any(not _finite(original.get(k)) for k in ("stake_eur","returned_eur","open_stake_eur","cash_balance_eur")):
            return "MISSING_OR_INVALID_PAPER_FINANCIAL"
        if original.get("settled") and not _finite(original.get("realized_net_eur")):
            return "MISSING_PAPER_SETTLED_NET"
        return None
    financial = original.get("financial") or {}
    if any(not _finite(financial.get(k)) for k in ("stake_eur", "payout_eur", "net_eur")):
        return "MISSING_OR_INVALID_RECORDED_FINANCIAL"
    if financial["stake_eur"] <= 0 or financial["payout_eur"] < 0:
        return "INVALID_RECORDED_STAKE_OR_RETURN"
    if mode == "research":
        return None if financial.get("basis") == "RESEARCH_SIMULATION" and not original.get("wager") else "RESEARCH_COHORT_MISMATCH"
    wager = original.get("wager") or {}
    if financial.get("basis") != "MANUALLY_RECORDED" or not wager:
        return "ACTUAL_COHORT_MISMATCH"
    if wager.get("stake_eur") != financial["stake_eur"]:
        return "RECORDED_STAKE_MISMATCH"
    # A verified loss/refund needs the recorded stake and outcome, not winning
    # odds. Do not hide a known loss merely because an old price is estimated.
    if (row["outcome"] == "LOST" and financial["payout_eur"] == 0
            and financial["net_eur"] == -financial["stake_eur"]):
        return None
    if (row["outcome"] == "VOID" and financial["payout_eur"] == financial["stake_eur"]
            and financial["net_eur"] == 0):
        return None
    confirmed = (original.get("settlement") or {}).get("bookmaker_return") or {}
    if wager.get("actual_price_source") == "USER_CONFIRMED" or (
        _finite(confirmed.get("payout_eur")) and confirmed["payout_eur"] >= 0
        and confirmed["payout_eur"] == financial["payout_eur"]
    ):
        return None
    return "ACTUAL_PRICE_OR_RETURN_NOT_VERIFIED"


def _paper_drawdown(items):
    days = defaultdict(list)
    for item in items:
        if item.get("settled") and _finite(item.get("realized_net_eur")):
            days[item["date"]].append(Decimal(str(item["realized_net_eur"])))
    balance = peak = drawdown = Decimal(0)
    for _, changes in sorted(days.items()):
        balance += sum(changes, start=Decimal(0))
        peak = max(peak, balance)
        drawdown = max(drawdown, peak-balance)
    return float(drawdown)


def _financial(rows, originals, summarize, mode, unassigned=0):
    kind = {"actual":"RECORDED_ACTUAL_SETTLED_CASH", "research":"INDICATIVE_RESEARCH_SIMULATION",
            "paper":"HYPOTHETICAL_EUR1_VERIFIED_ORIGINAL_QUOTES", "recommendations":"OUTCOMES_ONLY_NO_ASSUMED_STAKE"}[mode]
    if mode == "recommendations" or not rows:
        return {"basis":kind,"available":False,"complete_financial_coverage":False,
                "settled_net_eur_to_date":None,"max_drawdown_eur":None,
                "known_subset_settled_net_eur":None,"known_subset_max_drawdown_eur":None,
                "eligible_records":0,"excluded_records":len(rows),"missing_reasons":{},
                "unassigned_source_records":unassigned,
                "reason":"NO_ASSUMED_RECOMMENDATION_STAKES" if mode == "recommendations" else "NO_OBSERVED_RECORDS"}
    good, reasons = [], Counter()
    for row in rows:
        original = originals[row["source_record_sha256"]]
        reason = _money_admission(original, row, mode)
        if reason:
            reasons[reason] += 1
        else:
            good.append(original)
    summary = summarize(good) if good else None
    net_key = "realized_net_eur" if mode == "paper" else "net_eur"
    known_net = summary[net_key] if summary else None
    known_dd = (_paper_drawdown(good) if mode == "paper" else summary["max_drawdown_eur"]) if good else None
    complete = len(good) == len(rows) and not unassigned
    return {"basis":kind,"available":bool(good),"complete_financial_coverage":complete,
            "settled_net_eur_to_date":known_net if complete else None,
            "max_drawdown_eur":known_dd if complete else None,
            "known_subset_settled_net_eur":known_net,"known_subset_max_drawdown_eur":known_dd,
            "eligible_records":len(good),"excluded_records":len(rows)-len(good),
            "unassigned_source_records":unassigned,
            "missing_reasons":dict(reasons),"open_stake_eur":summary.get("open_stake_eur") if summary else None,
            "drawdown_basis":"ORIGINAL_DAY_AGGREGATED_SETTLED_NET_ZERO_WINDOW_OPENING" if mode == "paper"
            else "EXISTING_SETTLEMENT_OBSERVATION_ORDER_ZERO_WINDOW_OPENING",
            "prior_window_bankroll_peak_included":False,"api_costs_tax_or_bonus_inferred":False}


def _goals(days, requested_days, unassigned):
    closed = [d for d in days if d["day_results_complete"]]
    count_met = sum(d["confirmed_winning_tickets"] >= 3 for d in closed)
    joint = [False if d["confirmed_winning_tickets"] < 3 else
             d["winning_combined_odds_sum"] >= 30 if d["winning_odds_complete"] else None for d in closed]
    joint_met = sum(value is True for value in joint)
    unknown = sum(value is None for value in joint)
    n = len(closed)
    rate = lambda wins, count: 100*wins/count if count else None
    complete = n == requested_days and not unassigned
    return {"required_winners_per_day":3,"secondary_required_winning_odds_sum":30,
            "observed_closed_days":n,"observed_open_days":len(days)-n,
            "count_goal_met_closed_days":count_met,
            "count_goal_closed_observed_rate_pct":rate(count_met,n),
            "count_goal_full_calendar_rate_pct":rate(count_met,n) if complete else None,
            "joint_goal_met_closed_days":joint_met,"joint_goal_unknown_closed_days":unknown,
            "joint_goal_closed_observed_rate_pct":rate(joint_met,n) if not unknown else None,
            "joint_goal_known_subset_rate_pct":rate(joint_met,n-unknown),
            "joint_goal_full_calendar_rate_pct":rate(joint_met,n) if complete and not unknown else None,
            "denominator":"CLOSED_OBSERVED_TICKET_DAYS_ONLY_NOT_MISSING_OR_OPEN_DAYS"}


def build_rolling_ticket_kpis(records, summarize=None, *, mode, as_of=None, month=None, daily_kpis=None,
                              unassigned_input_records=0):
    if mode not in {"actual","research","recommendations","paper"}:
        raise ValueError("Unknown rolling KPI mode")
    if type(unassigned_input_records) is not int or unassigned_input_records < 0:
        raise ValueError("Invalid dropped-source count")
    end, future = _anchor(as_of,month)
    output = {"version":VERSION,"mode":mode,"status":"FUTURE_PERIOD" if future else "AVAILABLE",
              "window_end_date":end.isoformat(),"month":month,"decision_effect":"NONE",
              "basis":"ORIGINAL_TICKET_DAY_CURRENT_RESULTS_NOT_HISTORICAL_ASOF",
              "cross_cohort_results_additive":False,"windows":[]}
    if future:
        return output
    daily = daily_kpis if daily_kpis is not None else build_daily_ticket_kpis(records,mode=mode)
    if daily.get("status") != "AVAILABLE" or daily.get("mode") != mode:
        raise ValueError("Daily source not available")
    originals = {canonical_sha256(record):record for record in records}
    rows = daily["records"]
    hashes = {row["source_record_sha256"] for row in rows}
    if (daily["input_records"] != len(records) or len(hashes) != len(rows)
            or not hashes <= set(originals)
            or any(row["date"] != originals[row["source_record_sha256"]].get("date") for row in rows)):
        raise ValueError("Daily source cohort mismatch")
    # Exact deduplicated coverage, including invalid-date rows kept as unknown.
    if len(rows)+sum(daily["unassigned_records"].values()) != len(originals):
        raise ValueError("Daily source does not cover the whole original cohort")
    unassigned = sum(daily["unassigned_records"].values())+unassigned_input_records
    with localcontext() as context:
        context.prec = 100
        for size in WINDOWS:
            start = end-timedelta(days=size-1)
            selected = [r for r in rows if start.isoformat() <= r["date"] <= end.isoformat()]
            days = [d for d in daily["days"] if start.isoformat() <= d["date"] <= end.isoformat()]
            observed = {d["date"] for d in days}
            missing = [(start+timedelta(days=i)).isoformat() for i in range(size)
                       if (start+timedelta(days=i)).isoformat() not in observed]
            profiles = defaultdict(list)
            for row in selected:
                original = originals[row["source_record_sha256"]]
                version = original.get("strategy_version") or "UNKNOWN_LEGACY"
                profiles[str(row.get("profile_id") or "UNKNOWN"),str(version)].append(row)
            reliability = []
            for (profile,version), items in sorted(profiles.items()):
                counts = Counter(row["outcome"] for row in items)
                decided = counts["WON"]+counts["LOST"]
                legs = [leg for row in items
                        for leg in (originals[row["source_record_sha256"]].get("legs") or [])]
                fixtures = [leg["fixture_id"] for leg in legs if isinstance(leg,dict)
                            and isinstance(leg.get("fixture_id"),str) and leg["fixture_id"].strip()]
                source = originals[items[0]["source_record_sha256"]]
                reliability.append({"profile_id":profile,"profile_name":source.get("profile_name"),
                    "strategy_version":version,"records":len(items),"observed_days":len({r['date'] for r in items}),
                    "decided_ticket_days":len({r['date'] for r in items if r['outcome'] in {'WON','LOST'}}),
                    "won":counts["WON"],"lost":counts["LOST"],"pending":counts["PENDING"],
                    "review":counts["REVIEW"],"void":counts["VOID"],
                    "decided_win_rate_pct":100*counts["WON"]/decided if decided else None,
                    "unique_fixtures":len(set(fixtures)),"repeated_fixture_occurrences":len(fixtures)-len(set(fixtures)),
                    "unidentified_fixture_occurrences":len(legs)-len(fixtures),
                    "predictive_reliability_validated":False,"strategy_recommendation":None,
                    "sample_status":"DESCRIPTIVE_ONLY_REQUIRES_WALK_FORWARD_AND_DEPENDENCE_VALIDATION",
                    "financial":_financial(items,originals,summarize,mode,unassigned)})
            output["windows"].append({"window_days":size,"start_date":start.isoformat(),"end_date":end.isoformat(),
                "records":len(selected),"observed_days":len(days),"missing_calendar_days":len(missing),
                "missing_dates":missing,"unassigned_source_records":unassigned,
                "full_calendar_coverage":not missing and not unassigned,
                "goals":_goals(days,size,unassigned),
                "financial":_financial(selected,originals,summarize,mode,unassigned),
                "profile_reliability":reliability})
    return output


def safe_rolling_ticket_kpis(records, summarize=None, **kwargs):
    try:
        return build_rolling_ticket_kpis(records,summarize,**kwargs)
    except Exception:  # noqa: BLE001 - Optional reporting cannot veto stable accounting or selection.
        return {"version":VERSION,"mode":kwargs.get("mode"),"status":"UNAVAILABLE",
                "reason":"ROLLING_TICKET_KPI_ANALYSIS_FAILED","decision_effect":"NONE","windows":[]}
