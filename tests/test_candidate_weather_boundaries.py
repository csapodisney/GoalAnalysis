"""ACTIVE weather isolation preserves controls and never guesses stadiums."""
import json
from copy import deepcopy
from datetime import UTC, datetime
from urllib.error import HTTPError

import pytest
from test_open_meteo import fixture

from goal_analysis.jobs import candidate_context_review as job
from goal_analysis.providers.open_meteo import OpenMeteoForecast

NOW = datetime(2026, 9, 25, 10, tzinfo=UTC)


def payload():
    return {'hourly': {'time': ['2026-09-25T19:00'], 'temperature_2m': [14.2],
        'precipitation_probability': [60], 'precipitation': [.5], 'weather_code': [61],
        'wind_speed_10m': [18], 'wind_gusts_10m': [30]}}


@pytest.mark.parametrize('error', [TimeoutError('timeout'), ValueError('malformed response'),
                                 HTTPError('https://api.open-meteo.com', 503, 'unavailable', {}, None)])
def test_active_single_failed_query_does_not_disable_other_fixture_weather(error):
    calls = []

    def transport(url):
        calls.append(url)
        if len(calls) == 1:
            raise error
        return payload()

    provider = OpenMeteoForecast(transport, clock=lambda: NOW, isolate_request_failures=True)
    with pytest.raises(type(error)):
        provider.forecast(fixture(), coordinates=(52.52, 13.4))
    with pytest.raises(ValueError):
        provider.forecast(fixture(), coordinates=(52.52, 13.4))
    result = provider.forecast(fixture('Munich'), coordinates=(48.7, 11.4))
    assert result['status'] == 'SOURCED' and result['observed_at'] == NOW.isoformat()
    assert len(calls) == 2 and calls[0] != calls[1]


@pytest.mark.parametrize('status', [401, 403, 429])
def test_active_quota_or_access_failure_stops_host_without_retry(status):
    calls = []

    def transport(url):
        calls.append(url)
        raise HTTPError(url, status, 'provider rejection', {}, None)

    provider = OpenMeteoForecast(transport, isolate_request_failures=True)
    with pytest.raises(HTTPError):
        provider.forecast(fixture(), coordinates=(52.52, 13.4))
    with pytest.raises(ValueError):
        provider.forecast(fixture('Munich'), coordinates=(48.7, 11.4))
    assert len(calls) == 1


def native_fixture(city=None):
    return {**fixture(city), 'fixture_id': 'the_stats_api:mt_101',
        'source_provider': 'the_stats_api', 'provider_fixture_id': 'mt_101',
        'provider_league_id': 'comp_100', 'provider_season_id': 'sn_100',
        'provider_home_team_id': 'tm_101', 'provider_away_team_id': 'tm_102'}


def no_stadium(root, monkeypatch):
    from goal_analysis.jobs import stadium_locations_shadow, the_stats_context
    from goal_analysis.providers import the_stats_primary

    limits, calls = [], []
    monkeypatch.setattr(stadium_locations_shadow, 'fixture_location', lambda *a, **k: None)
    monkeypatch.setattr(the_stats_primary, 'TheStatsPrimaryClient',
        lambda root, max_requests, clock: limits.append(max_requests))

    class Resources:
        def __init__(self, root, client, clock, maximum):
            self.clock = clock
            self.usage = {'requests': 0}

        def get(self, endpoint):
            calls.append(endpoint)
            self.usage['requests'] += 1
            return None, None, 'NO_VERIFIED_DETAIL'

    monkeypatch.setattr(the_stats_context, 'Resources', Resources)
    return limits, calls


@pytest.mark.parametrize('active,limit', [(False, 12), (True, 40)])
def test_control_and_active_stadium_lookups_have_distinct_shared_caps(tmp_path, monkeypatch, active, limit):
    from goal_analysis.jobs.sports_context_archive import make_weather_provider

    limits, calls = no_stadium(tmp_path, monkeypatch)
    provider = make_weather_provider(tmp_path, 'bounded-weather', clock=lambda: NOW,
        transport=lambda url: pytest.fail('Unverified stadium must not reach weather HTTP'))
    lookup = provider.forecast_for_candidate_context if active else provider.forecast
    first, second = native_fixture(), {**native_fixture(), 'fixture_id': 'the_stats_api:mt_102',
                                      'provider_fixture_id': 'mt_102'}
    for value in (first, second):
        assert lookup(value)['status'] == 'UNKNOWN'
    assert limits == [limit] and len(calls) == 2
    assert provider.isolate_request_failures is active


def test_active_city_name_cannot_bypass_native_stadium_refresh_or_imply_coordinates(tmp_path, monkeypatch):
    from goal_analysis.jobs.sports_context_archive import make_weather_provider

    limits, calls = no_stadium(tmp_path, monkeypatch)
    provider = make_weather_provider(tmp_path, 'strict-existing-city', clock=lambda: NOW,
        transport=lambda url: pytest.fail('A city name is not reviewed stadium coordinates'))
    value = native_fixture('Berlin'); before = deepcopy(value)
    result = provider.forecast_for_candidate_context(value)
    assert calls == ['/football/matches/mt_101'] and limits == [40]
    assert result['status'] == 'UNKNOWN' and result['location_basis'] == 'UNVERIFIED_STADIUM'
    assert result['archive_status']['status'] == 'ARCHIVED'
    assert result['observed_at'] == NOW.isoformat() and result['time_basis'] == 'LOOKUP_ATTEMPT_AT'
    assert value == before and 'latitude' not in result and 'longitude' not in result


def test_scope_uses_explicit_strict_weather_method_when_available():
    value = native_fixture('Berlin')
    calls = []

    class Provider:
        def forecast(self, fixture):
            pytest.fail('Candidate scope must use strict stadium mode')

        def forecast_for_candidate_context(self, fixture):
            calls.append(fixture['fixture_id'])
            return {'fixture_id': fixture['fixture_id'], 'status': 'UNKNOWN',
                    'observed_at': NOW.isoformat()}

    forecasts, attempted, skipped = job._forecast_scope([value], [{'fixture_id': value['fixture_id']}],
        Provider(), lambda: NOW)
    assert calls == attempted == [value['fixture_id']] and not skipped
    assert forecasts[value['fixture_id']]['status'] == 'UNKNOWN'


@pytest.mark.parametrize('mode', ['OFF', 'SHADOW', 'ACTIVE'])
def test_coordinator_enables_new_weather_scope_only_in_active_mode(tmp_path, monkeypatch, mode):
    from test_daily_223_live import NOW as FLOW_NOW
    from test_portfolio_flow import RecordedReviewer, environment

    from goal_analysis.jobs.portfolio import run_portfolio
    from goal_analysis.providers.open_meteo import unknown
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger

    cfg, football, odds, cache, _calls, _state, _db = environment(tmp_path)
    ledger = PortfolioLedger(tmp_path/'data/arthur/ledger.sqlite3', now=lambda: FLOW_NOW)
    folder = tmp_path/'config'; folder.mkdir(exist_ok=True)
    (folder/'candidate-context-review.json').write_text(json.dumps({'version': job.VERSION, 'mode': mode}))
    received, weather_calls = [], []

    class Provider:
        def forecast(self, fixture):
            weather_calls.append(fixture['fixture_id'])
            return unknown(fixture)

    provider = Provider()

    def prepare(root, fixtures, requested, **kwargs):
        received.append(kwargs['weather_provider'])
        return {'contexts': {}, 'as_of': FLOW_NOW.isoformat(), 'collection': {'usage': {'requests': 0}}}

    monkeypatch.setattr(job, 'prepare', prepare)
    run_portfolio(cfg, {}, football, odds, cache, FLOW_NOW.date(), RecordedReviewer(), ledger,
        weather_provider=provider, clock=lambda: FLOW_NOW)
    assert received == ([] if mode == 'OFF' else [provider if mode == 'ACTIVE' else None])
    assert bool(weather_calls) is (mode != 'ACTIVE')
