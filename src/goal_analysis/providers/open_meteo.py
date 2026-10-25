"""Kickoff forecasts from Open-Meteo, using the fixture's actual venue city."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import UTC, datetime, timedelta

_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"
_HOURLY = ("temperature_2m", "precipitation_probability", "precipitation", "weather_code", "wind_speed_10m", "wind_gusts_10m")
_UNKNOWN = "A mérkőzés helyszíne nem azonosítható egyértelműen; nincs megbízható időjárás-előrejelzés."


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Arthur-GoalAnalysis/3.7 (weather forecast)"})
    with urllib.request.urlopen(request, timeout=8) as response:
        raw = response.read(500_001)
    if len(raw) > 500_000:
        raise ValueError("Open-Meteo response too large")
    result = json.loads(raw)
    if not isinstance(result, dict) or result.get("error"):
        raise ValueError("Open-Meteo response invalid")
    return result


def _fold(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))


def _number(value, low: float, high: float):
    return value if type(value) in (int, float) and math.isfinite(value) and low <= value <= high else None


def unknown(fixture: dict, reason: str = _UNKNOWN) -> dict:
    return {"fixture_id": fixture["fixture_id"], "status": "UNKNOWN", "provider": "open_meteo", "venue_name": fixture.get("venue_name"), "venue_city": fixture.get("venue_city"), "summary": reason, "source_url": None}


class OpenMeteoForecast:
    def __init__(self, transport=_get_json, *, clock=None, recorder=None, isolate_request_failures=False):
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(UTC))
        self.recorder = recorder
        self._locations = {}
        self._forecasts = {}
        self._unavailable_hosts = set()
        self._failed_requests = set()
        self.isolate_request_failures = isolate_request_failures

    def _observed_at(self):
        observed = self.clock()
        if not isinstance(observed, datetime) or observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("weather clock must return a timezone-aware datetime")
        return observed.astimezone(UTC).isoformat()

    def _finish(self, fixture, weather, *, data=None, observed_at=None, source_url=None):
        """Record full payload separately; cached forecasts retain fetch provenance."""
        weather = dict(weather)
        weather.update(weather_kind="FORECAST", provider_issue_time=None,
                       observed_at=observed_at or self._observed_at(), kickoff=fixture.get("kickoff"),
                       time_basis="FETCHED_AT" if data is not None else "LOOKUP_ATTEMPT_AT")
        raw_hash = None
        if data is not None:
            canonical = json.dumps(data, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":"), allow_nan=False).encode("utf-8")
            raw_hash = hashlib.sha256(canonical).hexdigest()
            weather["raw_payload_hash"] = raw_hash
        if self.recorder is not None:
            self.recorder(deepcopy(fixture), deepcopy(weather), {
                "raw_payload": deepcopy(data), "raw_payload_hash": raw_hash,
                "source_url": source_url or weather.get("source_url"),
                "observed_at": weather["observed_at"],
            })
        return weather

    def _fetch(self, url: str) -> dict:
        host = urllib.parse.urlsplit(url).netloc
        if host in self._unavailable_hosts or url in self._failed_requests:
            raise ValueError("Open-Meteo endpoint unavailable for this run")
        try:
            return self.transport(url)
        except urllib.error.HTTPError as error:
            self._failed_requests.add(url)
            # Access/quota denial applies to the host. A single timeout or bad
            # location response must not suppress all other eligible fixtures.
            if not self.isolate_request_failures or error.code in {401, 403, 429}:
                self._unavailable_hosts.add(host)
            raise
        except (OSError, ValueError, TimeoutError):
            self._failed_requests.add(url)
            if not self.isolate_request_failures:
                self._unavailable_hosts.add(host)
            raise

    def forecast(self, fixture: dict, *, coordinates=None) -> dict:
        city = fixture.get("venue_city")
        if not isinstance(city, str) or len(city.strip()) < 2:
            return self._finish(fixture, unknown(fixture))
        city = city.strip()
        country = fixture.get("venue_country")
        if not isinstance(country, str) or country in {"World", "Europe", "Africa", "Asia", "International"}:
            country = None
        key = (city.casefold(), country.casefold() if country else None)
        if coordinates is not None:
            if not isinstance(coordinates, tuple) or len(coordinates) != 2:
                raise ValueError('Explicit weather coordinates require a latitude/longitude pair')
            place = {'latitude': coordinates[0], 'longitude': coordinates[1]}
        elif key not in self._locations:
            query = urllib.parse.urlencode({"name": city, "count": 100, "language": "en", "format": "json"})
            places = self._fetch(f"{_GEOCODE}?{query}").get("results", [])
            exact = [p for p in places if isinstance(p, dict) and isinstance(p.get("name"), str) and _fold(p["name"]) == _fold(city)]
            if country:
                expected = "United Kingdom" if country in {"England", "Scotland", "Wales", "Northern Ireland"} else country
                exact = [p for p in exact if _fold(str(p.get("country", ""))) == _fold(expected)]
            self._locations[key] = exact[0] if len(exact) == 1 and len(places) < 100 else None
            place = self._locations[key]
        else:
            place = self._locations[key]
        if not place:
            return self._finish(fixture, unknown(fixture))
        lat = _number(place.get("latitude"), -90, 90)
        lon = _number(place.get("longitude"), -180, 180)
        if lat is None or lon is None:
            return self._finish(fixture, unknown(fixture))
        kickoff = datetime.fromisoformat(fixture["kickoff"]).astimezone(UTC)
        hour = kickoff.replace(minute=0, second=0, microsecond=0)
        if kickoff - hour >= timedelta(minutes=30):
            hour += timedelta(hours=1)
        cache_key = (lat, lon, hour.date().isoformat())
        if cache_key not in self._forecasts:
            query = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "hourly": ",".join((*_HOURLY, 'relative_humidity_2m')), "start_date": hour.date().isoformat(), "end_date": hour.date().isoformat(), "timezone": "UTC"})
            url = f"{_FORECAST}?{query}"
            data = self._fetch(url)
            self._forecasts[cache_key] = (url, data, self._observed_at())
        url, data, observed_at = self._forecasts[cache_key]
        hourly = data.get("hourly", {})
        times = hourly.get("time", []) if isinstance(hourly, dict) else []
        try:
            index = times.index(hour.strftime("%Y-%m-%dT%H:%M"))
            values = {field: hourly[field][index] for field in _HOURLY}
        except (ValueError, KeyError, IndexError, TypeError):
            weather = unknown(fixture, "Az Open-Meteo nem adott órás előrejelzést a kezdés időpontjára.")
            weather.update(source_url=url, latitude=lat, longitude=lon,
                           forecast_time=hour.isoformat())
            return self._finish(fixture, weather, data=data, observed_at=observed_at)
        temperature = _number(values["temperature_2m"], -100, 70)
        rain = _number(values["precipitation_probability"], 0, 100)
        wind = _number(values["wind_speed_10m"], 0, 400)
        if any(value is None for value in (temperature, rain, wind)):
            weather = unknown(fixture, "Hiányos Open-Meteo előrejelzés a kezdés időpontjára.")
            weather.update(source_url=url, latitude=lat, longitude=lon,
                           forecast_time=hour.isoformat())
            return self._finish(fixture, weather, data=data, observed_at=observed_at)
        summary = f"{city}: a kezdéshez közeli órában {temperature:g} °C, csapadék esélye {rain:g}%, szél {wind:g} km/h (előrejelzés)."
        weather = {"fixture_id": fixture["fixture_id"], "status": "SOURCED", "provider": "open_meteo", "venue_name": fixture.get("venue_name"), "venue_city": city, "forecast_time": hour.isoformat(), "temperature_c": temperature, "precipitation_probability_pct": rain, "precipitation_mm": _number(values["precipitation"], 0, 1000), "weather_code": _number(values["weather_code"], 0, 99), "wind_speed_kmh": wind, "wind_gusts_kmh": _number(values["wind_gusts_10m"], 0, 500), "summary": summary, "source_url": url, "latitude": lat, "longitude": lon}
        humidity = hourly.get('relative_humidity_2m', [])
        weather['relative_humidity_pct'] = (_number(humidity[index], 0, 100)
            if isinstance(humidity, list) and index < len(humidity) else None)
        return self._finish(fixture, weather, data=data, observed_at=observed_at)


def weather_context(weather: dict) -> dict:
    return {"fixture_id": weather["fixture_id"], "topic": "weather", "status": weather["status"], "summary": weather["summary"], "source_urls": [weather["source_url"]] if weather.get("source_url") else []}
