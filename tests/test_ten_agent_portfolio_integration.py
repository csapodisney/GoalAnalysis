"""Actual coordinator routing with TESTONLY acquisition/executor; no paid or DB work."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.config.portfolio import MODEL_PROFILE_IDS
from goal_analysis.jobs import portfolio

NOW = datetime(2026, 11, 12, 15, tzinfo=UTC)


def _source():
    config = {
        'schema_version': 1, 'account_region': 'DE', 'currency': 'EUR',
        'preferred_bookmakers': ['testonly_book'], 'max_football_calls': 0, 'max_odds_credits': 0,
        'leagues': [{'competition_id': 'TESTONLY', 'api_football_id': 78, 'season': 2026,
                     'history_seasons': [2026], 'odds_sport_key': 'soccer_testonly',
                     'competition_type': 'LEAGUE'}],
    }
    fixtures = [{'fixture_id': f'TESTONLY:f:{index}', 'kickoff': (NOW + timedelta(hours=4)).isoformat(),
                 'home_team': f'TESTONLY home {index}', 'away_team': f'TESTONLY away {index}'}
                for index in range(45)]
    candidates = [{**row, 'candidate_id': f'TESTONLY:c:{index}', 'market_key': 'totals_2_5',
                   'selection_key': 'over', 'period': 'FULL_TIME', 'decimal_price': 2.0,
                   'quoted_at': None, 'evidence': []} for index, row in enumerate(fixtures[:35])]
    receipt = {'scope': 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY', 'date': NOW.date().isoformat(),
               'status': 'COMPLETE', 'provider_fixture_ids': [row['fixture_id'] for row in fixtures],
               'events': deepcopy(fixtures)}
    return config, {
        'config': deepcopy(config), 'calendar': {'scope_receipt': receipt},
        'fixtures': {'records': fixtures},
        'history': {'records': [{'fixture_id': 'TESTONLY:retained-match', 'full_time_home': 2}]},
        'candidate_input': {'date': NOW.date().isoformat(), 'observed_at': NOW.isoformat(),
                            'candidates': candidates},
        'report': {'usage': {'football_calls': 0}, 'data_issues': ['TESTONLY: quote clock UNKNOWN']},
    }


def _setup(monkeypatch, tmp_path, *, model_failure=False):
    config, source = _source()
    calls = {'collect': [], 'predict': [], 'context': [], 'executor': []}
    forbidden = Mock(side_effect=AssertionError('TESTONLY_OLD_SELECTION_OR_PAID_REVIEW_FORBIDDEN'))
    for name in (
        'goal_analysis.engine.portfolio.build_portfolio',
        'goal_analysis.engine.diversification.allocate',
        'goal_analysis.engine.portfolio_preview.build_previews',
        'goal_analysis.engine.portfolio_preview.preview_candidates',
        'goal_analysis.engine.recommendations.build_recommendations',
        'goal_analysis.engine.model_recommendations.build_model_recommendations',
        'goal_analysis.tipsters.build_tipster_recommendations',
        'goal_analysis.generated_tickets.finalize_generated',
        'goal_analysis.engine.publication_odds.apply_publication_odds',
        'goal_analysis.jobs.portfolio._guard_final_publication',
    ):
        monkeypatch.setattr(name, forbidden)
    monkeypatch.setattr('goal_analysis.tipsters.coverage_search.policy', lambda root: 'SHADOW')
    monkeypatch.setattr('goal_analysis.jobs.daily_review_cohort.policy', lambda root: 'SHADOW')
    monkeypatch.setattr('goal_analysis.jobs.profile_context_stage.policy', lambda root: 'ACTIVE')
    monkeypatch.setattr('goal_analysis.jobs.candidate_context_review.policy', lambda root: 'ACTIVE')
    monkeypatch.setattr('goal_analysis.jobs.odds_monitor.monitor_history', lambda root, now: [])
    monkeypatch.setattr('goal_analysis.engine.portfolio.specialist_veto_override_contexts',
                        lambda *args, **kwargs: {})
    monkeypatch.setattr('goal_analysis.tipsters.recommendations.club_veto_candidate_context',
                        lambda *args, **kwargs: {})
    monkeypatch.setattr('goal_analysis.tipsters.recommendations.contrarian_candidate_context',
                        lambda *args, **kwargs: {})
    monkeypatch.setattr('goal_analysis.engine.odds_movement.annotate_odds_movement',
                        lambda value, history, now: {**deepcopy(value), 'TESTONLY_history': deepcopy(history)})
    monkeypatch.setattr(portfolio, 'enrich_daily223_input',
                        lambda value, history: {**deepcopy(value), 'TESTONLY_history_features': deepcopy(history)})

    def collect(*args, **kwargs):
        calls['collect'].append(deepcopy(kwargs))
        return {'artifacts': deepcopy(source)}

    def predict(fixtures, history, cutoff, **kwargs):
        calls['predict'].append((deepcopy(fixtures), deepcopy(history), cutoff, deepcopy(kwargs)))
        if model_failure:
            raise RuntimeError('TESTONLY optional numeric model unavailable')
        return {name: {'profile_id': name, 'status': 'TESTONLY_SUPPLIED', 'predictions': [],
                       'fixture_ids': [row['fixture_id'] for row in fixtures]} for name in MODEL_PROFILE_IDS}

    def context(root, fixtures, packets, **kwargs):
        calls['context'].append((deepcopy(fixtures), deepcopy(packets), deepcopy(kwargs['eligible_candidates'])))
        return {'as_of': NOW.isoformat(), 'collection': {'TESTONLY': True},
                'source_views': {'xg': {'value': 1.4}, 'players': {'TESTONLY': 'fixture-only'}},
                'weather_by_fixture': {fixtures[0]['fixture_id']: {
                    'fixture_id': fixtures[0]['fixture_id'], 'status': 'SOURCED', 'temperature_c': 12}},
                'TESTONLY_context_marker': 'all supplied fixtures preserved'}

    def executor(**frame):
        calls['executor'].append(deepcopy(frame))
        return {'schema_version': 3, 'module': 'ARTHUR_PORTFOLIO',
                'run_id': frame['run_id'], 'date': frame['target_date'].isoformat(),
                'construction_status': 'TESTONLY_EXECUTOR_RESULT',
                'tickets': [{'ticket_id': 'TESTONLY:canonical-output', 'profile_id': AGENT_IDS[0],
                             'legs': [], 'status': 'DRAFT', 'playable': False}],
                'TESTONLY_executor_marker': ['preserve', 'order'], 'real_wager_placed': False}

    monkeypatch.setattr(portfolio, 'run_daily223_live', collect)
    monkeypatch.setattr('goal_analysis.jobs.candidate_context_review.prepare', context)
    monkeypatch.setattr('goal_analysis.jobs.candidate_context_review.attach', lambda packets, context: deepcopy(packets))
    ledger = SimpleNamespace(
        path=tmp_path / 'TESTONLY-root/data/arthur/ledger.sqlite3',
        list_runs=lambda **kwargs: [{'run_id': 'TESTONLY:prior', 'date': NOW.date().isoformat()}],
        feedback_summary=lambda: {'actual': {'summary': {'TESTONLY': 'unknown costs remain unknown'}}},
    )
    arguments = {'reviewer': SimpleNamespace(review=forbidden), 'ledger': ledger, 'clock': lambda: NOW,
                 'model_predictor': predict, 'agent_executor': executor, 'weather_provider': object(),
                 'nemotron_stylist': forbidden, 'package_reviewer': forbidden, 'expert_reviewer': forbidden}
    settings = {'operating_contract_version': 2, 'enabled_profiles': ['daily223', *AGENT_IDS]}
    return config, settings, source, calls, forbidden, arguments


@pytest.mark.parametrize('model_failure', [False, True])
def test_explicit_ten_agent_path_receives_full_frame_and_returns_without_old_selector(
    monkeypatch, tmp_path, model_failure,
):
    config, settings, source, calls, forbidden, arguments = _setup(monkeypatch, tmp_path, model_failure=model_failure)
    before = deepcopy(source)
    result = portfolio.run_portfolio(config, settings, object(), object(), None, NOW.date(), **arguments)
    report = result['artifacts']['report']
    assert len(calls['collect']) == len(calls['predict']) == len(calls['context']) == len(calls['executor']) == 1
    assert calls['collect'][0]['selected_competition_ids'] is None
    assert calls['collect'][0]['construct_legacy'] is False
    assert calls['collect'][0]['operating_contract_version'] == 2
    fixtures, history, cutoff, model_arguments = calls['predict'][0]
    assert len(fixtures) == 45 and fixtures == source['fixtures']['records']
    assert history == source['history']['records'] and cutoff == NOW
    assert tuple(model_arguments['enabled_profiles']) == MODEL_PROFILE_IDS
    assert not set(MODEL_PROFILE_IDS) & set(settings['enabled_profiles'])
    context_fixtures, packets, eligible = calls['context'][0]
    assert context_fixtures == fixtures and len(context_fixtures) == 45
    assert {row['candidate_id'] for row in packets} == {row['candidate_id'] for row in eligible}
    assert len(packets) == 35  # Exceeds old max_candidates=30; no shared top-N packet shortlist.
    frame = calls['executor'][0]
    assert frame['fixture_records'] == fixtures
    assert len(frame['artifacts']['calendar']['scope_receipt']['provider_fixture_ids']) == 45
    assert len(frame['enriched']['candidates']) == len(frame['club_enriched']['candidates']) == 35
    assert frame['candidate_context']['TESTONLY_context_marker'] == 'all supplied fixtures preserved'
    assert frame['weather_by_fixture'][fixtures[0]['fixture_id']]['temperature_c'] == 12
    assert frame['historical_runs'][0]['run_id'] == 'TESTONLY:prior'
    assert frame['feedback']['actual']['summary']['TESTONLY'] == 'unknown costs remain unknown'
    assert set(frame['artifacts']['numeric_models']) == set(MODEL_PROFILE_IDS)
    assert all(row['status'] == ('MODEL_ERROR' if model_failure else 'TESTONLY_SUPPLIED')
               for row in frame['artifacts']['numeric_models'].values())
    assert frame['target_date'] == NOW.date() and frame['as_of'] == NOW
    assert report['construction_status'] == 'TESTONLY_EXECUTOR_RESULT'
    assert report['TESTONLY_executor_marker'] == ['preserve', 'order']
    assert [row['ticket_id'] for row in report['tickets']] == ['TESTONLY:canonical-output']
    assert report['numeric_models'] == frame['artifacts']['numeric_models']
    assert report['real_wager_placed'] is False
    assert source == before
    forbidden.assert_not_called()


def test_manual_league_shortlist_rejected_before_collection_or_executor(monkeypatch, tmp_path):
    config, settings, _, calls, forbidden, arguments = _setup(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match='full accessible day'):
        portfolio.run_portfolio(config, settings, object(), object(), None, NOW.date(),
                                selected_competition_ids=[78], **arguments)
    assert all(not values for values in calls.values())
    forbidden.assert_not_called()


def test_bad_executor_report_is_rejected_without_legacy_fallback(monkeypatch, tmp_path):
    config, settings, _, calls, forbidden, arguments = _setup(monkeypatch, tmp_path)
    arguments['agent_executor'] = lambda **frame: {'run_id': 'FOREIGN', 'date': NOW.date().isoformat(),
                                                  'real_wager_placed': False}
    with pytest.raises(ValueError, match='independent-agent report identity'):
        portfolio.run_portfolio(config, settings, object(), object(), None, NOW.date(), **arguments)
    assert len(calls['collect']) == len(calls['predict']) == 1
    forbidden.assert_not_called()


def test_missing_candidate_envelope_stops_explicitly_without_old_selector(monkeypatch, tmp_path):
    config, settings, source, calls, forbidden, arguments = _setup(monkeypatch, tmp_path)
    source.pop('candidate_input')
    before = deepcopy(source)
    with pytest.raises(ValueError, match='TEN_AGENT_PRICE_INPUT_ENVELOPE_MISSING_NO_LEGACY_FALLBACK'):
        portfolio.run_portfolio(config, settings, object(), object(), None, NOW.date(), **arguments)
    assert len(calls['collect']) == 1
    assert calls['predict'] == calls['context'] == calls['executor'] == []
    assert source == before
    forbidden.assert_not_called()


def test_missing_optional_history_preserves_full_day_and_other_facts_with_local_unknown(monkeypatch, tmp_path):
    config, settings, source, calls, forbidden, arguments = _setup(monkeypatch, tmp_path)
    source.pop('history')
    source['candidate_input']['candidates'][0]['evidence'] = [
        {'category': 'historical', 'claim': 'TESTONLY unsupported history must not survive'},
        {'category': 'venue_form', 'claim': 'TESTONLY unsupported venue form must not survive'},
        {'category': 'team_news', 'claim': 'TESTONLY independent source stays available'},
    ]
    before = deepcopy(source)
    result = portfolio.run_portfolio(config, settings, object(), object(), None, NOW.date(), **arguments)
    assert len(calls['executor']) == 1
    frame = calls['executor'][0]
    assert len(frame['fixture_records']) == 45
    assert len(frame['enriched']['candidates']) == 35
    assert frame['enriched']['history_analysis'] == {
        'status': 'UNKNOWN', 'candidates': [], 'reason': 'NO_COLLECTED_HISTORY_ENVELOPE'}
    assert frame['enriched']['candidates'][0]['evidence'] == [
        {'category': 'team_news', 'claim': 'TESTONLY independent source stays available'}]
    assert calls['predict'][0][1] == []
    assert tuple(calls['predict'][0][3]['enabled_profiles']) == MODEL_PROFILE_IDS
    assert frame['candidate_context']['TESTONLY_context_marker'] == 'all supplied fixtures preserved'
    assert result['artifacts']['report']['construction_status'] == 'TESTONLY_EXECUTOR_RESULT'
    assert source == before
    forbidden.assert_not_called()
