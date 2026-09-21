import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from goal_analysis.providers import OddsEventRef, TheOddsApiProvider
from goal_analysis.providers.base import ProviderError
from goal_analysis.storage import Database, SnapshotStore

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)


def payload() -> list[dict]:
    return [
        {
            "id": "event-1",
            "home_team": "Home FC",
            "away_team": "Away FC",
            "bookmakers": [
                {
                    "key": "book-a",
                    "last_update": "2026-09-20T09:59:30+00:00",
                    "markets": [
                        {
                            "key": "totals",
                            "last_update": "2026-09-20T09:59:30+00:00",
                            "outcomes": [
                                {"name": "Over", "price": 1.91, "point": 2.5},
                                {"name": "Under", "price": 1.89, "point": 2.5},
                            ],
                        },
                        {
                            "key": "h2h",
                            "last_update": "2026-09-20T09:59:30+00:00",
                            "outcomes": [
                                {"name": "Home FC", "price": 2.1},
                                {"name": "Away FC", "price": 3.3},
                                {"name": "Draw", "price": 3.1},
                            ],
                        },
                    ],
                }
            ],
        }
    ]


def test_live_quotes_are_normalized_snapshotted_and_metered(tmp_path) -> None:
    seen: list[str] = []
    raw = json.dumps(payload()).encode()

    def transport(url, headers):
        seen.append(url)
        assert headers == {"Accept": "application/json"}
        return payload(), {
            "content-type": "application/json",
            "x-requests-remaining": "490",
            "x-requests-used": "10",
            "x-requests-last": "1",
        }, raw

    database = Database(tmp_path / "goal-analysis.sqlite3")
    database.initialize()
    provider = TheOddsApiProvider(
        [OddsEventRef("fixture-1", "soccer_germany_bundesliga", "event-1")],
        api_key="secret-value",
        transport=transport,
        snapshot_store=SnapshotStore(database, tmp_path / "raw"),
    )

    quotes = provider.get_quotes(
        ["fixture-1"], ["totals_2_5", "match_result"], NOW
    )

    assert {(item.market_key, item.selection_key) for item in quotes} == {
        ("totals_2_5", "over"),
        ("totals_2_5", "under"),
        ("match_result", "home"),
        ("match_result", "away"),
        ("match_result", "draw"),
    }
    query = parse_qs(urlparse(seen[0]).query)
    assert query["eventIds"] == ["event-1"]
    assert query["markets"] == ["h2h,totals"]
    assert query["oddsFormat"] == ["decimal"]
    assert provider.last_usage.remaining == 490
    with database.connect() as connection:
        row = connection.execute("SELECT source_url FROM raw_snapshots").fetchone()
    assert row["source_url"].endswith("/soccer_germany_bundesliga/odds/")
    assert "secret-value" not in row["source_url"]


def test_missing_event_mapping_fails_closed() -> None:
    provider = TheOddsApiProvider([], api_key="key", transport=lambda *_: ([], {}, b"[]"))

    with pytest.raises(ProviderError, match="Missing The Odds API event mapping"):
        provider.get_quotes(["unknown"], ["totals_2_5"], NOW)


def test_missing_key_and_unsupported_market_fail_closed(monkeypatch) -> None:
    monkeypatch.setenv("THE_ODDS_API_KEY", "real-environment-key-must-not-be-used")
    ref = OddsEventRef("fixture-1", "soccer_germany_bundesliga", "event-1")
    with pytest.raises(ProviderError, match="not configured"):
        TheOddsApiProvider([ref], api_key="").get_quotes(["fixture-1"], ["totals_2_5"], NOW)
    with pytest.raises(ProviderError, match="Unsupported live odds market"):
        TheOddsApiProvider([ref], api_key="key").get_quotes(["fixture-1"], ["corners"], NOW)
