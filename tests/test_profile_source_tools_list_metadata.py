"""Pure protocol compatibility fixtures; no native process or source read."""
import json
from io import StringIO

import pytest

from goal_analysis.agents.profile_source_stdio import canonical, serve, tool_schema


class NoReadKernel:
    def receipt(self):
        return {'status': 'OPEN', 'attempts': 0}

    def read_data(self, *args, **kwargs):
        raise AssertionError('tools/list must not read source data')


def incoming(params):
    messages = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 'TESTONLY', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': params},
    ]
    return StringIO(''.join(canonical(message) + '\n' for message in messages))


def test_tools_list_accepts_optional_metadata_without_changing_advertised_schema():
    output, kernel = StringIO(), NoReadKernel()
    assert serve(incoming({'_meta': {'progressToken': 'TESTONLY'}}), output, kernel) == 0
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[-1] == {'jsonrpc': '2.0', 'id': 2, 'result': {'tools': [tool_schema()]}}
    assert [tool['name'] for tool in replies[-1]['result']['tools']] == ['read_data']
    assert kernel.receipt()['attempts'] == 0


def test_tools_list_rejects_other_keys_and_non_object_metadata():
    for params in ({'_meta': {}, 'arguments': {}}, {'other': True}, {'_meta': 'TESTONLY'},
                   {'_meta': None}, {'_meta': []}):
        output = StringIO()
        with pytest.raises(ValueError, match='^SOURCE_TOOL_LIST_HAS_NO_CURSOR$'):
            serve(incoming(params), output, NoReadKernel())
        assert len(output.getvalue().splitlines()) == 1


def test_tools_list_still_rejects_cursor_even_with_valid_metadata():
    for cursor in ('TESTONLY', '', None):
        output = StringIO()
        with pytest.raises(ValueError, match='^SOURCE_TOOL_LIST_HAS_NO_CURSOR$'):
            serve(incoming({'_meta': {}, 'cursor': cursor}), output, NoReadKernel())
        assert len(output.getvalue().splitlines()) == 1
