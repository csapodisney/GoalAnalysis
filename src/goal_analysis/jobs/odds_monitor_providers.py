"""Exact, bounded provider queries for the independent pre-match odds monitor.

Uses existing clients and canonical parsers. No fixture discovery, language
model, automatic wagering, credential storage or implicit network call occurs
while planning. The monitor core owns durable budgets/cache and single flight.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.daily_223_live import EXTRA_MARKETS
from goal_analysis.jobs.daily_223_candidates import assemble_daily223_candidates
from goal_analysis.providers.api_football_odds import normalize_odds

VERSION = "arthur-odds-monitor-provider-queries-v1"
LOCKOUT = timedelta(minutes=15)
PROVIDERS = frozenset({"api_football", "the_odds_api", 'the_stats_api'})


class MonitorFetchError(RuntimeError):
    """Only static public diagnostics may cross the provider boundary."""

    def __init__(self, *, status_code=None, retry_after_seconds=None, quota_exhausted=False,
                 reason="PROVIDER_UNAVAILABLE", remaining_quota=None, actual_cost=None,
                 unexpected_cost=False):
        super().__init__(reason)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.quota_exhausted = bool(quota_exhausted)
        self.reason = reason
        self.remaining_quota = remaining_quota
        self.actual_cost = actual_cost
        self.unexpected_cost = bool(unexpected_cost)


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo is not None and result.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _clock(now):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("An aware monitor clock is required")
    return now


def _public_config(config):
    return {
        "account_region": config.get("account_region", "DE"),
        "currency": config.get("currency", "EUR"),
        "odds_region": config.get("odds_region", "eu"),
        "preferred_bookmakers": deepcopy(config.get("preferred_bookmakers", [])),
        "team_aliases": deepcopy(config.get("team_aliases", {})),
        "extra_markets": sorted(set(config.get("extra_markets", [])) & set(EXTRA_MARKETS)),
    }


def _saved_quotes(report):
    snapshot = report.get("odds_history_snapshot", {})
    if isinstance(snapshot, dict) and isinstance(snapshot.get("quotes"), list):
        yield from (quote for quote in snapshot["quotes"] if isinstance(quote, dict))
    for key in ("tickets", "recommendations", "profile_proposals"):
        for item in report.get(key, []):
            if isinstance(item, dict):
                yield from (leg for leg in item.get("legs", []) if isinstance(leg, dict))


def _source_mappings(report, fixtures):
    events, books = defaultdict(set), defaultdict(set)
    for quote in _saved_quotes(report):
        identifier = quote.get("fixture_id")
        if identifier not in fixtures:
            continue
        try:
            url = urlsplit(quote.get("quote_source_id", ""))
            if url.scheme != "https" or url.username or url.password:
                continue
            if quote.get("quote_provider") == "the_odds_api":
                parts = url.path.strip("/").split("/")
                if (url.hostname == "api.the-odds-api.com" and len(parts) == 6
                        and parts[:2] == ["v4", "sports"] and parts[3] == "events"
                        and parts[5] == "odds" and parts[2] == fixtures[identifier]["sport_key"]
                        and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", parts[4])):
                    events[identifier].add(parts[4])
            elif quote.get("quote_provider") == "api_football":
                query = parse_qs(url.query)
                book = query.get("bookmaker", [])
                if (url.hostname == "v3.football.api-sports.io" and url.path == "/odds"
                        and query.get("fixture") == [fixtures[identifier]["api_football_fixture_id"]]
                        and len(book) == 1 and re.fullmatch(r"[1-9][0-9]*", book[0])
                        and str(quote.get("bookmaker", "")).endswith("_api_football_" + book[0])):
                    books[identifier].add(int(book[0]))
        except (ValueError, TypeError):
            continue
    return events, books


def build_monitor_queries(fixtures, now, config):
    """Plan stable API-Football fixture and Odds API sport/date/extra queries.

    Pass a saved report to reuse its exact quote-derived event/book references;
    a fixture list is also supported. Unknown event mappings do not cause extra
    discovery calls. Every query includes its public normalization context.
    """
    _clock(now)
    report = fixtures if isinstance(fixtures, dict) else {}
    rows = report.get("fixtures", []) if report else fixtures
    rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    counts = Counter(row.get("fixture_id") for row in rows if isinstance(row.get("fixture_id"), str))
    leagues = {str(row.get("api_football_id")): row for row in config.get("leagues", [])
               if isinstance(row, dict)}
    known = {}
    for row in rows:
        identifier, kickoff = row.get("fixture_id"), _time(row.get("kickoff"))
        api_id = str(row.get("api_football_fixture_id", ""))
        league = leagues.get(str(row.get("api_football_league_id")), {})
        sport = row.get("sport_key") or league.get("odds_sport_key")
        season = row.get("season", league.get("season"))
        native = row.get('source_provider') == 'the_stats_api'
        if native:
            from goal_analysis.normalization.live_identity import fixture_identity

            try:
                fixture_identity(row)
            except (ValueError, TypeError):
                continue
        if ((not native and (not re.fullmatch(r"[1-9][0-9]*", api_id) or identifier != "api_football:" + api_id))
                or counts[identifier] != 1 or kickoff is None or kickoff <= now + LOCKOUT
                or row.get("provider_status") != "NS" or type(season) is not int
                or not isinstance(sport, str) or not re.fullmatch(r"soccer_[a-z0-9_]+", sport)
                or (not native and not re.fullmatch(r"[1-9][0-9]*", str(row.get("api_football_league_id", ""))))
                or any(not isinstance(row.get(key), str) or not row[key].strip()
                       for key in ("home_team", "away_team"))):
            continue
        fixture = deepcopy(row)
        fixture.update(season=season, sport_key=sport,
                       target_date=kickoff.astimezone(ZoneInfo("Europe/Berlin")).date().isoformat())
        if not native:
            fixture.update(api_football_fixture_id=api_id, api_football_league_id=str(row['api_football_league_id']))
        known[identifier] = fixture
    events, books = _source_mappings(report, known)
    public = _public_config(config)
    enabled = set(config.get("monitor_providers", PROVIDERS)) & PROVIDERS
    queries = []

    def add(provider, params, rows, cost, kind):
        rows = sorted(rows, key=lambda row: row["fixture_id"])
        # TTL belongs to the external request, not the changing set of unlocked
        # fixtures parsed from it. Removing one near-kickoff fixture must never
        # turn the same sport/date request into a new billable cache key.
        identity = {"provider": provider, "kind": kind, "params": params}
        queries.append({"provider": provider, "query_key": provider + ":" + canonical_sha256(identity),
                        "kind": kind, "params": deepcopy(params), "estimated_cost": cost,
                        "fixture_ids": [row["fixture_id"] for row in rows],
                        "kickoff_by_fixture": {row["fixture_id"]: row["kickoff"] for row in rows},
                        "fixtures": deepcopy(rows), "normalization_config": deepcopy(public),
                        "policy_version": VERSION})

    if 'the_stats_api' in enabled:
        for fixture in known.values():
            if fixture.get('source_provider') == 'the_stats_api':
                add('the_stats_api', {'match_id': fixture['provider_fixture_id'],
                    'markets': sorted({'h2h', 'totals', *public['extra_markets']})},
                    [fixture], 1, 'NATIVE_FIXTURE_ODDS')
    if "api_football" in enabled:
        configured_book = config.get("api_football_bookmaker_id")
        for identifier, fixture in sorted(known.items()):
            if fixture.get('source_provider') == 'the_stats_api':
                continue
            params = {"fixture": int(fixture["api_football_fixture_id"]), "page": 1,
                      "timezone": "Europe/Berlin"}
            book = configured_book if type(configured_book) is int and configured_book > 0 else (
                next(iter(books[identifier])) if len(books[identifier]) == 1 else None)
            if book is not None:
                params["bookmaker"] = book
            add("api_football", params, [fixture], 1, "FIXTURE_ODDS")
    if "the_odds_api" in enabled:
        groups = defaultdict(list)
        for fixture in known.values():
            # Locally configured synthetic sport keys have no Odds API product.
            if not fixture["sport_key"].startswith("soccer_arthur_"):
                groups[(fixture["sport_key"], fixture["target_date"])].append(fixture)
        for (sport, target), grouped in sorted(groups.items()):
            add("the_odds_api", {"sport_key": sport, "date": target,
                                 "markets": ["h2h", "totals"], "region": public["odds_region"]},
                grouped, 2, "BULK_ODDS")
        extras = public["extra_markets"]
        if extras:
            owners = Counter(next(iter(value)) for value in events.values() if len(value) == 1)
            for identifier, options in sorted(events.items()):
                if len(options) != 1 or owners[next(iter(options))] != 1:
                    continue
                fixture = known[identifier]
                if fixture["sport_key"].startswith("soccer_arthur_"):
                    continue
                add("the_odds_api", {"sport_key": fixture["sport_key"], "event_id": next(iter(options)),
                                     "markets": extras, "region": public["odds_region"]},
                    [fixture], len(extras), "EXTRA_ODDS")
    return sorted(queries, key=lambda item: (item["provider"], item["query_key"]))


def _safe_error(error, now, *, quota_exhausted=False, remaining_quota=None, actual_cost=None,
                estimated_cost=1):
    code = getattr(error, "status_code", None)
    retry = getattr(error, "retry_after_seconds", None)
    current = error
    for _ in range(4):
        if current is None:
            break
        candidate = getattr(current, "code", None)
        if type(candidate) is int and 100 <= candidate <= 599:
            code = candidate
        headers = getattr(current, "headers", None)
        if headers is not None:
            normalized = {key.casefold(): str(value) for key, value in headers.items()}
            for name in ("x-ratelimit-requests-remaining", "x-requests-remaining"):
                if normalized.get(name, "").isdigit():
                    remaining_quota = int(normalized[name])
            if normalized.get("x-requests-last", "").isdigit():
                actual_cost = int(normalized["x-requests-last"])
            value = headers.get("Retry-After")
            if isinstance(value, str):
                try:
                    retry = max(0, int(value)) if value.isdigit() else max(0, math.ceil(
                        (parsedate_to_datetime(value) - now).total_seconds()))
                except (TypeError, ValueError, OverflowError):
                    pass
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    if code is None:
        # Existing sanitized feed exceptions preserve the HTTP code in text.
        match = re.search(r"\bHTTP\s+([1-5][0-9]{2})\b", str(error))
        code = int(match[1]) if match else None
        if code is None and any(token in str(error).casefold() for token in ("ratelimit", "rate limit")):
            code = 429
    # Resources.get returns a static code; TheStatsOddsFeed wraps that exact code.
    reason = ("QUOTA_TRACKING_UNAVAILABLE"
              if str(error) == "TheStats odds: QUOTA_TRACKING_UNAVAILABLE"
              else "PROVIDER_UNAVAILABLE")
    return MonitorFetchError(reason=reason, status_code=code, retry_after_seconds=retry,
                             quota_exhausted=quota_exhausted or remaining_quota == 0,
                             remaining_quota=remaining_quota, actual_cost=actual_cost,
                             unexpected_cost=(type(actual_cost) is int and actual_cost > estimated_cost))


def fetch_monitor_query(query, *, football_client=None, odds_feed=None, now=None):
    """Fetch one already-budgeted query with injected existing provider clients."""
    now = _clock(now or datetime.now(UTC))
    fixtures = query.get("fixtures", [])
    if not fixtures or any((_time(row.get("kickoff")) or now) <= now + LOCKOUT for row in fixtures):
        raise MonitorFetchError(reason="KICKOFF_LOCKOUT")
    provider, params = query.get("provider"), query.get("params", {})
    remaining, actual = None, None
    odds_usage_before = len(getattr(odds_feed, "usage", []))
    try:
        if provider == 'the_stats_api':
            from goal_analysis.providers.the_stats_odds import TheStatsOddsFeed

            if football_client is None or getattr(football_client, 'provider', None) != 'the_stats_api':
                raise MonitorFetchError(reason='PROVIDER_NOT_CONFIGURED')
            clock = football_client._clock
            feed = TheStatsOddsFeed(football_client.root, query['normalization_config']['odds_region'],
                                   1, client=football_client, clock=clock)
            feed.bind_fixtures(fixtures)
            event = feed.extra(fixtures[0]['sport_key'], params['match_id'], params['markets'])
            events = [event]
            now = clock()
            remaining, actual = football_client.last_usage.monthly_remaining, feed.calls
        elif provider == "api_football":
            if football_client is None:
                raise MonitorFetchError(reason="PROVIDER_NOT_CONFIGURED")
            payload = football_client.request("odds", params)
            remaining = getattr(getattr(football_client, "last_usage", None), "remaining_day", None)
            actual = 1
            paging = payload.get("paging", {}) if isinstance(payload, dict) else {}
            if (not isinstance(payload, dict) or payload.get("errors")
                    or not isinstance(payload.get("response"), list)
                    or paging.get("total", 1) not in (0, 1) or paging.get("current", 1) != 1):
                raise MonitorFetchError(reason="INVALID_PROVIDER_RESPONSE")
            events, _ = normalize_odds(payload["response"], fixtures, now)
        elif provider == "the_odds_api":
            if odds_feed is None or getattr(odds_feed, "region", params.get("region")) != params.get("region"):
                raise MonitorFetchError(reason="PROVIDER_NOT_CONFIGURED")
            if query.get("kind") == "BULK_ODDS":
                events = odds_feed.bulk(params["sport_key"], date.fromisoformat(params["date"]))
                if not isinstance(events, list):
                    raise MonitorFetchError(reason="INVALID_PROVIDER_RESPONSE")
            elif query.get("kind") == "EXTRA_ODDS":
                event = odds_feed.extra(params["sport_key"], params["event_id"], params["markets"])
                if not isinstance(event, dict) or event.get("id") != params["event_id"]:
                    raise MonitorFetchError(reason="INVALID_PROVIDER_RESPONSE")
                events = [event]
            else:
                raise MonitorFetchError(reason="INVALID_QUERY")
            usage = getattr(odds_feed, "usage", [])
            remaining = usage[-1].get("remaining") if usage else None
            actual = usage[-1].get("last_cost") if usage else None
        else:
            raise MonitorFetchError(reason="PROVIDER_NOT_CONFIGURED")
    except Exception as error:
        if provider == 'the_stats_api':
            remaining = getattr(getattr(football_client, 'last_usage', None), 'monthly_remaining', None)
        elif provider == "api_football":
            remaining = getattr(getattr(football_client, "last_usage", None), "remaining_day", None)
        elif provider == "the_odds_api":
            usage = getattr(odds_feed, "usage", [])
            remaining = usage[-1].get("remaining") if usage else None
            actual = usage[-1].get("last_cost") if len(usage) > odds_usage_before else None
        if isinstance(error, MonitorFetchError):
            error.remaining_quota, error.actual_cost = remaining, actual
            error.quota_exhausted = remaining == 0
            error.unexpected_cost = type(actual) is int and actual > query.get("estimated_cost", 1)
            raise
        raise _safe_error(error, now, quota_exhausted=remaining == 0, remaining_quota=remaining,
                          actual_cost=actual, estimated_cost=query.get("estimated_cost", 1)) from None
    config = query["normalization_config"]
    target = date.fromisoformat(fixtures[0]["target_date"])
    try:
        normalized = assemble_daily223_candidates(fixtures, events, config, target, now, now)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise MonitorFetchError(reason="INVALID_PROVIDER_RESPONSE", remaining_quota=remaining,
                                quota_exhausted=remaining == 0, actual_cost=actual) from None
    quotes = []
    for quote in normalized["candidates"]:
        stamp = _time(quote.get('quote_observed_at')) if provider == 'the_stats_api' else _time(quote.get("quote_timestamp_raw", quote.get("quoted_at")))
        # Missing/future provider timestamps cannot establish a historical move.
        if stamp is not None and stamp <= now:
            quotes.append(quote)
    return {"quotes": quotes, "remaining_quota": remaining, "actual_cost": actual}


class MonitorProviderAdapter:
    def __init__(self, football_client=None, odds_feed=None, clock=None):
        self.football_client, self.odds_feed = football_client, odds_feed
        self.clock = clock or (lambda: datetime.now(UTC))

    def __call__(self, query):
        return fetch_monitor_query(query, football_client=self.football_client,
                                   odds_feed=self.odds_feed, now=self.clock())
