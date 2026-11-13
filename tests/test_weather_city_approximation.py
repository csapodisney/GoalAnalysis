"""New city fallback with fake transport/recorders; no network or SQLite."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from goal_analysis.jobs import sports_context_archive
from goal_analysis.jobs.agent_snapshot import FAMILIES, build_normal_snapshot
from goal_analysis.providers.open_meteo import weather_context

NOW = datetime(2026, 11, 13, 16, tzinfo=UTC)


def _fixture(number=1, city='Berlin', country='Germany'):
    return {'fixture_id': f'the_stats_api:mt_{number}', 'provider_fixture_id': f'mt_{number}',
        'source_provider': 'the_stats_api', 'provider_league_id': 'comp_1',
        'provider_season_id': 'sn_1', 'provider_home_team_id': 'tm_1',
        'provider_away_team_id': 'tm_2', 'venue_name': 'Collected venue',
        'venue_city': city, 'venue_country': country,
        'kickoff': (NOW + timedelta(hours=6)).isoformat(),
        'fixture_source': {'provider': 'the_stats_api', 'content_hash': 'a' * 64,
            'source_url': 'https://api.thestatsapi.com/api/football/matches?date=2026-11-13',
            'observed_at': (NOW - timedelta(minutes=1)).isoformat()}}


def _raw():
    return {'hourly': {'time': ['2026-11-13T22:00'], 'temperature_2m': [8.5],
        'precipitation_probability': [0], 'precipitation': [0.0], 'weather_code': [1],
        'wind_speed_10m': [6], 'wind_gusts_10m': [12], 'relative_humidity_2m': [70]}}


def _environment(tmp_path, monkeypatch, *, geocode=None, location=None, venue_gap=None):
    from goal_analysis.jobs import stadium_locations_shadow, the_stats_context, the_stats_venue
    from goal_analysis.providers import the_stats_primary
    from goal_analysis.storage import weather_availability

    seen = {'urls': [], 'archive': [], 'completion': [], 'venue': [], 'limits': [], 'monotonic': 1000.0}
    time = [NOW]

    class Store:
        def __init__(self, _database):
            pass

        def initialize(self):
            seen['archive'].append('initialize')

        def ingest(self, weather, fixture, **kwargs):
            seen['archive'].append(deepcopy((weather, fixture, kwargs)))
            return {'inserted': True, 'snapshot_id': 'b' * 64}

    class Completion:
        def __init__(self, *args, **kwargs):
            pass

        def check_held(self):
            pass

        def transport(self, underlying):
            return underlying

        def record(self, archive, weather, fixture, context, **kwargs):
            seen['completion'].append(deepcopy((weather, fixture, context)))
            return archive.ingest(weather, fixture, **kwargs), {'status': 'TEST_ONLY_STRICT_PATH', 'created': True}

    def resources(_root, _client, _clock, limit):
        seen['limits'].append(limit)
        return SimpleNamespace(usage={'requests': 0})

    def venue(_resources, fixture, *, refresh_existing):
        seen['venue'].append(refresh_existing)
        return deepcopy(fixture), {'status': venue_gap or 'VERIFIED'}

    def transport(url):
        seen['urls'].append(url)
        seen['monotonic'] += seen.get('request_elapsed', 0)
        if 'geocoding-api' in url:
            city = parse_qs(urlsplit(url).query)['name'][0]
            return geocode(city) if geocode else {'results': [
                {'name': city, 'country': 'Germany', 'latitude': 52.52, 'longitude': 13.4}]}
        return _raw()

    monkeypatch.setattr(sports_context_archive, 'WeatherStore', Store)
    monkeypatch.setattr(weather_availability, 'WeatherCompletion', Completion)
    monkeypatch.setattr(stadium_locations_shadow, 'fixture_location', lambda *a, **k: deepcopy(location))
    monkeypatch.setattr(the_stats_context, 'Resources', resources)
    monkeypatch.setattr(the_stats_primary, 'TheStatsPrimaryClient', lambda *a, **k: object())
    monkeypatch.setattr(the_stats_venue, 'resolve_venue', venue)
    provider = sports_context_archive.make_weather_provider(tmp_path, 'TESTONLY-city',
        clock=lambda: time[0], transport=transport, root_run_lock=object(),
        monotonic_clock=lambda: seen['monotonic'])
    return provider, seen, time


def _snapshot(weather, fixtures, cutoff):
    events = [{'fixture_id': f['fixture_id'], 'kickoff': f['kickoff']} for f in fixtures]
    return build_normal_snapshot(run_id='TESTONLY-city-run', target_date='2026-11-13',
        snapshot_as_of=cutoff.isoformat(), fixture_records=fixtures, weather_by_fixture=weather,
        artifacts={'calendar': {'scope_receipt': {'scope': 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY',
            'date': '2026-11-13', 'status': 'COMPLETE', 'provider_calendar_status': 'COMPLETE',
            'routing_status': 'COMPLETE', 'provider_fixture_ids': [f['fixture_id'] for f in fixtures],
            'events': events, 'page_issues': [], 'rejected_rows': []}}}).data()


def test_city_forecast_reuses_cache_and_is_visible_without_strict_receipt(tmp_path, monkeypatch):
    provider, seen, time = _environment(tmp_path, monkeypatch, venue_gap='REQUEST_BUDGET')
    fixture = _fixture(country=None)
    first = provider.forecast_for_candidate_context(fixture)
    time[0] += timedelta(minutes=4)
    seen['monotonic'] += 120
    second = provider.forecast_for_candidate_context(_fixture(2, country=None))
    assert first['status'] == second['status'] == 'SOURCED'
    assert first['location_precision'] == second['location_precision'] == 'CITY_APPROXIMATION'
    assert first['observed_at'] == second['observed_at'] == NOW.isoformat()
    assert first['raw_payload_hash'] == second['raw_payload_hash']
    assert first['provider_issue_time'] is None and first['precipitation_probability_pct'] == 0
    assert first['availability_status'] == {'status': 'NO_STRICT_WEATHER_RECEIPT', 'created': False}
    assert first['archive_status']['status'] == 'REPORT_ONLY_CITY_APPROXIMATION'
    assert not first['strict_stadium_forecast'] and first['probability_effect'] == 'NOT_ESTIMATED'
    assert seen['archive'] == seen['completion'] == []
    assert len(seen['urls']) == 2 and seen['limits'] == [40]
    assert first['venue_lookup']['status'] == 'REQUEST_BUDGET'
    assert first['city_source']['country_status'] == 'UNKNOWN'
    assert first['city_source']['venue_country'] is None
    assert first['city_source']['source_receipt'] == fixture['fixture_source']
    blocked = provider.forecast_for_candidate_context(_fixture(3, city='Other'))
    assert blocked['status'] == 'UNKNOWN'
    assert blocked['city_approximation_reason'] == 'CITY_TIME_BUDGET_EXHAUSTED'
    assert len(seen['urls']) == 2
    context = weather_context(first)
    assert context['status'] == 'SOURCED' and context['location_precision'] == 'CITY_APPROXIMATION'
    assert context['availability_status'] == first['availability_status']
    snapshot = _snapshot({fixture['fixture_id']: first}, [fixture, _fixture(2)], time[0])
    family = snapshot['families']['weather']
    assert family['status'] == 'AVAILABLE'
    assert family['value']['by_fixture'][fixture['fixture_id']] == first
    assert len(snapshot['fixture_ids']) == 2 and len(snapshot['owner_ids']) == 10
    assert set(snapshot['families']) == set(FAMILIES) and snapshot['model_probabilities'] == {}


def test_exact_stadium_retains_priority_and_original_archive_path(tmp_path, monkeypatch):
    location = {'record_id': 'TESTONLY-exact', 'payload': {'native_name': 'Exact venue',
        'native_city': 'Berlin', 'latitude': 51.0, 'longitude': 12.0}}
    provider, seen, _time = _environment(tmp_path, monkeypatch, location=location)
    weather = provider.forecast_for_candidate_context(_fixture())
    assert weather['location_basis'] == 'EXACT_MATCH_AND_REVIEWED_STADIUM_WGS84'
    assert weather['location_record_id'] == 'TESTONLY-exact'
    assert 'location_precision' not in weather and 'city_source' not in weather
    assert len(seen['urls']) == 1 and 'geocoding-api' not in seen['urls'][0]
    assert parse_qs(urlsplit(seen['urls'][0]).query)['latitude'] == ['51.0']
    assert len(seen['completion']) == 1 and seen['archive'][0] == 'initialize'
    assert seen['venue'] == [] and seen['limits'] == []


def test_ambiguous_city_is_local_unknown_and_other_city_can_continue(tmp_path, monkeypatch):
    def geocode(city):
        matches = [{'name': city, 'country': 'Germany', 'latitude': 52, 'longitude': 13}]
        return {'results': matches * (2 if city == 'Ambiguous' else 1)}

    provider, seen, _time = _environment(tmp_path, monkeypatch, geocode=geocode)
    unknown = provider.forecast_for_candidate_context(_fixture(city='Ambiguous'))
    valid = provider.forecast_for_candidate_context(_fixture(2))
    assert unknown['status'] == 'UNKNOWN' and 'temperature_c' not in unknown
    assert unknown['city_approximation_reason'] == 'CITY_FORECAST_UNAVAILABLE'
    assert valid['status'] == 'SOURCED' and valid['temperature_c'] == 8.5
    assert len(seen['urls']) == 3 and not seen['archive'] and not seen['completion']


def test_missing_or_invalid_city_provenance_never_uses_unsourced_text(tmp_path, monkeypatch):
    provider, seen, _time = _environment(tmp_path, monkeypatch)
    missing = _fixture(city=None)
    unsourced = _fixture(2); unsourced.pop('fixture_source')
    future = _fixture(3); future['fixture_source']['observed_at'] = (NOW + timedelta(seconds=1)).isoformat()
    foreign = _fixture(4); foreign['_venue_source'] = {**foreign['fixture_source'],
        'source_url': 'https://api.thestatsapi.com/api/football/matches/mt_999'}
    invalid_country = _fixture(5, country=False)
    for fixture in (missing, unsourced, future, foreign, invalid_country):
        result = provider.forecast_for_candidate_context(fixture)
        assert result['status'] == 'UNKNOWN' and 'temperature_c' not in result
        assert result['location_basis'] == 'UNVERIFIED_STADIUM'
        assert result['city_approximation_reason']
    assert seen['urls'] == []
    seen['monotonic'] = False
    bad_clock = provider.forecast_for_candidate_context(_fixture(6))
    assert bad_clock['status'] == 'UNKNOWN'
    assert bad_clock['city_approximation_reason'] == 'CITY_MONOTONIC_CLOCK_INVALID'
    assert seen['urls'] == []


def test_city_deadline_checks_each_transport_but_does_not_limit_exact_stadium(tmp_path, monkeypatch):
    from goal_analysis.jobs import stadium_locations_shadow

    provider, seen, _time = _environment(tmp_path, monkeypatch)
    seen['request_elapsed'] = 121
    blocked = provider.forecast_for_candidate_context(_fixture())
    assert blocked['status'] == 'UNKNOWN' and 'temperature_c' not in blocked
    assert blocked['city_approximation_reason'] == 'CITY_TIME_BUDGET_EXHAUSTED'
    assert len(seen['urls']) == 1 and 'geocoding-api' in seen['urls'][0]
    assert not seen['archive'] and not seen['completion']
    location = {'record_id': 'TESTONLY-exact', 'payload': {'native_name': 'Exact venue',
        'native_city': 'Berlin', 'latitude': 52.52, 'longitude': 13.4}}
    monkeypatch.setattr(stadium_locations_shadow, 'fixture_location', lambda *a, **k: deepcopy(location))
    exact = provider.forecast_for_candidate_context(_fixture(2))
    assert exact['status'] == 'SOURCED'
    assert exact['location_basis'] == 'EXACT_MATCH_AND_REVIEWED_STADIUM_WGS84'
    assert len(seen['urls']) == 2 and len(seen['completion']) == 1


def test_city_transport_failure_is_local_and_does_not_leak_error_or_retry(tmp_path, monkeypatch):
    def geocode(city):
        if city == 'Failed':
            raise TimeoutError('private-provider-detail')
        return {'results': [{'name': city, 'country': 'Germany', 'latitude': 52, 'longitude': 13}]}

    provider, seen, _time = _environment(tmp_path, monkeypatch, geocode=geocode)
    failed = provider.forecast_for_candidate_context(_fixture(city='Failed'))
    valid = provider.forecast_for_candidate_context(_fixture(2))
    assert failed['status'] == 'UNKNOWN' and 'private-provider-detail' not in str(failed)
    assert failed['availability_status']['status'] == 'NO_STRICT_WEATHER_RECEIPT'
    assert valid['status'] == 'SOURCED' and len(seen['urls']) == 3
    assert not seen['archive'] and not seen['completion']
