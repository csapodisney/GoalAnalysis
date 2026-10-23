"""Persistent odds-only polling, independent of Astra and immutable tickets."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from goal_analysis.agents import canonical_sha256

VERSION = "arthur-odds-only-monitor-v1"
QUOTE_FIELDS = (
    "candidate_id", "fixture_id", "home_team", "away_team", "competition",
    "kickoff", "market_key", "selection_key", "period", "settlement", "bookmaker",
    "region", "currency", "quote_provider", "decimal_price", "quoted_at", "quote_source_id",
    'quote_observed_at', 'quote_source_sha256', 'quote_time_basis', 'provider_bookmaker', 'provider_market_id',
    'bookmaker_name', 'bookmaker_id', 'quote_timestamp_raw', 'quote_timestamp_status', 'price_kind',
)


def _aware(value):
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Odds monitoring requires an aware clock")
    return stamp.astimezone(UTC)


@dataclass(frozen=True)
class MonitorPolicy:
    interval_seconds: int = 1800
    lockout_seconds: int = 900
    max_requests_per_minute: int = 3
    max_queries_per_tick: int = 6
    lease_seconds: int = 120
    daily_credit_limits: dict = field(default_factory=lambda: {"api_football": 120, "the_odds_api": 12})
    quota_reserve_limits: dict = field(default_factory=dict)


class OddsMonitor:
    def __init__(self, root, *, policy=None, clock=lambda: datetime.now(UTC), shadow=False):
        if type(shadow) is not bool:
            raise ValueError('Boolean monitor scope required')
        self.root, self.clock = Path(root), clock
        self.shadow = shadow
        self.path = _monitor_path(self.root, shadow)
        self.policy = policy or MonitorPolicy()
        for value in (self.policy.interval_seconds, self.policy.lockout_seconds,
                      self.policy.max_requests_per_minute, self.policy.max_queries_per_tick,
                      self.policy.lease_seconds):
            if type(value) is not int or value <= 0:
                raise ValueError("Monitor limits must be positive integers")
        if any(type(value) is not int or value < 0 for value in (
                *self.policy.daily_credit_limits.values(), *self.policy.quota_reserve_limits.values())):
            raise ValueError("Daily monitor credit limits must be nonnegative integers")

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("CREATE TABLE IF NOT EXISTS monitor_queries (query_key TEXT PRIMARY KEY, provider TEXT, state_json TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS monitor_providers (provider TEXT PRIMARY KEY, state_json TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS monitor_attempts (id INTEGER PRIMARY KEY, provider TEXT, at TEXT, cost INTEGER)")
        db.execute("CREATE TABLE IF NOT EXISTS monitor_snapshots (snapshot_id TEXT PRIMARY KEY, query_key TEXT, provider TEXT, observed_at TEXT, quotes_json TEXT)")
        return db

    @staticmethod
    def _state(db, table, key, value):
        row = db.execute(f"SELECT state_json FROM {table} WHERE {key}=?", (value,)).fetchone()
        return json.loads(row[0]) if row else {}

    @staticmethod
    def _save(db, table, key, value, state, *, provider=None):
        payload = json.dumps(state, sort_keys=True, allow_nan=False)
        if table == "monitor_queries":
            db.execute("INSERT INTO monitor_queries VALUES (?,?,?) ON CONFLICT(query_key) DO UPDATE SET state_json=excluded.state_json",
                       (value, provider, payload))
        else:
            db.execute("INSERT INTO monitor_providers VALUES (?,?) ON CONFLICT(provider) DO UPDATE SET state_json=excluded.state_json", (value, payload))

    def _unlocked(self, query, now):
        fixtures, kickoffs = query.get("fixture_ids"), query.get("kickoff_by_fixture", {})
        if not isinstance(fixtures, list) or not fixtures:
            return False
        try:
            return all(_aware(kickoffs[fixture]) > now + timedelta(seconds=self.policy.lockout_seconds)
                       for fixture in fixtures)
        except (KeyError, TypeError, ValueError):
            return False

    def _claim(self, query, now):
        provider, key = query["provider"], query["query_key"]
        cost = query.get("estimated_cost", 1)
        if (provider not in self.policy.daily_credit_limits or not isinstance(key, str) or not key
                or type(cost) is not int or cost < 1):
            return "INVALID_QUERY", None
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            state = self._state(db, "monitor_queries", "query_key", key)
            shared = self._state(db, "monitor_providers", "provider", provider)

            def blocked(reason):
                if state.get("status") != reason:
                    state.update(status=reason, last_check_at=now.isoformat())
                    self._save(db, "monitor_queries", "query_key", key, state, provider=provider)
                    db.commit()
                return reason, None

            for name, result in (("lease_until", "BUSY"), ("retry_at", "BACKOFF")):
                if state.get(name) and _aware(state[name]) > now:
                    return result, None
            if state.get("last_success_at") and _aware(state["last_success_at"]) + timedelta(seconds=self.policy.interval_seconds) > now:
                return "CACHED", None
            if shared.get("retry_at") and _aware(shared["retry_at"]) > now:
                return blocked("PROVIDER_BACKOFF")
            remaining = shared.get("remaining_quota")
            if (type(remaining) is int and shared.get("quota_checked_at")
                    and _aware(shared["quota_checked_at"]) + timedelta(hours=24) > now):
                if remaining < cost:
                    return blocked("QUOTA_PAUSED")
                if remaining - cost < self.policy.quota_reserve_limits.get(provider, 0):
                    return blocked("QUOTA_RESERVED")
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            spent = db.execute("SELECT COALESCE(SUM(cost),0) FROM monitor_attempts WHERE provider=? AND at>=?",
                               (provider, day_start)).fetchone()[0]
            burst = db.execute("SELECT COUNT(*) FROM monitor_attempts WHERE provider=? AND at>?",
                               (provider, (now - timedelta(minutes=1)).isoformat())).fetchone()[0]
            if spent + cost > self.policy.daily_credit_limits[provider]:
                return blocked("DAILY_BUDGET")
            if burst >= self.policy.max_requests_per_minute:
                return blocked("MINUTE_BUDGET")
            token = uuid4().hex
            state.update(lease_token=token, lease_until=(now + timedelta(seconds=self.policy.lease_seconds)).isoformat(),
                         last_attempt_at=now.isoformat(), status="FETCHING")
            cursor = db.execute("INSERT INTO monitor_attempts(provider,at,cost) VALUES (?,?,?)", (provider, now.isoformat(), cost))
            state["attempt_id"] = cursor.lastrowid
            if type(remaining) is int and remaining >= 0:
                shared["remaining_quota"] = max(0, remaining - cost)
            self._save(db, "monitor_queries", "query_key", key, state, provider=provider)
            self._save(db, "monitor_providers", "provider", provider, shared)
            db.commit()
            return "CLAIMED", token
        finally:
            db.close()

    def _finish(self, query, token, now, *, result=None, error=None):
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            key, provider = query["query_key"], query["provider"]
            state = self._state(db, "monitor_queries", "query_key", key)
            if state.get("lease_token") != token:
                return "LEASE_LOST"
            shared = self._state(db, "monitor_providers", "provider", provider)
            state.pop("lease_until", None)
            state.pop("lease_token", None)
            if error is not None:
                failures = state.get("consecutive_failures", 0) + 1
                retry_after = getattr(error, "retry_after_seconds", None)
                status = getattr(error, "status_code", None)
                delay = min(3600, 60 * 2 ** min(failures - 1, 6))
                if type(retry_after) in (int, float) and retry_after >= 0:
                    delay = max(delay, min(86400, retry_after))
                # Deterministic short jitter prevents synchronized provider retries.
                delay += int(canonical_sha256([key, failures])[:4], 16) % 6
                state.update(status="RATE_LIMITED" if status == 429 else "FETCH_FAILED",
                             consecutive_failures=failures, retry_at=(now + timedelta(seconds=delay)).isoformat())
                if status in {401, 403, 429}:
                    shared.update(retry_at=state["retry_at"], status=state["status"])
                if getattr(error, "quota_exhausted", False):
                    shared.update(remaining_quota=0, quota_checked_at=now.isoformat(), status="QUOTA_PAUSED")
                remaining = getattr(error, "remaining_quota", None)
                if type(remaining) is int and remaining >= 0:
                    shared.update(remaining_quota=remaining, quota_checked_at=now.isoformat())
                actual_cost = getattr(error, "actual_cost", None)
                if type(actual_cost) is int and actual_cost >= 0:
                    db.execute("UPDATE monitor_attempts SET cost=? WHERE id=?", (actual_cost, state.get("attempt_id")))
                if (getattr(error, "unexpected_cost", False)
                        or (type(actual_cost) is int and actual_cost > query.get("estimated_cost", 1))):
                    shared.update(status="UNEXPECTED_COST", retry_at=(now + timedelta(hours=24)).isoformat())
                # Error strings and request URLs are intentionally never persisted.
            else:
                if not isinstance(result, dict) or not isinstance(result.get("quotes"), list):
                    raise ValueError("Monitor adapter returned an invalid quote snapshot")
                quotes = []
                for quote in result["quotes"]:
                    if not isinstance(quote, dict) or quote.get("fixture_id") not in query["fixture_ids"]:
                        continue
                    if not self._unlocked({"fixture_ids": [quote["fixture_id"]], "kickoff_by_fixture": query["kickoff_by_fixture"]}, now):
                        continue
                    quotes.append({field: quote[field] for field in QUOTE_FIELDS if field in quote})
                snapshot_id = canonical_sha256([provider, key, now.isoformat(), quotes])
                db.execute("INSERT OR IGNORE INTO monitor_snapshots VALUES (?,?,?,?,?)",
                           (snapshot_id, key, provider, now.isoformat(), json.dumps(quotes, sort_keys=True, allow_nan=False)))
                state.update(status="UPDATED", last_success_at=now.isoformat(), snapshot_id=snapshot_id,
                             quote_count=len(quotes), consecutive_failures=0)
                state.pop("retry_at", None)
                remaining = result.get("remaining_quota")
                if type(remaining) is int and remaining >= 0:
                    shared.update(remaining_quota=remaining, quota_checked_at=now.isoformat())
                shared.update(status="UPDATED")
                shared.pop("retry_at", None)
                actual_cost = result.get("actual_cost")
                if type(actual_cost) is int and actual_cost >= 0:
                    db.execute("UPDATE monitor_attempts SET cost=? WHERE id=?", (actual_cost, state.get("attempt_id")))
                    if actual_cost > query.get("estimated_cost", 1):
                        shared.update(status="UNEXPECTED_COST", retry_at=(now + timedelta(hours=24)).isoformat())
            state.pop("attempt_id", None)
            self._save(db, "monitor_queries", "query_key", key, state, provider=provider)
            self._save(db, "monitor_providers", "provider", provider, shared)
            db.commit()
            return state["status"]
        finally:
            db.close()

    def tick(self, queries, fetch):
        results, calls = [], 0
        # Missing/old successful snapshots precede freshly cached ones. Stable
        # provider-key ordering alone would starve later fixtures on every tick.
        known = {row["query_key"]: row for row in monitor_status(self.root, _aware(self.clock()), shadow=self.shadow)["queries"]}
        ordered = sorted(queries, key=lambda query: (
            known.get(query.get("query_key"), {}).get("last_success_at", ""),
            query.get("provider", ""), query.get("query_key", ""),
        ))
        for query in ordered:
            now = _aware(self.clock())
            if not self._unlocked(query, now):
                results.append({"provider": query.get("provider"), "status": "LOCKED_OR_INVALID"})
                continue
            if calls >= self.policy.max_queries_per_tick:
                results.append({"provider": query["provider"], "status": "TICK_BUDGET"})
                continue
            status, token = self._claim(query, now)
            if token:
                calls += 1
                try:
                    result = fetch(query)
                    status = self._finish(query, token, _aware(self.clock()), result=result)
                except Exception as error:  # noqa: BLE001 - never persist provider text/credentials
                    status = self._finish(query, token, _aware(self.clock()), error=error)
            results.append({"provider": query["provider"], "query_key": query["query_key"], "status": status})
        return {"version": VERSION, "observed_at": _aware(self.clock()).isoformat(),
                "status": "UPDATED" if any(row["status"] == "UPDATED" for row in results) else "NO_CHANGE",
                "provider_calls": calls, "queries": results, "astra_calls": 0}


def _monitor_path(root, shadow):
    if type(shadow) is not bool:
        raise ValueError('Boolean monitor scope required')
    return Path(root)/('data/arthur/prospective-odds/shadow-monitor.sqlite3' if shadow else 'data/arthur/odds-monitor.sqlite3')


def monitor_history(root, now, lookback_hours=48, *, shadow=False):
    """Read compact immutable price snapshots; never create a database or schema."""
    path = _monitor_path(root, shadow)
    if not path.is_file():
        return []
    now = _aware(now)
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10) as db:
            rows = db.execute("SELECT snapshot_id,observed_at,quotes_json FROM monitor_snapshots WHERE observed_at>=? AND observed_at<=? ORDER BY observed_at",
                              ((now - timedelta(hours=lookback_hours)).isoformat(), now.isoformat())).fetchall()
        return [{"run_id": "odds-monitor-" + identity, "observed_at": observed, "finished_at": observed,
                 "odds_history_snapshot": {"quotes": json.loads(quotes)}} for identity, observed, quotes in rows]
    except (sqlite3.Error, ValueError, OSError):
        return []


def saved_monitor_input(root, now):
    """Use the latest existing immutable report for each upcoming Berlin day."""
    path = Path(root) / "data/arthur/ledger.sqlite3"
    if not path.is_file():
        return {"fixtures": [], "recommendations": []}
    now = _aware(now)
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10) as db:
            rows = db.execute("SELECT date,snapshot_json FROM portfolio_runs ORDER BY observed_at DESC LIMIT 100").fetchall()
        seen_days, fixtures, recommendations, quotes = set(), {}, [], []
        for day, payload in rows:
            if day in seen_days:
                continue
            report = json.loads(payload)
            if not report.get("fixtures"):
                continue
            seen_days.add(day)
            recommendations.extend(report.get("recommendations", []))
            quotes.extend(report.get("odds_history_snapshot", {}).get("quotes", []))
            for fixture in report.get("fixtures", []):
                try:
                    kickoff = _aware(fixture["kickoff"])
                    if now + timedelta(minutes=15) < kickoff <= now + timedelta(days=2):
                        fixtures.setdefault(fixture["fixture_id"], fixture)
                except (KeyError, TypeError, ValueError):
                    continue
        return {"fixtures": sorted(fixtures.values(), key=lambda fixture: (fixture["kickoff"], fixture["fixture_id"])),
                "recommendations": recommendations, "odds_history_snapshot": {"quotes": quotes}}
    except (sqlite3.Error, ValueError, OSError):
        return {"fixtures": [], "recommendations": []}


def saved_monitor_fixtures(root, now):
    return saved_monitor_input(root, now)["fixtures"]


def monitor_status(root, now, *, shadow=False):
    """Read-only operational diagnostics, safe to expose in the dashboard."""
    path = _monitor_path(root, shadow)
    if not path.is_file():
        return {"version": VERSION, "status": "NOT_STARTED", "queries": [], "providers": {},
                "local_check_seconds": 60, "provider_refresh_seconds": 1800}
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10) as db:
            queries = [{"query_key": key, "provider": provider, **json.loads(payload)}
                       for key, provider, payload in db.execute("SELECT query_key,provider,state_json FROM monitor_queries")]
            providers = {provider: json.loads(payload) for provider, payload
                         in db.execute("SELECT provider,state_json FROM monitor_providers")}
        for query in queries:
            query.pop("lease_token", None)
            if query.get("last_success_at"):
                query["seconds_since_success"] = max(0, (_aware(now) - _aware(query["last_success_at"])).total_seconds())
        return {"version": VERSION, "status": "AVAILABLE", "interval_seconds": 1800,
                "local_check_seconds": 60, "provider_refresh_seconds": 1800,
                "lockout_seconds": 900, "queries": queries, "providers": providers,
                "astra_calls": 0, "immutable_tickets_modified": False}
    except (sqlite3.Error, ValueError, OSError):
        return {"version": VERSION, "status": "UNAVAILABLE", "queries": [], "providers": {}}
