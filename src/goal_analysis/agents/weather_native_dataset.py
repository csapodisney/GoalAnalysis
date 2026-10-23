"""Explicit SHADOW-only TheStats weather history; no active ranking path switch.

Native identities, canonical result versions and actual local availability
witnesses must agree. Reanalysis remains retrospective. A forecast received
before kickoff but only witnessed later is not pre-match availability proof.
The archived API-Football reader is deliberately left unchanged.
"""
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from goal_analysis.agents.weather_dataset import _weather_record
from goal_analysis.normalization.canonical import canonical_id, utc
from goal_analysis.normalization.leakage import require_known_before
from goal_analysis.normalization.temporal import project_times
from goal_analysis.normalization.training_datasets import sha, snapshot
from goal_analysis.providers.open_meteo_history import _FIELDS, HOURLY
from goal_analysis.storage.canonical_registry import digest, encoded
from goal_analysis.storage.historical_results import decision_content, identity
from goal_analysis.storage.temporal import _source_hash

VERSION = 'native-weather-dataset-shadow-v2'
MAX_VERSIONS = 20_000
MAX_BYTES = 64 * 1024 * 1024
MAX_REANALYSIS_SOURCES = 128
MAX_REANALYSIS_RAW_BYTES = 500_000


def _native(value, prefix):
    if not isinstance(value, str) or re.fullmatch(prefix + r'_\d{1,30}', value) is None:
        raise ValueError('INVALID_NATIVE_ID')
    return value


def _witness(db, namespace, key, source, cutoff):
    source_sha = _source_hash(source)
    witness = db.execute('''SELECT * FROM temporal_receipts
        WHERE namespace=? AND source_key=? AND source_sha256=?''', (namespace, key, source_sha)).fetchone()
    if witness is None:
        raise ValueError('MISSING_AVAILABILITY_RECEIPT')
    if witness['receipt_id'] != digest([namespace, key, source_sha]):
        raise ValueError('RECEIPT_ID_MISMATCH')
    times = project_times(namespace, source, available_at=witness['available_at'])
    if encoded(times) != witness['times_json'] or any(
            times[field] != witness[field] for field in ('observed_at', 'published_at', 'ingested_at', 'available_at')):
        raise ValueError('RECEIPT_TIME_MISMATCH')
    require_known_before(times, as_of=cutoff)
    return {'receipt_id': witness['receipt_id'], 'source_sha256': source_sha, 'times': times}


def _result(source):
    record = json.loads(source['record_json'])
    if digest(record) != source['version_id'] or identity(record) != source['identity_sha256']:
        raise ValueError('RESULT_CONTENT_OR_IDENTITY_HASH_MISMATCH')
    native = _native(record['provider_id'], 'mt')
    if (record['provider'] != 'the_stats_api'
            or canonical_id('fixture', 'the_stats_api:' + native) != source['match_id']):
        raise ValueError('RESULT_PROVIDER_IDENTITY_MISMATCH')
    for name, prefix in (('competition_id', 'comp'), ('season_id', 'sn'), ('home_id', 'tm'), ('away_id', 'tm')):
        _native(record[name], prefix)
    if record['home_id'] == record['away_id']:
        raise ValueError('IDENTICAL_TEAMS')
    return record


def _results(db, cutoff, gaps):
    groups, size = defaultdict(list), 0
    rows = db.execute('''SELECT v.* FROM historical_result_versions v
        JOIN historical_result_matches m USING(match_id) WHERE m.provider='the_stats_api'
        AND v.observed_at<=? AND v.ingested_at<=?
        ORDER BY v.match_id,v.observed_at DESC,v.version_id LIMIT ?''',
        (cutoff.isoformat(timespec='microseconds'), cutoff.isoformat(timespec='microseconds'), MAX_VERSIONS + 1))
    count = 0
    for count, raw in enumerate(rows, 1):
        size += len(raw['record_json'].encode('utf-8'))
        if count > MAX_VERSIONS or size > MAX_BYTES:
            raise ValueError('NATIVE_RESULT_SOURCE_BUDGET_EXCEEDED')
        source = dict(raw)
        group = groups[source['match_id']]
        if not group or source['observed_at'] == group[0]['observed_at']:
            group.append(source)
    matches, lineage = {}, []
    for sources in groups.values():
        try:
            records = [_result(source) for source in sources]
            if len({digest(decision_content(record)) for record in records}) != 1:
                raise ValueError('CONFLICTING_LATEST_RESULT')
            source, record = sources[0], records[0]
            if (record['score_scope'] != 'REGULATION' or record['kickoff'] is None
                    or record['status'] not in {'FT', 'finished', 'FINISHED'}):
                raise ValueError('UNPROVEN_REGULATION_RESULT')
            kickoff = utc(record['kickoff'])
            if kickoff >= cutoff or kickoff.date().isoformat() != record['match_date']:
                raise ValueError('FUTURE_OR_INCONSISTENT_KICKOFF')
            if any(type(record[key]) is not int or record[key] < 0 for key in ('home_goals', 'away_goals')):
                raise ValueError('MISSING_REGULATION_SCORE')
            witness = _witness(db, 'result', source['version_id'], source, cutoff)
            fixture = 'the_stats_api:' + record['provider_id']
            match = {'fixture_id': fixture, 'provider': 'the_stats_api',
                'league_id': 'the_stats_api:' + record['competition_id'], 'season': record['season_id'],
                'home_team_id': 'the_stats_api:' + record['home_id'],
                'away_team_id': 'the_stats_api:' + record['away_id'],
                'home_team': record['home_team'], 'away_team': record['away_team'],
                'kickoff': record['kickoff'], 'home_goals': record['home_goals'], 'away_goals': record['away_goals'],
                'result_period': 'FULL_TIME_90_MINUTES', 'status': 'FT', 'competition_type': 'UNKNOWN',
                'observed_at': record['observed_at'], 'available_at': witness['times']['available_at'],
                'result_version_id': source['version_id'], 'result_receipt_id': witness['receipt_id'],
                'source_content_hash': record['source_sha256'], 'source_url': record['source_url']}
            halves = [record.get('half_' + side + '_goals') for side in ('home', 'away')]
            valid_halves = all(type(value) is int and 0 <= value <= record[side + '_goals']
                               for value, side in zip(halves, ('home', 'away'), strict=True))
            match.update(half_home_goals=halves[0] if valid_halves else None,
                         half_away_goals=halves[1] if valid_halves else None)
            if not valid_halves:
                gaps['MISSING_EXPLICIT_HALF_SCORE'] += 1
            matches[fixture] = match
            lineage.append({'namespace': 'result', 'source_key': source['version_id'], **witness})
        except (KeyError, TypeError, ValueError) as error:
            gaps['RESULT:' + str(error)] += 1
    return matches, lineage, count


def _number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _reanalysis(root, db, row, stored, match, witness, budget):
    """Rebuild the recorded ERA5 contract, never trust a normalized value alone.

    Only immutable reads. Venue review and HTTP/result evidence are evaluated
    at this weather row's own availability, not through a newer location release.
    A repeated identical raw blob without its claimed receipt clock is unknown.
    """
    budget['sources'] += 1
    if budget['sources'] > MAX_REANALYSIS_SOURCES:
        raise ValueError('NATIVE_REANALYSIS_SOURCE_BUDGET_EXCEEDED')
    weather, raw = stored['weather'], stored.get('raw_payload')
    if (row['provider'] != 'open_meteo' or weather.get('provider') != 'open_meteo'
            or weather.get('model') != 'era5' or stored.get('time_basis') != 'FETCHED_AT'
            or weather.get('time_basis') != 'FETCHED_AT' or row['provider_issue_time'] is not None
            or weather.get('provider_issue_time') is not None or not isinstance(raw, dict) or raw.get('error')):
        raise ValueError('REANALYSIS_PROVIDER_MODEL_OR_TIME_CONTRACT')
    available = utc(witness['times']['available_at']); observed = utc(row['observed_at'])
    kickoff = utc(match['kickoff']); hour = kickoff.replace(minute=0, second=0, microsecond=0)
    if kickoff-hour >= timedelta(minutes=30):
        hour += timedelta(hours=1)
    if (utc(weather['kickoff']) != kickoff or utc(row['target_time']) != hour
            or utc(weather['forecast_time']) != hour or utc(weather['target_time']) != hour
            or utc(weather['requested_target_time']) != hour
            or hour.date() > observed.date()-timedelta(days=6)):
        raise ValueError('REANALYSIS_UTC_TARGET_OR_PUBLICATION_LAG')
    metadata = stored.get('metadata')
    if not isinstance(metadata, dict) or not isinstance(metadata.get('stadium_location'), dict):
        raise TypeError('REANALYSIS_STADIUM_METADATA_MISSING')
    location = metadata['stadium_location']
    if metadata.get('historical_use') != 'RETROSPECTIVE_REANALYSIS_NOT_PREMATCH_KNOWLEDGE':
        raise ValueError('REANALYSIS_HISTORICAL_USE_UNPROVEN')
    result = db.execute('SELECT * FROM historical_result_versions WHERE version_id=?',
                        (metadata['result_version_id'],)).fetchone()
    if result is None:
        raise ValueError('REANALYSIS_BOUND_RESULT_MISSING')
    result = dict(result); original = _result(result)
    result_proof = _witness(db, 'result', result['version_id'], result, available)
    if (result_proof['receipt_id'] != metadata['result_receipt_id']
            or original['provider_id'] != match['fixture_id'].split(':', 1)[1]
            or 'the_stats_api:'+original['competition_id'] != match['league_id']
            or original['season_id'] != match['season']
            or 'the_stats_api:'+original['home_id'] != match['home_team_id']
            or 'the_stats_api:'+original['away_id'] != match['away_team_id']
            or utc(original['kickoff']) != kickoff):
        raise ValueError('REANALYSIS_RESULT_SOURCE_IDENTITY_MISMATCH')
    receipts = location.get('source_receipts')
    if not isinstance(receipts, list) or not receipts or not isinstance(receipts[-1], dict):
        raise ValueError('REANALYSIS_ACTUAL_EVENT_HTTP_WITNESS_REQUIRED')
    http = receipts[-1]
    if (http.get('kind') != 'ACTUAL_AUTHENTICATED_HTTP_RESPONSE_NOT_QUOTE_TIME'
            or not isinstance(http.get('times'), dict)):
        raise ValueError('REANALYSIS_ACTUAL_EVENT_HTTP_WITNESS_REQUIRED')
    source = {'provider':'the_stats_api', 'content_hash':http['source_key'], 'source_url':http['source_url'],
        'observed_at':http['times']['observed_at'], 'http_observation_id':http['observation_id'],
        'http_receipt_id':http['receipt_id'], 'http_observation_status':'VERIFIED'}
    descriptor = {'fixture_id':original['provider_id'], 'competition_id':original['competition_id'],
        'season_id':original['season_id'], 'home_team_id':original['home_id'], 'away_team_id':original['away_id'],
        'kickoff':match['kickoff'], 'status':'finished', '_venue_source':source}
    from goal_analysis.jobs.stadium_locations_shadow import match_locations

    locations, issues = match_locations(root, [descriptor], as_of=available)
    actual = locations.get(original['provider_id'])
    if actual is None or issues or actual != location:
        raise ValueError('REANALYSIS_ACTUAL_EVENT_STADIUM_UNPROVEN')
    p = actual['payload']
    if (weather.get('latitude') != p['latitude'] or weather.get('longitude') != p['longitude']
            or not _number(weather.get('latitude'), -90, 90) or not _number(weather.get('longitude'), -180, 180)
            or weather.get('venue_name') != p['native_name'] or weather.get('venue_city') != p['native_city']):
        raise ValueError('REANALYSIS_REVIEWED_STADIUM_COORDINATE_MISMATCH')
    parts = urlsplit(row['source_url'])
    query = {'latitude':[str(p['latitude'])], 'longitude':[str(p['longitude'])],
        'start_date':[str(hour.date())], 'end_date':[str(hour.date())], 'hourly':[','.join(HOURLY)],
        'models':['era5'], 'timezone':['UTC'], 'temperature_unit':['celsius'],
        'wind_speed_unit':['kmh'], 'precipitation_unit':['mm']}
    if (parts.scheme != 'https' or parts.netloc != 'archive-api.open-meteo.com' or parts.path != '/v1/archive'
            or parts.fragment or weather.get('source_url') != row['source_url']
            or parse_qs(parts.query, keep_blank_values=True) != query):
        raise ValueError('REANALYSIS_EXACT_REQUEST_QUERY_MISMATCH')
    rawrow = db.execute("SELECT * FROM raw_snapshots WHERE content_hash=? AND provider='open_meteo'",
                        (row['raw_payload_hash'],)).fetchone()
    if rawrow is None:
        raise ValueError('REANALYSIS_RAW_ARCHIVE_MISSING')
    rawrow = dict(rawrow)
    if (rawrow['resource_type'] != 'era5_hourly' or rawrow['source_url'] != row['source_url']
            or utc(rawrow['fetched_at']) != observed or not 0 < rawrow['size_bytes'] <= MAX_REANALYSIS_RAW_BYTES):
        raise ValueError('REANALYSIS_RAW_SOURCE_URL_OR_RECEIPT_CLOCK_MISMATCH')
    rawroot = root/'data/raw'; path = (rawroot/rawrow['relative_path']).resolve()
    if not path.is_relative_to(rawroot.resolve()) or not path.is_file() or path.stat().st_size != rawrow['size_bytes']:
        raise ValueError('REANALYSIS_RAW_ARCHIVE_PATH_OR_SIZE_MISMATCH')
    with path.open('rb') as stream:
        content = stream.read(MAX_REANALYSIS_RAW_BYTES + 1)
    budget['bytes'] += len(content)
    if budget['bytes'] > MAX_BYTES:
        raise ValueError('NATIVE_REANALYSIS_BYTE_BUDGET_EXCEEDED')
    if (len(content) != rawrow['size_bytes'] or hashlib.sha256(content).hexdigest() != row['raw_payload_hash']
            or content != encoded(raw).encode() or weather.get('raw_payload_hash') != row['raw_payload_hash']):
        raise ValueError('REANALYSIS_RAW_ARCHIVE_BYTES_MISMATCH')
    hourly, units = raw.get('hourly'), raw.get('hourly_units')
    if not all(isinstance(v, dict) for v in (hourly, units, weather.get('units'))):
        raise ValueError('REANALYSIS_RAW_UTC_HOURLY_SHAPE_MISMATCH')
    times = hourly['time']
    if (type(raw.get('utc_offset_seconds')) is not int or raw['utc_offset_seconds'] != 0
            or raw.get('timezone') not in {'GMT','UTC','Etc/UTC','Etc/GMT'} or units.get('time') != 'iso8601'
            or not isinstance(times, list) or not 1 <= len(times) <= 24 or len(set(times)) != len(times)):
        raise ValueError('REANALYSIS_RAW_UTC_HOURLY_SHAPE_MISMATCH')
    for time in times:
        parsed = datetime.strptime(time, '%Y-%m-%dT%H:%M').replace(tzinfo=UTC)
        if parsed.strftime('%Y-%m-%dT%H:%M') != time or parsed.minute != 0 or parsed.date() != hour.date():
            raise ValueError('REANALYSIS_RAW_UTC_HOUR_MISMATCH')
    wanted = hour.strftime('%Y-%m-%dT%H:%M')
    if times.count(wanted) != 1:
        raise ValueError('REANALYSIS_RAW_TARGET_HOUR_MISSING')
    index = times.index(wanted)
    for field, (name, low, high, unit) in _FIELDS.items():
        values = hourly.get(field)
        if (units.get(field) != unit or weather['units'].get(field) != unit
                or not isinstance(values, list) or len(values) != len(times)
                or not _number(values[index], low, high) or not _number(weather.get(name), low, high)
                or values[index] != weather[name]
                or (field == 'weather_code' and values[index] != int(values[index]))):
            raise ValueError('REANALYSIS_RAW_UNIT_OR_VALUE_MISMATCH:'+field)
    for key, bound in (('latitude',90), ('longitude',180)):
        if (not _number(raw.get(key), -bound, bound)
                or not _number(weather.get('provider_grid_'+key), -bound, bound)
                or weather.get('provider_grid_'+key) != raw[key]):
            raise ValueError('REANALYSIS_PROVIDER_GRID_PROVENANCE_MISMATCH')
    if weather.get('wind_gusts_kmh') is not None or weather.get('unsupported_fields') != ['wind_gusts_10m']:
        raise ValueError('REANALYSIS_UNSUPPORTED_GUSTS_NOT_UNKNOWN')
    return {'contract':'EXACT_ERA5_RAW_HOUR_UNITS_VALUES_EVENT_STADIUM_V1',
        'raw_content_sha256':row['raw_payload_hash'], 'raw_metadata_sha256':_source_hash(rawrow),
        'raw_observed_at':rawrow['fetched_at'], 'weather_available_at':available.isoformat(),
        'venue_record_id':actual['record_id'], 'venue_available_at':actual['times']['available_at'],
        'event_http_receipt_id':http['receipt_id'], 'bound_result_receipt_id':result_proof['receipt_id'],
        'provider_grid_coordinates':[raw['latitude'],raw['longitude']], 'grid_is_not_stadium_measurement':True}


def _weather(db, matches, cutoff, gaps, root):
    latest, size = {}, 0
    rows = db.execute('''SELECT * FROM sports_weather_snapshots
        WHERE fixture_id LIKE 'the_stats_api:%' AND observed_at<=?
        ORDER BY fixture_id,weather_kind,observed_at DESC,snapshot_id LIMIT ?''',
        (cutoff.isoformat(timespec='microseconds'), MAX_VERSIONS + 1))
    for count, raw in enumerate(rows, 1):
        row = dict(raw)
        size += len(row['snapshot_json'].encode('utf-8'))
        if count > MAX_VERSIONS or size > MAX_BYTES:
            raise ValueError('NATIVE_WEATHER_SOURCE_BUDGET_EXCEEDED')
        match, kind = matches.get(row['fixture_id']), row['weather_kind']
        if match is None:
            gaps['WEATHER_WITHOUT_ACCEPTED_RESULT'] += 1
            continue
        if kind not in {'REANALYSIS', 'FORECAST'}:
            gaps['UNSUPPORTED_WEATHER_KIND'] += 1
            continue
        if kind == 'FORECAST' and not (utc(match['kickoff']) - timedelta(hours=48)
                <= utc(row['observed_at']) <= utc(match['kickoff']) - timedelta(minutes=15)):
            gaps['FORECAST_OUTSIDE_PREMATCH_WINDOW'] += 1
            continue
        key = row['fixture_id'], kind
        group = latest.setdefault(key, [])
        if not group or row['observed_at'] == group[0]['observed_at']:
            group.append(row)
    lineage = [];budget = {'sources':0, 'bytes':0}
    for (fixture, kind), candidates in latest.items():
        try:
            values = []
            for row in candidates:
                stored = json.loads(row['snapshot_json'])
                if not isinstance(stored, dict) or not isinstance(stored.get('weather'), dict):
                    raise TypeError('WEATHER_INVALID_STRUCTURE')
                if any(stored.get(field) != row[field] for field in (
                        'fixture_id', 'provider', 'weather_kind', 'status', 'observed_at', 'target_time',
                        'provider_issue_time', 'kickoff', 'source_url', 'raw_payload_hash')):
                    raise ValueError('WEATHER_COLUMN_PAYLOAD_MISMATCH')
                value = _weather_record(row, matches[fixture], cutoff, kind)
                deadline = utc(matches[fixture]['kickoff']) - timedelta(minutes=15) if kind == 'FORECAST' else cutoff
                witness = _witness(db, 'weather', row['snapshot_id'], row, deadline)
                if kind == 'REANALYSIS':
                    value['source_validation'] = _reanalysis(root, db, row, stored, matches[fixture], witness, budget)
                values.append((value, witness, row))
            comparable = [{k: v for k, v in value.items() if k not in {'snapshot_id','source_validation'}} for value, _, _ in values]
            if len({sha(value) for value in comparable}) != 1:
                raise ValueError('CONFLICTING_LATEST_WEATHER')
            value, witness, row = values[0]
            value['available_at'] = witness['times']['available_at']
            value['receipt_id'] = witness['receipt_id']
            matches[fixture]['prematch_weather' if kind == 'FORECAST' else 'weather'] = value
            lineage.append({'namespace': 'weather', 'source_key': row['snapshot_id'], **witness})
        except (KeyError, TypeError, ValueError, OSError, sqlite3.Error, IndexError) as error:
            if str(error).endswith('BUDGET_EXCEEDED'):
                raise
            gaps['WEATHER:' + str(error)] += 1
    return lineage


def load_native_weather_dataset(root, *, as_of):
    """Explicit research input only; never fetch, write or alter the daily reader.

    Receipt admission at as_of does not certify older model fitting cutoffs.
    Earlier Elo/result-availability and heldout-market promotion gates remain.
    """
    cutoff, root = utc(as_of), Path(root).resolve()
    path, gaps = root / 'data/goal_analysis.sqlite3', Counter()
    matches, lineage, scanned = {}, [], 0
    if path.exists():
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {'historical_result_versions', 'historical_result_matches', 'temporal_receipts'} <= tables:
                matches, lineage, scanned = _results(db, cutoff, gaps)
                if 'sports_weather_snapshots' in tables:
                    lineage.extend(_weather(db, matches, cutoff, gaps, root))
                else:
                    gaps['MISSING_WEATHER_TABLE'] += 1
            else:
                gaps['MISSING_RESULT_OR_RECEIPT_TABLE'] += 1
    else:
        gaps['MISSING_DATABASE'] += 1
    rows = sorted(matches.values(), key=lambda r: (r['kickoff'], r['fixture_id']))
    coverage = {'result_versions_scanned': scanned, 'completed_matches': len(rows),
                'matches_with_reanalysis': sum('weather' in row for row in rows),
                'matches_with_prematch_forecast': sum('prematch_weather' in row for row in rows),
                'matches_with_both': sum('weather' in row and 'prematch_weather' in row for row in rows),
                'matches_with_explicit_half_score': sum(row['half_home_goals'] is not None for row in rows)}
    dataset = snapshot(rows, role='native_weather_reference_shadow', as_of=cutoff,
        recipe={'version': VERSION, 'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        lineage=lineage)
    return {'version': VERSION, 'mode': 'SHADOW', 'effective_data_path': 'LEGACY_UNCHANGED',
            'status': 'NO_ACCEPTED_RESULTS' if not rows else 'PARTIAL'
            if gaps or coverage['matches_with_both'] < len(rows) else 'AVAILABLE',
            'as_of': cutoff.isoformat(), 'matches': rows, 'coverage': coverage, 'gaps': dict(gaps),
            'data_hash': sha(rows), 'dataset_manifest': dataset['manifest'], 'dataset': dataset,
            'identity_policy': 'EXACT_THE_STATS_IDS_NO_CROSS_PROVIDER_JOIN',
            'validation_limit': 'NOT_A_CERTIFIED_PROSPECTIVE_MODEL_OR_MARKET_BACKTEST',
            'external_requests': 0, 'production_writes': 0, 'live_decision_effect': False}
