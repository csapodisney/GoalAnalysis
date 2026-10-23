"""Local historical-context recorders; no provider requests or ticket mutations."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from goal_analysis.storage.db import Database
from goal_analysis.storage.sports_news import NewsStore
from goal_analysis.storage.sports_weather import WeatherStore


def make_weather_provider(root: Path, source_run_id: str | None = None, *, clock=None,
                          transport=None):
    """Build the normal forecast provider with a local, failure-visible recorder.

    A provider belongs to one serial portfolio run. Construction does no I/O;
    each completed lookup saves its original retrieval/attempt timestamp. Store
    failure adds archive_status without replacing valid forecast values.
    """
    from goal_analysis.providers.open_meteo import OpenMeteoForecast, unknown

    clock = clock or (lambda: datetime.now(UTC))
    if source_run_id is None:
        source_run_id = f"weather-{clock().isoformat()}"
    archive = WeatherStore(Database(Path(root) / "data/goal_analysis.sqlite3"))
    archive_status = None
    venue_resources = None

    def record(fixture, weather, context):
        nonlocal archive_status
        try:
            archive.initialize()
            saved = archive.ingest(weather, fixture, source_run_id=source_run_id,
                                   raw_payload=context.get("raw_payload"),
                                   metadata={k:fixture[v] for k,v in (
                                       ('venue_source','_venue_source'),('stadium_location','_stadium_location'))
                                       if fixture.get(v)} or None)
            archive_status = {
                "status": "ARCHIVED" if saved["inserted"] else "ALREADY_ARCHIVED",
                "snapshot_id": saved["snapshot_id"], "source_run_id": source_run_id,
            }
        except (ValueError, TypeError, OverflowError):
            archive_status = {"status": "FAILED", "error_code": "WEATHER_ARCHIVE_INVALID_INPUT"}
        except (OSError, sqlite3.Error):
            archive_status = {"status": "FAILED", "error_code": "WEATHER_ARCHIVE_STORE_UNAVAILABLE"}
        except Exception:  # noqa: BLE001 - preserve valid forecasts when archival fails
            archive_status = {"status": "FAILED", "error_code": "WEATHER_ARCHIVE_FAILED"}

    kwargs = {"clock": clock, "recorder": record}
    if transport is not None:
        kwargs["transport"] = transport
    provider = OpenMeteoForecast(**kwargs)
    forecast = provider.forecast

    def archived_forecast(fixture):
        nonlocal archive_status, venue_resources
        checked_at = clock()
        archive_status = None
        venue_lookup = None
        try:
            if fixture.get('source_provider') == 'the_stats_api' and not fixture.get('venue_city'):
                from goal_analysis.jobs.the_stats_context import Resources
                from goal_analysis.jobs.the_stats_venue import resolve_venue
                from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient

                if venue_resources is None:
                    venue_resources = Resources(root, TheStatsPrimaryClient(root, max_requests=12, clock=clock), clock, 12)
                fixture, venue_lookup = resolve_venue(venue_resources, fixture)
                from goal_analysis.normalization.live_identity import field_id

                for field in ('league', 'home_team', 'away_team'):
                    fixture[field + '_id'] = 'the_stats_api:' + field_id(fixture, field)
            location = None
            if fixture.get('source_provider') == 'the_stats_api':
                from goal_analysis.jobs.stadium_locations_shadow import fixture_location

                location = fixture_location(root, fixture, as_of=clock())
            if location is not None:
                p = location['payload']
                fixture = {**fixture, '_stadium_location': location,
                           'venue_name':p['native_name'],'venue_city':p['native_city']}
                weather = forecast(fixture, coordinates=(p['latitude'],p['longitude']))
                weather['location_basis'] = 'EXACT_MATCH_AND_REVIEWED_STADIUM_WGS84'
                weather['location_record_id'] = location['record_id']
            else:
                weather = forecast(fixture)
        except Exception:  # noqa: BLE001 - same optional-weather boundary as portfolio
            weather = unknown(fixture, "Az Open-Meteo lekérés nem sikerült; az időjárás ismeretlen.")
            weather.update(weather_kind="FORECAST", provider_issue_time=None,
                           observed_at=checked_at.isoformat(), kickoff=fixture.get("kickoff"),
                           time_basis="LOOKUP_ATTEMPT_AT")
            record(fixture, weather, {"raw_payload": None})
        weather["archive_status"] = archive_status
        if venue_lookup is not None:
            weather['venue_lookup'] = venue_lookup
            weather['venue_lookup_requests'] = venue_resources.usage['requests']
        return weather

    provider.forecast = archived_forecast
    return provider


def archive_grok_result(
    root: Path, result: dict, request_id: str, completed_at: datetime | str,
    model: str = "grok-4.7",
) -> dict[str, Any]:
    """Archive a persisted Grok result using its original completion timestamp.

    Shared by new results, cached results and historical backfills. This recorder
    performs no network I/O. Failure is explicit and credential-safe; callers
    keep the already persisted advisory result and may retry this local step.
    """
    try:
        completed = (datetime.fromisoformat(completed_at)
                     if isinstance(completed_at, str) else completed_at)
        if (not isinstance(completed, datetime) or completed.tzinfo is None
                or completed.utcoffset() is None):
            raise ValueError("completion timestamp must be timezone-aware")
        archive = NewsStore(Database(Path(root) / "data/goal_analysis.sqlite3"))
        archive.initialize()
        counts = archive.ingest_grok_result(
            result, request_id=request_id, observed_at=completed, model=model,
        )
        return {"status": "ARCHIVED" if counts["imports_created"] else "ALREADY_ARCHIVED",
                **counts}
    except (ValueError, TypeError, OverflowError):
        return {"status": "FAILED", "error_code": "NEWS_ARCHIVE_INVALID_INPUT"}
    except (OSError, sqlite3.Error):
        return {"status": "FAILED", "error_code": "NEWS_ARCHIVE_STORE_UNAVAILABLE"}
    except Exception:  # noqa: BLE001 - an optional archive must preserve the valid advisory
        return {"status": "FAILED", "error_code": "NEWS_ARCHIVE_FAILED"}
