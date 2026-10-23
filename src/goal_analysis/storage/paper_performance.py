"""Pure 1 EUR hypothetical accounting from original recommendation snapshots.

No bets, odds backfilling, database writes or changes to team qualification.
Current results are grouped by publication day, not by cash settlement time.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from goal_analysis.team.metrics import _quote_valid

from .daily_ticket_kpis import safe_daily_ticket_kpis
from .leg_error_analysis import safe_leg_error_analysis
from .performance_breakdowns import safe_breakdowns
from .rolling_ticket_kpis import safe_rolling_ticket_kpis

POLICY_VERSION = "paper-eur1-v1"
CENT, UNIT = Decimal("0.01"), Decimal("1.00")
BERLIN = ZoneInfo("Europe/Berlin")
REASONS = {
    "MISSING_ODDS": "Az eredeti szorzó hiányzik.",
    "INVALID_ODDS": "Az eredeti ár nem érvényes decimális szorzó.",
    "UNVERIFIED_QUOTE": "Az eredeti ár szolgáltatói eredete vagy piacazonossága nem igazolható.",
    "INCOMPATIBLE_BOOKMAKERS": "A választások nem azonos iroda, régió és pénznem árai.",
    "INVALID_PUBLICATION": "A kezdés előtti publikálás vagy a napi mérkőzésazonosság nem igazolható.",
    "INVALID_RECORD": "A mentett ajánlat azonosítója vagy láblistája hiányos.",
    "DUPLICATE_FIXTURE": "Egy mérkőzés többször szerepel a szelvényen.",
    "AMBIGUOUS_PROFILE_DAY": "Egy profilhoz ugyanarra a napra több kanonikusnak jelölt ajánlat tartozik.",
}
MONEY_FIELDS = ("stake_eur", "returned_eur", "realized_net_eur", "open_stake_eur", "cash_balance_eur")


def _time(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp if stamp.tzinfo and stamp.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None


def _price(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 1 < value <= 1000:
        return None
    return Decimal(str(value))


def _money(value):
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _identity(leg):
    return tuple(leg.get(key) for key in ("fixture_id", "market_key", "selection_key", "period"))


def _canonical(records):
    """Preserve the caller's canonical rows; never combine reruns or frozen copies."""
    groups, ignored = defaultdict(list), Counter()
    for record in records:
        if not isinstance(record, dict):
            ignored["invalid_input_records"] += 1
            continue
        if record.get("active") is False:
            ignored["archived_versions_ignored"] += 1
            continue
        if not record.get("legs"):
            ignored["no_ticket_records_ignored"] += 1
            continue
        key = str(record.get("date", "")), str(record.get("profile_id", ""))
        # Duplicate copies from joins must not charge the same saved ticket twice.
        comparable = {k: record.get(k) for k in ("recommendation_id", "run_id", "date", "profile_id",
                                                "created_at", "legs", "settlement", "outcome")}
        if any(comparable == previous[1] for previous in groups[key]):
            ignored["duplicate_copies_ignored"] += 1
        else:
            groups[key].append((record, comparable))
    output = []
    for key in sorted(groups):
        rows = groups[key]
        # A conflict remains visible, but no arbitrary version earns a payout.
        selected = min(rows, key=lambda row: (str(row[0].get("created_at", "")),
                                             str(row[0].get("recommendation_id", ""))))[0]
        ambiguous = len(rows) > 1
        if ambiguous:
            ignored["ambiguous_profile_days"] += 1
        output.append((selected, ambiguous))
    return output, dict(ignored)


def _quote_ok(leg):
    try:
        url = urlsplit(leg.get("quote_source_id") or "")
        if url.port not in (None, 443) or url.fragment:
            return False
        if leg.get("quote_available") is False:
            return False
        period, market, selection = leg.get("period"), leg.get("market_key"), leg.get("selection_key")
        if period not in {"FULL_TIME", "FIRST_HALF"}:
            return False
        if not ((market == "h2h" and selection in {"home", "away", "draw"})
                or (market == "btts" and selection in {"yes", "no"})
                or (isinstance(market, str) and re.fullmatch(r"totals_[0-9]+_5", market)
                    and selection in {"over", "under"})):
            return False
        return _quote_valid({"fixture_id": leg.get("fixture_id"), "market": f"{period}:{market}",
                             "selection": selection, "quote_provenance": leg})
    except (TypeError, ValueError):
        return False


def _outcome(record, originals):
    settlement = record.get("settlement")
    if not isinstance(settlement, dict) or not isinstance(settlement.get("legs"), list):
        return "PENDING" if record.get("outcome", "PENDING") == "PENDING" else "REVIEW", {}, "RESULT_NOT_VERIFIED"
    rows = settlement["legs"]
    if any(not isinstance(row, dict) for row in rows):
        return "REVIEW", {}, "INVALID_SETTLEMENT_LEGS"
    identities = [_identity(row) for row in rows]
    expected = [_identity(leg) for leg in originals]
    if len(set(identities)) != len(identities) or set(identities) != set(expected):
        return "REVIEW", {}, "SETTLEMENT_IDENTITY_MISMATCH"
    mapped = dict(zip(identities, rows, strict=True))
    statuses = []
    for leg in originals:
        row = mapped[_identity(leg)]
        status = row.get("outcome", "REVIEW")
        if status not in {"WON", "LOST", "VOID", "PENDING", "REVIEW"}:
            status = "REVIEW"
        # A cancellation/postponement alone is not an acknowledged refund.
        if status == "VOID" and row.get("reason") != "BOOKMAKER_VOID_CONFIRMED":
            status = "REVIEW"
        if row.get("decimal_price") is not None and row["decimal_price"] != leg.get("decimal_price"):
            status = "REVIEW"
        mapped[_identity(leg)] = {**row, "outcome": status}
        statuses.append(status)
    outcome = next((status for status in ("LOST", "REVIEW", "PENDING") if status in statuses),
                   "VOID" if set(statuses) == {"VOID"} else "WON")
    # Current recommendation ledger reports all-void rows as WON; individual
    # explicitly confirmed voids are authoritative for this paper refund policy.
    claimed = settlement.get("outcome", record.get("outcome"))
    permitted = {outcome, "WON"} if outcome == "VOID" else {outcome}
    if claimed not in permitted or record.get("outcome", claimed) not in permitted:
        return "REVIEW", mapped, "SETTLEMENT_OUTCOME_MISMATCH"
    return outcome, mapped, None if outcome in {"WON", "LOST", "VOID"} else "RESULT_PENDING_OR_REVIEW"


def _row(record, ambiguous):
    reasons = ["AMBIGUOUS_PROFILE_DAY"] if ambiguous else []
    originals = record.get("legs", [])
    if not isinstance(originals, list) or not originals or any(not isinstance(leg, dict) for leg in originals):
        originals = []
        reasons.append("INVALID_RECORD")
    for key in ("recommendation_id", "run_id", "profile_id"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            reasons.append("INVALID_RECORD")
    day = record.get("date")
    try:
        valid_day = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
    except ValueError:
        valid_day = False
    created = _time(record.get("created_at"))
    if not created or not valid_day:
        reasons.append("INVALID_PUBLICATION")
    prices, legs, contexts, fixture_ids = [], [], set(), []
    for leg in originals:
        kickoff = _time(leg.get("kickoff"))
        if (not created or not kickoff or kickoff <= created
                or kickoff.astimezone(BERLIN).date().isoformat() != day):
            reasons.append("INVALID_PUBLICATION")
        fixture = leg.get("fixture_id")
        if not isinstance(fixture, str) or not fixture:
            reasons.append("INVALID_RECORD")
        fixture_ids.append(str(fixture))
        price = _price(leg.get("decimal_price"))
        if price is None:
            reasons.append("MISSING_ODDS" if leg.get("decimal_price") is None else "INVALID_ODDS")
        else:
            prices.append(price)
        verified = _quote_ok(leg)
        if not verified:
            reasons.append("UNVERIFIED_QUOTE")
        context = tuple(leg.get(k) for k in ("bookmaker", "region", "currency"))
        if not all(isinstance(value, str) and value.strip() for value in context):
            reasons.append("INCOMPATIBLE_BOOKMAKERS")
        else:
            contexts.add(context)
        copied = {k: deepcopy(leg.get(k)) for k in (
            "fixture_id", "home_team", "away_team", "kickoff", "market_key", "selection_key", "period",
            "bookmaker", "region", "currency", "quote_provider", "quote_source_id", "provider_market_id",
            "candidate_id", "quote_candidate_id", "source_candidate_id", "quoted_at", "quote_timestamp_raw",
            'quote_observed_at', 'quote_source_sha256', 'quote_time_basis', 'provider_bookmaker',
            'competition', 'competition_type', 'source_provider', 'provider_league_id', 'api_football_league_id',
        )}
        copied.update(decimal_price=float(price) if price is not None else None, price_verified=verified and price is not None)
        legs.append(copied)
    if len(set(fixture_ids)) != len(fixture_ids):
        reasons.append("DUPLICATE_FIXTURE")
    if len(contexts) != 1:
        reasons.append("INCOMPATIBLE_BOOKMAKERS")
    product = math.prod(prices, start=UNIT) if originals and len(prices) == len(originals) else None
    if product is not None and (not product.is_finite() or product > Decimal(1000000)):
        reasons.append("INVALID_ODDS")
        product = None
    outcome, results, settlement_issue = _outcome(record, originals) if originals else ("REVIEW", {}, "NO_LEGS")
    for leg, original in zip(legs, originals, strict=True):
        leg["measured_outcome"] = results.get(_identity(original), {}).get("outcome", "PENDING")
    reasons = list(dict.fromkeys(reasons))
    eligible = not reasons
    settled = eligible and outcome in {"WON", "LOST", "VOID"}
    stake, returned, net, open_stake, cash = (None,) * 5
    if eligible:
        stake = UNIT
        if outcome == "WON":
            # Confirmed void legs have a hypothetical multiplier of one. Never
            # use actual wager stakes, accepted combo prices or bookmaker_return.
            multiplier = math.prod((price for leg, price in zip(originals, prices, strict=True)
                                    if results[_identity(leg)]["outcome"] != "VOID"), start=UNIT)
            returned = multiplier.quantize(CENT, rounding=ROUND_HALF_UP)
        elif outcome == "VOID":
            returned = UNIT
        else:
            returned = Decimal(0)
        net = returned - UNIT if settled else None
        open_stake = Decimal(0) if settled else UNIT
        cash = returned - UNIT
    item = {k: deepcopy(record.get(k)) for k in ("recommendation_id", "run_id", "profile_id", "profile_name",
                                                "date", "created_at", "publication_scope", "strategy_version")}
    item.update(
        policy_version=POLICY_VERSION, paper_eligible=eligible, exclusion_reasons=reasons,
        exclusion_messages=[REASONS[reason] for reason in reasons],
        original_combined_price=float(product) if product is not None else None,
        original_combined_price_decimal=format(product, "f") if product is not None else None,
        original_reported_combined_price=record.get("combined_price") if _price(record.get("combined_price")) else None,
        current_outcome=record.get("outcome", "PENDING"), outcome=outcome, settled=settled,
        settlement_issue=settlement_issue, legs=legs, assumed_stake_eur=1.0,
    )
    for key, value in zip(MONEY_FIELDS, (stake, returned, net, open_stake, cash), strict=True):
        item[key] = _money(value) if value is not None else None
    return item


def _summary(records):
    eligible = [row for row in records if row["paper_eligible"]]
    settled = [row for row in eligible if row["settled"]]
    outcomes = Counter(row["outcome"] for row in eligible)
    total = lambda rows, key: sum((Decimal(str(row[key])) for row in rows if row[key] is not None), Decimal(0))
    stake, returned = total(eligible, "stake_eur"), total(eligible, "returned_eur")
    settled_stake, net = total(settled, "stake_eur"), total(settled, "realized_net_eur")
    complete = len(records) == len(eligible)
    return {"records": len(records), "eligible_records": len(eligible), "excluded_records": len(records) - len(eligible),
            "settled_records": len(settled), "pending_records": outcomes["PENDING"], "review_records": outcomes["REVIEW"],
            "won": outcomes["WON"], "lost": outcomes["LOST"], "void": outcomes["VOID"],
            "total_stake_eur": _money(stake), "settled_stake_eur": _money(settled_stake),
            "open_stake_eur": _money(total(eligible, "open_stake_eur")),
            "total_returned_eur": _money(returned), "realized_net_eur": _money(net),
            "cash_balance_eur": _money(returned - stake),
            "roi_pct": _money(net / settled_stake * 100) if settled_stake else None,
            "all_record_assumed_stake_eur": float(len(records)),
            "excluded_assumed_stake_eur": float(len(records) - len(eligible)),
            "complete_result_available": complete,
            "full_all_record_realized_net_eur": _money(net) if complete else None,
            "all_records_settled": complete and len(settled) == len(records)}


def build_paper_performance(records, month=None, *, as_of=None):
    """One hypothetical EUR per canonical published recommendation, no actual cash."""
    if month is not None:
        try:
            valid = isinstance(month, str) and date.fromisoformat(month + "-01").strftime("%Y-%m") == month
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("month must be YYYY-MM")
    canonical, ignored = _canonical(records)
    with localcontext() as context:
        context.prec = 80
        all_rows = [_row(record, ambiguous) for record, ambiguous in canonical]
        selected = [row for row in all_rows if month is None or str(row.get("date", "")).startswith(month)]
        days, months, profiles = defaultdict(list), defaultdict(list), defaultdict(list)
        for row in selected:
            days[str(row.get("date") or "UNKNOWN")].append(row)
            months[str(row.get("date") or "UNKNOWN")[:7]].append(row)
            profiles[str(row.get("profile_id") or "UNKNOWN")].append(row)
        by_day, cumulative = [], Counter()
        for day, rows in sorted(days.items()):
            summary = _summary(rows)
            for source, target in (("total_stake_eur", "cumulative_stake_eur"),
                                   ("total_returned_eur", "cumulative_returned_eur"),
                                   ("realized_net_eur", "cumulative_realized_net_eur"),
                                   ("cash_balance_eur", "cumulative_cash_balance_eur")):
                cumulative[target] = Decimal(str(cumulative[target])) + Decimal(str(summary[source]))
            by_day.append({"date": day, **summary, **{key: _money(value) for key, value in cumulative.items()},
                           "cumulative_net_eur": _money(cumulative["cumulative_realized_net_eur"])})
        exclusions = Counter(reason for row in selected for reason in row["exclusion_reasons"])
        summary, overall_summary = _summary(selected), _summary(all_rows)
        if ignored.get("invalid_input_records"):
            # A dropped malformed input cannot be assigned to a month/profile.
            # Do not advertise a complete whole ledger after that omission.
            for totals in (summary, overall_summary):
                totals.update(complete_result_available=False, full_all_record_realized_net_eur=None,
                              all_records_settled=False)
        leg_analysis = safe_leg_error_analysis(selected, mode="paper")
        daily_kpis = safe_daily_ticket_kpis(selected, mode="paper", leg_analysis=leg_analysis)
        return {
            "mode": "HYPOTHETICAL_EUR1_ORIGINAL_QUOTES", "policy_version": POLICY_VERSION,
            "unit_stake_eur": 1.0, "currency": "EUR", "month": month, "summary": summary,
            "overall_summary": overall_summary, "by_day": by_day,
            "breakdowns": safe_breakdowns(selected, _summary, mode="paper"),
            "leg_error_analysis": leg_analysis,
            "daily_kpis": daily_kpis,
            "rolling_kpis": safe_rolling_ticket_kpis(all_rows, _summary, mode="paper", as_of=as_of,
                month=month, daily_kpis=daily_kpis if len(selected) == len(all_rows) else None,
                unassigned_input_records=ignored.get("invalid_input_records", 0)),
            "by_month": [{"month": key, **_summary(rows)} for key, rows in sorted(months.items())],
            "by_profile": [{"profile_id": key, "profile_name": rows[-1].get("profile_name") or key,
                            **_summary(rows)} for key, rows in sorted(profiles.items())],
            "records": sorted(selected, key=lambda row: (str(row.get("date", "")), str(row.get("created_at", "")),
                                                          str(row.get("recommendation_id", ""))), reverse=True),
            "coverage": {"complete": not exclusions and not ignored.get("invalid_input_records"),
                         "exclusion_counts": dict(exclusions), **ignored},
            "aggregation_scope": "VERIFIED_ORIGINAL_PRICE_RECORDS_ONLY",
            "cumulative_basis": "PUBLICATION_DAY_CURRENT_RESULTS_SELECTED_PERIOD_ZERO_OPENING",
            "rounding_policy": "DECIMAL_PRODUCT_THEN_ROUND_HALF_UP_PER_TICKET_TO_EUR_CENT",
            "notes": [
                "Minden megőrzött napi/profil ajánlathoz 1 EUR hipotetikus tét tartozik; nincs valódi fogadás vagy pénzmozgás.",
                "A pénzügyi részösszeg csak igazolt eredeti árú sorokból készül. Hiányos áreredet mellett a teljes csapat eredménye ismeretlen.",
                "A függő és ellenőrzésre váró tét nyitott összeg, nem realizált veszteség; a cash egyenleg a visszatérülés mínusz minden jogosult tét.",
                "Egyszerű kombinációnál egy igazolt vesztes láb lezárja a papírszelvényt akkor is, ha másik lába még függő vagy ellenőrzésre vár; a bizonytalan teljes lábazonosság nem számolható el.",
                "A kumulált sor a publikáció napjára csoportosított aktuális eredmény, nem múltbeli elszámolási eseménysor vagy valós bankroll.",
                "A kiválasztott időszak kumulált nyitóegyenlege nulla; a teljes időszak külön összesítésben látható.",
                "Az összszorzó eredeti egyedi árak elméleti szorzata; a jelenlegi ár és az elfogadott irodai kombináció nem igazolt.",
                "Az ár időbélyege nem kizáró feltétel. A közös mérkőzések és átfedő szelvények nem független megfigyelések.",
                "Nem része a 260 szelvényes Szent Grál minősítésnek; a tényleges fogadások összegeit és a szolgáltatási költségeket nem használja.",
            ],
        }
