"""TESTONLY immutable query fixtures; no provider, DB, host, process or fit."""
import io
import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from goal_analysis.agents.profile_source_access import (
    FAMILY_NAMES,
    SourceAccessClosed,
    SourceAccessKernel,
    SourceBundle,
    canonical_json,
    require_candidate_reads,
    validate_read_journal,
    value_hash,
)
from goal_analysis.agents.profile_source_host import _native_calls, validate_source_evidence
from goal_analysis.agents.profile_source_stdio import serve


def source(rows=None, *, count=4, **limits):
    rows = rows if rows is not None else [
        {'fixture_id': f'TESTONLY:f:{i}', 'home_team': f'Home {i}', 'away_team': f'Away {i}',
         'kickoff': '2026-11-14T18:00:00+00:00', 'price': 2.0 + i / 100}
        for i in range(count)]
    candidate = {'candidate_id': 'TESTONLY:c', 'fixture_id': 'TESTONLY:f:0',
                 'decimal_price': 2.25, 'quoted_at': None}
    odds = {name: {'candidates': [deepcopy(candidate)]}
            for name in ('candidate_input', 'enriched', 'club_enriched')}
    odds['enriched']['candidates'].append({**candidate, 'decimal_price': 2.5})
    families = {name: {'status': 'UNKNOWN', 'value': None, 'value_sha256': value_hash(None),
                       'reason': 'TESTONLY_NOT_AVAILABLE', 'source_refs': []} for name in FAMILY_NAMES}
    for name, value in (('calendar', rows), ('odds_and_price_history', odds)):
        families[name] = {'status': 'AVAILABLE', 'value': value, 'value_sha256': value_hash(value)}
    raw = {'fixture_ids': [f'TESTONLY:f:{i}' for i in range(len(rows))],
           'owner_ids': [f'TESTONLY:owner:{i}' for i in range(10)], 'families': families}
    view = {**deepcopy(raw), 'view': {'source_snapshot_id': value_hash(raw)}}
    bundle = SourceBundle(canonical_json(raw), canonical_json(view), value_hash(raw), value_hash(view))
    binding = {'agent_id': raw['owner_ids'][0], 'session_id': 'TESTONLY:session',
               'snapshot_id': bundle.snapshot_id, 'phase': 'PLAN'}
    records = []

    def append(record):
        records.append(deepcopy(record))
        return value_hash(record)

    kernel = SourceAccessKernel(bundle, binding, append,
                                lambda: datetime(2026, 11, 13, 22, tzinfo=UTC), **limits)
    return kernel, binding, records, bundle, raw


def query(binding, **changes):
    return {**binding, 'namespace': 'raw', 'path': ['families', 'calendar', 'value'],
            'filters': [], 'fields': [['fixture_id'], ['price']],
            'unknown_policy': 'include', 'limit': 100, 'cursor': None, **changes}


def payload(response):
    return json.loads(response['result']['content'][0]['text'])


def read(binding, **changes):
    return {**binding, 'namespace': 'candidates', 'path': ['TESTONLY:c'],
            'mode': 'text', 'offset': 0, 'limit': 12_000, **changes}


def test_all_274_rows_accessible_by_counted_pages_without_rank_or_shared_prefilter():
    kernel, binding, records, bundle, raw = source(count=274)
    original = bundle.raw_json
    request, identifiers = query(binding), []
    while True:
        page = payload(kernel.query_data(request, rpc_id=len(records) + 1))
        assert page['status'] == 'OK' and page['scope_count'] == page['matched_count'] == 274
        assert page['unknown_count'] == page['excluded_count'] == 0
        assert page['source_order'] == 'ORIGINAL_NO_RANKING'
        assert page['full_selected_candidate_read_proven'] is False
        identifiers.extend(row['fields'][0]['value'] for row in page['items'])
        if page['next_cursor'] is None:
            break
        assert page['truncated'] is True
        request['cursor'] = page['next_cursor']
    assert identifiers == raw['fixture_ids']
    assert len(raw['owner_ids']) == 10 and set(raw['families']) == set(FAMILY_NAMES)
    assert bundle.raw_json == original
    assert all(row['wire_characters'] <= 24_000 for row in records)
    assert sum(row['wire_bytes'] for row in records) <= 524_288
    validate_read_journal(records, kernel.receipt(), binding=binding, bundle=bundle)


def test_missing_null_unknown_false_zero_and_invalid_types_remain_distinct():
    rows = [{}, {'v': None}, {'v': 'UNKNOWN'}, {'v': False}, {'v': 0}, {'v': '0'}]
    kernel, binding, records, bundle, _ = source(rows)
    clause = {'path': ['v'], 'type': 'number', 'op': 'gte', 'value': 0}
    page = payload(kernel.query_data(query(binding, filters=[clause], fields=[['v']]), rpc_id=1))
    assert (page['scope_count'], page['matched_count'], page['unknown_count'], page['excluded_count']) == (6, 1, 5, 0)
    fields = [row['fields'][0] for row in page['items']]
    assert fields[0]['state'] == 'MISSING' and 'value' not in fields[0]
    assert fields[1]['state'] == 'NULL' and fields[1]['value'] is None
    assert fields[2]['state'] == 'UNKNOWN'
    assert fields[3]['value'] is False and fields[4]['value'] == 0 and type(fields[4]['value']) is int
    assert page['reason_counts']['INVALID_TYPE'] == 2
    excluded = payload(kernel.query_data(query(binding, filters=[clause], fields=[['v']],
        unknown_policy='exclude_with_counts'), rpc_id=2))
    assert excluded['returned_count'] == 1 and excluded['unknown_excluded_count'] == 5
    boolean = payload(kernel.query_data(query(binding, filters=[{
        'path': ['v'], 'type': 'boolean', 'op': 'eq', 'value': False}], fields=[['v']],
        unknown_policy='exclude_with_counts'), rpc_id=3))
    assert boolean['returned_count'] == 1 and boolean['items'][0]['fields'][0]['value'] is False
    validate_read_journal(records, kernel.receipt(), binding=binding, bundle=bundle)


def test_bad_query_is_repairable_and_projection_never_satisfies_selected_raw_read():
    kernel, binding, records, bundle, _ = source()
    bad = query(binding, filters=[{'path': ['price'], 'type': 'number', 'op': 'gte', 'value': '2'}])
    failed = payload(kernel.query_data(bad, rpc_id=1))
    assert failed['status'] == 'REFUSED' and failed['terminal'] is False
    assert failed['retry_hint'] and failed['allowed_ops'] and failed['fallback']['tool'] == 'read_data'
    corrected = query(binding, namespace='candidates', path=[], fields=[['candidate_id'], ['producer_rows']])
    result = payload(kernel.query_data(corrected, rpc_id=2))
    assert result['status'] == 'OK' and result['scope_count'] == 1
    assert len(result['items'][0]['fields'][1]['value']) == 4  # Conflicting producer rows are retained.
    with pytest.raises(ValueError, match='SELECTED_CANDIDATE_RECORD_NOT_FULLY_READ'):
        require_candidate_reads(records, candidate_ids=['TESTONLY:c'], bundle=bundle)
    assert payload(kernel.read_data(read(binding), rpc_id=3))['status'] == 'OK'
    assert kernel.receipt()['attempts'] == 3 and kernel.failure_reason is None
    validate_read_journal(records, kernel.receipt(), binding=binding, bundle=bundle)
    assert require_candidate_reads(records, candidate_ids=['TESTONLY:c'], bundle=bundle)[
        'candidates']['TESTONLY:c']['producer_rows'] == 4


def test_foreign_cursor_is_local_but_identity_and_shared_call_limit_are_terminal():
    kernel, binding, records, bundle, _ = source(max_calls=4)
    first = payload(kernel.query_data(query(binding, limit=1), rpc_id=1))
    cursor = deepcopy(first['next_cursor'])
    cursor['snapshot_id'] = 'FOREIGN'
    assert payload(kernel.query_data(query(binding, cursor=cursor), rpc_id=2))['reason'] == 'FOREIGN_OR_CHANGED_QUERY_CURSOR'
    assert payload(kernel.query_data(query(binding), rpc_id=3))['status'] == 'OK'
    assert payload(kernel.read_data(read(binding), rpc_id=4))['status'] == 'OK'
    validate_read_journal(records, kernel.receipt(), binding=binding, bundle=bundle)
    assert payload(kernel.query_data(query(binding), rpc_id=5))['terminal'] is True
    with pytest.raises(SourceAccessClosed):
        kernel.read_data(read(binding), rpc_id=6)
    other, identity, _, _, _ = source()
    refused = payload(other.query_data(query(identity, agent_id='TESTONLY:foreign'), rpc_id=1))
    assert refused['terminal'] is True and other.failure_reason == 'FOREIGN_OWNER_SESSION_SNAPSHOT_OR_PHASE'


def test_unsupported_optional_shape_and_large_projection_can_fall_back_without_poisoning_owner():
    kernel, binding, records, bundle, _ = source([{'fixture_id': 'TESTONLY:f:0', 'large': 'é' * 30_000}])
    unsupported = payload(kernel.query_data(query(binding, path=['families', 'weather', 'value']), rpc_id=1))
    assert unsupported['status'] == 'REFUSED' and unsupported['data_status'] == 'UNKNOWN'
    assert unsupported['fallback']['path'] == ['families', 'weather', 'value']
    large = payload(kernel.query_data(query(binding, fields=[['large']]), rpc_id=2))
    assert large['reason'] == 'QUERY_ROW_EXCEEDS_REMAINING_PAGE_BUDGET' and not large['terminal']
    small = payload(kernel.query_data(query(binding, fields=[['fixture_id']]), rpc_id=3))
    assert small['status'] == 'OK' and small['scope_count'] == small['returned_count'] == 1
    raw = payload(kernel.read_data(read(binding, namespace='raw', path=['families', 'weather', 'value']), rpc_id=4))
    assert raw['page']['text'] == 'null' and kernel.failure_reason is None
    validate_read_journal(records, kernel.receipt(), binding=binding, bundle=bundle)


def test_audit_recomputes_source_query_and_detects_rehashed_argument_or_native_tool_tamper():
    kernel, binding, records, bundle, _ = source()
    args = query(binding, limit=1)
    response = kernel.query_data(args, rpc_id=1)
    events = [{'type': 'item.completed', 'item': {'id': 'TESTONLY:native:1', 'type': 'mcp_tool_call',
        'server': 'arthur_data', 'tool': 'query_data', 'arguments': args, 'status': 'completed',
        'result': response['result'], 'error': None}}]
    calls = _native_calls(events)
    request, reply = dict(binding), {'status': 'TESTONLY'}
    proof = {'version': 'profile-source-host-evidence-v1', 'binding': binding,
        'request_sha256': value_hash(request), 'reply_sha256': value_hash(reply),
        'receipt': {**kernel.receipt(), 'stdio_closed': True, 'stdio_outcome': 0,
                    'protocol_error_type': None, 'forbidden_operations': []},
        'journal_records': records, 'native_calls': calls, 'view_sha256': bundle.view_sha256}
    summary = validate_source_evidence(proof, request=request, reply=reply, bundle=bundle)
    assert summary['read_count'] == 0 and summary['query_count'] == 1
    changed = deepcopy(proof)
    changed['native_calls'][0]['tool_name'] = 'read_data'
    with pytest.raises(ValueError, match='NATIVE_SOURCE_RESULT_NOT_IN_JOURNAL'):
        validate_source_evidence(changed, request=request, reply=reply, bundle=bundle)
    altered = deepcopy(records)
    altered[0]['query_arguments']['fields'] = [['home_team']]
    altered[0]['request_sha256'] = value_hash(altered[0]['query_arguments'])
    receipt = {**kernel.receipt(), 'journal_tail_sha256': value_hash(altered[0])}
    with pytest.raises(ValueError, match='SOURCE_JOURNAL_RESPONSE_HASH_OR_BYTES_CHANGED'):
        validate_read_journal(altered, receipt, binding=binding, bundle=bundle)


def test_actual_stdio_dispatch_advertises_two_tools_and_shares_one_journal():
    kernel, binding, records, bundle, _ = source()
    messages = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'query_data', 'arguments': query(binding)}},
        {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'read_data', 'arguments': read(binding)}},
    ]
    output = io.StringIO()
    assert serve(io.StringIO(''.join(canonical_json(row) + '\n' for row in messages)), output, kernel) == 0
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [tool['name'] for tool in replies[1]['result']['tools']] == ['read_data', 'query_data']
    assert records[0]['version'] == 'profile-source-query-record-v1'
    assert records[1]['version'] == 'profile-source-read-v1'
    assert kernel.receipt()['attempts'] == 2
    validate_read_journal(records, kernel.receipt(), binding=binding, bundle=bundle)


def test_timestamp_filter_requires_actual_aware_clocks_and_does_not_invent_missing_clock():
    rows = [{'time': '2026-11-14T10:00:00+00:00'}, {'time': '2026-11-14T12:00:00'},
            {'time': None}, {}, {'time': '2026-11-14T12:00:00+01:00'}]
    kernel, binding, records, bundle, _ = source(rows)
    args = query(binding, fields=[['time']], filters=[{
        'path': ['time'], 'type': 'timestamp', 'op': 'gte', 'value': '2026-11-14T10:30:00+00:00'}])
    result = payload(kernel.query_data(args, rpc_id=1))
    assert (result['matched_count'], result['unknown_count'], result['excluded_count']) == (1, 3, 1)
    assert result['items'][-1]['fields'][0]['value'] == '2026-11-14T12:00:00+01:00'
    assert result['reason_counts'] == {'INVALID_TYPE': 1, 'NULL': 1, 'MISSING': 1}
    validate_read_journal(records, kernel.receipt(), binding=binding, bundle=bundle)


def test_pinned_loader_ignores_cache_and_rejects_source_changed_after_capture(tmp_path, monkeypatch):
    import hashlib
    import importlib.util
    import sys
    from pathlib import Path

    from goal_analysis.agents.profile_source_stdio import _load_pinned_query, _PinnedSourceLoader

    monkeypatch.setattr(sys, 'dont_write_bytecode', True)
    monkeypatch.setitem(sys.modules, 'arthur_profile_source_query', None)
    source_file = tmp_path / 'TESTONLY_query.py'
    original = b"class SourceQueryIndex:\n    sentinel = 'BOUND_SOURCE'\n"
    source_file.write_bytes(original)
    expected = hashlib.sha256(original).hexdigest()
    cache = Path(importlib.util.cache_from_source(str(source_file)))
    cache.parent.mkdir()
    cache_bytes = b'TESTONLY_SENTINEL_BYTECODE_MUST_NEVER_BE_USED'
    cache.write_bytes(cache_bytes)
    seen = []
    original_get_data = _PinnedSourceLoader.get_data

    def observe_get_data(self, path):
        seen.append(path)
        return original_get_data(self, path)

    monkeypatch.setattr(_PinnedSourceLoader, 'get_data', observe_get_data)
    module = _load_pinned_query(source_file, expected)
    assert module.SourceQueryIndex.sentinel == 'BOUND_SOURCE'
    assert Path(module.__file__).resolve() == source_file.resolve()
    assert str(cache) in seen and str(source_file) in seen
    assert cache.read_bytes() == cache_bytes and source_file.read_bytes() == original
    with pytest.raises(OSError, match='SOURCE_BYTECODE_CACHE_NOT_ALLOWED'):
        module.__loader__.get_data(str(cache))

    def mutate_after_capture(self, path):
        bound = original_get_data(self, path)
        source_file.write_bytes(b"class SourceQueryIndex:\n    sentinel = 'CHANGED_SOURCE'\n")
        return bound

    monkeypatch.setattr(_PinnedSourceLoader, 'get_data', mutate_after_capture)
    with pytest.raises(ValueError, match='SOURCE_PIN_CHANGED'):
        _load_pinned_query(source_file, expected)
    assert sys.modules['arthur_profile_source_query'].SourceQueryIndex.sentinel == 'BOUND_SOURCE'
    assert source_file.read_bytes() != original and cache.read_bytes() == cache_bytes
