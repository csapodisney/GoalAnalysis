"""Source semantics on an isolated actual N4 row; no synthetic model benefit."""
import json
import socket
import sqlite3
from datetime import timedelta
from hashlib import sha256

import pytest
from reanalysis_sources import ORIGINAL, install, original_venue_bytes

from goal_analysis.agents import weather_native_dataset as native
from goal_analysis.normalization.canonical import utc


@pytest.fixture
def source(tmp_path, monkeypatch):
    original_venue_bytes(monkeypatch, tmp_path)
    return tmp_path


def load(root, cutoff=None):
    return native.load_native_weather_dataset(root, as_of=cutoff or ORIGINAL['as_of'])


def reject(view, reason):
    assert view['coverage']['completed_matches'] == 1
    assert view['coverage']['matches_with_reanalysis'] == 0
    assert any(reason in key for key in view['gaps']), view['gaps']
    assert view['external_requests'] == view['production_writes'] == 0
    assert not view['live_decision_effect']


def change_path(path, value):
    def change(stored):
        node = stored
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return change


def test_actual_archived_source_passes_without_network_writes_or_future_release(source, monkeypatch):
    body, database = install(source)
    # SQLite read-only WAL readers update shared-memory reader locks. They do
    # not change the main database, transaction log or archived source bytes.
    before = {p: sha256(p.read_bytes()).hexdigest() for p in source.rglob('*')
              if p.is_file() and not p.name.endswith('-shm')}
    connect = sqlite3.connect

    def readonly(path, *args, **kwargs):
        assert '?mode=ro' in str(path) and kwargs.get('uri') is True
        return connect(path, *args, **kwargs)

    def no_network(*args, **kwargs):
        pytest.fail('Source reader attempted a network request')

    monkeypatch.setattr(sqlite3, 'connect', readonly)
    monkeypatch.setattr(socket, 'socket', no_network)
    view = load(source)
    assert view == load(source)
    assert view['coverage']['matches_with_reanalysis'] == 1
    weather = view['matches'][0]['weather']
    assert weather['temperature_c'] == 9.4 and weather['wind_gusts_kmh'] is None
    proof = weather['source_validation']
    assert proof['grid_is_not_stadium_measurement'] is True
    assert proof['venue_available_at'].startswith('2026-10-22T23:33:32')
    assert utc(proof['venue_available_at']) < utc(proof['weather_available_at'])
    assert weather['historical_use'] == 'RETROSPECTIVE_REANALYSIS_NOT_PREMATCH_KNOWLEDGE'
    assert proof['bound_result_receipt_id'] == json.loads(body['tables']['sports_weather_snapshots'][0]['snapshot_json'])['metadata']['result_receipt_id']
    assert before == {p: sha256(p.read_bytes()).hexdigest() for p in source.rglob('*')
                      if p.is_file() and not p.name.endswith('-shm')}
    assert database.path.exists() and not view['live_decision_effect']


@pytest.mark.parametrize('path,value,reason', [
    (('weather', 'temperature_c'), 25, 'RAW_UNIT_OR_VALUE_MISMATCH'),
    (('weather', 'relative_humidity_pct'), 11, 'RAW_UNIT_OR_VALUE_MISMATCH'),
    (('weather', 'precipitation_mm'), 8, 'RAW_UNIT_OR_VALUE_MISMATCH'),
    (('weather', 'wind_speed_kmh'), 120, 'RAW_UNIT_OR_VALUE_MISMATCH'),
    (('weather', 'weather_code'), 95, 'RAW_UNIT_OR_VALUE_MISMATCH'),
    (('weather', 'temperature_c'), None, 'RAW_UNIT_OR_VALUE_MISMATCH'),
    (('weather', 'units', 'temperature_2m'), 'F', 'RAW_UNIT_OR_VALUE_MISMATCH'),
    (('raw_payload', 'hourly_units', 'temperature_2m'), 'F', 'RAW_UNIT_OR_VALUE_MISMATCH'),
    (('raw_payload', 'hourly_units', 'wind_speed_10m'), 'm/s', 'RAW_UNIT_OR_VALUE_MISMATCH'),
    (('raw_payload', 'hourly_units', 'precipitation'), 'inch', 'RAW_UNIT_OR_VALUE_MISMATCH'),
    (('raw_payload', 'hourly_units', 'relative_humidity_2m'), 'fraction', 'RAW_UNIT_OR_VALUE_MISMATCH'),
    (('raw_payload', 'hourly_units', 'weather_code'), 'unknown', 'RAW_UNIT_OR_VALUE_MISMATCH'),
    (('raw_payload', 'hourly_units', 'time'), 'unixtime', 'UTC_HOURLY_SHAPE_MISMATCH'),
    (('raw_payload', 'utc_offset_seconds'), 3600, 'UTC_HOURLY_SHAPE_MISMATCH'),
    (('raw_payload', 'utc_offset_seconds'), False, 'UTC_HOURLY_SHAPE_MISMATCH'),
    (('raw_payload', 'timezone'), 'Europe/Paris', 'UTC_HOURLY_SHAPE_MISMATCH'),
    (('raw_payload', 'hourly'), [], 'UTC_HOURLY_SHAPE_MISMATCH'),
    (('raw_payload', 'hourly_units'), None, 'UTC_HOURLY_SHAPE_MISMATCH'),
    (('weather', 'units'), None, 'UTC_HOURLY_SHAPE_MISMATCH'),
    (('weather', 'model'), 'best_match', 'PROVIDER_MODEL_OR_TIME_CONTRACT'),
    (('weather', 'provider'), 'other', 'PROVIDER_MODEL_OR_TIME_CONTRACT'),
    (('weather', 'time_basis'), 'PROVIDER_ISSUED', 'PROVIDER_MODEL_OR_TIME_CONTRACT'),
    (('weather', 'provider_issue_time'), '2026-10-17T00:00:00+00:00', 'PROVIDER_MODEL_OR_TIME_CONTRACT'),
    (('metadata',), [], 'STADIUM_METADATA_MISSING'),
    (('metadata', 'stadium_location'), None, 'STADIUM_METADATA_MISSING'),
    (('metadata', 'historical_use'), 'PREMATCH', 'HISTORICAL_USE_UNPROVEN'),
    (('metadata', 'result_version_id'), 'absent', 'BOUND_RESULT_MISSING'),
    (('metadata', 'result_receipt_id'), 'other', 'RESULT_SOURCE_IDENTITY_MISMATCH'),
    (('metadata', 'stadium_location', 'source_receipts'), [], 'HTTP_WITNESS_REQUIRED'),
    (('metadata', 'stadium_location', 'source_receipts', -1, 'kind'), 'CLAIM', 'HTTP_WITNESS_REQUIRED'),
    (('metadata', 'stadium_location', 'source_receipts', -1, 'observation_id'), '0'*32, 'EVENT_STADIUM_UNPROVEN'),
    (('metadata', 'stadium_location', 'record_id'), 'other', 'EVENT_STADIUM_UNPROVEN'),
    (('metadata', 'stadium_location', 'payload', 'latitude'), 0, 'EVENT_STADIUM_UNPROVEN'),
    (('weather', 'latitude'), 0, 'REVIEWED_STADIUM_COORDINATE_MISMATCH'),
    (('weather', 'venue_name'), 'Home ground guess', 'REVIEWED_STADIUM_COORDINATE_MISMATCH'),
    (('weather', 'venue_city'), 'Other', 'REVIEWED_STADIUM_COORDINATE_MISMATCH'),
    (('weather', 'requested_target_time'), '2026-10-17T16:00:00+00:00', 'UTC_TARGET_OR_PUBLICATION_LAG'),
    (('weather', 'provider_grid_latitude'), 0, 'GRID_PROVENANCE_MISMATCH'),
    (('weather', 'provider_grid_longitude'), 0, 'GRID_PROVENANCE_MISMATCH'),
    (('raw_payload', 'latitude'), 0, 'GRID_PROVENANCE_MISMATCH'),
    (('weather', 'wind_gusts_kmh'), 15, 'UNSUPPORTED_GUSTS_NOT_UNKNOWN'),
    (('weather', 'unsupported_fields'), [], 'UNSUPPORTED_GUSTS_NOT_UNKNOWN'),
])
def test_rehashed_and_rewitnessed_semantic_mutations_rejected(source, path, value, reason):
    install(source, change=change_path(path, value))
    reject(load(source), reason)


@pytest.mark.parametrize('case,reason', [
    ('duplicate', 'UTC_HOURLY_SHAPE_MISMATCH'), ('missing', 'TARGET_HOUR_MISSING'),
    ('shifted-day', 'UTC_HOUR_MISMATCH'), ('minute', 'UTC_HOUR_MISMATCH'),
    ('unaligned', 'RAW_UNIT_OR_VALUE_MISMATCH'), ('missing-unit', 'RAW_UNIT_OR_VALUE_MISMATCH'),
    ('bool-value', 'RAW_UNIT_OR_VALUE_MISMATCH'), ('fraction-wmo', 'RAW_UNIT_OR_VALUE_MISMATCH'),
])
def test_hour_alignment_and_complete_units_are_required(source, case, reason):
    def change(s):
        h = s['raw_payload']['hourly']; t = h['time']; target = utc(s['target_time']).strftime('%Y-%m-%dT%H:%M')
        i = t.index(target)
        if case == 'duplicate': t[0] = t[1]
        elif case == 'missing':
            for values in h.values(): values.pop(i)
        elif case == 'shifted-day': t[0] = '2026-10-16T00:00'
        elif case == 'minute': t[0] = '2026-10-17T00:01'
        elif case == 'unaligned': h['temperature_2m'].pop()
        elif case == 'missing-unit': s['raw_payload']['hourly_units'].pop('temperature_2m')
        elif case == 'bool-value': h['temperature_2m'][i] = True
        elif case == 'fraction-wmo':
            h['weather_code'][i] = s['weather']['weather_code'] = 1.5
    install(source, change=change)
    reject(load(source), reason)


@pytest.mark.parametrize('append', ['&latitude=0', '&extra=1', '&models=best_match', '#fake'])
def test_exact_single_value_query_is_required(source, append):
    def change(s):
        s['source_url'] += append
        s['weather']['source_url'] = s['source_url']
    install(source, change=change)
    reject(load(source), 'EXACT_REQUEST_QUERY_MISMATCH')


@pytest.mark.parametrize('field,value', [
    ('resource_type', 'forecast'), ('source_url', 'https://other.invalid'),
    ('fetched_at', '2026-10-23T11:14:39.745167+00:00'),
    ('size_bytes', 500_001), ('size_bytes', 0),
])
def test_original_raw_metadata_and_deduplicated_first_clock_cannot_be_forged(source, field, value):
    install(source, raw_metadata=lambda r: r.update({field: value}))
    reject(load(source), 'RAW_SOURCE_URL_OR_RECEIPT_CLOCK_MISMATCH')


@pytest.mark.parametrize('case', ['missing-raw-row', 'missing-http-witness', 'future-http-witness', 'future-result'])
def test_source_receipts_must_exist_at_weather_own_availability(source, case):
    def change(body):
        t = body['tables']
        if case == 'missing-raw-row':
            t['raw_snapshots'] = [r for r in t['raw_snapshots'] if r['provider'] != 'open_meteo']
        elif case == 'missing-http-witness': t['the_stats_http_witnesses'] = []
        elif case == 'future-http-witness':
            t['the_stats_http_witnesses'][0]['available_at'] = '2026-10-24T00:00:00+00:00'
        else:
            next(r for r in t['temporal_receipts'] if r['namespace'] == 'result')['available_at'] = '2026-10-24T00:00:00+00:00'
    install(source, bundle_change=change)
    view = load(source)
    if case == 'future-result':
        assert view['coverage']['completed_matches'] == 0
    else:
        reject(view, 'RAW_ARCHIVE_MISSING' if case == 'missing-raw-row' else 'EVENT_STADIUM_UNPROVEN')


@pytest.mark.parametrize('case', ['missing', 'corrupt', 'outside'])
def test_original_physical_bytes_are_required(source, case):
    body, database = install(source)
    row = next(r for r in body['tables']['raw_snapshots'] if r['provider'] == 'open_meteo')
    p = source/'data/raw'/row['relative_path']
    if case == 'missing': p.unlink()
    elif case == 'corrupt': p.write_bytes(b'x'*row['size_bytes'])
    else:
        with sqlite3.connect(database.path) as db:
            db.execute("UPDATE raw_snapshots SET relative_path='../../outside.json' WHERE provider='open_meteo'")
    reject(load(source), 'RAW_ARCHIVE_BYTES_MISMATCH' if case == 'corrupt' else 'RAW_ARCHIVE_PATH_OR_SIZE_MISMATCH')


def test_real_weather_availability_is_not_backdated_to_historical_match(source):
    body, _ = install(source)
    receipt = next(r for r in body['tables']['temporal_receipts'] if r['namespace'] == 'weather')
    at = utc(receipt['available_at'])
    assert load(source, at-timedelta(microseconds=1))['coverage']['matches_with_reanalysis'] == 0
    assert load(source, at)['coverage']['matches_with_reanalysis'] == 1


def test_reanalysis_source_budget_aborts_instead_of_silent_partial(source, monkeypatch):
    install(source)
    monkeypatch.setattr(native, 'MAX_REANALYSIS_SOURCES', 0)
    with pytest.raises(ValueError, match='NATIVE_REANALYSIS_SOURCE_BUDGET_EXCEEDED'):
        load(source)


def test_reanalysis_raw_byte_bound_rejects_source(source, monkeypatch):
    install(source)
    monkeypatch.setattr(native, 'MAX_REANALYSIS_RAW_BYTES', 100)
    reject(load(source), 'RAW_SOURCE_URL_OR_RECEIPT_CLOCK_MISMATCH')
