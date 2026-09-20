import json
from datetime import UTC, date, datetime, timedelta

from goal_analysis.features import FeaturePolicy, GoalFeatureEngine, HistoricalMatch
from goal_analysis.jobs import DailyFixtureCollector, DailyScreeningPipeline, write_pipeline_json
from goal_analysis.normalization.models import Competition, Fixture, Team
from goal_analysis.screening import ScreeningPolicy
from goal_analysis.storage import CacheStore, Database

NOW = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)


class TwoFixtureProvider:
    name = "test"

    def list_fixtures(self, target_date, competition_ids=None):
        return [
            Fixture(
                "high",
                self.name,
                Competition("DE1", "Bundesliga"),
                Team("high-home", "High Home"),
                Team("high-away", "High Away"),
                NOW + timedelta(hours=4),
            ),
            Fixture(
                "low",
                self.name,
                Competition("DE1", "Bundesliga"),
                Team("low-home", "Low Home"),
                Team("low-away", "Low Away"),
                NOW + timedelta(hours=3),
            ),
        ]


def historical(team_prefix: str, goals: tuple[int, int]) -> list[HistoricalMatch]:
    return [
        HistoricalMatch(
            f"{team_prefix}-{index}",
            "DE1",
            NOW - timedelta(days=index + 1),
            f"{team_prefix}-home",
            f"{team_prefix}-away",
            *goals,
        )
        for index in range(3)
    ]


def test_pipeline_ranks_by_features_and_exports_explanation(tmp_path) -> None:
    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    collector = DailyFixtureCollector(TwoFixtureProvider(), CacheStore(database))
    policy = ScreeningPolicy(date(2026, 9, 20), "Europe/Berlin", frozenset({"DE1"}), 30)
    engine = GoalFeatureEngine(
        historical("high", (3, 2)) + historical("low", (1, 0)),
        policy=FeaturePolicy(minimum_sample=3),
    )
    pipeline = DailyScreeningPipeline(collector, policy, engine)

    result = pipeline.run(NOW)
    output = tmp_path / "ranked.json"
    write_pipeline_json(output, result)
    payload = json.loads(output.read_text("utf-8"))

    assert [item["fixture_id"] for item in payload["accepted"]] == ["high", "low"]
    assert payload["accepted"][0]["feature_version"] == "goal-shortlist-v1"
    assert payload["accepted"][0]["features"]["average_total_goals"] == 5.0
    assert payload["accepted"][0]["ranking"]["score"] > payload["accepted"][1]["ranking"]["score"]
    assert "odds" not in json.dumps(payload).lower()
