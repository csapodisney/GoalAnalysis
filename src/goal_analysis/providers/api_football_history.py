"""Cached completed-fixture snapshots for the independent DAILY_223 branch."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from goal_analysis.agents import canonical_sha256
from goal_analysis.storage.cache import CacheStore

from .api_football import ApiFootballClient
from .base import ProviderError


def aware_time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return result


def positive_id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError("positive API-Football integer ID required")
    text = str(value)
    if not text.isascii() or not text.isdecimal() or int(text) <= 0:
        raise ValueError("positive API-Football integer ID required")
    return str(int(text))


def _score(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("score must be a nonnegative integer or null")
    return value


def normalize_history(payload: dict, league_id: str, season: int, fetched_at: str) -> list[dict]:
    """Keep regular-time and first-half scores separate, including AET/PEN games."""
    if payload.get("errors"):
        raise ProviderError("API-Football returned errors; history unavailable")
    rows = payload.get("response")
    if not isinstance(rows, list):
        raise ProviderError("history response must be a list")
    paging = payload.get("paging", {})
    if not isinstance(paging, dict) or paging.get("total", 1) not in (0, 1):
        raise ProviderError("unexpected history pagination; incomplete data not accepted")
    stamp = aware_time(fetched_at)
    records: dict[str, dict] = {}
    try:
        for raw in rows:
            fixture, league, teams = raw["fixture"], raw["league"], raw["teams"]
            if positive_id(league["id"]) != league_id or league["season"] != season:
                raise ValueError("history league/season mismatch")
            status = fixture["status"]["short"]
            if status not in {"FT", "AET", "PEN"}:
                continue
            kickoff = aware_time(fixture["date"])
            if kickoff >= stamp:
                raise ValueError("finished fixture cannot start at/after observation")
            scores = raw.get("score", {})
            full = scores.get("fulltime") or {}
            half = scores.get("halftime") or {}
            record = {
                "fixture_id": positive_id(fixture["id"]),
                "league_id": league_id,
                "season": season,
                "competition_type": str(league.get("type", "UNKNOWN")),
                "kickoff": kickoff.isoformat(),
                "status": status,
                "home_team_id": positive_id(teams["home"]["id"]),
                "away_team_id": positive_id(teams["away"]["id"]),
                "full_time_home": _score(full.get("home")),
                "full_time_away": _score(full.get("away")),
                "first_half_home": _score(half.get("home")),
                "first_half_away": _score(half.get("away")),
                "observed_at": fetched_at,
                "source_id": f"https://v3.football.api-sports.io/fixtures?id={fixture['id']}",
            }
            if record["home_team_id"] == record["away_team_id"]:
                raise ValueError("identical home and away team")
            for side in ("home", "away"):
                first, final = record[f"first_half_{side}"], record[f"full_time_{side}"]
                if first is not None and final is not None and first > final:
                    raise ValueError("first-half score exceeds regular-time score")
            identifier = record["fixture_id"]
            if identifier in records and records[identifier] != record:
                raise ValueError("conflicting duplicate historical fixture")
            records[identifier] = record
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ProviderError(f"invalid historical fixture data: {error}") from error
    return sorted(records.values(), key=lambda item: (item["kickoff"], item["fixture_id"]))


class ApiFootballHistoryCollector:
    NAMESPACE = "daily223_history_v1"

    def __init__(
        self,
        client: ApiFootballClient,
        cache: CacheStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        ttl: timedelta = timedelta(hours=6),
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("history cache TTL must be positive")
        self.client, self.cache, self.clock, self.ttl = client, cache, clock, ttl

    def collect(
        self,
        league_seasons: Sequence[tuple[int, int]],
        max_calls: int = 8,
        force_refresh: bool = False,
    ) -> dict:
        if type(max_calls) is not int or max_calls < 0:
            raise ValueError("max_calls must be a nonnegative integer")
        pairs = set()
        for league, season in league_seasons:
            if type(season) is not int or not 1900 <= season <= 2200:
                raise ValueError("explicit integer season required")
            pairs.add((positive_id(league), season))
        if not pairs:
            raise ValueError("at least one league-season pair is required")
        started = self.clock()
        aware_time(started.isoformat())
        prepared = []
        for league, season in sorted(pairs):
            key = f"{league}:{season}"
            cached = None if force_refresh else self.cache.get(self.NAMESPACE, key, started)
            if cached is not None and aware_time(cached["fetched_at"]) > started:
                cached = None
            prepared.append((league, season, key, cached))
        required = sum(cached is None for _, _, _, cached in prepared)
        if required > max_calls:
            raise ProviderError(f"history requires {required} API calls; budget is {max_calls}")
        records: dict[str, dict] = {}
        sources, calls = [], 0
        for league, season, key, cached in prepared:
            from_cache = cached is not None
            if cached is None:
                if self.client.last_usage.remaining_day == 0:
                    raise ProviderError("API-Football daily quota exhausted")
                payload = self.client.request(
                    "fixtures",
                    {
                        "league": int(league),
                        "season": season,
                        "status": "FT-AET-PEN",
                        "timezone": "UTC",
                    },
                )
                calls += 1
                fetched = self.clock()
                aware_time(fetched.isoformat())
                if fetched < started:
                    raise ProviderError("clock moved backwards during collection")
                cached = {
                    "fetched_at": fetched.isoformat(),
                    "payload_sha256": canonical_sha256(payload),
                    "records": normalize_history(payload, league, season, fetched.isoformat()),
                }
                self.cache.set(self.NAMESPACE, key, cached, self.ttl, fetched)
            sources.append(
                {
                    "league_id": league,
                    "season": season,
                    "from_cache": from_cache,
                    "fetched_at": cached["fetched_at"],
                    "payload_sha256": cached["payload_sha256"],
                    "record_count": len(cached["records"]),
                }
            )
            for record in cached["records"]:
                identifier = record["fixture_id"]
                if identifier in records and records[identifier] != record:
                    raise ProviderError("conflicting fixture across history queries")
                records[identifier] = record
        completed = self.clock()
        aware_time(completed.isoformat())
        if completed < started or any(
            aware_time(item["fetched_at"]) > completed for item in sources
        ):
            raise ProviderError("clock moved backwards during collection")
        result = {
            "schema_version": 1,
            "kind": "DAILY223_HISTORY",
            "provider": "api_football",
            "collected_at": completed.isoformat(),
            "api_calls": calls,
            "cache_hits": len(prepared) - calls,
            "sources": sources,
            "records": sorted(
                records.values(), key=lambda item: (item["kickoff"], item["fixture_id"])
            ),
        }
        result["snapshot_sha256"] = canonical_sha256(result)
        return result
