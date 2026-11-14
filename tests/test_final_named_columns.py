"""Lossless named-column FINAL wires; pure JSON, no host, source files or database."""
import json
from copy import deepcopy

import pytest

from goal_analysis.agents.profile_input import (
    FINAL_REQUEST_PROJECTION,
    FINAL_REQUEST_PROJECTION_V1,
    FINAL_REQUEST_PROJECTION_V2,
    final_request_data,
)
from goal_analysis.jobs.agent_snapshot import digest, json_text


def _restore(data):
    data = deepcopy(data)
    current = data.get('final_data_projection')
    if current is not None and current['version'] == 'FINAL_EXACT_NAMED_COLUMNS_V3':
        columns, used = data.pop('exact_columns'), set()
        assert set(columns) == {f'k{index}' for index in range(len(columns))}
        assert all(isinstance(fields, list) and all(isinstance(key, str) for key in fields)
                   and fields == sorted(set(fields)) for fields in columns.values())

        def decode_rows(value):
            if isinstance(value, dict) and set(value) == {'$row'}:
                row = value['$row']
                assert isinstance(row, list) and len(row) == 2
                key, cells = row
                assert isinstance(key, str) and key in columns
                assert isinstance(cells, list) and len(cells) == len(columns[key])
                used.add(key)
                return dict(zip(columns[key], [decode_rows(cell) for cell in cells], strict=True))
            if isinstance(value, dict):
                return {key: decode_rows(item) for key, item in value.items()}
            if isinstance(value, list):
                return [decode_rows(item) for item in value]
            return value

        data.pop('final_data_projection')
        data = decode_rows(data)
        assert used == set(columns)
        if current['values_projection'] is not None:
            data['final_data_projection'] = current['values_projection']
        assert digest(data) == current['values_wire_sha256']
        restored = _restore(data)
        assert digest(restored) == current['source_data_sha256']
        return restored
    marker = data.pop('final_data_projection', None)
    if marker is None:
        return data
    values, active = data.pop('exact_values'), set()
    assert marker['version'] in {'FINAL_EXACT_SUBTREES_V1', 'FINAL_EXACT_VALUES_V2'}
    if marker['version'] == 'FINAL_EXACT_VALUES_V2':
        assert set(values) == {f'v{index}' for index in range(len(values))}

    def expand(value):
        if isinstance(value, dict) and set(value) == {'__arthur_final_ref__'}:
            key = value['__arthur_final_ref__']
            assert key not in active
            active.add(key)
            result = expand(values[key]['value'])
            active.remove(key)
            if marker['version'] == 'FINAL_EXACT_SUBTREES_V1':
                assert digest(result) == key == values[key]['source_value_sha256']
            else:
                assert digest(result) == values[key]['source_value_sha256']
            return result
        if isinstance(value, dict):
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    restored = expand(data)
    assert digest(restored) == marker['source_data_sha256']
    return restored


def _data():
    records = []
    for index in range(160):
        row = {f'original_descriptive_metric_field_{column}': index * 20 + column
               for column in range(14)}
        row.update(index=index, typed=[None, False, True, 0, 1, 1.0, 'UNKNOWN'],
                   legacy_shared=[None, False, True, 1, 1.0, 'UNKNOWN'] * 40,
                   full_source='https://TESTONLY.invalid/original/' + 'source-' * 100,
                   nested={'original_available_status': 'UNKNOWN', 'original_measurement_index': index})
        if index % 2:
            row['optional_original_fact'] = None
        records.append(row)
    return {'snapshot': {'clock': '2026-11-14T00:00:00+00:00', 'status': 'UNKNOWN'},
            'own_plan': {'options': [], 'reason': 'TESTONLY original'},
            'validated_own_options': [{'option_id': 'TESTONLY-A', 'records': records}],
            'tool_results': [], 'own_source_details': {}, 'own_option_admission': None}


def test_named_columns_default_preserves_every_typed_value_and_canonical_order():
    data = _data()
    original = json_text(data)
    prior = final_request_data(data, projection_policy=FINAL_REQUEST_PROJECTION_V2)
    wire = final_request_data(data)
    assert wire['final_data_projection']['version'] == FINAL_REQUEST_PROJECTION
    assert wire['final_data_projection']['values_projection'] == prior['final_data_projection']
    assert wire['final_data_projection']['values_wire_sha256'] == digest(prior)
    assert len(json_text(wire)) < len(json_text(prior))
    assert json_text(_restore(wire)) == original == json_text(data)
    assert wire['snapshot'] == data['snapshot'] and wire['own_plan'] == data['own_plan']
    assert wire['validated_own_options'][0]['option_id'] == 'TESTONLY-A'
    restored = _restore(wire)['validated_own_options'][0]['records']
    assert [row['index'] for row in restored] == list(range(160))
    assert restored[1]['optional_original_fact'] is None
    assert 'optional_original_fact' not in restored[0]
    assert [type(value) for value in restored[0]['typed']] == [type(None), bool, bool, int, int, float, str]

    def reverse_fields(value):
        if isinstance(value, dict):
            return {key: reverse_fields(value[key]) for key in reversed(value)}
        if isinstance(value, list):
            return [reverse_fields(row) for row in value]
        return value

    assert json_text(final_request_data(json.loads(original))) == json_text(wire)
    assert json_text(final_request_data(reverse_fields(data))) == json_text(wire)
    assert all(fields == sorted(set(fields)) for fields in wire['exact_columns'].values())


def test_named_columns_rejects_ambiguous_or_corrupt_rows_and_retains_exact_old_policies():
    data = _data()
    for reserved in ('exact_columns', 'exact_values', 'final_data_projection'):
        collision = deepcopy(data)
        collision[reserved] = {'original': 'TESTONLY'}
        assert final_request_data(collision) == collision
    for reserved in ('$row', '__arthur_final_ref__'):
        collision = deepcopy(data)
        collision['own_source_details'] = {'original': {reserved: 'TESTONLY'}}
        assert final_request_data(collision) == collision
    small = deepcopy(data)
    small['validated_own_options'] = [{'option_id': 'A', 'short': 1}, {'option_id': 'B', 'short': 2}]
    assert final_request_data(small) == small
    wire = final_request_data(data)
    for corruption in ('missing_column', 'duplicate_column', 'wrong_intermediate_hash', 'wrong_original_hash'):
        broken = deepcopy(wire)
        first = next(iter(broken['exact_columns']))
        if corruption == 'missing_column':
            broken['exact_columns'][first].pop()
        elif corruption == 'duplicate_column':
            broken['exact_columns'][first][0] = broken['exact_columns'][first][1]
        elif corruption == 'wrong_intermediate_hash':
            broken['final_data_projection']['values_wire_sha256'] = '0' * 64
        else:
            broken['final_data_projection']['source_data_sha256'] = '0' * 64
        with pytest.raises(AssertionError):
            _restore(broken)
    for policy in (FINAL_REQUEST_PROJECTION_V1, FINAL_REQUEST_PROJECTION_V2):
        historical = final_request_data(data, projection_policy=policy)
        assert historical['final_data_projection']['version'] == policy
        assert 'exact_columns' not in historical
        assert json_text(_restore(historical)) == json_text(data)
        assert json_text(final_request_data(json.loads(json_text(data)), projection_policy=policy)) == json_text(historical)
    with pytest.raises(ValueError, match='UNKNOWN_FINAL_REQUEST_PROJECTION'):
        final_request_data(data, projection_policy='FOREIGN')
