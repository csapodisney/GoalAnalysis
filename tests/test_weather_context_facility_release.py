"""Weather input consumption and local fallback; no real provider requests.

The additional-release unit fixture uses explicitly synthetic Brest aliases;
the five real new facilities are separately physically verified in scope proof.
"""
import json
import socket
from copy import deepcopy
from datetime import timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_stadium_locations_shadow import NOW, sources

from goal_analysis.jobs import candidate_context_review as context
from goal_analysis.jobs import stadium_locations_shadow as geo


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket, 'socket', Mock(side_effect=AssertionError('NO_REAL_NETWORK')))


def additional_release(root, monkeypatch):
    monkeypatch.setattr(geo, 'ADDITIONAL_REVIEWED_RELEASES', ())
    previous = sources(root, monkeypatch)
    latest = deepcopy(previous)
    latest['reviewed_at'] = latest['available_at'] = (NOW - timedelta(seconds=30)).isoformat()
    for number in range(5):
        row = deepcopy(previous['records'][0])
        row['native_name'] = 'TESTONLY reviewed Brest alias ' + str(number)
        row['alias_note'] = 'SYNTHETIC_UNIT_ALIAS_NOT_A_REAL_NEW_FACILITY'
        latest['records'].append(row)
    reseal(root, monkeypatch, latest)
    return previous, latest


def reseal(root, monkeypatch, packet):
    raw = json.dumps(packet, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    digest = sha256(raw).hexdigest()
    (root / 'data/arthur/stadium-locations' / (digest + '.json')).write_bytes(raw)
    monkeypatch.setattr(geo, 'ADDITIONAL_REVIEWED_RELEASES', ((digest, 74, packet['available_at'], 110_000),))
    return digest


def test_new_exact_reviewed_release_preserves_original_69_and_byte_guard(tmp_path, monkeypatch):
    previous, _ = additional_release(tmp_path, monkeypatch)
    values = geo.read_locations(tmp_path, as_of=NOW)
    assert len(values) == 74
    assert geo.ADDITIONAL_REVIEWED_RELEASES[0][3] == 110_000
    assert all((row['native_name'], row['native_city']) in values for row in previous['records'])
    assert all(('TESTONLY reviewed Brest alias ' + str(i), 'Brest') in values for i in range(5))


def test_new_release_cannot_change_a_cutoff_before_its_actual_review(tmp_path, monkeypatch):
    additional_release(tmp_path, monkeypatch)
    values = geo.read_locations(tmp_path, as_of=NOW - timedelta(seconds=31))
    assert len(values) == 69
    assert not any(key[0].startswith('TESTONLY') for key in values)


@pytest.mark.parametrize('fault', ['coordinate', 'revision', 'future_source', 'duplicate_alias', 'physical_bytes'])
def test_new_release_keeps_original_hash_identity_clock_and_ambiguity_checks(tmp_path, monkeypatch, fault):
    _, packet = additional_release(tmp_path, monkeypatch)
    last = packet['records'][-1]
    if fault == 'coordinate':
        last['coordinate_statement']['latitude'] = 0
    elif fault == 'revision':
        last['lastrevid'] += 1
    elif fault == 'future_source':
        last['identity_source']['observed_at'] = (NOW + timedelta(seconds=1)).isoformat()
    elif fault == 'duplicate_alias':
        last['native_name'] = packet['records'][-2]['native_name']
    digest = reseal(tmp_path, monkeypatch, packet)
    if fault == 'physical_bytes':
        file = tmp_path / 'data/arthur/stadium-locations' / (digest + '.json')
        file.write_bytes(file.read_bytes() + b' ')
    with pytest.raises((ValueError, KeyError)):
        geo.read_locations(tmp_path, as_of=NOW)


def fixture(number=1):
    return {'fixture_id': 'the_stats_api:mt_' + str(990000 + number),
            'kickoff': (NOW + timedelta(hours=2)).isoformat()}


def forecast(value):
    return {'fixture_id': value['fixture_id'], 'kickoff': value['kickoff'],
            'status': 'SOURCED', 'weather_kind': 'FORECAST', 'observed_at': NOW.isoformat(),
            'time_basis': 'FETCHED_AT', 'source_url': 'https://api.open-meteo.com/v1/forecast?latitude=48.4&longitude=-4.46',
            'temperature_c': 10, 'wind_speed_kmh': 12, 'wind_gusts_kmh': 20, 'precipitation_mm': .1,
            'raw_payload_hash': 'a' * 64}


def forbidden_provider():
    return SimpleNamespace(forecast=Mock(side_effect=AssertionError('NO_REFRESH_OF_CACHED_WEATHER')))


@pytest.mark.parametrize('field,value', [('fixture_id', 'the_stats_api:mt_999999'),
                                      ('kickoff', (NOW + timedelta(hours=3)).isoformat()),
                                      ('kickoff', 'invalid-clock')])
def test_bad_cached_forecast_is_local_unknown_without_refresh_or_clock_graft(field, value):
    target = fixture()
    cached = forecast(target)
    cached[field] = value
    original = deepcopy(cached)
    packets = [{'fixture_id': target['fixture_id'], 'weather_forecast': cached}]
    provider = forbidden_provider()
    values, attempts, skipped = context._forecast_scope([target], packets, provider, lambda: NOW)
    result = values[target['fixture_id']]
    assert result['status'] == 'UNKNOWN' and result['fixture_id'] == target['fixture_id']
    assert result['reuse_issue'] == 'WEATHER_FIXTURE_OR_KICKOFF_MISMATCH'
    assert result['time_basis'] == 'LOOKUP_ATTEMPT_AT'
    assert result['rejected_original_observed_at'] == original['observed_at']
    assert cached == original and not attempts and not skipped
    provider.forecast.assert_not_called()


def test_matching_cache_keeps_original_observation_even_with_equivalent_timezone():
    target = fixture()
    cached = forecast(target)
    cached['kickoff'] = (NOW + timedelta(hours=2)).astimezone(
        timezone(timedelta(hours=2))).isoformat()
    provider = forbidden_provider()
    values, attempts, _ = context._forecast_scope([target],
        [{'fixture_id': target['fixture_id'], 'weather_forecast': cached}], provider,
        lambda: NOW + timedelta(minutes=2))
    assert values[target['fixture_id']] == cached and not attempts
    provider.forecast.assert_not_called()


def test_existing_unknown_cache_does_not_become_healthy_or_trigger_a_global_refresh():
    targets = [fixture(1), fixture(2)]
    unknown = {**forecast(targets[0]), 'status': 'UNKNOWN'}
    valid = forecast(targets[1])
    provider = forbidden_provider()
    values, attempts, _ = context._forecast_scope(targets, [
        {'fixture_id': targets[0]['fixture_id'], 'weather_forecast': unknown},
        {'fixture_id': targets[1]['fixture_id'], 'weather_forecast': valid}], provider, lambda: NOW)
    assert values[targets[0]['fixture_id']]['status'] == 'UNKNOWN'
    assert values[targets[1]['fixture_id']]['status'] == 'SOURCED'
    assert not attempts


def test_new_lookup_returning_another_known_kickoff_is_not_consumed():
    target = fixture()
    wrong = {**forecast(target), 'kickoff': (NOW + timedelta(hours=3)).isoformat()}
    provider = SimpleNamespace(forecast=Mock(return_value=wrong))
    values, attempts, _ = context._forecast_scope([target], [{'fixture_id': target['fixture_id']}],
                                                provider, lambda: NOW)
    assert values[target['fixture_id']]['status'] == 'UNKNOWN' and len(attempts) == 1
    provider.forecast.assert_called_once()


@pytest.mark.parametrize('fault', ['foreign_fixture', 'foreign_kickoff', 'future_clock', 'reanalysis'])
def test_direct_compose_excludes_only_bad_weather_and_keeps_other_fixture(tmp_path, monkeypatch, fault):
    from goal_analysis.jobs import xg_specialist_shadow
    monkeypatch.setattr(xg_specialist_shadow, '_targets', lambda *_a, **_k: ({}, {}, {}))
    targets = [fixture(1), fixture(2)]
    bad, valid = forecast(targets[0]), forecast(targets[1])
    if fault == 'foreign_fixture':
        bad['fixture_id'] = targets[1]['fixture_id']
    elif fault == 'foreign_kickoff':
        bad['kickoff'] = (NOW + timedelta(hours=3)).isoformat()
    elif fault == 'future_clock':
        bad['observed_at'] = (NOW + timedelta(seconds=1)).isoformat()
    else:
        bad['weather_kind'] = 'REANALYSIS'
    packets = [{'fixture_id': targets[0]['fixture_id'], 'weather_forecast': bad},
               {'fixture_id': targets[1]['fixture_id'], 'weather_forecast': valid}]
    result = context.compose(tmp_path, targets, packets, {}, as_of=NOW)
    weather = {key: next(row for row in value['categories'] if row['category'] == 'weather')
               for key, value in result['contexts'].items()}
    assert weather[targets[0]['fixture_id']]['status'] == 'UNKNOWN'
    assert weather[targets[1]['fixture_id']]['status'] == 'SOURCED'
    assert weather[targets[1]['fixture_id']]['data'] == valid


def test_one_optional_forecast_failure_does_not_stop_next_valid_fixture():
    targets = [fixture(1), fixture(2)]
    provider = SimpleNamespace(forecast=Mock(side_effect=[OSError('TESTONLY timeout'), forecast(targets[1])]))
    values, attempts, _ = context._forecast_scope(targets,
        [{'fixture_id': target['fixture_id']} for target in targets], provider, lambda: NOW)
    assert provider.forecast.call_count == len(attempts) == 2
    assert values[targets[0]['fixture_id']]['status'] == 'UNKNOWN'
    assert values[targets[1]['fixture_id']]['status'] == 'SOURCED'
