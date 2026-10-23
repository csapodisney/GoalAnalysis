"""Prospective Arthur calendar expansion, without changing ticket rules or budgets."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta

from goal_analysis.providers.api_football_history import aware_time

from .portfolio_calendar import collect_calendar

MINIMUM_PENDING_FIXTURES = 15
SERIE_C_GROUPS = {138: "IT3_A", 942: "IT3_B", 943: "IT3_C"}


def _calendar_rows(payload):
    """Keep malformed venue structures from escaping the shared normalizer."""
    rows = payload.get("response", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    # The normalizer rejects malformed identities, teams, dates and seasons.
    # Its optional venue field assumes an object; invalidate those rows while
    # retaining their identity so duplicate exclusions still apply to both rows.
    result = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("fixture"), dict):
            venue = row["fixture"].get("venue")
            if venue is not None and not isinstance(venue, dict):
                row = {**row, "fixture": {**row["fixture"], "date": None, "venue": None}}
        result.append(row)
    return result


def _pending(rows, leagues, target_date, now):
    fixtures, _, _ = collect_calendar(None, leagues, target_date, {"response": rows})
    cutoff = now + timedelta(minutes=15)
    return [fixture for fixture in fixtures
            if fixture["provider_status"] == "NS" and aware_time(fixture["kickoff"]) > cutoff]


def _fallback_config(identifier, season):
    return {
        "competition_id": SERIE_C_GROUPS[identifier],
        "api_football_id": identifier,
        "season": season,
        "history_seasons": [season - 1, season] if season > 1900 else [season],
        # This internal routing key skips unverified The Odds API sport names;
        # the existing API-Football odds collector remains responsible for prices.
        "odds_sport_key": f"soccer_arthur_{identifier}",
        "competition_type": "LEAGUE",
    }


def resolve_default_leagues(payload, target_date, configured, now: datetime) -> list[dict]:
    """Add available Serie C groups only below 15 valid, unlocked default fixtures.

    The caller uses this only for automatic Arthur scope selection. Explicit user
    selections and the independent DAILY_223 collector do not use this function.
    It consumes the already fetched calendar and performs no provider requests.
    """
    aware_time(now.isoformat())
    result = deepcopy(list(configured))
    rows = _calendar_rows(payload)
    if len(result) >= 40 or len(_pending(rows, result, target_date, now)) >= MINIMUM_PENDING_FIXTURES:
        return result

    existing = {int(league["api_football_id"]) for league in result}
    possible = {}
    for row in rows:
        try:
            league = row["league"]
            identifier, season = league["id"], league["season"]
            if (type(identifier) is not int or identifier not in SERIE_C_GROUPS
                    or identifier in existing or type(season) is not int
                    or not 1900 <= season <= 2200):
                continue
            possible[(identifier, season)] = _fallback_config(identifier, season)
        except (KeyError, TypeError):
            continue

    available = {}
    for (identifier, season), league in sorted(possible.items()):
        # Normalize against the whole payload so duplicate fixture identities are
        # excluded in full, including duplicates in an otherwise unrelated league.
        fixtures = _pending(rows, [league], target_date, now)
        if any(fixture["season"] == season for fixture in fixtures):
            available.setdefault(identifier, []).append(league)
    for identifier in sorted(available):
        choices = available[identifier]
        if len(choices) == 1 and len(result) < 40:
            result.append(choices[0])
    return result
