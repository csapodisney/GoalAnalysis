"""Lossless own-tool profile reuse; no host, model or external data access."""
from copy import deepcopy

from goal_analysis.agents.profile_input import public_tool_results
from goal_analysis.jobs.agent_snapshot import digest, json_text


def _tool(profiles, by_option):
    return [{'request': {'role': 'candidate_scenarios', 'request_id': 'request-1'},
             'result': {'role': 'candidate_scenarios', 'status': 'AVAILABLE',
                        'result_id': 'original-result-id', 'option_hash': 'original-option-hash',
                        'value': {'by_option': deepcopy(by_option),
                                  'producer_result': {'profiles': deepcopy(profiles),
                                                      'existing_kernel_result': {'unchanged': True}}}}}]


def test_exact_duplicate_profiles_roundtrip_order_and_full_values_without_mutation():
    first = {'models': {'laplace': {'p': 0.31, 'calibration': None}}, 'source_ids': ['a', 'b']}
    second = {'models': {'gauss': {'p': 0.43, 'cutoff': '2026-11-13T12:00:00Z'}},
              'source_ids': ['c', 'd']}
    original = _tool([second, first, second], {'first': first, 'second': second})
    before = deepcopy(original)
    public = public_tool_results(original)
    result = public[0]['result']
    reference = result['value']['producer_result']['profiles']
    assert reference['encoding'] == 'EXACT_BY_OPTION_PROFILE_REFERENCES_V1'
    assert reference['ordered_option_ids'] == ['second', 'first', 'second']
    restored = [deepcopy(result['value']['by_option'][key])
                for key in reference['ordered_option_ids']]
    assert restored == before[0]['result']['value']['producer_result']['profiles']
    assert digest(restored) == reference['source_profiles_sha256']
    assert reference['source_profile_count'] == 3
    assert result['value']['by_option'] == before[0]['result']['value']['by_option']
    assert result['value']['producer_result']['existing_kernel_result'] == {'unchanged': True}
    assert result['result_id'] == 'original-result-id'
    assert result['option_hash'] == 'original-option-hash'
    assert result['prompt_projection']['original_result_sha256'] == digest(before[0]['result'])
    assert result['prompt_projection']['original_result_validation_unchanged'] is True
    assert original == before
    restored[0]['models']['gauss']['p'] = 0.1
    assert original == before


def test_near_match_or_unknown_profile_shape_remains_literal():
    admitted = {'models': {'laplace': {'p': 0.31}}, 'source_ids': ['a']}
    changed = {'models': {'laplace': {'p': 0.3100001}}, 'source_ids': ['a']}
    original = _tool([admitted, changed], {'own': admitted})
    assert public_tool_results(original) == original
    malformed = _tool([admitted, None], {'own': admitted})
    assert public_tool_results(malformed) == malformed
    empty = _tool([], {'own': admitted})
    assert public_tool_results(empty) == empty


def test_large_exact_duplicate_fits_existing_cap_with_all_model_values_recoverable():
    profile = {'models': {'laplace': {'p': 0.31, 'details': ['TESTONLY-' + 'a' * 991] * 100}}}
    original = _tool([profile], {'own': profile})
    original[0]['result']['value']['producer_result']['existing_kernel_result'] = {
        'diagnostics': 'TESTONLY-' + 'b' * 99_991}
    frame = {'own_plan': 'TESTONLY-' + 'c' * 79_991, 'tool_results': original}
    assert len(json_text(frame)) > 360_000
    projected = public_tool_results(original)
    frame['tool_results'] = projected
    assert len(json_text(frame)) < 360_000
    ref = projected[0]['result']['value']['producer_result']['profiles']
    reconstructed = [projected[0]['result']['value']['by_option'][key]
                     for key in ref['ordered_option_ids']]
    assert reconstructed == original[0]['result']['value']['producer_result']['profiles']
    assert projected[0]['result']['value']['producer_result']['existing_kernel_result'] == (
        original[0]['result']['value']['producer_result']['existing_kernel_result'])
