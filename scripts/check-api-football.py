from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from goal_analysis.config import load_competition_catalog
from goal_analysis.providers import ApiFootballClient, ApiFootballFixtureProvider, ProviderError


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely verify API-Football fixture access")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--competition", default="DE1")
    args = parser.parse_args()

    catalog = load_competition_catalog(Path("config/competitions.toml"))
    league_ids = {item.id: item.api_football_id for item in catalog.competitions}
    provider = ApiFootballFixtureProvider(ApiFootballClient(), league_ids, catalog.timezone)
    try:
        fixtures = provider.list_fixtures(
            date.fromisoformat(args.date), [args.competition]
        )
    except ProviderError as error:
        print(f"API check failed: {error}")
        return 1

    print(f"API check succeeded: {len(fixtures)} fixture(s)")
    for fixture in fixtures[:5]:
        print(f"{fixture.kickoff.isoformat()} | {fixture.home_team.name} - {fixture.away_team.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
