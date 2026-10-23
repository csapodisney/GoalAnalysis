"""Real temp raw/receipt reader plus existing numeric selector, no live calls."""
import json
import socket
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from goal_analysis.engine.model_recommendations import SETTLEMENT
from goal_analysis.jobs.cup_specialist_shadow import inspect_cup_specialist
from goal_analysis.normalization.competition_types import RULES
from goal_analysis.providers.the_stats_odds import bookmaker_key
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.temporal import TemporalStore

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
OBS = NOW - timedelta(minutes=2)


def environment(root, *, competition='comp_7739', stage=None, witness=True, when=OBS):
    db = Database(root / 'data/goal_analysis.sqlite3')
    db.initialize()
    store = SnapshotStore(db, root / 'data/raw')
    temporal = TemporalStore(db, root / 'data/raw', clock=lambda: NOW - timedelta(seconds=30))
    temporal.initialize()

    def save(data, resource, url):
        return store.save('the_stats_api', resource, json.dumps({'data': data}).encode(),
                          'application/json', fetched_at=when, source_url=url)

    name, country, confederation, format_, _, _ = RULES[competition]
    save([{'id': competition, 'name': name, 'country': country,
           'confederation': confederation, 'type': format_}], 'competition_catalogue',
         'https://api.thestatsapi.com/api/football/competitions?page=1&per_page=100')
    matches = [{'id': f'mt_{i}', 'competition_id': competition, 'season_id': 'sn_1',
                'utc_date': (NOW + timedelta(hours=6)).isoformat(),
                'home_team': {'id': f'tm_{i * 2}'}, 'away_team': {'id': f'tm_{i * 2 + 1}'},
                'stage_name': stage} for i in range(1, 4)]
    meta = save(matches, 'matches', 'https://api.thestatsapi.com/api/football/matches?competition_id=' + competition)
    fixtures, candidates = [], []
    for row in matches:
        fid = 'the_stats_api:' + row['id']
        fixture = {'fixture_id': fid, 'source_provider': 'the_stats_api',
                   'provider_fixture_id': row['id'], 'provider_league_id': competition,
                   'provider_season_id': 'sn_1', 'provider_home_team_id': row['home_team']['id'],
                   'provider_away_team_id': row['away_team']['id'],
                   'home_team': 'Home ' + row['id'], 'away_team': 'Away ' + row['id'],
                   'competition': name, 'kickoff': row['utc_date'], 'provider_status': 'NS',
                   'fixture_source': {'content_hash': meta.content_hash}}
        fixtures.append(fixture)
        url = 'https://api.thestatsapi.com/api/football/matches/' + row['id'] + '/odds'
        odds = save({'match_id': row['id'], 'bookmakers': [{'bookmaker': 'Synthetic', 'markets': {
            'match_odds': {'home': {'last_seen': 2.3}, 'draw': {'last_seen': 3.6}, 'away': {'last_seen': 3.8}}}}]},
                    'native_odds', url)
        candidates.append({**fixture, 'candidate_id': 'cup-' + row['id'],
                           'market_key': 'h2h', 'selection_key': 'home', 'period': 'FULL_TIME',
                           'event_status': 'SCHEDULED', 'settlement': SETTLEMENT,
                           'decimal_price': 2.3, 'quoted_at': None, 'quote_observed_at': when.isoformat(),
                           'quote_time_basis': 'PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME',
                           'quote_source_sha256': odds.content_hash, 'quote_source_id': url,
                           'quote_provider': 'the_stats_api', 'bookmaker': bookmaker_key('Synthetic'),
                           'provider_bookmaker': 'Synthetic', 'provider_market_id': 'match_odds',
                           'region': 'DE', 'currency': 'EUR', 'quote_available': True, 'evidence': []})
    if witness:
        temporal.capture('raw', limit=100)
    models = {source: {'status': 'READY', 'profile_id': source, 'predictions': [
        {'fixture_id': f['fixture_id'], 'probabilities': {'home': .6, 'draw': .2, 'away': .2},
         'stability': {'home': .02, 'draw': .02, 'away': .02},
         'model_details': {'cutoff': NOW.isoformat(), 'history_hash': 'a' * 64, 'training_count': 100}}
        for f in fixtures]} for source in ('gauss', 'laplace')}
    return {'date': NOW.date().isoformat(), 'fixtures': fixtures, 'candidates': candidates}, models, db


def run(root, data, models):
    with patch.object(socket, 'socket', side_effect=AssertionError('NO_NETWORK')):
        return inspect_cup_specialist(root, data, models, as_of=NOW, enabled=True)


def test_default_disabled_does_not_read_a_root_or_enriched_input(tmp_path):
    result = inspect_cup_specialist(tmp_path / 'missing', None, None, as_of=NOW)
    assert result['status'] == 'DISABLED' and not (tmp_path / 'missing').exists()
    assert result['live_decision_effect'] is False and result['candidate_legs'] == []


@pytest.mark.parametrize('competition', ['comp_7739', 'comp_7428'])
def test_verified_club_cup_produces_separate_shadow_combination_without_rotation_fiction(tmp_path, competition):
    data, models, _db = environment(tmp_path, competition=competition)
    original = deepcopy((data, models))
    result = run(tmp_path, data, models)
    assert result['status'] == 'SHADOW_CANDIDATE', result
    assert len(result['candidate_legs']) == 3 and result['combined_price'] == pytest.approx(2.3 ** 3)
    assert result['playable'] is result['live_decision_effect'] is False
    assert result['ranking_multiplier'] == 1 and result['probability_adjustment'] == 0
    assert result['external_requests'] == result['production_writes'] == 0
    assert (data, models) == original
    for leg in result['candidate_legs']:
        assert leg['quoted_at'] is None and leg['price_receipt']['receipt_id']
        assert leg['cup_stage_rotation_implication'] == 'UNKNOWN'
        assert leg['cup_context']['phase'] == 'UNKNOWN'
        assert leg['model_probability'] == .6


@pytest.mark.parametrize('competition', ['comp_3039', 'comp_574977', 'comp_29967'])
def test_league_national_or_friendly_does_not_become_cup_by_candidate_label(tmp_path, competition):
    data, models, _ = environment(tmp_path, competition=competition)
    for fixture in data['fixtures']:
        fixture['competition_type'] = 'CUP'
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']


@pytest.mark.parametrize('broken', ['price', 'receipt', 'source', 'quote_clock', 'currency',
                                  'model', 'lockout', 'cancelled', 'wrong_date',
                                  'future_clock', 'settlement', 'half', 'future_model'])
def test_incomplete_or_invalid_inputs_cannot_pad_a_shadow_cup_ticket(tmp_path, broken):
    data, models, db = environment(tmp_path)
    quote = data['candidates'][0]
    if broken == 'price':
        quote['decimal_price'] = 2.4
    elif broken == 'receipt':
        # A newly downloaded source without a witness; existing append-only
        # receipts are never altered even in this temporary database.
        record = SnapshotStore(db, tmp_path / 'data/raw').save(
            'the_stats_api', 'native_odds', b'{"data":{"unwitnessed":true}}',
            'application/json', fetched_at=OBS, source_url=quote['quote_source_id'])
        quote['quote_source_sha256'] = record.content_hash
    elif broken == 'source':
        quote['quote_source_sha256'] = 'a' * 64
    elif broken == 'quote_clock':
        quote['quote_observed_at'] = (NOW - timedelta(hours=1)).isoformat()
    elif broken == 'currency':
        quote['currency'] = 'USD'
    elif broken == 'model':
        models.pop('laplace')
    elif broken == 'lockout':
        data['fixtures'][0]['kickoff'] = (NOW + timedelta(minutes=15)).isoformat()
    elif broken == 'cancelled':
        data['fixtures'][0]['provider_status'] = 'PST'
    elif broken == 'wrong_date':
        data['date'] = (NOW + timedelta(days=1)).date().isoformat()
    elif broken == 'future_clock':
        quote['quote_observed_at'] = (NOW + timedelta(seconds=1)).isoformat()
    elif broken == 'settlement':
        quote['settlement'] = 'INCLUDING_EXTRA_TIME'
    elif broken == 'half':
        quote['period'] = 'FIRST_HALF'
    elif broken == 'future_model':
        for model in models.values():
            model['predictions'][0]['model_details']['cutoff'] = (NOW + timedelta(seconds=1)).isoformat()
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs'], result


def test_missing_local_witness_cannot_be_replaced_by_well_formed_hashes(tmp_path):
    data, models, _ = environment(tmp_path, witness=False)
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']


def test_repeated_same_fixture_cannot_make_three_legs(tmp_path):
    data, models, _ = environment(tmp_path)
    data['candidates'] = [data['candidates'][0]] * 3
    assert run(tmp_path, data, models)['status'] == 'ABSTAINED'


def test_reader_failure_is_explicit_and_no_fallback_positive_vote(tmp_path, monkeypatch):
    data, models, _ = environment(tmp_path)
    from goal_analysis.jobs import cup_specialist_shadow as module
    monkeypatch.setattr(module, 'inspect_competition_types', lambda *_a, **_k: (_ for _ in ()).throw(ValueError('broken')))
    result = run(tmp_path, data, models)
    assert result['status'] == 'UNAVAILABLE' and not result['candidate_legs']


def test_non_boolean_feature_flag_and_oversize_input_are_rejected(tmp_path):
    with pytest.raises(ValueError, match='BOOLEAN'):
        inspect_cup_specialist(tmp_path, {}, {}, as_of=NOW, enabled='LIVE')
    with pytest.raises(ValueError, match='BOUNDED'):
        inspect_cup_specialist(tmp_path, {'fixtures': [{}] * 1001, 'candidates': []}, {}, as_of=NOW, enabled=True)
