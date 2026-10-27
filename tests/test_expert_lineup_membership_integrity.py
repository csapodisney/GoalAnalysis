"""Confirmed XI membership confidence; pure TESTONLY inputs and no external operations."""
import socket
import sqlite3
import subprocess
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs.expert_advisory import _stats_fixture_signal, weight_sports_context

NOW = datetime(2026, 10, 27, tzinfo=UTC)
ATTEMPTS = []


@pytest.fixture(autouse=True)
def no_operations(monkeypatch):
    from goal_analysis.providers.the_stats_api import TheStatsApiClient
    from goal_analysis.storage import Database
    from goal_analysis.storage.temporal import TemporalStore
    from goal_analysis.storage.training_datasets import DatasetStore

    def forbidden(*args, **kwargs):
        ATTEMPTS.append('FORBIDDEN_EXTERNAL_OR_DATABASE_OPERATION')
        raise AssertionError(ATTEMPTS[-1])
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    for cls in (Database, DatasetStore, TemporalStore, TheStatsApiClient):
        monkeypatch.setattr(cls, '__init__', forbidden)
    yield
    assert not ATTEMPTS


def source(char='1'):
    return {'content_hash': char * 64, 'observed_at': NOW.isoformat(),
            'source_url': 'https://example.test/explicit-TESTONLY'}


def fact(category, subject, data, char='3'):
    return {'category': category, 'subject_id': subject, 'status': 'AVAILABLE',
            'data': data, 'source': source(char)}


def players(prefix, count=11):
    return [{'player_id': prefix + str(i), 'position': 'F'} for i in range(count)]


def record(home=None, away=None, *, confirmed=True, kind='team_sheet', active=False):
    mapping = {'home_team_id': 'h', 'away_team_id': 'a', 'season_id': 's', 'competition_id': 'c'}
    facts = [fact('team_season_stats', team,
                  {'team_id': team, 'season_id': 's', 'competition_id': 'c',
                   'xg_matches': 10, 'expected_goals_for': 20,
                   'expected_goals_against': 10, 'goals_for': 20}, char)
             for team, char in [('h', '1'), ('a', '2')]]
    facts += [fact('player_season_stats', pid,
                   {'player_id': pid, 'team_id': team, 'season_id': 's',
                    'competition_id': 'c', 'goals': 8, 'expected_goals': 8})
              for pid, team in [('hp', 'h'), ('ap', 'a')]]
    if home is not None or away is not None:
        facts.append(fact('lineup', 'm', {'match_id': 'm', 'type': kind,
            'confirmed': confirmed, 'home_team_id': 'h', 'away_team_id': 'a',
            'home_players': home, 'away_players': away}))
    if active:
        facts.append(fact('injuries_suspensions', 'h', {'team_id': 'h',
            'injuries': [{'player_id': 'hp', 'active': True}], 'suspensions': []}))
    return {'status': 'MATCHED', 'mapping': mapping, 'facts': facts,
            'kickoff': (NOW + timedelta(hours=8)).isoformat()}


def by_player(signal):
    return {row['player_id']: row for row in signal['player_context']}


@pytest.mark.parametrize('label,home', [
    ('empty', []), ('partial', players('h', 5)),
    ('duplicate', players('h', 10) + [{'player_id': 'h0'}]),
])
def test_incomplete_confirmed_xi_never_invents_absence(label, home):
    value = record(home, players('a'))
    before = deepcopy(value)
    signal = _stats_fixture_signal(value)
    assert by_player(signal)['hp']['availability'] == 'AVAILABLE_OR_UNKNOWN'
    assert signal['home_xg_rate'] == signal['baseline_home_xg_rate']
    assert by_player(signal)['ap']['availability'] == 'CONFIRMED_NOT_STARTING'
    assert not signal['lineup_membership']['h']['negative_membership_eligible']
    assert signal['lineup_membership']['a']['negative_membership_eligible']
    assert value == before


def test_complete_xi_preserves_legitimate_negative_membership():
    value = record(players('h'), players('a'))
    signal = _stats_fixture_signal(value)
    assert by_player(signal)['hp']['availability'] == 'CONFIRMED_NOT_STARTING'
    assert by_player(signal)['ap']['availability'] == 'CONFIRMED_NOT_STARTING'
    assert signal['home_xg_rate'] / signal['baseline_home_xg_rate'] == pytest.approx(.92)
    assert signal['away_xg_rate'] / signal['baseline_away_xg_rate'] == pytest.approx(.92)
    assert all(row['negative_membership_eligible'] for row in signal['lineup_membership'].values())


def test_complete_home_incomplete_away_is_independent():
    signal = _stats_fixture_signal(record(players('h'), []))
    assert by_player(signal)['hp']['availability'] == 'CONFIRMED_NOT_STARTING'
    assert by_player(signal)['ap']['availability'] == 'AVAILABLE_OR_UNKNOWN'
    assert signal['away_xg_rate'] == signal['baseline_away_xg_rate']


@pytest.mark.parametrize('home', [None, 'malformed', {}, [None], [{'player_id': None}],
                                [{'player_id': ' '}], players('h', 12)])
def test_malformed_or_non_eleven_side_is_local_unknown(home):
    signal = _stats_fixture_signal(record(home, players('a')))
    assert by_player(signal)['hp']['availability'] == 'AVAILABLE_OR_UNKNOWN'
    assert signal['home_xg_rate'] == signal['baseline_home_xg_rate']
    assert by_player(signal)['ap']['availability'] == 'CONFIRMED_NOT_STARTING'


@pytest.mark.parametrize('confirmed,kind', [(False, 'predicted'), (True, 'predicted'),
                                          (False, 'team_sheet')])
def test_predicted_or_unconfirmed_does_not_prove_absence(confirmed, kind):
    signal = _stats_fixture_signal(record(players('h'), players('a'),
                                           confirmed=confirmed, kind=kind))
    assert all(row['availability'] == 'AVAILABLE_OR_UNKNOWN' for row in signal['player_context'])
    assert not signal['player_availability_applied']


def test_positive_partial_starter_and_replacement_reduce_independent_absence():
    value = record([{'player_id': 'replacement', 'position': 'F'}], [], active=True)
    value['facts'].append(fact('player_season_stats', 'replacement',
        {'player_id': 'replacement', 'team_id': 'h', 'season_id': 's',
         'competition_id': 'c', 'goals': 8, 'expected_goals': 8}))
    signal = _stats_fixture_signal(value)
    assert by_player(signal)['replacement']['availability'] == 'AVAILABLE_OR_UNKNOWN'
    assert by_player(signal)['hp']['availability'] == 'ACTIVE_ABSENCE'
    assert not signal['lineup_membership']['h']['negative_membership_eligible']
    assert signal['replacement_coverage']['h'] == pytest.approx(.2)
    assert signal['home_xg_rate'] / signal['baseline_home_xg_rate'] == pytest.approx(.936)


def test_active_absence_survives_incomplete_lineup():
    signal = _stats_fixture_signal(record([], [], active=True))
    assert by_player(signal)['hp']['availability'] == 'ACTIVE_ABSENCE'
    assert signal['home_xg_rate'] / signal['baseline_home_xg_rate'] == pytest.approx(.92)
    assert by_player(signal)['ap']['availability'] == 'AVAILABLE_OR_UNKNOWN'


def test_shared_starter_identity_is_not_complete_coverage():
    home, away = players('h'), players('a')
    away[0]['player_id'] = home[0]['player_id']
    signal = _stats_fixture_signal(record(home, away))
    assert all(row['availability'] == 'AVAILABLE_OR_UNKNOWN' for row in signal['player_context'])
    assert all(row['status'] == 'CONFLICTING_SIDE_IDENTITIES'
               for row in signal['lineup_membership'].values())


def test_wrong_team_binding_is_local_unknown():
    value = record(players('h'), players('a'))
    value['facts'][-1]['data']['home_team_id'] = 'different-team'
    signal = _stats_fixture_signal(value)
    assert by_player(signal)['hp']['availability'] == 'AVAILABLE_OR_UNKNOWN'
    assert by_player(signal)['ap']['availability'] == 'CONFIRMED_NOT_STARTING'


def test_no_lineup_preserves_missing_scope_and_base_rates():
    value = record()
    signal = _stats_fixture_signal(value)
    assert 'lineup_membership' not in signal
    assert signal['data_coverage']['lineup'] == 'MISSING'
    assert signal['home_xg_rate'] == signal['baseline_home_xg_rate']
    assert signal['away_xg_rate'] == signal['baseline_away_xg_rate']
    assert not signal['player_availability_applied']


def test_incomplete_xi_keeps_existing_rank_consumer_and_daily_immutable():
    fixture = record([], [])
    leg = {'fixture_id': 'TESTONLY:fixture', 'selection_key': 'over',
           'market_key': 'totals_2_5', 'period': 'FULL_TIME',
           'model_probability': .62, 'decimal_price': 2.0}
    option = {'quality': .8, 'scoring': {'confidence': .7},
              'recommendation': {'profile_id': 'club', 'legs': [leg]}}
    daily = deepcopy(option)
    daily['recommendation']['profile_id'] = 'daily223'
    before = {'club': [deepcopy(option)], 'daily223': [deepcopy(daily)]}
    stats = {'version': 'explicit-TESTONLY',
             'context_by_fixture': {'TESTONLY:fixture': fixture}}
    original_stats, original_options = deepcopy(stats), deepcopy(before)
    result, audit = weight_sports_context(before, stats)
    assert audit['eligible_fixtures'] == 1
    assert audit['evidence_attached_options'] == 1
    assert audit['player_availability_adjusted_legs'] == 0
    assert result['club'][0]['quality'] > 0
    assert result['club'][0]['scoring']['the_stats_api']['legs'][0]['base_model_probability'] == .62
    assert result['club'][0]['recommendation']['legs'] == [leg]
    assert result['daily223'] == before['daily223']
    assert before == original_options and stats == original_stats
