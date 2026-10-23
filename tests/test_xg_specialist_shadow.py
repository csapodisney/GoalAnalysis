"""Actual temp raw/receipt pipeline, synthetic football values, no remote calls."""
import json
import socket
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pytest
from test_cup_specialist_shadow import NOW, OBS
from test_cup_specialist_shadow import environment as base_environment

from goal_analysis.jobs.xg_specialist_shadow import inspect_xg_specialist
from goal_analysis.storage import SnapshotStore
from goal_analysis.storage.temporal import TemporalStore


def environment(root, *, history_teams=range(2, 8), target_neutral=False, status='scheduled',
                witness=True, metadata_when=OBS):
    data, models, db = base_environment(root)
    store = SnapshotStore(db, root / 'data/raw')

    def put(value, resource, url, when=OBS):
        return store.save('the_stats_api', resource, json.dumps({'data': value}).encode(),
                          'application/json', fetched_at=when, source_url=url)

    history = []
    for index, (home, away) in enumerate((h, a) for h in history_teams for a in history_teams if h != a):
        native = 'mt_' + str(100 + index)
        history.append({'id': native, 'competition_id': 'comp_7739', 'season_id': 'sn_1',
                        'home_team': {'id': f'tm_{home}'}, 'away_team': {'id': f'tm_{away}'},
                        'utc_date': (NOW - timedelta(days=index + 1)).isoformat(),
                        'status': 'finished', 'is_neutral': False, 'xg_available': True, 'xg_quality': 'full',
                        'score': {'went_to_extra_time': False, 'went_to_penalties': False}})
        put({'match_id': native, 'overview': {'expected_goals': {
            'first_half': {'home': .8, 'away': .55}, 'second_half': {'home': .8, 'away': .55},
            'all': {'home': 99, 'away': 99}}}}, 'xg_stats',
            'https://api.thestatsapi.com/api/football/matches/' + native + '/stats')
    put(history, 'xg_history', 'https://api.thestatsapi.com/api/football/matches?status=finished&per_page=100')
    targets = [{'id': f['provider_fixture_id'], 'competition_id': f['provider_league_id'],
                'season_id': f['provider_season_id'], 'utc_date': f['kickoff'],
                'home_team': {'id': f['provider_home_team_id']},
                'away_team': {'id': f['provider_away_team_id']},
                'status': status, 'is_neutral': target_neutral} for f in data['fixtures']]
    meta = put(targets, 'current_targets',
               'https://api.thestatsapi.com/api/football/matches?status=scheduled', when=metadata_when)
    for fixture in data['fixtures']:
        fixture['neutral_venue'] = False  # Deliberately not trusted over actual raw is_neutral.
        fixture['fixture_source'] = {'content_hash': meta.content_hash, 'source_url': meta.source_url,
                                     'observed_at': metadata_when.isoformat()}
    if witness:
        TemporalStore(db, root / 'data/raw', clock=lambda: NOW - timedelta(seconds=10)).capture('raw', limit=1000)
    return data, models, db, meta


def run(root, data, models, **extra):
    with patch.object(socket, 'socket', side_effect=AssertionError('NO_NETWORK')):
        return inspect_xg_specialist(root, data, models, as_of=NOW, enabled=True, **extra)


def test_default_disabled_has_no_filesystem_or_model_input_dependency(tmp_path):
    result = inspect_xg_specialist(tmp_path / 'missing', None, None, as_of=NOW)
    assert result['status'] == 'DISABLED' and not (tmp_path / 'missing').exists()
    assert not result['xg_rate_refit_performed']


def test_full_reader_rate_forecast_and_selector_are_separate_shadow_method(tmp_path):
    data, models, _db, _meta = environment(tmp_path)
    original = deepcopy((data, models))
    result = run(tmp_path, data, models)
    assert result['status'] == 'SHADOW_CANDIDATE', result
    assert len(result['candidate_legs']) == 3 and result['combined_price'] == pytest.approx(2.3 ** 3)
    assert (data, models) == original
    assert result['probability'] is None and result['calibrated'] is False
    assert result['playable'] is result['live_decision_effect'] is False
    assert result['probability_adjustment'] == 0 and result['ranking_multiplier'] == 1
    assert result['diagnostics']['actual_xg_matches'] == 30
    assert result['recipe']['probability_combination'].startswith('NONE_')
    for leg in result['candidate_legs']:
        assert .45 < leg['xg_probability'] < .55
        assert leg['shared_control_probability'] == .6
        assert leg['xg_estimated_edge'] > .05 and 0 < leg['selection_score'] < 1
        assert leg['xg_prediction']['lambdas'] == pytest.approx({'home': 1.6, 'away': 1.1})
        assert leg['quoted_at'] is None and leg['price_receipt']['receipt_id']
        assert leg['target_receipt']['receipt_id']
        assert leg['xg_prediction']['input_dataset_id'] == result['diagnostics']['xg_dataset_id']


@pytest.mark.parametrize('neutral', [True, None])
def test_raw_neutral_venue_cannot_be_overwritten_by_supplied_false_flag(tmp_path, neutral):
    data, models, _, _ = environment(tmp_path, target_neutral=neutral)
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']
    assert result['diagnostics']['witnessed_fresh_target_fixtures'] == 0


@pytest.mark.parametrize('status', ['cancelled', 'finished', 'postponed'])
def test_raw_status_is_checked_even_if_normalized_label_claims_scheduled(tmp_path, status):
    data, models, _, _ = environment(tmp_path, status=status)
    assert all(f['provider_status'] == 'NS' for f in data['fixtures'])
    assert run(tmp_path, data, models)['status'] == 'ABSTAINED'


@pytest.mark.parametrize('teams', [range(2, 4), range(2, 6)])
def test_no_thin_or_missing_opponent_history_substitution(tmp_path, teams):
    data, models, _, _ = environment(tmp_path, history_teams=teams)
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']
    assert result['diagnostics']['two_sided_xg_prediction_fixtures'] < 3


@pytest.mark.parametrize('minutes', [31, -1])
def test_old_or_future_target_metadata_is_not_current_evidence(tmp_path, minutes):
    data, models, _, _ = environment(tmp_path, metadata_when=NOW - timedelta(minutes=minutes))
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']


@pytest.mark.parametrize('broken', ['price', 'source', 'models', 'cutoff', 'mixed_book', 'identity', 'lockout'])
def test_existing_hard_data_and_control_gates_cannot_be_bypassed_by_xg(tmp_path, broken):
    data, models, _, _ = environment(tmp_path)
    if broken == 'price':
        data['candidates'][0]['decimal_price'] = 2.8
    elif broken == 'source':
        data['candidates'][0]['quote_source_sha256'] = 'a' * 64
    elif broken == 'models':
        models.pop('laplace')
    elif broken == 'cutoff':
        models['laplace']['predictions'][0]['model_details']['cutoff'] = (NOW + timedelta(seconds=1)).isoformat()
    elif broken == 'mixed_book':
        data['candidates'][0]['currency'] = 'USD'
    elif broken == 'identity':
        data['fixtures'][0]['provider_home_team_id'] = 'tm_999'
    elif broken == 'lockout':
        data['fixtures'][0]['kickoff'] = (NOW + timedelta(minutes=15)).isoformat()
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']


def test_missing_actual_witness_is_not_replaced_with_a_source_hash(tmp_path):
    data, models, _, _ = environment(tmp_path, witness=False)
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']


def test_corrupt_target_raw_payload_is_not_used(tmp_path):
    data, models, _, meta = environment(tmp_path)
    path = tmp_path / 'data/raw' / meta.relative_path
    path.write_bytes(path.read_bytes() + b' ')
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']


def test_xg_reader_failure_cannot_stop_the_stable_path_or_manufacture_a_candidate(tmp_path, monkeypatch):
    from goal_analysis.jobs import xg_specialist_shadow as module
    data, models, _, _ = environment(tmp_path)
    monkeypatch.setattr(module, 'inspect_xg_rates', lambda *_a, **_k: (_ for _ in ()).throw(ValueError('bad')))
    result = run(tmp_path, data, models)
    assert result['status'] == 'UNAVAILABLE' and not result['candidate_legs']


def test_unknown_flags_or_unbounded_input_rejected_before_work(tmp_path):
    with pytest.raises(ValueError, match='BOOLEAN'):
        inspect_xg_specialist(tmp_path, {}, {}, as_of=NOW, enabled='LIVE')
    with pytest.raises(ValueError, match='BOUNDED'):
        inspect_xg_specialist(tmp_path, {'fixtures': [{}] * 1001, 'candidates': []}, {}, as_of=NOW, enabled=True)


def test_recipe_does_not_change_with_daily_input_and_binds_actual_dependencies(tmp_path):
    first = inspect_xg_specialist(tmp_path, None, None, as_of=NOW)
    later = inspect_xg_specialist(tmp_path, {'different': True}, None, as_of=NOW + timedelta(days=1))
    assert first['recipe_sha256'] == later['recipe_sha256']
    from hashlib import sha256
    from pathlib import Path

    hashes = first['recipe']['implementation_files_sha256']
    assert len(hashes) == 20
    assert {'storage/source_observations.py', 'storage/http_observations.py'} <= set(hashes)
    root = Path(__file__).resolve().parents[1] / 'src/goal_analysis'
    assert all(sha256((root / name).read_bytes()).hexdigest() == value for name, value in hashes.items())


def test_xg_control_disagreement_prevents_prose_or_control_only_override(tmp_path):
    data, models, _, _ = environment(tmp_path)
    for model in models.values():
        for prediction in model['predictions']:
            prediction['probabilities'] = {'home': .85, 'draw': .075, 'away': .075}
    result = run(tmp_path, data, models)
    assert result['diagnostics']['control_eligible_selections'] == 3
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']
