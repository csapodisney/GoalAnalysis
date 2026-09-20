from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Sequence

from goal_analysis.normalization.models import Competition, Fixture, FixtureStatus, Team
from goal_analysis.providers.base import FixtureProvider
from goal_analysis.storage.cache import CacheStore


@dataclass(frozen=True, slots=True)
class DailyCollectionResult:
    fixtures: tuple[Fixture, ...]
    from_cache: bool
    cache_key: str


class DailyFixtureCollector:
    """Collect a normalized daily fixture universe once, then reuse it from cache."""

    CACHE_NAMESPACE = "daily_fixtures_v1"

    def __init__(
        self,
        provider: FixtureProvider,
        cache: CacheStore,
        ttl: timedelta = timedelta(hours=6),
    ) -> None:
        if ttl.total_seconds() <= 0:
            raise ValueError("ttl must be positive")
        self.provider = provider
        self.cache = cache
        self.ttl = ttl

    def collect(
        self,
        target_date: date,
        competition_ids: Sequence[str],
        now: datetime,
        force_refresh: bool = False,
    ) -> DailyCollectionResult:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        normalized_ids = tuple(sorted(set(competition_ids)))
        if not normalized_ids:
            raise ValueError("competition_ids must not be empty")
        cache_key = f"{self.provider.name}:{target_date.isoformat()}:{','.join(normalized_ids)}"

        if not force_refresh:
            cached = self.cache.get(self.CACHE_NAMESPACE, cache_key, now)
            if cached is not None:
                return DailyCollectionResult(
                    fixtures=tuple(_fixture_from_dict(item) for item in cached),
                    from_cache=True,
                    cache_key=cache_key,
                )

        fixtures = tuple(self.provider.list_fixtures(target_date, normalized_ids))
        self.cache.set(
            self.CACHE_NAMESPACE,
            cache_key,
            [_fixture_to_dict(fixture) for fixture in fixtures],
            self.ttl,
            now,
        )
        return DailyCollectionResult(fixtures, False, cache_key)


def _fixture_to_dict(fixture: Fixture) -> dict[str, Any]:
    return {
        "id": fixture.id,
        "provider": fixture.provider,
        "competition": {
            "id": fixture.competition.id,
            "name": fixture.competition.name,
            "country_code": fixture.competition.country_code,
        },
        "home_team": {
            "id": fixture.home_team.id,
            "name": fixture.home_team.name,
            "aliases": list(fixture.home_team.aliases),
        },
        "away_team": {
            "id": fixture.away_team.id,
            "name": fixture.away_team.name,
            "aliases": list(fixture.away_team.aliases),
        },
        "kickoff": fixture.kickoff.isoformat(),
        "status": fixture.status.value,
        "provider_ids": dict(fixture.provider_ids or {}),
    }


def _fixture_from_dict(item: dict[str, Any]) -> Fixture:
    competition = item["competition"]
    home = item["home_team"]
    away = item["away_team"]
    return Fixture(
        id=item["id"],
        provider=item["provider"],
        competition=Competition(
            id=competition["id"],
            name=competition["name"],
            country_code=competition.get("country_code"),
        ),
        home_team=Team(home["id"], home["name"], tuple(home.get("aliases", []))),
        away_team=Team(away["id"], away["name"], tuple(away.get("aliases", []))),
        kickoff=datetime.fromisoformat(item["kickoff"]),
        status=FixtureStatus(item["status"]),
        provider_ids=item.get("provider_ids") or None,
    )
