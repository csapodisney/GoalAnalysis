from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from goal_analysis.normalization.models import OddsQuote
from goal_analysis.storage import SnapshotStore

from .base import ProviderError

Transport = Callable[
    [str, Mapping[str, str]],
    tuple[list[dict[str, Any]], Mapping[str, str], bytes],
]


def _default_transport(
    url: str, headers: Mapping[str, str]
) -> tuple[list[dict[str, Any]], Mapping[str, str], bytes]:
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, list):
                raise ProviderError("The Odds API response must be a list")
            return payload, dict(response.headers.items()), raw
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise ProviderError(f"The Odds API HTTP {error.code}: {detail[:300]}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ProviderError(f"The Odds API request failed: {error}") from error


@dataclass(frozen=True, slots=True)
class OddsEventRef:
    fixture_id: str
    sport_key: str
    event_id: str


@dataclass(frozen=True, slots=True)
class OddsApiUsage:
    remaining: int | None = None
    used: int | None = None
    last_cost: int | None = None


class TheOddsApiProvider:
    """Live post-ranking odds adapter using explicit cross-provider event IDs."""

    name = "the_odds_api"
    BASE_URL = "https://api.the-odds-api.com/v4/sports"

    def __init__(
        self,
        event_refs: Sequence[OddsEventRef],
        api_key: str | None = None,
        region: str = "eu",
        transport: Transport | None = None,
        snapshot_store: SnapshotStore | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("THE_ODDS_API_KEY")
        self.region = region
        self.transport = transport or _default_transport
        self.snapshot_store = snapshot_store
        self.event_refs = {item.fixture_id: item for item in event_refs}
        self.last_usage = OddsApiUsage()

    def get_quotes(
        self,
        fixture_ids: Sequence[str],
        market_keys: Sequence[str],
        observed_at: datetime,
    ) -> Sequence[OddsQuote]:
        del observed_at
        if not self.api_key:
            raise ProviderError("THE_ODDS_API_KEY is not configured")
        unknown = sorted(set(fixture_ids) - self.event_refs.keys())
        if unknown:
            raise ProviderError(f"Missing The Odds API event mapping: {', '.join(unknown)}")

        by_sport: dict[str, list[OddsEventRef]] = defaultdict(list)
        for fixture_id in fixture_ids:
            by_sport[self.event_refs[fixture_id].sport_key].append(self.event_refs[fixture_id])
        api_markets = sorted({_api_market(key) for key in market_keys})
        quotes: list[OddsQuote] = []
        for sport_key, refs in sorted(by_sport.items()):
            query = urllib.parse.urlencode(
                {
                    "apiKey": self.api_key,
                    "regions": self.region,
                    "markets": ",".join(api_markets),
                    "oddsFormat": "decimal",
                    "dateFormat": "iso",
                    "eventIds": ",".join(item.event_id for item in refs),
                }
            )
            url = f"{self.BASE_URL}/{urllib.parse.quote(sport_key)}/odds/?{query}"
            payload, headers, raw = self.transport(url, {"Accept": "application/json"})
            if self.snapshot_store is not None:
                self.snapshot_store.save(
                    provider=self.name,
                    resource_type="odds",
                    content=raw,
                    media_type=headers.get("content-type", "application/json"),
                    source_url=f"{self.BASE_URL}/{sport_key}/odds/",
                )
            self.last_usage = OddsApiUsage(
                _optional_int(headers.get("x-requests-remaining")),
                _optional_int(headers.get("x-requests-used")),
                _optional_int(headers.get("x-requests-last")),
            )
            event_to_fixture = {item.event_id: item.fixture_id for item in refs}
            quotes.extend(_parse_events(payload, event_to_fixture, set(market_keys)))
        return tuple(quotes)


def _api_market(market_key: str) -> str:
    if market_key.startswith("totals_"):
        return "totals"
    if market_key == "match_result":
        return "h2h"
    if market_key == "btts":
        return "btts"
    raise ProviderError(f"Unsupported live odds market: {market_key}")


def _parse_events(
    events: Sequence[Mapping[str, Any]],
    event_to_fixture: Mapping[str, str],
    requested_markets: set[str],
) -> list[OddsQuote]:
    quotes: list[OddsQuote] = []
    for event in events:
        fixture_id = event_to_fixture.get(str(event.get("id")))
        if fixture_id is None:
            continue
        home_team = str(event.get("home_team", ""))
        away_team = str(event.get("away_team", ""))
        for bookmaker in event.get("bookmakers", []):
            bookmaker_key = str(bookmaker["key"])
            for market in bookmaker.get("markets", []):
                api_key = str(market["key"])
                quoted_at = datetime.fromisoformat(
                    str(market.get("last_update") or bookmaker["last_update"])
                )
                for outcome in market.get("outcomes", []):
                    normalized = _normalize_outcome(api_key, outcome, home_team, away_team)
                    if normalized is None:
                        continue
                    market_key, selection_key = normalized
                    if market_key not in requested_markets:
                        continue
                    quotes.append(
                        OddsQuote(
                            fixture_id=fixture_id,
                            bookmaker=bookmaker_key,
                            market_key=market_key,
                            selection_key=selection_key,
                            decimal_price=float(outcome["price"]),
                            quoted_at=quoted_at,
                            provider="the_odds_api",
                        )
                    )
    return quotes


def _normalize_outcome(
    api_market: str,
    outcome: Mapping[str, Any],
    home_team: str,
    away_team: str,
) -> tuple[str, str] | None:
    name = str(outcome.get("name", ""))
    if api_market == "totals" and outcome.get("point") is not None:
        point = str(outcome["point"]).replace(".", "_")
        return f"totals_{point}", name.lower()
    if api_market == "btts" and name.lower() in {"yes", "no"}:
        return "btts", name.lower()
    if api_market == "h2h":
        if name == home_team:
            return "match_result", "home"
        if name == away_team:
            return "match_result", "away"
        if name.lower() == "draw":
            return "match_result", "draw"
    return None


def _optional_int(value: str | None) -> int | None:
    return int(value) if value is not None and value.isdigit() else None
