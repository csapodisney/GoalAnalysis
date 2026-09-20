from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from goal_analysis.features import GoalFeatureEngine, GoalFeatureSet, RankingBreakdown
from goal_analysis.screening import ScreeningPolicy, ScreeningResult, screen_fixtures

from .daily import DailyCollectionResult, DailyFixtureCollector


@dataclass(frozen=True, slots=True)
class DailyPipelineResult:
    target_date: date
    generated_at: datetime
    collection: DailyCollectionResult
    screening: ScreeningResult
    feature_sets: Mapping[str, GoalFeatureSet]
    ranking_breakdowns: Mapping[str, RankingBreakdown]


class DailyScreeningPipeline:
    """Connect cached fixture collection to deterministic, odds-free screening."""

    def __init__(
        self,
        collector: DailyFixtureCollector,
        policy: ScreeningPolicy,
        feature_engine: GoalFeatureEngine | None = None,
    ) -> None:
        self.collector = collector
        self.policy = policy
        self.feature_engine = feature_engine

    def run(self, now: datetime, force_refresh: bool = False) -> DailyPipelineResult:
        collection = self.collector.collect(
            self.policy.target_date,
            sorted(self.policy.allowed_competition_ids),
            now,
            force_refresh=force_refresh,
        )
        feature_sets: dict[str, GoalFeatureSet] = {}
        ranking_breakdowns: dict[str, RankingBreakdown] = {}

        def score(fixture) -> float:
            if self.feature_engine is None:
                return 0.0
            features, ranking = self.feature_engine.evaluate(fixture)
            feature_sets[fixture.id] = features
            ranking_breakdowns[fixture.id] = ranking
            return ranking.score

        screening = screen_fixtures(collection.fixtures, self.policy, now, score)
        return DailyPipelineResult(
            self.policy.target_date,
            now,
            collection,
            screening,
            feature_sets,
            ranking_breakdowns,
        )


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
                "feature_version": (
                    GoalFeatureEngine.RANKING_VERSION
                    if item.fixture.id in result.feature_sets
                    else None
                ),
                "features": (
                    result.feature_sets[item.fixture.id].to_dict()
                    if item.fixture.id in result.feature_sets
                    else None
                ),
                "ranking": (
                    result.ranking_breakdowns[item.fixture.id].to_dict()
                    if item.fixture.id in result.ranking_breakdowns
                    else None
                ),
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
