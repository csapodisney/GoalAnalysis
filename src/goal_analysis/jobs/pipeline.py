from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from goal_analysis.screening import ScreeningPolicy, ScreeningResult, screen_fixtures

from .daily import DailyCollectionResult, DailyFixtureCollector


@dataclass(frozen=True, slots=True)
class DailyPipelineResult:
    target_date: date
    generated_at: datetime
    collection: DailyCollectionResult
    screening: ScreeningResult


class DailyScreeningPipeline:
    """Connect cached fixture collection to deterministic, odds-free screening."""

    def __init__(self, collector: DailyFixtureCollector, policy: ScreeningPolicy) -> None:
        self.collector = collector
        self.policy = policy

    def run(self, now: datetime, force_refresh: bool = False) -> DailyPipelineResult:
        collection = self.collector.collect(
            self.policy.target_date,
            sorted(self.policy.allowed_competition_ids),
            now,
            force_refresh=force_refresh,
        )
        screening = screen_fixtures(collection.fixtures, self.policy, now)
        return DailyPipelineResult(self.policy.target_date, now, collection, screening)


def write_pipeline_json(path: Path, result: DailyPipelineResult) -> None:
    """Atomically write the compact, auditable pre-LLM fixture packet."""

    payload = pipeline_to_dict(result)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    temporary.replace(destination)


def pipeline_to_dict(result: DailyPipelineResult) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "target_date": result.target_date.isoformat(),
        "generated_at": result.generated_at.isoformat(),
        "source": "cache" if result.collection.from_cache else "provider",
        "input_count": result.screening.input_count,
        "accepted_count": len(result.screening.accepted),
        "rejected_count": len(result.screening.rejected),
        "accepted": [
            {
                "fixture_id": item.fixture.id,
                "provider": item.fixture.provider,
                "competition_id": item.fixture.competition.id,
                "competition_name": item.fixture.competition.name,
                "home_team": item.fixture.home_team.name,
                "away_team": item.fixture.away_team.name,
                "kickoff": item.fixture.kickoff.isoformat(),
                "status": item.fixture.status.value,
                "screening_score": item.score,
            }
            for item in result.screening.accepted
        ],
        "rejected": [
            {
                "fixture_id": rejection.fixture_id,
                "code": rejection.code.value,
                "detail": rejection.detail,
            }
            for rejection in result.screening.rejected
        ],
    }
