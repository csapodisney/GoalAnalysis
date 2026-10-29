"""Installed activation failures cannot silently become untracked requests."""
import json
import os
import socket
import sqlite3
import stat
import subprocess
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from goal_analysis.jobs.the_stats_context import VERSION, Resources
from goal_analysis.providers import the_stats_api
from goal_analysis.providers.the_stats_api import TheStatsApiError
from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
from goal_analysis.storage import provider_quota
from goal_analysis.storage.db import Database

NOW = datetime(2026, 10, 27, 7, tzinfo=UTC)
KEY = 'fixture-only-key-never-a-live-credential'
RELATIVE_ACTIVATION = Path('data/arthur/historical-background/activation.json')


class FixtureBoundaryError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def owned_boundary(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    project = Path(__file__).resolve().parents[1]
    production = Path(os.environ.get('ARTHUR_PRODUCTION_ROOT', project)).resolve()
    assert not root.is_relative_to(project) and not root.is_relative_to(production)
    marker = {'TESTONLY': True, 'scope': 'quota-activation-integrity', 'root': str(root)}
    marker_path = root / 'OWNED_TESTONLY.json'
    marker_path.write_text(json.dumps(marker), encoding='utf-8')
    state = SimpleNamespace(root=root, production=production, rejected=[], quota_calls=[],
                            sql_calls=[], forbidden=[])

    def require_owned(path):
        resolved = Path(path).resolve()
        if (not resolved.is_relative_to(root)
                or json.loads(marker_path.read_text(encoding='utf-8')) != marker):
            state.rejected.append(str(resolved))
            raise FixtureBoundaryError('TESTONLY_ROOT_REQUIRED_BEFORE_CONSTRUCTOR')
        return resolved

    quota_init = provider_quota.SharedQuota.__init__
    primary_init = TheStatsPrimaryClient.__init__
    database_init = Database.__init__
    quota_connect = provider_quota.SharedQuota._connect
    sqlite_connect = sqlite3.connect

    def guarded_quota(self, path, *args, **kwargs):
        require_owned(path)
        return quota_init(self, path, *args, **kwargs)

    def guarded_primary(self, path, *args, **kwargs):
        require_owned(path)
        return primary_init(self, path, *args, **kwargs)

    def guarded_database(self, path, *args, **kwargs):
        require_owned(path)
        return database_init(self, path, *args, **kwargs)

    def guarded_quota_connect(self):
        state.quota_calls.append(str(require_owned(self.path)))
        return quota_connect(self)

    def guarded_sql(path, *args, **kwargs):
        state.sql_calls.append(str(require_owned(path)))
        return sqlite_connect(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        state.forbidden.append('NETWORK_OR_PROCESS')
        raise AssertionError('NO_NETWORK_OR_PROCESS_IN_TESTONLY_CONTROLS')

    monkeypatch.setattr(provider_quota.SharedQuota, '__init__', guarded_quota)
    monkeypatch.setattr(provider_quota.SharedQuota, '_connect', guarded_quota_connect)
    monkeypatch.setattr(TheStatsPrimaryClient, '__init__', guarded_primary)
    monkeypatch.setattr(Database, '__init__', guarded_database)
    monkeypatch.setattr(sqlite3, 'connect', guarded_sql)
    monkeypatch.setattr(the_stats_api, '_default_transport', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    yield state
    assert state.forbidden == []


def valid_activation(**changes):
    return {'version': 'historical-background-v1', 'tracking_enabled': True,
            'background_enabled': True, 'activated_at': NOW.isoformat(), **changes}


def install(root, value):
    path = root / RELATIVE_ACTIVATION
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
    return path


class TransportSpy:
    def __init__(self, *, failing=False):
        self.calls = []
        self.failing = failing

    def __call__(self, url, headers, *, timeout):
        self.calls.append((url, dict(headers), timeout))
        if self.failing:
            raise OSError('secret-provider-body-' + KEY)
        if url.endswith('/health'):
            return {'status': 'healthy'}, {}, 200
        return {'data': [{'id': 'controlled-fixture'}]}, {
            'X-Monthly-Quota-Remaining': '90000', 'X-Ratelimit-Remaining': '99'}, 200


def client(root, transport, **kwargs):
    return TheStatsPrimaryClient(root, api_key=KEY, transport=transport,
                                 clock=lambda: NOW, **kwargs)


def counts(observer):
    with sqlite3.connect(observer.path) as connection:
        return tuple(connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                     for table in ('quota_attempts', 'quota_receipts'))


@pytest.mark.parametrize('mode', ['missing', 'disabled', 'disabled-background-on'])
def test_optional_absent_or_valid_disabled_stays_untracked(owned_boundary, mode):
    root = owned_boundary.root
    if mode != 'missing':
        install(root, valid_activation(tracking_enabled=False,
                                      background_enabled=mode.endswith('-on')))
    assert provider_quota.observer_for(root, clock=lambda: NOW) is None
    transport = TransportSpy()
    instance = client(root, transport, quota_observer=None)
    assert instance.quota_observer is None
    assert instance.request('/coverage/summary')['data']
    assert len(transport.calls) == 1
    assert owned_boundary.quota_calls == owned_boundary.sql_calls == []


INVALID = [
    b'{bad-json-and-secret-provider-body', [], None, True, 'installed',
    valid_activation(version='wrong'), valid_activation(tracking_enabled=1),
    valid_activation(tracking_enabled='true'), valid_activation(background_enabled=0),
    {key: value for key, value in valid_activation().items() if key != 'activated_at'},
    valid_activation(activated_at='2026-10-27T07:00:00'),
    valid_activation(activated_at='not-a-clock'), valid_activation(activated_at=[]),
    valid_activation(tracking_enabled=False, activated_at='invalid-disabled-clock'),
]


@pytest.mark.parametrize('value', INVALID)
def test_invalid_installed_activation_is_blocked_before_request_and_quota_sql(
        owned_boundary, value):
    root = owned_boundary.root
    path = install(root, value)
    before = path.read_bytes()
    observer = provider_quota.observer_for(root, clock=lambda: NOW)
    assert observer is not None
    with pytest.raises(ValueError, match='^INVALID_INSTALLED_QUOTA_ACTIVATION$'):
        observer.begin()
    transport = TransportSpy()
    instance = client(root, transport)
    with pytest.raises(TheStatsApiError) as error:
        instance.request('/coverage/summary')
    assert error.value.code == 'QUOTA_TRACKING_UNAVAILABLE'
    assert str(error.value) == 'TheStatsAPI QUOTA_TRACKING_UNAVAILABLE'
    assert error.value.__cause__ is None
    assert instance.request_count == instance.authenticated_request_count == 0
    assert transport.calls == owned_boundary.quota_calls == owned_boundary.sql_calls == []
    assert path.read_bytes() == before
    assert not (root / 'data/arthur/the-stats-quota.sqlite3').exists()


@pytest.mark.parametrize('mode', ['directory', 'symlink', 'stat-denied', 'read-denied',
                                 'disappeared-after-stat'])
def test_installed_non_file_or_unreadable_is_not_optional_absence(
        owned_boundary, monkeypatch, mode):
    root = owned_boundary.root
    path = root / RELATIVE_ACTIVATION
    if mode == 'directory':
        path.mkdir(parents=True)
    else:
        install(root, valid_activation())
    original_stat, original_read = Path.lstat, Path.read_bytes

    def lstat(current):
        if current == path:
            if mode == 'stat-denied':
                raise PermissionError(KEY)
            if mode == 'symlink':
                return SimpleNamespace(st_mode=stat.S_IFLNK)
        return original_stat(current)

    def read(current):
        if current == path:
            if mode == 'read-denied':
                raise PermissionError(KEY)
            if mode == 'disappeared-after-stat':
                raise FileNotFoundError(KEY)
        return original_read(current)

    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(Path, 'read_bytes', read)
    observer = provider_quota.observer_for(root)
    with pytest.raises(ValueError, match='^INVALID_INSTALLED_QUOTA_ACTIVATION$'):
        observer.begin()
    assert owned_boundary.quota_calls == owned_boundary.sql_calls == []


@pytest.mark.parametrize('explicit_none', [False, True])
def test_enabled_tracking_records_exactly_one_actual_claim_and_receipt(
        owned_boundary, explicit_none):
    root = owned_boundary.root
    install(root, valid_activation())
    transport = TransportSpy()
    instance = client(root, transport, **({'quota_observer': None} if explicit_none else {}))
    assert isinstance(instance.quota_observer, provider_quota.SharedQuota)
    assert not instance.quota_observer.path.exists()
    result = instance.request('/coverage/summary')
    assert result['data'] == [{'id': 'controlled-fixture'}]
    assert len(transport.calls) == 1 and counts(instance.quota_observer) == (1, 1)
    assert instance.authenticated_request_count == instance.request_count == 1
    assert KEY.encode() not in instance.quota_observer.path.read_bytes()


def test_explicit_observer_preserved_and_factory_is_not_eager(owned_boundary, monkeypatch):
    install(owned_boundary.root, b'{corrupt')
    observer = SimpleNamespace(begin_calls=0, finish_calls=0)

    def begin():
        observer.begin_calls += 1
        return 'caller-owned-attempt'

    def finish(identifier, receipt, usage):
        assert identifier == 'caller-owned-attempt' and receipt['authenticated'] is True
        observer.finish_calls += 1

    def forbidden_factory(*args, **kwargs):
        raise AssertionError('EXPLICIT_OBSERVER_MUST_NOT_EVALUATE_DEFAULT')

    observer.begin, observer.finish = begin, finish
    monkeypatch.setattr(provider_quota, 'observer_for', forbidden_factory)
    transport = TransportSpy()
    instance = client(owned_boundary.root, transport, quota_observer=observer)
    assert instance.quota_observer is observer
    instance.request('/coverage/summary')
    assert observer.begin_calls == observer.finish_calls == len(transport.calls) == 1
    assert owned_boundary.quota_calls == owned_boundary.sql_calls == []


def test_explicit_none_cannot_disable_an_invalid_installed_activation(owned_boundary):
    install(owned_boundary.root, b'{corrupt')
    transport = TransportSpy()
    instance = client(owned_boundary.root, transport, quota_observer=None)
    assert instance.quota_observer is not None
    with pytest.raises(TheStatsApiError, match='QUOTA_TRACKING_UNAVAILABLE'):
        instance.request('/coverage/summary')
    assert transport.calls == owned_boundary.quota_calls == []


def test_transport_failure_keeps_committed_claim_without_fake_receipt(owned_boundary):
    install(owned_boundary.root, valid_activation())
    transport = TransportSpy(failing=True)
    instance = client(owned_boundary.root, transport)
    with pytest.raises(TheStatsApiError, match='^TheStatsAPI TRANSPORT_UNAVAILABLE$'):
        instance.request('/coverage/summary')
    assert counts(instance.quota_observer) == (1, 0) and len(transport.calls) == 1


def test_public_health_does_not_create_quota_claim_or_receipt(owned_boundary):
    install(owned_boundary.root, b'{corrupt')
    transport = TransportSpy()
    instance = client(owned_boundary.root, transport)
    assert instance.health() == {'status': 'healthy'}
    assert len(transport.calls) == 1 and instance.authenticated_request_count == 0
    assert owned_boundary.quota_calls == owned_boundary.sql_calls == []


def test_verified_cache_hit_survives_invalid_activation_without_a_new_request(owned_boundary):
    root = owned_boundary.root
    install(root, b'{corrupt')
    transport = TransportSpy()
    instance = client(root, transport)
    resources = Resources(root, instance, lambda: NOW, 1)
    endpoint = '/coverage/summary'
    url = the_stats_api.BASE_URL + endpoint
    payload = {'data': [{'id': 'valid-retained-content'}]}
    raw = json.dumps(payload).encode()
    saved = resources.snapshots.save('the_stats_api', 'daily_context', raw,
                                     'application/json', NOW, url)
    source = {'provider': 'the_stats_api', 'source_url': url, 'observed_at': NOW.isoformat(),
              'content_hash': saved.content_hash, 'published_at': None}
    key = json.dumps([endpoint, {}], sort_keys=True, separators=(',', ':'))
    resources.cache.set(VERSION, key, {'source': source}, timedelta(minutes=30), NOW)
    before = len(owned_boundary.sql_calls)
    data, proof, error = resources.get(endpoint, cache_only=True)
    assert data == payload['data'] and proof == source and error is None
    assert resources.usage['cache_hits'] == 1 and resources.usage['requests'] == 0
    assert len(owned_boundary.sql_calls) > before  # Actual cache/raw reads, not a mock.
    assert transport.calls == owned_boundary.quota_calls == []
    assert instance.request_count == 0


@pytest.mark.parametrize('which', ['primary', 'quota', 'database'])
def test_production_root_is_rejected_before_client_or_database_constructor(
        owned_boundary, which):
    constructors = {
        'primary': lambda: TheStatsPrimaryClient(owned_boundary.production),
        'quota': lambda: provider_quota.SharedQuota(owned_boundary.production),
        'database': lambda: Database(owned_boundary.production / 'data/goal_analysis.sqlite3'),
    }
    with pytest.raises(FixtureBoundaryError, match='TESTONLY_ROOT_REQUIRED_BEFORE_CONSTRUCTOR'):
        constructors[which]()
    assert len(owned_boundary.rejected) == 1
    assert owned_boundary.sql_calls == owned_boundary.quota_calls == []


def test_blocked_history_observer_has_unknown_legacy_state_without_quota_sql(owned_boundary):
    install(owned_boundary.root, b'{corrupt')
    observer = provider_quota.observer_for(owned_boundary.root, kind='history')
    assert observer.legacy_state() is None
    with pytest.raises(ValueError, match='^INVALID_INSTALLED_QUOTA_ACTIVATION$'):
        observer.begin()
    assert owned_boundary.quota_calls == owned_boundary.sql_calls == []


def test_actual_retained_history_page_is_verified_with_zero_new_requests(owned_boundary):
    from urllib.parse import urlencode

    from goal_analysis.jobs import the_stats_history as history
    from goal_analysis.storage.snapshots import SnapshotStore

    root = owned_boundary.root
    install(root, b'{corrupt')
    selected = history.prepare_plan(['comp_1'], as_of=NOW,
                                    date_from='2026-10-26', date_to='2026-10-26')
    assert len(selected['queries']) == 1
    work = root / 'data/arthur/the-stats-history' / selected['plan_sha256']
    work.mkdir(parents=True)
    row = {'id': 'mt_1', 'competition_id': 'comp_1', 'season_id': 'sn_1',
           'status': 'finished', 'utc_date': '2026-10-26T12:00:00Z',
           'home_team': {'id': 'tm_1', 'name': 'Home'},
           'away_team': {'id': 'tm_2', 'name': 'Away'},
           'score': {'regulation': {'home': 2, 'away': 1}}}
    payload = {'data': [row], 'meta': {'page': 1, 'per_page': 100, 'total_pages': 1}}
    params = {**selected['queries'][0], 'page': 1, 'per_page': 100}
    url = the_stats_api.BASE_URL + history.ENDPOINT + '?' + urlencode(params)
    database = Database(root / 'data/goal_analysis.sqlite3')
    database.initialize()
    saved = SnapshotStore(database, root / 'data/raw').save(
        'the_stats_api', 'historical_matches', json.dumps(payload).encode(),
        'application/json', NOW, url)
    source = {'plan_sha256': selected['plan_sha256'], 'params': params,
              'content_hash': saved.content_hash, 'source_url': url,
              'observed_at': NOW.isoformat()}
    receipt = work / 'page-00000-000001.json'
    history._atomic(receipt, source)
    before = receipt.read_bytes()
    summary = {'version': history.VERSION, 'plan_sha256': selected['plan_sha256'],
               'requests': 0, 'cached_pages': 0, 'processed_pages': 0, 'status': 'PLAN_ONLY',
               'query_count': 1, 'automatic_model_changes': False}
    transport = TransportSpy()
    # The unmodified retained consumer body; no RootRunLock or provider operation.
    result = history._run(root, work, selected, summary, 0, KEY, transport,
                          lambda: NOW, 100, None)
    assert result['status'] == 'COMPLETE'
    assert result['cached_pages'] == result['processed_pages'] == 1
    assert result['requests'] == 0 and result['pages'][0]['content_hash'] == saved.content_hash
    assert receipt.read_bytes() == before and result['authenticated_quota'] is None
    assert transport.calls == owned_boundary.quota_calls == []
    assert not (root / 'data/arthur/the-stats-quota.sqlite3').exists()


@pytest.mark.parametrize('primary,quota,cached,status,query_status', [
    ('corrupt', 'invalid', False, 'MONITOR_UNAVAILABLE', None),
    ('valid', 'invalid', False, 'NO_CHANGE', 'FETCH_FAILED'),
    ('valid', 'valid', False, 'UPDATED', 'UPDATED'),
    ('valid', 'invalid', True, 'UPDATED', 'UPDATED'),
    ('absent', 'invalid', False, 'NO_UNLOCKED_FIXTURES', None),
    ('empty', 'invalid', False, 'NO_UNLOCKED_FIXTURES', None),
], ids=['primary-first', 'quota-before-http', 'one-receipt', 'verified-cache',
        'absent-primary', 'empty-primary'])
def test_joint_primary_source_and_quota_gates(
        owned_boundary, monkeypatch, primary, quota, cached, status, query_status):
    import hashlib
    import importlib.util
    import shutil
    from sqlite3 import dbapi2

    from goal_analysis.agents import canonical_sha256
    from goal_analysis.jobs import odds_monitor as monitor
    from goal_analysis.jobs import odds_monitor_providers as planner

    root = owned_boundary.root
    project = Path(__file__).resolve().parents[1]
    ledger = root / 'data/arthur/ledger.sqlite3'
    activation_path = install(root, valid_activation() if quota == 'valid' else b'{corrupt')
    activation_before = activation_path.read_bytes()
    fixture = {
        'fixture_id': 'the_stats_api:mt_990001', 'source_provider': 'the_stats_api',
        'provider_fixture_id': 'mt_990001', 'provider_league_id': 'comp_990001',
        'provider_home_team_id': 'tm_990001', 'provider_away_team_id': 'tm_990002',
        'provider_season_id': 'sn_990001', 'season': 2026, 'provider_status': 'NS',
        'sport_key': 'soccer_testonly', 'home_team': 'TESTONLY Home',
        'away_team': 'TESTONLY Away', 'kickoff': (NOW + timedelta(hours=1)).isoformat(),
    }
    ledger.parent.mkdir(parents=True, exist_ok=True)
    if primary == 'corrupt':
        ledger.write_bytes(b'TESTONLY_CORRUPT_PRIMARY')
    elif primary != 'absent':
        report = {
            'run_id': 'joint-run', 'date': NOW.date().isoformat(),
            'observed_at': (NOW - timedelta(minutes=1)).isoformat(),
            'fixtures': [fixture] if primary == 'valid' else [],
            'recommendations': [], 'odds_history_snapshot': {'quotes': []},
        }
        with sqlite3.connect(ledger) as connection:
            connection.execute('CREATE TABLE portfolio_runs('
                               'run_id TEXT,date TEXT,observed_at TEXT,'
                               'snapshot_json TEXT,snapshot_hash TEXT)')
            connection.execute('INSERT INTO portfolio_runs VALUES (?,?,?,?,?)',
                               (report['run_id'], report['date'], report['observed_at'],
                                json.dumps(report), canonical_sha256(report)))
    ledger_before = ledger.read_bytes() if ledger.exists() else None
    guarded_sql = sqlite3.connect
    marker = {'TESTONLY': True, 'scope': 'quota-activation-integrity', 'root': str(root)}

    def source_readonly_sql(path, *args, **kwargs):
        # Extend only this case's guard to the real source reader's owned RO URI.
        if str(path) == ledger.as_uri() + '?mode=ro' and kwargs.get('uri') is True:
            assert ledger.resolve().is_relative_to(root)
            assert not root.is_relative_to(project)
            assert not root.is_relative_to(owned_boundary.production)
            assert json.loads((root / 'OWNED_TESTONLY.json').read_text('utf-8')) == marker
            owned_boundary.sql_calls.append(str(ledger))
            return dbapi2.connect(path, *args, **kwargs)
        return guarded_sql(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', source_readonly_sql)
    endpoint = '/football/matches/mt_990001/odds'
    url = the_stats_api.BASE_URL + endpoint
    payload = {'data': {'match_id': 'mt_990001', 'bookmakers': [{
        'bookmaker': 'TESTONLY Book', 'markets': {'match_odds': {
            'home': {'last_seen': 2.1}, 'draw': {'last_seen': 3.3},
            'away': {'last_seen': 3.5},
        }},
    }]}}
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                     separators=(',', ':')).encode('utf-8')
    raw_hash = hashlib.sha256(raw).hexdigest()
    transports, events, clients, sources, planned = [], [], [], [], []

    def transport(request_url, headers, *, timeout):
        assert request_url == url and timeout > 0
        assert headers['Authorization'] == 'Bearer ' + KEY
        transports.append(request_url)
        events.append('transport')
        return raw, {'X-Monthly-Quota-Remaining': '90000',
                     'X-Ratelimit-Remaining': '99'}, 200

    if cached:
        resources = Resources(root, client(root, transport), lambda: NOW, 1)
        saved = resources.snapshots.save('the_stats_api', 'daily_context', raw,
                                         'application/json', NOW, url)
        source = {'provider': 'the_stats_api', 'source_url': url,
                  'observed_at': NOW.isoformat(), 'content_hash': saved.content_hash,
                  'published_at': None}
        cache_key = json.dumps([endpoint, {}], sort_keys=True, separators=(',', ':'))
        resources.cache.set(VERSION, cache_key, {'source': source}, timedelta(minutes=5), NOW)
        with resources.database.connect() as connection:
            cache_before = tuple(connection.execute(
                'SELECT value_json,content_hash,created_at,expires_at FROM cache_entries '
                'WHERE namespace=? AND cache_key=?', (VERSION, cache_key)).fetchone())

    # Load the actual installed hyphenated CLI; no artifact import or added path hook.
    spec = importlib.util.spec_from_file_location(
        'joint_integrity_installed_cli', project / 'scripts/run-arthur-odds-monitor.py')
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    config = root / 'config/daily223-live.json'
    config.parent.mkdir(parents=True)
    shutil.copy2(project / 'config/daily223-live.json', config)
    monkeypatch.setattr(cli, 'datetime', SimpleNamespace(now=lambda _zone: NOW))
    monkeypatch.setenv('THE_STATS_API_KEY', KEY)
    original_read = monitor.saved_monitor_input
    original_plan = planner.build_monitor_queries
    guarded_primary = TheStatsPrimaryClient.__init__
    original_monitor = monitor.OddsMonitor.__init__
    original_observer = provider_quota.observer_for

    def read_source(*args, **kwargs):
        events.append('source')
        value = original_read(*args, **kwargs)
        sources.append(value)
        return value

    def plan(*args, **kwargs):
        events.append('plan')
        value = original_plan(*args, **kwargs)
        planned.extend(value)
        return value

    def primary_client(instance, path, **kwargs):
        events.append('client')
        guarded_primary(instance, path, api_key=KEY, transport=transport,
                        clock=lambda: NOW, **kwargs)
        clients.append(instance)

    def quota_observer(*args, **kwargs):
        events.append('quota')
        return original_observer(*args, **kwargs)

    def monitor_init(instance, path, **kwargs):
        assert Path(path).resolve() == root
        return original_monitor(instance, path, clock=lambda: NOW, **kwargs)

    monkeypatch.setattr(monitor, 'saved_monitor_input', read_source)
    monkeypatch.setattr(planner, 'build_monitor_queries', plan)
    monkeypatch.setattr(TheStatsPrimaryClient, '__init__', primary_client)
    monkeypatch.setattr(provider_quota, 'observer_for', quota_observer)
    monkeypatch.setattr(monitor.OddsMonitor, '__init__', monitor_init)
    result = cli.main(root)

    assert result['status'] == status and result['astra_calls'] == 0
    # This is the actual classifier in process, not a new native-exit measurement.
    assert cli.process_exit_code(result) == (1 if primary == 'corrupt' else 0)
    assert activation_path.read_bytes() == activation_before
    assert (ledger.read_bytes() if ledger.exists() else None) == ledger_before
    assert KEY not in json.dumps(result)
    quota_path = root / 'data/arthur/the-stats-quota.sqlite3'
    monitor_path = root / 'data/arthur/odds-monitor.sqlite3'
    if primary != 'valid':
        assert events == ['source'] and clients == planned == transports == []
        assert result['provider_calls'] == 0
        assert not quota_path.exists() and not monitor_path.exists()
        assert owned_boundary.quota_calls == []
        if primary == 'corrupt':
            assert result['source_read_failure'] is True
            assert result['reason'] == 'PRIMARY_LEDGER_UNREADABLE'
        else:
            assert sources[0]['fixtures'] == []
            assert sources[0]['source_read']['status'] == (
                'FIRST_STORE_MISSING' if primary == 'absent' else 'AVAILABLE')
        return

    assert events[:4] == ['source', 'plan', 'client', 'quota']
    assert sources[0]['fixtures'] == [fixture] and len(planned) == len(clients) == 1
    assert result['provider_calls'] == 1  # Monitor dispatch attempt, not HTTP count.
    assert [row['status'] for row in result['queries']] == [query_status]
    expected_http = int(quota == 'valid' and not cached)
    assert len(transports) == expected_http
    assert clients[0].request_count == clients[0].authenticated_request_count == expected_http
    if expected_http:
        assert counts(clients[0].quota_observer) == (1, 1)
    else:
        assert not quota_path.exists() and owned_boundary.quota_calls == []
    with sqlite3.connect(monitor_path) as connection:
        snapshots = connection.execute('SELECT quotes_json FROM monitor_snapshots').fetchall()
    if query_status == 'FETCH_FAILED':
        assert snapshots == []
        assert clients[0].last_response is None
        # Existing outer NO_CHANGE/classifier 0 remains a named classification limitation.
    else:
        assert len(snapshots) == 1
        quotes = json.loads(snapshots[0][0])
        assert len(quotes) == 3
        assert {quote['fixture_id'] for quote in quotes} == {fixture['fixture_id']}
        assert all(quote['quote_provider'] == 'the_stats_api'
                   and quote['quote_source_sha256'] == raw_hash
                   and quote['quote_observed_at'] == NOW.isoformat()
                   and quote['quote_source_id'] == url for quote in quotes)
    if cached:
        assert resources.snapshots.read(saved.content_hash) == raw
        with resources.database.connect() as connection:
            cache_after = tuple(connection.execute(
                'SELECT value_json,content_hash,created_at,expires_at FROM cache_entries '
                'WHERE namespace=? AND cache_key=?', (VERSION, cache_key)).fetchone())
        assert cache_after == cache_before
