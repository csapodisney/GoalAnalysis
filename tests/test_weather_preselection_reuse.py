"""Frozen weather and witnessed previous venues share bounded acquisitions."""

import json
from copy import deepcopy
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from test_expert_advisory import NOW

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import expert_advisory


def _report(*, age_minutes=12):
    observed = NOW - timedelta(minutes=age_minutes)
    forecast = {'fixture_id': 'api_football:1', 'status': 'UNKNOWN',
        'weather_kind': 'FORECAST', 'time_basis': 'LOOKUP_ATTEMPT_AT',
        'observed_at': observed.isoformat(), 'reason': 'PROVIDER_UNAVAILABLE'}
    fixture = {'fixture_id': 'api_football:1',
        'kickoff': (NOW + timedelta(hours=8)).isoformat(), 'weather': forecast}
    manifest = {'version': 'candidate-context-weather-v1', 'run_id': 'frozen-weather-run',
        'as_of': (observed + timedelta(seconds=1)).isoformat(),
        'fixture_ids': [fixture['fixture_id']], 'attempted_fixture_ids': [fixture['fixture_id']],
        'skipped_fixture_ids': [], 'forecasts_sha256': canonical_sha256({fixture['fixture_id']: forecast})}
    return {'date': NOW.date().isoformat(), 'run_id': manifest['run_id'], 'fixtures': [fixture],
        'recommendations': [{'profile_id': 'daily223', 'legs': [{'fixture_id': fixture['fixture_id']}]}],
        'candidate_context_weather': manifest}


class Provider:
    def __init__(self):
        self.calls = []

    def forecast(self, fixture):
        self.calls.append(fixture['fixture_id'])
        return {'fixture_id': fixture['fixture_id'], 'status': 'UNKNOWN',
            'weather_kind': 'FORECAST', 'observed_at': NOW.isoformat(),
            'time_basis': 'LOOKUP_ATTEMPT_AT', 'reason': 'TEST_NEW_ATTEMPT'}


def _analyze(root, report, provider):
    return expert_advisory.analyze_report(root, report, weather_provider=provider, clock=lambda: NOW,
        dataset={'matches': [], 'coverage': {}, 'data_hash': 'empty'}, persist=False)


@pytest.mark.parametrize('age_minutes', [12, 45, 100])
def test_failed_frozen_preselection_not_retried_after_long_model_review(tmp_path, age_minutes):
    report, provider = _report(age_minutes=age_minutes), Provider()
    before = deepcopy(report)
    result = _analyze(tmp_path, report, provider)
    assert provider.calls == [] and result['additional_weather_lookups'] == 0
    forecast = result['weather']['fixtures'][0]['forecast']
    assert forecast == report['fixtures'][0]['weather']
    assert not result['weather']['fixtures'][0]['live_weight_eligible']
    assert result['additional_llm_requests'] == 0 and report == before


@pytest.mark.parametrize('fault', [
    'missing_manifest', 'different_run', 'different_version', 'wrong_hash',
    'future_cutoff', 'cutoff_before_receipt', 'duplicate_fixture_id',
    'duplicate_attempted_id', 'attempted_outside_scope', 'missing_scoped_forecast',
    'altered_forecast', 'invalid_receipt',
])
def test_invalid_weather_manifest_cannot_suppress_legitimate_lookup(tmp_path, fault):
    report, provider = _report(), Provider()
    manifest = report['candidate_context_weather']
    if fault == 'missing_manifest':
        report.pop('candidate_context_weather')
    elif fault == 'different_run':
        manifest['run_id'] = 'other-run'
    elif fault == 'different_version':
        manifest['version'] = 'unsupported-version'
    elif fault == 'wrong_hash':
        manifest['forecasts_sha256'] = '0' * 64
    elif fault == 'future_cutoff':
        manifest['as_of'] = (NOW + timedelta(seconds=1)).isoformat()
    elif fault == 'cutoff_before_receipt':
        manifest['as_of'] = (NOW - timedelta(hours=1)).isoformat()
    elif fault == 'duplicate_fixture_id':
        manifest['fixture_ids'] *= 2
    elif fault == 'duplicate_attempted_id':
        manifest['attempted_fixture_ids'] *= 2
    elif fault == 'attempted_outside_scope':
        manifest['attempted_fixture_ids'].append('api_football:999')
    elif fault == 'missing_scoped_forecast':
        manifest['fixture_ids'].append('api_football:999')
    elif fault == 'altered_forecast':
        report['fixtures'][0]['weather']['reason'] = 'DIFFERENT_FROM_BOUND_FORECAST'
    else:
        report['fixtures'][0]['weather']['observed_at'] = 'not-a-time'
        manifest['forecasts_sha256'] = canonical_sha256({
            fixture['fixture_id']: fixture['weather'] for fixture in report['fixtures']})
    before = deepcopy(report)
    result = _analyze(tmp_path, report, provider)
    assert provider.calls == ['api_football:1'] and result['additional_weather_lookups'] == 1
    assert result['weather']['fixtures'][0]['forecast']['reason'] == 'TEST_NEW_ATTEMPT'
    assert report == before


def test_full_scope_hash_includes_unselected_fixture_forecast(tmp_path):
    report, provider = _report(), Provider()
    other = deepcopy(report['fixtures'][0])
    other['fixture_id'] = other['weather']['fixture_id'] = 'api_football:2'
    report['fixtures'].append(other)
    manifest = report['candidate_context_weather']
    manifest['fixture_ids'].append(other['fixture_id'])
    manifest['attempted_fixture_ids'].append(other['fixture_id'])
    manifest['forecasts_sha256'] = canonical_sha256({
        fixture['fixture_id']: fixture['weather'] for fixture in report['fixtures']})
    assert _analyze(tmp_path, report, provider)['additional_weather_lookups'] == 0
    other['weather']['reason'] = 'CHANGED_UNSELECTED_FORECAST'
    result = _analyze(tmp_path, report, provider)
    assert result['additional_weather_lookups'] == 1 and provider.calls == ['api_football:1']


def test_local_witnessed_stadium_is_used_without_new_native_venue_request(tmp_path, monkeypatch):
    from test_stadium_locations_shadow import NOW as VENUE_NOW
    from test_stadium_locations_shadow import prepared

    from goal_analysis.jobs import sports_context_archive, the_stats_venue
    from goal_analysis.providers import the_stats_primary
    from goal_analysis.storage.sports_weather import WeatherStore

    data, _models, database, _receipts, _bundle = prepared(tmp_path, monkeypatch, http=True, pointer=True)
    fixture = deepcopy(data['fixtures'][0])
    fixture.pop('venue_city', None)
    fixture.pop('venue_name', None)
    before, calls = deepcopy(fixture), []

    def forbidden(*_args, **_kwargs):
        pytest.fail('A fresh witnessed local stadium must not spend a new TheStats request')

    monkeypatch.setattr(the_stats_primary, 'TheStatsPrimaryClient', forbidden)
    monkeypatch.setattr(the_stats_venue, 'resolve_venue', forbidden)

    def transport(url):
        assert 'geocoding-api' not in url
        calls.append(url)
        return {'hourly': {'time': [fixture['kickoff'][:16]], 'temperature_2m': [10],
            'precipitation_probability': [20], 'precipitation': [.1], 'weather_code': [3],
            'wind_speed_10m': [12], 'wind_gusts_10m': [20], 'relative_humidity_2m': [75]}}

    provider = sports_context_archive.make_weather_provider(tmp_path,
        source_run_id='weather-local-stadium', clock=lambda: VENUE_NOW, transport=transport)
    forecast = provider.forecast(fixture)
    assert forecast['status'] == 'SOURCED' and len(calls) == 1
    assert forecast['location_basis'] == 'EXACT_MATCH_AND_REVIEWED_STADIUM_WGS84'
    assert forecast['venue_city'] == 'Brest' and forecast['latitude'] == 48.40293
    assert forecast['archive_status']['status'] == 'ARCHIVED'
    assert not forecast.get('venue_lookup_requests')
    archived = WeatherStore(database).as_of(VENUE_NOW)
    assert len(archived) == 1 and archived[0]['raw_payload']['hourly']['temperature_2m'] == [10]
    assert fixture == before


def _previous_venue_environment(root, monkeypatch, *, known=False):
    from test_schedule_features import NOW as CALENDAR_NOW
    from test_schedule_features import calendars, match
    from test_schedule_shadow import setup as calendar_setup
    from test_stadium_locations_shadow import sources

    from goal_analysis.storage.temporal import TemporalStore

    database, raw, _temporal, fixture, _saved = calendar_setup(root)
    sources(root, monkeypatch, now=CALENDAR_NOW)
    matches = [match(), calendars()[0]['payload']['matches'][-1], calendars()[1]['payload']['matches'][-1]]
    venues = [('Stade Francis-Le Ble', 'Brest'), ('Stadio Olimpico Grande Torino', 'Turin'),
              ('MHP Arena', 'Stuttgart')]
    details = {}
    for row, (name, city) in zip(matches, venues, strict=True):
        detail = {'id': row['fixture_id'], 'competition_id': row['competition_id'],
            'season_id': row['season_id'], 'home_team': {'id': row['home_team_id']},
            'away_team': {'id': row['away_team_id']}, 'utc_date': row['kickoff'],
            'status': row['status'], 'is_neutral': False, 'venue': {'name': name, 'city': city}}
        details[row['fixture_id']] = detail
        if known or row['fixture_id'] == 'mt_100':
            raw.save('the_stats_api', 'detail', json.dumps({'data': detail}).encode(),
                'application/json', fetched_at=CALENDAR_NOW - timedelta(minutes=2),
                source_url='https://api.thestatsapi.com/api/football/matches/' + row['fixture_id'])
    TemporalStore(database, root / 'data/raw', clock=lambda: CALENDAR_NOW - timedelta(seconds=20)).capture(
        'raw', limit=100)
    return CALENDAR_NOW, fixture, details


def _previous_venue_client(monkeypatch, now, details, *, fault=None):
    from goal_analysis.providers import the_stats_primary
    from goal_analysis.providers.the_stats_api import TheStatsApiClient

    calls, clients = [], []

    def transport(url, _headers, **_kwargs):
        fid = urlsplit(url).path.rsplit('/', 1)[-1]
        calls.append(fid)
        body = deepcopy(details[fid])
        if fid == 'mt_1' and fault == 'wrong_home':
            body['home_team']['id'] = 'tm_999'
        elif fid == 'mt_1' and fault == 'wrong_status':
            body['status'] = 'scheduled'
        headers = {'X-RateLimit-Limit': '120', 'X-RateLimit-Remaining': '100',
            'X-RateLimit-Reset': str(int((now + timedelta(minutes=1)).timestamp())),
            'X-Monthly-Quota-Limit': '100000', 'X-Monthly-Quota-Remaining': '90000',
            'X-Monthly-Quota-Reset': str(int((now + timedelta(days=30)).timestamp()))}
        if fault == 'rate_limited':
            return {'error': {'code': 'RATE_LIMITED'}}, headers, 429
        return {'data': body}, headers, 200

    def factory(_root, **kwargs):
        clients.append(kwargs['max_requests'])
        return TheStatsApiClient('synthetic-previous-venue', transport=transport, **kwargs)

    monkeypatch.setattr(the_stats_primary, 'TheStatsPrimaryClient', factory)
    return calls, clients


def test_previous_venues_all_locally_verified_need_no_client_and_repeated_stage_is_free(tmp_path, monkeypatch):
    from goal_analysis.jobs.sports_context_archive import make_weather_provider
    from goal_analysis.providers import the_stats_primary

    now, fixture, _details = _previous_venue_environment(tmp_path, monkeypatch, known=True)

    def forbidden(*_args, **_kwargs):
        pytest.fail('All previous venues are already witnessed locally')

    monkeypatch.setattr(the_stats_primary, 'TheStatsPrimaryClient', forbidden)
    provider = make_weather_provider(tmp_path, 'local-previous-venues', clock=lambda: now)
    result = provider.collect_previous_venues([fixture])
    assert result['status'] == 'LOCAL_SOURCES_SUFFICIENT' and result['requests'] == 0
    assert result['previous_match_ids'] == ['mt_1', 'mt_2'] and result['locally_verified'] == 2
    assert provider.collect_previous_venues([fixture]) == {'status': 'ALREADY_ATTEMPTED', 'requests': 0}


@pytest.mark.parametrize('fault', [None, 'wrong_home', 'wrong_status'])
def test_previous_venue_raw_identity_is_checked_before_schedule_uses_distance(tmp_path, monkeypatch, fault):
    from goal_analysis.jobs.schedule_shadow import inspect_schedule
    from goal_analysis.jobs.sports_context_archive import make_weather_provider

    now, fixture, details = _previous_venue_environment(tmp_path, monkeypatch)
    calls, clients = _previous_venue_client(monkeypatch, now, details, fault=fault)
    before = deepcopy(fixture)
    provider = make_weather_provider(tmp_path, 'new-previous-venues', clock=lambda: now)
    result = provider.collect_previous_venues([fixture])
    assert calls == ['mt_1', 'mt_2'] and clients == [20]
    assert result['requests'] == 2 and result['max_requests'] == 20
    assert result['locally_verified_before'] == 0
    assert result['actual_itinerary_claimed'] is False
    schedule = inspect_schedule(tmp_path, [fixture], as_of=now)
    home, away = schedule['features'][0]['teams']
    assert away['previous_to_target_venue_great_circle_km'] > 900
    assert home['actual_travel_km'] is away['actual_travel_km'] is None
    if fault is None:
        assert result['verified_after'] == 2 and result['unresolved_fixture_ids'] == []
        assert home['previous_to_target_venue_great_circle_km'] > 900
    else:
        assert result['verified_after'] == 1 and result['unresolved_fixture_ids'] == ['mt_1']
        assert result['reader_issues']['MATCH_LOCATION_IDENTITY_OR_STATUS_CONFLICT'] == 1
        assert home['previous_to_target_venue_great_circle_km'] is None
    assert provider.collect_previous_venues([fixture])['requests'] == 0
    assert calls == ['mt_1', 'mt_2'] and clients == [20] and fixture == before


def test_previous_venue_provider_rate_limit_stops_once_without_second_collection(tmp_path, monkeypatch):
    from goal_analysis.jobs.sports_context_archive import make_weather_provider

    now, fixture, details = _previous_venue_environment(tmp_path, monkeypatch)
    calls, clients = _previous_venue_client(monkeypatch, now, details, fault='rate_limited')
    provider = make_weather_provider(tmp_path, 'limited-previous-venues', clock=lambda: now)
    result = provider.collect_previous_venues([fixture])
    assert calls == ['mt_1'] and clients == [20] and result['requests'] == 1
    assert result['attempted_fixture_ids'] == ['mt_1'] and len(result['failures']) == 1
    assert result['verified_after'] == 0 and result['unresolved_fixture_ids'] == ['mt_1', 'mt_2']
    assert provider.collect_previous_venues([fixture])['status'] == 'ALREADY_ATTEMPTED'
    assert calls == ['mt_1'] and clients == [20]


def test_previous_venue_deduplication_and_shared_twenty_request_bound(tmp_path, monkeypatch):
    from test_schedule_features import NOW as CALENDAR_NOW
    from test_schedule_features import match

    from goal_analysis.jobs import schedule_shadow, stadium_locations_shadow, the_stats_context
    from goal_analysis.jobs.sports_context_archive import make_weather_provider
    from goal_analysis.providers import the_stats_primary

    completed = [match(n=n, days=-2, status='finished') for n in range(1, 31)]
    ignored = [match(n=90, days=-2, status='scheduled'), match(n=91, days=-2, status='cancelled')]
    features = [{'teams': [{'previous_fixture': row} for row in completed + completed + ignored]}]
    fixture = {'fixture_id': 'the_stats_api:mt_100', 'source_provider': 'the_stats_api',
               'kickoff': match()['kickoff']}
    seen_matches, clients, resources, calls = [], [], [], []

    def locations(_root, matches, *, as_of):
        assert as_of == CALENDAR_NOW
        seen_matches.append(deepcopy(matches))
        return {'mt_1': {}, 'mt_2': {}}, {}

    monkeypatch.setattr(schedule_shadow, 'inspect_schedule', lambda *_a, **_k: {'features': features})
    monkeypatch.setattr(stadium_locations_shadow, 'match_locations', locations)

    def client(_root, **kwargs):
        clients.append(kwargs['max_requests'])
        return object()

    class Resources:
        def __init__(self, _root, _client, _clock, max_requests):
            resources.append(max_requests)
            self.usage = {'requests': 0, 'stopped_reason': None}

        def get(self, endpoint):
            calls.append(endpoint.rsplit('/', 1)[-1])
            self.usage['requests'] += 1
            return None, None, None

    monkeypatch.setattr(the_stats_primary, 'TheStatsPrimaryClient', client)
    monkeypatch.setattr(the_stats_context, 'Resources', Resources)
    provider = make_weather_provider(tmp_path, 'bounded-previous-venues', clock=lambda: CALENDAR_NOW)
    result = provider.collect_previous_venues([fixture])
    wanted = sorted({m['fixture_id'] for m in completed} - {'mt_1', 'mt_2'})[:20]
    assert calls == wanted and result['attempted_fixture_ids'] == wanted
    assert clients == resources == [20] and result['requests'] == 20
    assert result['previous_match_ids'] == sorted({m['fixture_id'] for m in completed})
    assert all(len(values) == 30 for values in seen_matches)
    assert 'mt_90' not in result['previous_match_ids'] and 'mt_91' not in result['previous_match_ids']
    assert provider.collect_previous_venues([fixture])['requests'] == 0
    assert calls == wanted and clients == resources == [20]


def test_conflicting_previous_venue_identity_is_not_silently_overwritten(tmp_path, monkeypatch):
    from test_schedule_features import NOW as CALENDAR_NOW
    from test_schedule_features import match

    from goal_analysis.jobs import schedule_shadow, stadium_locations_shadow
    from goal_analysis.jobs.sports_context_archive import make_weather_provider
    from goal_analysis.providers import the_stats_primary

    original = match(n=1, days=-2, status='finished')
    conflict = {**deepcopy(original), 'home_team_id': 'tm_999'}
    features = [{'teams': [{'previous_fixture': original}, {'previous_fixture': conflict}]}]
    fixture = {'fixture_id': 'the_stats_api:mt_100', 'source_provider': 'the_stats_api',
               'kickoff': match()['kickoff']}
    reader_inputs = []

    def locations(_root, matches, **_kwargs):
        reader_inputs.append(deepcopy(matches))
        return {}, {}

    def forbidden(*_args, **_kwargs):
        pytest.fail('Conflicting previous-match identities do not authorize a detail request')

    monkeypatch.setattr(schedule_shadow, 'inspect_schedule', lambda *_a, **_k: {'features': features})
    monkeypatch.setattr(stadium_locations_shadow, 'match_locations', locations)
    monkeypatch.setattr(the_stats_primary, 'TheStatsPrimaryClient', forbidden)
    provider = make_weather_provider(tmp_path, 'conflicting-previous-venues', clock=lambda: CALENDAR_NOW)
    result = provider.collect_previous_venues([fixture])
    assert result['requests'] == 0 and result['previous_match_ids'] == []
    assert result['conflicting_previous_fixture_ids'] == ['mt_1']
    assert reader_inputs == [[]]
