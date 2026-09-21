import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from goal_analysis.normalization.models import FixtureStatus
from goal_analysis.providers import ApiFootballClient, ApiFootballFixtureProvider, ProviderError


def sample_payload() -> dict:
    return json.loads(Path("tests/fixtures/api-football-fixtures.json").read_text("utf-8"))


def test_missing_key_fails_without_network_access() -> None:
    client = ApiFootballClient(api_key=None, transport=lambda *_: ({}, {}))
    client._api_key = None
    with pytest.raises(ProviderError, match="API_FOOTBALL_KEY"):
        client.request("fixtures", {"date": "2026-09-20"})


def test_fixture_provider_builds_expected_query_and_normalizes() -> None:
    captured = {}

    def transport(url, headers):
        captured["url"] = url
        captured["headers"] = headers
        return sample_payload(), {
            "x-ratelimit-requests-remaining": "99",
            "x-ratelimit-requests-limit": "100",
        }

    client = ApiFootballClient(api_key="not-a-real-key", transport=transport)
    provider = ApiFootballFixtureProvider(client, {"DE1": 78})
    fixtures = provider.list_fixtures(date(2026, 9, 20), ["DE1"])

    query = parse_qs(urlparse(captured["url"]).query)
    assert query == {
        "date": ["2026-09-20"],
        "league": ["78"],
        "season": ["2026"],
        "timezone": ["Europe/Berlin"],
    }
    assert captured["headers"] == {"x-apisports-key": "not-a-real-key"}
    assert len(fixtures) == 1
    assert fixtures[0].competition.id == "DE1"
    assert fixtures[0].status is FixtureStatus.SCHEDULED
    assert fixtures[0].home_team.name == "Home FC"
    assert client.last_usage.remaining_day == 99


def test_provider_rejects_unknown_internal_competition() -> None:
    provider = ApiFootballFixtureProvider(ApiFootballClient("fake"), {"DE1": 78})
    with pytest.raises(ProviderError, match="Unknown competition"):
        provider.list_fixtures(date(2026, 9, 20), ["XX1"])


def test_api_errors_are_not_silently_accepted() -> None:
    client = ApiFootballClient(
        "fake",
        transport=lambda *_: ({"errors": {"rateLimit": "exceeded"}, "response": []}, {}),
    )
    with pytest.raises(ProviderError, match="rateLimit"):
        client.request("fixtures", {"date": "2026-09-20"})
