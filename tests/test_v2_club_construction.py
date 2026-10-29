"""Prepared v2 construction regressions; source/model inputs are TESTONLY fakes."""

import math
import socket
import sqlite3
import subprocess
import urllib.request
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.engine.diversification import _eligible
from goal_analysis.generated_tickets import _fallback
from goal_analysis.jobs.nemotron_tickets import _menus
from goal_analysis.providers.the_stats_odds import bookmaker_key
from goal_analysis.tipsters.catalog import CATALOG_VERSION, TIPSTER_BY_ID, get_catalogue
from goal_analysis.tipsters.contracts import valid_construction
from goal_analysis.tipsters.recommendations import build_tipster_recommendations

NOW = datetime(2026, 10, 29, 10, tzinfo=UTC)
DAY = NOW.date().isoformat()
LOTTI = 'tipster_rovid_lotti'
NORA = 'tipster_archiv_nora'
BENCE = 'tipster_bastya_bence'
BOOK = 'TESTONLY Construction Book'


@pytest.fixture(autouse=True)
def forbid_external_work(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('NO_REAL_DB_NETWORK_PROCESS_PROVIDER_OR_MODEL_EXECUTION')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def construction(profile=LOTTI, total=30, *, version=2, count=None):
    count = TIPSTER_BY_ID[profile]['min_legs'] if count is None else count
    prices = [2.0] * (count - 1) + [total / 2 ** (count - 1)]
    return {'profile_id': profile, 'catalog_version': CATALOG_VERSION,
            'operating_contract_version': version, 'date': DAY, 'status': 'READY',
            'combined_price': math.prod(prices), 'legs': [
                {'fixture_id': f'the_stats_api:mt_{index}', 'decimal_price': price,
                 'kickoff': (NOW + timedelta(hours=8)).isoformat(),
                 'bookmaker': 'TESTONLY', 'region': 'eu', 'currency': 'EUR',
                 'market_key': 'h2h', 'selection_key': 'home', 'period': 'FULL_TIME'}
                for index, price in enumerate(prices, 1)]}


def cautious_proof():
    return {'status': 'APPLIED', 'applied': True, 'full_day_coverage': True,
            'primary_options_count': 0,
            'reason': 'NO_OWN_15_40_OPTION_AFTER_FULL_DAY_SEARCH'}


@pytest.mark.parametrize('profile', [LOTTI, NORA])
@pytest.mark.parametrize('total', [30, 40])
def test_current_band_accepts_above_the_pinned14_or22_maximum(profile, total):
    card = construction(profile, total)
    before = deepcopy(card), get_catalogue()
    assert TIPSTER_BY_ID[profile]['target_odds_band']['max'] in {14, 22}
    assert valid_construction(card, NOW)
    assert not valid_construction({**card, 'operating_contract_version': 1}, NOW)
    assert (card, get_catalogue()) == before


@pytest.mark.parametrize('total', [12, 14.99, 40.01, 50])
def test_v2_rejects_historical_only_or_outside_current_band(total):
    assert not valid_construction(construction(total=total), NOW)


def test_unmarked_and_explicit_v1_retain_historical_band_and_daily_is_separate():
    card = construction(total=12, version=1)
    assert valid_construction(card, NOW)
    del card['operating_contract_version']
    assert valid_construction(card, NOW)
    assert not valid_construction(construction(total=15, version=1), NOW)
    assert valid_construction({'profile_id': 'daily223', 'operating_contract_version': 999}, NOW)


def test_price_above40_previously_valid_for_a_wide_legacy_persona_is_rejected_in_v2():
    profile = next(profile for profile, persona in TIPSTER_BY_ID.items()
                   if persona['target_odds_band']['max'] >= 45)
    assert valid_construction(construction(profile, 45, version=1), NOW)
    assert not valid_construction(construction(profile, 45), NOW)


def test_existing_v1_singleton_waiver_cannot_override_v2_range_or_minimum_legs(monkeypatch):
    from goal_analysis.engine import veto_overrides

    calls = []
    def approved_legacy_source(candidate, review=None, now=None):
        calls.append((candidate, now))
        return {'gates': ['HIGH_ODDS']}
    monkeypatch.setattr(veto_overrides, 'verified_veto_override', approved_legacy_source)
    card = construction(total=50, version=1, count=1)
    assert valid_construction(card, NOW)
    assert len(calls) == 1
    assert not valid_construction({**card, 'operating_contract_version': 2}, NOW)
    assert not valid_construction(construction(total=30, count=1), NOW)
    assert len(calls) == 1


@pytest.mark.parametrize('version', [None, True, False, 0, 3, '2', 2.0])
def test_unknown_or_untyped_club_versions_fail_closed(version):
    assert not valid_construction(construction(version=version), NOW)


@pytest.mark.parametrize('changes', [
    {'status': 'PRIMARY_RANGE'}, {'applied': False}, {'full_day_coverage': False},
    {'primary_options_count': 1}, {'primary_options_count': False},
    {'primary_options_count': 0.0}, {'reason': 'caller_authorized_fallback'},
])
def test_cautious_proof_forgery_matches_existing_canonical_rejection(changes):
    card = construction(BENCE, 8)
    card['selection_diagnostics'] = {'odds_exception': {**cautious_proof(), **changes}}
    assert not _fallback(card)
    assert not valid_construction(card, NOW)


def test_named_cautious_proof_is_rechecked_without_granting_caller_authority():
    card = construction(BENCE, 8)
    assert not valid_construction(card, NOW)
    card['selection_diagnostics'] = {'odds_exception': cautious_proof()}
    assert _fallback(card) and valid_construction(card, NOW)
    wrong_profile = construction(LOTTI, 8)
    wrong_profile['selection_diagnostics'] = deepcopy(card['selection_diagnostics'])
    assert not _fallback(wrong_profile) and not valid_construction(wrong_profile, NOW)
    too_short = construction(BENCE, 8, count=2)
    too_short['selection_diagnostics'] = deepcopy(card['selection_diagnostics'])
    assert not valid_construction(too_short, NOW)


@pytest.mark.parametrize('fault', ['caller_persona', 'catalogue', 'duplicate', 'cohort', 'price', 'product'])
def test_versioned_band_does_not_relax_other_construction_gates(fault):
    card = construction()
    if fault == 'caller_persona':
        card = construction(total=50, count=1)
        card['persona'] = {'min_legs': 1, 'max_legs': 10, 'target_odds_band': {'min': 1, 'max': 100}}
    elif fault == 'catalogue':
        card['catalog_version'] = 'invented'
    elif fault == 'duplicate':
        card['legs'][1]['fixture_id'] = card['legs'][0]['fixture_id']
    elif fault == 'cohort':
        card['legs'][1]['currency'] = 'USD'
    elif fault == 'price':
        card['legs'][0]['decimal_price'] = True
    else:
        card['combined_price'] += 1
    assert not valid_construction(card, NOW)


def inputs(count, price):
    fixtures, quotes = [], []
    for identifier in range(1, count + 1):
        fixture = {'fixture_id': f'the_stats_api:mt_{identifier}', 'source_provider': 'the_stats_api',
                   'provider_fixture_id': f'mt_{identifier}', 'provider_league_id': 'comp_4643',
                   'provider_season_id': 'sn_2026', 'provider_home_team_id': f'tm_{identifier * 2}',
                   'provider_away_team_id': f'tm_{identifier * 2 + 1}', 'config_league_id': '78',
                   'home_team': f'TEST Home {identifier}', 'away_team': f'TEST Away {identifier}',
                   'kickoff': (NOW + timedelta(hours=8)).isoformat(), 'provider_status': 'NS',
                   'competition': 'TEST_LEAGUE', 'competition_type': 'LEAGUE', 'sport_key': 'soccer_test',
                   'target_date': DAY}
        fixtures.append(fixture)
        quotes.append({**fixture, 'candidate_id': f'TEST-quote-{identifier}', 'decimal_price': price,
                       'market_key': 'h2h', 'selection_key': 'home', 'period': 'FULL_TIME',
                       'event_status': 'SCHEDULED', 'quote_available': True,
                       'bookmaker': bookmaker_key(BOOK), 'provider_bookmaker': BOOK,
                       'region': 'eu', 'currency': 'EUR', 'quote_provider': 'the_stats_api',
                       'provider_market_id': 'match_odds', 'quote_source_id':
                           f'https://api.thestatsapi.com/api/football/matches/mt_{identifier}/odds',
                       'quote_source_sha256': 'a' * 64, 'quote_observed_at': NOW.isoformat(),
                       'quote_time_basis': 'PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME', 'evidence': []})
    values = {'home': .65, 'draw': .2, 'away': .15, 'over25': .35, 'under25': .65, 'btts_yes': .35}
    results = {source: {'profile_id': source, 'status': 'READY', 'predictions': [
        {'fixture_id': f['fixture_id'], 'probabilities': values, 'stability': dict.fromkeys(values, .03),
         'model_details': {'cutoff': NOW.isoformat(), 'training_count': 123, 'calibration': 'unvalidated'}}
        for f in fixtures]} for source in ('gauss', 'kepler', 'laplace')}
    data = {'date': DAY, 'fixtures': fixtures, 'candidates': quotes,
            'calendar_universe': [{**f, 'status': 'ROUTED'} for f in fixtures],
            'calendar_scope_receipt': {'scope': 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY', 'status': 'COMPLETE',
                'provider_calendar_status': 'COMPLETE', 'full_product_run': True,
                'provider_fixture_ids': sorted(f['fixture_id'] for f in fixtures)}}
    return data, results


def test_real_v2_builder_alternates_survive_allocator_and_bounded_review_menu():
    data, results = inputs(4, 3.2)
    options = {}
    before = deepcopy(data), deepcopy(results), get_catalogue()
    cards = build_tipster_recommendations(data, results,
        {'enabled_profiles': [LOTTI], 'operating_contract_version': 2}, NOW,
        diversity_options=options)
    assert cards[0]['legs'] and options[LOTTI]
    for option in options[LOTTI]:
        card = option['recommendation']
        assert card['operating_contract_version'] == 2
        assert card['combined_price'] == pytest.approx(3.2 ** 3)
        assert card['combined_price'] > TIPSTER_BY_ID[LOTTI]['target_odds_band']['max']
        assert valid_construction(card, NOW) and _eligible(option, NOW, DAY)
    menus = _menus(options, cards)
    assert menus[LOTTI]['options']
    assert all(15 <= item[1]['combined_price'] <= 40 for item in menus[LOTTI]['options'])
    assert (data, results, get_catalogue()) == before


def test_real_builder_cautious_proof_is_accepted_without_new_fallback_authority():
    data, results = inputs(3, 2.0)
    cards = build_tipster_recommendations(data, results,
        {'enabled_profiles': [BENCE], 'operating_contract_version': 2}, NOW)
    card = cards[0]
    assert card['combined_price'] == 8 and card['selection_diagnostics']['odds_exception']['applied'] is True
    assert _fallback(card) and valid_construction(card, NOW)
