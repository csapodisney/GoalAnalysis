"""Automatic forecast archiving: fake transport, temporary SQLite, no network."""

import json
import sqlite3
from datetime import UTC, date, datetime

import pytest
from test_daily_223_live import config

from goal_analysis.config.portfolio import DEFAULT_SETTINGS
from goal_analysis.jobs import sports_context_archive
from goal_analysis.jobs.nightly import NightlyWorkflow
from goal_analysis.providers.open_meteo import OpenMeteoForecast
from goal_analysis.storage.db import Database
from goal_analysis.storage.sports_weather import WeatherStore

NOW = datetime(2026, 10, 18, 10, tzinfo=UTC)


def fixture():
    return {"fixture_id": "api_football:123", "venue_name": "Stadium", "venue_city": "Berlin",
            "venue_country": "Germany", "kickoff": "2026-10-18T18:30:00+00:00"}


def transport(url):
    if "geocoding-api" in url:
        return {"results": [{"name": "Berlin", "country": "Germany",
                             "latitude": 52.52, "longitude": 13.4}]}
    return {"hourly": {"time": ["2026-10-18T19:00"], "temperature_2m": [14.2],
                       "precipitation_probability": [60], "precipitation": [0.5],
                       "weather_code": [61], "wind_speed_10m": [18], "wind_gusts_10m": [30]},
            "provider_extra": ["retained", None]}


def store(root):
    return WeatherStore(Database(root / "data/goal_analysis.sqlite3"))


def test_construction_is_side_effect_free_and_forecast_persists_full_values(tmp_path):
    provider = sports_context_archive.make_weather_provider(
        tmp_path, "run-forecast", clock=lambda: NOW, transport=transport)
    assert isinstance(provider, OpenMeteoForecast)
    assert list(tmp_path.iterdir()) == []
    weather = provider.forecast(fixture())
    assert weather["status"] == "SOURCED"
    assert weather["temperature_c"] == 14.2
    assert weather["archive_status"]["status"] == "ARCHIVED"
    row = store(tmp_path).as_of(NOW)[0]
    assert row["raw_payload"]["provider_extra"] == ["retained", None]
    assert row["observed_at"] == "2026-10-18T10:00:00.000000+00:00"
    assert row["time_basis"] == "FETCHED_AT"
    assert row["weather"]["temperature_c"] == weather["temperature_c"]
    assert "archive_status" not in row["weather"]
    repeated = provider.forecast(fixture())
    assert repeated["archive_status"]["status"] == "ALREADY_ARCHIVED"
    assert store(tmp_path).summary()["snapshot_count"] == 1


@pytest.mark.parametrize("error", [OSError("secret-a"), ValueError("secret-b"),
                                  RuntimeError("secret-c")])
def test_archive_errors_are_explicit_and_do_not_discard_forecast(tmp_path, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(WeatherStore, "ingest", fail)
    weather = sports_context_archive.make_weather_provider(
        tmp_path, "run", clock=lambda: NOW, transport=transport).forecast(fixture())
    assert weather["status"] == "SOURCED"
    assert weather["temperature_c"] == 14.2
    assert weather["precipitation_probability_pct"] == 60
    assert weather["archive_status"]["status"] == "FAILED"
    assert "secret-" not in json.dumps(weather)


def test_network_failure_archives_unknown_at_original_attempt_time_without_retry(tmp_path):
    calls = []
    clock_calls = []

    def clock():
        clock_calls.append(True)
        return NOW.replace(minute=len(clock_calls) - 1)

    def fail(url):
        calls.append(url)
        raise OSError("https://provider.invalid/?key=secret")

    provider = sports_context_archive.make_weather_provider(tmp_path, "failed-run", clock=clock,
                                                           transport=fail)
    result = provider.forecast(fixture())
    assert result["status"] == "UNKNOWN"
    assert result["archive_status"]["status"] == "ARCHIVED"
    assert result["observed_at"] == NOW.isoformat()
    assert result["time_basis"] == "LOOKUP_ATTEMPT_AT"
    assert result["summary"] == "Az Open-Meteo lekérés nem sikerült; az időjárás ismeretlen."
    assert "temperature_c" not in result
    assert "secret" not in json.dumps(result)
    row = store(tmp_path).as_of(NOW)[0]
    assert row["raw_payload"] is None
    assert row["raw_payload_hash"] is None
    provider.forecast(fixture())
    assert len(calls) == 1


def test_default_run_identity_is_fixed_at_construction(tmp_path):
    provider = sports_context_archive.make_weather_provider(
        tmp_path, clock=lambda: NOW, transport=transport)
    result = provider.forecast(fixture())
    assert result["archive_status"]["source_run_id"] == f"weather-{NOW.isoformat()}"


def test_nightly_generate_uses_stage_run_id_and_durable_weather(tmp_path, monkeypatch):
    from goal_analysis.agents import codex_review
    from goal_analysis.jobs import nightly

    factory = sports_context_archive.make_weather_provider
    monkeypatch.setattr(sports_context_archive, "make_weather_provider",
                        lambda root, run_id, **kwargs: factory(root, run_id, transport=transport,
                                                              **kwargs))
    monkeypatch.setattr(codex_review, "CodexReviewer", lambda **kwargs: object())
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    workflow = NightlyWorkflow(tmp_path, clock=lambda: NOW, ledger=object())
    monkeypatch.setattr(workflow, "_football", lambda *args: object())
    job = {"config": config(), "usage": {}, "stages": [
        {"name": "generation", "attempts": 1, "run_id": "nightly-stage-run"}]}

    def collect(*args, **kwargs):
        return {"weather": kwargs["weather_provider"].forecast(fixture())}

    monkeypatch.setattr(nightly, "run_portfolio", collect)
    result = workflow._generate(date(2026, 10, 18), DEFAULT_SETTINGS, job)
    assert result["weather"]["archive_status"]["source_run_id"] == "nightly-stage-run"
    assert store(tmp_path).summary()["snapshot_count"] == 1
    with sqlite3.connect(tmp_path / "data/goal_analysis.sqlite3") as db:
        assert db.execute("SELECT source_run_id FROM sports_weather_source_runs").fetchone() == (
            "nightly-stage-run",
        )
