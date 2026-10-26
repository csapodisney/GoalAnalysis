"""Synthetic TESTONLY contracts, no production database/receipt/model/network operation."""
from copy import deepcopy

import pytest
from test_native_history_shadow import NOW, put, raw_match, setup

from goal_analysis.jobs import native_history_shadow as module
from goal_analysis.jobs.native_history_compaction import (
    HistoryCompactor as Compactor,
)
from goal_analysis.jobs.native_history_compaction import (
    HistoryIdentityConflict,
    reconstruct,
)
from goal_analysis.normalization.training_datasets import sha


def envelope(record_id='one', source='first', observed='2026-10-01T00:00:00+00:00', **truth):
    payload = {'fixture_id': 'mt_1', 'full_time_home': 2, 'full_time_away': 1,
        'neutral_venue': False, 'observed_at': observed, 'available_at': observed,
        'ingested_at': observed, 'published_at': None, 'source_id': source, **truth}
    return {'record_id': record_id, 'payload': payload, 'payload_sha256': sha(payload),
        'times': {k: payload[k] for k in ('observed_at', 'available_at', 'ingested_at', 'published_at')},
        'receipt_id': 'receipt-' + source, 'source_key': 'raw-' + source, 'source_sha256': 'sha-' + source}


def test_lossless_different_clocks_and_earliest_genuine_envelope_not_grafted():
    early = envelope()
    late = envelope('two', 'later', '2026-10-02T00:00:00+00:00')
    for order in ([early, late], [late, early]):
        c = Compactor()
        for e in order:
            c.add(e)
        assert len(c) == 1 and c.envelopes() == [early]
        assert reconstruct(c.aliases()) == [early, late]


@pytest.mark.parametrize('changes', [{'full_time_home': 3}, {'neutral_venue': None},
    {'neutral_venue': True}, {'season': 'other'}, {'half_time_home': None},
    {'home_id': 'different'}, {'kickoff': 'changed'}])
def test_every_truth_difference_survives_for_original_conflict_checker(changes):
    c = Compactor()
    c.add(envelope())
    c.add(envelope('two', **changes))
    assert len(c) == 2 and len(reconstruct(c.aliases())) == 2


def test_mutated_alias_clock_rejected_by_original_payload_hash():
    c = Compactor()
    c.add(envelope())
    graph = deepcopy(c.aliases())
    graph[0]['aliases'][0]['payload_provenance']['available_at'] = '2000-01-01T00:00:00+00:00'
    with pytest.raises(ValueError):
        reconstruct(graph)


def test_duplicate_identity_with_changed_truth_must_reject_even_when_not_representative():
    c = Compactor()
    c.add(envelope('earliest'))
    c.add(envelope('duplicate', 'later', '2026-10-02T00:00:00+00:00'))
    with pytest.raises(HistoryIdentityConflict, match='CONFLICTING_RAW_HISTORY_IDENTITY'):
        c.add(envelope('duplicate', 'later', '2026-10-02T00:00:00+00:00', full_time_home=3))


@pytest.mark.parametrize('last', [{'is_neutral': None}, {'score': {'regulation': {'home': 3, 'away': 1}}}])
def test_late_unknown_and_contradiction_after_old_raw_boundary_cannot_hide(last, tmp_path):
    rows = [raw_match(f'mt_{i}') for i in range(1, 101)]
    database, temporal, fixture = setup(tmp_path, history=rows)
    for page in range(2, 103):
        put(database, tmp_path, rows, page=page)
    put(database, tmp_path, [raw_match('mt_1', **last)], page=103)
    temporal.capture('raw')
    view = module.native_history_view(tmp_path, [fixture], as_of=NOW)
    assert view['scanned_history_rows'] == 10201
    assert view['status'] != 'LIMIT_REACHED'
    assert {r['fixture_id'] for r in view['history']} == {f'mt_{i}' for i in range(2, 101)}
    assert view['decision_effect'] == 'NONE' and not view['live_promotion']


def test_distinct_truth_budget_and_source_budget_still_fail_closed(tmp_path, monkeypatch):
    database, temporal, fixture = setup(tmp_path, history=[raw_match(f'mt_{i}') for i in range(1, 4)])
    monkeypatch.setattr(module, 'MAX_ROWS', 2)
    view = module.native_history_view(tmp_path, [fixture], as_of=NOW)
    assert view['status'] == 'LIMIT_REACHED' and not view['history']
    monkeypatch.setattr(module, 'MAX_ROWS', 10000)
    put(database, tmp_path, [raw_match('mt_4')], page=2)
    temporal.capture('raw')
    monkeypatch.setattr(module, 'MAX_SOURCES', 1)
    view = module.native_history_view(tmp_path, [fixture], as_of=NOW)
    assert view['status'] == 'LIMIT_REACHED' and not view['history']


def test_canonical_identity_map_stores_only_hashes_and_output_is_detached():
    c = Compactor()
    first = envelope()
    c.add(first)
    assert c._identities == {'one': sha(first)}
    graph = c.aliases()
    graph[0]['truth']['full_time_home'] = 99
    assert reconstruct(c.aliases()) == [first]
    chosen = c.envelopes()
    chosen[0]['payload']['full_time_home'] = 99
    assert c.envelopes() == [first]


def test_payload_hash_or_missing_provenance_rejected_without_invented_null():
    c = Compactor()
    changed = envelope()
    changed['payload']['full_time_home'] = 99
    with pytest.raises(ValueError, match='HISTORY_PAYLOAD_HASH_MISMATCH'):
        c.add(changed)
    missing = envelope()
    del missing['payload']['available_at']
    missing['payload_sha256'] = sha(missing['payload'])
    with pytest.raises(KeyError):
        c.add(missing)
    assert len(c) == 0


def test_same_raw_identity_contradiction_is_hard_failure_not_skipped_row(tmp_path):
    _, _, fixture = setup(tmp_path, history=[raw_match(), raw_match(score={'regulation': {'home': 3, 'away': 1}})])
    with pytest.raises(HistoryIdentityConflict, match='CONFLICTING_RAW_HISTORY_IDENTITY'):
        module.native_history_view(tmp_path, [fixture], as_of=NOW)


@pytest.mark.parametrize('field,value', [('receipt_id', 'other'), ('source_sha256', 'other'),
    ('times', {'available_at': '2000-01-01T00:00:00+00:00'})])
def test_alias_envelope_metadata_cannot_graft_receipt_source_or_clock(field, value):
    c = Compactor()
    c.add(envelope())
    graph = c.aliases()
    graph[0]['aliases'][0][field] = value
    with pytest.raises(ValueError, match='HISTORY_ENVELOPE_HASH_MISMATCH'):
        reconstruct(graph)


def test_input_envelope_times_must_match_its_own_payload():
    c = Compactor()
    value = envelope()
    value['times']['available_at'] = '2000-01-01T00:00:00+00:00'
    with pytest.raises(ValueError, match='HISTORY_CLOCK_BINDING_MISMATCH'):
        c.add(value)
    assert len(c) == 0
