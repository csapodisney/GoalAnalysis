"""TESTONLY canonical assembly; native-shaped fake facts and zero external I/O."""

import math
import socket
import sqlite3
import subprocess
import urllib.request
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.config.portfolio import PROFILE_IDS
from goal_analysis.generated_tickets import (
    CORE_MARKETS,
    capture_profile_decisions,
    finalize_generated,
)
from goal_analysis.portfolio_objectives import assess_objectives
from goal_analysis.providers.the_stats_odds import bookmaker_key
from goal_analysis.tipsters.catalog import CATALOG_VERSION, TIPSTER_BY_ID
from goal_analysis.tipsters.recommendations import PRODUCTS

NOW = datetime(2026, 10, 28, 10, tzinfo=UTC)
BOOK = 'TESTONLY Book'


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('NO_PROVIDER_DATABASE_NETWORK_PROCESS_OR_MODEL_EXECUTION')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def leg(identifier, price, product=('h2h', 'home', 'FULL_TIME')):
    market, selection, period = product
    return {'fixture_id': f'the_stats_api:mt_{identifier}', 'source_provider': 'the_stats_api',
            'provider_fixture_id': f'mt_{identifier}', 'provider_league_id': 'comp_4643',
            'provider_season_id': 'sn_2026', 'provider_home_team_id': f'tm_{identifier * 2}',
            'provider_away_team_id': f'tm_{identifier * 2 + 1}', 'home_team': f'TEST Home {identifier}',
            'away_team': f'TEST Away {identifier}', 'kickoff': (NOW + timedelta(hours=8)).isoformat(),
            'candidate_id': f'TEST-quote-{identifier}', 'market_key': market, 'selection_key': selection,
            'period': period, 'decimal_price': price, 'bookmaker': bookmaker_key(BOOK),
            'region': 'eu', 'currency': 'EUR', 'quote_provider': 'the_stats_api',
            'provider_bookmaker': BOOK, 'provider_market_id': 'match_odds',
            'quote_source_id': f'https://api.thestatsapi.com/api/football/matches/mt_{identifier}/odds',
            'quote_source_sha256': 'a' * 64, 'quote_observed_at': NOW.isoformat(),
            'quote_time_basis': 'PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME',
            'model_probability': .65, 'estimated_edge': .65 * price - 1,
            'model_stability': 0,
            'model_details': {'shared_model_probabilities': dict.fromkeys(('gauss', 'kepler', 'laplace'), .65)}}


def card(profile, offset, *, status='DRAFT', scope='PAPER_AUDITION', prices=None):
    persona = TIPSTER_BY_ID.get(profile)
    product = CORE_MARKETS.get(profile, ('h2h', 'home', 'FULL_TIME'))
    if persona and persona['selection_policy']['allowed_market_keys']:
        product = (*PRODUCTS[persona['selection_policy']['allowed_market_keys'][0]], 'FULL_TIME')
    if product[1] is None:
        product = (product[0], 'home', product[2])
    count = persona['min_legs'] if persona else 3 if profile == 'daily223' else 2 if profile in {
        'gauss', 'kepler', 'laplace'} else 4
    prices = prices or ([2, 2, 3] if profile == 'daily223' else [16 ** (1 / count)] * count)
    legs = [leg(offset + index, price, product) for index, price in enumerate(prices)]
    return {'profile_id': profile, 'date': NOW.date().isoformat(), 'created_at': NOW.isoformat(),
            'status': status, 'publication_scope': scope, 'playable': False, 'stake_eur': None,
            'combined_price': math.prod(prices), 'legs': legs, 'reasoning': ['TEST own source decision'],
            'catalog_version': CATALOG_VERSION, 'odds_band': 'STRICT' if profile == 'daily223' else None}


def report(count=15, *, status='DRAFT'):
    roster = [profile for profile in PROFILE_IDS
              if not TIPSTER_BY_ID.get(profile, {}).get('selection_policy', {}).get('contrarian_value')][:count]
    settings = {'operating_contract_version': 2, 'enabled_profiles': roster,
                'stake_eur': 1, 'max_ticket_count': 30}
    return {'run_id': 'TESTONLY-one-run', 'date': NOW.date().isoformat(),
            'observed_at': NOW.isoformat(), 'finished_at': NOW.isoformat(), 'settings': settings,
            'tickets': [], 'recommendations': [card(profile, 100 * index + 1, status=status)
                                               for index, profile in enumerate(roster)],
            'financial_policy': {'max_daily_tickets': 5, 'stake_eur': 1, 'real_wagers_today': 5}}


def finalize(value, **kwargs):
    return finalize_generated(value, value['settings'], **kwargs)


def test15_draft_paper_cards_meet_generation_goal_without_ready_or_live_promotion():
    value = report()
    financial = deepcopy(value['financial_policy'])
    finalize(value)
    assert len(value['generated_tickets']) == 15
    assert {ticket['status'] for ticket in value['generated_tickets']} == {'DRAFT'}
    assert {ticket['publication_scope'] for ticket in value['generated_tickets']} == {'PAPER_AUDITION'}
    assert all(ticket['playable'] is False and ticket['stake_eur'] is None for ticket in value['generated_tickets'])
    assert value['tickets'] == []
    assert value['generation_capacity']['status'] == 'IN_TARGET'
    assert value['generation_capacity']['shortfall'] == 0
    assert value['financial_policy'] == financial
    objective = assess_objectives(value)
    assert objective['generated_count'] == 15
    assert objective['capacity_shortfall'] == objective['ready_live_recommendations'] == 0
    assert objective['ready_recommendations'] == 0
    assert objective['draft_generated_recommendations'] == 15


def test14_full_cards_never_meet_the_minimum():
    value = finalize(report(14))
    assert value['generation_capacity']['generated_count'] == 14
    assert value['generation_capacity']['shortfall'] == 1
    assert value['generation_capacity']['status'] == 'BELOW_TARGET'
    assert assess_objectives(value)['capacity_status'] == 'BELOW_TARGET'


def test_duplicate_full_bets_empty_suspended_and_invalid_prices_do_not_inflate():
    value = report()
    cards = value['recommendations']
    # GAUSS and KEPLER each allow these same real-shaped products.
    gauss = next(item for item in cards if item['profile_id'] == 'gauss')
    kepler = next(item for item in cards if item['profile_id'] == 'kepler')
    kepler['legs'] = deepcopy(gauss['legs'])
    kepler['combined_price'] = gauss['combined_price']
    cards[1].update(legs=[], status='ABSTAINED')
    cards[2]['status'] = 'SUSPENDED'
    cards[3]['legs'][0]['decimal_price'] = 0
    cards[4]['legs'][0]['quote_source_id'] = 'https://test.invalid/unverified'
    finalize(value)
    assert len(value['generated_tickets']) == 10
    excluded = {item['reason'] for item in value['generation_capacity']['excluded_profiles']}
    assert 'DUPLICATE_FULL_BET_SIGNATURE' in excluded
    assert 'INVALID_PRICE_OR_QUOTE_SOURCE' in excluded
    assert value['generation_capacity']['shortfall'] == 5


def test_same_persona_independently_constructed_alternative_can_resolve_duplicate():
    value = report(10)
    gauss, kepler = value['recommendations'][7:9]
    kepler.update(legs=deepcopy(gauss['legs']), combined_price=gauss['combined_price'])
    alternative = card('kepler', 9001, status='MODEL_READY')
    finalize(value, options={'kepler': [{'quality': .8, 'recommendation': alternative}]})
    selected = next(ticket for ticket in value['generated_tickets'] if ticket['profile_id'] == 'kepler')
    assert selected['legs'] == alternative['legs']
    assert selected['status'] == 'DRAFT'
    assert len(value['generated_tickets']) == 10


def test_capture_preserves_own_complete_cards_when_allocator_erases_them():
    value = report()
    original = deepcopy(value['recommendations'])
    capture_profile_decisions(value)
    for item in value['recommendations']:
        item.update(legs=[], status='ABSTAINED', combined_price=None)
    finalize(value)
    assert len(value['generated_tickets']) == 15
    assert [source['card'] for source in value['profile_decision_sources']] == original
    assert all(ticket['generation_source_status'] == 'DRAFT' for ticket in value['generated_tickets'])


def test_stable_run_ids_repeat_assembly_and_detached_old_snapshots():
    value = report()
    past = deepcopy(value)
    capture_profile_decisions(value)
    captured = deepcopy(value['profile_decision_sources'])
    finalize(value)
    first = deepcopy(value['generated_tickets'])
    finalize(value)
    assert value['generated_tickets'] == first
    assert value['profile_decision_sources'] == captured
    assert past == report()
    assert len({ticket['ticket_id'] for ticket in first}) == 15


def test_matching_review_can_finish_actual_ready_source_but_model_ready_stays_draft():
    value = report(10, status='READY')
    for item in value['recommendations']:
        item['publication_scope'] = 'LIVE'
        item['playable'] = True
    value['recommendations'][7]['status'] = 'MODEL_READY'
    capture_profile_decisions(value)
    finalize(value)
    assert not value['tickets']
    for item in value['recommendations']:
        for item_leg in item['legs']:
            item_leg['astra_review'] = {'candidate_id': item_leg['candidate_id'], 'assessment': 'support'}
    value['astra'] = {'status': 'COMPLETE'}
    finalize(value)
    assert len(value['tickets']) == 9
    assert all(ticket in value['generated_tickets'] for ticket in value['tickets'])
    numeric = next(ticket for ticket in value['generated_tickets'] if ticket['profile_id'] == 'gauss')
    assert numeric['status'] == 'DRAFT' and numeric['playable'] is False


def test_changed_quote_does_not_overwrite_captured_source_or_promote_paper():
    value = report(1, status='READY')
    capture_profile_decisions(value)
    original_legs = deepcopy(value['recommendations'][0]['legs'])
    value['recommendations'][0]['publication_scope'] = 'LIVE'
    value['recommendations'][0]['legs'][0]['quote_source_sha256'] = 'b' * 64
    finalize(value)
    selected = value['generated_tickets'][0]
    assert selected['legs'] == original_legs
    assert selected['publication_scope'] == 'PAPER_AUDITION'
    assert not selected['playable']


def test_final_cutoff_prevents_resurrection_after_publication_lockout():
    value = report(1)
    capture_profile_decisions(value)
    value['finished_at'] = (NOW + timedelta(hours=8, minutes=-15)).isoformat()
    finalize(value)
    assert value['generated_tickets'] == []
    assert value['generation_capacity']['excluded_profiles'][0]['reason'] == 'PREMATCH_LOCKOUT'


def test_daily_protected_slots_and_cautious_exception_are_verified():
    value = report(7)
    value['recommendations'][0].update(legs=[leg(1, 1.8), leg(2, 2), leg(3, 4)], combined_price=14.4)
    cautious = value['recommendations'][4]
    cautious.update(legs=[leg(900 + index, 2, CORE_MARKETS['orszem']) for index in range(3)], combined_price=8)
    cautious['selection_diagnostics'] = {'odds_exception': {
        'status': 'APPLIED', 'applied': True, 'full_day_coverage': True, 'primary_options_count': 0,
        'reason': 'NO_OWN_15_40_OPTION_AFTER_FULL_DAY_SEARCH'}}
    finalize(value)
    assert 'daily223' not in {ticket['profile_id'] for ticket in value['generated_tickets']}
    assert 'orszem' in {ticket['profile_id'] for ticket in value['generated_tickets']}
    assert assess_objectives(value)['cautious_odds_exception_profile_ids'] == ['orszem']


def test_v1_is_unchanged_and_objective_retains_historical20_live_goal():
    value = report()
    value['settings']['operating_contract_version'] = 1
    original = deepcopy(value)
    assert capture_profile_decisions(value) == original
    assert finalize(value) == original
    assert 'generated_tickets' not in value
    objective = assess_objectives(value)
    assert objective['version'] == 'arthur-portfolio-objectives-v2-live-paper'
    assert objective['target_ready_range'] == {'minimum': 20, 'maximum': 30}


def test_missing_essential_remi_price_movement_case_is_not_a_generated_ticket():
    value = report(1)
    value['settings']['enabled_profiles'] = ['tipster_remi_doma']
    value['recommendations'] = [card('tipster_remi_doma', 8001)]
    finalize(value)
    assert value['generated_tickets'] == []
    assert value['generation_capacity']['excluded_profiles'] == [{
        'profile_id': 'tipster_remi_doma', 'reason': 'OWN_STRATEGY_CONSTRUCTION_INVALID'}]


def test_invalid_model_probability_and_nan_price_cannot_inflate_count():
    value = report(10)
    value['recommendations'][7]['legs'][0]['model_probability'] = .1
    value['recommendations'][8]['legs'][0]['decimal_price'] = float('nan')
    finalize(value)
    assert len(value['generated_tickets']) == 8
    assert 'gauss' not in {ticket['profile_id'] for ticket in value['generated_tickets']}
    assert 'kepler' not in {ticket['profile_id'] for ticket in value['generated_tickets']}


def test_ready_paper_is_counted_separately_and_cannot_gain_live_scope_from_alias():
    value = report(1, status='READY')
    for item_leg in value['recommendations'][0]['legs']:
        item_leg['astra_review'] = {'candidate_id': item_leg['candidate_id'], 'assessment': 'support'}
    value['astra'] = {'status': 'COMPLETE'}
    capture_profile_decisions(value)
    value['recommendations'][0]['publication_scope'] = 'LIVE'
    finalize(value)
    selected = value['generated_tickets'][0]
    assert selected['status'] == 'READY'
    assert selected['publication_scope'] == 'PAPER_AUDITION'
    assert selected['playable'] is False and value['tickets'] == []
    objective = assess_objectives(value)
    assert objective['ready_paper_recommendations'] == 1
    assert objective['ready_live_recommendations'] == objective['ready_recommendations'] == 0
    assert objective['ready_recommendations_all_scopes'] == objective['generated_count'] == 1


def test_core_singleton_cannot_pose_as_complete_two_to_six_leg_strategy():
    value = report(2)
    value['recommendations'][1] = card('kronikas', 9001, prices=[16])
    finalize(value)
    assert {ticket['profile_id'] for ticket in value['generated_tickets']} == {'daily223'}
