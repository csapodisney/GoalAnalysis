"""Lossless FINAL-only wire projection; no provider or execution fixtures."""

from copy import deepcopy

import pytest

from goal_analysis.agents.profile_input import final_request_data
from goal_analysis.jobs.agent_snapshot import digest, json_text


def _restore(projected):
    result = deepcopy(projected)
    marker = result.pop('final_data_projection', None)
    if marker is None:
        return result
    assert marker['version'] == 'FINAL_EXACT_SUBTREES_V1'
    table = result.pop('exact_values')
    active = set()

    def expand(value):
        if isinstance(value, dict) and set(value) == {'__arthur_final_ref__'}:
            key = value['__arthur_final_ref__']
            assert key not in active
            active.add(key)
            restored = expand(table[key]['value'])
            active.remove(key)
            assert digest(restored) == key == table[key]['source_value_sha256']
            return restored
        if isinstance(value, dict):
            return {key: expand(row) for key, row in value.items()}
        if isinstance(value, list):
            return [expand(row) for row in value]
        return value

    result = expand(result)
    assert digest(result) == marker['source_data_sha256']
    return result


def _data():
    leg = {'candidate_id': 'source-1', 'price': 2.75, 'quoted_at': None,
           'observed_at': '2026-11-13T18:00:00+00:00',
           'history': [{'index': index, 'status': 'UNKNOWN', 'p': None} for index in range(40)]}
    options = [{'option_id': f'own-{index}', 'legs': [deepcopy(leg), deepcopy(leg)],
                'canonical_card': {'legs': [deepcopy(leg), deepcopy(leg)]}}
               for index in range(6)]
    return {'snapshot': {'snapshot_id': 'snapshot', 'fixture_ids': ['all-day-1', 'all-day-2']},
            'own_plan': {'options': [{'option_id': row['option_id']} for row in options]},
            'validated_own_options': options,
            'tool_results': [{'request': {'request_id': 'request-1'},
                              'result': {'status': 'AVAILABLE', 'source_hash': 'original',
                                         'value': {'legs': [deepcopy(leg)] * 12}}}],
            'own_source_details': {'source-1': deepcopy(leg)},
            'own_option_admission': {'accepted_option_ids': [row['option_id'] for row in options]}}


def test_six_option_projection_roundtrips_and_keeps_headers_and_ids_readable():
    data = _data()
    before = deepcopy(data)
    projected = final_request_data(data)
    assert _restore(projected) == data == before
    assert len(json_text(projected)) < len(json_text(data))
    assert set(data).issubset(projected)
    for key in ('snapshot', 'own_plan', 'own_option_admission'):
        assert projected[key] == data[key]
    assert [row['option_id'] for row in projected['validated_own_options']] == [f'own-{i}' for i in range(6)]
    assert projected['tool_results'][0]['request'] == data['tool_results'][0]['request']
    assert projected['tool_results'][0]['result']['status'] == 'AVAILABLE'
    projected['exact_values'][next(iter(projected['exact_values']))]['source_value_sha256'] = 'tampered'
    with pytest.raises(AssertionError):
        _restore(projected)


def test_same_identity_different_values_and_order_remain_exact():
    data = _data()
    changed = data['validated_own_options'][1]['legs'][0]
    changed.update({'price': 3.1, 'quoted_at': '2026-11-13T17:59:00+00:00',
                    'unknown': ['UNKNOWN', None, False, 0, 0.0],
                    'observed_at': '2026-11-13T18:01:00+00:00'})
    changed['history'].reverse()
    projected = final_request_data(data)
    restored = _restore(projected)
    assert json_text(restored) == json_text(data)
    assert restored['validated_own_options'][0]['legs'][0] != restored['validated_own_options'][1]['legs'][0]
    assert restored['validated_own_options'][1]['legs'][0]['unknown'] == ['UNKNOWN', None, False, 0, 0.0]
    assert restored['validated_own_options'][1]['legs'][0]['history'][0]['index'] == 39


def test_reserved_markers_and_unique_small_data_do_not_create_ambiguous_references():
    for key in ('exact_values', 'final_data_projection'):
        data = _data()
        data[key] = {'original_producer_value': True}
        assert final_request_data(data) == data
    data = _data()
    data['own_source_details']['source-1']['__arthur_final_ref__'] = 'producer-owned'
    assert final_request_data(data) == data
    small = {'snapshot': {'fixture_ids': ['one']}, 'own_plan': {},
             'validated_own_options': [], 'tool_results': [],
             'own_source_details': {'unknown': None}, 'own_option_admission': {}}
    assert final_request_data(small) == small
