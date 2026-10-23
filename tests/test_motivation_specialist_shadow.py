"""Real local news witnesses, native source prices, unchanged numerical ranks."""
import json
import socket
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pytest
from test_xg_specialist_shadow import NOW
from test_xg_specialist_shadow import environment as target_environment

from goal_analysis.jobs.cup_specialist_shadow import _persona
from goal_analysis.jobs.motivation_specialist_shadow import inspect_motivation_specialist
from goal_analysis.normalization.news_kinds import HUMAN_KINDS
from goal_analysis.storage import SnapshotStore
from goal_analysis.storage.sports_news import NewsStore
from goal_analysis.storage.temporal import TemporalStore
from goal_analysis.tipsters.recommendations import _fixtures, _models, _pool, _ranked


def environment(root, *, kind='SALARY_PAYMENT', witness=True, published_hours=2, observed_hours=.75,
                available=None, with_news=True):
    data, models, db, meta = target_environment(root, history_teams=[])
    archive = NewsStore(db)
    archive.initialize()
    published = NOW - timedelta(hours=published_hours)
    observed = NOW - timedelta(hours=observed_hours)
    fetched = observed - timedelta(minutes=5)
    post = (int(published.timestamp() * 1000) - 1288834974657) << 22
    url = f'https://x.com/ClubNews/status/{post}'
    answer = {'status': 'COMPLETE', 'model': 'grok-4.7', 'citations': [url], 'claims': [
        {'claim_id': 'claim-' + f['provider_fixture_id'], 'fixture_id': f['fixture_id'], 'kind': kind,
         'claim': 'Model summary about payment or motivation; not verified source content.',
         'source_url': url, 'author': 'ClubNews', 'published_at': published.isoformat(),
         'fetched_at': fetched.isoformat(), 'content_status': 'UNVERIFIED',
         'provenance_status': 'CITATION_AND_TIMESTAMP_CHECKED'} for f in data['fixtures']]}
    if with_news:
        archive.ingest_grok_result(answer, request_id='motivation-test', observed_at=observed)
    if witness:
        TemporalStore(db, root / 'data/raw', clock=lambda: available or NOW - timedelta(seconds=5)).capture('news', limit=100)
    return data, models, db, archive, answer, meta


def run(root, data, models):
    with patch.object(socket, 'socket', side_effect=AssertionError('NO_NETWORK')):
        return inspect_motivation_specialist(root, data, models, as_of=NOW, enabled=True)


def table(root, db):
    rows = [{'team': {'id': f'tm_{i}'}, 'group_label': None, 'position': i - 1, 'matches_played': 4,
             'wins': 3, 'draws': 1, 'losses': 0, 'points': 10, 'goals_for': 7,
             'goals_against': 5, 'goal_difference': 2} for i in range(2, 8)]
    raw = SnapshotStore(db, root / 'data/raw').save('the_stats_api', 'standings',
        json.dumps({'data': rows}).encode(), 'application/json', fetched_at=NOW - timedelta(minutes=2),
        source_url='https://api.thestatsapi.com/api/football/competitions/comp_7739/seasons/sn_1/standings')
    TemporalStore(db, root / 'data/raw', clock=lambda: NOW - timedelta(seconds=1)).capture('raw', limit=100)
    return raw


def test_disabled_does_not_require_inputs_or_create_a_directory(tmp_path):
    result = inspect_motivation_specialist(tmp_path / 'missing', None, None, as_of=NOW)
    assert result['status'] == 'DISABLED' and not (tmp_path / 'missing').exists()


@pytest.mark.parametrize('kind', sorted(HUMAN_KINDS))
def test_fresh_source_bound_human_context_is_review_scope_not_a_motivational_uplift(tmp_path, kind):
    data, models, _, _, _, _ = environment(tmp_path, kind=kind)
    original = deepcopy((data, models))
    result = run(tmp_path, data, models)
    assert result['status'] == 'SHADOW_REVIEW_CANDIDATE', result
    assert result['combined_price'] == pytest.approx(2.3 ** 3) and len(result['candidate_legs']) == 3
    assert (data, models) == original
    assert not result['playable'] and not result['live_decision_effect'] and result['independent_review_required']
    assert result['probability'] is result['mental_motivation'] is None
    assert result['probability_adjustment'] == 0 and result['ranking_multiplier'] == 1.
    for leg in result['candidate_legs']:
        view = leg['motivation_review_context']
        assert view['claim_truth_status'] == 'UNVERIFIED' and view['mental_motivation'] is None
        assert view['fresh_human_claims'][0]['kind'] == kind
        assert view['fresh_human_claims'][0]['provenances'][0]['receipt_id']
        assert view['independent_corroboration'] == 'NOT_ESTABLISHED'
        assert leg['shared_control_probability'] == .6 and leg['quoted_at'] is None


def test_numerical_ranks_are_identical_to_existing_base_persona(tmp_path):
    data, models, *_ = environment(tmp_path)
    fixtures = _fixtures(data['fixtures'], data['date'], NOW)
    pool, _ = _pool(data, fixtures, _models(models, fixtures, NOW), NOW)
    base = {r['quote']['candidate_id']: r['score'] for r in _ranked(pool, _persona(), NOW)}
    result = run(tmp_path, data, models)
    assert result['status'] == 'SHADOW_REVIEW_CANDIDATE'
    assert all(l['selection_score'] == base[l['candidate_id']] for l in result['candidate_legs'])


@pytest.mark.parametrize('kwargs', [
    {'witness': False}, {'available': NOW + timedelta(seconds=1)},
    {'published_hours': 49}, {'published_hours': 8, 'observed_hours': 7},
    {'with_news': False}, {'kind': 'OTHER'}, {'kind': 'INJURY'}])
def test_missing_stale_future_or_nonhuman_context_cannot_become_motivation(tmp_path, kwargs):
    data, models, *_ = environment(tmp_path, **kwargs)
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']
    assert result['mental_motivation'] is None


def test_standings_and_verified_true_caller_flags_do_not_invent_remaining_games_or_objectives(tmp_path):
    data, models, db, *_ = environment(tmp_path, with_news=False)
    table(tmp_path, db)
    data['motivation'] = {'verified': True, 'home_must_win': True, 'probability_uplift': .5}
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/competition-rules.json').write_text('{"verified":true}')
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED'
    assert result['diagnostics']['stakes_team_statuses'] == {'DESCRIPTIVE_ONLY': 6}
    assert result['diagnostics']['official_rules_registry'] == 'SOURCE_BOUND_SHADOW_V1'
    for view in result['fixture_evidence'].values():
        assert not view['mathematical_stakes_used_for_selection']
        assert all(t['games_remaining'] is None and not t['objectives'] for t in view['standings_context']['teams'])


def test_descriptive_standings_can_be_attached_to_review_without_changing_ranking(tmp_path):
    data, models, db, *_ = environment(tmp_path)
    first = run(tmp_path, data, models)
    table(tmp_path, db)
    second = run(tmp_path, data, models)
    assert first['status'] == second['status'] == 'SHADOW_REVIEW_CANDIDATE'
    assert [l['selection_score'] for l in first['candidate_legs']] == [l['selection_score'] for l in second['candidate_legs']]
    assert all(l['motivation_review_context']['standings_context']['table_status'] == 'DESCRIPTIVE_TABLE_ONLY'
               for l in second['candidate_legs'])


def test_multiple_summaries_and_reimports_are_retained_not_voted_as_independent_truth(tmp_path):
    data, models, db, archive, answer, _ = environment(tmp_path)
    archive.ingest_grok_result(answer, request_id='repeat', observed_at=NOW - timedelta(minutes=10))
    changed = deepcopy(answer)
    for c in changed['claims']:
        c['claim'] = 'Another unverified summary says the payment issue was resolved.'
    archive.ingest_grok_result(changed, request_id='another-version', observed_at=NOW - timedelta(minutes=5))
    TemporalStore(db, tmp_path / 'data/raw', clock=lambda: NOW - timedelta(seconds=1)).capture('news', limit=100)
    result = run(tmp_path, data, models)
    assert result['status'] == 'SHADOW_REVIEW_CANDIDATE'
    for leg in result['candidate_legs']:
        view = leg['motivation_review_context']
        assert len(view['fresh_human_claims']) == 2 and view['source_posts'] == 1
        assert view['same_kind_multiple_assertions'] and view['independent_corroboration'] == 'NOT_ESTABLISHED'
        assert sorted(len(c['provenances']) for c in view['fresh_human_claims']) == [1, 2]


@pytest.mark.parametrize('broken', ['price', 'models', 'model_cutoff', 'mixed_book', 'identity', 'lockout', 'metadata'])
def test_human_context_cannot_bypass_price_identity_model_or_time_gates(tmp_path, broken):
    data, models, *_ = environment(tmp_path)
    if broken == 'price':
        data['candidates'][0]['decimal_price'] = 3.
    elif broken == 'models':
        models.pop('laplace')
    elif broken == 'model_cutoff':
        models['gauss']['predictions'][0]['model_details']['cutoff'] = (NOW + timedelta(seconds=1)).isoformat()
    elif broken == 'mixed_book':
        data['candidates'][0]['currency'] = 'USD'
    elif broken == 'identity':
        data['fixtures'][0]['provider_home_team_id'] = 'tm_999'
    elif broken == 'lockout':
        data['fixtures'][0]['kickoff'] = (NOW + timedelta(minutes=15)).isoformat()
    else:
        data['fixtures'][0]['fixture_source']['content_hash'] = 'a' * 64
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']


def test_source_bytes_are_required_even_with_a_local_hash_and_a_model_summary(tmp_path):
    data, models, _, _, _, meta = environment(tmp_path)
    path = tmp_path / 'data/raw' / meta.relative_path
    path.write_bytes(path.read_bytes() + b' ')
    result = run(tmp_path, data, models)
    assert result['status'] == 'ABSTAINED' and not result['candidate_legs']


def test_optional_reader_failure_cannot_approve_a_tip_or_mutate_stable_inputs(tmp_path, monkeypatch):
    from goal_analysis.jobs import motivation_specialist_shadow as module
    data, models, *_ = environment(tmp_path)
    original = deepcopy((data, models))
    monkeypatch.setattr(module, 'inspect_human_factors', lambda *_a, **_k: (_ for _ in ()).throw(ValueError('bad')))
    result = run(tmp_path, data, models)
    assert result['status'] == 'UNAVAILABLE' and not result['candidate_legs'] and (data, models) == original


def test_recipe_fixed_and_input_flags_bounded(tmp_path):
    a = inspect_motivation_specialist(tmp_path, None, None, as_of=NOW)
    b = inspect_motivation_specialist(tmp_path, {}, {}, as_of=NOW + timedelta(days=1))
    assert a['recipe_sha256'] == b['recipe_sha256']
    with pytest.raises(ValueError, match='BOOLEAN'):
        inspect_motivation_specialist(tmp_path, {}, {}, as_of=NOW, enabled='LIVE')
    with pytest.raises(ValueError, match='BOUNDED'):
        inspect_motivation_specialist(tmp_path, {'fixtures': [{}] * 1001}, {}, as_of=NOW, enabled=True)
