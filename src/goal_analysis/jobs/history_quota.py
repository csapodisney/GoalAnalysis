"""Persisted authenticated quota facts shared by bounded history consumers."""
import json
from dataclasses import asdict
from datetime import datetime, timedelta

from goal_analysis.normalization.canonical import utc
from goal_analysis.providers.the_stats_api import TheStatsApiUsage


def normalized(state, *, now):
    if state is None:
        return None
    if "authenticated_quota" in state:
        return normalized(state["authenticated_quota"], now=now)
    if state.get("requests") == 0:  # a cached-only legacy batch is not a new receipt
        return None
    if (state.get("error_code") in {"TRANSPORT_UNAVAILABLE", "MISSING_OR_INVALID_API_KEY",
                                    "INVALID_RESPONSE_STATUS"} and state.get("http_status") is None):
        return None
    observed = utc(state["observed_at"])
    if observed > now:
        raise ValueError("FUTURE_QUOTA_STATE")
    usage = TheStatsApiUsage(**state["usage"])
    for name, value in asdict(usage).items():
        if value is not None and (type(value) is not int or value < (
                -1 if name in {"monthly_limit", "monthly_remaining"} else 0)):
            raise ValueError("INVALID_QUOTA_STATE")
    if all(value is None for value in asdict(usage).values()) and state.get("http_status") is None:
        return None
    result = {"provider": "the_stats_api", "observed_at": observed.isoformat(),
            "usage": asdict(usage), "status": state.get("status"),
            "http_status": state.get("http_status")}
    if state.get("_receipts"):
        result["_receipts"] = [normalized(row, now=now) for row in state["_receipts"]]
    return result


def receipts(states, *, now):
    found = {}
    for raw in states:
        state = normalized(raw, now=now)
        if state is None:
            continue
        for prior in state.pop("_receipts", []):
            if prior is not None:
                found[json.dumps(prior, sort_keys=True)] = prior
        found[json.dumps(state, sort_keys=True)] = state
    return list(found.values())


def _dimension(rows, name):
    def value(row):
        return row.get("http_status") if name == "http_status" else row["usage"][name]
    known = [row for row in rows if value(row) is not None]
    if not known:
        return None
    # Identical-time contradictory remaining values use the lower quota; a
    # denial/retry uses the stronger fence. No invented mixed usage numbers.
    return max(known, key=lambda row: (utc(row["observed_at"]),
        -value(row) if name.endswith("remaining") else value(row)))


def quota_block(state, *, now, reserve):
    rows = receipts([state], now=now)
    if not rows:
        return None
    state = _dimension(rows, "monthly_remaining")
    usage = TheStatsApiUsage(**state["usage"]) if state else TheStatsApiUsage()
    if (usage.monthly_remaining is not None and 0 <= usage.monthly_remaining <= reserve
            and (usage.monthly_reset is None or now.timestamp() < usage.monthly_reset)):
        return "QUOTA_RESERVED_FOR_DAILY_WORK"
    state = _dimension(rows, "minute_remaining")
    usage = TheStatsApiUsage(**state["usage"]) if state else TheStatsApiUsage()
    if usage.minute_remaining == 0:
        observed = datetime.fromisoformat(state["observed_at"])
        reset = usage.minute_reset if usage.minute_reset is not None else observed.timestamp() + 60
        if now.timestamp() < reset:
            return "QUOTA_RESERVED_FOR_DAILY_WORK"
    state = _dimension(rows, "retry_after")
    usage = TheStatsApiUsage(**state["usage"]) if state else TheStatsApiUsage()
    observed = datetime.fromisoformat(state["observed_at"]) if state else now
    if usage.retry_after is not None and now < observed + timedelta(seconds=usage.retry_after):
        return "PROVIDER_RETRY_AFTER"
    state = _dimension(rows, "http_status")
    if state and state.get("http_status") in {401, 403}:
        return "PROVIDER_ACCESS_REQUIRES_RECHECK"
    return None


def latest_quota(states, *, now, reserve):
    candidates = receipts(states, now=now)
    if not candidates:
        return None
    latest = max(candidates, key=lambda state: utc(state["observed_at"]))
    retained = {}
    for name in ("monthly_remaining", "minute_remaining", "retry_after", "http_status"):
        row = _dimension(candidates, name)
        if row is not None:
            retained[json.dumps(row, sort_keys=True)] = row
    # At most four original receipts preserve dimension-specific freshness.
    # A newer response missing monthly headers cannot erase a known month fence.
    if len(retained) > 1 or latest not in retained.values():
        latest = {**latest, "_receipts": list(retained.values())}
    return latest
