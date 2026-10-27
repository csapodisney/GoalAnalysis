"""UNRUN artifact controls; synthetic source callback, no DB/HTTP/fit operations."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs import online_history_venue as binding
from goal_analysis.jobs.the_stats_live import normalize_fixture, normalize_history

NOW = datetime(2026, 10, 27, 1, tzinfo=UTC)
MISSING = 'TESTONLY_MISSING_FLAG'

def source(digest='a', *, finished=True):
    return {'content_hash': digest * 64, 'source_url': 'https://api.thestatsapi.com/api/football/matches?per_page=100&page=1' + ('&status=finished&competition_id=comp_3039' if finished else ''), 'observed_at': (NOW - timedelta(hours=1, minutes=ord(digest) - ord('a'))).isoformat()}

def raw(number=1, neutral=False, *, scheduled=False):
    value = {'id': 'mt_' + str(number), 'competition_id': 'comp_3039', 'season_id': 'sn_1', 'utc_date': (NOW + timedelta(hours=2) if scheduled else NOW - timedelta(days=3)).isoformat(), 'status': 'scheduled' if scheduled else 'finished', 'home_team': {'id': 'tm_1', 'name': 'Home'}, 'away_team': {'id': 'tm_2', 'name': 'Away'}, 'score': {'regulation': {'home': 2, 'away': 1}}}
    if neutral != MISSING:
        value['is_neutral'] = neutral
    return value

def reader_for(mapping):

    def read(pointer):
        data = mapping[pointer['content_hash']]
        if isinstance(data, Exception):
            raise data
        body = {'data': deepcopy(data), 'meta': {'page': 1, 'per_page': 100, 'total_pages': 1}}
        times = {'observed_at': pointer['observed_at'], 'available_at': (NOW - timedelta(minutes=30)).isoformat(), 'ingested_at': (NOW - timedelta(minutes=45)).isoformat(), 'published_at': None, 'availability_basis': 'SOURCE_PRESENCE_UPPER_BOUND', 'event_time': None, 'event_time_kind': 'UNKNOWN'}
        return (body, {'times': times, 'source_url': pointer['source_url'], 'receipt_id': 'TESTONLY_RECEIPT'})
    return read

@pytest.mark.parametrize('flag,expected', [(False, False), (True, True), (None, None), (MISSING, None)])
def test_actual_normalized_history_recovers_only_explicit_raw_venue_without_changing_original(flag, expected):
    pointer = source()
    match = raw(neutral=flag)
    row = normalize_history(match, 'comp_3039', pointer, NOW)
    before = deepcopy(row)
    packet = {'records': [row], 'sources': [pointer]}
    result = binding.correct_inputs([], packet, as_of=NOW, source_reader=reader_for({pointer['content_hash']: [match]}))
    assert result['history'][0] == {**row, 'neutral_venue': expected}
    assert row == before and result['new_receipts_or_availability_clocks'] == 0
    if flag == MISSING:
        assert row['neutral_venue'] is False and result['history'][0]['neutral_venue'] is None

def test_actual_normalized_target_missing_raw_flag_cannot_borrow_false():
    pointer = source('b', finished=False)
    match = raw(900, MISSING, scheduled=True)
    league = {'api_football_id': 39, 'season': 2026, 'competition_id': 'PREMIER_LEAGUE', 'competition_type': 'LEAGUE', 'odds_sport_key': 'TESTONLY_SOCCER'}
    target = normalize_fixture(match, league, NOW.date(), pointer)
    original = deepcopy(target)
    result = binding.correct_inputs([target], {'records': [], 'sources': []}, as_of=NOW, source_reader=reader_for({pointer['content_hash']: [match]}))
    assert target == original and target['neutral_venue'] is False
    assert result['fixtures'][0] == {**target, 'neutral_venue': None}
    assert result['bindings'][0]['reason'] == 'RAW_VENUE_UNKNOWN'

def test_one_unproven_page_only_marks_its_native_record_unknown_other_source_continues():
    first, second = (source(), source('b'))
    one, two = (raw(1), raw(2))
    records = [normalize_history(one, 'comp_3039', first, NOW), normalize_history(two, 'comp_3039', second, NOW)]
    result = binding.correct_inputs([], {'records': records, 'sources': [first, second]}, as_of=NOW, source_reader=reader_for({first['content_hash']: OSError('TESTONLY_SECRET'), second['content_hash']: [two]}))
    assert result['history'][0]['neutral_venue'] is None
    assert result['history'][1]['neutral_venue'] is False
    assert 'TESTONLY_SECRET' not in str(result)

@pytest.mark.parametrize('fault', ['score', 'duplicate', 'clock'])
def test_record_cannot_borrow_unrelated_or_ambiguous_raw_source(fault):
    pointer, match = (source(), raw())
    row = normalize_history(match, 'comp_3039', pointer, NOW)
    if fault == 'score':
        row['full_time_home'] = 3
    mapping = {pointer['content_hash']: [match, deepcopy(match)] if fault == 'duplicate' else [match]}
    original = reader_for(mapping)

    def reader(value):
        body, proof = original(value)
        if fault == 'clock':
            proof['times']['observed_at'] = (NOW - timedelta(hours=2)).isoformat()
        return (body, proof)
    result = binding.correct_inputs([], {'records': [row], 'sources': [pointer]}, as_of=NOW, source_reader=reader)
    assert result['history'][0]['neutral_venue'] is None
    assert {k: v for k, v in result['history'][0].items() if k != 'neutral_venue'} == {k: v for k, v in row.items() if k != 'neutral_venue'}

def test_source_scope_bound_does_not_choose_a_favorable_first_page(monkeypatch):
    monkeypatch.setattr(binding, 'MAX_SOURCES', 0)
    pointer, match = (source(), raw())
    row = normalize_history(match, 'comp_3039', pointer, NOW)

    def forbidden(_value):
        pytest.fail('BOUND_MUST_FAIL_BEFORE_ANY_SOURCE_CALLBACK')
    result = binding.correct_inputs([], {'records': [row], 'sources': [pointer]}, as_of=NOW, source_reader=forbidden)
    assert result['history'][0]['neutral_venue'] is None
    assert result['bindings'][0]['reason'] == 'ONLINE_VENUE_SOURCE_BOUND_NO_FIRST_N'
