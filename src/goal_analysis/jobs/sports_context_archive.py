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
    venue_resources = {}
    previous_venues_collected = False

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

    def archived_forecast(fixture, *, exact_stadium=False):
        nonlocal archive_status
        provider.isolate_request_failures = exact_stadium
        checked_at = clock()
        archive_status = None
        venue_lookup = None
        try:
            location = None
            if fixture.get('source_provider') == 'the_stats_api':
                from goal_analysis.jobs.stadium_locations_shadow import fixture_location

                location = fixture_location(root, fixture, as_of=clock())
            if (fixture.get('source_provider') == 'the_stats_api'
                    and location is None and (exact_stadium or not fixture.get('venue_city'))):
                from goal_analysis.jobs.the_stats_context import Resources
                from goal_analysis.jobs.the_stats_venue import resolve_venue
                from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient

                limit = 40 if exact_stadium else 12
                if limit not in venue_resources:
                    # Each mode shares one ceiling across its fixture scope.
                    venue_resources[limit] = Resources(root,
                        TheStatsPrimaryClient(root, max_requests=limit, clock=clock), clock, limit)
                fixture, venue_lookup = resolve_venue(venue_resources[limit], fixture,
                    refresh_existing=exact_stadium)
                from goal_analysis.normalization.live_identity import field_id

                for field in ('league', 'home_team', 'away_team'):
                    fixture[field + '_id'] = 'the_stats_api:' + field_id(fixture, field)
                location = fixture_location(root, fixture, as_of=clock())
            if location is not None:
                p = location['payload']
                fixture = {**fixture, '_stadium_location': location,
                           'venue_name':p['native_name'],'venue_city':p['native_city']}
                weather = forecast(fixture, coordinates=(p['latitude'],p['longitude']))
                weather['location_basis'] = 'EXACT_MATCH_AND_REVIEWED_STADIUM_WGS84'
                weather['location_record_id'] = location['record_id']
            elif exact_stadium:
                weather = unknown(fixture,
                    'Nincs igazolt mérkőzés–stadion–koordináta kapcsolat; az időjárás ismeretlen.')
                weather.update(weather_kind='FORECAST', provider_issue_time=None,
                    observed_at=checked_at.isoformat(), kickoff=fixture.get('kickoff'),
                    time_basis='LOOKUP_ATTEMPT_AT', location_basis='UNVERIFIED_STADIUM')
                record(fixture, weather, {'raw_payload': None})
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
            weather['venue_lookup_requests'] = venue_resources[40 if exact_stadium else 12].usage['requests']
        return weather

    provider.forecast = archived_forecast
    provider.forecast_for_candidate_context = lambda fixture: archived_forecast(fixture, exact_stadium=True)
    def collect_previous_venues(fixtures):
        """At most 20 missing, exact previous-match details, once per run."""
        nonlocal previous_venues_collected
        if previous_venues_collected:
            return {'status': 'ALREADY_ATTEMPTED', 'requests': 0}
        previous_venues_collected = True
        from goal_analysis.engine.publication_window import allows_new_leg
        from goal_analysis.jobs.schedule_shadow import inspect_schedule
        from goal_analysis.jobs.stadium_locations_shadow import match_locations
        from goal_analysis.jobs.the_stats_context import Resources
        from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient

        selected = [f for f in fixtures if f.get('source_provider') == 'the_stats_api'
                    and allows_new_leg(f, clock())]
        inspected = inspect_schedule(root, selected, as_of=clock())
        matches, conflicts = {}, set()
        for feature in inspected['features']:
            for team in feature.get('teams', []):
                previous = team.get('previous_fixture')
                if previous and previous.get('status') == 'finished':
                    if previous['fixture_id'] in matches and matches[previous['fixture_id']] != previous:
                        conflicts.add(previous['fixture_id'])
                    matches[previous['fixture_id']] = previous
        matches = {fid: match for fid, match in matches.items() if fid not in conflicts}
        known, _ = match_locations(root, list(matches.values()), as_of=clock())
        missing = sorted(set(matches)-set(known))
        if not missing:
            return {'status': 'LOCAL_SOURCES_SUFFICIENT', 'requests': 0,
                    'previous_match_ids': sorted(matches), 'locally_verified': len(known),
                    'conflicting_previous_fixture_ids': sorted(conflicts)}
        resources = Resources(root, TheStatsPrimaryClient(root, max_requests=20, clock=clock), clock, 20)
        attempted, failures = [], []
        for fid in missing[:20]:
            # Requests merely preserve the original response. Exact identity,
            # status, receipt and reviewed coordinates are checked by the reader.
            attempted.append(fid)
            _row, _source, error = resources.get('/football/matches/'+fid)
            if error:
                failures.append({'fixture_id': fid, 'reason': error})
                if resources.usage.get('stopped_reason'):
                    break
        verified, issues = match_locations(root, list(matches.values()), as_of=clock())
        return {'status': 'PREVIOUS_VENUE_SOURCES_ATTEMPTED',
                'requests': resources.usage['requests'], 'max_requests': 20,
                'previous_match_ids': sorted(matches), 'attempted_fixture_ids': attempted,
                'conflicting_previous_fixture_ids': sorted(conflicts),
                'locally_verified_before': len(known), 'verified_after': len(verified),
                'unresolved_fixture_ids': sorted(set(matches)-set(verified)),
                'failures': failures, 'reader_issues': issues,
                'actual_itinerary_claimed': False}

    provider.collect_previous_venues = collect_previous_venues
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
