"""TESTONLY native provider-shaped facts, with no provider/DB/model execution."""

import socket
import sqlite3
import subprocess
import urllib.request
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.providers.the_stats_odds import bookmaker_key
from goal_analysis.tipsters.catalog import TIPSTER_CATALOG, TIPSTER_PROFILE_IDS
from goal_analysis.tipsters.recommendations import (
    _fixtures,
    _pool,
    build_tipster_recommendations,
)

NOW = datetime(2026, 10, 28, 10, tzinfo=UTC)
BOOK = 'TESTONLY Book'
BENCE = 'tipster_bastya_bence'
MARK = 'tipster_hazai_mark'
MIRA = 'tipster_csendes_mira'


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('NO_PROVIDER_DATABASE_NETWORK_PROCESS_OR_MODEL_EXECUTION')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def fixture(identifier):
    return {'fixture_id': f'the_stats_api:mt_{identifier}', 'source_provider': 'the_stats_api',
            'provider_fixture_id': f'mt_{identifier}', 'provider_league_id': 'comp_4643',
            'provider_season_id': 'sn_2026', 'provider_home_team_id': f'tm_{identifier * 2}',
            'provider_away_team_id': f'tm_{identifier * 2 + 1}', 'config_league_id': '78',
            'home_team': f'TEST Home {identifier}', 'away_team': f'TEST Away {identifier}',
            'kickoff': (NOW + timedelta(hours=8)).isoformat(), 'provider_status': 'NS',
            'competition': 'TEST_LEAGUE', 'competition_type': 'LEAGUE', 'sport_key': 'soccer_test',
            'target_date': NOW.date().isoformat()}


def event(identifier, **changes):
    return {**fixture(identifier), 'status': 'ROUTED',
            'competition_name': 'TEST League', 'source_id': 'https://test.invalid/calendar', **changes}


def quote(identifier, price=2.1, selection='home', market='h2h'):
    return {**fixture(identifier), 'candidate_id': f'TEST-quote-{identifier}-{market}-{selection}',
            'decimal_price': price, 'market_key': market, 'selection_key': selection,
            'period': 'FULL_TIME', 'event_status': 'SCHEDULED', 'quote_available': True,
            'bookmaker': bookmaker_key(BOOK), 'region': 'eu', 'currency': 'EUR',
            'quote_provider': 'the_stats_api', 'provider_bookmaker': BOOK,
            'provider_market_id': 'match_odds',
            'quote_source_id': f'https://api.thestatsapi.com/api/football/matches/mt_{identifier}/odds',
            'quote_source_sha256': 'a' * 64, 'quote_observed_at': NOW.isoformat(),
            'quote_time_basis': 'PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME', 'evidence': []}


def models(count):
    values = {'home': .65, 'draw': .2, 'away': .15,
              'over25': .35, 'under25': .65, 'btts_yes': .35}
    return {source: {'profile_id': source, 'status': 'READY', 'predictions': [
        {'fixture_id': fixture(identifier)['fixture_id'], 'probabilities': values,
         'stability': dict.fromkeys(values, .03),
         'model_details': {'cutoff': NOW.isoformat(), 'training_count': 123,
                           'source_id': f'TEST-model-snapshot:{source}', 'calibration': 'unvalidated'}}
        for identifier in range(1, count + 1)]} for source in ('gauss', 'kepler', 'laplace')}


def data(count=4, *, price=2.1, universe=None):
    universe = universe if universe is not None else [event(identifier) for identifier in range(1, count + 1)]
    return {'date': NOW.date().isoformat(),
            'fixtures': [fixture(identifier) for identifier in range(1, count + 1)],
            'candidates': [quote(identifier, price) for identifier in range(1, count + 1)],
            'calendar_universe': universe,
            'calendar_scope_receipt': {
                'scope': 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY', 'status': 'COMPLETE',
                'provider_calendar_status': 'COMPLETE', 'full_product_run': True,
                'provider_fixture_ids': sorted({item['fixture_id'] for item in universe})}}


def build(payload, profiles=(BENCE,), *, version=2, results=None, options=None):
    settings = {'enabled_profiles': list(profiles)}
    if version is not None:
        settings['operating_contract_version'] = version
    return build_tipster_recommendations(payload, models(len(payload['fixtures'])) if results is None else results,
                                        settings, NOW, diversity_options=options, option_search_mode='OFF')


def test_all_twenty_personas_account_for_same45_events_including_unmapped_and_unpriced():
    universe = [event(identifier) for identifier in range(1, 46)]
    universe[-1].update(provider_league_id='comp_999999', competition_name='TEST Unmapped Cup',
                        status='UNMAPPED_COMPETITION')
    payload = data(44, universe=universe)
    payload['candidates'] = []
    payload['calendar_scope_receipt'].update(status='PARTIAL', full_product_run=False)
    cards = build(payload, TIPSTER_PROFILE_IDS, results={})
    expected = sorted(item['fixture_id'] for item in universe)
    assert len(cards) == 20
    for card in cards:
        trace = card['selection_diagnostics']
        assert trace['input_fixture_ids'] == expected
        assert trace['input_fixture_count'] == len(trace['fixture_decisions']) == 45
        assert trace['ranked_fixture_ids'] == trace['selected_fixture_ids'] == []
        assert all('NO_VALID_QUOTES' in decision['reasons'] for decision in trace['fixture_decisions'])
        assert 'UNMAPPED_COMPETITION' in trace['fixture_decisions'][-1]['reasons']
        assert trace['all_daily_fixtures_considered'] is False


def test_own_market_rules_and_required_models_are_recorded_per_persona():
    payload = data()
    cards = {card['profile_id']: card for card in build(payload, (MARK, MIRA))}
    assert cards[MARK]['legs']
    assert not cards[MIRA]['legs']
    assert cards[MARK]['selection_diagnostics']['ranked_fixture_ids']
    assert set(cards[MIRA]['selection_diagnostics']['candidate_rejections'].values()) == {'PERSONA_MARKET_RULE'}
    no_models = build(payload, (MARK,), results={})[0]
    assert not no_models['legs']
    assert set(no_models['selection_diagnostics']['candidate_rejections'].values()) == {'REQUIRED_MODEL_UNKNOWN'}
    assert no_models['selection_diagnostics']['input_fixture_ids'] == cards[MARK]['selection_diagnostics']['input_fixture_ids']


def test_valid_quote_pool_does_not_require_models_or_invent_probability():
    payload = data()
    fixtures = _fixtures(payload['fixtures'], payload['date'], NOW)
    pool, count = _pool(payload, fixtures, {}, NOW)
    assert count == len(pool) == 4
    assert all(row['models'] == {} for row in pool)
    assert all(card['probability'] is None and not card['legs']
               for card in build(payload, TIPSTER_PROFILE_IDS, results={}))


def test_v2_effective15_40_does_not_mutate_catalogue_or_model_values():
    payload, supplied_models = data(5), models(5)
    original = deepcopy([TIPSTER_CATALOG, payload, supplied_models])
    options = {}
    card = build(payload, results=supplied_models, options=options)[0]
    assert 15 <= card['combined_price'] <= 40
    assert card['persona']['target_odds_band'] == {'min': 15.0, 'max': 40.0}
    assert card['selection_diagnostics']['odds_exception']['status'] == 'PRIMARY_RANGE'
    assert len(card['legs']) == 4
    for leg in card['legs']:
        assert leg['model_probability'] == pytest.approx(.65)
        assert leg['decision_context']['historical_support'] is None
        assert leg['decision_context']['quote_source_id'] == leg['quote_source_id']
        assert leg['decision_context']['score_terms'] == leg['model_details']['selection_score_terms']
        assert leg['decision_context']['model_source_details']['gauss']['training_count'] == 123
        assert 'UNKNOWN' not in leg['decision_context']['model_probabilities']
        assert leg['source_candidate_id'] in card['selection_diagnostics']['selected_source_candidate_ids']
    assert len(options[BENCE]) > 1
    for template in options[BENCE]:
        recommendation = template['recommendation']
        selected = set(recommendation['selection_diagnostics']['selected_source_candidate_ids'])
        accounted = {identifier for decision in recommendation['selection_diagnostics']['fixture_decisions']
                     for identifier in decision['selected_candidate_ids']}
        assert selected == accounted
        actual_reasons = {leg['selection_reason'] for leg in recommendation['legs']}
        assert actual_reasons <= set(recommendation['reasoning'])
        old_unselected = {leg['selection_reason'] for leg in card['legs']
                          if leg['source_candidate_id'] not in selected}
        assert old_unselected.isdisjoint(recommendation['reasoning'])
    assert [TIPSTER_CATALOG, payload, supplied_models] == original


def test_cautious_fallback_only_after_failed_primary_and_proven_full_day():
    payload = data(3, price=1.8)
    card = build(payload)[0]
    assert card['combined_price'] == pytest.approx(1.8 ** 3)
    assert card['persona']['target_odds_band'] == {'min': 5.0, 'max': 15.0}
    exception = card['selection_diagnostics']['odds_exception']
    assert exception['status'] == 'APPLIED' and exception['primary_options_count'] == 0
    assert exception['reason'] == 'NO_OWN_15_40_OPTION_AFTER_FULL_DAY_SEARCH'
    assert 'CAUTIOUS_ODDS_EXCEPTION_AFTER_FULL_DAY_SEARCH' in card['quality_warnings']
    assert not build(payload, (MARK,))[0]['legs']


@pytest.mark.parametrize('restriction', ['PARTIAL', 'MANUAL_RESEARCH', 'ID_MISMATCH', 'NO_UNIVERSE'])
def test_incomplete_or_restricted_calendar_never_authorizes_lower_odds(restriction):
    payload = data(3, price=1.8)
    if restriction == 'ID_MISMATCH':
        payload['calendar_scope_receipt']['provider_fixture_ids'].append('the_stats_api:mt_9999')
    elif restriction == 'NO_UNIVERSE':
        payload.pop('calendar_universe')
    else:
        payload['calendar_scope_receipt']['status'] = restriction
    card = build(payload)[0]
    assert card['legs'] == []
    assert card['selection_diagnostics']['odds_exception']['status'] == 'NOT_AUTHORIZED'


def test_v1_unmarked_and_explicit_legacy_are_identical_despite_full_day_metadata():
    payload = data(4, price=2)
    unmarked = build(payload, version=None)
    explicit = build(payload, version=1)
    assert unmarked == explicit
    assert 'input_fixture_ids' not in explicit[0]['selection_diagnostics']
    assert 'operating_contract_version' not in explicit[0]
    assert explicit[0]['persona']['target_odds_band'] != {'min': 15.0, 'max': 40.0}


def test_lockout_remains_in_received_universe_with_explicit_own_rejection():
    payload = data()
    stamp = (NOW + timedelta(minutes=15)).isoformat()
    payload['fixtures'][0]['kickoff'] = payload['candidates'][0]['kickoff'] = stamp
    payload['calendar_universe'][0]['kickoff'] = stamp
    card = build(payload)[0]
    trace = card['selection_diagnostics']
    assert fixture(1)['fixture_id'] in trace['input_fixture_ids']
    decision = next(item for item in trace['fixture_decisions'] if item['fixture_id'] == fixture(1)['fixture_id'])
    assert 'PREMATCH_LOCKOUT' in decision['reasons']
    assert fixture(1)['fixture_id'] not in trace['ranked_fixture_ids']
