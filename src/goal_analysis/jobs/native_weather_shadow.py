"""Read-only native prematch weather context; no estimate, fit or ranking effect.

Keep an authenticated local forecast source distinct from its venue link and
from an unvalidated betting effect. Legacy IDs and reanalysis never substitute.
"""
import math
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs, urlsplit

from goal_analysis.agents.weather_dataset import _weather_record
from goal_analysis.agents.weather_expert import POLICY
from goal_analysis.agents.weather_native_dataset import _witness
from goal_analysis.jobs.xg_specialist_shadow import _targets
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.identity import native_id
from goal_analysis.normalization.training_datasets import snapshot
from goal_analysis.storage.source_observations import read_source_raw

VERSION = 'native-prematch-weather-context-shadow-v2'
MAX_BYTES = 64*1024*1024


def _number(value, bound):
    if type(value) not in (float, int) or not math.isfinite(value) or abs(value) > bound:
        raise ValueError('UNPROVEN_NATIVE_VENUE_COORDINATES')
    return value


def _forecast(row, fixture, cutoff, db):
    value = _weather_record(row, fixture, cutoff, 'FORECAST')
    import json

    stored = json.loads(row['snapshot_json'])
    if any(stored.get(k) != row[k] for k in ('fixture_id', 'provider', 'weather_kind', 'status',
            'observed_at', 'target_time', 'provider_issue_time', 'kickoff', 'source_url', 'raw_payload_hash')):
        raise ValueError('WEATHER_COLUMN_PAYLOAD_MISMATCH')
    if stored.get('time_basis') != 'FETCHED_AT' or stored['provider'] != 'open_meteo':
        raise ValueError('UNPROVEN_ACTUAL_FORECAST_RETRIEVAL')
    deadline = min(cutoff, utc(fixture['kickoff'])-timedelta(minutes=15))
    witness = _witness(db, 'weather', row['snapshot_id'], row, deadline)
    if not 0 <= (cutoff-utc(value['observed_at'])).total_seconds() <= POLICY['forecast_freshness_hours']*3600:
        raise ValueError('STALE_FORECAST')
    raw, weather = stored.get('raw_payload'), stored['weather']
    if not isinstance(raw, dict) or stored.get('raw_payload_hash') is None:
        raise ValueError('MISSING_PHYSICAL_FORECAST_PAYLOAD')
    url = urlsplit(stored['source_url']); query = parse_qs(url.query)
    if (url.scheme != 'https' or url.netloc != 'api.open-meteo.com' or url.path != '/v1/forecast'
            or query.get('timezone') != ['UTC']):
        raise ValueError('UNSUPPORTED_FORECAST_SOURCE')
    if raw.get('utc_offset_seconds') != 0:
        raise ValueError('UNPROVEN_FORECAST_UTC_HOURS')
    lat, lon = _number(weather.get('latitude'), 90), _number(weather.get('longitude'), 180)
    if any(len(query.get(k, [])) != 1 for k in ('latitude', 'longitude')):
        raise ValueError('AMBIGUOUS_FORECAST_REQUEST_COORDINATES')
    if float(query['latitude'][0]) != lat or float(query['longitude'][0]) != lon:
        raise ValueError('FORECAST_REQUEST_COORDINATE_CONFLICT')
    target = utc(value['target_time'])
    times = raw['hourly']['time']; text = target.strftime('%Y-%m-%dT%H:%M')
    if times.count(text) != 1 or utc(weather['forecast_time']) != target:
        raise ValueError('AMBIGUOUS_FORECAST_HOUR')
    index = times.index(text)
    for key, field, unit in (('temperature_c', 'temperature_2m', '°C'),
            ('precipitation_mm', 'precipitation', 'mm'), ('wind_speed_kmh', 'wind_speed_10m', 'km/h'),
            ('relative_humidity_pct', 'relative_humidity_2m', '%'),
            ('wind_gusts_kmh', 'wind_gusts_10m', 'km/h'), ('weather_code', 'weather_code', 'wmo code')):
        if weather.get(key) is not None and (
                raw['hourly_units'].get(field) != unit or raw['hourly'][field][index] != weather[key]):
            raise ValueError('FORECAST_VALUE_OR_UNIT_CONFLICT')
    value.update(latitude=lat, longitude=lon, provider_grid_latitude=_number(raw.get('latitude'), 90),
                 provider_grid_longitude=_number(raw.get('longitude'), 180),
                 provider_issue_time=stored.get('provider_issue_time'))
    return value, witness


def inspect_native_weather(root, fixtures, *, as_of, _deadline=None):
    """Exact IDs and locally witnessed bytes only; never fetch or admit sources."""
    if _deadline is not None and monotonic() >= _deadline:
        raise ValueError('WEATHER_FEATURE_TIME_BUDGET')
    if not isinstance(fixtures, list) or len(fixtures) > 1000:
        raise ValueError('BOUNDED_WEATHER_FIXTURES_REQUIRED')
    ids = [f['fixture_id'] for f in fixtures]
    if len(set(ids)) != len(ids) or any(f['fixture_id'] != 'the_stats_api:'+native_id(
            f['provider_fixture_id'], 'mt') for f in fixtures):
        raise ValueError('UNIQUE_NATIVE_WEATHER_IDENTITIES_REQUIRED')
    root, cutoff = Path(root).resolve(), utc(as_of)
    targets, target_proofs, target_gaps = _targets(root, {f['fixture_id']: f for f in fixtures}, cutoff)
    rows, issues, lineage = [], Counter(), []
    path = root/'data/goal_analysis.sqlite3'
    sources = {}
    if path.exists() and ids:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'sports_weather_snapshots' in tables:
                marks = ','.join('?' for _ in ids)
                records = [dict(r) for r in db.execute(f'''SELECT * FROM sports_weather_snapshots
                    WHERE fixture_id IN ({marks}) AND weather_kind='FORECAST' AND observed_at<=?
                    ORDER BY fixture_id,observed_at DESC,snapshot_id LIMIT 2001''',
                    [*ids, cutoff.isoformat(timespec='microseconds')])]
                if len(records) > 2000 or sum(len(r['snapshot_json'].encode()) for r in records) > MAX_BYTES:
                    raise ValueError('NATIVE_WEATHER_SOURCE_BUDGET_EXCEEDED')
                for r in records:
                    group = sources.setdefault(r['fixture_id'], [])
                    if not group or r['observed_at'] == group[0]['observed_at']:
                        group.append(r)
            for fixture in fixtures:
                if _deadline is not None and monotonic() >= _deadline:
                    raise ValueError('WEATHER_FEATURE_TIME_BUDGET')
                fid = fixture['fixture_id']
                row = {'fixture_id': fid, 'kickoff': fixture['kickoff'], 'status': 'UNKNOWN',
                    'reason': 'MISSING_ARCHIVED_NATIVE_FORECAST', 'forecast': None, 'forecast_receipt': None,
                    'target_receipt': target_proofs.get(fid), 'venue_location': None,
                    'target_is_fresh_and_verified': fid in targets, 'probability_adjustment': 0,
                    'ranking_multiplier': 1., 'live_decision_effect': False}
                try:
                    candidates = sources.get(fid, [])
                    if not candidates:
                        raise ValueError('MISSING_ARCHIVED_NATIVE_FORECAST')
                    # Same-time divergent snapshots are not hash-order winners.
                    if len(candidates) != 1:
                        raise ValueError('AMBIGUOUS_LATEST_NATIVE_FORECAST')
                    value, proof = _forecast(candidates[0], fixture, cutoff, db)
                    row.update(forecast=value, forecast_receipt=proof)
                    lineage.append({'namespace': 'weather', 'source_key': candidates[0]['snapshot_id'], **proof})
                    if fid not in targets:
                        raise ValueError('UNVERIFIED_FRESH_NATIVE_TARGET')
                    from goal_analysis.jobs.stadium_locations_shadow import fixture_location

                    location = fixture_location(root, fixture, as_of=cutoff)
                    if location is not None:
                        venue = location['payload'];lat,lon = venue['latitude'],venue['longitude']
                        target_proof = {'reviewed_location':location}
                        basis = 'EXACT_MATCH_AND_REVIEWED_STADIUM_WGS84'
                    else:
                        source = fixture['fixture_source']
                        meta = db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?', (source['content_hash'],)).fetchone()
                        body, target_proof = read_source_raw(db, dict(meta), root, cutoff, source)
                        data = body['data'] if isinstance(body['data'], list) else [body['data']]
                        match = next(m for m in data if m['id'] == fixture['provider_fixture_id'])
                        venue = match.get('venue') or {}
                        lat, lon = _number(venue.get('latitude'), 90), _number(venue.get('longitude'), 180)
                        basis = 'EXPLICIT_NATIVE_RAW_COORDINATES'
                    if (lat, lon) != (value['latitude'], value['longitude']):
                        raise ValueError('NATIVE_VENUE_FORECAST_COORDINATE_CONFLICT')
                    row.update(status='FORECAST_CONTEXT_VERIFIED_SHADOW', reason='CONTEXT_ONLY_EFFECT_UNVALIDATED',
                        venue_location={'latitude': lat, 'longitude': lon, 'name': venue.get('native_name',venue.get('name')),
                            'city': venue.get('native_city',venue.get('city')), 'basis': basis,
                            'provider_grid_is_not_stadium_measurement': True})
                    lineage.append({'namespace': 'reviewed_stadium_location' if location else 'raw', **target_proof})
                except (ValueError, KeyError, TypeError, OSError, StopIteration) as error:
                    row['reason'] = str(error) if isinstance(error, ValueError) else type(error).__name__
                    issues[row['reason']] += 1
                rows.append(row)
    if not rows:
        rows = [{'fixture_id': f['fixture_id'], 'kickoff': f['kickoff'], 'status': 'UNKNOWN',
                 'reason': 'MISSING_WEATHER_DATABASE', 'forecast': None, 'forecast_receipt': None,
                 'target_receipt': None, 'venue_location': None, 'target_is_fresh_and_verified': False,
                 'probability_adjustment': 0, 'ranking_multiplier': 1.,
                 'live_decision_effect': False} for f in fixtures]
        issues['MISSING_WEATHER_DATABASE'] += len(rows)
    recipe = {'version': VERSION, 'implementation_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
              'mode': 'SHADOW', 'exact_native_venue_coordinates_required': True,
              'forecast_freshness_hours': POLICY['forecast_freshness_hours'], 'probability_adjustment': 0}
    dataset = snapshot(rows, role='native_daily_weather_context_shadow', as_of=cutoff, recipe=recipe, lineage=lineage)
    return {'version': VERSION, 'mode': 'SHADOW', 'as_of': cutoff.isoformat(), 'rows': rows, 'dataset': dataset,
        'issues': dict(issues), 'target_issues': target_gaps, 'external_requests': 0, 'production_writes': 0,
        'live_decision_effect': False, 'probability_adjustment': 0, 'ranking_multiplier': 1.,
        'summary': {'fixture_contexts': len(rows), 'verified_forecast_sources': sum(r['forecast'] is not None for r in rows),
                    'verified_venue_forecast_contexts': sum(r['status']=='FORECAST_CONTEXT_VERIFIED_SHADOW' for r in rows)}}
