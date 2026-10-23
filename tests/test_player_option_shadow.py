from copy import deepcopy

import pytest
from test_player_specialist_shadow import NOW, environment

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs.player_option_shadow import review_player_options
from goal_analysis.jobs.player_specialist_shadow import _signals


def inputs(data):
    home = deepcopy(data['candidates'][0])
    other = {**home, 'selection_key': 'away'}
    options = {'club': [
        {'quality': .8, 'scoring': {'kind': 'SYNTHETIC_TEST'},
         'recommendation': {'profile_id': 'club', 'legs': [other]}},
        {'quality': .79, 'scoring': {'kind': 'SYNTHETIC_TEST'},
         'recommendation': {'profile_id': 'club', 'legs': [home]}}],
        'daily223': [{'quality': .79, 'scoring': {'kind': 'SYNTHETIC_TEST'},
                      'ticket': {'profile_id': 'daily223', 'legs': [home]}}]}
    alias = {**home, 'candidate_id': 'alias', 'source_candidate_id': home['candidate_id']}
    report = {'tickets': [{'profile_id': 'club', 'legs': [alias]},
                          {'profile_id': 'daily223', 'legs': [home]}]}
    return report, options


def test_real_stored_source_path_changes_hypothetical_order_and_binds_final_leg_only(tmp_path):
    data, models, _, _ = environment(tmp_path)
    report, options = inputs(data)
    before = deepcopy((data, models, report, options))
    result = review_player_options(tmp_path, report, options, data, models, as_of=NOW)
    assert (data, models, report, options) == before
    assert result['summary']['supported_selections'] == 3
    ranking = result['rankings'][0]
    assert ranking['baseline_order'] == [0, 1] and ranking['shadow_order'] == [1, 0]
    assert result['summary']['hypothetically_weighted_options'] == 1
    assert result['option_reviews'][-1]['shadow_quality'] == .79  # DAILY exempt.
    review = result['card_reviews'][0]
    assert review['card_sha256'] == canonical_sha256(report['tickets'][0])
    leg = review['legs'][0]
    assert leg['leg_sha256'] == canonical_sha256(report['tickets'][0]['legs'][0])
    assert leg['status'] == 'SOURCE_CONTROL_PRICE_BOUND_SHADOW_SUPPORT'
    assert leg['shadow_score_multiplier'] == pytest.approx(1.03)
    bound = leg['bound_support']
    assert bound['shared_control_probability'] == .6
    assert bound['quote']['quoted_at'] is None and bound['quote']['quote_observed_at']
    assert bound['price_receipt'] and bound['target_receipt']
    view = result
    for key in leg['player_evidence']['fixture_evidence_ref']:
        view = view[key]
    assert canonical_sha256(view) == leg['player_evidence']['fixture_evidence_sha256']
    assert all(e['available_at'] for e in view['source_events'])
    assert bound['player_evidence_sha256'] == canonical_sha256(view)
    assert result['card_reviews'][1]['legs'][0]['status'] == 'DAILY_223_EXEMPT'
    assert result['live_decision_effect'] is False and result['probability_adjustment'] == 0


@pytest.mark.parametrize('field,value', [
    ('fixture_id', 'other'), ('market_key', 'totals_2_5'), ('selection_key', 'away'),
    ('period', 'FIRST_HALF'), ('bookmaker', 'other'), ('region', 'other'),
    ('currency', 'USD'), ('decimal_price', 7.), ('quote_source_id', 'https://wrong.test/'),
    ('quote_source_sha256', 'a'*64), ('quote_observed_at', '2026-10-22T08:00:00Z'),
    ('quoted_at', '2026-10-22T08:00:00Z'), ('settlement', 'other'),
])
def test_same_candidate_alias_cannot_hide_product_source_or_receipt_difference(tmp_path, field, value):
    data, models, _, _ = environment(tmp_path)
    report, options = inputs(data)
    options['club'][1]['recommendation']['legs'][0][field] = value
    result = review_player_options(tmp_path, report, options, data, models, as_of=NOW)
    leg = result['option_reviews'][1]['legs'][0]
    assert leg['bound_support'] is None and leg['shadow_score_multiplier'] == 1
    assert result['option_reviews'][1]['shadow_quality'] == .79


def test_quote_identity_without_matching_source_alias_does_not_claim_support(tmp_path):
    data, models, _, _ = environment(tmp_path)
    report, options = inputs(data)
    options['club'][1]['recommendation']['legs'][0]['candidate_id'] = 'not-the-same-source'
    result = review_player_options(tmp_path, report, options, data, models, as_of=NOW)
    assert result['option_reviews'][1]['legs'][0]['bound_support'] is None


def test_unmatched_leg_stays_in_geometric_mean_denominator(tmp_path):
    data, models, _, _ = environment(tmp_path)
    report, options = inputs(data)
    options['club'][1]['recommendation']['legs'].append({'fixture_id': 'unknown'})
    result = review_player_options(tmp_path, report, options, data, models, as_of=NOW)
    row = result['option_reviews'][1]
    assert row['shadow_score_multiplier'] == pytest.approx(1.03 ** .5)
    assert row['legs'][1]['status'] == 'MISSING_VALIDATED_PLAYER_CONTEXT'


def test_protected_card_profile_cannot_be_weighted_under_other_dictionary_key(tmp_path):
    data, models, _, _ = environment(tmp_path)
    report, options = inputs(data)
    options['club'][1]['recommendation']['profile_id'] = 'daily223'
    result = review_player_options(tmp_path, report, options, data, models, as_of=NOW)
    assert result['option_reviews'][1]['protected_daily223']
    assert result['option_reviews'][1]['shadow_quality'] == .79


def test_absence_without_two_numerical_controls_is_withheld_not_a_positive_vote(tmp_path):
    data, models, _, _ = environment(tmp_path)
    report, options = inputs(data)
    models.pop('laplace')
    result = review_player_options(tmp_path, report, options, data, models, as_of=NOW)
    assert result['summary']['supported_selections'] == 0
    assert result['card_reviews'][0]['legs'][0]['status'] == 'WITHHELD_CONTROL_PRICE_OR_EDGE_UNPROVEN'
    assert result['option_reviews'][1]['shadow_quality'] == .79


def test_identical_call_is_deterministic_and_does_not_compound_multiplier(tmp_path):
    data, models, _, _ = environment(tmp_path)
    report, options = inputs(data)
    first = review_player_options(tmp_path, report, options, data, models, as_of=NOW)
    assert first == review_player_options(tmp_path, report, options, data, models, as_of=NOW)


def test_supported_single_leg_is_reported_without_forcing_a_three_leg_specialist_ticket(tmp_path):
    data, models, _, _ = environment(tmp_path)
    report, options = inputs(data)
    data['candidates'] = data['candidates'][:1]
    result = review_player_options(tmp_path, report, options, data, models, as_of=NOW)
    assert result['specialist']['status'] == 'ABSTAINED'
    assert result['specialist']['candidate_legs'] == []
    assert result['summary']['supported_selections'] == 1
    assert result['option_reviews'][1]['shadow_quality'] > .79


def test_option_leg_budget_rejected_before_reading_any_database(tmp_path):
    with pytest.raises(ValueError, match='BOUNDED_PLAYER_OPTION_LEGS'):
        review_player_options(tmp_path, {}, {'club': [{'ticket': {'legs': [{}] * 10001}}]},
                              {}, {}, as_of=NOW)


def test_references_do_not_duplicate_large_fixture_event_lists_per_option(tmp_path):
    data, models, _, _ = environment(tmp_path)
    report, options = inputs(data)
    options['club'] = [deepcopy(options['club'][1]) for _ in range(30)]
    result = review_player_options(tmp_path, report, options, data, models, as_of=NOW)
    assert len(result['option_reviews']) == 31
    assert all('source_events' not in row['legs'][0]['player_evidence']
               for row in result['option_reviews'])
    assert all('player_evidence' not in row['legs'][0]['bound_support']
               for row in result['option_reviews'] if row['legs'][0]['bound_support'])


def test_predicted_xi_is_visible_but_cannot_prove_role_absence_or_confirmation(tmp_path):
    data, _, _, events = environment(tmp_path, squad=False)
    rows = [r for r in events if r['fixture_id'] == events[0]['fixture_id']]
    fixture = data['fixtures'][0]
    payload = {'type': 'predicted', 'confirmed': False}
    for index, side in enumerate(('home', 'away')):
        payload[side + '_team_id'] = fixture['provider_' + side + '_team_id']
        payload[side + '_players'] = [{'player_id': f'pl_{1000+index*100+i}', 'position': 'F'}
                                    for i in range(11)]
    rows.append({**rows[0], 'event_id': 'synthetic-predicted-event', 'category': 'lineup', 'payload': payload})
    view = _signals(fixture, rows)
    assert view['lineup_status'] == 'PREDICTED' and view['confirmed_xi'] is False
    assert not view['signals'] and view['gaps']['POSITION_UNKNOWN_OR_CONFLICTING'] == 1
    assert view['entropy_score'] == .5


@pytest.mark.parametrize('enabled,fail', [(False, False), (True, False), (True, True)])
def test_coordinator_connection_default_off_failure_isolation_and_exact_card_stability(
        tmp_path, monkeypatch, enabled, fail):
    from test_model_integration import predictor_fixture
    from test_portfolio_flow import NOW as FLOW_NOW
    from test_portfolio_flow import RecordedReviewer
    from test_portfolio_flow import environment as flow_environment

    from goal_analysis.jobs import player_option_shadow
    from goal_analysis.jobs.portfolio import run_portfolio

    calls = []
    def inspect(*args, **kwargs):
        calls.append((args, kwargs))
        if fail:
            raise ValueError('Synthetic optional reader failure')
        return {'mode': 'SHADOW', 'live_decision_effect': False, 'fake_test_marker': True}
    monkeypatch.setattr(player_option_shadow, 'review_player_options', inspect)
    def run(path, flag):
        cfg, football, odds, cache, _, _, _ = flow_environment(path)
        return run_portfolio(cfg, {}, football, odds, cache, FLOW_NOW.date(),
            RecordedReviewer(), clock=lambda: FLOW_NOW, model_predictor=predictor_fixture,
            player_specialist_shadow_root=tmp_path if flag else None)['artifacts']
    control, treatment = run(tmp_path / 'control', False), run(tmp_path / 'treatment', enabled)
    assert control['numeric_models'] == treatment['numeric_models']
    for group in ('tickets', 'recommendations', 'profile_proposals', 'preview_tickets'):
        assert control['report'].get(group) == treatment['report'].get(group)
    assert len(calls) == int(enabled)
    if not enabled:
        assert 'player_specialist_shadow' not in treatment['report']
    else:
        assert treatment['report']['player_specialist_shadow']['live_decision_effect'] is False
