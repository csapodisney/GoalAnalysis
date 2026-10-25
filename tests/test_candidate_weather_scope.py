"""Weather follows the full candidate universe and its original receipt cutoff."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest
from test_candidate_context_scope import _candidate, _environment
from test_player_specialist_shadow import NOW

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import candidate_context_review as job


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, seconds=1):
        self.now += timedelta(seconds=seconds)


def _forecast(fixture, observed_at):
    return {
        'fixture_id': fixture['fixture_id'], 'status': 'SOURCED',
        'weather_kind': 'FORECAST', 'observed_at': observed_at.isoformat(),
        'time_basis': 'FETCHED_AT', 'temperature_c': 9.0,
        'source_url': 'https://api.open-meteo.com/v1/forecast?latitude=51&longitude=0',
    }


def _weather_category(context):
    return next(row for row in context['categories'] if row['category'] == 'weather')


def test_weather_collected_once_for_all_eligible_fixtures_and_frozen_for_late_review(
    tmp_path, monkeypatch,
):
    data, collections, synthetic_calls = _environment(tmp_path, monkeypatch)
    first, second, unused = data['fixtures']
    first_id, second_id = first['fixture_id'], second['fixture_id']
    initial, follow_up = _candidate(data, first), _candidate(data, second)
    packets = [initial, {**deepcopy(initial), 'candidate_id': 'initial-second-quote'}]
    eligible = [follow_up, {**deepcopy(follow_up), 'candidate_id': 'late-second-quote'}]
    fixtures = [first, second, deepcopy(second), unused]
    before = deepcopy((fixtures, packets, eligible))
    clock, calls, returned = Clock(), [], {}

    class Provider:
        def forecast(self, fixture):
            assert len(collections) == 1
            calls.append(fixture['fixture_id'])
            clock.advance(2)
            returned[fixture['fixture_id']] = _forecast(fixture, clock())
            return deepcopy(returned[fixture['fixture_id']])

    prepared = job.prepare(tmp_path, fixtures, packets, run_id='weather-all-candidates',
        eligible_candidates=eligible, weather_provider=Provider(), clock=clock)

    assert calls == [first_id, second_id]
    assert len(collections) == 1 and not synthetic_calls
    assert prepared['weather_by_fixture'] == returned
    assert prepared['as_of'] == clock().isoformat()
    assert prepared['frozen_weather'] == {
        'version': 'candidate-context-weather-v1', 'run_id': 'weather-all-candidates',
        'as_of': prepared['as_of'], 'fixture_ids': sorted(returned),
        'attempted_fixture_ids': sorted(returned), 'skipped_fixture_ids': [],
        'forecasts_sha256': canonical_sha256(returned),
    }
    scope = {row['fixture_id']: row for row in prepared['scope_packets']}
    assert scope[second_id]['weather_forecast'] == returned[second_id]
    for fid in (first_id, second_id):
        category = _weather_category(prepared['contexts'][fid])
        assert category['status'] == 'SOURCED'
        assert category['data'] == returned[fid]
    frozen = deepcopy(prepared)
    attached = job.attach([follow_up], prepared)[0]
    late_weather = _weather_category(attached['decision_context'])
    assert late_weather['data'] == returned[second_id]
    assert late_weather['evidence_id'] in attached['evidence_ids']
    reconstructed = job.compose(tmp_path, fixtures, prepared['scope_packets'],
        prepared['collection'], as_of=prepared['as_of'])
    assert reconstructed['contexts'] == prepared['contexts']
    assert prepared == frozen and calls == [first_id, second_id]
    assert (fixtures, packets, eligible) == before


def test_weather_receipts_follow_sports_players_and_calendar_before_final_cutoff(
    tmp_path, monkeypatch,
):
    from goal_analysis.jobs import (
        calendar_collection_shadow,
        expert_advisory,
        player_collection_shadow,
    )

    data, _collections, _synthetic = _environment(tmp_path, monkeypatch)
    clock, stages = Clock(), []
    original_sports = expert_advisory.enrich_sports_supplement

    def sports(*args, **kwargs):
        stages.append('sports')
        clock.advance()
        return original_sports(*args, **kwargs)

    monkeypatch.setattr(expert_advisory, 'enrich_sports_supplement', sports)
    for name, module in (('players', player_collection_shadow), ('calendar', calendar_collection_shadow)):
        monkeypatch.setattr(module, 'enabled', lambda _root: True)

        def collect(*_args, stage=name, **_kwargs):
            stages.append(stage)
            clock.advance()
            return {'status': 'TEST_COLLECTION_COMPLETE'}

        monkeypatch.setattr(module, 'collect', collect)

    class Provider:
        def forecast(self, fixture):
            assert stages == ['sports', 'players', 'calendar']
            stages.append('weather')
            clock.advance()
            return _forecast(fixture, clock())

    fixture = data['fixtures'][0]
    prepared = job.prepare(tmp_path, data['fixtures'], [_candidate(data, fixture)],
        run_id='weather-receipt-order', weather_provider=Provider(), clock=clock)
    assert stages == ['sports', 'players', 'calendar', 'weather']
    assert prepared['started_at'] == NOW.isoformat()
    assert prepared['as_of'] == (NOW + timedelta(seconds=4)).isoformat()
    assert prepared['weather_by_fixture'][fixture['fixture_id']]['observed_at'] == prepared['as_of']
    assert _weather_category(prepared['contexts'][fixture['fixture_id']])['status'] == 'SOURCED'


def test_scope_rechecks_lockout_before_each_weather_lookup(tmp_path, monkeypatch):
    data, _collections, _synthetic = _environment(tmp_path, monkeypatch)
    first, second, _unused = data['fixtures']
    second['kickoff'] = (NOW + timedelta(minutes=15, seconds=1)).isoformat()
    first_id, second_id = first['fixture_id'], second['fixture_id']
    clock, calls = Clock(), []

    class Provider:
        def forecast(self, fixture):
            calls.append(fixture['fixture_id'])
            clock.advance(2)
            return _forecast(fixture, clock())

    prepared = job.prepare(tmp_path, data['fixtures'], [_candidate(data, first)],
        run_id='weather-lockout', eligible_candidates=[_candidate(data, second)],
        weather_provider=Provider(), clock=clock)
    assert calls == [first_id]
    assert second_id in {row['fixture_id'] for row in prepared['scope_packets']}
    assert second_id not in prepared['weather_by_fixture']
    assert prepared['frozen_weather']['attempted_fixture_ids'] == [first_id]
    assert prepared['frozen_weather']['skipped_fixture_ids'] == [second_id]
    assert _weather_category(prepared['contexts'][second_id])['status'] == 'UNKNOWN'


def test_weather_failure_keeps_real_attempt_receipt_and_other_candidate_context(tmp_path, monkeypatch):
    data, _collections, _synthetic = _environment(tmp_path, monkeypatch)
    first, second, _unused = data['fixtures']
    first_id, second_id = first['fixture_id'], second['fixture_id']
    clock, calls, attempts = Clock(), [], {}

    class Provider:
        def forecast(self, fixture):
            fid = fixture['fixture_id']
            calls.append(fid)
            attempts[fid] = clock()
            clock.advance(3)
            if fid == second_id:
                raise TimeoutError('private-provider-query-key')
            return _forecast(fixture, clock())

    prepared = job.prepare(tmp_path, data['fixtures'], [_candidate(data, first)],
        run_id='weather-failure', eligible_candidates=[_candidate(data, second)],
        weather_provider=Provider(), clock=clock)
    assert calls == [first_id, second_id]
    failed = prepared['weather_by_fixture'][second_id]
    assert failed['status'] == 'UNKNOWN' and failed['weather_kind'] == 'FORECAST'
    assert failed['time_basis'] == 'LOOKUP_ATTEMPT_AT'
    assert failed['observed_at'] == attempts[second_id].isoformat()
    assert failed['observed_at'] != prepared['as_of']
    assert 'private-provider-query-key' not in json.dumps(prepared)
    assert _weather_category(prepared['contexts'][first_id])['status'] == 'SOURCED'
    assert _weather_category(prepared['contexts'][second_id])['status'] == 'UNKNOWN'
    assert prepared['frozen_weather']['attempted_fixture_ids'] == sorted(calls)


def test_existing_initial_forecast_is_reused_unchanged_and_included_in_scope_hash(tmp_path, monkeypatch):
    data, _collections, _synthetic = _environment(tmp_path, monkeypatch)
    first, second, _unused = data['fixtures']
    first_id, second_id = first['fixture_id'], second['fixture_id']
    old = _forecast(first, NOW - timedelta(minutes=2))
    initial = {**_candidate(data, first), 'weather_forecast': deepcopy(old)}
    calls = []

    class Provider:
        def forecast(self, fixture):
            calls.append(fixture['fixture_id'])
            return _forecast(fixture, NOW)

    prepared = job.prepare(tmp_path, data['fixtures'], [initial], run_id='weather-initial-reuse',
        eligible_candidates=[_candidate(data, second)], weather_provider=Provider(), clock=lambda: NOW)
    assert calls == [second_id]
    assert prepared['weather_by_fixture'][first_id] == old
    assert prepared['frozen_weather']['fixture_ids'] == sorted([first_id, second_id])
    assert prepared['frozen_weather']['attempted_fixture_ids'] == [second_id]
    assert prepared['frozen_weather']['forecasts_sha256'] == canonical_sha256(prepared['weather_by_fixture'])
    assert initial['weather_forecast'] == old


def test_no_weather_provider_keeps_previous_preparation_contract(tmp_path, monkeypatch):
    data, _collections, _synthetic = _environment(tmp_path, monkeypatch)
    first, second, _unused = data['fixtures']
    old = _forecast(first, NOW - timedelta(minutes=2))
    initial = {**_candidate(data, first), 'weather_forecast': old}
    prepared = job.prepare(tmp_path, data['fixtures'], [initial], run_id='weather-no-provider',
        eligible_candidates=[_candidate(data, second)], clock=lambda: NOW)
    assert not prepared.get('frozen_weather')
    assert _weather_category(prepared['contexts'][first['fixture_id']])['data'] == old
    assert _weather_category(prepared['contexts'][second['fixture_id']])['status'] == 'UNKNOWN'


@pytest.mark.parametrize('failure', [False, True])
def test_previous_venue_stage_runs_once_after_forecasts_before_final_cutoff(
    tmp_path, monkeypatch, failure,
):
    data, _collections, _synthetic = _environment(tmp_path, monkeypatch)
    first, second, _unused = data['fixtures']
    clock, stages = Clock(), []

    class Provider:
        def forecast(self, fixture):
            stages.append(('forecast', fixture['fixture_id']))
            clock.advance()
            return _forecast(fixture, clock())

        def collect_previous_venues(self, fixtures):
            assert stages == [('forecast', first['fixture_id']), ('forecast', second['fixture_id'])]
            assert [f['fixture_id'] for f in fixtures] == [first['fixture_id'], second['fixture_id']]
            stages.append(('previous_venues', None))
            clock.advance(7)
            if failure:
                raise OSError('private-source-error')
            return {'status': 'PREVIOUS_VENUE_SOURCES_ATTEMPTED', 'requests': 1,
                    'actual_itinerary_claimed': False}

    prepared = job.prepare(tmp_path, data['fixtures'], [_candidate(data, first)],
        run_id='weather-previous-venue-stage', eligible_candidates=[_candidate(data, second)],
        weather_provider=Provider(), clock=clock)
    assert stages[-1] == ('previous_venues', None) and len(stages) == 3
    assert prepared['as_of'] == (NOW + timedelta(seconds=9)).isoformat()
    assert prepared['frozen_weather']['as_of'] == prepared['as_of']
    assert prepared['weather_by_fixture'][second['fixture_id']]['observed_at'] == (
        NOW + timedelta(seconds=2)).isoformat()
    assert all(_weather_category(context)['status'] == 'SOURCED'
               for context in prepared['contexts'].values())
    if failure:
        assert prepared['previous_venue_collection'] == {
            'status': 'UNAVAILABLE', 'failure_class': 'OSError'}
        assert 'private-source-error' not in json.dumps(prepared)
    else:
        assert prepared['previous_venue_collection']['requests'] == 1
        assert prepared['previous_venue_collection']['actual_itinerary_claimed'] is False
