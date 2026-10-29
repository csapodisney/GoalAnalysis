"""PREPARED ONLY. Promote with the candidate; never executed during preparation."""

import json
import socket
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from goal_analysis.jobs import the_stats_context
from goal_analysis.jobs import the_stats_live as module

NOW = datetime(2026, 10, 28, 10, tzinfo=UTC)
MISSING = object()
LEAGUE = {'api_football_id': 78, 'competition_id': 'TESTONLY_DE1', 'season': 2026,
          'history_seasons': [2026], 'odds_sport_key': 'soccer_testonly', 'competition_type': 'LEAGUE'}


@pytest.fixture(autouse=True)
def no_provider_or_database(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('TESTONLY_NO_DATABASE_OR_PROVIDER')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def native(kind):
    return {'id': 'mt_1' if kind == 'fixture' else 'mt_2', 'competition_id': 'comp_4643',
            'season_id': 'sn_2026', 'status': 'scheduled' if kind == 'fixture' else 'finished',
            'utc_date': (NOW + timedelta(hours=6) if kind == 'fixture'
                         else NOW - timedelta(days=1)).isoformat(),
            'home_team': {'id': 'tm_1', 'name': 'TESTONLY Home'},
            'away_team': {'id': 'tm_2', 'name': 'TESTONLY Away'},
            'score': {'regulation': {'home': 2, 'away': 1}, 'half_time_home': 1, 'half_time_away': 0}}


def normalize(kind, row, **kwargs):
    source = {'source_url': 'TESTONLY-native-source', 'observed_at': NOW.isoformat()}
    if kind == 'fixture':
        return module.normalize_fixture(row, LEAGUE, NOW.date(), source, **kwargs)
    return module.normalize_history(row, 'comp_4643', source, NOW, **kwargs)


@pytest.mark.parametrize('kind', ['fixture', 'history'])
@pytest.mark.parametrize('value', [pytest.param(MISSING, id='missing'), None, True, False,
                                 0, 1, 'false', 'true', {}, []])
def test_v2_typed_neutral_and_exact_legacy_scalar_bytes(kind, value):
    row = native(kind)
    if value is not MISSING:
        row['is_neutral'] = value
    before = deepcopy(row)
    current = normalize(kind, row, operating_contract_version=2)
    expected = value if type(value) is bool else None
    assert current['neutral_venue'] is expected
    original_default = normalize(kind, row)
    explicit_legacy = normalize(kind, row, operating_contract_version=1)
    assert json.dumps(original_default, sort_keys=True) == json.dumps(explicit_legacy, sort_keys=True)
    old_scalar = False if value is MISSING else value
    assert json.dumps(original_default['neutral_venue']) == json.dumps(old_scalar)
    assert set(current) == set(original_default)
    assert {key: item for key, item in current.items() if key != 'neutral_venue'} == {
        key: item for key, item in original_default.items() if key != 'neutral_venue'}
    assert row == before


def test_collect_live_propagates_v2_to_both_normalizers_without_db(tmp_path, monkeypatch):
    upcoming, finished = native('fixture'), native('history')
    source = {'content_hash': 'a' * 64, 'source_url': 'TESTONLY-calendar-source',
              'observed_at': NOW.isoformat()}
    history_source = {**source, 'content_hash': 'b' * 64, 'source_url': 'TESTONLY-history-source'}
    payload = {'data': [upcoming], 'meta': {'page': 1, 'per_page': 100, 'total_pages': 1}}
    resources = SimpleNamespace(
        snapshots=SimpleNamespace(read=lambda digest: json.dumps(payload)),
        usage={'requests': 0, 'cache_hits': 0, 'max_requests': 8, 'stopped_reason': None})
    monkeypatch.setattr(the_stats_context, 'Resources', lambda *args: resources)
    monkeypatch.setattr(module, 'calendar', lambda *args: ([upcoming], [source]))
    monkeypatch.setattr(module, '_page', lambda *args: ([finished], history_source, 1))
    config = {'leagues': [deepcopy(LEAGUE)], 'max_football_calls': 8}
    result = module.collect_live(config, SimpleNamespace(root=tmp_path), NOW.date(), lambda: NOW,
                                 construct_legacy=False, operating_contract_version=2)
    assert len(result['fixtures']['records']) == len(result['history']['records']) == 1
    assert result['fixtures']['records'][0]['neutral_venue'] is None
    assert result['history']['records'][0]['neutral_venue'] is None
    assert 'is_neutral' not in upcoming and 'is_neutral' not in finished
    assert resources.usage['requests'] == 0
