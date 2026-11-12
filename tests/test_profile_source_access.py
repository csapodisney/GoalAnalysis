"""TESTONLY pure source pages and audit; no file, host, provider, DB or process."""
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from goal_analysis.agents.profile_source_access import (
    FAMILY_NAMES,
    SourceAccessClosed,
    SourceAccessKernel,
    SourceBundle,
    assert_selected_candidate_reads,
    build_source_seed,
    canonical_json,
    require_candidate_reads,
    validate_read_journal,
    value_hash,
)


def _source(*, fixtures=3, extra=None):
    calendar = [{'fixture_id': f'TESTONLY:f:{index}', 'home_team': f'Home {index}',
                 'away_team': f'Away {index}', 'kickoff': '2026-11-12T20:00:00+00:00'}
                for index in range(fixtures)]
    candidate = {'candidate_id': 'TESTONLY:c', 'fixture_id': 'TESTONLY:f:0',
        'decimal_price': 2.125, 'quoted_at': None, 'market_key': 'h2h', 'selection_key': 'home',
        'evidence': [{'statement': 'TESTONLY player injury is uncertain.', 'status': 'UNKNOWN'}]}
    odds = {'candidate_input': {'candidates': [deepcopy(candidate)]},
        'enriched': {'candidates': [{**deepcopy(candidate), 'decimal_price': 2.25}, deepcopy(candidate)]},
        'club_enriched': {'candidates': [deepcopy(candidate)]}}
    families = {name: {'status': 'UNKNOWN', 'reason': 'TESTONLY unavailable',
        'value': None, 'value_sha256': value_hash(None), 'source_refs': []} for name in FAMILY_NAMES}
    for name, value in (('calendar', calendar), ('odds_and_price_history', odds)):
        families[name] = {'status': 'AVAILABLE', 'value': value, 'value_sha256': value_hash(value),
                          'source_refs': ['TESTONLY:source']}
    raw = {'version': 'TESTONLY:shared', 'run_id': 'TESTONLY:run', 'target_date': '2026-11-12',
        'as_of': '2026-11-12T10:00:00+00:00', 'owner_ids': [f'TESTONLY:owner:{index}' for index in range(10)],
        'fixture_ids': [row['fixture_id'] for row in calendar], 'families': families,
        'source_manifest': {'TESTONLY:source': {'physical_validation': False}}}
    if extra:
        raw.update(deepcopy(extra))
    view = deepcopy(raw)
    view['view'] = {'source_snapshot_id': value_hash(raw), 'version': 'TESTONLY:full-view'}
    snapshot = SimpleNamespace(payload_json=canonical_json(raw), snapshot_id=value_hash(raw))
    return SourceBundle.from_snapshot(snapshot, view), raw, view, snapshot


def _kernel(bundle=None, **limits):
    bundle = bundle or _source()[0]
    binding = {'agent_id': 'TESTONLY:owner:0', 'session_id': value_hash(['TESTONLY:session']),
               'snapshot_id': bundle.snapshot_id, 'phase': 'PLAN'}
    records = []

    def append(record):
        records.append(deepcopy(record))
        return value_hash(record)

    return SourceAccessKernel(bundle, binding, append, lambda: datetime(2026, 11, 12, 10, tzinfo=UTC), **limits), binding, records


def _request(binding, **changes):
    return {**binding, 'namespace': 'raw', 'path': [], 'mode': 'describe', 'offset': 0, 'limit': 1000, **changes}


def _payload(response):
    return json.loads(response['result']['content'][0]['text'])


def test_full_calendar_seed_keeps_all_133_fixtures_ten_owners_and_twelve_original_hashes():
    bundle, raw, view, snapshot = _source(fixtures=133)
    seed = build_source_seed(snapshot, view)
    assert seed['fixture_ids'] == raw['fixture_ids']
    assert seed['owner_ids'] == raw['owner_ids'] and len(seed['owner_ids']) == 10
    assert seed['calendar'] == raw['families']['calendar']['value']
    assert seed['view']['source_snapshot_id'] == bundle.snapshot_id
    assert seed['view']['full_view_sha256'] == bundle.view_sha256
    assert seed['view']['fixture_or_candidate_shortlist_applied'] is False
    assert set(seed['families']) == set(FAMILY_NAMES)
    assert all(seed['families'][name]['value_sha256'] == raw['families'][name]['value_sha256'] for name in FAMILY_NAMES)
    assert seed['families']['weather']['status'] == 'UNKNOWN'
    assert 'value' not in seed['families']['weather']


def test_source_strings_detach_callers_and_all_candidate_producer_conflicts_remain():
    bundle, raw, view, _ = _source()
    raw['families']['odds_and_price_history']['value'].clear()
    view['fixture_ids'].clear()
    candidates = bundle.data('candidates')
    rows = candidates['TESTONLY:c']['producer_rows']
    assert [row['producer'] for row in rows] == ['candidate_input', 'enriched', 'enriched', 'club_enriched']
    assert [row['value']['decimal_price'] for row in rows] == [2.125, 2.25, 2.125, 2.125]
    assert all(row['value']['quoted_at'] is None for row in rows)
    assert rows[2]['path'][-1] == 1
    candidates['TESTONLY:c']['producer_rows'].clear()
    assert len(bundle.data('candidates')['TESTONLY:c']['producer_rows']) == 4


def test_canonical_text_pages_reconstruct_unicode_exactly_and_include_wrapper_bytes():
    text = 'TESTONLY árvíztűrő ☂ "quoted"\n' * 20
    kernel, binding, records = _kernel(_source(extra={'text': text})[0])
    chunks, offset = [], 0
    while True:
        response = kernel.read_data(_request(binding, path=['text'], mode='text', offset=offset, limit=97), rpc_id=len(chunks) + 1)
        payload = _payload(response)
        page = payload['page']
        assert payload['status'] == 'OK' and payload['source_value_sha256'] == value_hash(text)
        assert len(canonical_json(response) + '\n') <= 24000
        assert records[-1]['wire_bytes'] == len((canonical_json(response) + '\n').encode('utf-8'))
        assert 'text' not in records[-1]['page']
        chunks.append(page['text'])
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    assert ''.join(chunks) == canonical_json(text)
    assert kernel.receipt()['response_bytes'] == sum(row['wire_bytes'] for row in records)
    proof = validate_read_journal(records, kernel.receipt(), binding=binding, bundle=kernel.bundle)
    assert proof['status'] == 'VALIDATED_SOURCE_PAGES_JOURNAL_AND_COUNTS'
    assert proof['all_raw_rows_read'] is False


def test_descriptor_pages_all_object_keys_without_hidden_first_n_and_lists_keep_full_length():
    mapping = {f'TESTONLY:key:{index:02d}': index for index in range(11)}
    kernel, binding, _ = _kernel(_source(extra={'mapping': mapping, 'array': list(range(150))})[0])
    keys, offset = [], 0
    while True:
        value = _payload(kernel.read_data(_request(binding, path=['mapping'], offset=offset, limit=3), rpc_id=offset))
        assert value['page']['total_keys'] == 11
        keys.extend(value['page']['keys'])
        if value['page']['next_offset'] is None:
            break
        offset = value['page']['next_offset']
    assert keys == sorted(mapping)
    value = _payload(kernel.read_data(_request(binding, path=['array']), rpc_id=99))
    assert value['page']['array_length'] == 150 and value['page']['keys'] is None


@pytest.mark.parametrize(('path', 'reason'), [(['missing'], 'OBJECT_KEY_NOT_FOUND'),
    (['fixture_ids', 100], 'LIST_INDEX_NOT_FOUND'), (['fixture_ids', '0'], 'PATH_TYPE_MISMATCH'),
    (['fixture_ids', True], 'TYPED_JSON_PATH_REQUIRED'), (['fixture_ids', -1], 'TYPED_JSON_PATH_REQUIRED')])
def test_missing_and_typed_path_refusals_are_explicit_counted_and_journaled(path, reason):
    kernel, binding, records = _kernel()
    result = _payload(kernel.read_data(_request(binding, path=path), rpc_id=1))
    assert result['status'] == 'REFUSED' and result['reason'] == reason
    assert result['terminal'] is False and kernel.receipt()['attempts'] == 1
    assert kernel.receipt()['successful_reads'] == 0 and len(records) == 1
    validate_read_journal(records, kernel.receipt(), binding=binding, bundle=kernel.bundle)


def test_owner_identity_violation_is_terminal_and_original_parent_binding_is_unchanged():
    kernel, binding, records = _kernel()
    forged = _request(binding, agent_id='TESTONLY:owner:1')
    result = _payload(kernel.read_data(forged, rpc_id=1))
    assert result['terminal'] is True and result['status'] == 'REFUSED'
    assert records[0]['binding'] == binding and records[0]['request_sha256'] == value_hash(forged)
    with pytest.raises(SourceAccessClosed):
        kernel.read_data(_request(binding), rpc_id=2)
    assert kernel.receipt()['attempts'] == 1


def test_budget_counts_refusals_and_never_returns_more_source_after_exhaustion():
    kernel, binding, records = _kernel(max_calls=2)
    kernel.read_data(_request(binding, path=['missing']), rpc_id=1)
    kernel.read_data(_request(binding), rpc_id=2)
    denied = _payload(kernel.read_data(_request(binding), rpc_id=3))
    assert denied['reason'] == 'SOURCE_CALL_BUDGET_EXHAUSTED' and denied['terminal'] is True
    assert len(records) == kernel.receipt()['attempts'] == 3
    assert kernel.receipt()['successful_reads'] == 1


def test_utf8_budget_keeps_terminal_error_inside_the_same_4096_byte_limit():
    kernel, binding, records = _kernel(_source(extra={'large': '☂' * 5000})[0],
        response_byte_limit=4096, response_char_limit=2048)
    offset = 0
    for rpc_id in range(1, 9):
        payload = _payload(kernel.read_data(_request(binding, path=['large'], mode='text',
            offset=offset, limit=24000), rpc_id=rpc_id))
        if payload['status'] == 'REFUSED':
            break
        offset = payload['page']['next_offset']
    assert payload['status'] == 'REFUSED' and payload['terminal'] is True
    assert kernel.receipt()['response_bytes'] == sum(row['wire_bytes'] for row in records) <= 4096


def test_journal_failure_is_terminal_not_a_successful_read_receipt():
    bundle = _source()[0]
    _, binding, _ = _kernel(bundle)
    kernel = SourceAccessKernel(bundle, binding, lambda record: 'wrong-ack')
    with pytest.raises(SourceAccessClosed, match='SOURCE_JOURNAL_WRITE_FAILED'):
        kernel.read_data(_request(binding), rpc_id=1)
    assert kernel.receipt()['status'] == 'FAILED' and kernel.receipt()['successful_reads'] == 0


@pytest.mark.parametrize('defect', ['source_hash', 'page', 'wire_bytes', 'path'])
def test_audit_rebinds_pages_to_source_even_when_forged_chain_tail_is_recomputed(defect):
    kernel, binding, records = _kernel()
    kernel.read_data(_request(binding, path=['fixture_ids'], mode='text', limit=15), rpc_id=7)
    forged, receipt = deepcopy(records), kernel.receipt()
    if defect == 'source_hash':
        forged[0]['source_value_sha256'] = '0' * 64
    elif defect == 'page':
        forged[0]['page']['offset'] += 1
    elif defect == 'wire_bytes':
        forged[0]['wire_bytes'] += 1
        receipt['response_bytes'] += 1
    else:
        forged[0]['path'] = ['owner_ids']
    receipt['journal_tail_sha256'] = value_hash(forged[0])
    with pytest.raises(ValueError):
        validate_read_journal(forged, receipt, binding=binding, bundle=kernel.bundle)


def test_selected_record_requires_full_exact_pages_and_descriptor_or_unrelated_query_is_insufficient():
    kernel, binding, records = _kernel()
    kernel.read_data(_request(binding, namespace='candidates', path=['TESTONLY:c']), rpc_id=1)
    with pytest.raises(ValueError, match='NOT_FULLY_READ'):
        require_candidate_reads(records, candidate_ids=['TESTONLY:c'], bundle=kernel.bundle)
    offset = 0
    for rpc_id in range(2, 60):
        payload = _payload(kernel.read_data(_request(binding, namespace='candidates', path=['TESTONLY:c'],
            mode='text', offset=offset, limit=150), rpc_id=rpc_id))
        if payload['page']['next_offset'] is None:
            break
        offset = payload['page']['next_offset']
    validate_read_journal(records, kernel.receipt(), binding=binding, bundle=kernel.bundle)
    proof = assert_selected_candidate_reads(records, [{'source_candidate_ids': ['TESTONLY:c']}], kernel.bundle)
    assert proof['candidates']['TESTONLY:c']['producer_rows'] == 4
    assert proof['candidates']['TESTONLY:c']['covered_chars'] == proof['candidates']['TESTONLY:c']['total_chars']
    incomplete = [row for row in records if row['mode'] == 'text'][1:]
    with pytest.raises(ValueError, match='NOT_FULLY_READ'):
        require_candidate_reads(incomplete, candidate_ids=['TESTONLY:c'], bundle=kernel.bundle)
    with pytest.raises(ValueError, match='NOT_IN_SOURCE'):
        require_candidate_reads(records, candidate_ids=['foreign'], bundle=kernel.bundle)


def test_empty_abstain_has_valid_empty_journal_but_no_read_or_influence_claim():
    kernel, binding, records = _kernel()
    proof = validate_read_journal(records, kernel.receipt(), binding=binding, bundle=kernel.bundle)
    assert proof['successful_read_count'] == 0 and proof['actual_host_events_match'].startswith('REQUIRES_PARENT')
    assert require_candidate_reads(records, candidate_ids=[], bundle=kernel.bundle)['candidates'] == {}


def test_duplicate_json_keys_and_modified_frozen_source_are_rejected():
    _, _, view, _ = _source()
    ambiguous = '{"x":1,"x":2}'
    with pytest.raises(ValueError, match='DUPLICATE_OBJECT_KEY'):
        SourceBundle(ambiguous, canonical_json(view), hashlib.sha256(ambiguous.encode()).hexdigest(), value_hash(view))
    kernel, binding, _ = _kernel()
    object.__setattr__(kernel.bundle, 'raw_json', '{}')
    result = _payload(kernel.read_data(_request(binding), rpc_id=1))
    assert result['terminal'] is True and result['reason'] == 'SOURCE_HASH_OR_CANONICAL_VALUE_CHANGED'
