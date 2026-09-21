"""The Odds API feed for DAILY_223, retaining event identity and timestamps."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from goal_analysis.storage import SnapshotStore

from .base import ProviderError

Transport = Callable[[str, Mapping[str, str]], tuple[Any, Mapping[str, str], bytes]]


def _transport(url: str, headers: Mapping[str, str]) -> tuple[Any, Mapping[str, str], bytes]:
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=dict(headers)), timeout=30
        ) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")), dict(response.headers.items()), raw
    except urllib.error.HTTPError as error:
        # Error bodies and URLs can echo query-string credentials.
        raise ProviderError(f"The Odds API HTTP {error.code}") from None
    except (OSError, ValueError):
        raise ProviderError("The Odds API transport or JSON decoding failed") from None


class Daily223OddsFeed:
    ROOT = "https://api.the-odds-api.com/v4/sports"

    def __init__(
        self,
        api_key: str,
        region: str,
        max_credits: int,
        snapshots: SnapshotStore | None = None,
        transport: Transport = _transport,
    ) -> None:
        if not api_key:
            raise ProviderError("THE_ODDS_API_KEY is not configured")
        self._api_key, self.region, self.max_credits = api_key, region, max_credits
        self.snapshots, self.transport = snapshots, transport
        self.reserved_credits = 0
        self.calls = 0
        self.usage: list[dict] = []
        self.halted = False

    def reserve_check(self, cost: int) -> None:
        if self.halted:
            raise ProviderError("odds feed halted after unexpected provider cost")
        if (
            self.usage
            and self.usage[-1]["remaining"] is not None
            and self.usage[-1]["remaining"] < cost
        ):
            raise ProviderError("The Odds API remaining quota is insufficient")
        if self.reserved_credits + cost > self.max_credits:
            raise ProviderError(
                f"odds credit budget exceeded: need {cost} more, {self.max_credits - self.reserved_credits} available"
            )

    def bulk(self, sport: str, target_date: date) -> list[dict]:
        start = datetime.combine(target_date, time.min, ZoneInfo("Europe/Berlin"))
        end = datetime.combine(
            target_date + timedelta(days=1), time.min, ZoneInfo("Europe/Berlin")
        ) - timedelta(seconds=1)
        payload = self._request(
            sport,
            "odds",
            ["h2h", "totals"],
            {
                "commenceTimeFrom": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "commenceTimeTo": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            },
        )
        if not isinstance(payload, list):
            raise ProviderError("bulk odds response must be a list")
        return payload

    def extra(self, sport: str, event_id: str, markets: list[str]) -> dict:
        payload = self._request(
            sport, f"events/{urllib.parse.quote(event_id, safe='')}/odds", markets, {}
        )
        if not isinstance(payload, dict):
            raise ProviderError("event odds response must be an object")
        return payload

    def _request(self, sport: str, endpoint: str, markets: list[str], params: dict) -> Any:
        cost = len(set(markets))  # One explicitly selected region; conservative reservation.
        self.reserve_check(cost)
        self.reserved_credits += cost
        self.calls += 1
        public_url = f"{self.ROOT}/{urllib.parse.quote(sport, safe='')}/{endpoint}"
        query = urllib.parse.urlencode(
            {
                "apiKey": self._api_key,
                "regions": self.region,
                "markets": ",".join(sorted(set(markets))),
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                **params,
            }
        )
        try:
            payload, headers, raw = self.transport(
                f"{public_url}?{query}", {"Accept": "application/json"}
            )
        except ProviderError as error:
            # Also sanitize custom transports, whose exceptions may contain the full URL.
            message = str(error).replace(self._api_key, "[REDACTED]")
            message = message.replace(urllib.parse.quote_plus(self._api_key), "[REDACTED]")
            raise ProviderError(message) from None
        except (OSError, ValueError):
            raise ProviderError("The Odds API request failed") from None
        if isinstance(payload, dict) and (payload.get("error_code") or payload.get("message")):
            raise ProviderError("The Odds API returned an error object")
        if self.snapshots is not None:
            self.snapshots.save(
                provider="the_odds_api",
                resource_type="daily223_odds",
                content=raw,
                media_type="application/json",
                source_url=public_url,
            )
        normalized = {key.lower(): str(value) for key, value in headers.items()}
        usage = {
            name: int(normalized[key]) if normalized.get(key, "").isdigit() else None
            for name, key in (
                ("remaining", "x-requests-remaining"),
                ("used", "x-requests-used"),
                ("last_cost", "x-requests-last"),
            )
        }
        self.usage.append(usage)
        if usage["last_cost"] is not None and usage["last_cost"] > cost:
            self.halted = True
            raise ProviderError("provider odds cost exceeded the reserved amount; no further calls")
        if usage["last_cost"] is not None:
            # Empty responses can cost zero. Release only provider-confirmed
            # unused credits; failures and absent headers remain conservative.
            self.reserved_credits -= cost - usage["last_cost"]
        return payload


class UnavailableOddsFeed:
    """An explicit missing credential; fixture collection remains usable."""

    def __init__(self, region, max_credits):
        self.region, self.max_credits = region, max_credits
        self.calls = self.reserved_credits = 0
        self.usage = []

    def reserve_check(self, cost):
        raise ProviderError("THE_ODDS_API_KEY nincs beállítva; szorzó nélküli előzetes készülhet.")
