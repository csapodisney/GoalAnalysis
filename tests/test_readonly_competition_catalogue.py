"""Readonly competition catalogue: public TESTONLY SQLite/HTTP fixtures, no live provider."""
import http.client
import json
import socket
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from goal_analysis import portfolio_dashboard as DASHBOARD
from goal_analysis.jobs import readonly_competitions as CANDIDATE

ROOT = Path(DASHBOARD.__file__).resolve().parents[2]
PRODUCTION = ROOT
TESTONLY_BASE = None
ATTEMPTS = []


def config():
    identifiers = (39, 40, 45, 48, 78, 79, 80, 81, 140, 143, 135, 136, 137,
                   61, 62, 66, 88, 89, 94, 2, 3, 848, 10, 5, 32)
    return {'leagues': [{'api_football_id': identifier, 'competition_id': f'TESTONLY_{identifier}',
        'season': 2026, 'competition_type': 'NATIONAL' if identifier in {5, 10, 32} else 'LEAGUE'}
        for identifier in identifiers]}


def checked_root(path, *, create=False):
    root = Path(path).resolve()
    if root == PRODUCTION or root.is_relative_to(PRODUCTION) or PRODUCTION.is_relative_to(root):
        raise ValueError('PRODUCTION_ROOT_REJECTED_BEFORE_WRITER_OR_CONSTRUCTOR')
    if TESTONLY_BASE is None or root != TESTONLY_BASE or not root.name.startswith('a01ro'):
        raise ValueError('EXPLICIT_SHORT_TESTONLY_ROOT_REQUIRED')
    marker = root / 'ARTHUR_TESTONLY_SCOPE.json'
    expected = {'scope': 'readonly-competition-catalogue-contract', 'TESTONLY': True, 'root': str(root)}
    if create:
        with marker.open('x', encoding='utf-8') as stream:
            json.dump(expected, stream)
    elif json.loads(marker.read_text('utf-8')) != expected:
        raise ValueError('EXACT_TESTONLY_MARKER_REQUIRED')
    return root


def writer_target(path):
    target = Path(path).resolve()
    if target == PRODUCTION or target.is_relative_to(PRODUCTION) or PRODUCTION.is_relative_to(target):
        raise ValueError('PRODUCTION_OR_OUT_OF_SCOPE_WRITER_REJECTED')
    base = checked_root(TESTONLY_BASE)
    if not target.is_relative_to(base) or target.parent != base:
        raise ValueError('PRODUCTION_OR_OUT_OF_SCOPE_WRITER_REJECTED')
    allowed = {'fresh', 'stale', 'corrupt', 'missing', 'alias_nonempty', 'alias_empty', 'alias_stale',
               'alias_url_bad', 'alias_clock_bad', 'alias_hash_bad', 'alias_unauth', 'alias_missing',
               'alias_malformed', 'alias_http201'}
    if target.name not in allowed:
        raise ValueError('EXPLICIT_TESTONLY_CASE_REQUIRED')
    return target


def hashes(root):
    return {path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def seed(target, *, age=timedelta(), corrupt=False, missing=False):
    target = writer_target(target)  # Must precede directory, schema and SQLite constructor.
    target.mkdir(exist_ok=False)
    (target / 'config.json').write_text(json.dumps(config()), encoding='utf-8')
    (target / 'quota-marker.json').write_text('{"TESTONLY_unchanged_quota":true}', encoding='utf-8')
    if missing:
        return target
    (target / 'data/raw').mkdir(parents=True)
    alias = target.name.startswith('alias_')
    if target.name == 'alias_stale':
        age = timedelta(hours=2)
    observed = datetime.now(UTC) - age - timedelta(seconds=1)
    day = datetime.now(ZoneInfo('Europe/Berlin')).date()
    query = CANDIDATE._query(day, 1)
    url = CANDIDATE.BASE_URL + '?' + CANDIDATE.urlencode(query)
    body = {'meta': {'page': 1, 'per_page': 100, 'total_pages': 1},
            'data': [{'id': 'mt_1', 'competition_id': 'comp_3039',
                      'utc_date': day.isoformat() + 'T15:00:00+00:00', 'status': 'scheduled',
                      'home_team': {'id': 'tm_1', 'name': 'EXPLICIT TESTONLY Home'},
                      'away_team': {'id': 'tm_2', 'name': 'EXPLICIT TESTONLY Away'}}]}
    if target.name == 'alias_empty':
        body.update(meta={'page': 1, 'per_page': 100, 'total_pages': 0}, data=[])
    raw = json.dumps(body, sort_keys=True, separators=(',', ':')).encode()
    digest = sha256(raw).hexdigest()
    filename = digest + '.json'
    (target / 'data/raw' / filename).write_bytes(raw)
    source = {'provider': 'the_stats_api', 'source_url': url, 'observed_at': observed.isoformat(),
              'content_hash': digest, 'published_at': None}
    key = json.dumps(['/football/matches', query], sort_keys=True, separators=(',', ':'))
    raw_url = CANDIDATE.BASE_URL + '?' + CANDIDATE.urlencode({**query, 'utc_offset': '+00:00'}) if alias else url
    raw_time = observed - timedelta(hours=1) if alias else observed
    with closing(sqlite3.connect(target / 'data/goal_analysis.sqlite3')) as db:
        db.row_factory = sqlite3.Row
        db.executescript((ROOT / 'src/goal_analysis/storage/schema.sql').read_text('utf-8'))
        db.execute('INSERT INTO raw_snapshots VALUES(?,?,?,?,?,?,?,?)',
                   (digest, 'the_stats_api', 'daily_context', raw_time.isoformat(),
                    'application/json', filename, len(raw), raw_url))
        if alias:
            from goal_analysis.storage.canonical_registry import digest as row_digest
            from goal_analysis.storage.canonical_registry import encoded
            from goal_analysis.storage.http_observations import VERSION as HTTP_VERSION
            from goal_analysis.storage.temporal import _source_hash

            db.executescript('''CREATE TABLE the_stats_http_observations (
                response_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
                source_url TEXT NOT NULL, observed_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
                raw_metadata_sha256 TEXT NOT NULL, http_status INTEGER NOT NULL, authenticated INTEGER NOT NULL);
                CREATE TABLE the_stats_http_witnesses (
                response_id TEXT PRIMARY KEY REFERENCES the_stats_http_observations(response_id),
                receipt_id TEXT NOT NULL UNIQUE, observation_sha256 TEXT NOT NULL,
                available_at TEXT NOT NULL, times_json TEXT NOT NULL);''')
            meta = dict(db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?', (digest,)).fetchone())
            response_id = sha256(target.name.encode()).hexdigest()[:32]
            stamp = observed - timedelta(seconds=1) if target.name == 'alias_clock_bad' else observed
            record = {'response_id': response_id,
                'content_hash': '0' * 64 if target.name == 'alias_hash_bad' else digest,
                'source_url': raw_url if target.name == 'alias_url_bad' else url,
                'observed_at': stamp.isoformat(), 'ingested_at': observed.isoformat(),
                'raw_metadata_sha256': _source_hash(meta),
                'http_status': 201 if target.name == 'alias_http201' else 200,
                'authenticated': 0 if target.name == 'alias_unauth' else 1}
            record_hash = row_digest(record)
            receipt_id = row_digest([HTTP_VERSION, response_id, record_hash])
            available = observed.isoformat()
            times = {'event_time': None, 'event_time_kind': 'UNKNOWN', 'published_at': None,
                'observed_at': record['observed_at'], 'ingested_at': record['ingested_at'],
                'available_at': available, 'availability_basis': 'SOURCE_PRESENCE_UPPER_BOUND'}
            db.execute('INSERT INTO the_stats_http_observations VALUES(?,?,?,?,?,?,?,?)', tuple(record.values()))
            if target.name != 'alias_missing':
                db.execute('INSERT INTO the_stats_http_witnesses VALUES(?,?,?,?,?)',
                    (response_id, '0' * 64 if target.name == 'alias_malformed' else receipt_id,
                     record_hash, available, encoded(times)))
            source.update(http_observation_id=response_id, http_receipt_id=receipt_id,
                          http_observation_status='VERIFIED')
        value = json.dumps({'source': source}, sort_keys=True, separators=(',', ':'))
        db.execute('INSERT INTO cache_entries VALUES(?,?,?,?,?,?)',
                   (CANDIDATE.NAMESPACE, key, value, observed.isoformat(),
                    (observed + timedelta(minutes=5)).isoformat(),
                    '0' * 64 if corrupt else sha256(value.encode()).hexdigest()))
        db.commit()
    return target


@pytest.fixture(scope='session')
def cases(tmp_path_factory):
    global TESTONLY_BASE
    TESTONLY_BASE = tmp_path_factory.mktemp('a01ro').resolve()
    base = checked_root(TESTONLY_BASE, create=True)
    names = ('fresh', 'stale', 'corrupt', 'missing', 'alias_nonempty', 'alias_empty', 'alias_stale',
             'alias_url_bad', 'alias_clock_bad', 'alias_hash_bad', 'alias_unauth', 'alias_missing',
             'alias_malformed', 'alias_http201')
    return {name: seed(base / name, age=timedelta(hours=2) if name == 'stale' else timedelta(),
                       corrupt=name == 'corrupt', missing=name == 'missing')
            for name in names}


@contextmanager
def passive_guards(root, allowed_port=None):
    root = writer_target(root)
    real_connect, real_socket_connect, real_getaddrinfo = sqlite3.connect, socket.socket.connect, socket.getaddrinfo
    reads = []

    def forbidden(*_args, **_kwargs):
        ATTEMPTS.append('FORBIDDEN_EXTERNAL_PROVIDER_INITIALIZATION_OR_WRITE')
        raise AssertionError(ATTEMPTS[-1])

    def readonly_connect(database, **kwargs):
        expected = (root / 'data/goal_analysis.sqlite3').as_uri() + '?mode=ro'
        if str(database) != expected or kwargs != {'uri': True, 'timeout': 2}:
            return forbidden()
        reads.append(str(database))
        connection = real_connect(database, **kwargs)

        def authorize(action, first, second, _database, _trigger):
            if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_TRANSACTION and first == 'BEGIN':
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_PRAGMA and first == 'query_only' and second == 'ON':
                return sqlite3.SQLITE_OK
            ATTEMPTS.append('FORBIDDEN_SQL_WRITE_OR_SCHEMA_OPERATION')
            return sqlite3.SQLITE_DENY
        connection.set_authorizer(authorize)
        return connection

    def local_connect(sock, address):
        if allowed_port is None or address != ('127.0.0.1', allowed_port):
            return forbidden()
        return real_socket_connect(sock, address)

    def local_resolution(host, port, *args, **kwargs):
        if allowed_port is None or host != '127.0.0.1' or port != allowed_port:
            return forbidden()
        return real_getaddrinfo(host, port, *args, **kwargs)

    from contextlib import ExitStack
    with ExitStack() as guards:
        guards.enter_context(patch('sqlite3.connect', side_effect=readonly_connect))
        guards.enter_context(patch('socket.socket.connect', local_connect))
        guards.enter_context(patch('socket.socket.connect_ex', side_effect=forbidden))
        guards.enter_context(patch('socket.getaddrinfo', side_effect=local_resolution))
        for name in ('urllib.request.urlopen', 'subprocess.Popen',
                     'goal_analysis.storage.db.Database.__init__',
                     'goal_analysis.storage.db.Database.initialize',
                     'goal_analysis.storage.cache.CacheStore.set',
                     'goal_analysis.storage.cache.CacheStore.delete',
                     'goal_analysis.storage.cache.CacheStore.purge_expired',
                     'goal_analysis.storage.provider_quota.SharedQuota.__init__',
                     'goal_analysis.storage.provider_quota.SharedQuota.allowance',
                     'goal_analysis.jobs.the_stats_context.Resources.__init__',
                     'goal_analysis.providers.the_stats_primary.TheStatsPrimaryClient.__init__',
                     'goal_analysis.providers.the_stats_api.TheStatsApiClient.__init__'):
            guards.enter_context(patch(name, side_effect=forbidden))
        yield reads
    assert not ATTEMPTS


def test_production_root_rejected_before_writer_schema_or_constructor():
    with pytest.raises(ValueError, match='PRODUCTION_OR_OUT_OF_SCOPE'):
        seed(PRODUCTION)
    with pytest.raises(ValueError, match='PRODUCTION_ROOT_REJECTED'):
        checked_root(PRODUCTION, create=True)


@pytest.mark.parametrize('name,status,cache', [
    ('fresh', 'CACHED_SNAPSHOT', 'VALID'),
    ('stale', 'STALE_SNAPSHOT', 'VALID'),
    ('corrupt', 'CONFIGURED_FALLBACK', 'CORRUPT_CACHE_ENVELOPE'),
    ('missing', 'CONFIGURED_FALLBACK', 'MISSING_DB')])
def test_genuine_sqlite_readonly_query_and_fallback_bytes_exact(cases, name, status, cache):
    root = cases[name]
    before = hashes(root)
    with passive_guards(root) as reads:
        result = CANDIDATE.read_competition_catalogue(root, config(),
            datetime.now(ZoneInfo('Europe/Berlin')).date(), as_of=datetime.now(UTC))
    assert result['status'] == status and result['cache_status'] == cache
    assert len(result['competitions']) == 25
    assert next(row for row in result['competitions'] if row['id'] == 32)['fixture_count'] is None
    english = next(row for row in result['competitions'] if row['id'] == 39)
    assert english['fixture_count'] == (1 if name in {'fresh', 'stale'} else None)
    assert len(reads) == (0 if name == 'missing' else 1)
    assert before == hashes(root)
    if name == 'missing':
        assert not (root / 'data').exists()


def test_crossdate_real_cache_is_unknown_without_acquisition(cases):
    root = cases['fresh']; before = hashes(root)
    with passive_guards(root):
        result = CANDIDATE.read_competition_catalogue(root, config(),
            datetime.now(ZoneInfo('Europe/Berlin')).date() + timedelta(days=1), as_of=datetime.now(UTC))
    assert result['cache_status'] == 'MISSING_CALENDAR_CACHE_PAGE'
    assert len(result['competitions']) == 25 and all(x['fixture_count'] is None for x in result['competitions'])
    assert before == hashes(root)


@pytest.mark.parametrize('name,status', [('fresh', 'CACHED_SNAPSHOT'), ('stale', 'STALE_SNAPSHOT'),
                                       ('corrupt', 'CONFIGURED_FALLBACK'), ('missing', 'CONFIGURED_FALLBACK')])
def test_actual_local_ephemeral_get_uses_real_ro_sqlite_and_never_command(cases, monkeypatch, name, status):
    from http.server import ThreadingHTTPServer
    root = cases[name]; before = hashes(root)
    state = object.__new__(DASHBOARD.ArthurState)
    state.root, state.config_path = root, root / 'config.json'
    state.competition_cache = {'TESTONLY': 'UNCHANGED'}
    monkeypatch.setitem(__import__('sys').modules, 'goal_analysis.jobs.readonly_competitions', CANDIDATE)
    monkeypatch.setattr(state, 'competitions', lambda *_args: (_ for _ in ()).throw(AssertionError('MUTATING_COMMAND_REACHED')))
    server = ThreadingHTTPServer(('127.0.0.1', 0), DASHBOARD.make_handler(state, 'EXPLICIT_TESTONLY_TOKEN'))
    port = server.server_port
    assert port not in {8765, 8766}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with passive_guards(root, port) as reads:
            for suffix in ('', '&refresh=1'):
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                connection.request('GET', '/api/competitions?date=' + datetime.now(ZoneInfo('Europe/Berlin')).date().isoformat() + suffix,
                                   headers={'X-Arthur-Token': 'EXPLICIT_TESTONLY_TOKEN'})
                response = connection.getresponse(); value = json.loads(response.read())
                connection.close()
                assert response.status == 200 and value['status'] == status
                assert len(value['competitions']) == 25
                assert next(x for x in value['competitions'] if x['id'] == 39)['fixture_count'] == (1 if name in {'fresh', 'stale'} else None)
                assert next(x for x in value['competitions'] if x['id'] == 32)['fixture_count'] is None
                assert value['local_reload_requested'] is bool(suffix)
            assert len(reads) == (0 if name == 'missing' else 2)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
    assert not thread.is_alive() and state.competition_cache == {'TESTONLY': 'UNCHANGED'}
    assert before == hashes(root)




@pytest.mark.parametrize('name,count,status', [('alias_nonempty', 1, 'CACHED_SNAPSHOT'),
                                             ('alias_empty', 0, 'CACHED_SNAPSHOT'),
                                             ('alias_stale', 1, 'STALE_SNAPSHOT')])
def test_genuine_existing_authenticated_alias_preserves_first_raw_metadata(cases, name, count, status):
    root = cases[name]; before = hashes(root)
    with passive_guards(root):
        result = CANDIDATE.read_competition_catalogue(root, config(),
            datetime.now(ZoneInfo('Europe/Berlin')).date(), as_of=datetime.now(UTC))
    assert result['status'] == status and result['cache_status'] == 'VALID'
    english = next(x for x in result['competitions'] if x['id'] == 39)
    assert english['fixture_count'] == count
    source = result['sources'][0]
    assert source['source_proof_kind'] == 'EXISTING_AUTHENTICATED_HTTP_CACHE_OBSERVATION_NOT_QUOTE_TIME'
    assert source['raw_first_source_url'] != source['source_url']
    assert source['raw_first_fetched_at'] != source['observed_at']
    assert source['existing_http_observation_proof']['times']['observed_at'] == source['observed_at']
    assert before == hashes(root)


@pytest.mark.parametrize('name', ['alias_url_bad', 'alias_clock_bad', 'alias_hash_bad',
                                 'alias_unauth', 'alias_missing', 'alias_malformed', 'alias_http201'])
def test_genuine_missing_or_mismatched_http_alias_never_falls_back_to_clock_graft(cases, name):
    root = cases[name]; before = hashes(root)
    with passive_guards(root):
        result = CANDIDATE.read_competition_catalogue(root, config(),
            datetime.now(ZoneInfo('Europe/Berlin')).date(), as_of=datetime.now(UTC))
    assert result['status'] == 'CONFIGURED_FALLBACK'
    assert result['cache_status'] == 'INVALID_EXPLICIT_HTTP_CACHE_PROOF'
    assert len(result['competitions']) == 25 and all(x['fixture_count'] is None for x in result['competitions'])
    assert before == hashes(root)


def test_final_passive_probe_has_zero_unexpected_attempts():
    assert ATTEMPTS == []
