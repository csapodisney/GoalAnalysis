"""TESTONLY full job: native-shaped collection/model fakes, real profile pipeline."""

import json
import socket
import sqlite3
import subprocess
import urllib.request
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_daily_223_live import config as live_config
from test_full_day_persona_input import NOW, fixture, models, quote

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.portfolio import (
    LEGACY_PROFILE_IDS,
    MODEL_PROFILE_IDS,
    PROFILE_IDS,
    TIPSTER_PROFILE_IDS,
)
from goal_analysis.engine.diversification import ticket_signature
from goal_analysis.jobs import portfolio
from goal_analysis.jobs.league_scope import resolve_full_day_scope


@pytest.fixture(autouse=True)
def no_real_boundaries(monkeypatch):
    forbidden = Mock(side_effect=AssertionError('TESTONLY_NO_PROVIDER_DB_MODEL_PROCESS_OR_NETWORK'))
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    yield
    forbidden.assert_not_called()


def cohort():
    """Reuse existing native fixtures; twelve priced games support distinct cards."""
    config = live_config()
    fixtures = [fixture(index) for index in range(1, 46)]
    raw = [{
        'id': row['provider_fixture_id'], 'competition_id': row['provider_league_id'],
        'season_id': row['provider_season_id'], 'utc_date': row['kickoff'], 'status': 'scheduled',
        'home_team': {'id': row['provider_home_team_id'], 'name': row['home_team']},
        'away_team': {'id': row['provider_away_team_id'], 'name': row['away_team']},
    } for row in fixtures]
    _, receipt = resolve_full_day_scope(raw, NOW.date(), config['leagues'],
        operating_contract_version=2, page_receipts=[{
            'page': 1, 'per_page': 100, 'total_pages': 1, 'returned_rows': len(raw),
            'source_id': 'https://api.thestatsapi.com/api/football/matches?TESTONLY=calendar&page=1',
            'content_hash': canonical_sha256(raw),
        }])
    candidates = []
    products = [('h2h', 'home', 2.1), ('h2h', 'draw', 3.2), ('h2h', 'away', 3),
                ('totals_2_5', 'over', 2.1), ('totals_2_5', 'under', 2.1),
                ('btts', 'yes', 2), ('btts', 'no', 2), ('totals_0_5', 'over', 2)]
    for index in range(1, 13):
        for market, selection, price in products:
            if market == 'totals_2_5' and selection == 'over' and index % 3 == 0:
                price = 3  # Genuine-shaped quote role for protected DAILY_223.
            item = quote(index, price, selection, market)
            item['period'] = 'FIRST_HALF' if market == 'totals_0_5' else 'FULL_TIME'
            item['settlement'] = ('FIRST_HALF_PLUS_STOPPAGE' if item['period'] == 'FIRST_HALF'
                else 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES')
            item['sensitivity_note'] = 'TESTONLY immutable source snapshot; optional context UNKNOWN.'
            item['provider_market_id'] = {'h2h': 'match_odds', 'totals_2_5': 'total_goals',
                'btts': 'btts', 'totals_0_5': 'first_half_total_goals'}[market]
            candidates.append(item)
    history_rows = []
    for index in range(1, 13):
        target = fixtures[index - 1]
        for role in ('home', 'away'):
            team = target[f'provider_{role}_team_id']
            opponent = f'tm_{5000 + index}'
            home, away = (team, opponent) if role == 'home' else (opponent, team)
            for previous in range(6):
                kickoff = NOW - timedelta(days=3 * previous + 1)
                history_rows.append({
                    'provider': 'the_stats_api', 'fixture_id': f'mt_{100000 + len(history_rows)}',
                    'league_id': target['provider_league_id'], 'season': 'sn_2026', 'status': 'FT',
                    'kickoff': kickoff.isoformat(), 'observed_at': (kickoff + timedelta(hours=2)).isoformat(),
                    'home_team_id': home, 'away_team_id': away,
                    'full_time_home': 3 if role == 'home' else 1,
                    'full_time_away': 1 if role == 'home' else 3,
                    'first_half_home': 2 if role == 'home' else 0,
                    'first_half_away': 0 if role == 'home' else 2,
                    'source_id': 'TESTONLY-native-history-snapshot',
                })
    history = {'schema_version': 1, 'kind': 'DAILY223_HISTORY', 'provider': 'the_stats_api',
        'collected_at': (NOW - timedelta(minutes=2)).isoformat(), 'api_calls': 0, 'cache_hits': 0,
        'sources': [], 'records': history_rows}
    history['snapshot_sha256'] = canonical_sha256(history)
    predictions = models(12)
    for source, result in predictions.items():
        for index, prediction in enumerate(result['predictions'], 1):
            favored = ('home', 'away', 'draw')[index % 3]
            values = dict.fromkeys(('home', 'draw', 'away'), .175)
            values[favored] = .65
            values.update(over25=.7 if index % 2 else .3,
                          under25=.3 if index % 2 else .7, btts_yes=.7 if index % 2 else .3)
            prediction['probabilities'] = {key: value for key, value in values.items()
                                           if source != 'kepler' or key in {'home', 'draw', 'away'}}
            prediction['model_details']['version'] = 'TESTONLY-fixed-existing-model-output'
    artifacts = {
        'config': config, 'fixtures': {'observed_at': NOW.isoformat(), 'records': fixtures},
        'calendar': {'method': 'THE_STATS_NATIVE_DATE_WIDE', 'scope_receipt': receipt,
                     'date': NOW.date().isoformat(), 'returned_fixtures': len(fixtures)},
        'history': history, 'candidate_input': {
            'schema_version': 1, 'date': NOW.date().isoformat(), 'observed_at': NOW.isoformat(),
            'candidates': candidates, 'preferred_bookmakers': [candidates[0]['bookmaker']],
            'data_issues': [], 'matching': []},
        'odds_bulk': {}, 'report': {'usage': {'football_calls': 0, 'api_football_calls': 0},
            'data_issues': [], 'market_coverage': [], 'requested_market_coverage': []},
    }
    return config, artifacts, predictions


def test_actual_job_full_calendar_to_distinct_canonical_cards_and_scenario_reasoning(monkeypatch, tmp_path):
    owned_root = tmp_path.resolve()
    assert not owned_root.is_relative_to(Path(__file__).resolve().parents[1])
    assert not owned_root.is_relative_to(Path('C:/AI-Work/GoalAnalysis').resolve())
    (owned_root / 'TESTONLY-system-root.json').write_text(json.dumps({
        'TESTONLY': True, 'scope': 'full-day-portfolio-system', 'root': str(owned_root),
    }), encoding='utf-8')
    config, source, supplied_models = cohort()
    before = deepcopy((source, supplied_models))
    calls = {'collection': [], 'models': [], 'review': [], 'expert': [], 'package': []}

    def collected(*args, **kwargs):
        calls['collection'].append(kwargs)
        return {'artifacts': deepcopy(source)}

    def predictor(fixtures, history, now, **kwargs):
        calls['models'].append((deepcopy(fixtures), deepcopy(history), kwargs))
        assert now == NOW
        return deepcopy(supplied_models)

    class UnavailableReviewer:
        def review(self, packets, target_date, feedback=None):
            calls['review'].append(deepcopy(packets))
            return {'status': 'UNAVAILABLE', 'reviews': [], 'usage': {'requests': 0},
                    'message': 'TESTONLY optional review unavailable'}

    def unavailable_expert(report, options):
        calls['expert'].append(True)
        raise RuntimeError('TESTONLY optional expert unavailable')

    def unavailable_package(report, options):
        calls['package'].append(True)
        raise RuntimeError('TESTONLY optional package review unavailable')

    monkeypatch.setattr(portfolio, 'run_daily223_live', collected)
    result = portfolio.run_portfolio(config, {'operating_contract_version': 2},
        SimpleNamespace(provider='the_stats_api'), SimpleNamespace(provider='the_stats_api'), None,
        NOW.date(), reviewer=UnavailableReviewer(), clock=lambda: NOW, model_predictor=predictor,
        expert_reviewer=unavailable_expert, package_reviewer=unavailable_package)
    report = result['artifacts']['report']
    generated = report['generated_tickets']
    assert len(generated) >= 15, report['generation_capacity']
    assert len({card['profile_id'] for card in generated}) == len(generated)
    assert len({ticket_signature(card) for card in generated}) == len(generated)
    assert report['generation_capacity']['generated_count'] == len(generated) <= 30
    assert report['portfolio_objectives']['generated_count'] == len(generated)
    assert report['portfolio_objectives']['capacity_shortfall'] == 0
    assert report['tickets'] == [card for card in generated
                                if card['publication_scope'] == 'LIVE' and card['status'] == 'READY']
    assert all(card['status'] == 'DRAFT' and card['playable'] is False for card in generated)
    assert any(card['publication_scope'] == 'PAPER_AUDITION' for card in generated)
    assert report['astra']['status'] == report['expert_advisory']['status'] == report['grok_review']['status'] == 'UNAVAILABLE'
    assert len(calls['collection']) == len(calls['models']) == len(calls['expert']) == len(calls['package']) == 1
    assert calls['collection'][0]['operating_contract_version'] == 2
    assert calls['collection'][0]['selected_competition_ids'] is None
    assert tuple(calls['models'][0][2]['enabled_profiles']) == MODEL_PROFILE_IDS
    assert report['numeric_models'] == supplied_models
    expected = sorted(row['fixture_id'] for row in source['fixtures']['records'])
    assert sorted(row['fixture_id'] for row in calls['models'][0][0]) == expected
    assert report['full_day_calendar']['provider_fixture_ids'] == expected
    assert report['full_day_calendar']['provider_calendar_status'] == 'COMPLETE'
    assert report['full_day_calendar']['pages'][0]['returned_rows'] == 45
    assert {card['profile_id'] for card in report['recommendations']} == set(PROFILE_IDS)
    recommendations = {card['profile_id']: card for card in report['recommendations']}
    core = {row['profile_id']: row for row in report['profiles'] if row['profile_id'] in LEGACY_PROFILE_IDS}
    for name in PROFILE_IDS:
        trace = (core[name] if name in LEGACY_PROFILE_IDS else recommendations[name])['selection_diagnostics']
        assert trace['input_fixture_ids'] == expected, name
        assert trace['input_fixture_count'] == 45, name
        unpriced = next(row for row in trace['fixture_decisions'] if row['fixture_id'] == fixture(45)['fixture_id'])
        assert 'NO_VALID_QUOTES' in unpriced['reasons'], (name, unpriced)
        if name in MODEL_PROFILE_IDS:
            assert trace['model_input_fixture_ids'] == expected, name
            assert 'MODEL_PREDICTION_UNKNOWN' in unpriced['reasons'], (name, unpriced)
    scenarios = report['profile_predecision_scenarios']
    assert scenarios['phase'] == 'BEFORE_FINAL_PROFILE_DECISION'
    assert scenarios['external_requests'] == 0 and scenarios['status'] in {'AVAILABLE', 'PARTIAL'}
    persona = next(card for card in generated if card['profile_id'] in TIPSTER_PROFILE_IDS)
    assert persona['decision_reasoning']['phase'] == 'BEFORE_FINAL_PROFILE_DECISION'
    assert persona['decision_reasoning']['scenario_risk_facts']
    assert persona['decision_reasoning']['numeric_probability_or_rank_changed'] is False
    assessed = next(row for row in scenarios['profiles'] if row['profile_id'] == persona['profile_id'])
    assert any(model['known_leg_count'] for model in assessed['models'].values()), assessed
    assert any('OPTIONAL_CONTEXT_UNAVAILABLE' in warning
               for warning in persona['decision_reasoning']['uncertainties'])
    from goal_analysis.jobs.profile_scenarios import source_leg_signature

    for card in generated:
        assert card['decision_reasoning']['source_leg_signature'] == source_leg_signature(card), card['profile_id']
        assert any(line.startswith('[S07]') for line in card['reasoning']), card['profile_id']
    assert report['real_wager_placed'] is False
    assert (source, supplied_models) == before
    report_path = owned_root / 'TESTONLY-system-report.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    assert json.loads(report_path.read_text(encoding='utf-8')) == report
    print(f'TESTONLY_SYSTEM_REPORT={report_path} GENERATED_COUNT={len(generated)} PROFILE_INPUT_RECEIPTS=30')
