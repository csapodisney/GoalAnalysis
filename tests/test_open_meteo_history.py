"""Historical weather remains retrospective, bounded and provenance-preserving."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

from goal_analysis.providers.open_meteo_history import NAMESPACE, OpenMeteoHistory
from goal_analysis.storage.cache import CacheStore
from goal_analysis.storage.db import Database
from goal_analysis.storage.snapshots import SnapshotStore

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)
FIXTURE = {"fixture_id": "api_football:123", "venue_name": "Olympiastadion",
           "venue_city": "Berlin", "kickoff": "2026-09-25T20:30:00+02:00"}
COORDS = {"latitude": 52.5147, "longitude": 13.2395,
          "coordinate_source": "verified stadium registry:Olympiastadion"}
PAYLOAD = {
    "latitude": 52.5, "longitude": 13.25, "utc_offset_seconds": 0,
    "hourly": {"time": ["2026-09-25T19:00"], "temperature_2m": [14.2],
               "relative_humidity_2m": [70], "precipitation": [0.5], "weather_code": [61],
               "wind_speed_10m": [18], "wind_gusts_10m": [30]},
    "hourly_units": {"time": "iso8601", "temperature_2m": "°C",
                     "relative_humidity_2m": "%", "precipitation": "mm",
                     "weather_code": "wmo code", "wind_speed_10m": "km/h",
                     "wind_gusts_10m": "km/h"},
    "unfamiliar_provider_field": {"retain": [1, None, "full source"]},
}


def provider(tmp_path, transport=None, **kwargs):
    database = Database(tmp_path / "weather.sqlite3")
    database.initialize()
    return OpenMeteoHistory(transport or (lambda url: deepcopy(PAYLOAD)),
                           cache=CacheStore(database), snapshots=SnapshotStore(database, tmp_path / "raw"),
                           clock=kwargs.pop("clock", lambda: NOW), **kwargs)


def test_era5_request_and_record_are_explicit_reanalysis(tmp_path):
    calls, records = [], []

    def transport(url):
        calls.append(url)
        return deepcopy(PAYLOAD)

    times = iter([NOW, NOW + timedelta(seconds=2)])
    client = provider(tmp_path, transport, clock=lambda: next(times),
                      recorder=lambda *args: records.append(args))
    result = client.historical(FIXTURE, **COORDS)
    assert client.requests_used == 1
    assert result["status"] == "SOURCED"
    assert result["weather_kind"] == "REANALYSIS"
    assert result["time_basis"] == "FETCHED_AT"
    assert result["observed_at"] == (NOW + timedelta(seconds=2)).isoformat()
    assert result["provider_issue_time"] is None
    assert result["model"] == "era5"
    assert result["target_time"] == "2026-09-25T19:00:00+00:00"
    assert result["temperature_c"] == 14.2
    assert result["precipitation_mm"] == 0.5
    assert "precipitation_probability_pct" not in result
    assert result["latitude"] == COORDS["latitude"]
    assert result["provider_grid_latitude"] == 52.5
    query = parse_qs(urlparse(calls[0]).query)
    assert urlparse(calls[0]).hostname == "archive-api.open-meteo.com"
    assert query["models"] == ["era5"]
    assert query["timezone"] == ["UTC"]
    assert query["start_date"] == query["end_date"] == ["2026-09-25"]
    assert len(query["hourly"][0].split(",")) == 5
    assert "wind_gusts_10m" not in query["hourly"][0]
    assert result["wind_gusts_kmh"] is None
    assert result["unsupported_fields"] == ["wind_gusts_10m"]
    assert "apikey" not in query
    assert records[0][2]["raw_payload"] == PAYLOAD
    assert "raw_payload" not in result
    raw = client.snapshots.read(result["raw_payload_hash"])
    assert json.loads(raw) == PAYLOAD
    assert hashlib.sha256(raw).hexdigest() == result["raw_payload_hash"]


def test_persistent_cache_reused_by_new_instance_and_new_fixture_without_fresh_timestamp(tmp_path):
    first = provider(tmp_path).historical(FIXTURE, **COORDS)

    def unexpected(url):
        pytest.fail("cache hit must not download")

    client = provider(tmp_path, unexpected, clock=lambda: NOW + timedelta(days=1))
    second = client.historical(dict(FIXTURE, fixture_id="api_football:456"), **COORDS)
    assert first["observed_at"] == second["observed_at"] == NOW.isoformat()
    assert second["fixture_id"] == "api_football:456"
    assert first["raw_payload_hash"] == second["raw_payload_hash"]
    assert client.requests_used == 0


def test_cache_expiry_refetches_revision_without_deleting_old_payload(tmp_path):
    first = provider(tmp_path).historical(FIXTURE, **COORDS)
    revised = deepcopy(PAYLOAD)
    revised["hourly"]["temperature_2m"][0] = 15.7
    client = provider(tmp_path, lambda url: revised, clock=lambda: NOW + timedelta(days=31))
    second = client.historical(FIXTURE, **COORDS)
    assert client.requests_used == 1
    assert second["raw_payload_hash"] != first["raw_payload_hash"]
    assert json.loads(client.snapshots.read(first["raw_payload_hash"])) == PAYLOAD
    assert second["observed_at"] != first["observed_at"]


@pytest.mark.parametrize("kickoff,reason", [
    ("2026-10-13T12:00:00Z", "ERA5_PUBLICATION_LAG"),
    ("2026-10-18T12:00:00Z", "ERA5_PUBLICATION_LAG"),
    ("2026-11-01T12:00:00Z", "ERA5_PUBLICATION_LAG"),
    ("1939-12-31T12:00:00Z", "BEFORE_ERA5_COVERAGE"),
    ("2026-09-25T19:00:00", "INVALID_KICKOFF"),
    (None, "INVALID_KICKOFF"),
    ("not a date", "INVALID_KICKOFF"),
])
def test_unsupported_dates_never_consume_network(tmp_path, kickoff, reason):
    client = provider(tmp_path, lambda url: pytest.fail("must not fetch"))
    result = client.historical(dict(FIXTURE, kickoff=kickoff), **COORDS)
    assert result["status"] == "UNKNOWN"
    assert result["reason_code"] == reason
    assert result["time_basis"] == "LOOKUP_ATTEMPT_AT"
    assert client.requests_used == 0


def test_six_day_boundary_and_utc_rounding_drive_requested_day(tmp_path):
    urls = []
    client = provider(tmp_path, lambda url: urls.append(url) or deepcopy(PAYLOAD))
    # Berlin date is Oct 13; actual rounded UTC hour is Oct 12, so six-day cutoff passes.
    client.historical(dict(FIXTURE, kickoff="2026-10-13T00:20:00+02:00"), **COORDS)
    assert parse_qs(urlparse(urls[0]).query)["start_date"] == ["2026-10-12"]
    blocked = client.historical(dict(FIXTURE, kickoff="2026-10-12T23:30:00Z"), **COORDS)
    assert blocked["reason_code"] == "ERA5_PUBLICATION_LAG"
    assert len(urls) == 1


@pytest.mark.parametrize("override", [
    {"latitude": 91}, {"latitude": float("nan")}, {"longitude": -181},
    {"longitude": True}, {"latitude": "52.5"}, {"coordinate_source": ""},
    {"coordinate_source": None},
])
def test_invalid_or_unverified_coordinates_do_not_trigger_geocoding(tmp_path, override):
    client = provider(tmp_path, lambda url: pytest.fail("must not fetch"))
    result = client.historical(FIXTURE, **(COORDS | override))
    assert result["reason_code"] == "UNVERIFIED_COORDINATES"
    assert client.requests_used == 0


@pytest.mark.parametrize("error", [HTTPError("https://example.invalid", 403, "blocked", {}, None),
                                  HTTPError("https://example.invalid", 429, "rate", {}, None),
                                  TimeoutError("slow")])
def test_provider_failure_stops_remaining_downloads_but_cache_still_works(tmp_path, error):
    provider(tmp_path).historical(FIXTURE, **COORDS)
    calls = []

    def failure(url):
        calls.append(url)
        raise error

    client = provider(tmp_path, failure)
    first = client.historical(FIXTURE, **(COORDS | {"latitude": 51}))
    second = client.historical(FIXTURE, **(COORDS | {"latitude": 50}))
    cached = client.historical(FIXTURE, **COORDS)
    assert first["reason_code"] == second["reason_code"] == client.stopped_reason
    assert cached["status"] == "SOURCED"
    assert len(calls) == client.requests_used == 1


def test_request_budget_applies_to_downloads_only(tmp_path):
    client = provider(tmp_path, max_requests=1)
    first = client.historical(FIXTURE, **COORDS)
    cached = client.historical(FIXTURE, **COORDS)
    blocked = client.historical(FIXTURE, **(COORDS | {"longitude": 14}))
    assert first == cached
    assert blocked["reason_code"] == "REQUEST_BUDGET_EXHAUSTED"
    assert client.requests_used == 1


@pytest.mark.parametrize("mutation,reason", [
    (lambda data: data["hourly"].update(time=[]), "MISSING_OR_AMBIGUOUS_UTC_HOUR"),
    (lambda data: data["hourly"]["time"].append("2026-09-25T19:00"), "MISSING_OR_AMBIGUOUS_UTC_HOUR"),
    (lambda data: data.update(utc_offset_seconds=3600), "MISSING_OR_AMBIGUOUS_UTC_HOUR"),
    (lambda data: data["hourly"].update(temperature_2m=[None]), "INCOMPLETE_HOURLY_DATA"),
    (lambda data: data["hourly_units"].update(wind_speed_10m="m/s"), "INCOMPLETE_HOURLY_DATA"),
    (lambda data: data["hourly"].update(weather_code=[61.5]), "INCOMPLETE_HOURLY_DATA"),
    (lambda data: data["hourly"].update(precipitation=[True]), "INCOMPLETE_HOURLY_DATA"),
])
def test_missing_or_invalid_readings_remain_explicit_and_full_payload_retained(tmp_path, mutation, reason):
    payload = deepcopy(PAYLOAD)
    mutation(payload)
    records = []
    client = provider(tmp_path, lambda url: payload, recorder=lambda *args: records.append(args))
    result = client.historical(FIXTURE, **COORDS)
    assert result["status"] == "UNKNOWN"
    assert result["reason_code"] == reason
    assert records[0][2]["raw_payload"] == payload
    assert json.loads(client.snapshots.read(result["raw_payload_hash"])) == payload
    assert client.historical(FIXTURE, **COORDS) == result
    assert client.requests_used == 1


def test_future_timestamp_in_cache_cannot_be_laundered(tmp_path):
    client = provider(tmp_path)
    result = client.historical(FIXTURE, **COORDS)
    key = result["source_url"]
    entry = client.cache.get(NAMESPACE, key, now=NOW)
    entry["observed_at"] = (NOW + timedelta(days=1)).isoformat()
    client.cache.set(NAMESPACE, key, entry, timedelta(days=30), now=NOW)
    new = provider(tmp_path)
    assert new.historical(FIXTURE, **COORDS)["observed_at"] == NOW.isoformat()
    assert new.requests_used == 1


def test_corrupt_cache_hash_forces_fresh_download(tmp_path):
    client = provider(tmp_path)
    result = client.historical(FIXTURE, **COORDS)
    key = result["source_url"]
    entry = client.cache.get(NAMESPACE, key, now=NOW)
    entry["payload"]["hourly"]["temperature_2m"][0] = 999
    client.cache.set(NAMESPACE, key, entry, timedelta(days=30), now=NOW)
    assert provider(tmp_path).historical(FIXTURE, **COORDS)["temperature_c"] == 14.2


def test_recorder_mutation_cannot_change_returned_weather_or_cached_payload(tmp_path):
    def recorder(fixture, weather, context):
        fixture["fixture_id"] = "changed"
        weather["temperature_c"] = 999
        context["raw_payload"]["hourly"]["temperature_2m"][0] = 999

    client = provider(tmp_path, recorder=recorder)
    assert client.historical(FIXTURE, **COORDS)["temperature_c"] == 14.2
    assert client.historical(FIXTURE, **COORDS)["temperature_c"] == 14.2
    assert FIXTURE["fixture_id"] == "api_football:123"


def test_recorder_failure_is_visible_and_download_is_still_safely_cached(tmp_path):
    def recorder(*args):
        raise OSError("storage unavailable")

    client = provider(tmp_path, recorder=recorder)
    with pytest.raises(OSError, match="storage unavailable"):
        client.historical(FIXTURE, **COORDS)
    next_client = provider(tmp_path, lambda url: pytest.fail("must use saved raw data"))
    assert next_client.historical(FIXTURE, **COORDS)["status"] == "SOURCED"


@pytest.mark.parametrize("budget", [-1, 101, 1.5, True])
def test_invalid_request_budget_rejected(tmp_path, budget):
    with pytest.raises(ValueError, match="max_requests"):
        provider(tmp_path, max_requests=budget)


def test_zero_budget_is_cache_only(tmp_path):
    client = provider(tmp_path, max_requests=0)
    assert client.historical(FIXTURE, **COORDS)["reason_code"] == "REQUEST_BUDGET_EXHAUSTED"


def test_factory_creates_only_requested_database_and_raw_directory(tmp_path):
    client = OpenMeteoHistory.from_root(tmp_path, transport=lambda url: deepcopy(PAYLOAD),
                                       clock=lambda: NOW)
    assert client.historical(FIXTURE, **COORDS)["status"] == "SOURCED"
    assert (tmp_path / "data" / "goal_analysis.sqlite3").exists()
    assert len(list((tmp_path / "data" / "raw" / "open_meteo" / "era5_hourly").glob("*.bin"))) == 1


def test_store_accepts_reanalysis_without_making_it_available_before_receipt(tmp_path):
    from goal_analysis.storage.sports_weather import WeatherStore

    database = Database(tmp_path / "archive.sqlite3")
    store = WeatherStore(database)
    store.initialize()

    def recorder(fixture, weather, context):
        store.ingest(weather, fixture, source_run_id="historical-test",
                     raw_payload=context["raw_payload"])

    client = provider(tmp_path, recorder=recorder)
    result = client.historical(FIXTURE, **COORDS)
    assert not store.as_of(datetime(2026, 9, 25, 18, tzinfo=UTC),
                           fixture_id=FIXTURE["fixture_id"], weather_kind="REANALYSIS")
    rows = store.as_of(NOW, fixture_id=FIXTURE["fixture_id"], weather_kind="REANALYSIS")
    assert len(rows) == 1
    assert rows[0]["raw_payload_hash"] == result["raw_payload_hash"]


def test_unknown_future_lookup_can_be_stored_without_fabricated_target_data(tmp_path):
    from goal_analysis.storage.sports_weather import WeatherStore

    store = WeatherStore(Database(tmp_path / "archive.sqlite3"))
    store.initialize()

    def recorder(fixture, weather, context):
        store.ingest(weather, fixture, source_run_id="future-lookup",
                     raw_payload=context["raw_payload"])

    fixture = dict(FIXTURE, kickoff="2026-11-01T12:00:00Z")
    result = provider(tmp_path, recorder=recorder).historical(fixture, **COORDS)
    assert result["status"] == "UNKNOWN"
    assert result["requested_target_time"] == "2026-11-01T12:00:00+00:00"
    assert result.get("forecast_time") is None
    saved = store.as_of(NOW, weather_kind="REANALYSIS")
    assert len(saved) == 1
    assert saved[0]["target_time"] is None
    assert saved[0]["time_basis"] == "LOOKUP_ATTEMPT_AT"


def test_unsupported_era5_gusts_do_not_make_valid_core_data_unknown(tmp_path):
    payload = deepcopy(PAYLOAD)
    payload["hourly"].pop("wind_gusts_10m")
    payload["hourly_units"].pop("wind_gusts_10m")
    result = provider(tmp_path, lambda url: payload).historical(FIXTURE, **COORDS)
    assert result["status"] == "SOURCED"
    assert result["missing_fields"] == []
    assert result["wind_gusts_kmh"] is None
