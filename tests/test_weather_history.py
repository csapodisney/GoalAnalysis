"""Historical weather batches reuse locations, stay bounded and resume safely."""

import json
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

from goal_analysis.jobs import weather_history
from goal_analysis.providers.open_meteo_history import OpenMeteoHistory
from goal_analysis.storage.db import Database
from goal_analysis.storage.sports_weather import WeatherStore

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)


def store_at(root):
    database = Database(root / "data" / "goal_analysis.sqlite3")
    database.initialize()
    store = WeatherStore(database)
    store.initialize()
    return store


def seed(root, fixture_id="api_football:1", day=10, latitude=52.5, longitude=13.2,
         observed_at="2026-10-08T12:00:00Z", kind="FORECAST", model=None):
    kickoff = f"2026-10-{day:02d}T18:20:00+00:00"
    fixture = {"fixture_id": fixture_id, "kickoff": kickoff, "venue_city": "Berlin",
               "venue_name": "Example stadium"}
    weather = {"fixture_id": fixture_id, "kickoff": kickoff, "provider": "open_meteo",
               "status": "SOURCED", "weather_kind": kind,
               "observed_at": observed_at, "provider_issue_time": None,
               "forecast_time": f"2026-10-{day:02d}T18:00:00+00:00",
               "latitude": latitude, "longitude": longitude,
               "temperature_c": 14.2, "source_url": "https://api.open-meteo.com/v1/forecast?source=known"}
    if model:
        weather["model"] = model
    saved = store_at(root).ingest(weather, fixture, source_run_id="seed")
    return fixture, saved


def fake_payload(url):
    query = parse_qs(urlparse(url).query)
    day = query["start_date"][0]
    return {"utc_offset_seconds": 0,
            "hourly_units": {"temperature_2m": "°C", "relative_humidity_2m": "%",
                             "precipitation": "mm", "weather_code": "wmo code",
                             "wind_speed_10m": "km/h"},
            "hourly": {"time": [f"{day}T{hour:02d}:00" for hour in range(24)],
                       "temperature_2m": [12.3] * 24, "relative_humidity_2m": [66] * 24,
                       "precipitation": [0] * 24, "weather_code": [1] * 24,
                       "wind_speed_10m": [20] * 24},
            "latitude": float(query["latitude"][0]), "longitude": float(query["longitude"][0])}


def inject(monkeypatch, transport):
    original = OpenMeteoHistory.from_root
    monkeypatch.setattr(OpenMeteoHistory, "from_root",
                        lambda root, **kwargs: original(root, transport=transport, **kwargs))


def test_archive_batch_uses_existing_city_coordinates_and_stores_provenance(tmp_path, monkeypatch):
    _, original = seed(tmp_path)
    calls = []
    inject(monkeypatch, lambda url: calls.append(url) or fake_payload(url))
    result = weather_history.backfill_weather(tmp_path, clock=lambda: NOW)
    assert result["requests"] == result["sourced"] == 1
    assert result["unknown"] == result["deferred"] == 0
    assert len(calls) == 1
    assert urlparse(calls[0]).hostname == "archive-api.open-meteo.com"
    saved = store_at(tmp_path).as_of(NOW, weather_kind="REANALYSIS")
    assert len(saved) == 1
    assert saved[0]["weather"]["coordinate_precision"] == "VENUE_CITY"
    assert saved[0]["metadata"]["coordinate_source_snapshot_id"] == original["snapshot_id"]
    assert saved[0]["raw_payload"] == fake_payload(calls[0])
    assert json.loads((tmp_path / "data/arthur/weather-history-import.json").read_text("utf-8")) == result


def test_second_batch_skips_already_sourced_matching_reanalysis(tmp_path, monkeypatch):
    seed(tmp_path)
    calls = []
    inject(monkeypatch, lambda url: calls.append(url) or fake_payload(url))
    weather_history.backfill_weather(tmp_path, clock=lambda: NOW)
    second = weather_history.backfill_weather(tmp_path, clock=lambda: NOW + timedelta(hours=1))
    assert second["requests"] == second["records_examined"] == 0
    assert len(calls) == 1


def test_newest_missing_fixtures_first_with_cap_and_resume(tmp_path, monkeypatch):
    for day in (9, 10, 11):
        seed(tmp_path, f"api_football:{day}", day=day)
    calls = []
    inject(monkeypatch, lambda url: calls.append(url) or fake_payload(url))
    first = weather_history.backfill_weather(tmp_path, max_requests=2, clock=lambda: NOW)
    assert first["requests"] == first["sourced"] == 2
    assert first["deferred"] == 1
    assert [item["fixture_id"] for item in first["items"]] == ["api_football:11", "api_football:10", "api_football:9"]
    assert len(store_at(tmp_path).as_of(NOW, weather_kind="REANALYSIS")) == 2
    second = weather_history.backfill_weather(tmp_path, max_requests=2, clock=lambda: NOW)
    assert second["sourced"] == second["requests"] == 1
    assert len(calls) == 3


def test_shared_day_cache_can_source_multiple_fixtures_after_network_budget_used(tmp_path, monkeypatch):
    seed(tmp_path, "api_football:1")
    seed(tmp_path, "api_football:2")
    inject(monkeypatch, fake_payload)
    result = weather_history.backfill_weather(tmp_path, max_requests=1, clock=lambda: NOW)
    assert result["requests"] == 1
    assert result["sourced"] == 2
    assert result["deferred"] == 0


def test_newest_forecast_coordinates_win_and_duplicate_versions_do_not_multiply_work(tmp_path, monkeypatch):
    seed(tmp_path, latitude=50)
    seed(tmp_path, latitude=51, observed_at="2026-10-09T12:00:00Z")
    calls = []
    inject(monkeypatch, lambda url: calls.append(url) or fake_payload(url))
    result = weather_history.backfill_weather(tmp_path, clock=lambda: NOW)
    assert result["records_examined"] == result["sourced"] == 1
    assert parse_qs(urlparse(calls[0]).query)["latitude"] == ["51"]


@pytest.mark.parametrize("different", [{"model": "era5_land"}, {"latitude": 51}])
def test_wrong_model_or_coordinates_do_not_count_as_complete(tmp_path, monkeypatch, different):
    seed(tmp_path)
    seed(tmp_path, kind="REANALYSIS", observed_at=NOW.isoformat(), **({"model": "era5"} | different))
    inject(monkeypatch, fake_payload)
    result = weather_history.backfill_weather(tmp_path, clock=lambda: NOW)
    assert result["sourced"] == result["requests"] == 1


def test_rescheduled_latest_future_fixture_does_not_fall_back_to_older_date(tmp_path, monkeypatch):
    seed(tmp_path)
    seed(tmp_path, day=20, observed_at="2026-10-17T12:00:00Z")
    inject(monkeypatch, lambda url: pytest.fail("future fixture must not fetch"))
    result = weather_history.backfill_weather(tmp_path, clock=lambda: NOW)
    assert result["requests"] == result["records_examined"] == 0


def test_missing_coordinates_are_deferred_without_geocoding(tmp_path, monkeypatch):
    seed(tmp_path, latitude=None)
    inject(monkeypatch, lambda url: pytest.fail("location must not be guessed"))
    result = weather_history.backfill_weather(tmp_path, clock=lambda: NOW)
    assert result["requests"] == 0
    assert result["deferred"] == 1
    assert result["items"][0]["reason_code"] == "MISSING_COORDINATES"


def test_http_error_stops_batch_without_retries_and_preserves_attempt(tmp_path, monkeypatch):
    for day in (9, 10, 11):
        seed(tmp_path, f"api_football:{day}", day=day)
    calls = []

    def transport(url):
        calls.append(url)
        raise HTTPError(url, 429, "limit", {}, None)

    inject(monkeypatch, transport)
    result = weather_history.backfill_weather(tmp_path, clock=lambda: NOW)
    assert result["requests"] == result["unknown"] == len(calls) == 1
    assert result["deferred"] == 2
    assert result["stopped_reason"] == "HTTP_429"
    rows = store_at(tmp_path).as_of(NOW, weather_kind="REANALYSIS")
    assert len(rows) == 1
    assert rows[0]["time_basis"] == "LOOKUP_ATTEMPT_AT"


def test_cache_only_batch_uses_existing_raw_archive_without_network(tmp_path, monkeypatch):
    fixture, _ = seed(tmp_path)
    OpenMeteoHistory.from_root(tmp_path, transport=fake_payload, clock=lambda: NOW).historical(
        fixture, latitude=52.5, longitude=13.2, coordinate_source="test registry")
    inject(monkeypatch, lambda url: pytest.fail("cache only"))
    result = weather_history.backfill_weather(tmp_path, max_requests=0, clock=lambda: NOW)
    assert result["requests"] == 0
    assert result["sourced"] == 1


def test_record_read_window_is_bounded(tmp_path, monkeypatch):
    for day in (9, 10, 11):
        seed(tmp_path, f"api_football:{day}", day=day)
    monkeypatch.setattr(weather_history, "MAX_RECORDS", 2)
    inject(monkeypatch, fake_payload)
    result = weather_history.backfill_weather(tmp_path, clock=lambda: NOW)
    assert result["records_examined"] == result["requests"] == 2


def test_empty_database_is_a_zero_request_batch(tmp_path, monkeypatch):
    inject(monkeypatch, lambda url: pytest.fail("nothing to fetch"))
    result = weather_history.backfill_weather(tmp_path, clock=lambda: NOW)
    assert result["requests"] == result["records_examined"] == 0


@pytest.mark.parametrize("cap", [-1, 101, True, 2.5])
def test_invalid_batch_budget_rejected_before_creating_database(tmp_path, cap):
    with pytest.raises(ValueError, match="max_requests"):
        weather_history.backfill_weather(tmp_path, max_requests=cap)
    assert not (tmp_path / "data").exists()
