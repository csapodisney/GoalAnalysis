"""Exact inline values and saved-auditor dispatch; no host, process, DB or source I/O."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from goal_analysis.agents import profile_input, profile_source_host
from goal_analysis.agents.profile_input import (
    FINAL_REQUEST_PROJECTION_V1,
)
from goal_analysis.agents.profile_input import (
    FINAL_REQUEST_PROJECTION_V2 as FINAL_REQUEST_PROJECTION,
)
from goal_analysis.agents.profile_input import (
    final_request_data as _final_request_data,
)
from goal_analysis.jobs import ten_agent_ticket_evidence
from goal_analysis.jobs.agent_snapshot import digest, json_text


def final_request_data(data, *, projection_policy=FINAL_REQUEST_PROJECTION):
    return _final_request_data(data, projection_policy=projection_policy)


OLD_RECONSTRUCTION = 'In the six original data fields, recursively replace exact one-key __arthur_final_ref__ objects with exact_values[key].value, expanding nested references first. Verify each restored value SHA256 against its table key and source_value_sha256; remove exact_values and final_data_projection to recover the original data and verify source_data_sha256. All values are supplied in this same request; order, UNKNOWN, clocks and numbers are unchanged. This is not extra source-read or profit evidence.'


def _data(records):
    return {'snapshot': {'clock': '2026-11-14T00:00:00+00:00', 'status': 'UNKNOWN'},
        'own_plan': {'options': [], 'reason': 'TESTONLY original'},
        'validated_own_options': [{'option_id': 'TESTONLY-A', 'records': records}],
        'tool_results': [], 'own_source_details': {}, 'own_option_admission': None}


def _restore(wire):
    data = deepcopy(wire)
    marker = data.pop('final_data_projection', None)
    if marker is None:
        return data
    assert marker['version'] in {FINAL_REQUEST_PROJECTION_V1, FINAL_REQUEST_PROJECTION}
    values = data.pop('exact_values')
    if marker['version'] == FINAL_REQUEST_PROJECTION:
        assert set(values) == {f'v{index}' for index in range(len(values))}
    active = set()

    def expand(value):
        if isinstance(value, dict) and set(value) == {'__arthur_final_ref__'}:
            key = value['__arthur_final_ref__']
            assert key not in active
            active.add(key)
            result = expand(values[key]['value'])
            active.remove(key)
            assert digest(result) == values[key]['source_value_sha256']
            if marker['version'] == FINAL_REQUEST_PROJECTION_V1:
                assert key == digest(result)
            return result
        if isinstance(value, dict):
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    restored = expand(data)
    assert digest(restored) == marker['source_data_sha256']
    return restored


def test_large_repeated_strings_short_keys_restore_types_order_and_every_value():
    long_value = 'https://TESTONLY.invalid/evidence/' + 'original-source-' * 150
    records = [{'index': index, 'source': long_value, 'typed': [None, False, True, 1, 1.0, 'UNKNOWN'],
                **({'optional': None} if index % 2 else {})} for index in range(300)]
    data = _data(records)
    before = json_text(data)
    projected = final_request_data(data)
    assert len(before) > 360000 and len(json_text(projected)) < 360000
    assert projected['final_data_projection']['version'] == FINAL_REQUEST_PROJECTION
    assert any(isinstance(row['value'], str) for row in projected['exact_values'].values())
    assert all(key.startswith('v') and len(row['source_value_sha256']) == 64
               for key, row in projected['exact_values'].items())
    assert json_text(_restore(projected)) == before == json_text(data)
    assert projected['snapshot'] == data['snapshot'] and projected['own_plan'] == data['own_plan']
    assert final_request_data(data) == projected
    assert _restore(projected)['validated_own_options'][0]['records'][1]['optional'] is None
    assert 'optional' not in _restore(projected)['validated_own_options'][0]['records'][0]
    corrupted = deepcopy(projected)
    entry = next(row for row in corrupted['exact_values'].values() if isinstance(row['value'], str))
    entry['value'] += 'FOREIGN'
    with pytest.raises(AssertionError):
        _restore(corrupted)
    foreign_key = deepcopy(projected)
    foreign_key['exact_values']['foreign'] = foreign_key['exact_values'].pop('v0')
    with pytest.raises(AssertionError):
        _restore(foreign_key)


def test_v1_wire_is_exact_and_reserved_input_or_unprofitable_strings_stay_unchanged(monkeypatch):
    rows = list(range(2000))
    data = _data([])
    data['validated_own_options'] = [{'option_id': 'A', 'legs': rows}, {'option_id': 'B', 'legs': rows}]
    key = digest(rows)
    expected = deepcopy(data)
    for option in expected['validated_own_options']:
        option['legs'] = {'__arthur_final_ref__': key}
    expected['exact_values'] = {key: {'source_value_sha256': key, 'value': rows}}
    expected['final_data_projection'] = {'version': FINAL_REQUEST_PROJECTION_V1,
        'source_data_sha256': digest(data), 'reconstruction': OLD_RECONSTRUCTION,
        'original_evidence_and_tools_unchanged': True}
    legacy = final_request_data(data, projection_policy=FINAL_REQUEST_PROJECTION_V1)
    assert json_text(legacy) == json_text(expected)
    assert json_text(_restore(legacy)) == json_text(data)
    modern = final_request_data(data)
    assert len(json_text(modern)) < len(json_text(legacy))
    assert json_text(_restore(modern)) == json_text(data)
    ambiguous = deepcopy(data)
    ambiguous['own_source_details']['original'] = {'__arthur_final_ref__': 'producer-owned'}
    assert final_request_data(ambiguous) == ambiguous
    small = _data([])
    small['validated_own_options'] = [{'option_id': 'A', 'x': 'x' * 150}, {'option_id': 'B', 'x': 'x' * 150}]
    assert final_request_data(small) == small
    with pytest.raises(ValueError, match='UNKNOWN_FINAL_REQUEST_PROJECTION'):
        final_request_data(data, projection_policy='FOREIGN')
    collisions = _data([{'index': index, 'a': 'a' * 1000, 'b': 'b' * 1000} for index in range(5)])
    monkeypatch.setattr(profile_input, 'digest', lambda _value: '0' * 64)
    with pytest.raises(ValueError, match='EXACT_SUBTREE_HASH_COLLISION'):
        final_request_data(collisions)


def test_actual_saved_auditor_reconstructs_markerless_v1_and_v2_without_retagging(monkeypatch):
    identity = {'agent_id': 'kronikas', 'session_id': 'TESTONLY-session', 'snapshot_id': 'TESTONLY-snapshot'}
    details = {'records': [{'index': index, 'source': 'TESTONLY-' * 300} for index in range(20)]}
    plan = {**identity, 'phase': 'PLAN', 'options': []}
    final = {**identity, 'phase': 'FINAL'}
    seed, shared_final = {'TESTONLY': 'PLAN'}, {'TESTONLY': 'FINAL'}
    evidence = {'version': 'owner-decision-evidence-v3-source-access', 'snapshot_id': identity['snapshot_id'],
        'plan': plan, 'final': final, 'validated_own_options': [], 'tool_results': [],
        'source_access': {'PLAN': {}, 'FINAL': {}},
        'input_views': {'PLAN': {'kind': 'profile_source_access.build_source_seed', 'sha256': digest(seed)},
            'FINAL': {'kind': 'profile_decision_runner.source_final_reference+own_details',
                'shared_sha256': digest(shared_final), 'own_details_sha256': digest(details),
                'public_tools_sha256': digest([])}}}
    owner = {'agent_id': identity['agent_id'], 'status': 'DECISION_COMPLETED', 'decision_evidence': evidence}
    snapshot = SimpleNamespace(snapshot_id=identity['snapshot_id'], decision_data=dict)
    shared = {'states': {identity['agent_id']: SimpleNamespace(**identity)}, 'source_seed': seed,
        'source_seed_sha256': digest(seed), 'source_final_shared': shared_final,
        'source_final_shared_sha256': digest(shared_final), 'source_bundle': None}
    original = {'snapshot': shared_final, 'own_plan': plan, 'validated_own_options': [],
                'tool_results': [], 'own_source_details': details, 'own_option_admission': None}
    seen = []

    class CapturedFinal(BaseException):
        pass

    def capture(_proof, *, request, reply, bundle):
        assert bundle is None and reply['phase'] == request['phase']
        if request['phase'] == 'FINAL':
            seen.append(deepcopy(request['data']))
            raise CapturedFinal
        return {}

    monkeypatch.setattr(ten_agent_ticket_evidence, 'final_option_details', lambda *_args, **_kwargs: deepcopy(details))
    monkeypatch.setattr(ten_agent_ticket_evidence, '_plan_auxiliary_inputs', lambda *_args: {})
    monkeypatch.setattr(profile_source_host, 'validate_source_evidence', capture)
    for policy in (None, FINAL_REQUEST_PROJECTION_V1, FINAL_REQUEST_PROJECTION):
        evidence['final_request_projection'] = policy
        with pytest.raises(CapturedFinal):
            ten_agent_ticket_evidence._owner_evidence(owner, snapshot, shared)
        expected = original if policy is None else final_request_data(original, projection_policy=policy)
        assert json_text(seen[-1]) == json_text(expected)
        assert json_text(_restore(seen[-1])) == json_text(original)
    assert digest(seen[0]) != digest(seen[2])
    assert owner['decision_evidence']['validated_own_options'] == []
    evidence['final_request_projection'] = 'FOREIGN'
    with pytest.raises(ValueError, match='UNKNOWN_FINAL_REQUEST_PROJECTION'):
        ten_agent_ticket_evidence._owner_evidence(owner, snapshot, shared)
