"""Run the independent daily history/fixture/odds pipeline or check its local setup."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from goal_analysis.config import Settings
from goal_analysis.config.daily_223_live import validate_live_config
from goal_analysis.jobs.daily_223_live import (
    blocked_report,
    run_daily223_live,
    write_daily223_bundle,
)
from goal_analysis.providers.api_football import ApiFootballClient
from goal_analysis.providers.base import ProviderError
from goal_analysis.providers.daily_223_odds import Daily223OddsFeed
from goal_analysis.storage import CacheStore, Database, SnapshotStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/daily223-live.json"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-config", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    now = datetime.now(UTC)
    today = now.astimezone(ZoneInfo("Europe/Berlin")).date()
    if args.check_config:
        try:
            config = validate_live_config(json.loads(args.config.read_text("utf-8-sig")))
            missing = [
                key
                for key in ("API_FOOTBALL_KEY", "THE_ODDS_API_KEY")
                if not os.environ.get(key, "").strip()
            ]
            print(
                f"Config valid: {len(config['leagues'])} leagues, odds_region={config['odds_region']}, preferred={','.join(config['preferred_bookmakers'])}"
            )
            print("Missing environment variables: " + (", ".join(missing) if missing else "none"))
            print("No network request was made; authentication/access is not verified.")
            return 2 if missing else 0
        except (OSError, ValueError, TypeError, KeyError) as error:
            print(f"Configuration incomplete: {error}")
            return 2
    output = args.output_dir or Path("reports/daily223") / f"{today}-{uuid4().hex[:12]}"
    try:
        output.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        print(f"Cannot create new run directory: {error}")
        return 1
    try:
        config = validate_live_config(json.loads(args.config.read_text("utf-8-sig")))
        for key in ("API_FOOTBALL_KEY", "THE_ODDS_API_KEY"):
            if not os.environ.get(key, "").strip():
                raise ProviderError(f"{key} is not configured")
        settings = Settings()
        database = Database(settings.database_path)
        database.initialize()
        snapshots = SnapshotStore(database, settings.raw_snapshot_dir)
        football = ApiFootballClient(snapshot_store=snapshots)
        odds = Daily223OddsFeed(
            os.environ["THE_ODDS_API_KEY"],
            config["odds_region"],
            config["max_odds_credits"],
            snapshots,
        )
        bundle = run_daily223_live(config, football, odds, CacheStore(database))
    except (OSError, ValueError, TypeError, KeyError, ProviderError, AttributeError) as error:
        bundle = {
            "schema_version": 1,
            "artifacts": {"report": blocked_report(today, now, "setup", str(error))},
        }
    try:
        write_daily223_bundle(output, bundle)
    except OSError as error:
        print(f"Cannot save complete run bundle: {error}")
        return 1
    report = bundle["artifacts"]["report"]
    print(
        f"DAILY_223: {report['construction_status']}; price={report['combined_price']}; output={output}"
    )
    print("RESEARCH_ONLY; real_wager_placed=false")
    if report.get("reason"):
        print(report["reason"])
    return 0 if report["construction_status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
