from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from pathlib import Path

from goal_analysis.config import Settings, load_competition_catalog
from goal_analysis.features import GoalFeatureEngine, load_history_csv
from goal_analysis.jobs import DailyFixtureCollector, DailyScreeningPipeline, write_pipeline_json
from goal_analysis.providers import ApiFootballClient, ApiFootballFixtureProvider, ProviderError
from goal_analysis.screening import ScreeningPolicy
from goal_analysis.storage import CacheStore, Database, SnapshotStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Run cached, odds-free daily screening")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument(
        "--history-csv",
        default=None,
        help="Provider-neutral settled match history used for deterministic ranking",
    )
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date)
    settings = Settings()
    catalog = load_competition_catalog(Path("config/competitions.toml"))
    database = Database(settings.database_path)
    database.initialize()
    snapshots = SnapshotStore(database, settings.raw_snapshot_dir)
    client = ApiFootballClient(snapshot_store=snapshots)
    league_ids = {item.id: item.api_football_id for item in catalog.competitions}
    provider = ApiFootballFixtureProvider(client, league_ids, catalog.timezone)
    collector = DailyFixtureCollector(provider, CacheStore(database))
    policy = ScreeningPolicy(
        target_date=target_date,
        timezone_name=catalog.timezone,
        allowed_competition_ids=catalog.allowed_ids,
        shortlist_max=catalog.shortlist_max,
    )
    feature_engine = (
        GoalFeatureEngine(load_history_csv(Path(args.history_csv)))
        if args.history_csv
        else None
    )
    pipeline = DailyScreeningPipeline(collector, policy, feature_engine)

    try:
        result = pipeline.run(datetime.now(timezone.utc), args.force_refresh)
    except ProviderError as error:
        print(f"Daily screening failed: {error}")
        return 1

    output = Path(args.output or f"reports/daily/screening-{target_date.isoformat()}.json")
    write_pipeline_json(output, result)
    source = "cache" if result.collection.from_cache else "API-Football"
    print(
        f"Daily screening succeeded: accepted={len(result.screening.accepted)}, "
        f"rejected={len(result.screening.rejected)}, source={source}"
    )
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
