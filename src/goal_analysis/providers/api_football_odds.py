"""API-Football pre-match quotes, using the existing account and fixture IDs.

Only explicitly recognised pre-match markets are mapped. Provider bookmaker IDs
remain separate from The Odds API books: equal names do not prove equal regions
or account products. No model is involved in parsing or choosing prices.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import timedelta

from goal_analysis.config.daily_223_live import normalized_name
from goal_analysis.providers.api_football_history import aware_time, positive_id
from goal_analysis.providers.base import ProviderError

ROOT = "https://v3.football.api-sports.io/odds"
# Exact names from the pre-match catalogue; never reuse live bet IDs or infer
# a period from a numeric ID. Unrecognised products remain out of the universe.
MARKETS = {
    "match winner": "h2h",
    "goals over/under": "totals",
    "goals over/under first half": "totals_h1",
    "both teams score": "btts",
    "both teams score - first half": "btts_h1",
    "first half winner": "h2h_3_way_h1",
}


def _outcomes(bet, market, fixture):
    values = bet["values"]
    if not isinstance(values, list) or not values:
        raise ValueError("empty market")
    outcomes = []
    for item in values:
        value = normalized_name(item["value"])
        raw = item["odd"]
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            raise TypeError("invalid price")
        price = float(raw)
        if not math.isfinite(price) or price <= 1:
            raise ValueError("invalid price")
        if market in {"h2h", "h2h_3_way_h1"}:
            names = {"home": fixture["home_team"], "away": fixture["away_team"], "draw": "Draw"}
            outcomes.append({"name": names[value], "price": price})
        elif market in {"btts", "btts_h1"}:
            if value not in {"yes", "no"}:
                raise ValueError("invalid BTTS selection")
            outcomes.append({"name": value.title(), "price": price})
        else:
            match = re.fullmatch(r"(over|under) ([0-9]+(?:\.[0-9]+)?)", value)
            if not match:
                raise ValueError("invalid totals selection")
            point = float(match[2])
            # Integer/Asian split lines have different refund rules. Skip them.
            if not 0 <= point <= 20 or point % 1 != 0.5:
                continue
            outcomes.append({"name": match[1].title(), "point": point, "price": price})
    if not outcomes:
        raise ValueError("no supported half-goal lines")
    return outcomes


def normalize_odds(rows, fixtures, now):
    """Join by exact provider fixture ID, league, season and kickoff; keep update time."""
    duplicates = Counter(f["api_football_fixture_id"] for f in fixtures)
    known = {f["api_football_fixture_id"]: f for f in fixtures}
    events, issues = {}, []
    for row in rows:
        identifier = None
        try:
            identifier = positive_id(row["fixture"]["id"])
            if identifier not in known:
                continue
            fixture = known[identifier]
            if duplicates[identifier] != 1:
                raise ValueError("ambiguous fixture")
            start = aware_time(row["fixture"]["date"])
            if (
                positive_id(row["league"]["id"]) != fixture["api_football_league_id"]
                or row["league"]["season"] != fixture["season"]
                or abs((start - aware_time(fixture["kickoff"])).total_seconds()) > 60
                or start <= now
                or fixture["provider_status"] != "NS"
            ):
                raise ValueError("identity mismatch")
            # Preserve provider metadata; only the fixture/market/price decides
            # eligibility. Even an absent or invalid update cannot erase odds.
            stamp = row.get("update")
            books = row["bookmakers"]
            if not isinstance(books, list):
                raise TypeError("invalid bookmaker list")
        except (KeyError, ValueError, TypeError, OverflowError):
            issues.append({"status": "API_FOOTBALL_ODDS_INVALID_ROW", "fixture_id": identifier})
            continue
        event = events.setdefault(
            identifier,
            {
                "id": f"apifootball_{identifier}",
                "sport_key": fixture["sport_key"],
                "home_team": fixture["home_team"],
                "away_team": fixture["away_team"],
                "commence_time": start.isoformat(),
                "bookmakers": [],
            },
        )
        for book in books:
            try:
                book_id = positive_id(book["id"])
                title = book["name"]
                name = normalized_name(title)
                slug = re.sub(r"[^a-z0-9]+", "_", name).strip("_") or "book"
                bets = book["bets"]
                if not isinstance(bets, list):
                    raise TypeError("invalid bet list")
            except (KeyError, ValueError, TypeError):
                issues.append(
                    {"status": "API_FOOTBALL_ODDS_INVALID_BOOKMAKER", "fixture_id": identifier}
                )
                continue
            markets = []
            for bet in bets:
                try:
                    market = MARKETS.get(normalized_name(bet["name"]))
                    if market is None:
                        continue
                    bet_id = positive_id(bet["id"])
                    markets.append(
                        {
                            "key": market,
                            "last_update": stamp,
                            "outcomes": _outcomes(bet, market, fixture),
                            "quote_provider": "api_football",
                            "provider_market_id": bet_id,
                            "quote_source_id": f"{ROOT}?fixture={identifier}&bookmaker={book_id}&bet={bet_id}",
                        }
                    )
                except (KeyError, ValueError, TypeError, OverflowError):
                    issues.append(
                        {
                            "status": "API_FOOTBALL_ODDS_INVALID_MARKET",
                            "fixture_id": identifier,
                            "bookmaker_id": book_id,
                        }
                    )
            event["bookmakers"].append(
                {
                    "key": f"{slug}_api_football_{book_id}",
                    "title": title,
                    "last_update": stamp,
                    "markets": markets,
                }
            )
    return list(events.values()), issues


class ApiFootballOddsCollector:
    """Round-robin pagination with per-run and shared football limits, plus caching."""

    NAMESPACE = "api_football_prematch_odds_v1"

    def __init__(self, client, cache, maximum, clock):
        self.client, self.cache, self.maximum, self.clock = client, cache, maximum, clock
        self.calls = 0

    def collect(self, fixtures, target_date):
        groups = sorted({(f["api_football_league_id"], f["season"]) for f in fixtures})
        queue = [(league, season, 1) for league, season in groups]
        rows, queries, issues = [], [], []
        seen_pages = {}
        while queue:
            league, season, page = queue.pop(0)
            params = {
                "league": int(league),
                "season": season,
                "date": target_date.isoformat(),
                "timezone": "Europe/Berlin",
                "page": page,
            }
            key = f"{league}:{season}:{target_date.isoformat()}:{page}"
            now = self.clock()
            payload = self.cache.get(self.NAMESPACE, key, now)
            cached = payload is not None
            query = {"provider": "api_football", **params, "from_cache": cached}
            queries.append(query)
            if not cached:
                if (
                    self.calls >= self.maximum
                    or self.client.calls >= self.client.maximum
                    or self.client.last_usage.remaining_day == 0
                ):
                    query["status"] = "BUDGET_EXHAUSTED"
                    issues.append(
                        {"status": "API_FOOTBALL_ODDS_BUDGET_EXHAUSTED", "league_id": league}
                    )
                    continue
                try:
                    # The shared client also checks remaining provider quota.
                    before = self.client.calls
                    try:
                        payload = self.client.request("odds", params)
                    finally:
                        self.calls += self.client.calls - before
                except (ProviderError, OSError, ValueError, TypeError):
                    # Provider text may echo keys; reports only receive static diagnostics.
                    query["status"] = "UNAVAILABLE"
                    issues.append(
                        {
                            "status": "API_FOOTBALL_ODDS_UNAVAILABLE",
                            "league_id": league,
                            "message": "Az API-Football szorzólekérése nem sikerült a meglévő hozzáféréssel. Nincs automatikus csomagváltás.",
                        }
                    )
                    continue
            try:
                if (
                    not isinstance(payload, dict)
                    or payload.get("errors")
                    or not isinstance(payload.get("response"), list)
                ):
                    raise ValueError("invalid odds response")
                paging = payload["paging"]
                total = paging["total"]
                if (
                    type(total) is not int
                    or not 0 <= total <= 1000
                    or type(paging["current"]) is not int
                    or paging["current"] != page
                    or (total < page and not (page == 1 and total == 0))
                ):
                    raise ValueError("invalid pagination")
                # Do not silently accept a moving pagination snapshot.
                group = (league, season)
                if group in seen_pages and seen_pages[group] != total:
                    raise ValueError("pagination changed")
                seen_pages[group] = total
                records = payload["response"]
                if not records and page < total:
                    raise ValueError("empty intermediate page")
            except (ValueError, TypeError, KeyError):
                query["status"] = "INVALID_RESPONSE"
                issues.append({"status": "API_FOOTBALL_ODDS_INVALID_RESPONSE", "league_id": league})
                continue
            if not cached:
                self.cache.set(
                    self.NAMESPACE,
                    key,
                    payload,
                    timedelta(minutes=15 if records else 30),
                    self.clock(),
                )
            query.update(
                status="OK" if records else "EMPTY", returned_events=len(records), total_pages=total
            )
            rows.extend(records)
            if page < total:
                queue.append((league, season, page + 1))
        return rows, queries, issues
