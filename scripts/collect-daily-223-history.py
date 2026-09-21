"""Collect cached API-Football history, one call per explicit league/season pair."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from goal_analysis.config import Settings
from goal_analysis.providers.api_football import ApiFootballClient
from goal_analysis.providers.api_football_history import ApiFootballHistoryCollector
from goal_analysis.providers.base import ProviderError
from goal_analysis.storage import CacheStore, Database, SnapshotStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--league-season", action="append", required=True, help="API league ID:season, repeatable"
    )
    parser.add_argument("--max-api-calls", type=int, default=8)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        pairs = []
        for value in args.league_season:
            league, season = value.split(":")
            pairs.append((int(league), int(season)))
        # Reserve the output before spending API quota; existing snapshots are immutable.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            try:
                settings = Settings()
                database = Database(settings.database_path)
                database.initialize()
                client = ApiFootballClient(
                    snapshot_store=SnapshotStore(database, settings.raw_snapshot_dir)
                )
                collector = ApiFootballHistoryCollector(client, CacheStore(database))
                result = collector.collect(pairs, args.max_api_calls, args.force_refresh)
                stream.write(
                    json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
                )
            except Exception:
                stream.close()
                args.output.unlink(missing_ok=True)
                raise
        print(
            f"History saved: {len(result['records'])} matches; API calls={result['api_calls']}; cache hits={result['cache_hits']}"
        )
        return 0
    except (ProviderError, OSError, ValueError, TypeError, KeyError) as error:
        print(f"History DATA_BLOCKED: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
