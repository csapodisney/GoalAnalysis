from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs.expert_advisory import (
    _save,
    _weather_fixtures,
    analyze_report,
    refresh_news,
    snapshot,
    weight_options,
    weight_sports_context,
)

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def option(profile='club', selection='over', market='totals_2_5', period='FULL_TIME'):
    return {'quality': .8, 'scoring': {'confidence': .7}, 'recommendation': {
        'profile_id': profile, 'combined_price': 18.2,
        'legs': [{'fixture_id': 'api_football:1', 'selection_key': selection,
                  'market_key': market, 'period': period, 'model_probability': .62}]}}


def weather(valid=True):
    return {'version': 'test', 'fixtures': [{'fixture_id': 'api_football:1',
        'live_weight_eligible': valid, 'market_effects': {'over25': 1.1, 'under25': .9}}]}


def test_ranking_direction_daily_protection_no_probability_or_odds_changes():
    options = {'daily223': [option('daily223')], 'over': [option()],
               'under': [option(selection='under')], 'h1': [option(period='FIRST_HALF')],
               'other': [option(market='totals_3_5')]}
    original = deepcopy(options)
    result, count = weight_options(options, weather())
    assert count == 2
    assert result['over'][0]['quality'] == pytest.approx(.88)
    assert result['under'][0]['quality'] == pytest.approx(.72)
    for profile in ('daily223', 'h1', 'other'):
        assert result[profile] == original[profile]
    for profile in result:
        assert result[profile][0]['recommendation'] == original[profile][0]['recommendation']
    assert options == original
    assert weight_options(result, weather()) == (result, 0)


def test_unvalidated_history_neutral_and_long_ticket_effect_bounded():
    value = option()
    value['recommendation']['legs'] *= 10
    assert weight_options({'club': [value]}, weather(False)) == ({'club': [value]}, 0)
    assert weight_options({'club': [value]}, weather())[0]['club'][0]['quality'] == pytest.approx(.88)


def test_stats_xg_is_bounded_visible_and_daily_protected():
    stats = {'version': 'stats-test', 'context_by_fixture': {'api_football:1': {
        'status': 'MATCHED',
        'kickoff': (NOW + timedelta(hours=8)).isoformat(),
        'mapping': {'home_team_id': 'h', 'away_team_id': 'a',
                    'season_id': 's', 'competition_id': 'c'},
        'facts': [
            {'category': 'team_season_stats', 'subject_id': 'h', 'status': 'AVAILABLE',
             'data': {'team_id': 'h', 'season_id': 's', 'competition_id': 'c',
                      'xg_matches': 10, 'expected_goals_for': 20,
                      'expected_goals_against': 10},
             'source': {'content_hash': '1' * 64, 'observed_at': NOW.isoformat(),
                        'source_url': 'https://example.test/h'}},
            {'category': 'team_season_stats', 'subject_id': 'a', 'status': 'AVAILABLE',
             'data': {'team_id': 'a', 'season_id': 's', 'competition_id': 'c',
                      'xg_matches': 10, 'expected_goals_for': 18,
                      'expected_goals_against': 20},
             'source': {'content_hash': 'a' * 64, 'observed_at': NOW.isoformat(),
                        'source_url': 'https://example.test/a'}},
            {'category': 'match_odds', 'subject_id': 'm', 'status': 'AVAILABLE',
             'data': {'match_id': 'm', 'timestamp_basis': 'PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME',
                      'quotes': [{'bookmaker': 'Bet365', 'market_key': 'totals',
                                  'selection_key': 'over', 'point': 2.5,
                                  'opening_price': 1.9, 'last_price': 2.1}]},
             'source': {'content_hash': 'b' * 64, 'observed_at': NOW.isoformat(),
                        'source_url': 'https://example.test/odds'}},
        ]}}}
    club = option()
    club['recommendation']['legs'][0]['decimal_price'] = 2.0
    club['recommendation']['legs'][0]['bookmaker'] = 'bet365'
    daily = deepcopy(club)
    daily['recommendation']['profile_id'] = 'daily223'
    original_daily = deepcopy(daily)
    result, audit = weight_sports_context({'club': [club], 'daily223': [daily]}, stats)
    assert .8 * .96 <= result['club'][0]['quality'] <= .8 * 1.04
    assert audit == {'weighted_options': 1, 'evidence_attached_options': 1,
                     'eligible_fixtures': 1, 'player_context_legs': 0,
                     'player_availability_adjusted_legs': 0,
                     'daily223_protected': True}
    evidence = result['club'][0]['scoring']['the_stats_api']
    assert evidence['kind'] == 'SEASON_XG_RANKING_TIEBREAKER_NOT_PROBABILITY'
    assert len(evidence['legs']) == 1 and len(evidence['legs'][0]['sources']) == 2
    assert evidence['legs'][0]['base_model_probability'] == .62
    assert 0 < evidence['legs'][0]['shadow_ensemble_probability'] < 1
    assert evidence['legs'][0]['shadow_only'] is True
    assert evidence['legs'][0]['shadow_quote_comparison']['last_price'] == 2.1
    assert evidence['legs'][0]['shadow_quote_comparison']['candidate_price'] == 2.0
    assert result['club'][0]['recommendation']['legs'][0]['model_probability'] == .62
    assert result['daily223'][0] == original_daily
    assert weight_sports_context(result, stats)[0] == result


def test_confirmed_scorer_absence_changes_only_xg_tiebreaker_and_protects_daily():
    source = lambda char: {'content_hash': char * 64, 'observed_at': NOW.isoformat(),
                           'source_url': 'https://example.test/context'}
    stats = {'version': 'stats-player-test', 'context_by_fixture': {'api_football:1': {
        'status': 'MATCHED', 'kickoff': (NOW + timedelta(hours=8)).isoformat(),
        'mapping': {'home_team_id': 'h', 'away_team_id': 'a',
                    'season_id': 's', 'competition_id': 'c'},
        'facts': [
            {'category': 'team_season_stats', 'subject_id': 'h', 'status': 'AVAILABLE',
             'data': {'team_id': 'h', 'season_id': 's', 'competition_id': 'c',
                      'xg_matches': 10, 'expected_goals_for': 20,
                      'expected_goals_against': 10, 'goals_for': 20}, 'source': source('1')},
            {'category': 'team_season_stats', 'subject_id': 'a', 'status': 'AVAILABLE',
             'data': {'team_id': 'a', 'season_id': 's', 'competition_id': 'c',
                      'xg_matches': 10, 'expected_goals_for': 18,
                      'expected_goals_against': 20, 'goals_for': 18}, 'source': source('2')},
            {'category': 'injuries_suspensions', 'subject_id': 'h', 'status': 'AVAILABLE',
             'data': {'team_id': 'h', 'injuries': [{'player_id': 'p', 'active': True}],
                      'suspensions': []}, 'source': source('3')},
            {'category': 'player_season_stats', 'subject_id': 'p', 'status': 'AVAILABLE',
             'data': {'player_id': 'p', 'team_id': 'h', 'season_id': 's',
                      'competition_id': 'c', 'goals': 8, 'expected_goals': 8,
                      'minutes_played': 800}, 'source': source('4')},
        ]}}}
    over, under = option(), option(selection='under')
    over['recommendation']['legs'][0]['decimal_price'] = 2.0
    under['recommendation']['legs'][0]['decimal_price'] = 2.0
    daily = option('daily223')
    before = deepcopy({'over': [over], 'under': [under], 'daily223': [daily]})
    result, audit = weight_sports_context(before, stats)
    over_leg = result['over'][0]['scoring']['the_stats_api']['legs'][0]
    under_leg = result['under'][0]['scoring']['the_stats_api']['legs'][0]
    player = over_leg['player_context'][0]
    assert player['availability'] == 'ACTIVE_ABSENCE'
    assert player['attacking_share'] == pytest.approx(.4)
    assert player['attack_multiplier'] == pytest.approx(.92)
    assert over_leg['player_availability_applied'] is True
    assert under_leg['season_xg_probability'] > 1 - over_leg['season_xg_probability'] - 1e-12
    assert result['daily223'][0] == before['daily223'][0]
    assert audit['daily223_protected'] is True


def test_stats_sparse_or_unmatched_context_is_neutral():
    value = option()
    value['recommendation']['legs'][0]['decimal_price'] = 2.0
    sparse = {'context_by_fixture': {'api_football:1': {'status': 'MATCHED',
        'mapping': {'home_team_id': 'h', 'away_team_id': 'a'}, 'facts': []}}}
    assert weight_sports_context({'club': [value]}, sparse) == (
        {'club': [value]}, {'weighted_options': 0, 'evidence_attached_options': 0,
                            'eligible_fixtures': 0, 'player_context_legs': 0,
                            'player_availability_adjusted_legs': 0,
                            'daily223_protected': True})


def test_only_explicit_provider_identity_joined():
    fixtures = [{'fixture_id': 'api_football:1', 'home_team': 'City',
                 'api_football_home_team_id': '34', 'api_football_away_team_id': 'bad',
                 'api_football_league_id': 39}]
    clean = _weather_fixtures(fixtures)[0]
    assert clean['home_team_id'] == 'api_football:34'
    assert clean['league_id'] == 'api_football:39'
    assert 'away_team_id' not in clean and 'home_team_id' not in fixtures[0]


def test_snapshot_integrity_date_and_run_boundaries(tmp_path):
    result = {'date': '2026-10-18', 'run_id': 'a', 'value': 1}
    _save(tmp_path, result)
    assert snapshot(tmp_path, '2026-10-18', {'run_id': 'a'})['status'] == 'AVAILABLE'
    assert snapshot(tmp_path, '2026-10-18', {'run_id': 'b'})['status'] == 'DIFFERENT_RUN'
    path = tmp_path / 'data/arthur/experts/2026-10-18.json'
    path.write_text(path.read_text().replace('"value":1', '"value":2'))
    assert snapshot(tmp_path, '2026-10-18')['status'] == 'NOT_RUN'
    assert snapshot(tmp_path, '../../config/x')['status'] == 'NOT_RUN'


@pytest.mark.parametrize('age,expected', [(1, 0), (10, 1)])
def test_failed_forecast_recent_attempt_not_repeated_old_attempt_retried(tmp_path, age, expected):
    class Provider:
        calls = 0

        def forecast(self, fixture):
            self.calls += 1
            return {'status': 'UNKNOWN', 'fixture_id': fixture['fixture_id']}

    fixture = {'fixture_id': 'api_football:1', 'kickoff': (NOW + timedelta(hours=8)).isoformat(),
               'weather': {'status': 'UNKNOWN', 'time_basis': 'LOOKUP_ATTEMPT_AT',
                           'observed_at': (NOW - timedelta(minutes=age)).isoformat()}}
    report = {'date': '2026-10-18', 'run_id': 'test', 'fixtures': [fixture],
              'recommendations': [{'profile_id': 'daily223', 'legs': [{'fixture_id': fixture['fixture_id']}]}]}
    original, provider = deepcopy(report), Provider()
    result = analyze_report(tmp_path, report, weather_provider=provider, clock=lambda: NOW,
                            dataset={'matches': [], 'coverage': {}, 'data_hash': 'empty'})
    assert result['additional_weather_lookups'] == provider.calls == expected
    assert result['additional_llm_requests'] == 0
    assert report == original
    assert not result['weather']['fixtures'][0]['live_weight_eligible']
    assert snapshot(tmp_path, report['date'], report)['analysis_hash'] == result['analysis_hash']
    refreshed = refresh_news(tmp_path, {**report, 'expert_advisory': result}, clock=lambda: NOW)
    assert refreshed['recommendations'] == report['recommendations']
    assert snapshot(tmp_path, report['date'], report)['analysis_hash'] == refreshed['expert_advisory']['analysis_hash']


def test_portfolio_rejects_expert_daily_mutation(tmp_path):
    from test_portfolio_flow import RecordedReviewer, environment

    from goal_analysis.jobs.portfolio import run_portfolio

    cfg, football, odds, cache, _calls, _state, _db = environment(tmp_path)

    def bad_expert(report, options):
        report['recommendations'][0]['profile_id'] = 'tampered'
        return {'report': report, 'options': options}

    from test_daily_223_live import NOW as RUN_NOW
    report = run_portfolio(cfg, {}, football, odds, cache, RUN_NOW.date(), RecordedReviewer(),
        clock=lambda: RUN_NOW, expert_reviewer=bad_expert)['artifacts']['report']
    assert report['expert_advisory']['status'] == 'UNAVAILABLE'
    assert any(row['profile_id'] == 'daily223' for row in report['recommendations'])


def test_news_archive_failure_keeps_grok_repair(tmp_path, monkeypatch):
    from goal_analysis.jobs import expert_advisory

    def fail(*args, **kwargs):
        raise OSError('private failure details')

    monkeypatch.setattr(expert_advisory, '_refresh_news', fail)
    report = {'grok_review': {'changed_profiles': ['club']}, 'tickets': [{'id': 'repaired'}]}
    result = refresh_news(tmp_path, report)
    assert result['tickets'] == report['tickets'] and result['grok_review'] == report['grok_review']
    assert result['expert_news_refresh']['status'] == 'UNAVAILABLE'


def test_weather_failure_still_runs_motivation(tmp_path):
    class FailedWeather:
        def forecast(self, fixture):
            raise TimeoutError('provider unavailable')

    fixture = {'fixture_id': 'api_football:1', 'kickoff': (NOW + timedelta(hours=8)).isoformat()}
    report = {'date': '2026-10-18', 'run_id': 'test', 'fixtures': [fixture],
              'recommendations': [{'profile_id': 'club', 'legs': [fixture]}]}
    result = analyze_report(tmp_path, report, weather_provider=FailedWeather(), clock=lambda: NOW,
        dataset={'matches': [], 'coverage': {}, 'data_hash': 'empty'})
    assert result['weather']['fixtures'][0]['forecast']['reason'] == 'WEATHER_LOOKUP_FAILED'
    assert len(result['motivation']['fixtures']) == 1


def test_failed_analysis_preserves_request_accounting(tmp_path, monkeypatch):
    from goal_analysis.jobs import expert_advisory

    class Client:
        def request(self, endpoint, params):
            return {'response': []}

    def fail(root, report, **kwargs):
        kwargs['standings_client'].request('standings', {'league': 39})
        raise OSError('archive failure after paid request')

    monkeypatch.setattr(expert_advisory, 'analyze_report', fail)
    original = {'daily223': [option('daily223')]}
    result = expert_advisory.review_experts(tmp_path, {}, original, standings_client=Client())
    assert result['report']['expert_advisory']['standings_collection']['requests'] == 1
    assert result['options'] == original


def _player_stats_fixture(extra_facts):
    source = lambda char: {'content_hash': char * 64, 'observed_at': NOW.isoformat(),
                           'source_url': 'https://example.test/context'}
    return {'status': 'MATCHED', 'kickoff': (NOW + timedelta(hours=8)).isoformat(),
        'mapping': {'home_team_id': 'h', 'away_team_id': 'a',
                    'season_id': 's', 'competition_id': 'c'},
        'facts': [
            {'category': 'team_season_stats', 'subject_id': 'h', 'status': 'AVAILABLE',
             'data': {'team_id': 'h', 'season_id': 's', 'competition_id': 'c',
                      'xg_matches': 10, 'expected_goals_for': 20,
                      'expected_goals_against': 10, 'goals_for': 20}, 'source': source('1')},
            {'category': 'team_season_stats', 'subject_id': 'a', 'status': 'AVAILABLE',
             'data': {'team_id': 'a', 'season_id': 's', 'competition_id': 'c',
                      'xg_matches': 10, 'expected_goals_for': 18,
                      'expected_goals_against': 20, 'goals_for': 18}, 'source': source('2')},
            *extra_facts,
        ]}


def test_multiple_attacker_absences_are_combined_with_a_team_cap():
    from goal_analysis.jobs.expert_advisory import _stats_fixture_signal
    source = lambda char: {'content_hash': char * 64, 'observed_at': NOW.isoformat(),
                           'source_url': 'https://example.test/context'}
    record = _player_stats_fixture([
        {'category': 'injuries_suspensions', 'subject_id': 'h', 'status': 'AVAILABLE',
         'data': {'team_id': 'h', 'injuries': [
             {'player_id': 'p1', 'active': True}, {'player_id': 'p2', 'active': True}],
                  'suspensions': []}, 'source': source('3')},
        {'category': 'player_season_stats', 'subject_id': 'p1', 'status': 'AVAILABLE',
         'data': {'player_id': 'p1', 'team_id': 'h', 'season_id': 's',
                  'competition_id': 'c', 'goals': 8, 'expected_goals': 8}, 'source': source('4')},
        {'category': 'player_season_stats', 'subject_id': 'p2', 'status': 'AVAILABLE',
         'data': {'player_id': 'p2', 'team_id': 'h', 'season_id': 's',
                  'competition_id': 'c', 'goals': 6, 'expected_goals': 6}, 'source': source('5')},
    ])
    signal = _stats_fixture_signal(record)
    multipliers = {row['player_id']: row['attack_multiplier'] for row in signal['player_context']}
    assert multipliers['p1'] == pytest.approx(.8648)
    assert multipliers['p2'] == pytest.approx(.8648)
    assert .85 <= multipliers['p1'] < .92


def test_confirmed_replacement_reduces_but_does_not_erase_absence_penalty():
    from goal_analysis.jobs.expert_advisory import _stats_fixture_signal
    source = lambda char: {'content_hash': char * 64, 'observed_at': NOW.isoformat(),
                           'source_url': 'https://example.test/context'}
    record = _player_stats_fixture([
        {'category': 'injuries_suspensions', 'subject_id': 'h', 'status': 'AVAILABLE',
         'data': {'team_id': 'h', 'injuries': [{'player_id': 'p1', 'active': True}],
                  'suspensions': []}, 'source': source('3')},
        {'category': 'lineup', 'subject_id': 'm', 'status': 'AVAILABLE',
         'data': {'match_id': 'm', 'confirmed': True, 'type': 'team_sheet',
                  'home_team_id': 'h', 'away_team_id': 'a',
                  'home_players': [{'player_id': 'p2', 'name': 'Replacement', 'position': 'F'}],
                  'away_players': []}, 'source': source('4')},
        {'category': 'player_season_stats', 'subject_id': 'p1', 'status': 'AVAILABLE',
         'data': {'player_id': 'p1', 'team_id': 'h', 'season_id': 's',
                  'competition_id': 'c', 'goals': 8, 'expected_goals': 8}, 'source': source('5')},
        {'category': 'player_season_stats', 'subject_id': 'p2', 'status': 'AVAILABLE',
         'data': {'player_id': 'p2', 'team_id': 'h', 'season_id': 's',
                  'competition_id': 'c', 'goals': 8, 'expected_goals': 8}, 'source': source('6')},
    ])
    signal = _stats_fixture_signal(record)
    assert signal['replacement_coverage']['h'] == pytest.approx(.2)
    assert signal['home_xg_rate'] / signal['baseline_home_xg_rate'] == pytest.approx(.936)
    assert signal['data_coverage']['lineup'] == 'CONFIRMED'


def test_goalkeeper_absence_has_market_specific_neutral_bounded_effect():
    from goal_analysis.jobs.expert_advisory import _stats_fixture_signal
    source = lambda char: {'content_hash': char * 64, 'observed_at': NOW.isoformat(),
                           'source_url': 'https://example.test/context'}
    record = _player_stats_fixture([
        {'category': 'injuries_suspensions', 'subject_id': 'h', 'status': 'AVAILABLE',
         'data': {'team_id': 'h', 'injuries': [{'player_id': 'gk', 'active': True}],
                  'suspensions': []}, 'source': source('3')},
        {'category': 'squad', 'subject_id': 'h', 'status': 'AVAILABLE',
         'data': {'team_id': 'h', 'players': [
             {'player_id': 'gk', 'name': 'Keeper', 'position': 'GK'}],
                  'total': 1, 'truncated': False}, 'source': source('4')},
    ])
    signal = _stats_fixture_signal(record)
    effects = signal['player_market_effects']
    assert signal['away_xg_rate'] / signal['baseline_away_xg_rate'] == pytest.approx(1.06)
    assert effects['totals_2_5:over'] > 1
    assert effects['totals_2_5:under'] < 1
    assert effects['btts:yes'] > 1
    assert effects['h2h:away'] > 1
    assert signal['absence_context'][0]['role'] == 'GOALKEEPER'


def test_missing_player_sources_are_explicit_and_neutral():
    from goal_analysis.jobs.expert_advisory import _stats_fixture_signal
    signal = _stats_fixture_signal(_player_stats_fixture([]))
    assert signal['home_xg_rate'] == signal['baseline_home_xg_rate']
    assert signal['away_xg_rate'] == signal['baseline_away_xg_rate']
    assert signal['data_coverage'] == {
        'injuries': 'MISSING', 'lineup': 'MISSING', 'squad_positions': 'MISSING',
        'player_stats_by_team': {'h': 0, 'a': 0}}
    assert not signal['player_availability_applied']
