"""Deterministic cross-provider event matching and independent price-role candidates."""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.daily_223_live import EXTRA_MARKETS, normalized_name
from goal_analysis.providers.api_football_history import aware_time, positive_id
from goal_analysis.providers.base import ProviderError


def normalize_daily_fixtures(payload: dict, league: dict, target_date: date) -> list[dict]:
    if payload.get("errors") or not isinstance(payload.get("response"), list):
        raise ProviderError("invalid daily football response")
    if payload.get("paging", {}).get("total", 1) not in (0, 1):
        raise ProviderError("unexpected pagination in daily football response")
    result = []
    for row in payload["response"]:
        fixture, competition, teams = row["fixture"], row["league"], row["teams"]
        if (
            positive_id(competition["id"]) != str(league["api_football_id"])
            or competition["season"] != league["season"]
        ):
            raise ProviderError("daily fixture league/season mismatch")
        stamp = aware_time(fixture["date"])
        home, away = positive_id(teams["home"]["id"]), positive_id(teams["away"]["id"])
        if home == away:
            raise ProviderError("daily fixture has identical home and away team")
        normalized_name(teams["home"]["name"])
        normalized_name(teams["away"]["name"])
        result.append(
            {
                "fixture_id": f"api_football:{positive_id(fixture['id'])}",
                "api_football_fixture_id": positive_id(fixture["id"]),
                "api_football_home_team_id": home,
                "api_football_away_team_id": away,
                "api_football_league_id": str(league["api_football_id"]),
                "home_team": teams["home"]["name"],
                "away_team": teams["away"]["name"],
                "competition": league["competition_id"],
                "competition_type": league["competition_type"],
                "sport_key": league["odds_sport_key"],
                "kickoff": stamp.isoformat(),
                "provider_status": fixture["status"]["short"],
                "target_date": target_date.isoformat(),
            }
        )
    return result


def event_identity(event: dict) -> tuple:
    identifier = event["id"]
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
        raise ValueError("invalid odds event ID")
    sport = event["sport_key"]
    if not isinstance(sport, str) or not re.fullmatch(r"soccer_[a-z0-9_]+", sport):
        raise ValueError("invalid odds sport key")
    return (
        identifier,
        sport,
        normalized_name(event["home_team"]),
        normalized_name(event["away_team"]),
        aware_time(event["commence_time"]),
    )


def match_daily_events(
    fixtures: list[dict], events: list[dict], aliases: dict, target_date: date, now: datetime
) -> tuple[list[tuple[dict, dict]], list[dict]]:
    valid_events, issues = [], []
    for event in events:
        try:
            identity = event_identity(event)
            valid_events.append((event, identity))
        except (TypeError, ValueError, KeyError):
            issues.append({"status": "INVALID_ODDS_EVENT"})
    duplicate_events = Counter(
        event.get("id")
        for event in events
        if isinstance(event, dict) and isinstance(event.get("id"), str)
    )
    duplicate_fixtures = Counter(item["fixture_id"] for item in fixtures)
    proposed = []
    for fixture in sorted(fixtures, key=lambda item: item["fixture_id"]):
        identifier = fixture["fixture_id"]
        kickoff = aware_time(fixture["kickoff"])
        if duplicate_fixtures[identifier] != 1:
            issues.append({"fixture_id": identifier, "status": "DUPLICATE_FOOTBALL_FIXTURE"})
            continue
        if (
            fixture["provider_status"] != "NS"
            or kickoff <= now
            or kickoff.astimezone(ZoneInfo("Europe/Berlin")).date() != target_date
        ):
            issues.append({"fixture_id": identifier, "status": "NOT_PENDING_ON_TARGET_DAY"})
            continue
        home_names = {
            normalized_name(name)
            for name in [
                fixture["home_team"],
                *aliases.get(fixture["api_football_home_team_id"], []),
            ]
        }
        away_names = {
            normalized_name(name)
            for name in [
                fixture["away_team"],
                *aliases.get(fixture["api_football_away_team_id"], []),
            ]
        }
        matches = [
            event
            for event, identity in valid_events
            if identity[1] == fixture["sport_key"]
            and identity[2] in home_names
            and identity[3] in away_names
            and identity[4] > now
            and identity[4].astimezone(ZoneInfo("Europe/Berlin")).date() == target_date
            and abs((identity[4] - kickoff).total_seconds()) <= 60
        ]
        if len(matches) != 1 or duplicate_events[matches[0]["id"]] != 1:
            issues.append(
                {
                    "fixture_id": identifier,
                    "status": "UNMATCHED" if not matches else "AMBIGUOUS_ODDS_MATCH",
                    "match_count": len(matches),
                }
            )
            continue
        proposed.append((fixture, matches[0]))
    uses = Counter(event["id"] for _, event in proposed)
    matched = []
    for fixture, event in proposed:
        if uses[event["id"]] != 1:
            issues.append(
                {
                    "fixture_id": fixture["fixture_id"],
                    "status": "ODDS_EVENT_REUSED_BY_MULTIPLE_FIXTURES",
                }
            )
        else:
            matched.append((fixture, event))
    return matched, issues


def merge_extra_event(base: dict, extra: dict) -> dict:
    if event_identity(base) != event_identity(extra):
        raise ProviderError("event identity changed between bulk and extra odds responses")
    # Separate bookmaker entries are intentional: each market keeps its own timestamp.
    return {**base, "bookmakers": [*base.get("bookmakers", []), *extra.get("bookmakers", [])]}


def assemble_daily223_candidates(
    fixtures: list[dict],
    events: list[dict],
    config: dict,
    target_date: date,
    now: datetime,
    fixtures_observed_at: datetime,
) -> dict:
    if not 0 <= (now - fixtures_observed_at).total_seconds() <= 300:
        raise ProviderError("daily fixture snapshot is stale or from the future")
    matched, issues = match_daily_events(fixtures, events, config["team_aliases"], target_date, now)
    candidates = []
    allowed_markets = {"h2h", "totals", *config["extra_markets"]}
    usable_markets = set()
    mapping = []
    for fixture, event in matched:
        mapping.append(
            {
                "fixture_id": fixture["fixture_id"],
                "odds_event_id": event["id"],
                "sport_key": event["sport_key"],
                "method": "UNIQUE_SPORT_HOME_AWAY_KICKOFF_WITH_EXPLICIT_ALIASES",
            }
        )
        bookmakers = event.get("bookmakers", [])
        if not isinstance(bookmakers, list):
            raise TypeError("bookmakers must be a list")
        market_counts = Counter()
        for bookmaker in bookmakers:
            if not isinstance(bookmaker, dict):
                raise TypeError("bookmaker must be an object")
            bookmaker_key = bookmaker.get("key")
            if not isinstance(bookmaker_key, str) or not re.fullmatch(
                r"[a-z0-9_]+", bookmaker_key
            ):
                raise ValueError("invalid bookmaker key")
            markets = bookmaker.get("markets", [])
            if not isinstance(markets, list):
                raise TypeError("bookmaker markets must be a list")
            market_counts.update((bookmaker_key, market.get("key")) for market in markets)
        for bookmaker in bookmakers:
            bookmaker_key = bookmaker["key"]
            for market in bookmaker.get("markets", []):
                key = market.get("key")
                if key not in allowed_markets:
                    continue
                if market_counts[(bookmaker_key, key)] > 1:
                    issues.append(
                        {
                            "fixture_id": fixture["fixture_id"],
                            "bookmaker": bookmaker_key,
                            "market": key,
                            "status": "DUPLICATE_MARKET_SNAPSHOT",
                        }
                    )
                    continue
                try:
                    stamp = aware_time(market.get("last_update") or bookmaker["last_update"])
                    if not 0 <= (now - stamp).total_seconds() <= 300:
                        raise ValueError("STALE_OR_FUTURE_QUOTE")
                    outcomes = market["outcomes"]
                    if not isinstance(outcomes, list):
                        raise TypeError("outcomes must be a list")
                    if key in {"h2h", "h2h_3_way_h1"}:
                        labels = {normalized_name(item["name"]) for item in outcomes}
                        if labels != {
                            normalized_name(event["home_team"]),
                            normalized_name(event["away_team"]),
                            "draw",
                        }:
                            raise ValueError("THREE_WAY_RESULT_REQUIRED")
                    parsed = [
                        _candidate(
                            fixture, event, key, outcome, stamp, bookmaker_key, config
                        )
                        for outcome in outcomes
                    ]
                    candidates.extend(parsed)
                    usable_markets.add(key)
                except (TypeError, ValueError, KeyError, OverflowError) as error:
                    issues.append(
                        {
                            "fixture_id": fixture["fixture_id"],
                            "bookmaker": bookmaker_key,
                            "market": key,
                            "status": "INVALID_MARKET",
                            "reason": str(error),
                        }
                    )
    counts = Counter(item["candidate_id"] for item in candidates)
    for identifier, count in counts.items():
        if count > 1:
            issues.append({"candidate_id": identifier, "status": "DUPLICATE_CURRENT_PRODUCT"})
    filtered = sum(item["decimal_price"] < 1.96 for item in candidates)
    candidates = [
        item
        for item in candidates
        if counts[item["candidate_id"]] == 1 and item["decimal_price"] >= 1.96
    ]
    bookmaker_counts = Counter(item["bookmaker"] for item in candidates)
    oversized = {key for key, count in bookmaker_counts.items() if count > 200}
    for key in sorted(oversized):
        issues.append(
            {
                "bookmaker": key,
                "status": "BOOKMAKER_CANDIDATE_CAPACITY_EXCEEDED",
                "candidate_count": bookmaker_counts[key],
            }
        )
    candidates = [item for item in candidates if item["bookmaker"] not in oversized]
    available_bookmakers = sorted({item["bookmaker"] for item in candidates})
    return {
        "schema_version": 1,
        "date": target_date.isoformat(),
        "observed_at": now.isoformat(),
        "candidates": sorted(candidates, key=lambda item: item["candidate_id"]),
        "matching": mapping,
        "data_issues": issues,
        "below_price_floor_count": filtered,
        "market_coverage": sorted(usable_markets),
        "requested_market_coverage": sorted(allowed_markets),
        "available_bookmakers": available_bookmakers,
        "preferred_bookmakers": list(config["preferred_bookmakers"]),
        "account_context": {
            "region": config["account_region"],
            "currency": config["currency"],
            "odds_feed_region": config["odds_region"],
            "origin": "USER_CONFIGURED_NOT_VERIFIED_BY_FEED",
        },
    }


def _candidate(
    fixture: dict,
    event: dict,
    api_market: str,
    outcome: dict,
    stamp: datetime,
    bookmaker_key: str,
    config: dict,
) -> dict:
    if api_market not in {"h2h", "totals", *EXTRA_MARKETS}:
        raise ValueError("unsupported market")
    period = "FIRST_HALF" if api_market.endswith("_h1") else "FULL_TIME"
    name = normalized_name(outcome["name"])
    price = outcome["price"]
    if type(price) not in (int, float) or not math.isfinite(price) or price <= 1:
        raise ValueError("INVALID_PRICE")
    if api_market in {"h2h", "h2h_3_way_h1"}:
        selection = {
            normalized_name(event["home_team"]): "home",
            normalized_name(event["away_team"]): "away",
            "draw": "draw",
        }.get(name)
        market = "h2h"
    elif api_market in {"btts", "btts_h1"}:
        selection, market = name if name in {"yes", "no"} else None, "btts"
    else:
        point = outcome.get("point")
        if (
            type(point) not in (int, float)
            or not math.isfinite(point)
            or not 0 <= point <= 20
            or Decimal(str(point)) % 1 != Decimal("0.5")
        ):
            raise ValueError("HALF_GOAL_LINE_REQUIRED")
        market, selection = f"totals_{int(point)}_5", name if name in {"over", "under"} else None
    if selection is None:
        raise ValueError("UNRECOGNIZED_SELECTION")
    identity = {
        "fixture_id": fixture["fixture_id"],
        "event_id": event["id"],
        "market": market,
        "selection": selection,
        "period": period,
        "bookmaker": bookmaker_key,
    }
    return {
        **{
            key: value
            for key, value in fixture.items()
            if key not in {"provider_status", "target_date", "sport_key"}
        },
        "candidate_id": "d223-" + canonical_sha256(identity)[:24],
        "event_status": "SCHEDULED",
        "market_key": market,
        "selection_key": selection,
        "period": period,
        "settlement": "FIRST_HALF_PLUS_STOPPAGE"
        if period == "FIRST_HALF"
        else "REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES",
        "bookmaker": bookmaker_key,
        "region": config["account_region"],
        "currency": config["currency"],
        "decimal_price": float(price),
        "quoted_at": stamp.isoformat(),
        "quote_source_id": f"https://api.the-odds-api.com/v4/sports/{event['sport_key']}/events/{event['id']}/odds",
        "quote_available": True,
        "evidence": [],
        "sensitivity_note": "A kezdőcsapat, friss kerethírek és motiváció ebben a futásban még nincs külön forrással értékelve.",
    }
