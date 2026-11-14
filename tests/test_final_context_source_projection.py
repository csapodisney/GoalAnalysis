"""TESTONLY exact FINAL context locators; no host, provider, DB or process."""
import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from goal_analysis.agents.profile_input import (
    SOURCE_DETAILS_POLICY,
    SOURCE_DETAILS_POLICY_V1,
    final_option_details,
)
from goal_analysis.agents.profile_source_access import SourceAccessKernel, SourceBundle
from goal_analysis.agents.profile_source_host import validate_source_evidence
from goal_analysis.jobs import ten_agent_ticket_evidence
from goal_analysis.jobs.agent_snapshot import build_snapshot, digest, json_text

CONTEXTS = ('team_stats_xg', 'players_squad_xi', 'schedule_venue_pitch', 'weather')
NOW = datetime(2026, 11, 14, 10, tzinfo=UTC)


def _fixture():
    ids = [f'TESTONLY-context-fixture-{index}' for index in range(5)]
    rows = [{'candidate_id': f'TESTONLY-candidate-{index}', 'fixture_id': key,
             'decimal_price': 2.0 + index / 10, 'quote_observed_at': NOW.isoformat(),
             'quoted_at': None, 'optional_status': 'UNKNOWN'} for index, key in enumerate(ids)]
    containers = {name: {'candidates': deepcopy(rows)}
                  for name in ('candidate_input', 'enriched', 'club_enriched')}
    families = {'odds_and_price_history': {'status': 'AVAILABLE', 'value': containers,
                                         'source_refs': ['TESTONLY-odds']}}
    for family in CONTEXTS:
        records = {key: {'fixture_id': key, 'status': 'UNKNOWN', 'value': None,
            'quoted_at': None, 'observed_at': NOW.isoformat(),
            'source_note': ('TESTONLY exact source; quoted "text" and Unicode ő. ' * 200),
            'original_marker': {'format': SOURCE_DETAILS_POLICY, 'path': ['NOT-A-LOCATOR']}}
            for key in ids}
        families[family] = {'status': 'AVAILABLE', 'source_refs': ['TESTONLY-' + family],
            'value': {'categories': {key: [deepcopy(row), {'status': 'UNKNOWN', 'value': None}]
                                     for key, row in records.items()},
                      'by_fixture': deepcopy(records),
                      'fixtures': [deepcopy(records[ids[0]]), deepcopy(records[ids[1]]),
                                   {**deepcopy(records[ids[0]]), 'duplicate_note': 'SECOND DISTINCT ROW'},
                                   deepcopy(records[ids[-1]])]}}
    probabilities = {f'TESTONLY-model-{index}': {'fixture_id': key, 'model_id': 'TESTONLY-model',
        'market': 'FULL_TIME:h2h', 'outcome': 'home', 'value': None if index == 0 else 0.5,
        'source_refs': ['TESTONLY-model-source'], 'observed_at': NOW.isoformat(),
        'provenance': {'status': 'UNKNOWN', 'calibration': None}} for index, key in enumerate(ids)}
    snapshot = build_snapshot(run_id='TESTONLY-context-projection', target_date='2026-11-14',
        as_of=NOW.isoformat(), authoritative_fixture_ids=ids,
        calendar_receipt={'scope': 'FULL_PROVIDER_DAY', 'target_date': '2026-11-14',
            'status': 'COMPLETE', 'fixture_ids': ids, 'source_refs': ['TESTONLY-calendar']},
        fixtures=[{'fixture_id': key} for key in ids], family_values=families,
        source_manifest={'TESTONLY-calendar': 'a' * 64}, model_probabilities=probabilities)
    options = [{'agent_id': 'kronikas', 'option_id': 'TESTONLY-option', 'legs': deepcopy(rows[:2])}]
    return snapshot, options


def _read(raw, locator):
    assert locator['namespace'] == 'raw' and locator['format'] == SOURCE_DETAILS_POLICY
    value = raw
    for key in locator['path']:
        value = value[key]
    assert digest(value) == locator['source_value_sha256']
    assert len(json_text(value)) == locator['total_chars']
    return deepcopy(value)


def test_selected_contexts_roundtrip_every_record_order_unknown_and_probability():
    snapshot, options = _fixture()
    frozen, original_options = snapshot.payload_json, deepcopy(options)
    old = final_option_details(snapshot, 'kronikas', options, source_access=True,
                               source_policy=SOURCE_DETAILS_POLICY_V1)
    projected = final_option_details(snapshot, 'kronikas', options, source_access=True)
    assert projected['source_details_policy']['version'] == SOURCE_DETAILS_POLICY
    assert projected['all_raw_rows_read_by_host'] is False
    assert len(json_text(projected)) < len(json_text(old))
    restored, raw = deepcopy(projected), snapshot.data()
    for family in CONTEXTS:
        context = restored['fixture_context'][family]
        selected = context['selected_fixture_values']
        for key in ('categories', 'by_fixture'):
            selected[key] = {fixture: _read(raw, locator) for fixture, locator in selected[key].items()}
        selected['fixtures'] = [_read(raw, locator) for locator in selected['fixtures']]
        assert len(selected['fixtures']) == 3
        assert selected['fixtures'][0]['fixture_id'] == selected['fixtures'][2]['fixture_id']
        assert selected['fixtures'][2]['duplicate_note'] == 'SECOND DISTINCT ROW'
        assert selected['fixtures'][0]['quoted_at'] is None
        assert selected['fixtures'][0]['original_marker']['path'] == ['NOT-A-LOCATOR']
    for key, locator in projected['model_probabilities'].items():
        original = _read(raw, locator)
        assert locator['value'] == original['value']
        assert all(locator[name] == original[name] for name in ('model_id', 'fixture_id', 'market', 'outcome'))
        restored['model_probabilities'][key] = original
    for group in ('source_candidates_by_producer', 'history_features_by_producer'):
        for locators in restored[group].values():
            for locator in locators:
                locator['format'] = SOURCE_DETAILS_POLICY_V1
    restored['source_details_policy'] = deepcopy(old['source_details_policy'])
    assert restored == old
    projected['fixture_context']['weather']['selected_fixture_values']['fixtures'][0]['path'].append('MUTATED')
    assert snapshot.payload_json == frozen and options == original_options


def test_legacy_policy_remains_inline_context_and_invalid_policy_cannot_be_inferred():
    snapshot, options = _fixture()
    inline = final_option_details(snapshot, 'kronikas', options)
    old = final_option_details(snapshot, 'kronikas', options, source_access=True,
                               source_policy=SOURCE_DETAILS_POLICY_V1)
    assert old['source_details_policy']['version'] == 'READ_DATA_EXACT_ROWS_V1'
    assert old['fixture_context'] == inline['fixture_context']
    assert old['model_probabilities'] == inline['model_probabilities']
    assert final_option_details(snapshot, 'kronikas', options, source_access=False) == inline
    assert all(locator['format'] == SOURCE_DETAILS_POLICY_V1
               for rows in old['source_candidates_by_producer'].values() for locator in rows)
    with pytest.raises(ValueError, match='UNKNOWN_SOURCE_DETAILS_POLICY'):
        final_option_details(snapshot, 'kronikas', options, source_access=True, source_policy='FOREIGN')
    with pytest.raises(ValueError, match='SOURCE_POLICY_REQUIRES_SOURCE_ACCESS'):
        final_option_details(snapshot, 'kronikas', options, source_policy=SOURCE_DETAILS_POLICY)
    with pytest.raises(ValueError, match='ONLY_THIS_OWNERS_PROPOSED_OPTIONS'):
        final_option_details(snapshot, 'FOREIGN-owner', options, source_access=True)


def test_existing_source_kernel_reads_complete_context_pages_with_unchanged_phase_limits():
    snapshot, options = _fixture()
    details = final_option_details(snapshot, 'kronikas', options, source_access=True)
    locator = details['fixture_context']['weather']['selected_fixture_values']['fixtures'][0]
    view = snapshot.data()
    view['view'] = {'source_snapshot_id': snapshot.snapshot_id}
    bundle = SourceBundle.from_snapshot(snapshot, view)
    binding = {'agent_id': 'kronikas', 'session_id': digest(['TESTONLY-session']),
               'snapshot_id': snapshot.snapshot_id, 'phase': 'FINAL'}
    records = []

    def append(record):
        records.append(deepcopy(record))
        return digest(record)

    kernel = SourceAccessKernel(bundle, binding, append, lambda: NOW)
    assert records == [] and details['all_raw_rows_read_by_host'] is False
    assert kernel.max_calls == 64 and kernel.byte_limit == 512 * 1024 and kernel.char_limit == 24000
    chunks, offset = [], 0
    while True:
        wire = kernel.read_data({**binding, 'namespace': locator['namespace'], 'path': locator['path'],
            'mode': 'text', 'offset': offset, 'limit': 3000}, rpc_id=len(chunks) + 1)
        response = json.loads(wire['result']['content'][0]['text'])
        assert response['status'] == 'OK'
        assert response['source_value_sha256'] == locator['source_value_sha256']
        assert response['total_chars'] == locator['total_chars']
        chunks.append(response['page']['text'])
        offset = response['page']['next_offset']
        if offset is None:
            break
    assert len(chunks) > 1 and json.loads(''.join(chunks)) == _read(snapshot.data(), locator)
    assert len(records) == len(chunks) and all(row['status'] == 'OK' for row in records)
    denied = kernel.read_data({**binding, 'agent_id': 'FOREIGN-owner', 'namespace': 'raw',
        'path': locator['path'], 'mode': 'text', 'offset': 0, 'limit': 3000}, rpc_id='FOREIGN')
    assert json.loads(denied['result']['content'][0]['text'])['status'] == 'REFUSED'


def test_saved_auditor_dispatches_explicit_old_and_new_policy_without_rebinding_old_proof(monkeypatch):
    snapshot, options = _fixture()
    owner = {'agent_id': 'kronikas', 'status': 'DECISION_COMPLETED', 'decision_evidence': {
        'version': 'owner-decision-evidence-v3-source-access', 'snapshot_id': snapshot.snapshot_id,
        'plan': {}, 'final': {}, 'validated_own_options': options}}
    observed = []

    class ObservedDispatch(BaseException):
        pass

    actual_details = final_option_details

    def capture(saved_snapshot, owner_id, saved_options, **kwargs):
        observed.append((kwargs, actual_details(saved_snapshot, owner_id, saved_options, **kwargs)))
        raise ObservedDispatch

    monkeypatch.setattr(ten_agent_ticket_evidence, 'final_option_details', capture)
    for policy in (None, SOURCE_DETAILS_POLICY_V1, SOURCE_DETAILS_POLICY):
        owner['decision_evidence']['source_details_policy'] = policy
        with pytest.raises(ObservedDispatch):
            ten_agent_ticket_evidence._owner_evidence(owner, snapshot, {'states': {'kronikas': None}})
        assert observed[-1][0] == {'source_access': policy is not None, 'source_policy': policy}
        if policy is not None:
            assert observed[-1][1]['source_details_policy']['version'] == policy
    old_request = {'data': observed[1][1]}
    changed_request = {'data': observed[2][1]}
    reply = {'status': 'TESTONLY-not-a-decision'}
    proof = {'version': 'profile-source-host-evidence-v2',
        'binding': {key: None for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')},
        'request_sha256': digest(old_request), 'reply_sha256': digest(reply)}
    with pytest.raises(ValueError, match='SOURCE_EVIDENCE_BINDING_CHANGED'):
        validate_source_evidence(proof, request=changed_request, reply=reply, bundle=None)
    owner['decision_evidence']['source_details_policy'] = 'UNRECOGNIZED'
    with pytest.raises(ValueError, match='UNKNOWN_SOURCE_DETAILS_POLICY'):
        ten_agent_ticket_evidence._owner_evidence(owner, snapshot, {})
