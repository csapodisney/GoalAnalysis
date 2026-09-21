from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from goal_analysis.config import Settings, load_competition_catalog
from goal_analysis.jobs import DailyFixtureCollector
from goal_analysis.providers import ApiFootballClient, ApiFootballFixtureProvider, ProviderError
from goal_analysis.storage import CacheStore, Database


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect and cache daily top-league fixtures")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    catalog = load_competition_catalog(Path("config/competitions.toml"))
    league_ids = {item.id: item.api_football_id for item in catalog.competitions}
    database = Database(settings.database_path)
    database.initialize()
    provider = ApiFootballFixtureProvider(ApiFootballClient(), league_ids, catalog.timezone)
    collector = DailyFixtureCollector(provider, CacheStore(database))

    try:
        result = collector.collect(
            date.fromisoformat(args.date),
            sorted(catalog.allowed_ids),
            datetime.now(timezone.utc),
            force_refresh=args.force_refresh,
        )
    except ProviderError as error:
        print(f"Daily collection failed: {error}")
        return 1

    source = "cache" if result.from_cache else "API-Football"
    counts = Counter(fixture.competition.id for fixture in result.fixtures)
    print(f"Daily collection succeeded: {len(result.fixtures)} fixture(s), source={source}")
    for competition_id in sorted(catalog.allowed_ids):
        print(f"{competition_id}: {counts[competition_id]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
