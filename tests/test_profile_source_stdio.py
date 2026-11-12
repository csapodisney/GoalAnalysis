"""StringIO protocol fixtures only; no native subprocess or real host."""
import json
from io import StringIO

import pytest
from test_profile_source_host import NOW, make_source_phase

from goal_analysis.agents.profile_source_access import SourceAccessKernel
from goal_analysis.agents.profile_source_host import sha
from goal_analysis.agents.profile_source_stdio import PROTOCOL_VERSION, canonical, serve


def _messages(requests):
    values = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
               'params': {'protocolVersion': '2099-01-01', 'capabilities': {},
                          'clientInfo': {'name': 'TESTONLY', 'version': '1'}}},
              {'jsonrpc': '2.0', 'method': 'notifications/initialized'}]
    return StringIO(''.join(canonical(row) + '\n' for row in [*values, *requests]))


def _kernel(tmp_path):
    scope, _, bundle = make_source_phase(tmp_path)
    records = []

    def append(record):
        records.append(record)
        return sha(record)

    return scope, SourceAccessKernel(bundle, scope.identity(), append, lambda: NOW), records


def test_stdio_protocol_returns_only_one_tool_and_exact_kernel_wire(tmp_path):
    scope, kernel, records = _kernel(tmp_path)
    arguments = {**scope.identity(), 'namespace': 'raw', 'path': ['families', 'weather', 'value'],
                 'mode': 'text', 'offset': 0, 'limit': 24_000}
    output = StringIO()
    status = serve(_messages([
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
         'params': {'name': 'read_data', 'arguments': arguments, '_meta': {'progressToken': 'TESTONLY'}}},
        {'jsonrpc': '2.0', 'id': 4, 'method': 'ping'}]), output, kernel)
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert status == 0 and responses[0]['result']['protocolVersion'] == PROTOCOL_VERSION
    assert [tool['name'] for tool in responses[1]['result']['tools']] == ['read_data']
    payload = json.loads(responses[2]['result']['content'][0]['text'])
    assert json.loads(payload['page']['text']) == {'temperature_c': -2.5, 'forecast': None}
    assert records[0]['response_sha256'] == sha(responses[2])
    assert records[0]['wire_bytes'] == len((canonical(responses[2]) + '\n').encode('utf-8'))
    assert kernel.receipt()['attempts'] == 1


@pytest.mark.parametrize('method', ['resources/read', 'sampling/createMessage', 'tools/execute'])
def test_other_protocol_capabilities_fail_before_kernel_read(tmp_path, method):
    _, kernel, records = _kernel(tmp_path)
    with pytest.raises(ValueError, match='SOURCE_RPC_METHOD_NOT_ALLOWLISTED'):
        serve(_messages([{'jsonrpc': '2.0', 'id': 2, 'method': method}]), StringIO(), kernel)
    assert records == [] and kernel.receipt()['attempts'] == 0


def test_tool_before_initialized_is_not_a_valid_source_call(tmp_path):
    _, kernel, records = _kernel(tmp_path)
    incoming = StringIO(canonical({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}) + '\n')
    with pytest.raises(ValueError, match='SOURCE_INITIALIZATION_REQUIRED'):
        serve(incoming, StringIO(), kernel)
    assert records == []


def test_foreign_owner_causes_terminal_stdio_result_without_data(tmp_path):
    scope, kernel, records = _kernel(tmp_path)
    arguments = {**scope.identity(), 'agent_id': 'TESTONLY-foreign', 'namespace': 'raw',
                 'path': [], 'mode': 'text', 'offset': 0, 'limit': 10}
    output = StringIO()
    assert serve(_messages([{'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
        'params': {'name': 'read_data', 'arguments': arguments}}]), output, kernel) == 1
    response = json.loads(output.getvalue().splitlines()[-1])
    payload = json.loads(response['result']['content'][0]['text'])
    assert payload['status'] == 'REFUSED' and payload['terminal'] is True
    assert 'page' not in payload and records[0]['status'] == 'REFUSED'
