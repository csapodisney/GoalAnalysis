import json
from datetime import date, datetime, timedelta, timezone

from goal_analysis.jobs import (
    DailyFixtureCollector,
    DailyScreeningPipeline,
    write_pipeline_json,
)
from goal_analysis.normalization.models import Competition, Fixture, Team
from goal_analysis.screening import ScreeningPolicy
from goal_analysis.storage import CacheStore, Database


class MixedProvider:
    name = "mixed"

    def list_fixtures(self, target_date, competition_ids=None):
        base = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
        return [
            Fixture(
                "valid",
                self.name,
                Competition("DE1", "Bundesliga", "DE"),
                Team("h1", "Home One"),
                Team("a1", "Away One"),
                base + timedelta(hours=4),
            ),
            Fixture(
                "wrong-league",
                self.name,
                Competition("XX1", "Other", "XX"),
                Team("h2", "Home Two"),
                Team("a2", "Away Two"),
                base + timedelta(hours=4),
            ),
        ]


def test_pipeline_writes_accepted_and_rejected_packet(tmp_path) -> None:
    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    collector = DailyFixtureCollector(MixedProvider(), CacheStore(database))
    policy = ScreeningPolicy(
        date(2026, 9, 20), "Europe/Berlin", frozenset({"DE1"}), 30
    )
    pipeline = DailyScreeningPipeline(collector, policy)
    result = pipeline.run(datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc))
    output = tmp_path / "screening.json"

    write_pipeline_json(output, result)
    payload = json.loads(output.read_text("utf-8"))

    assert payload["schema_version"] == 1
    assert payload["accepted_count"] == 1
    assert payload["accepted"][0]["fixture_id"] == "valid"
    assert payload["rejected_count"] == 1
    assert payload["rejected"][0]["code"] == "competition_not_allowed"
    assert "odds" not in json.dumps(payload).lower()
