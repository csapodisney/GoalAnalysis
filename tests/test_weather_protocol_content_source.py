"""Actual duplicate-response provenance; mutations are isolated synthetic negatives."""
import json
import socket
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from goal_analysis.learning.calibration_sources import Sources
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.temporal import project_times
from goal_analysis.storage import Database
from goal_analysis.storage import weather_protocols as storage
from goal_analysis.storage.canonical_registry import digest, encoded
from goal_analysis.storage.historical_results import HistoricalResults, identity
from goal_analysis.storage.http_observations import HttpObservations
from goal_analysis.storage.temporal import TemporalStore, _source_hash

FIXTURE = Path(__file__).parent/'fixtures/weather-protocols-20261023/two-version-result-source.json'
ORIGINAL = json.loads(FIXTURE.read_bytes())
NOW = datetime(2026, 10, 23, 14, tzinfo=UTC)
MODE = 'ORIGINAL_RESULT_CONTENT_WITH_UNVERIFIED_LATER_FETCH'


def versions(body, native='mt_155791009'):
    return sorted((r for r in body['tables']['historical_result_versions']
                   if json.loads(r['record_json'])['provider_id'] == native),
                  key=lambda r: r['observed_at'])


def receipt(body, row):
    return next(r for r in body['tables']['temporal_receipts']
                if r['namespace'] == 'result' and r['source_key'] == row['version_id'])


def reseal(body, row, mutate=None, available=None):
    """Build self-consistent forged test evidence, never edit the saved fixture."""
    witness = receipt(body, row)
    record = json.loads(row['record_json'])
    if mutate:
        mutate(record)
    row['record_json'] = encoded(record)
    row['version_id'] = digest(record)
    row['identity_sha256'] = identity(record)
    row['observed_at'] = record['observed_at']
    times = project_times('result', row, available_at=available or witness['available_at'])
    source_sha = _source_hash(row)
    witness.update(source_key=row['version_id'], source_sha256=source_sha,
                   receipt_id=digest(['result', row['version_id'], source_sha]),
                   times_json=encoded(times), **{k: times[k] for k in
                       ('observed_at', 'published_at', 'ingested_at', 'available_at')})


@pytest.fixture
def build(tmp_path):
    def install(change=None):
        body = deepcopy(ORIGINAL)
        if change:
            change(body)
        database = Database(tmp_path/'data/goal_analysis.sqlite3')
        database.initialize()
        HistoricalResults(database).initialize()
        TemporalStore(database, tmp_path/'data/raw').initialize()
        HttpObservations(database, tmp_path).initialize()
        for name, content in body['raw_files'].items():
            path = tmp_path/'data/raw'/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode('utf-8'))
        with database.transaction() as db:
            for table, rows in body['tables'].items():
                for row in rows:
                    db.execute('INSERT INTO '+table+' ('+','.join(row)+') VALUES ('+
                               ','.join('?' for _ in row)+')', tuple(row.values()))
        return {'root': tmp_path, 'database': database, 'body': body}
    return install


def resolve(env, *, native='mt_155791009', cutoff=NOW, first=False):
    selected = versions(env['body'], native)[0 if first else -1]
    path = env['database'].path.resolve()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        source = dict(db.execute('SELECT * FROM historical_result_versions WHERE version_id=?',
                                 (selected['version_id'],)).fetchone())
        return storage._result_content_source(Sources(env['root'], db), db, source, cutoff)


@pytest.mark.parametrize('native', ['mt_155791009', 'mt_200852660'])
def test_actual_duplicate_response_content_preserves_both_result_receipts(build, native):
    env = build()
    record, body, pointer, evidence = resolve(env, native=native)
    first, selected = versions(env['body'], native)
    raw = env['body']['tables']['raw_snapshots'][0]
    assert record == json.loads(selected['record_json'])
    assert evidence['mode'] == MODE
    assert evidence['later_fetch_verified'] is False
    assert evidence['selected_result']['version_id'] == selected['version_id']
    assert evidence['selected_result']['receipt_id'] == receipt(env['body'], selected)['receipt_id']
    assert evidence['original_result']['version_id'] == first['version_id']
    assert evidence['original_result']['receipt_id'] == receipt(env['body'], first)['receipt_id']
    assert pointer == {'content_hash': raw['content_hash'], 'source_url': raw['source_url'],
                       'observed_at': raw['fetched_at']}
    assert evidence['unverified_later_pointer'] == {
        'content_hash': record['source_sha256'], 'source_url': record['source_url'],
        'observed_at': record['observed_at']}
    assert evidence['raw']['source_url'] == raw['source_url']
    assert utc(evidence['raw']['times']['available_at']) == utc('2026-10-20T19:52:44.425003Z')
    exact = [r for r in body['data'] if r.get('id') == native]
    assert exact == [record['raw']]


def test_shared_reader_still_rejects_the_unproven_later_fetch(build):
    env = build()
    record = json.loads(versions(env['body'])[-1]['record_json'])
    pointer = {'content_hash': record['source_sha256'], 'source_url': record['source_url'],
               'observed_at': record['observed_at']}
    with closing(sqlite3.connect(env['database'].path)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        with pytest.raises(ValueError, match='RAW_SOURCE_ALIAS_URL_OR_CLOCK_MISMATCH'):
            Sources(env['root'], db).raw(pointer, NOW)
    assert resolve(env)[3]['mode'] == MODE


def test_exact_original_source_remains_the_direct_path(build):
    env = build()
    record, _, pointer, evidence = resolve(env, first=True)
    assert evidence['mode'] == 'EXACT_RESULT_SOURCE'
    assert evidence['selected_result']['version_id'] == versions(env['body'])[0]['version_id']
    assert pointer['source_url'] == record['source_url']
    assert 'unverified_later_pointer' not in evidence


@pytest.mark.parametrize('delta,passes', [(-1, False), (0, True)])
def test_actual_raw_availability_is_not_borrowed_from_earlier_result_receipts(build, delta, passes):
    env = build()
    cutoff = utc('2026-10-20T19:52:44.425003Z')+timedelta(microseconds=delta)
    if passes:
        assert resolve(env, cutoff=cutoff)[3]['mode'] == MODE
    else:
        with pytest.raises(ValueError):
            resolve(env, cutoff=cutoff)


@pytest.mark.parametrize('which', ['original', 'selected'])
def test_each_result_own_availability_is_required(build, which):
    def change(body):
        row = versions(body)[0 if which == 'original' else -1]
        reseal(body, row, available=(NOW+timedelta(microseconds=1)).isoformat())
    env = build(change)
    with pytest.raises(ValueError):
        resolve(env)


@pytest.mark.parametrize('which', ['original', 'selected'])
def test_each_missing_result_witness_is_rejected(build, which):
    def change(body):
        row = versions(body)[0 if which == 'original' else -1]
        body['tables']['temporal_receipts'].remove(receipt(body, row))
    env = build(change)
    with pytest.raises(ValueError):
        resolve(env)


@pytest.mark.parametrize('field', ['receipt_id', 'source_sha256', 'times_json'])
def test_corrupt_original_witness_is_not_skipped(build, field):
    def change(body):
        receipt(body, versions(body)[0])[field] = '{}' if field == 'times_json' else '0'*64
    env = build(change)
    with pytest.raises(ValueError):
        resolve(env)


@pytest.mark.parametrize('field,value', [
    ('home_goals', 99),
    ('source_url', 'https://api.thestatsapi.com/api/football/matches?competition_id=comp_0000'),
    ('source_sha256', '0'*64),
    ('observed_at', '2026-10-18T00:16:31.816715+00:00'),
])
def test_self_consistent_original_version_with_wrong_binding_is_rejected(build, field, value):
    def change(body):
        reseal(body, versions(body)[0], lambda r: r.update({field: value}))
    env = build(change)
    with pytest.raises(ValueError):
        resolve(env)


def test_self_consistent_original_raw_item_disagreement_is_rejected(build):
    def change(body):
        def mutate(record):
            record['raw']['source_test_marker'] = 'synthetic unmatched raw content'
        reseal(body, versions(body)[0], mutate)
    env = build(change)
    with pytest.raises(ValueError):
        resolve(env)


def test_conflicting_original_candidate_cannot_be_silently_filtered(build):
    def change(body):
        first = versions(body)[0]
        extra, witness = deepcopy(first), deepcopy(receipt(body, first))
        record = json.loads(extra['record_json'])
        record['home_goals'] += 1
        extra.update(record_json=encoded(record), version_id=digest(record))
        source_sha = _source_hash(extra)
        witness.update(source_key=extra['version_id'], source_sha256=source_sha,
                       receipt_id=digest(['result', extra['version_id'], source_sha]))
        body['tables']['historical_result_versions'].append(extra)
        body['tables']['temporal_receipts'].append(witness)
    env = build(change)
    with pytest.raises(ValueError):
        resolve(env)


def test_bounded_original_candidate_discovery_rejects_overflow(build):
    def change(body):
        first = versions(body)[0]
        for number in range(16):
            extra = deepcopy(first)
            record = json.loads(extra['record_json'])
            record['raw']['synthetic_candidate'] = number
            extra.update(record_json=encoded(record), version_id=digest(record))
            body['tables']['historical_result_versions'].append(extra)
    env = build(change)
    with pytest.raises(ValueError, match='BUDGET|LIMIT|AMBIGUOUS'):
        resolve(env)


@pytest.mark.parametrize('error', ['SOURCE_CHANGED_AFTER_WITNESS', 'UNWITNESSED_SOURCE',
    'RAW_HASH_OR_SIZE_MISMATCH', 'MISSING_EXACT_HTTP_SOURCE_OBSERVATION',
    'UNVERIFIED_HTTP_SOURCE', 'HTTP_SOURCE_ALIAS_NOT_AVAILABLE_AT_CUTOFF'])
def test_other_source_failures_never_trigger_independent_content_fallback(build, monkeypatch, error):
    env = build()
    calls = []
    def fail(self, pointer, cutoff):
        calls.append(deepcopy(pointer))
        raise ValueError(error)
    monkeypatch.setattr(Sources, 'raw', fail)
    with pytest.raises(ValueError, match=error):
        resolve(env)
    assert len(calls) == 1


def test_changed_physical_raw_bytes_cannot_use_the_alternative_result(build):
    env = build()
    raw = env['body']['tables']['raw_snapshots'][0]
    path = env['root']/'data/raw'/raw['relative_path']
    path.write_bytes(b'not the source payload')
    with pytest.raises(ValueError):
        resolve(env)


def test_provenance_resolution_is_readonly_and_has_no_network_or_new_receipts(build, monkeypatch):
    env = build()
    def forbidden(*args, **kwargs):
        raise AssertionError('source resolution must not call the network')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    with closing(sqlite3.connect(env['database'].path)) as db:
        before = list(db.iterdump())
    raw = env['body']['tables']['raw_snapshots'][0]
    path = env['root']/'data/raw'/raw['relative_path']
    before_raw = path.read_bytes()
    assert resolve(env)[3]['mode'] == MODE
    with closing(sqlite3.connect(env['database'].path)) as db:
        assert list(db.iterdump()) == before
    assert path.read_bytes() == before_raw


def test_same_clock_result_cannot_be_relabelled_as_earlier_content_evidence(build):
    def change(body):
        original, selected = versions(body)
        reseal(body, selected, lambda r: r.update(observed_at=original['observed_at']))
    env = build(change)
    with pytest.raises(ValueError, match='EARLIER_CONTENT_SOURCE_REQUIRED'):
        resolve(env)
