"""Explicit provider scope and resource budgets for independent DAILY_223 runs."""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy

from goal_analysis.providers.api_football_history import positive_id

EXTRA_MARKETS = {"btts", "btts_h1", "h2h_3_way_h1", "totals_h1"}


def normalized_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("nonempty team name required")
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def validate_live_config(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise TypeError("live config must be a JSON object")
    allowed = {
        "schema_version",
        "bookmaker_key",
        "odds_region",
        "preferred_bookmakers",
        "account_region",
        "currency",
        "max_football_calls",
        "max_odds_credits",
        "api_football_odds_max_calls",
        "extra_markets",
        "leagues",
        "team_aliases",
    }
    if set(payload) - allowed:
        raise ValueError("unknown live config field; credentials belong in environment variables")
    result = deepcopy(payload)
    if type(result.get("schema_version")) is not int or result["schema_version"] != 1:
        raise ValueError("live config schema_version 1 required")
    legacy_bookmaker = result.pop("bookmaker_key", None)
    result.setdefault("odds_region", "eu")
    if "preferred_bookmakers" not in result:
        result["preferred_bookmakers"] = (
            [legacy_bookmaker]
            if isinstance(legacy_bookmaker, str) and re.fullmatch(r"[a-z0-9_]+", legacy_bookmaker)
            else ["betano"]
        )
    for field, pattern in (
        ("odds_region", r"[a-z]+"),
        ("account_region", r"[A-Z]{2}"),
        ("currency", r"[A-Z]{3}"),
    ):
        if not isinstance(result.get(field), str) or not re.fullmatch(pattern, result[field]):
            raise ValueError(f"configure {field} explicitly")
    preferred = result.get("preferred_bookmakers")
    if (
        not isinstance(preferred, list)
        or not preferred
        or any(
            not isinstance(item, str) or not re.fullmatch(r"[a-z0-9_]+", item) for item in preferred
        )
        or len(set(preferred)) != len(preferred)
    ):
        raise ValueError("preferred_bookmakers must be a nonempty unique bookmaker-key list")
    for field in ("max_football_calls", "max_odds_credits"):
        if type(result.get(field)) is not int or not 0 <= result[field] <= 100:
            raise ValueError(f"{field} must be an integer between 0 and 100")
    # Optional for existing installations. This is a share of the existing
    # football allowance, never an additional daily allowance or paid service.
    result.setdefault("api_football_odds_max_calls", 12)
    cap = result["api_football_odds_max_calls"]
    if type(cap) is not int or not 0 <= cap <= 30:
        raise ValueError("api_football_odds_max_calls must be an integer between 0 and 30")
    extras = result.setdefault("extra_markets", [])
    if not isinstance(extras, list) or any(
        not isinstance(item, str) or item not in EXTRA_MARKETS for item in extras
    ):
        raise ValueError("unsupported extra markets")
    result["extra_markets"] = sorted(set(extras))
    leagues = result.get("leagues")
    if not isinstance(leagues, list) or not 1 <= len(leagues) <= 20:
        raise ValueError("configure between 1 and 20 leagues")
    ids, sports, codes = set(), set(), set()
    for league in leagues:
        if not isinstance(league, dict):
            raise TypeError("league config must be an object")
        if set(league) != {
            "competition_id",
            "api_football_id",
            "season",
            "history_seasons",
            "odds_sport_key",
            "competition_type",
        }:
            raise ValueError("league config fields must match the example schema; no credentials")
        identifier = positive_id(league["api_football_id"])
        sport = league["odds_sport_key"]
        code = league["competition_id"]
        if not isinstance(sport, str) or not re.fullmatch(r"soccer_[a-z0-9_]+", sport):
            raise ValueError("explicit soccer sport key required")
        if not isinstance(code, str) or not code.strip():
            raise ValueError("competition_id required")
        if identifier in ids or sport in sports or code in codes:
            raise ValueError("league IDs, competition IDs and sport keys must be unique")
        ids.add(identifier)
        sports.add(sport)
        codes.add(code)
        league["api_football_id"] = int(identifier)
        if league["competition_type"] not in {"LEAGUE", "CUP", "UEFA", "OTHER"}:
            raise ValueError("explicit competition_type required")
        seasons = league["history_seasons"]
        if not isinstance(seasons, list) or not seasons:
            raise ValueError("history_seasons must be a nonempty list")
        for year in [league["season"], *seasons]:
            if type(year) is not int or not 1900 <= year <= 2200:
                raise ValueError("explicit integer seasons required")
        league["history_seasons"] = sorted(set(seasons))
    aliases = result.setdefault("team_aliases", {})
    if not isinstance(aliases, dict):
        raise TypeError("team_aliases must be an object keyed by API-Football team ID")
    owners = {}
    for team, names in aliases.items():
        if positive_id(team) != team or not isinstance(names, list) or not names:
            raise ValueError("aliases require canonical team IDs and nonempty name lists")
        for name in names:
            normalized = normalized_name(name)
            if normalized in owners and owners[normalized] != team:
                raise ValueError("one team alias cannot identify two teams")
            owners[normalized] = team
    return result
