"""TESTONLY native routing regressions: saved source shapes, fake I/O, no fits."""
import json
import socket
import sqlite3
import subprocess
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
NOW = datetime(2026, 10, 29, 2, 16, 26, tzinfo=UTC)
DAY = date(2026, 10, 29)
INPUTS = json.loads((HERE / 'fixtures' / 'observed_native_calendar_20261029_TESTONLY.json').read_text(encoding='utf-8'))
GOLDEN_V1 = {f['fixture_id']: f for f in INPUTS['known_fixture_golden']['v1']}
GOLDEN_V2 = {f['fixture_id']: f for f in INPUTS['known_fixture_golden']['v2']}
ROWS = [r for p in INPUTS['pages'] for r in p['body']['data']]


def fail_io(*a, **kw):
    pytest.fail('TESTONLY_FORBIDDEN_REAL_CONSTRUCTOR_DB_NETWORK_PROCESS_OR_FIT')


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.setattr(sqlite3, 'connect', fail_io)
    monkeypatch.setattr(socket.socket, 'connect', fail_io)
    monkeypatch.setattr(socket, 'create_connection', fail_io)
    monkeypatch.setattr(subprocess, 'Popen', fail_io)
    from goal_analysis import storage
    monkeypatch.setattr(storage.Database, '__init__', fail_io)
    from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
    monkeypatch.setattr(TheStatsPrimaryClient, '__init__', fail_io)
    from goal_analysis.providers.the_stats_odds import TheStatsOddsFeed
    monkeypatch.setattr(TheStatsOddsFeed, '__init__', fail_io)
    from goal_analysis.jobs import the_stats_context
    monkeypatch.setattr(the_stats_context.Resources, '__init__', fail_io)

    from goal_analysis.jobs import (
        daily_223_live,
        league_scope,
        online_history_venue,
        the_stats_live,
    )
    return {'feed_type': TheStatsOddsFeed, 'context': the_stats_context,
            'league_scope': league_scope, 'the_stats_live': the_stats_live,
            'online_history_venue': online_history_venue, 'daily_223_live': daily_223_live}


class FakeTransport:
    """Finite TESTONLY endpoint responses; no network/library client constructor."""
    def __init__(self, mode, limit):
        self.mode, self.limit, self.seen = mode, limit, []
        self.snapshots = self
        self.usage = {'requests': 0, 'cache_hits': 0, 'max_requests': limit, 'stopped_reason': None}
    def read(self, digest):
        return json.dumps(next(p['body'] for p in INPUTS['pages'] if p['source']['content_hash'] == digest))
    def get(self, endpoint, params=None, **kw):
        self.seen.append((endpoint, deepcopy(params)))
        if self.usage['requests'] >= self.limit:
            self.usage['stopped_reason'] = 'TESTONLY_BUDGET_EXHAUSTED'
            return None, None, 'TESTONLY_BUDGET_EXHAUSTED'
        self.usage['requests'] += 1
        if self.mode == 'calendar':
            assert endpoint == '/football/matches' and not (params or {}).get('status')
            page = INPUTS['pages'][params['page'] - 1]
            return deepcopy(page['body']['data']), deepcopy(page['source']), None
        match = endpoint.split('/')[3]
        row = next(r for r in ROWS if r['id'] == match)
        source = {'source_url': 'https://api.thestatsapi.com/api' + endpoint,
                  'observed_at': NOW.isoformat(), 'content_hash': 'f' * 64}
        if self.mode == 'detail':
            return deepcopy(row), source, None
        assert endpoint.endswith('/odds')
        return {'match_id': match, 'bookmakers': [{'bookmaker': 'TESTONLY Book', 'markets': {
            'match_odds': {'home': {'last_seen': 2}, 'draw': {'last_seen': 3}, 'away': {'last_seen': 4}},
            'total_goals': {'2.5': {'over': {'last_seen': 2}, 'under': {'last_seen': 2}}}}}]}, source, None


def collected(modules, monkeypatch):
    transport = FakeTransport('calendar', 2)
    monkeypatch.setattr(modules['context'], 'Resources', lambda *a, **kw: transport)
    config = deepcopy(INPUTS['config'])
    client = SimpleNamespace(root=HERE / 'TESTONLY-NO-DATABASE', provider='the_stats_api',
                             last_usage=SimpleNamespace(remaining_day=1000))
    result = modules['the_stats_live'].collect_live(config, client, DAY, lambda: NOW,
                                                   operating_contract_version=2)
    return result, config, transport


def test_actual_114_rows_30_native_routes_preserve_known_mapping_and_v1(modules, monkeypatch):
    result, config, transport = collected(modules, monkeypatch)
    fixtures = result['fixtures']['records']
    assert len(fixtures) == 114 and len(result['native_competition_routes']) == 30
    assert result['calendar']['scope_receipt']['full_product_run'] is True
    assert len(result['calendar']['scope_receipt']['routed_fixture_ids']) == 114
    assert transport.usage['requests'] == transport.limit == 2
    assert any(x['status'] == 'HISTORY_PARTIAL' for x in result['history']['data_issues'])
    assert len({f['provider_league_id'] for f in fixtures}) == 33
    original_rows = {r['id']: r for r in ROWS}
    current = modules['the_stats_live']
    assert {str(k): list(v) for k, v in current.LEAGUES.items()} == INPUTS['known_crosswalk']
    lookup = {current.LEAGUES[str(l['api_football_id'])][0]: l for l in config['leagues']}
    known = []
    for f in fixtures:
        raw = original_rows[f['provider_fixture_id']]
        assert f['provider_league_id'] == raw['competition_id']
        assert f['provider_season_id'] == raw['season_id']
        assert f['neutral_venue'] is raw.get('is_neutral')
        if f.get('native_competition_route'):
            assert f['config_league_id'] is f['season'] is f['competition_type'] is None
            assert 'api_football_league_id' not in f
            assert f['competition'] == raw['competition_id']
            assert f['metadata_status'] == 'UNKNOWN'
        else:
            known.append(f)
            assert f == GOLDEN_V2[f['fixture_id']]
    assert len(known) == 17
    assert {f['fixture_id'] for f in known} == set(GOLDEN_V1) == set(GOLDEN_V2)
    # The old/unmarked scope and existing known fixture bytes retain their old contract.
    leagues, receipt = modules['league_scope'].resolve_full_day_scope(ROWS, DAY, config['leagues'])
    assert leagues == config['leagues'] and receipt['scope'] == 'LEGACY_CONFIGURED'
    _manual, receipt = modules['league_scope'].resolve_full_day_scope(ROWS, DAY, config['leagues'],
        operating_contract_version=2, selected_competition_ids=[135])
    assert receipt['scope'] == 'MANUALLY_RESTRICTED_RESEARCH' and not receipt['full_product_run']
    assert not receipt['native_competition_routes']
    for f in known:
        raw = original_rows[f['provider_fixture_id']]
        assert modules['the_stats_live'].normalize_fixture(raw, lookup[raw['competition_id']], DAY,
                 f['fixture_source']) == GOLDEN_V1[f['fixture_id']]


def test_real_caller_native_odds_detail_and_retained_receive_114_with_budget_gap(modules, monkeypatch):
    from goal_analysis.jobs.normal_history_input import prepare_inputs
    from goal_analysis.jobs.the_stats_venue import resolve_venue
    football = FakeTransport('calendar', 2)
    monkeypatch.setattr(modules['context'], 'Resources', lambda *a, **kw: football)
    odds = object.__new__(modules['feed_type'])
    odds.region = INPUTS['config']['odds_region']
    odds.max_credits = INPUTS['config']['max_odds_credits']
    odds.clock = lambda: NOW
    odds.resources = FakeTransport('odds', 3)
    odds.client = SimpleNamespace(last_usage=SimpleNamespace(monthly_remaining=999))
    odds.fixtures, odds.usage, odds.issues = {}, [], []
    odds.calls = odds.reserved_credits = 0
    client = SimpleNamespace(root=HERE / 'TESTONLY-NO-DATABASE', provider='the_stats_api',
                             last_usage=SimpleNamespace(remaining_day=1000))
    artifact = modules['daily_223_live'].run_daily223_live(deepcopy(INPUTS['config']), client, odds,
        None, DAY, lambda: NOW, construct_legacy=False, operating_contract_version=2)['artifacts']
    assert artifact['report']['construction_status'] == 'COLLECTION_COMPLETE', (artifact['report'].get('blocked_stage'), artifact['report'].get('reason'))
    fixtures = artifact['fixtures']['records']
    assert len(odds.fixtures) == len(fixtures) == 114
    assert odds.resources.usage['requests'] == 3
    gaps = [x for x in artifact['candidate_input']['data_issues']
            if x['status'] == 'BASE_MARKETS_BUDGET_UNAVAILABLE']
    assert gaps and any('fixture_id' in x for x in gaps)
    routes = modules['daily_223_live']._odds_leagues(artifact['config'], fixtures,
        stats_primary=True, operating_contract_version=2)
    assert len({r['odds_sport_key'] for r in routes}) == len(routes) == 33
    assert modules['daily_223_live']._odds_leagues(artifact['config'], fixtures,
        stats_primary=False, operating_contract_version=2) == artifact['config']['leagues']
    detail = FakeTransport('detail', 3)
    detail.clock = lambda: NOW
    detail_results = [resolve_venue(detail, f, refresh_existing=True) for f in fixtures]
    assert len(detail_results) == 114
    assert len(detail.seen) + sum(s and s.get('status') == 'LOCKED' for _, s in detail_results) == 114
    assert sum(s and s.get('status') == 'LOCKED' for _, s in detail_results) == 23
    assert detail.usage['requests'] == 3
    assert any(s['status'] == 'TESTONLY_BUDGET_EXHAUSTED' for _, s in detail_results)
    called = []
    def reader(targets, *, as_of):
        called.append((targets, as_of))
        return {'status': 'MISSING_SOURCE', 'gaps': {'TESTONLY_NO_DB_SOURCE': 1}}
    before = deepcopy(fixtures)
    prepare_inputs(fixtures, [], NOW, retained_reader=reader)
    assert called == [(before, NOW)] and len(called[0][0]) == 114
    assert fixtures == before


def test_native_source_renormalization_and_forged_route_rejected(modules, monkeypatch):
    result, _, _ = collected(modules, monkeypatch)
    fixtures = result['fixtures']['records']
    by_hash = {p['source']['content_hash']: p for p in INPUTS['pages']}
    def reader(pointer):
        page = by_hash[pointer['content_hash']]
        stamp = pointer['observed_at']
        times = {'observed_at': stamp, 'available_at': stamp, 'ingested_at': stamp,
                 'published_at': None, 'event_time': None, 'event_time_kind': 'UNKNOWN',
                 'availability_basis': 'SOURCE_PRESENCE_UPPER_BOUND'}
        return deepcopy(page['body']), {'times': times, 'source_url': pointer['source_url']}
    bound = modules['online_history_venue'].correct_inputs(fixtures, {'records': [], 'sources': []},
                                                         as_of=NOW, source_reader=reader)
    assert bound['fixtures'] == fixtures
    assert bound['diagnostics'].get('ONLINE_VENUE_SOURCE_UNPROVEN', 0) == 0
    assert sum(f['neutral_venue'] is False for f in fixtures) == 43
    target = next(f for f in fixtures if f.get('native_competition_route'))
    raw = next(r for r in ROWS if r['id'] == target['provider_fixture_id'])
    route = target['native_competition_route']
    for field, bad in [('provider_league_id', 'comp_999999999'), ('provider_season_ids', ['sn_999999']),
                       ('fixture_ids', []), ('source_receipts', []), ('routing_key', 'soccer_fake')]:
        forged = {**route, field: bad}
        with pytest.raises(ValueError, match='Unproven observed native'):
            modules['the_stats_live'].normalize_fixture(raw, None, DAY, target['fixture_source'],
                operating_contract_version=2, native_route=forged)
    with pytest.raises(ValueError):
        modules['the_stats_live'].normalize_fixture(raw, None, DAY, target['fixture_source'],native_route=route)


def test_unmapped_native_quote_reaches_existing_candidate_assembler(modules, monkeypatch):
    from goal_analysis.jobs.daily_223_candidates import assemble_daily223_candidates
    result, config, _ = collected(modules, monkeypatch)
    fixture = next(f for f in result['fixtures']['records'] if f.get('native_competition_route')
        and f['provider_status'] == 'NS' and datetime.fromisoformat(f['kickoff']) > NOW)
    feed = object.__new__(modules['feed_type'])
    feed.clock = lambda: NOW
    feed.resources = FakeTransport('odds', 1)
    feed.client = SimpleNamespace(last_usage=SimpleNamespace(monthly_remaining=999))
    feed.fixtures, feed.usage, feed.issues = {}, [], []
    feed.calls = feed.reserved_credits = 0
    feed.bind_fixtures([fixture])
    event = feed.extra(fixture['sport_key'], fixture['provider_fixture_id'], ['h2h', 'totals'])
    bundle = assemble_daily223_candidates([fixture], [event], config, DAY, NOW,
                                         datetime.fromisoformat(result['fixtures']['observed_at']))
    assert len(bundle['candidates']) == 5
    assert not any(x['status'] == 'INVALID_ODDS_EVENT' for x in bundle['data_issues'])
    for quote in bundle['candidates']:
        assert quote['quote_provider'] == quote['source_provider'] == 'the_stats_api'
        assert quote['provider_league_id'] == fixture['provider_league_id']
        assert quote['provider_season_id'] == fixture['provider_season_id']
        assert quote['competition_type'] is quote['config_league_id'] is None
        assert quote['quote_source_id'] == 'https://api.thestatsapi.com/api/football/matches/' + fixture['provider_fixture_id'] + '/odds'
    assert feed.resources.usage['requests'] == 1


def test_real_caller_never_sends_native_tokens_to_non_native_odds_feed(modules, monkeypatch):
    football = FakeTransport('calendar', 2)
    monkeypatch.setattr(modules['context'], 'Resources', lambda *a, **kw: football)
    odds = object.__new__(modules['feed_type'])
    odds.provider = 'the_odds_api'
    odds.region = INPUTS['config']['odds_region']
    odds.max_credits = INPUTS['config']['max_odds_credits']
    odds.clock = lambda: NOW
    odds.resources = FakeTransport('odds', 3)
    odds.client = SimpleNamespace(last_usage=SimpleNamespace(monthly_remaining=999))
    odds.fixtures, odds.usage, odds.issues = {}, [], []
    odds.calls = odds.reserved_credits = 0
    seen = []
    original = odds.bulk
    def fake_external_bulk(sport, target):
        seen.append(sport)
        return original(sport, target)
    odds.bulk = fake_external_bulk
    client = SimpleNamespace(root=HERE / 'TESTONLY-NO-DATABASE', provider='the_stats_api',
                             last_usage=SimpleNamespace(remaining_day=1000))
    out = modules['daily_223_live'].run_daily223_live(deepcopy(INPUTS['config']), client, odds,
        None, DAY, lambda: NOW, construct_legacy=False, operating_contract_version=2)['artifacts']
    assert out['report']['construction_status'] == 'COLLECTION_COMPLETE'
    assert len(out['fixtures']['records']) == 114
    assert seen and not any(key.startswith('soccer_the_stats_') for key in seen)
    assert set(seen) <= {league['odds_sport_key'] for league in INPUTS['config']['leagues']}


def test_real_model_fixture_normalizer_accepts_native_unknown_metadata_without_imputing_venue(modules, monkeypatch):
    from goal_analysis.models._data import normalize_fixtures
    result, _, _ = collected(modules, monkeypatch)
    fixtures = result['fixtures']['records']
    before = deepcopy(fixtures)
    prepared, issues = normalize_fixtures(fixtures, NOW)
    accepted = {row['fixture_id']: row for row in prepared}
    raw = {row['id']: row for row in ROWS}
    expected = {f['fixture_id'] for f in fixtures
                if type(raw[f['provider_fixture_id']].get('is_neutral')) is bool
                and f['provider_status'] == 'NS'
                and datetime.fromisoformat(f['kickoff']) > NOW}
    assert set(accepted) == expected
    native = [f for f in fixtures if f.get('native_competition_route')
              and f['fixture_id'] in expected]
    assert native, 'Observed cohort must exercise otherwise eligible unmapped native targets'
    for f in native:
        assert f['competition_type'] is f['season'] is f['venue_country'] is None
        actual = accepted[f['fixture_id']]
        assert actual['league_id'] == raw[f['provider_fixture_id']]['competition_id']
        assert actual['home_team_id'] == raw[f['provider_fixture_id']]['home_team']['id']
        assert actual['away_team_id'] == raw[f['provider_fixture_id']]['away_team']['id']
        assert actual['neutral_venue'] is raw[f['provider_fixture_id']]['is_neutral']
    unknown = {f['fixture_id'] for f in fixtures if f['neutral_venue'] is None}
    assert unknown and not unknown.intersection(accepted)
    assert sum(issue['code'] == 'INVALID_FIXTURE' for issue in issues) == len(unknown)
    assert fixtures == before
