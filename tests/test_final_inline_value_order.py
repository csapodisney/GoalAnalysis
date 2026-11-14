"""A request's saved canonical JSON must produce the identical V2 ordinal pool."""
import json
from copy import deepcopy

from goal_analysis.agents.profile_input import FINAL_REQUEST_PROJECTION, final_request_data
from goal_analysis.jobs.agent_snapshot import digest, json_text


def test_v2_wire_is_identical_after_saved_json_or_nested_dictionary_reordering():
    long_source = 'TESTONLY-original-source-' * 80
    shared = {'zeta': [None, False, True, 1, 1.0, 'UNKNOWN'] * 80, 'alpha': long_source}
    data = {'snapshot': {'zeta': 'UNCHANGED', 'alpha': None}, 'own_plan': {'options': []},
        'validated_own_options': [{'option_id': 'TESTONLY-A', 'zeta': deepcopy(shared),
            'alpha': [{'zeta': deepcopy(shared), 'alpha': long_source, 'index': i} for i in range(12)]},
            {'option_id': 'TESTONLY-B', 'alpha': deepcopy(shared), 'zeta': long_source}],
        'tool_results': [], 'own_source_details': {}, 'own_option_admission': None}
    before = json_text(data)
    saved = json.loads(before)

    def reversed_keys(value):
        if isinstance(value, dict):
            return {key: reversed_keys(value[key]) for key in reversed(value)}
        if isinstance(value, list):
            return [reversed_keys(item) for item in value]
        return value

    changed_order = reversed_keys(data)
    assert list(data['validated_own_options'][0]) != list(saved['validated_own_options'][0])
    assert json_text(changed_order) == before == json_text(saved)
    projected = final_request_data(data)
    assert projected['final_data_projection']['version'] == FINAL_REQUEST_PROJECTION
    assert len(projected['exact_values']) >= 2
    assert json_text(projected) == json_text(final_request_data(saved))
    assert json_text(projected) == json_text(final_request_data(changed_order))
    assert projected['final_data_projection']['source_data_sha256'] == digest(data)
    assert before == json_text(data)
