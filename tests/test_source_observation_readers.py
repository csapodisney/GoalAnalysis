"""Physical source readers with synthetic HTTP responses, never live API data."""
import hashlib
import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from goal_analysis.jobs.cup_specialist_shadow import _prices
from goal_analysis.jobs.fixture_contexts import raw_source
from goal_analysis.jobs.native_history_shadow import native_history_view
from goal_analysis.jobs.the_stats_context import Resources
from goal_analysis.jobs.xg_specialist_shadow import _targets
from goal_analysis.providers.the_stats_api import TheStatsApiClient
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.http_observations import HttpObservations
from goal_analysis.storage.source_observations import http_source, read_source_raw

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
LATER = NOW + timedelta(minutes=31)
BASE = 'https://api.thestatsapi.com/api'


def receive(root, body, endpoint, when=NOW, *, available=None, params=None):
    """An actual client response in synthetic transport, committed SQLite/raw."""
    transport = Mock(return_value=(json.dumps(body).encode(), {}, 200))
    client = TheStatsApiClient('safe-test-key', transport, clock=lambda: when, max_requests=1)
    client.request(endpoint, params)
    receipt = client.last_response
    database = Database(root/'data/goal_analysis.sqlite3')
    database.initialize()
    SnapshotStore(database, root/'data/raw').save('the_stats_api', 'daily_context',
        client.last_raw_response, 'application/json', when, receipt['source_url'])
    pointers = HttpObservations(database, root, clock=lambda: available or when).admit(receipt)
    assert transport.call_count == 1
    return database, {'provider': 'the_stats_api', 'source_url': receipt['source_url'],
        'observed_at': when.isoformat(), 'content_hash': receipt['payload_sha256'],
        'resource_type': 'daily_context', 'published_at': None, **pointers}


def read(root, source, cutoff):
    with closing(sqlite3.connect((root/'data/goal_analysis.sqlite3').as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        row = dict(db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?',
                              (source['content_hash'],)).fetchone())
        return read_source_raw(db, row, root, cutoff, source)


def unpoint(source):
    return {k: v for k, v in source.items() if not k.startswith('http_')}


def target():
    row = {'id': 'mt_900', 'competition_id': 'comp_1', 'season_id': 'sn_1',
        'utc_date': (NOW+timedelta(hours=5)).isoformat(), 'status': 'scheduled',
        'home_team': {'id': 'tm_1', 'name': 'H'}, 'away_team': {'id': 'tm_2', 'name': 'A'},
        'is_neutral': False}
    fixture = {'fixture_id': 'the_stats_api:mt_900', 'source_provider': 'the_stats_api',
        'provider_fixture_id': 'mt_900', 'provider_league_id': 'comp_1', 'provider_season_id': 'sn_1',
        'provider_home_team_id': 'tm_1', 'provider_away_team_id': 'tm_2',
        'kickoff': row['utc_date'], 'provider_status': 'NS', 'neutral_venue': False}
    return row, fixture


def page(rows):
    return {'data': rows, 'meta': {'page': 1, 'per_page': 100, 'total_pages': 1 if rows else 0}}


@pytest.mark.parametrize('pointers', [True, False])
def test_identical_repeated_bytes_have_exact_new_clock_and_old_cutoff_cannot_see_it(tmp_path, pointers):
    body = {'data': []}
    db, first = receive(tmp_path, body, '/football/teams/tm_1/injuries-suspensions')
    _, second = receive(tmp_path, body, '/football/teams/tm_1/injuries-suspensions', LATER)
    source = second if pointers else unpoint(second)
    result, proof = read(tmp_path, source, LATER)
    assert result == body and proof['observation_id'] == second['http_observation_id']
    assert proof['times']['observed_at'] == LATER.isoformat(timespec='microseconds')
    with pytest.raises(ValueError):
        read(tmp_path, source, NOW)
    assert read(tmp_path, first, NOW)[1]['observation_id'] == first['http_observation_id']
    assert raw_source(tmp_path, db, source, LATER) == []


def test_same_body_different_url_requires_its_own_actual_url_receipt_even_without_pointers(tmp_path):
    db, first = receive(tmp_path, {'data': []}, '/football/teams/tm_1/injuries-suspensions')
    _, other = receive(tmp_path, {'data': []}, '/football/teams/tm_2/injuries-suspensions', LATER)
    assert first['content_hash'] == other['content_hash']
    assert read(tmp_path, unpoint(other), LATER)[1]['source_url'] == other['source_url']
    assert raw_source(tmp_path, db, unpoint(other), LATER) == []
    with pytest.raises(ValueError):
        read(tmp_path, {**unpoint(other), 'source_url': BASE+'/football/teams/tm_3/injuries-suspensions'}, LATER)


@pytest.mark.parametrize('change', [{'http_receipt_id': 'a'*64}, {'http_observation_id': 'a'*32},
    {'http_observation_status': 'UNAVAILABLE'}, {'observed_at': (NOW+timedelta(seconds=1)).isoformat()}])
def test_explicit_invalid_pointer_never_falls_back_to_valid_first_raw(tmp_path, change):
    db, source = receive(tmp_path, {'data': []}, '/football/teams/tm_1/injuries-suspensions')
    with pytest.raises(ValueError):
        read(tmp_path, {**source, **change}, LATER)
    with pytest.raises(ValueError):
        raw_source(tmp_path, db, {**source, **change}, LATER)


def test_exact_clock_required_not_latest_for_bytes_and_missing_witness_never_falls_back(tmp_path):
    db, source = receive(tmp_path, {'data': []}, '/football/teams/tm_1/injuries-suspensions',
                         available=NOW+timedelta(seconds=1))
    with pytest.raises(ValueError):
        read(tmp_path, unpoint(source), NOW)
    forged = {**unpoint(source), 'observed_at': (NOW+timedelta(minutes=5)).isoformat()}
    with pytest.raises(ValueError, match='MISSING_EXACT_HTTP'):
        read(tmp_path, forged, LATER)
    with pytest.raises(ValueError, match='MISSING_EXACT_HTTP'):
        raw_source(tmp_path, db, forged, LATER)
    # An interrupted ingestion record cannot use the separately valid raw witness.
    with db.transaction() as con:
        record = dict(con.execute('SELECT * FROM the_stats_http_observations').fetchone())
        record.update(response_id='b'*32, observed_at=LATER.isoformat(timespec='microseconds'),
                      ingested_at=LATER.isoformat(timespec='microseconds'))
        con.execute('INSERT INTO the_stats_http_observations VALUES (?,?,?,?,?,?,?,?)', tuple(record.values()))
    with pytest.raises(ValueError, match='UNWITNESSED'):
        read(tmp_path, {**unpoint(source), 'observed_at': LATER.isoformat()}, LATER)


@pytest.mark.parametrize('pointers', [True, False])
def test_target_readers_use_new_observation_not_hash_only_cached_old_proof(tmp_path, pointers):
    row, fixture = target()
    _, first = receive(tmp_path, page([row]), '/football/matches', params={'per_page': 100})
    _, second = receive(tmp_path, page([row]), '/football/matches', LATER, params={'per_page': 100})
    fixture['fixture_source'] = second if pointers else unpoint(second)
    targets, proofs, issues = _targets(tmp_path, {fixture['fixture_id']: fixture}, LATER)
    assert len(targets) == 1 and not issues
    assert proofs[fixture['fixture_id']]['observation_id'] == second['http_observation_id']
    assert not _targets(tmp_path, {fixture['fixture_id']: fixture}, NOW)[0]
    view = native_history_view(tmp_path, [fixture], as_of=LATER)
    assert view['verified_target_count'] == 1
    assert view['fixtures'][0]['observed_at'] == LATER.isoformat(timespec='microseconds')
    old = deepcopy(fixture); old['fixture_source'] = first
    assert not _targets(tmp_path, {old['fixture_id']: old}, LATER)[0]


def test_raw_price_refresh_requires_exact_actual_http_time_and_price_not_quote_time(tmp_path):
    from test_cup_specialist_shadow import environment

    data, _, _ = environment(tmp_path, when=NOW-timedelta(hours=1))
    quote = data['candidates'][0]
    body = {'data': {'match_id': 'mt_1', 'bookmakers': [{'bookmaker': 'Synthetic', 'markets': {
        'match_odds': {'home': {'last_seen': 2.3}, 'draw': {'last_seen': 3.6}, 'away': {'last_seen': 3.8}}}}]}}
    # environment serialized the same content; the original raw clock remains old.
    _, source = receive(tmp_path, body, '/football/matches/mt_1/odds', NOW)
    assert source['content_hash'] == quote['quote_source_sha256']
    quote['quote_observed_at'] = NOW.isoformat()
    fixtures = {f['fixture_id']: f for f in data['fixtures']}
    accepted, proofs, issues = _prices(tmp_path, [quote], fixtures, NOW)
    assert len(accepted) == 1 and not issues and accepted[0]['quoted_at'] is None
    assert proofs[quote['candidate_id']]['observation_id'] == source['http_observation_id']
    old = deepcopy(quote); old['quote_observed_at'] = (NOW-timedelta(hours=1)).isoformat()
    both, _, _ = _prices(tmp_path, [quote, old], fixtures, NOW)
    assert len(both) == 1
    forged = deepcopy(quote); forged['quote_observed_at'] = (NOW+timedelta(seconds=1)).isoformat()
    assert not _prices(tmp_path, [forged], fixtures, NOW+timedelta(seconds=2))[0]


def test_history_discovers_actual_url_alias_when_first_raw_url_was_out_of_scope(tmp_path):
    row, fixture = target()
    _, fixture['fixture_source'] = receive(tmp_path, page([row]), '/football/matches', params={'per_page': 100})
    finished = {**row, 'id': 'mt_1', 'status': 'finished',
        'utc_date': (NOW-timedelta(days=5)).isoformat(), 'score': {'regulation': {'home': 2, 'away': 1}}}
    body = page([finished])
    receive(tmp_path, body, '/football/matches', params={'per_page': 100})
    params = {'competition_id': 'comp_1', 'date_from': '2026-01-01', 'date_to': '2026-10-22',
              'status': 'finished', 'per_page': 100, 'utc_offset': '+00:00'}
    _, source = receive(tmp_path, body, '/football/matches', LATER, params=params)
    view = native_history_view(tmp_path, [fixture], as_of=LATER)
    assert len(view['history']) == 1 and view['history'][0]['full_time_home'] == 2
    assert view['history'][0]['observed_at'] == LATER.isoformat(timespec='microseconds')
    assert source['http_receipt_id'] in {p['receipt_id'] for p in view['dataset']['lineage']['sources']}
    assert not native_history_view(tmp_path, [fixture], as_of=NOW)['history']


def test_history_http_alias_conflict_is_quarantined_instead_of_convenient_old_score(tmp_path):
    row, fixture = target()
    _, fixture['fixture_source'] = receive(tmp_path, page([row]), '/football/matches', params={'per_page': 100})
    params = {'competition_id': 'comp_1', 'date_from': '2026-01-01', 'date_to': '2026-10-22',
              'status': 'finished', 'per_page': 100, 'utc_offset': '+00:00'}
    row.update(id='mt_1', status='finished', utc_date=(NOW-timedelta(days=5)).isoformat(),
               score={'regulation': {'home': 2, 'away': 1}})
    receive(tmp_path, page([row]), '/football/matches', params=params)
    row['is_neutral'] = None
    receive(tmp_path, page([row]), '/football/matches', LATER, params=params)
    assert native_history_view(tmp_path, [fixture], as_of=NOW)['history']
    view = native_history_view(tmp_path, [fixture], as_of=LATER)
    assert not view['history'] and view['selection']['unknown_eligible_history_ids_quarantined'] == ['mt_1']


def test_actual_cache_hit_retains_old_observation_and_readers_do_not_write(tmp_path):
    raw = json.dumps({'data': []}).encode()
    transport = Mock(return_value=(raw, {}, 200))
    client = TheStatsApiClient('safe-test-key', transport, clock=lambda: NOW, max_requests=1)
    first = Resources(tmp_path, client, lambda: NOW, 1).get('/football/teams/tm_1/injuries-suspensions')[1]
    database = Database(tmp_path/'data/goal_analysis.sqlite3')
    with closing(database.connect()) as con:
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    path = database.path
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    resource = Resources(tmp_path, client, lambda: NOW+timedelta(minutes=1), 0)
    _, source, error = resource.get('/football/teams/tm_1/injuries-suspensions')
    assert not error and source == first and transport.call_count == 1
    assert read(tmp_path, source, NOW+timedelta(minutes=1))[1]['times']['observed_at'].startswith('2026-10-22T12:00:00')
    assert raw_source(tmp_path, database, unpoint(source), NOW+timedelta(minutes=1)) == []
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_exact_alias_lookup_is_bounded_and_does_not_create_missing_tables(tmp_path):
    database = Database(tmp_path/'data/goal_analysis.sqlite3'); database.initialize()
    with closing(database.connect()) as con:
        assert http_source(con, tmp_path, {'observed_at': NOW.isoformat()}, NOW) is None
        with pytest.raises(ValueError, match='MISSING_HTTP_SOURCE_PROOF_TABLES'):
            http_source(con, tmp_path, {'http_observation_status': 'VERIFIED'}, NOW)
        assert not con.execute("SELECT name FROM sqlite_master WHERE name='the_stats_http_observations'").fetchone()


def test_same_clock_responses_have_bounded_exact_resolution(tmp_path):
    endpoint = '/football/teams/tm_1/injuries-suspensions'
    for _ in range(17):
        _, source = receive(tmp_path, {'data': []}, endpoint)
    # Explicit pointers still work; a field-losing normalization cannot scan
    # arbitrarily many same-clock aliases or substitute a different clock.
    assert read(tmp_path, source, NOW)[0] == {'data': []}
    with pytest.raises(ValueError, match='OBSERVATION_LIMIT'):
        read(tmp_path, unpoint(source), NOW)


def test_history_url_scope_applies_before_metadata_cap(tmp_path, monkeypatch):
    from goal_analysis.jobs import native_history_shadow as module

    row, fixture = target()
    _, fixture['fixture_source'] = receive(tmp_path, page([row]), '/football/matches', params={'per_page': 100})
    finished = {**row, 'id': 'mt_1', 'status': 'finished',
        'utc_date': (NOW-timedelta(days=5)).isoformat(), 'score': {'regulation': {'home': 2, 'away': 1}}}
    for i in range(2, 8):
        receive(tmp_path, page([{**finished, 'id': f'mt_{i}'}]), '/football/matches', params={
            'competition_id': f'comp_{i}', 'status': 'finished', 'per_page': 100})
    receive(tmp_path, page([finished]), '/football/matches', params={'per_page': 100})
    receive(tmp_path, page([finished]), '/football/matches', LATER, params={
        'competition_id': 'comp_1', 'date_from': '2026-01-01', 'date_to': '2026-10-22',
        'status': 'finished', 'per_page': 100, 'utc_offset': '+00:00'})
    monkeypatch.setattr(module, 'MAX_METADATA', 1)
    view = native_history_view(tmp_path, [fixture], as_of=LATER)
    assert view['history_source_candidates'] == 1 and len(view['history']) == 1


def test_corrupt_raw_never_uses_new_http_clock(tmp_path):
    db, source = receive(tmp_path, {'data': []}, '/football/teams/tm_1/injuries-suspensions')
    with closing(db.connect()) as con:
        path = tmp_path/'data/raw'/con.execute('SELECT relative_path FROM raw_snapshots').fetchone()[0]
    original = path.read_bytes()
    path.write_bytes(original.replace(b'[]', b'{}'))
    with pytest.raises(ValueError, match='HASH_MISMATCH'):
        read(tmp_path, source, NOW)
