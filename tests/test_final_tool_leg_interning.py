"""Lossless same-result leg projection; pure fixtures, no host or model calls."""
from copy import deepcopy

from goal_analysis.agents.profile_input import public_tool_results
from goal_analysis.jobs.agent_snapshot import digest, json_text


def _result(legs, *, label='first'):
    models = {}
    kernel_models = {}
    for model, probability in [('gauss', None), ('kepler', None), ('laplace', 0.31)]:
        bindings = [{'leg': deepcopy(leg), 'source_leg_index': index,
                     'source_leg_sha256': digest(leg),
                     'binding': 'UNKNOWN' if probability is None else 'AVAILABLE'}
                    for index, leg in enumerate(legs)]
        models[model] = {'leg_bindings': bindings, 'probability': probability,
                         'status': 'UNKNOWN' if probability is None else 'AVAILABLE'}
        kernel_models[model] = {'cards': [{'leg_bindings': deepcopy(bindings),
                                           'probability': probability,
                                           'status': 'ORIGINAL_STATUS'}]}
    profile = {'models': models, 'profile_id': 'merlin', 'source_ids': [label]}
    return {'request': {'role': 'candidate_scenarios', 'request_id': label},
            'result': {'role': 'candidate_scenarios', 'result_id': label,
                       'option_hash': 'original-' + label, 'status': 'AVAILABLE',
                       'value': {'by_option': {'own': profile},
                                 'producer_result': {
                                     'profiles': [deepcopy(profile)],
                                     'existing_kernel_result': {'models': kernel_models}}}}}


def _restore(public):
    """Decode only the declared wire format, independently of its encoder."""
    restored = deepcopy(public)
    for pair in restored:
        result = pair['result']
        value = result['value']
        table = value.pop('exact_leg_values', None)
        if table is not None:
            assert table['encoding'] == 'EXACT_SCENARIO_LEG_VALUES_V1'
            model_results = [row for profile in value['by_option'].values()
                             for row in profile['models'].values()]
            model_results.extend(card for model in value['producer_result'][
                'existing_kernel_result']['models'].values() for card in model['cards'])
            for model in model_results:
                for binding in model['leg_bindings']:
                    reference = binding['leg']
                    if reference.get('encoding') == 'EXACT_SCENARIO_LEG_REFERENCE_V1':
                        leg = table['values'][reference['table_key']]
                        assert digest(leg) == reference['source_leg_sha256']
                        binding['leg'] = deepcopy(leg)
        producer = value['producer_result']
        reference = producer['profiles']
        if isinstance(reference, dict):
            assert reference['encoding'] == 'EXACT_BY_OPTION_PROFILE_REFERENCES_V1'
            profiles = [deepcopy(value['by_option'][key])
                        for key in reference['ordered_option_ids']]
            assert digest(profiles) == reference['source_profiles_sha256']
            producer['profiles'] = profiles
        result.pop('prompt_projection', None)
    return restored


def test_full_leg_roundtrip_preserves_order_unknowns_bindings_and_original_journal():
    first = {'candidate_id': 'source-a', 'quoted_at': None, 'history': {'status': 'UNKNOWN'},
             'price': 4.5, 'source_clock': '2026-11-13T16:00:00+00:00'}
    second = {'candidate_id': 'source-b', 'quoted_at': None, 'price': 6.0,
              'history': {'fixture_ids': ['old-2', 'old-1'], 'hit_rate': 0.25}}
    original = [_result([second, first, second])]
    before = deepcopy(original)
    projected = public_tool_results(original)
    value = projected[0]['result']['value']
    assert len(value['exact_leg_values']['values']) == 2
    assert _restore(projected) == before
    assert json_text(_restore(projected)) == json_text(before)
    assert original == before
    assert projected[0]['result']['option_hash'] == 'original-first'
    assert projected[0]['result']['prompt_projection']['original_result_sha256'] == digest(before[0]['result'])
    assert value['by_option']['own']['models']['gauss']['probability'] is None
    assert value['by_option']['own']['models']['laplace']['probability'] == 0.31
    value['exact_leg_values']['values']['leg-0']['price'] = 99
    assert original == before


def test_near_equal_leg_values_and_unknown_zero_false_remain_distinct():
    legs = [{'candidate_id': 'same', 'probability': None, 'available': None},
            {'candidate_id': 'same', 'probability': 0, 'available': False},
            {'candidate_id': 'same', 'probability': 0.000001, 'available': True}]
    original = [_result(legs)]
    projected = public_tool_results(original)
    values = projected[0]['result']['value']['exact_leg_values']['values']
    assert len(values) == 3
    assert list(values.values()) == legs
    assert _restore(projected) == original


def test_leg_tables_are_scoped_to_each_result_not_candidate_id_or_option_name():
    first = {'candidate_id': 'same', 'price': 4.5, 'quoted_at': None}
    second = {'candidate_id': 'same', 'price': 6.0, 'quoted_at': '2026-11-13T15:01:00Z'}
    original = [_result([first], label='first'), _result([second], label='second')]
    projected = public_tool_results(original)
    tables = [pair['result']['value']['exact_leg_values']['values'] for pair in projected]
    assert tables[0]['leg-0'] == first
    assert tables[1]['leg-0'] == second
    assert tables[0]['leg-0'] != tables[1]['leg-0']
    assert _restore(projected) == original


def test_unique_unknown_shapes_and_reserved_table_are_not_interned():
    original = _result([{'candidate_id': 'a', 'history': None}])
    original['result']['value']['exact_leg_values'] = {'producer_owned_unknown_schema': True}
    before = deepcopy(original)
    projected = public_tool_results([original])[0]
    assert projected['result']['value']['exact_leg_values'] == before['result']['value']['exact_leg_values']
    assert projected['result']['value']['by_option'] == before['result']['value']['by_option']
    assert original == before
    unique = _result([{'candidate_id': 'only'}])
    unique['result']['value']['producer_result']['profiles'] = []
    unique['result']['value']['producer_result']['existing_kernel_result']['models'] = {}
    models = unique['result']['value']['by_option']['own']['models']
    models['kepler']['leg_bindings'] = 'UNKNOWN_SCHEMA'
    models['laplace']['leg_bindings'] = [None, {'leg': 'NOT_A_DICTIONARY'}]
    assert public_tool_results([unique]) == [unique]


def test_large_repeated_legs_reduce_payload_with_whole_values_reconstructable():
    legs = [{'candidate_id': f'source-{index}', 'quoted_at': None,
             'history': {'status': 'DESCRIPTIVE_ONLY', 'rows': [f'TESTONLY-{index}-' + 'a' * 990] * 12}}
            for index in range(2)]
    original = [_result(legs)]
    projected = public_tool_results(original)
    # A component-size regression, deliberately not a saved whole FINAL fit claim.
    assert len(json_text(original)) - len(json_text(projected)) > 150_000
    assert _restore(projected) == original
    assert digest(_restore(projected)) == digest(original)
    assert original[0]['result']['value']['by_option']['own']['models']['gauss']['leg_bindings'][0]['leg'] == legs[0]


def test_original_reserved_leg_marker_disables_whole_new_leg_projection():
    marker = {'encoding': 'EXACT_SCENARIO_LEG_REFERENCE_V1', 'table_key': 'leg-0',
              'source_leg_sha256': 'PRODUCER_OWNED_UNKNOWN_VALUE'}
    ordinary = {'candidate_id': 'source-a', 'history': {'status': 'UNKNOWN'}}
    original = [_result([marker, ordinary, ordinary])]
    before = deepcopy(original)
    projected = public_tool_results(original)
    value = projected[0]['result']['value']
    assert 'exact_leg_values' not in value
    assert value['by_option'] == before[0]['result']['value']['by_option']
    assert value['producer_result']['profiles']['encoding'] == 'EXACT_BY_OPTION_PROFILE_REFERENCES_V1'
    assert projected[0]['result']['prompt_projection']['replaced_paths'] == [
        'value/producer_result/profiles']
    assert _restore(projected) == original
    assert original == before
