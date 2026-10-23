"""As-of price comparisons from immutable local quotes; no provider requests.

A rising price is an observed market change, never proof of public betting
direction or its cause. Missing comparable history stays explicitly unknown.
"""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime
from urllib.parse import urlsplit

from goal_analysis.agents import canonical_sha256

VERSION = "arthur-comparable-quote-movement-v1"
IDENTITY_FIELDS = ("fixture_id", "market_key", "selection_key", "period", "settlement",
                   "bookmaker", "region", "currency", "quote_provider")
PROVIDER_HOSTS = {"api_football": "v3.football.api-sports.io",
                  'the_stats_api': 'api.thestatsapi.com',
                  "the_odds_api": "api.the-odds-api.com"}


def _time(value):
    if not isinstance(value, str):
        raise TypeError("An aware timestamp is required")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("An aware timestamp is required")
    return result


def _identity(quote):
    if not isinstance(quote, dict) or any(not isinstance(quote.get(key), str) or not quote[key].strip()
           for key in IDENTITY_FIELDS):
        raise ValueError("A complete quote identity is required")
    return tuple(quote[key] for key in IDENTITY_FIELDS)


def _verified_quote(quote, cutoff):
    identity = _identity(quote)
    price = quote.get("decimal_price")
    if (type(price) not in (int, float) or not math.isfinite(price) or price <= 1
            or quote.get("quote_available") is False):
        raise ValueError("A real decimal quote is required")
    source = quote.get("quote_source_id")
    if not isinstance(source, str):
        raise TypeError("A provider source reference is required")
    url = urlsplit(source)
    if (url.scheme != "https" or url.username or url.password
            or url.hostname != PROVIDER_HOSTS.get(quote["quote_provider"])):
        raise ValueError("A known provider source reference is required")
    if quote.get('quote_provider') == 'the_stats_api':
        from goal_analysis.providers.the_stats_odds import valid_quote_source

        if not valid_quote_source(quote):
            raise ValueError('Unverified native snapshot source')
        quoted_at = _time(quote.get('quote_observed_at'))
    else:
        quoted_at = _time(quote.get("quoted_at"))
    if quoted_at > cutoff:
        raise ValueError("A future quote cannot prove historical price movement")
    return identity, float(price), quoted_at


def quote_snapshot(candidates, now):
    """Store compact current provider observations in the next immutable run."""
    quotes = []
    keys = (*IDENTITY_FIELDS, "candidate_id", "decimal_price", "quoted_at", "quote_source_id",
            'quote_observed_at', 'quote_source_sha256', 'quote_time_basis', 'provider_bookmaker', 'provider_market_id',
            'bookmaker_name', 'bookmaker_id', 'quote_timestamp_raw', 'quote_timestamp_status', 'price_kind')
    for candidate in candidates:
        try:
            _verified_quote(candidate, now)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        quotes.append(deepcopy({key: candidate[key] for key in keys if key in candidate}))
    quotes.sort(key=lambda item: (tuple(item[key] for key in IDENTITY_FIELDS),
                                  item.get('quote_observed_at') or item.get("quoted_at") or '', item.get("candidate_id", "")))
    return {"schema_version": 1, "policy_version": VERSION,
            "observed_at": now.isoformat(), "quotes": quotes}


def _history_index(runs, now):
    result = {}
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("run_id"), str):
            continue
        try:
            observed = _time(run.get("observed_at"))
            available = _time(run.get("finished_at", run.get("observed_at")))
            if observed > now or available > now or available < observed:
                continue
            source_hash = canonical_sha256(run)
        except (TypeError, ValueError, OverflowError):
            continue
        snapshot = run.get("odds_history_snapshot", {})
        if isinstance(snapshot, dict) and isinstance(snapshot.get("quotes"), list):
            quotes = snapshot["quotes"]
        else:
            quotes = []
        # Legacy runs retained selected legs, not a full quote history. They are
        # usable only when the exact provider/book/market identity is complete.
        quotes = [*quotes, *(leg for group in ("tickets", "recommendations", "profile_proposals")
                             for item in run.get(group, []) if isinstance(item, dict)
                             for leg in item.get("legs", []) if isinstance(leg, dict))]
        for quote in quotes:
            try:
                identity, price, quoted_at = _verified_quote(quote, min(now, available))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            age = (now - quoted_at).total_seconds() / 3600
            if 24 <= age <= 48:
                result.setdefault(identity, []).append({
                    "price": price, "quoted_at": quoted_at, "source_run_id": run["run_id"],
                    "source_snapshot_sha256": source_hash,
                    "quote_source_id": quote["quote_source_id"],
                })
    return result


def annotate_odds_movement(enriched, historical_runs, now):
    """Return a separate persona snapshot, leaving DAILY input untouched."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Price movement requires an aware information cutoff")
    result = deepcopy(enriched)
    index = _history_index(historical_runs, now)
    for quote in result.get("candidates", []):
        movement = {"policy_version": VERSION, "observed_as_of": now.isoformat(),
                    "status": "INSUFFICIENT_HISTORY",
                    "interpretation": "Árváltozás; a fogadói tömeg iránya és az ok ebből nem ismert."}
        try:
            identity, price, quoted_at = _verified_quote(quote, now)
            movement["identity"] = dict(zip(IDENTITY_FIELDS, identity, strict=True))
            choices = [item for item in index.get(identity, []) if item["quoted_at"] < quoted_at]
            if choices:
                # Choose the latest available baseline in the declared window;
                # never cherry-pick the cheapest historical observation.
                baseline = min(choices, key=lambda item: (-item["quoted_at"].timestamp(),
                                                          item["source_run_id"],
                                                          item["source_snapshot_sha256"]))
                relative = price / baseline["price"] - 1
                movement.update(
                    status="VERIFIED_RISE" if relative >= 0.10 - 1e-12 else "NO_SIGNIFICANT_RISE",
                    baseline_price=baseline["price"], current_price=price,
                    relative_change=relative,
                    baseline_quoted_at=baseline["quoted_at"].isoformat(),
                    current_quoted_at=quoted_at.isoformat(),
                    window_hours=(quoted_at - baseline["quoted_at"]).total_seconds() / 3600,
                    baseline_age_hours=(now - baseline["quoted_at"]).total_seconds() / 3600,
                    source_run_id=baseline["source_run_id"],
                    source_snapshot_sha256=baseline["source_snapshot_sha256"],
                    baseline_quote_source_id=baseline["quote_source_id"],
                    current_quote_source_id=quote["quote_source_id"],
                )
                if quote.get('quote_provider') == 'the_stats_api':
                    movement['time_basis'] = 'LOCAL_SNAPSHOT_OBSERVATIONS'
                    movement['baseline_observed_at'] = movement.pop('baseline_quoted_at')
                    movement['current_observed_at'] = movement.pop('current_quoted_at')
                movement["evidence_id"] = "odds-movement:" + canonical_sha256(movement)
        except (KeyError, TypeError, ValueError, OverflowError):
            movement["status"] = "INVALID_QUOTE"
        quote["odds_movement"] = movement
    return result
