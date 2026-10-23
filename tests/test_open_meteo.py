"""Open-Meteo weather is venue-bound, hourly, and never guessed."""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from goal_analysis.providers.open_meteo import OpenMeteoForecast, weather_context


def fixture(city="Berlin", country="Germany"):
    return {"fixture_id": "api_football:123", "venue_name": "Olympiastadion", "venue_city": city, "venue_country": country, "kickoff": "2026-09-25T18:30:00+00:00"}


def test_hourly_forecast_is_from_venue_city_and_nearest_kickoff_hour():
    calls = []

    def transport(url):
        calls.append(url)
        if "geocoding-api" in url:
            return {"results": [{"name": "Berlin", "country": "Germany", "latitude": 52.52, "longitude": 13.40}]}
        return {"hourly": {"time": ["2026-09-25T19:00"], "temperature_2m": [14.2], "precipitation_probability": [60], "precipitation": [0.5], "weather_code": [61], "wind_speed_10m": [18], "wind_gusts_10m": [30], 'relative_humidity_2m': [75]}}

    provider = OpenMeteoForecast(transport)
    result = provider.forecast(fixture())
    assert result["status"] == "SOURCED"
    assert result["forecast_time"] == datetime(2026, 9, 25, 19, tzinfo=UTC).isoformat()
    assert result["precipitation_probability_pct"] == 60
    assert result['relative_humidity_pct'] == 75
    assert 'relative_humidity_2m' in parse_qs(urlparse(calls[1]).query)['hourly'][0]
    assert parse_qs(urlparse(calls[0]).query)["name"] == ["Berlin"]
    assert parse_qs(urlparse(calls[1]).query)["timezone"] == ["UTC"]
    assert weather_context(result)["source_urls"] == [calls[1]]
    provider.forecast(fixture())
    assert len(calls) == 2  # one location and forecast fetch per shared place/day


def test_missing_or_ambiguous_venue_does_not_invent_weather():
    provider = OpenMeteoForecast(lambda url: {"results": [
        {"name": "Springfield", "country": "United States", "latitude": 1, "longitude": 2},
        {"name": "Springfield", "country": "United States", "latitude": 3, "longitude": 4},
    ]})
    assert provider.forecast(fixture(None))["status"] == "UNKNOWN"
    assert provider.forecast(fixture("Springfield", None))["status"] == "UNKNOWN"


def test_wrong_country_is_not_used_for_an_international_fixture():
    provider = OpenMeteoForecast(lambda url: {"results": [{"name": "Paris", "country": "United States", "latitude": 33, "longitude": -95}]})
    assert provider.forecast(fixture("Paris", "France"))["status"] == "UNKNOWN"


def test_geocoder_failure_does_not_discard_a_verified_coordinate_forecast():
    calls = []
    def transport(url):
        calls.append(url)
        if 'geocoding-api' in url:
            raise OSError('separate geocoding host unavailable')
        return {'hourly': {'time': ['2026-09-25T19:00'], 'temperature_2m': [14.2],
            'precipitation_probability': [60], 'precipitation': [.5], 'weather_code': [61],
            'wind_speed_10m': [18], 'wind_gusts_10m': [30]}}
    provider = OpenMeteoForecast(transport)
    with pytest.raises(OSError): provider.forecast(fixture())
    assert provider.forecast(fixture(), coordinates=(52.52, 13.4))['status'] == 'SOURCED'
    with pytest.raises(ValueError): provider.forecast(fixture('Another city'))
    assert len(calls) == 2


def test_forecast_host_failure_stops_further_calls_to_that_host():
    calls = []
    def transport(url):
        calls.append(url)
        raise OSError('forecast host unavailable')
    provider = OpenMeteoForecast(transport)
    with pytest.raises(OSError): provider.forecast(fixture(), coordinates=(52.52, 13.4))
    with pytest.raises(ValueError): provider.forecast(fixture(), coordinates=(48.7, 11.4))
    assert len(calls) == 1


def test_forecast_recorder_keeps_actual_fetch_time_when_cached_and_full_raw_values():
    calls, records = [], []
    original = {"hourly": {"time": ["2026-09-25T19:00"], "temperature_2m": [14.2],
                           "precipitation_probability": [60], "precipitation": [0.5],
                           "weather_code": [61], "wind_speed_10m": [18], "wind_gusts_10m": [30]},
                "unknown_provider_field": [1, None, "keep"]}

    def transport(url):
        calls.append(url)
        if "geocoding-api" in url:
            return {"results": [{"name": "Berlin", "country": "Germany",
                                 "latitude": 52.52, "longitude": 13.4}]}
        return original

    clock_calls = []

    def clock():
        clock_calls.append(True)
        return datetime(2026, 9, 25, 10 + len(clock_calls), tzinfo=UTC)

    provider = OpenMeteoForecast(transport, clock=clock,
                                recorder=lambda *args: records.append(args))
    first = provider.forecast(fixture())
    second = provider.forecast(fixture())
    assert first == second
    assert len(clock_calls) == 1
    assert len(calls) == 2
    assert first["observed_at"] == "2026-09-25T11:00:00+00:00"
    assert first["weather_kind"] == "FORECAST"
    assert first["provider_issue_time"] is None
    assert first["latitude"] == 52.52
    assert first["longitude"] == 13.4
    assert records[0][2]["raw_payload"] == original
    assert records[0][2]["raw_payload_hash"] == first["raw_payload_hash"]
    records[0][2]["raw_payload"]["hourly"]["temperature_2m"][0] = 999
    records[0][1]["temperature_c"] = 999
    assert provider.forecast(fixture())["temperature_c"] == 14.2
    assert "raw_payload" not in first


def test_unusable_download_still_records_full_raw_forecast_provenance():
    records = []

    def transport(url):
        if "geocoding-api" in url:
            return {"results": [{"name": "Berlin", "country": "Germany",
                                 "latitude": 52.52, "longitude": 13.4}]}
        return {"hourly": {"time": []}, "diagnostic": "no hourly data"}

    provider = OpenMeteoForecast(transport, clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
                                recorder=lambda *args: records.append(args))
    result = provider.forecast(fixture())
    assert result["status"] == "UNKNOWN"
    assert result["provider_issue_time"] is None
    assert records[0][2]["raw_payload"]["diagnostic"] == "no hourly data"
    assert result["source_url"].startswith("https://api.open-meteo.com/")
    assert result["forecast_time"] == "2026-09-25T19:00:00+00:00"


def test_unknown_location_records_no_invented_weather_payload():
    records = []
    provider = OpenMeteoForecast(lambda url: {},
                                recorder=lambda *args: records.append(args))
    result = provider.forecast(fixture(None))
    assert result["status"] == "UNKNOWN"
    assert records[0][2]["raw_payload"] is None
    assert records[0][2]["raw_payload_hash"] is None


def test_recorder_failure_is_not_silently_ignored():
    def recorder(*args):
        raise OSError("archive unavailable")

    with pytest.raises(OSError, match="archive unavailable"):
        OpenMeteoForecast(lambda url: {}, recorder=recorder).forecast(fixture(None))
