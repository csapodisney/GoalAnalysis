"""One date-wide calendar request, independent of bookmaker availability."""

from datetime import datetime
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.providers.base import ProviderError

from .daily_223_candidates import normalize_daily_fixtures


def collect_calendar(client, leagues, target_date):
    payload = client.request(
        "fixtures", {"date": target_date.isoformat(), "timezone": "Europe/Berlin"}
    )
    if payload.get("errors"):
        raise ProviderError(f"API-Football calendar: {payload['errors']}")
    if not isinstance(payload.get("response"), list):
        raise ProviderError("API-Football calendar response must be a list")
    if payload.get("paging", {}).get("total", 1) not in (0, 1):
        raise ProviderError("API-Football calendar returned incomplete pagination")
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
    counts = {}
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
