from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents import canonical_sha256
from goal_analysis.models import history_sample_shadow as shadow
from goal_analysis.models import kepler
from goal_analysis.models._data import (
    MIN_TEAM_MATCHES,
    InsufficientHistory,
    check_teams,
    normalize_fixtures,
    prepare_history,
)

NOW = datetime(2026, 10, 22, 10, tzinfo=UTC)


def row(identity='1', neutral=False):
    return {'fixture_id': identity, 'league_id': '78', 'home_team_id': '1',
        'away_team_id': '2', 'kickoff': (NOW - timedelta(days=2)).isoformat(),
        'observed_at': (NOW - timedelta(hours=1)).isoformat(), 'status': 'FT',
        'full_time_home': 2, 'full_time_away': 1, 'source_id': 'test:history',
        'neutral_venue': neutral}


def fixture(identity='900', neutral=False):
    return {'fixture_id': identity, 'league_id': '78', 'home_team_id': '1',
        'away_team_id': '2', 'kickoff': (NOW + timedelta(hours=2)).isoformat(),
        'neutral_venue': neutral}


def test_selects_only_explicit_false_without_mutation_and_records_counts():
    fixtures, history = [fixture(neutral=True)], [row(), row('2', True)]
    before = deepcopy((fixtures, history))
    result = shadow.select_sample(fixtures, history, NOW)
    assert (fixtures, history) == before
    assert result['selected_history_rows'] == result['isolated_neutral_rows'] == 1
    assert result['history'][0]['fixture_id'] == '1'
    assert result['fixtures'][0]['neutral_venue'] is True
    assert result['decision_effect'] == 'NONE'
    assert result['selected_history_sha256'] == canonical_sha256(result['history'])


@pytest.mark.parametrize('value', [None, 'False', 'true', 0, 1, [], {}])
def test_unknown_flags_never_become_false_and_quarantine_known_duplicate(value):
    unknown = row(neutral=value)
    result = shadow.select_sample([], [row(), unknown], NOW)
    assert result['history'] == []
    assert result['unknown_history_ids'] == ['1']
    assert result['boundary_counts']['KNOWN_ROWS_QUARANTINED_BY_UNKNOWN_VERSION'] == 1


def test_missing_history_and_target_flag_is_not_legacy_false_default():
    history, target = row(), fixture()
    history.pop('neutral_venue')
    target.pop('neutral_venue')
    result = shadow.select_sample([target, fixture()], [history, row('900')], NOW)
    assert result['history'] == []  # Target history excluded even for unknown venue.
    assert normalize_fixtures(result['fixtures'], NOW)[0] == []
    assert result['unknown_target_ids'] == ['900']


@pytest.mark.parametrize('changed', [{'neutral_venue': True}, {'full_time_home': 3},
    {'league_id': '79'}, {'away_team_id': '3'}])
def test_conflicting_versions_checked_before_neutral_isolation(changed):
    values = [row(), {**row(), **changed}]
    result = shadow.select_sample([], values, NOW)
    reverse = shadow.select_sample([], values[::-1], NOW)
    assert result == reverse
    assert result['history'] == []
    assert {'code': 'CONFLICTING_HISTORY', 'fixture_id': '1'} in result['history_diagnostics']


def test_duplicate_source_choice_is_stable_and_earliest_receipt_not_backdated():
    earlier = {**row(), 'source_id': 'earlier',
        'observed_at': (NOW - timedelta(hours=2)).isoformat()}
    result = shadow.select_sample([], [row(), earlier], NOW)
    assert result == shadow.select_sample([], [earlier, row()], NOW)
    assert result['selected_history_rows'] == 1
    assert result['history'][0]['source_id'] == 'earlier'
    assert result['history'][0]['observed_at'] == earlier['observed_at']


def test_unknown_future_or_invalid_version_cannot_remove_eligible_past_observation():
    for changed in ({'observed_at': (NOW + timedelta(seconds=1)).isoformat()},
                    {'status': 'LIVE'}, {'full_time_home': '2'}):
        unknown = {**row(neutral=None), **changed}
        result = shadow.select_sample([], [row(), unknown], NOW)
        assert len(result['history']) == 1
        assert result['unknown_eligible_history_ids_quarantined'] == []


def test_unknown_today_fixture_is_still_excluded_from_training():
    history = row('900')
    target = fixture(neutral=None)
    result = shadow.select_sample([target], [history], NOW)
    assert result['history'] == []
    assert any(d['code'] == 'TARGET_HISTORY_EXCLUDED' for d in result['history_diagnostics'])


@pytest.mark.parametrize('changed', [
    {'observed_at': (NOW + timedelta(seconds=1)).isoformat()},
    {'kickoff': (NOW - timedelta(hours=3)).isoformat()},
    {'status': 'LIVE'}, {'source_id': ''}, {'full_time_home': 2.5},
])
def test_existing_time_status_score_and_lineage_gates_remain(changed):
    assert shadow.select_sample([], [{**row(), **changed}], NOW)['history'] == []


def test_native_provider_identity_survives_projection_and_second_preparation():
    native = {**row('mt_123'), 'provider': 'the_stats_api', 'league_id': 'comp_123',
              'home_team_id': 'tm_123', 'away_team_id': 'tm_456'}
    selected = shadow.select_sample([], [native], NOW)['history']
    normalized, issues = prepare_history(selected, NOW, set())
    assert not issues and normalized[0]['fixture_id'] == native['fixture_id']
    assert selected[0]['provider'] == 'the_stats_api'


def test_disabled_default_does_not_validate_or_fit_any_input(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Disabled experiment must not run models')
    monkeypatch.setattr('goal_analysis.models.predict_models', forbidden)
    result = shadow.evaluate(None, None, None)
    assert result['status'] == 'DISABLED' and result['model_runs'] == 0


@pytest.mark.parametrize('value', [1, None, 'true'])
def test_switch_requires_explicit_boolean(value):
    with pytest.raises(ValueError):
        shadow.evaluate([], [], NOW, enabled=value)


def test_control_unmodified_treatment_isolated_and_inputs_never_aliased(monkeypatch, tmp_path):
    fixtures, history = [fixture()], [row(), row('2', True)]
    before = deepcopy((fixtures, history))
    calls = []
    def predict(targets, records, cutoff, **kwargs):
        calls.append(deepcopy((targets, records, cutoff, kwargs)))
        records.clear()  # Even a mutating injected model cannot change caller inputs.
        return {'test': {'status': 'INSUFFICIENT_HISTORY', 'predictions': []}}
    monkeypatch.setattr('goal_analysis.models.predict_models', predict)
    result = shadow.evaluate(fixtures, history, NOW, enabled=True, artifact_dir=tmp_path)
    assert (fixtures, history) == before
    assert calls[0][0:3] == (fixtures, history, NOW)
    assert len(calls[1][1]) == 1 and calls[1][1][0]['neutral_venue'] is False
    assert calls[0][3]['artifact_dir'] == tmp_path / 'control'
    assert calls[1][3]['artifact_dir'] == tmp_path / 'treatment'
    assert result['model_runs'] == 2 and result['live_promotion'] is False
    assert result['point_in_time_provider_history_certified'] is False


def test_model_minimums_stay_eight_and_sixty_plus_twenty():
    assert MIN_TEAM_MATCHES == 8
    assert kepler.MIN_TRAINING_ROWS == 60 and kepler.MIN_VALIDATION_ROWS == 20
    with pytest.raises(InsufficientHistory):
        check_teams([row(str(i)) for i in range(7)], fixture())


@pytest.mark.parametrize('enabled,fail', [(False, False), (True, False), (True, True)])
def test_real_coordinator_opt_in_sidecar_cannot_replace_live_numeric_or_tickets(
        tmp_path, monkeypatch, enabled, fail):
    from test_model_integration import predictor_fixture
    from test_portfolio_flow import NOW as FLOW_NOW
    from test_portfolio_flow import RecordedReviewer, environment

    from goal_analysis.jobs.portfolio import run_portfolio

    cfg, football, odds, cache, _, _, _ = environment(tmp_path / 'control')
    calls = []
    def evaluate(*args, **kwargs):
        calls.append((args, kwargs))
        if fail:
            raise RuntimeError('Test diagnostic failure')
        return {'mode': 'SHADOW', 'decision_effect': 'NONE',
            'treatment': {'daily223': {'predictions': [{'fixture_id': 'DO_NOT_SELECT'}]}}}
    monkeypatch.setattr(shadow, 'evaluate', evaluate)
    baseline = run_portfolio(cfg, {}, football, odds, cache, FLOW_NOW.date(),
        RecordedReviewer(), clock=lambda: FLOW_NOW, model_predictor=predictor_fixture)
    # Two fresh, identical recordings: a cache hit is a different source envelope.
    cfg, football, odds, cache, _, _, _ = environment(tmp_path / 'treatment')
    actual = run_portfolio(cfg, {}, football, odds, cache, FLOW_NOW.date(),
        RecordedReviewer(), clock=lambda: FLOW_NOW, model_predictor=predictor_fixture,
        history_sample_shadow=enabled)
    a, b = actual['artifacts'], baseline['artifacts']
    assert a['numeric_models'] == b['numeric_models']
    for group in ('tickets', 'recommendations', 'paper_proposals'):
        assert a['report'].get(group) == b['report'].get(group)
    assert len(calls) == int(enabled)
    if not enabled:
        assert 'history_sample_shadow' not in a['report']
    elif fail:
        assert a['report']['history_sample_shadow']['status'] == 'UNAVAILABLE'
    else:
        assert a['report']['history_sample_shadow']['decision_effect'] == 'NONE'
