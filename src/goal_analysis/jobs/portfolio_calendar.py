"""One date-wide calendar request, independent of bookmaker availability."""

from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.providers.base import ProviderError

from .daily_223_candidates import normalize_daily_fixtures


def calendar_payload(client, target_date):
    payload = client.request("fixtures", {"date": target_date.isoformat(), "timezone": "Europe/Berlin"})
    if payload.get("errors"):
        raise ProviderError(f"API-Football calendar: {payload['errors']}")
    if not isinstance(payload.get("response"), list):
        raise ProviderError("API-Football calendar response must be a list")
    if payload.get("paging", {}).get("total", 1) not in (0, 1):
        raise ProviderError("API-Football calendar returned incomplete pagination")
    return payload


def available_competitions(payload, target_date, configured=()):
    """Only competitions with a real fixture on the requested Berlin day."""
    configured_ids = {int(item["api_football_id"]) for item in configured}
    grouped = {}
    for row in payload["response"]:
        try:
            league, fixture = row["league"], row["fixture"]
            identifier = int(league["id"])
            kickoff = datetime.fromisoformat(fixture["date"])
            if kickoff.astimezone(ZoneInfo("Europe/Berlin")).date() != target_date:
                continue
            name = str(league["name"])
            country = str(league.get("country") or "")
            national = identifier in {1, 4, 5, 10, 25, 32, 850, 886, 890} or (
                country == "World" and ("Qualification" in name or name.startswith("Friendlies"))
                and "Clubs" not in name
            )
            entry = grouped.setdefault(identifier, {
                "id": identifier, "name": name, "country": country,
                "season": int(league["season"]), "type": "NATIONAL" if national else "CLUB",
                "configured": identifier in configured_ids, "fixture_count": 0,
                "fixtures": [],
            })
            entry["fixture_count"] += 1
            if len(entry["fixtures"]) < 5:
                entry["fixtures"].append({
                    "home": str(row["teams"]["home"]["name"]),
                    "away": str(row["teams"]["away"]["name"]),
                    "kickoff": kickoff.isoformat(),
                })
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return sorted(grouped.values(), key=lambda x: (x["type"] != "NATIONAL", not x["configured"], x["country"], x["name"]))


def selected_leagues(payload, target_date, configured, selected_ids):
    options = {item["id"]: item for item in available_competitions(payload, target_date, configured)}
    if not selected_ids or len(selected_ids) != len(set(selected_ids)) or len(selected_ids) > 40:
        raise ValueError("Válassz 1–40 különböző, aznap elérhető sorozatot.")
    missing = set(selected_ids) - options.keys()
    if missing:
        raise ValueError("A kiválasztott sorozat már nem szerepel az aznapi kínálatban. Frissítsd a listát.")
    existing = {int(item["api_football_id"]): item for item in configured}
    result = []
    for identifier in selected_ids:
        option = options[identifier]
        if identifier in existing:
            result.append(existing[identifier])
        else:
            season = option["season"]
            result.append({
                "competition_id": f"API_{identifier}", "api_football_id": identifier,
                "season": season, "history_seasons": [season - 1, season],
                "odds_sport_key": f"soccer_arthur_{identifier}",
                "competition_type": "NATIONAL" if option["type"] == "NATIONAL" else "OTHER",
            })
    return result


def collect_calendar(client, leagues, target_date, payload=None):
    payload = payload if payload is not None else calendar_payload(client, target_date)
    configured = {str(item["api_football_id"]): item for item in leagues}
    fixtures, issues, seen = [], [], set()
    for row in payload["response"]:
        try:
            league = configured.get(str(row["league"]["id"]))
            if league is None:
                continue
            season = row["league"]["season"]
            if type(season) is not int or not 1900 <= season <= 2200:
                raise ValueError("invalid provider season")
            fixture = normalize_daily_fixtures(
                {"response": [row]}, {**league, "season": season}, target_date
            )[0]
            if (
                datetime.fromisoformat(fixture["kickoff"])
                .astimezone(ZoneInfo("Europe/Berlin"))
                .date()
                != target_date
            ):
                raise ValueError("fixture is outside requested Berlin date")
            if fixture["fixture_id"] in seen:
                raise ValueError("duplicate calendar fixture")
            seen.add(fixture["fixture_id"])
            fixture["season"] = season
            fixture["source_id"] = (
                "https://v3.football.api-sports.io/fixtures?id="
                + fixture["api_football_fixture_id"]
            )
            fixtures.append(fixture)
            if season != league["season"]:
                issues.append(
                    {
                        "status": "PROVIDER_SEASON_USED",
                        "fixture_id": fixture["fixture_id"],
                        "configured_season": league["season"],
                        "season": season,
                    }
                )
        except (KeyError, ValueError, TypeError, ProviderError):
            issues.append({"status": "INVALID_CALENDAR_FIXTURE"})
    # A repeated identity is excluded in full, including the first occurrence.
    counts = Counter()
    for row in payload["response"]:
        if isinstance(row, dict) and isinstance(row.get("fixture"), dict):
            identifier = str(row["fixture"].get("id"))
            counts[identifier] = counts.get(identifier, 0) + 1
    fixtures = [f for f in fixtures if counts[f["api_football_fixture_id"]] == 1]
    return (
        fixtures,
        issues,
        {
            "method": "DATE_WIDE",
            "date": target_date.isoformat(),
            "timezone": "Europe/Berlin",
            "returned_fixtures": len(payload["response"]),
            "configured_fixtures": len(fixtures),
            "configured_leagues": len(leagues),
        },
    )


def empty_history(now):
    snapshot = {
        "schema_version": 1,
        "kind": "DAILY223_HISTORY",
        "provider": "api_football",
        "collected_at": now.isoformat(),
        "api_calls": 0,
        "cache_hits": 0,
        "sources": [],
        "records": [],
    }
    snapshot["snapshot_sha256"] = canonical_sha256(snapshot)
    return snapshot
