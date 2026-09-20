from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from goal_analysis.normalization.models import Competition, Fixture, FixtureStatus, Team
from goal_analysis.storage.snapshots import SnapshotStore

from .base import ProviderError

TransportResult = (
    tuple[dict[str, Any], Mapping[str, str]]
    | tuple[dict[str, Any], Mapping[str, str], bytes]
)
Transport = Callable[[str, Mapping[str, str]], TransportResult]


def _default_transport(
    url: str, headers: Mapping[str, str]
) -> tuple[dict[str, Any], Mapping[str, str], bytes]:
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw_body = response.read()
            payload = json.loads(raw_body.decode("utf-8"))
            return payload, dict(response.headers.items()), raw_body
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise ProviderError(f"API-Football HTTP {error.code}: {body[:300]}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ProviderError(f"API-Football request failed: {error}") from error


@dataclass(frozen=True, slots=True)
class ApiUsage:
    remaining_day: int | None = None
    limit_day: int | None = None


class ApiFootballClient:
    BASE_URL = "https://v3.football.api-sports.io"

    def __init__(
        self,
        api_key: str | None = None,
        transport: Transport | None = None,
        snapshot_store: SnapshotStore | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("API_FOOTBALL_KEY")
        self._transport = transport or _default_transport
        self._snapshot_store = snapshot_store
        self.last_usage = ApiUsage()

    def request(self, endpoint: str, params: Mapping[str, str | int]) -> dict[str, Any]:
        if not self._api_key:
            raise ProviderError("API_FOOTBALL_KEY is not configured")
        query = urllib.parse.urlencode(params)
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}?{query}"
        transport_result = self._transport(url, {"x-apisports-key": self._api_key})
        if len(transport_result) == 2:
            payload, headers = transport_result
            raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        else:
            payload, headers, raw_body = transport_result
        if self._snapshot_store is not None:
            self._snapshot_store.save(
                provider="api_football",
                resource_type=endpoint.strip("/").replace("/", "_") or "root",
                content=raw_body,
                media_type=headers.get("content-type", "application/json"),
                source_url=url,
            )
        self.last_usage = ApiUsage(
            remaining_day=_optional_int(headers.get("x-ratelimit-requests-remaining")),
            limit_day=_optional_int(headers.get("x-ratelimit-requests-limit")),
        )
        errors = payload.get("errors")
        if errors:
            raise ProviderError(f"API-Football response errors: {errors}")
        if "response" not in payload:
            raise ProviderError("API-Football response field is missing")
        return payload


class ApiFootballFixtureProvider:
    name = "api_football"

    def __init__(
        self,
        client: ApiFootballClient,
        league_ids: Mapping[str, int],
        timezone_name: str = "Europe/Berlin",
    ) -> None:
        self.client = client
        self.league_ids = dict(league_ids)
        self.timezone_name = timezone_name

    def list_fixtures(
        self,
        target_date: date,
        competition_ids: Sequence[str] | None = None,
    ) -> Sequence[Fixture]:
        requested = list(competition_ids or self.league_ids.keys())
        unknown = sorted(set(requested) - self.league_ids.keys())
        if unknown:
            raise ProviderError(f"Unknown competition ids: {', '.join(unknown)}")

        fixtures: list[Fixture] = []
        season = target_date.year if target_date.month >= 7 else target_date.year - 1
        for competition_id in requested:
            payload = self.client.request(
                "fixtures",
                {
                    "date": target_date.isoformat(),
                    "league": self.league_ids[competition_id],
                    "season": season,
                    "timezone": self.timezone_name,
                },
            )
            fixtures.extend(
                _parse_fixture(item, competition_id, self.name)
                for item in payload["response"]
            )
        return fixtures


def _parse_fixture(item: Mapping[str, Any], competition_id: str, provider: str) -> Fixture:
    fixture_data = item["fixture"]
    league_data = item["league"]
    teams = item["teams"]
    status_code = fixture_data.get("status", {}).get("short", "TBD")
    return Fixture(
        id=str(fixture_data["id"]),
        provider=provider,
        competition=Competition(
            id=competition_id,
            name=str(league_data["name"]),
            country_code=league_data.get("country"),
        ),
        home_team=Team(id=str(teams["home"]["id"]), name=str(teams["home"]["name"])),
        away_team=Team(id=str(teams["away"]["id"]), name=str(teams["away"]["name"])),
        kickoff=datetime.fromisoformat(str(fixture_data["date"]).replace("Z", "+00:00")),
        status=_status(status_code),
        provider_ids={"api_football": str(fixture_data["id"])},
    )


def _status(code: str) -> FixtureStatus:
    if code in {"TBD", "NS"}:
        return FixtureStatus.SCHEDULED
    if code in {"PST", "SUSP"}:
        return FixtureStatus.POSTPONED
    if code in {"CANC", "ABD", "AWD", "WO"}:
        return FixtureStatus.CANCELLED
    if code in {"FT", "AET", "PEN"}:
        return FixtureStatus.FINISHED
    return FixtureStatus.LIVE


def _optional_int(value: str | None) -> int | None:
    return int(value) if value is not None and value.isdigit() else None
