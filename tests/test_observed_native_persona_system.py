"""TESTONLY observed native calendar -> collector -> real 30-profile job."""
import json
import urllib.request
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from test_full_day_portfolio_system import cohort
from test_observed_native_routing import NOW, collected, fail_io

from goal_analysis import models as numeric_models
from goal_analysis.config.portfolio import LEGACY_PROFILE_IDS, MODEL_PROFILE_IDS, PROFILE_IDS
from goal_analysis.jobs import portfolio
from goal_analysis.models import gauss, kepler, laplace

pytest_plugins = ('test_observed_native_routing',)


def test_observed_native_114_calendar_reaches_all_30_own_profile_inputs(
    modules, monkeypatch, tmp_path
):
    """Full input coverage is distinct from usable quotes or successful models."""
    owned_root = tmp_path.resolve()
    assert not owned_root.is_relative_to(Path('C:/AI-Work/GoalAnalysis').resolve())
    assert not owned_root.is_relative_to(Path(__file__).resolve().parents[1])
    (owned_root / 'TESTONLY-native-persona-system.json').write_text(
        json.dumps({'TESTONLY': True, 'scope': 'observed-native-114-persona-system'}),
        encoding='utf-8',
    )
    # The imported modules fixture denies SQLite, sockets, subprocesses and real
    # provider constructors. Also deny every actual numerical fit entry point.
    monkeypatch.setattr(urllib.request, 'urlopen', fail_io)
    monkeypatch.setattr(numeric_models, 'predict_models', fail_io)
    for model in (gauss, kepler, laplace):
        monkeypatch.setattr(model, 'fit_league', fail_io)
        monkeypatch.setattr(model, '_fit_league', fail_io)

    # Exercise the real collect_live against the finite observed native pages.
    observed, config, transport = collected(modules, monkeypatch)
    expected = sorted(row['fixture_id'] for row in observed['fixtures']['records'])
    assert len(expected) == len(set(expected)) == 114
    assert len({row['provider_league_id'] for row in observed['fixtures']['records']}) == 33
    assert len(observed['native_competition_routes']) == 30
    assert transport.usage['requests'] == 2
    assert observed['calendar']['scope_receipt']['full_product_run'] is True
    assert sorted(observed['calendar']['scope_receipt']['routed_fixture_ids']) == expected

    _, source, _ = cohort()
    source.update(deepcopy(observed))
    source['config'] = deepcopy(config)
    source['candidate_input'].update(
        date=NOW.date().isoformat(), observed_at=NOW.isoformat(), candidates=[],
        preferred_bookmakers=[], matching=[],
        data_issues=[{'status': 'TESTONLY_QUOTES_UNAVAILABLE_LOCAL_UNKNOWN'}],
    )
    before = deepcopy(source)
    calls = {'collection': [], 'models': [], 'review': [], 'expert': [], 'package': []}

    def already_collected(*args, **kwargs):
        calls['collection'].append(kwargs)
        return {'artifacts': deepcopy(source)}

    def unavailable_predictor(fixtures, history, now, **kwargs):
        calls['models'].append((deepcopy(fixtures), deepcopy(history), kwargs))
        assert now == NOW
        return {}  # Missing outputs stay UNKNOWN; no fitting or fabricated p.

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
        raise RuntimeError('TESTONLY optional package unavailable')

    monkeypatch.setattr(portfolio, 'run_daily223_live', already_collected)
    report = portfolio.run_portfolio(
        config, {'operating_contract_version': 2},
        SimpleNamespace(provider='the_stats_api'), SimpleNamespace(provider='the_stats_api'),
        None, NOW.date(), reviewer=UnavailableReviewer(), clock=lambda: NOW,
        model_predictor=unavailable_predictor, expert_reviewer=unavailable_expert,
        package_reviewer=unavailable_package,
    )['artifacts']['report']

    assert len(calls['collection']) == len(calls['models']) == 1
    assert calls['collection'][0]['operating_contract_version'] == 2
    assert calls['collection'][0]['selected_competition_ids'] is None
    numeric_fixtures, numeric_history, numeric_options = calls['models'][0]
    assert sorted(row['fixture_id'] for row in numeric_fixtures) == expected
    assert numeric_history == observed['history']['records']
    assert tuple(numeric_options['enabled_profiles']) == MODEL_PROFILE_IDS
    assert report['numeric_models'] == {}
    assert report['full_day_calendar']['provider_fixture_ids'] == expected
    assert report['full_day_calendar']['provider_calendar_status'] == 'COMPLETE'

    recommendations = report['recommendations']
    assert len(recommendations) == len(PROFILE_IDS) == 30
    by_profile = {card['profile_id']: card for card in recommendations}
    assert set(by_profile) == set(PROFILE_IDS)
    assert all(isinstance(card.get('profile_name'), str) and card['profile_name'].strip()
               for card in recommendations)
    assert len({card['profile_name'] for card in recommendations}) == 30
    core = {row['profile_id']: row for row in report['profiles']
            if row['profile_id'] in LEGACY_PROFILE_IDS}
    for profile in PROFILE_IDS:
        card = core[profile] if profile in LEGACY_PROFILE_IDS else by_profile[profile]
        trace = card['selection_diagnostics']
        assert trace['input_fixture_ids'] == expected, profile
        assert trace['input_fixture_count'] == 114, profile
        assert trace['all_daily_fixtures_considered'] is True, profile
        assert sorted(row['fixture_id'] for row in trace['fixture_decisions']) == expected, profile
        assert all(row['status'] == 'REJECTED' and 'NO_VALID_QUOTES' in row['reasons']
                   for row in trace['fixture_decisions']), profile
        assert trace['ranked_candidate_ids'] == trace['selected_source_candidate_ids'] == [], profile
        if profile in MODEL_PROFILE_IDS:
            assert trace['model_input_fixture_ids'] == expected, profile
            assert not any(row['model_prediction_available'] for row in trace['fixture_decisions'])

    assert report['generated_tickets'] == report['tickets'] == []
    assert report['generation_capacity']['generated_count'] == 0
    assert report['generation_capacity']['shortfall'] == 15
    assert report['generation_capacity']['status'] == 'BELOW_TARGET'
    assert report['portfolio_objectives']['generated_count'] == 0
    assert report['portfolio_objectives']['capacity_shortfall'] == 15
    assert all(not card.get('legs') for card in recommendations)
    assert report['real_wager_placed'] is False
    assert source == before
