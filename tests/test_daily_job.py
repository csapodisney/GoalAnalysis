from datetime import date, datetime, timedelta, timezone

from goal_analysis.jobs import DailyFixtureCollector
from goal_analysis.normalization.models import Competition, Fixture, Team
from goal_analysis.storage import CacheStore, Database


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def list_fixtures(self, target_date, competition_ids=None):
        self.calls += 1
        return [
            Fixture(
                id="fixture-1",
                provider=self.name,
                competition=Competition("DE1", "Bundesliga", "DE"),
                home_team=Team("home", "Home"),
                away_team=Team("away", "Away"),
                kickoff=datetime(2026, 9, 20, 15, 30, tzinfo=timezone.utc),
                provider_ids={"fake": "fixture-1"},
            )
        ]


def collector(tmp_path):
    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    provider = FakeProvider()
    return DailyFixtureCollector(provider, CacheStore(database)), provider


def test_second_collection_uses_cache(tmp_path) -> None:
    job, provider = collector(tmp_path)
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)

    first = job.collect(date(2026, 9, 20), ["DE1"], now)
    second = job.collect(date(2026, 9, 20), ["DE1"], now + timedelta(minutes=1))

    assert first.from_cache is False
    assert second.from_cache is True
    assert provider.calls == 1
    assert second.fixtures == first.fixtures


def test_expired_cache_refreshes_provider(tmp_path) -> None:
    job, provider = collector(tmp_path)
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)

    job.collect(date(2026, 9, 20), ["DE1"], now)
    refreshed = job.collect(date(2026, 9, 20), ["DE1"], now + timedelta(hours=6))

    assert refreshed.from_cache is False
    assert provider.calls == 2


def test_force_refresh_bypasses_valid_cache(tmp_path) -> None:
    job, provider = collector(tmp_path)
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)

    job.collect(date(2026, 9, 20), ["DE1"], now)
    refreshed = job.collect(
        date(2026, 9, 20), ["DE1"], now + timedelta(minutes=1), force_refresh=True
    )

    assert refreshed.from_cache is False
    assert provider.calls == 2


def test_competition_order_does_not_change_cache_key(tmp_path) -> None:
    job, provider = collector(tmp_path)
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)

    first = job.collect(date(2026, 9, 20), ["GB1", "DE1", "DE1"], now)
    second = job.collect(date(2026, 9, 20), ["DE1", "GB1"], now)

    assert first.cache_key == second.cache_key
    assert provider.calls == 1
