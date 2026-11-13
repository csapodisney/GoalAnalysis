"""TESTONLY real coordinator/scope with fake acquisition; no DB, host or network."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

from test_ten_agent_portfolio_integration import NOW, _setup

from goal_analysis.jobs import candidate_context_review as context_job
from goal_analysis.jobs import portfolio


def _full_day(source, *, priced):
    template = deepcopy(source['fixtures']['records'][0])
    fixtures = [{**deepcopy(template), 'fixture_id': f'TESTONLY:full:{index}',
        'home_team': f'TESTONLY home {index}', 'away_team': f'TESTONLY away {index}'}
        for index in range(73)]
    source['fixtures']['records'] = fixtures
    receipt = source['calendar']['scope_receipt']
    receipt['provider_fixture_ids'] = [row['fixture_id'] for row in fixtures]
    receipt['events'] = deepcopy(fixtures)
    candidate_template = deepcopy(source['candidate_input']['candidates'][0])
    source['candidate_input']['candidates'] = [{**deepcopy(candidate_template), **row,
        'candidate_id': f'TESTONLY:full-price:{index}'}
        for index, row in enumerate(fixtures[:priced])]
    return fixtures


def test_active_context_and_weather_offer_all73_with_only38_priced_candidates(monkeypatch, tmp_path):
    actual_prepare = context_job.prepare
    config, settings, source, calls, forbidden, arguments = _setup(monkeypatch, tmp_path)
    fixtures = _full_day(source, priced=38)
    weather_calls, context_fixture_ids = [], []

    def forecast(fixture):
        weather_calls.append(fixture['fixture_id'])
        if fixture['fixture_id'] == fixtures[-1]['fixture_id']:
            raise RuntimeError('TESTONLY local optional weather failure')
        return {'fixture_id': fixture['fixture_id'], 'kickoff': fixture['kickoff'],
            'status': 'UNKNOWN', 'summary': 'TESTONLY unverified venue; no invented forecast',
            'observed_at': NOW.isoformat(), 'source_url': None}

    def supplement(root, seed, **kwargs):
        context_fixture_ids.extend(row['fixture_id'] for row in seed['fixtures'])
        assert kwargs['context_shadow'] is True
        return {'context_collection_shadow': {'collection': {
            'context_by_fixture': {}, 'usage': {'requests': 0}}}}

    monkeypatch.setattr(context_job, 'prepare', actual_prepare)
    monkeypatch.setattr('goal_analysis.jobs.expert_advisory.enrich_sports_supplement', supplement)
    monkeypatch.setattr('goal_analysis.jobs.fixture_contexts.link_report', lambda *args, **kwargs: {})
    monkeypatch.setattr('goal_analysis.jobs.xg_specialist_shadow._targets', lambda *args, **kwargs: ({}, {}, {}))
    monkeypatch.setattr('goal_analysis.jobs.player_collection_shadow.enabled', lambda root: False)
    monkeypatch.setattr('goal_analysis.jobs.calendar_collection_shadow.enabled', lambda root: False)
    monkeypatch.setattr(context_job, 'compose', lambda root, selected, packets, collection, as_of: {
        'as_of': as_of.isoformat(), 'contexts': {}, 'collection': deepcopy(collection)})
    arguments['weather_provider'] = SimpleNamespace(forecast=forecast)
    result = portfolio.run_portfolio(config, settings, object(), object(), None, NOW.date(), **arguments)

    all_ids = [row['fixture_id'] for row in fixtures]
    assert context_fixture_ids == weather_calls == all_ids
    assert len(weather_calls) == len(set(weather_calls)) == 73
    frame = calls['executor'][0]
    assert len(frame['enriched']['candidates']) == 38
    assert frame['fixture_records'] == fixtures
    assert set(frame['weather_by_fixture']) == set(all_ids)
    assert all(row['status'] == 'UNKNOWN' for row in frame['weather_by_fixture'].values())
    frozen = result['artifacts']['candidate_context_inputs']['frozen_weather']
    assert frozen['attempted_fixture_ids'] == sorted(all_ids)
    assert frozen['skipped_fixture_ids'] == []
    assert len(result['artifacts']['report']['full_day_calendar']['provider_fixture_ids']) == 73
    forbidden.assert_not_called()


def test_context_off_empty_price_cohort_still_gets_every_allowed_daily_weather_fixture(monkeypatch, tmp_path):
    config, settings, source, calls, forbidden, arguments = _setup(monkeypatch, tmp_path)
    fixtures = _full_day(source, priced=0)
    fixtures[-1]['kickoff'] = (NOW - timedelta(hours=1)).isoformat()
    source['calendar']['scope_receipt']['events'] = deepcopy(fixtures)
    weather_calls = []

    def forecast(fixture):
        weather_calls.append(fixture['fixture_id'])
        return {'fixture_id': fixture['fixture_id'], 'kickoff': fixture['kickoff'],
            'status': 'UNKNOWN', 'summary': 'TESTONLY missing exact stadium', 'source_url': None}

    monkeypatch.setattr('goal_analysis.jobs.profile_context_stage.policy', lambda root: 'OFF')
    monkeypatch.setattr(context_job, 'policy', lambda root: 'OFF')
    arguments['weather_provider'] = SimpleNamespace(forecast=forecast)
    portfolio.run_portfolio(config, settings, object(), object(), None, NOW.date(), **arguments)

    assert weather_calls == [row['fixture_id'] for row in fixtures[:-1]]
    assert len(weather_calls) == len(set(weather_calls)) == 72
    assert calls['context'] == []
    frame = calls['executor'][0]
    assert frame['enriched']['candidates'] == []
    assert len(frame['fixture_records']) == 73
    assert frame['artifacts']['calendar']['scope_receipt']['provider_fixture_ids'] == [
        row['fixture_id'] for row in fixtures]
    assert set(frame['weather_by_fixture']) == set(weather_calls)
    assert fixtures[-1]['fixture_id'] not in frame['weather_by_fixture']
    assert all(row['status'] == 'UNKNOWN' for row in frame['weather_by_fixture'].values())
    forbidden.assert_not_called()
