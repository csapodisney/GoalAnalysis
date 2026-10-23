"""Offline, provider-scoped league measurements. Never selects or promotes bets.

The caller supplies a witnessed fixture universe and normalized response frames.
An empty response is still a frame: it supersedes older prices. Receipt age is
reported separately from the provider quote delay. Successful cached responses
are not an HTTP-attempt denominator. No network, database or file writes occur.
"""
from collections import Counter, defaultdict
from decimal import Decimal
from statistics import mean

from goal_analysis.normalization.canonical import utc

MARKETS = {
    "h2h": frozenset({"home", "draw", "away"}),
    "totals": frozenset({"over", "under"}),
    "btts": frozenset({"yes", "no"}),
}
REQUESTED = tuple((market, period) for period in ("FULL_TIME", "FIRST_HALF") for market in MARKETS)
HALF_ALIASES = {"h2h_h1": "h2h", "h2h_3_way_h1": "h2h", "totals_h1": "totals", "btts_h1": "btts"}
REGULATION = {"REGULATION", "REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES"}


def _ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "ratio": numerator / denominator if denominator else None}


def _distribution(values):
    return {"count": len(values), "min": min(values) if values else None,
            "mean": mean(values) if values else None, "max": max(values) if values else None}


def _key(row):
    return row["provider"], row["fixture_id"]


def _product(row):
    return tuple(row.get(k) for k in ("market_key", "period", "line", "selection_key",
        "settlement", "payout_rule", "line_basis", "region", "currency", "price_kind"))


def measure_odds_coverage(*, leagues, fixtures, frames, as_of, universe_scope,
                          attempts=None, max_receipt_age_seconds=1800):
    """Measure only this declared universe, not a provider's worldwide coverage.

    Frame records must be the output of the existing odds normalizers. Names
    are never matched. Cross-provider differences remain unmeasurable here:
    provider-scoped fixture/book IDs are not an audited product crosswalk.
    `attempts=None` means absent telemetry; [] means a witnessed empty log.
    """
    cutoff = utc(as_of)
    if (not isinstance(universe_scope, str) or not universe_scope.strip()
            or type(max_receipt_age_seconds) is not int or max_receipt_age_seconds < 0):
        raise ValueError("EXPLICIT_SCOPE_AND_NONNEGATIVE_TIMING_REQUIRED")
    league_map = {r["league_id"]: r["name"] for r in leagues}
    if len(league_map) != len(leagues):
        raise ValueError("DUPLICATE_LEAGUE")
    by_fixture, issues = {}, Counter()
    duplicates = set()
    for fixture in fixtures:
        key = _key(fixture)
        if key in by_fixture or key in duplicates:
            duplicates.add(key)
            by_fixture.pop(key, None)
            issues["DUPLICATE_FIXTURE_ID"] += 1
            continue
        if fixture["league_id"] not in league_map:
            raise ValueError("UNDECLARED_LEAGUE")
        if utc(fixture["observed_at"]) > cutoff:
            issues["FUTURE_FIXTURE"] += 1
            continue
        by_fixture[key] = fixture
    latest, ambiguous = {}, set()
    for frame in frames:
        key, receipt = _key(frame), utc(frame["observed_at"])
        if receipt > cutoff:
            issues["FUTURE_RESPONSE"] += 1
            continue
        if key not in by_fixture:
            issues["OUTSIDE_FIXTURE_UNIVERSE"] += 1
            continue
        previous = latest.get(key)
        if previous is None or receipt > utc(previous["observed_at"]):
            latest[key] = frame
            ambiguous.discard(key)
        elif receipt == utc(previous["observed_at"]) and frame != previous:
            ambiguous.add(key)
    for key in ambiguous:
        latest.pop(key)
        issues["AMBIGUOUS_RESPONSE_AT_SAME_TIME"] += 1
    grouped_attempts = defaultdict(list)
    seen_attempts = set()
    if attempts is not None:
        for attempt in attempts:
            if attempt["attempt_id"] in seen_attempts:
                raise ValueError("DUPLICATE_HTTP_ATTEMPT")
            seen_attempts.add(attempt["attempt_id"])
            start = utc(attempt["started_at"])
            end = utc(attempt["finished_at"]) if attempt.get("finished_at") else None
            if end is not None and end < start:
                raise ValueError("NEGATIVE_HTTP_DURATION")
            if start > cutoff:
                issues["FUTURE_HTTP_ATTEMPT"] += 1
                continue
            if _key(attempt) not in by_fixture:
                issues["UNATTRIBUTABLE_HTTP_ATTEMPT"] += 1
                continue
            # A response after cutoff must not resolve a then-pending attempt.
            grouped_attempts[by_fixture[_key(attempt)]["league_id"]].append(
                (attempt, (end - start).total_seconds() if end and end <= cutoff else None))
    results = []
    for league, name in league_map.items():
        members = {k for k, f in by_fixture.items() if f["league_id"] == league}
        responses = {k: latest[k] for k in members if k in latest}
        counts, delays, ages, fresh_ages = Counter(), [], [], []
        complete = Counter()
        market_fixtures = defaultdict(set)
        any_price, supported_price, recent_price = set(), set(), set()
        book_spreads = []
        for key, frame in responses.items():
            receipt = utc(frame["observed_at"])
            age = (cutoff - receipt).total_seconds()
            ages.append(age)
            counts["parser_rejected_fragments"] += len(frame.get("rejected", []))
            counts["parser_unsupported_fragments"] += len(frame.get("unsupported", []))
            groups, cross_book, distinct = defaultdict(set), defaultdict(list), {}
            for row in frame["records"]:
                if (_key(row) != key or utc(row["observed_at"]) != receipt
                        or row.get("source_sha256") != frame["source_sha256"]):
                    raise ValueError("QUOTE_PARENT_MISMATCH")
                price = Decimal(row["decimal_price"])
                if not price.is_finite() or price <= 1:
                    raise ValueError("INVALID_DECIMAL_PRICE")
                if row["price_kind"] != "LAST_SEEN":
                    counts["non_last_seen_records"] += 1
                    continue
                identity = (row["bookmaker"], _product(row))
                if identity in distinct and distinct[identity] != row:
                    raise ValueError("CONFLICTING_QUOTE_IN_ONE_RESPONSE")
                distinct[identity] = row
            for row in distinct.values():
                counts["last_seen_prices"] += 1
                any_price.add(key)
                counts["mapping_" + row["mapping_status"]] += 1
                status = row["quote_time_status"]
                counts["quote_time_" + status] += 1
                quote = utc(row["quoted_at"]) if row.get("quoted_at") is not None else None
                if status == "KNOWN" and quote is not None and quote <= receipt:
                    delays.append((receipt - quote).total_seconds())
                elif quote is not None or status == "KNOWN":
                    raise ValueError("QUOTE_CLOCK_STATUS_MISMATCH")
                if row["mapping_status"] != "SUPPORTED":
                    continue
                supported_price.add(key)
                if age <= max_receipt_age_seconds:
                    recent_price.add(key)
                family, period = row["market_key"], row["period"]
                if family in HALF_ALIASES and period == "FIRST_HALF":
                    family = HALF_ALIASES[family]
                if ((family, period) not in REQUESTED or row["settlement"] not in REGULATION
                        or row.get("payout_rule") not in (None, "BINARY_NO_PUSH")):
                    continue
                line = row.get("line")
                if (family == "totals") != (line is not None):
                    continue
                groups[(row["bookmaker"], family, period, line)].add(row["selection_key"])
                cross_book[_product(row)].append(row)
            covered = {(family, period) for (_, family, period, _), selections in groups.items()
                       if selections == MARKETS[family]}
            for market in covered:
                market_fixtures[market].add(key)
            complete[key] = len(covered)
            # Same provider/native fixture/product/response only. This is price
            # dispersion between books, NOT a cross-provider discrepancy test.
            for rows in cross_book.values():
                if len({r["bookmaker"] for r in rows}) < 2:
                    continue
                values = [Decimal(r["decimal_price"]) for r in rows]
                book_spreads.append(float(max(values) / min(values) - 1))
            if key in recent_price:
                fresh_ages.append(age)
        attempt_rows = grouped_attempts[league]
        finished = [(a, duration) for a, duration in attempt_rows if duration is not None]
        errors = sum(bool(a.get("error")) or type(a.get("http_status")) is not int
                     or not 200 <= a["http_status"] < 300 for a, _ in finished)
        results.append({
            "league_id": league, "name": name, "expected_events": len(members),
            "responses": len(responses), "events_with_any_last_seen_price": _ratio(len(any_price), len(members)),
            "events_with_supported_price": _ratio(len(supported_price), len(members)),
            "events_with_recent_receipt": _ratio(len(recent_price), len(members)),
            "market_coverage": {f"{family}:{period}": _ratio(len(market_fixtures[(family, period)]), len(members))
                                for family, period in REQUESTED},
            "requested_market_slots": _ratio(sum(complete.values()), len(members) * len(REQUESTED)),
            "counts": dict(sorted(counts.items())),
            "receipt_age_seconds": _distribution(ages),
            "recent_receipt_age_seconds": _distribution(fresh_ages),
            "provider_quote_to_receipt_seconds": _distribution(delays),
            "quote_delay_status": "MEASURED" if delays else "NO_VALID_PROVIDER_QUOTE_CLOCK",
            "cross_provider_price_difference": {"pair_count": 0, "value": None,
                "status": "NO_VERIFIED_CONTEMPORANEOUS_PRODUCT_PAIRS"},
            "within_provider_cross_book_relative_spread": _distribution(book_spreads),
            "cross_book_scope": "SAME_RESPONSE_LAST_SEEN_NOT_SYNCHRONOUS_PROVIDER_QUOTES",
            "http": {"telemetry": "SUPPLIED_ATTEMPT_LOG" if attempts is not None else "MISSING_ATTEMPT_LOG",
                "attempts": len(attempt_rows) if attempts is not None else None,
                "completed": len(finished) if attempts is not None else None,
                "pending_at_cutoff": len(attempt_rows) - len(finished) if attempts is not None else None,
                "error_rate": _ratio(errors, len(finished)) if attempts is not None else _ratio(None, None),
                "completed_latency_seconds": _distribution([d for _, d in finished])},
        })
    return {"version": "league-odds-measurements-v1", "mode": "SHADOW", "as_of": cutoff.isoformat(),
            "universe_scope": universe_scope, "market_scope": "COMPLETE_SELECTION_SET_PER_BOOK_AND_LINE",
            "receipt_freshness_is_quote_freshness": False, "live_promotion": False,
            "max_receipt_age_seconds": max_receipt_age_seconds,
            "issues": dict(sorted(issues.items())), "leagues": results}
