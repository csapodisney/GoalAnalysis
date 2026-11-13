"""Local historical-context recorders; no provider requests or ticket mutations."""

from __future__ import annotations

import math
import re
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from goal_analysis.storage.db import Database
from goal_analysis.storage.sports_news import NewsStore
from goal_analysis.storage.sports_weather import WeatherStore


def _city_approximation_source(fixture, checked_at):
    """Use collected native venue text only; never infer a city from a team."""
    city = fixture.get('venue_city')
    if not isinstance(city, str) or len(city.strip()) < 2:
        return None, 'SOURCED_VENUE_CITY_MISSING'
    fid = fixture.get('provider_fixture_id')
    if (fixture.get('source_provider') != 'the_stats_api'
            or not isinstance(fid, str) or re.fullmatch(r'mt_\d{1,30}', fid) is None
            or fixture.get('fixture_id') != 'the_stats_api:' + fid):
        return None, 'NATIVE_FIXTURE_CITY_BINDING_UNAVAILABLE'
    source_field = '_venue_source' if '_venue_source' in fixture else 'fixture_source'
    source = fixture.get(source_field)
    if not isinstance(source, dict):
        return None, 'VENUE_CITY_SOURCE_RECEIPT_MISSING'
    try:
        observed = datetime.fromisoformat(source['observed_at'])
        kickoff = datetime.fromisoformat(fixture['kickoff'])
        parsed = urlsplit(source['source_url'])
        if (observed.tzinfo is None or observed.utcoffset() is None
                or kickoff.tzinfo is None or kickoff.utcoffset() is None
                or checked_at.tzinfo is None or checked_at.utcoffset() is None
                or not observed <= checked_at <= kickoff - timedelta(minutes=15)
                or source.get('provider') != 'the_stats_api'
                or parsed.scheme != 'https' or parsed.netloc != 'api.thestatsapi.com'
                or parsed.path not in {'/api/football/matches', '/api/football/matches/' + fid}
                or not isinstance(source.get('content_hash'), str)
                or re.fullmatch(r'[0-9a-f]{64}', source['content_hash']) is None):
            return None, 'VENUE_CITY_SOURCE_RECEIPT_INVALID'
    except (KeyError, TypeError, ValueError):
        return None, 'VENUE_CITY_SOURCE_RECEIPT_INVALID'
    country = fixture.get('venue_country')
    if country is not None and (not isinstance(country, str) or not country.strip()):
        return None, 'VENUE_COUNTRY_INVALID'
    country_known = country is not None and country not in {
        'World', 'Europe', 'Africa', 'Asia', 'International', 'UNKNOWN'}
    return {'venue_city': city.strip(), 'venue_country': country if country_known else None,
        'country_status': 'COLLECTED_FIXTURE_COUNTRY' if country_known else 'UNKNOWN',
        'source_field': source_field, 'source_receipt': deepcopy(source),
        'binding_basis': 'COLLECTED_NATIVE_FIXTURE_VENUE_CITY_NOT_STADIUM_COORDINATES',
        'physical_source_validation': 'NOT_PERFORMED_BY_CITY_FALLBACK'}, None


def make_weather_provider(root: Path, source_run_id: str | None = None, *, clock=None,
                          transport=None, root_run_lock=None, monotonic_clock=None):
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
    availability_status = None
    completion = None
    if root_run_lock is not None:
        from goal_analysis.storage.weather_availability import WeatherCompletion

        try:
            completion = WeatherCompletion(root, source_run_id, root_run_lock, clock=clock)
        except (ValueError, TypeError, AttributeError, OSError):
            availability_status = {"status": "NO_ADMISSION_INVALID_NORMAL_RUN_CAPABILITY", "created": False}
    venue_resources = {}
    previous_venues_collected = False
    city_approximation = None
    city_started = city_last = None
    city_transport_gap = None
    city_clock = monotonic_clock or monotonic

    def record(fixture, weather, context):
        nonlocal archive_status, availability_status
        try:
            if root_run_lock is not None:
                if completion is None:
                    raise ValueError("NORMAL_WEATHER_RUN_CAPABILITY_UNAVAILABLE")
                completion.check_held()
            if city_approximation is not None:
                # Strict readers currently accept SOURCED rows without a city
                # precision distinction. Keep this separate report-only context.
                archive_status = {'status': 'REPORT_ONLY_CITY_APPROXIMATION',
                    'source_run_id': source_run_id, 'snapshot_id': None}
                availability_status = {'status': 'NO_STRICT_WEATHER_RECEIPT', 'created': False}
                return
            archive.initialize()
            metadata = {k: fixture[v] for k, v in (
                ('venue_source', '_venue_source'), ('stadium_location', '_stadium_location')) if fixture.get(v)} or None
            if completion is None:
                saved = archive.ingest(weather, fixture, source_run_id=source_run_id,
                    raw_payload=context.get("raw_payload"), metadata=metadata)
            else:
                saved, availability_status = completion.record(archive, weather, fixture, context, metadata=metadata)
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
    if completion is not None:
        from goal_analysis.providers.open_meteo import _get_json

        kwargs["transport"] = completion.transport(transport if transport is not None else _get_json)
    elif transport is not None:
        kwargs["transport"] = transport
    provider = OpenMeteoForecast(**kwargs)
    original_transport = provider.transport

    def transport_with_city_budget(url):
        nonlocal city_started, city_last, city_transport_gap
        if city_approximation is not None:
            now = city_clock()
            if (type(now) not in (int, float) or not math.isfinite(now) or now < 0
                    or (city_last is not None and now < city_last)):
                # RuntimeError avoids poisoning the provider's failed-URL cache:
                # no request was attempted, and exact-stadium use stays independent.
                city_transport_gap = 'CITY_MONOTONIC_CLOCK_INVALID'
                raise RuntimeError(city_transport_gap)
            city_last = now
            if city_started is None:
                city_started = now
            if now - city_started >= 120:
                city_transport_gap = 'CITY_TIME_BUDGET_EXHAUSTED'
                raise RuntimeError(city_transport_gap)
        return original_transport(url)

    provider.transport = transport_with_city_budget
    forecast = provider.forecast

    def archived_forecast(fixture, *, exact_stadium=False):
        nonlocal archive_status, availability_status, city_approximation, city_transport_gap
        city_approximation = None
        city_transport_gap = None
        provider.isolate_request_failures = exact_stadium
        checked_at = clock()
        archive_status = None
        availability_status = None if completion is not None else availability_status
        venue_lookup = None
        try:
            kickoff = datetime.fromisoformat(fixture["kickoff"])
            if kickoff.tzinfo is None or kickoff.utcoffset() is None:
                raise ValueError("AWARE_WEATHER_KICKOFF_REQUIRED")
            if completion is not None:
                completion.check_held()
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
                city_approximation, city_gap = _city_approximation_source(fixture, clock())
                if city_approximation is not None:
                    city_fixture = {**fixture, 'venue_city': city_approximation['venue_city'],
                        'venue_country': city_approximation['venue_country']}
                    weather = forecast(city_fixture)
                else:
                    weather = unknown(fixture,
                        'Nincs igazolt mérkőzés–stadion–koordináta kapcsolat; az időjárás ismeretlen.')
                    weather.update(weather_kind='FORECAST', provider_issue_time=None,
                        observed_at=checked_at.isoformat(), kickoff=fixture.get('kickoff'),
                        time_basis='LOOKUP_ATTEMPT_AT', location_basis='UNVERIFIED_STADIUM',
                        city_approximation_reason=city_gap)
                    record(fixture, weather, {'raw_payload': None})
            else:
                weather = forecast(fixture)
        except Exception:  # noqa: BLE001 - same optional-weather boundary as portfolio
            weather = unknown(fixture, "Az Open-Meteo lekérés nem sikerült; az időjárás ismeretlen.")
            weather.update(weather_kind="FORECAST", provider_issue_time=None,
                           observed_at=checked_at.isoformat(), kickoff=fixture.get("kickoff"),
                           time_basis="LOOKUP_ATTEMPT_AT")
            record(fixture, weather, {"raw_payload": None})
        if city_approximation is not None:
            availability_status = {'status': 'NO_STRICT_WEATHER_RECEIPT', 'created': False}
            weather.update(location_basis='SOURCED_FIXTURE_CITY_GEOCODING',
                location_precision='CITY_APPROXIMATION', strict_stadium_forecast=False,
                probability_effect='NOT_ESTIMATED', city_source=city_approximation,
                city_network_policy={'deadline_seconds': 120, 'clock_basis': 'MONOTONIC',
                    'cache_after_deadline': 'ALLOWED_WITH_ORIGINAL_FETCH_CLOCK',
                    'deadline_applies': 'BEFORE_EACH_NEW_CITY_TRANSPORT_REQUEST',
                    'inflight_request_wall_time_bound_seconds': None},
                city_approximation_reason=city_transport_gap or ('CITY_FORECAST_NOT_VERIFIED_STADIUM_FORECAST'
                    if weather.get('status') == 'SOURCED' else 'CITY_FORECAST_UNAVAILABLE'),
                availability_status={'status': 'NO_STRICT_WEATHER_RECEIPT', 'created': False})
            weather['summary'] += ' Városi közelítés; nem igazolt stadion-előrejelzés.'
        weather["archive_status"] = archive_status
        if root_run_lock is not None:
            weather["availability_status"] = availability_status
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
