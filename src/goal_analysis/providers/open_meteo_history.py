"""Bounded ERA5 reanalysis downloads; never pretend these were pre-match forecasts.

The caller supplies verified venue coordinates and their provenance. No geocoding
or timezone guessing happens here. Cached responses retain their actual receipt
time, including when reused by another fixture at the same location and UTC day.
"""

from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from goal_analysis.storage.cache import CacheStore
from goal_analysis.storage.db import Database
from goal_analysis.storage.snapshots import SnapshotStore

ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
NAMESPACE = "open_meteo_era5_history_v1"
MODEL = "era5"
MAX_RESPONSE_BYTES = 500_000
HOURLY = (
    "temperature_2m", "relative_humidity_2m", "precipitation", "weather_code",
    "wind_speed_10m",
)
_FIELDS = {
    "temperature_2m": ("temperature_c", -100, 70, "°C"),
    "relative_humidity_2m": ("relative_humidity_pct", 0, 100, "%"),
    "precipitation": ("precipitation_mm", 0, 1000, "mm"),
    "weather_code": ("weather_code", 0, 99, "wmo code"),
    "wind_speed_10m": ("wind_speed_kmh", 0, 400, "km/h"),
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _get_json(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Arthur-GoalAnalysis/3.7 (historical weather)"}
    )
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=20) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Open-Meteo response too large")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError("Open-Meteo response invalid")
    return payload


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("weather time must be timezone-aware")
    return value.astimezone(UTC)


def _number(value, low, high):
    return value if type(value) in (int, float) and math.isfinite(value) and low <= value <= high else None


class OpenMeteoHistory:
    """One UTC day per request, up to ten requests per instance by default.

    HTTP failures stop subsequent downloads for this instance. Existing cached
    days remain usable. A 30-day TTL allows upstream revisions to be collected;
    old payloads remain in the content-addressed raw snapshot store.
    """

    def __init__(self, transport=_get_json, *, cache: CacheStore, snapshots: SnapshotStore,
                 clock=None, recorder=None, max_requests: int = 10,
                 cache_ttl: timedelta = timedelta(days=30)):
        if type(max_requests) is not int or not 0 <= max_requests <= 100:
            raise ValueError("max_requests must be an integer between 0 and 100")
        if not isinstance(cache_ttl, timedelta) or cache_ttl.total_seconds() <= 0:
            raise ValueError("cache_ttl must be positive")
        self.transport, self.cache, self.snapshots = transport, cache, snapshots
        self.clock = clock or (lambda: datetime.now(UTC))
        self.recorder = recorder
        self.max_requests, self.cache_ttl = max_requests, cache_ttl
        self.requests_used = 0
        self.stopped_reason = None

    @classmethod
    def from_root(cls, root: Path, **kwargs):
        database = Database(Path(root) / "data" / "goal_analysis.sqlite3")
        database.initialize()
        return cls(cache=CacheStore(database),
                   snapshots=SnapshotStore(database, Path(root) / "data" / "raw"), **kwargs)

    def _finish(self, fixture, weather, payload=None):
        weather = deepcopy(weather)
        if payload is not None:
            weather["raw_payload_hash"] = hashlib.sha256(_canonical(payload)).hexdigest()
        if self.recorder is not None:
            self.recorder(deepcopy(fixture), deepcopy(weather), {
                "raw_payload": deepcopy(payload),
                "raw_payload_hash": weather.get("raw_payload_hash"),
                "source_url": weather.get("source_url"),
                "observed_at": weather["observed_at"],
            })
        return weather

    def _cached(self, url, now):
        entry = self.cache.get(NAMESPACE, url, now=now)
        if not isinstance(entry, dict):
            return None
        try:
            observed = _utc(datetime.fromisoformat(entry["observed_at"]))
            payload = entry["payload"]
            raw = _canonical(payload)
            if (not isinstance(payload, dict) or payload.get("error") or observed > now
                    or entry["source_url"] != url
                    or hashlib.sha256(raw).hexdigest() != entry["raw_payload_hash"]
                    or self.snapshots.read(entry["raw_payload_hash"]) != raw):
                return None
            return payload, observed
        except (ValueError, TypeError, KeyError, OSError):
            return None

    def historical(self, fixture: dict, *, latitude, longitude, coordinate_source: str,
                   coordinate_precision: str = "UNSPECIFIED") -> dict:
        """Coordinates must be verified by the caller, with a nonempty provenance.

        Unknown/missing values are explicit. A reanalysis is only eligible six
        calendar days after its target UTC date, allowing ERA5's five-day lag.
        """
        now = _utc(self.clock())
        if not fixture.get("fixture_id"):
            raise ValueError("fixture_id required")
        weather = {
            "fixture_id": fixture["fixture_id"], "status": "UNKNOWN",
            "provider": "open_meteo", "weather_kind": "REANALYSIS", "model": MODEL,
            "provider_issue_time": None, "observed_at": now.isoformat(),
            "time_basis": "LOOKUP_ATTEMPT_AT", "kickoff": fixture.get("kickoff"),
            "venue_name": fixture.get("venue_name"), "venue_city": fixture.get("venue_city"),
            "source_url": None, "summary": "Nem érhető el történeti időjárásadat.",
            "wind_gusts_kmh": None, "unsupported_fields": ["wind_gusts_10m"],
        }
        lat, lon = _number(latitude, -90, 90), _number(longitude, -180, 180)
        if (lat is None or lon is None or not isinstance(coordinate_source, str)
                or not coordinate_source.strip() or len(coordinate_source) > 1000):
            weather["reason_code"] = "UNVERIFIED_COORDINATES"
            return self._finish(fixture, weather)
        weather.update(latitude=lat, longitude=lon, coordinate_source=coordinate_source.strip(),
                       coordinate_precision=coordinate_precision)
        try:
            value = fixture.get("kickoff")
            kickoff = _utc(value if isinstance(value, datetime) else datetime.fromisoformat(value))
        except (TypeError, ValueError):
            weather["reason_code"] = "INVALID_KICKOFF"
            return self._finish(fixture, weather)
        weather["kickoff"] = kickoff.isoformat()
        hour = kickoff.replace(minute=0, second=0, microsecond=0)
        if kickoff - hour >= timedelta(minutes=30):
            hour += timedelta(hours=1)
        weather["requested_target_time"] = hour.isoformat()
        if hour.date() < date(1940, 1, 1):
            weather["reason_code"] = "BEFORE_ERA5_COVERAGE"
            return self._finish(fixture, weather)
        if hour.date() > now.date() - timedelta(days=6):
            weather["reason_code"] = "ERA5_PUBLICATION_LAG"
            return self._finish(fixture, weather)
        weather.update(forecast_time=hour.isoformat(), target_time=hour.isoformat())
        query = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "start_date": hour.date().isoformat(),
            "end_date": hour.date().isoformat(), "hourly": ",".join(HOURLY),
            "models": MODEL, "timezone": "UTC", "temperature_unit": "celsius",
            "wind_speed_unit": "kmh", "precipitation_unit": "mm",
        })
        url = f"{ENDPOINT}?{query}"
        weather["source_url"] = url
        cached = self._cached(url, now)
        if cached is not None:
            payload, observed = cached
        else:
            if self.stopped_reason or self.requests_used >= self.max_requests:
                weather["reason_code"] = self.stopped_reason or "REQUEST_BUDGET_EXHAUSTED"
                return self._finish(fixture, weather)
            self.requests_used += 1
            try:
                payload = self.transport(url)
                observed = _utc(self.clock())  # actual receipt, not request-start time
                if not isinstance(payload, dict) or payload.get("error"):
                    raise ValueError("invalid weather response")
                raw = _canonical(payload)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("weather response too large")
            except (OSError, ValueError, TypeError, TimeoutError) as exc:
                status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
                self.stopped_reason = f"HTTP_{status}" if status else "PROVIDER_UNAVAILABLE"
                weather["reason_code"] = self.stopped_reason
                return self._finish(fixture, weather)
            snapshot = self.snapshots.save("open_meteo", "era5_hourly", raw,
                                           "application/json", fetched_at=observed, source_url=url)
            self.cache.set(NAMESPACE, url, {
                "payload": payload, "source_url": url, "observed_at": observed.isoformat(),
                "raw_payload_hash": snapshot.content_hash,
            }, ttl=self.cache_ttl, now=observed)
        weather["observed_at"] = observed.isoformat()
        weather["time_basis"] = "FETCHED_AT"
        weather["units"] = deepcopy(payload.get("hourly_units", {}))
        weather["provider_grid_latitude"] = _number(payload.get("latitude"), -90, 90)
        weather["provider_grid_longitude"] = _number(payload.get("longitude"), -180, 180)
        hourly = payload.get("hourly")
        times = hourly.get("time") if isinstance(hourly, dict) else None
        wanted = hour.strftime("%Y-%m-%dT%H:%M")
        if (not isinstance(times, list) or times.count(wanted) != 1
                or payload.get("utc_offset_seconds") != 0):
            weather["reason_code"] = "MISSING_OR_AMBIGUOUS_UTC_HOUR"
            return self._finish(fixture, weather, payload)
        index = times.index(wanted)
        units = payload.get("hourly_units")
        missing = []
        for field, (name, low, high, unit) in _FIELDS.items():
            values = hourly.get(field)
            value = values[index] if isinstance(values, list) and index < len(values) else None
            valid_unit = isinstance(units, dict) and units.get(field) == unit
            number = _number(value, low, high) if valid_unit else None
            if field == "weather_code" and number is not None and number != int(number):
                number = None
            weather[name] = number
            if number is None:
                missing.append(field)
        weather["missing_fields"] = missing
        if missing:
            weather["reason_code"] = "INCOMPLETE_HOURLY_DATA"
        else:
            weather.update(status="SOURCED", reason_code=None,
                           summary="ERA5 történeti időjárás-reanalízis a kezdéshez közeli órára.")
        return self._finish(fixture, weather, payload)
