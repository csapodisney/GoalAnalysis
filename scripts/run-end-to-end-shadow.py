from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

from goal_analysis.agents import OpenAIResponsesRoleRunner, PromptRegistry
from goal_analysis.config import Settings, load_competition_catalog
from goal_analysis.engine import TicketGatePolicy
from goal_analysis.features import GoalFeatureEngine, load_history_csv
from goal_analysis.jobs import (
    DailyFixtureCollector,
    DailyScreeningPipeline,
    complete_shadow_run,
    pipeline_to_dict,
    write_shadow_bundle,
)
from goal_analysis.providers import (
    ApiFootballClient,
    ApiFootballFixtureProvider,
    OddsEventRef,
    TheOddsApiProvider,
)
from goal_analysis.screening import ScreeningPolicy
from goal_analysis.storage import CacheStore, Database, SnapshotStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the complete Goal Analysis shadow pipeline")
    parser.add_argument("--date", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--history-csv", required=True)
    parser.add_argument("--odds-event-map", required=True)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--prompt-version", default="kerekasztal-v1")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--max-legs", type=int, default=6)
    parser.add_argument("--max-quote-age", type=int, default=600)
    parser.add_argument("--target-price", type=float, default=None)
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date)
    observed_at = datetime.now(UTC)
    settings = Settings()
    catalog = load_competition_catalog(Path("config/competitions.toml"))
    database = Database(settings.database_path)
    database.initialize()
    snapshots = SnapshotStore(database, settings.raw_snapshot_dir)

    football_client = ApiFootballClient(snapshot_store=snapshots)
    football_provider = ApiFootballFixtureProvider(
        football_client,
        {item.id: item.api_football_id for item in catalog.competitions},
        catalog.timezone,
    )
    collector = DailyFixtureCollector(football_provider, CacheStore(database))
    feature_engine = GoalFeatureEngine(load_history_csv(Path(args.history_csv)))
    policy = ScreeningPolicy(
        target_date,
        catalog.timezone,
        catalog.allowed_ids,
        catalog.shortlist_max,
    )
    daily = DailyScreeningPipeline(collector, policy, feature_engine)

    try:
        screening = pipeline_to_dict(daily.run(observed_at, args.force_refresh))
        prompts = PromptRegistry(Path("config/prompts"), args.prompt_version)
        role_runner = OpenAIResponsesRoleRunner(prompts)
        odds_provider = TheOddsApiProvider(
            _load_event_refs(Path(args.odds_event_map)), snapshot_store=snapshots
        )
        bundle = complete_shadow_run(
            screening,
            role_runner,
            odds_provider,
            observed_at,
            TicketGatePolicy(args.max_legs, args.max_quote_age, args.target_price),
        )
        output = Path(args.output_dir or f"reports/shadow/{target_date.isoformat()}")
        write_shadow_bundle(output, bundle)
    except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"End-to-end shadow run failed: {error}")
        return 1

    gate = bundle["artifacts"]["ticket_gate"]
    tokens = bundle["artifacts"]["token_usage"]["totals"]["total_tokens"]
    print(
        f"Shadow run complete: ticket_ready={gate['ticket_ready']}, "
        f"legs={len(gate['legs'])}, tokens={tokens}"
    )
    print(f"Output directory: {output}")
    print("Real wager placed: false")
    return 0


def _load_event_refs(path: Path) -> tuple[OddsEventRef, ...]:
    payload = json.loads(path.read_text("utf-8"))
    if not isinstance(payload, list):
        raise TypeError("odds event map must be a JSON list")
    return tuple(
        OddsEventRef(
            fixture_id=str(item["fixture_id"]),
            sport_key=str(item["sport_key"]),
            event_id=str(item["event_id"]),
        )
        for item in payload
    )


if __name__ == "__main__":
    raise SystemExit(main())
