"""Descriptive proper scores and strict same-product closing-price comparisons.

No fitting, probability repair, ticket probability, or decision effect. Callers
must supply original prospective records and source-verified archive quotes.
The ledger reader supplies those original records; closing quotes are optional
and never fetched or inferred from receipts by this module.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime

VERSION = "arthur-forecast-quality-v1"
LOG_FLOOR = 1e-15


def _time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("AWARE_TIMESTAMP_REQUIRED")
    return value


def _probability(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def _order(record):
    try:
        stamp = _time(record.get("created_at"))
    except (TypeError, ValueError):
        # Invalid rows still reach their explicit exclusion path below.
        stamp = datetime.max.replace(tzinfo=UTC)
    return stamp, str(record.get("id", ""))


def _numeric(leg):
    kind = str(leg.get("confidence_kind", "")).upper()
    details = leg.get("model_details") or {}
    method = str(details.get("method", "")).upper()
    if any(word in kind + method for word in ("HEURISTIC", "EVIDENCE_STRENGTH", "NOT_PROBABILITY")):
        return False
    if kind == "UNCALIBRATED_SHARED_MODEL_ESTIMATE":
        values = details.get("shared_model_probabilities", {})
        weights = details.get("shared_model_weights", {})
        return (method == "SHARED_MODEL_WEIGHTED_MEAN" and isinstance(values, dict)
                and isinstance(weights, dict) and bool(values) and set(values) == set(weights)
                and all(_probability(v) for v in values.values())
                and all(_probability(w) and w > 0 for w in weights.values())
                and math.isclose(math.fsum(weights.values()), 1, abs_tol=1e-9)
                and math.isclose(math.fsum(weights[k] * p for k, p in values.items()),
                                 leg["model_probability"], abs_tol=1e-9))
    if kind == "UNCALIBRATED_MODEL_ESTIMATE":
        return True
    # Explicit original legacy model version is retained, never inferred from
    # deployment date or persona name. Its calibration remains unvalidated.
    version = str(leg.get("model_version", "")).lower()
    return version.startswith(("gauss-", "kepler-", "laplace-"))


def _scores(rows):
    n = len(rows)
    values = [(row["probability"], int(row["outcome"] == "WON")) for row in rows]
    bins = []
    for index in range(5):
        subset = [(p, y) for p, y in values if min(int(p * 5), 4) == index]
        count = len(subset)
        bins.append({"lower": index / 5, "upper": (index + 1) / 5, "count": count,
                     "mean_prediction": math.fsum(p for p, _ in subset) / count if count else None,
                     "observed_rate": math.fsum(y for _, y in subset) / count if count else None})
    return {
        "eligible_selections": n,
        "brier_binary": math.fsum((p - y) ** 2 for p, y in values) / n if n else None,
        "log_loss_binary": math.fsum(-math.log(max(LOG_FLOOR, p if y else 1 - p))
                                    for p, y in values) / n if n else None,
        "log_loss_floor": LOG_FLOOR,
        "log_loss_floor_applied": sum((p if y else 1 - p) < LOG_FLOOR for p, y in values),
        "bins": bins,
    }


def _identity(leg, quote):
    """Exact source-scoped descriptor, including settlement/line and cohort.

    No bookmaker name aliases, cross-provider fixture mapping, or default
    regulation-time settlement is invented. Missing cohort is not a wildcard.
    """
    fields = ("quote_provider", "bookmaker", "settlement", "region", "currency")
    if any(not isinstance(quote.get(key), str) or not quote[key] for key in fields):
        return None
    if "line" not in quote:
        return None
    return (quote["quote_provider"], str(leg["fixture_id"]), leg["market"], leg["selection"],
            *(quote[key] for key in fields[1:]), str(quote["line"]))


def _closing(records, quotes, cutoff):
    index, rejected = defaultdict(list), Counter()
    for quote in quotes:
        try:
            identity = _identity(quote, quote)
            p = quote.get("decimal_price")
            if identity is None or type(p) not in (int, float) or not math.isfinite(p) or p <= 1:
                raise ValueError("INCOMPLETE_CLOSING_PRODUCT_OR_PRICE")
            if quote.get("price_kind") != "CLOSING" or quote.get("quote_time_status") != "KNOWN":
                raise ValueError("NOT_EXPLICIT_PROVIDER_CLOSING_QUOTE")
            sha = str(quote.get("source_sha256", ""))
            if (not quote.get("quote_id") or not quote.get("source_id") or len(sha) != 64
                    or any(c not in "0123456789abcdef" for c in sha)):
                raise ValueError("MISSING_CLOSING_SOURCE_REFERENCE")
            clock, receipt = _time(quote.get("quoted_at")), _time(quote.get("observed_at"))
            ingested, sealed = _time(quote.get("ingested_at")), _time(quote.get("sealed_at"))
            if not clock <= receipt <= ingested <= sealed <= cutoff:
                raise ValueError("INVALID_CLOSING_AVAILABILITY_CLOCKS")
            index[identity].append((clock, p, quote))
        except (ValueError, TypeError, KeyError):
            rejected["INVALID_OR_UNSUPPORTED_CLOSING_QUOTE"] += 1
    rows, missing, seen = [], Counter(), set()
    for record in sorted(records, key=_order):
        for leg in record["legs"]:
            try:
                created, kickoff = _time(record["created_at"]), _time(leg["kickoff"])
                if not created < kickoff or created > cutoff or not record.get("original_run_match"):
                    raise ValueError("NOT_VERIFIED_PROSPECTIVE_ORIGINAL")
                entry = leg.get("quote_provenance", {})
                identity = _identity(leg, entry)
                if identity is None:
                    raise ValueError("MISSING_EXACT_ENTRY_PRODUCT")
                key = record["profile_id"], record["version"], identity
                if key in seen:
                    continue
                seen.add(key)
                price = leg.get("decimal_price")
                if type(price) not in (int, float) or not math.isfinite(price) or price <= 1:
                    raise ValueError("MISSING_ORIGINAL_ENTRY_PRICE")
                clock, receipt = _time(entry.get("quoted_at")), _time(entry.get("quote_observed_at"))
                if not clock <= receipt <= created:
                    raise ValueError("INVALID_ENTRY_QUOTE_CLOCK")
                sha = str(entry.get("quote_source_sha256", ""))
                if (len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)
                        or not entry.get("quote_source_id")):
                    raise ValueError("MISSING_ENTRY_SOURCE_REFERENCE")
                options = [q for q in index.get(identity, ()) if created <= q[0] < kickoff]
                if not options:
                    raise ValueError("NO_VERIFIED_SAME_PRODUCT_CLOSING_QUOTE")
                last_clock = max(q[0] for q in options)
                last = [q for q in options if q[0] == last_clock]
                if len({q[1] for q in last}) != 1:
                    raise ValueError("CONFLICTING_CLOSING_PRICES")
                _, closing, source = min(last, key=lambda q: str(q[2]["quote_id"]))
                rows.append({"record_id": record["id"], "profile_id": record["profile_id"],
                             "strategy_version": record["version"], "fixture_id": leg["fixture_id"],
                             "market": leg["market"], "selection": leg["selection"],
                             "entry_price": price, "closing_price": closing,
                             "clv_price_ratio_minus_one": price / closing - 1,
                             "closing_quote_id": source["quote_id"], "closing_source_id": source["source_id"],
                             "closing_quoted_at": source["quoted_at"]})
            except (TypeError, KeyError, ValueError) as exc:
                reason = str(exc) if str(exc) in {
                    "NOT_VERIFIED_PROSPECTIVE_ORIGINAL", "MISSING_EXACT_ENTRY_PRODUCT",
                    "MISSING_ORIGINAL_ENTRY_PRICE", "INVALID_ENTRY_QUOTE_CLOCK",
                    "MISSING_ENTRY_SOURCE_REFERENCE",
                    "NO_VERIFIED_SAME_PRODUCT_CLOSING_QUOTE", "CONFLICTING_CLOSING_PRICES",
                } else "MISSING_OR_INVALID_ENTRY_CLOCK"
                missing[reason] += 1
    return {"matched_selections": len(rows), "rows": rows, "missing_reasons": dict(missing),
            "rejected_closing_quotes": dict(rejected),
            "mean_clv_price_ratio_minus_one": math.fsum(r["clv_price_ratio_minus_one"] for r in rows) / len(rows) if rows else None,
            "definition": "original_entry_decimal_odds / same_bookmaker_closing_decimal_odds - 1",
            "fair_probability_or_profit": False,
            "receipt_is_not_provider_quote_time": True}


def evaluate(records, *, as_of, closing_quotes=()):
    """Score first original forecast per profile/strategy/model/exact product.

    Conflicting labels for the same product are not majority-voted. All such
    forecasts are excluded. Repeated products across profiles remain correlated
    and are counted explicitly; these are selected-sample descriptive scores.
    """
    cutoff = _time(as_of)
    forecasts, reasons, labels, ambiguous = {}, Counter(), defaultdict(set), set()
    for record in sorted(records, key=_order):
        for leg in record["legs"]:
            try:
                created, kickoff = _time(record["created_at"]), _time(leg["kickoff"])
                if created > cutoff or created >= kickoff or not record.get("original_run_match"):
                    raise ValueError("NOT_VERIFIED_PROSPECTIVE_ORIGINAL")
                product = str(leg["fixture_id"]), leg["market"], leg["selection"]
                if leg.get("outcome") in {"WON", "LOST"}:
                    labels[product].add(leg["outcome"])
                key = record["profile_id"], record["version"], leg["model_version"], *product
                # Choose the original forecast before eligibility filtering: a
                # later good probability must not replace a missing first one.
                previous = forecasts.setdefault(key, (record, leg))
                if (_time(previous[0]["created_at"]) == created
                        and (previous[1].get("model_probability"), previous[1].get("confidence_kind"),
                             previous[1].get("model_details")) !=
                            (leg.get("model_probability"), leg.get("confidence_kind"), leg.get("model_details"))):
                    ambiguous.add(key)
            except (KeyError, TypeError, ValueError):
                reasons["NOT_VERIFIED_PROSPECTIVE_ORIGINAL"] += 1
    rows = []
    for key, (record, leg) in forecasts.items():
        if key in ambiguous:
            reasons["CONFLICTING_FIRST_FORECASTS"] += 1
        elif len(labels[key[3:]]) > 1:
            reasons["CONFLICTING_PRODUCT_RESULTS"] += 1
        elif leg.get("outcome") not in {"WON", "LOST"}:
            reasons["NOT_BINARY_SETTLED"] += 1
        elif not _probability(leg.get("model_probability")):
            reasons["MISSING_OR_INVALID_PROBABILITY"] += 1
        elif not _numeric(leg):
            reasons["HEURISTIC_OR_UNKNOWN_PROBABILITY_KIND"] += 1
        else:
            rows.append({"profile_id": key[0], "strategy_version": key[1], "model_version": key[2],
                         "fixture_id": key[3], "market": key[4], "selection": key[5],
                         "record_id": record["id"], "created_at": record["created_at"],
                         "probability": leg["model_probability"], "outcome": leg["outcome"]})
    groups = defaultdict(list)
    for row in rows:
        groups[row["profile_id"], row["strategy_version"], row["model_version"], row["market"]].append(row)
    return {"schema": VERSION, "as_of": cutoff.isoformat(), **_scores(rows),
            "validated": False, "walk_forward_certified": False, "decision_effect": "NONE",
            "basis": "First original selected-leg numeric forecasts; selection-biased, correlated sample.",
            "unique_fixtures": len({r["fixture_id"] for r in rows}),
            "unique_products": len({(r["fixture_id"], r["market"], r["selection"]) for r in rows}),
            "excluded": dict(reasons), "rows": rows,
            "by_profile_strategy_model_market": [
                {"profile_id": key[0], "strategy_version": key[1], "model_version": key[2],
                 "market": key[3], **_scores(batch)} for key, batch in sorted(groups.items())],
            "closing_line_value": _closing(records, closing_quotes, cutoff)}
