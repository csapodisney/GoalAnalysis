"""First inert source-bound FINAL controls; no real host, network, process or DB."""
import json
from copy import deepcopy
from functools import partial
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from goal_analysis.agents import codex_review
from goal_analysis.agents.profile_decision import (
    FAMILY_USES,
    SIMULATOR_ROLES,
    consider_family,
    consider_fixture,
    finalize_decisions,
    record_tool_result,
    request_tool,
    set_options,
    start_sessions,
)
from goal_analysis.agents.profile_decision_runner import (
    bound_final_family_states,
    profile_response_schema,
)
from goal_analysis.agents.profile_roster import ACTIVE_AGENTS, get_agent_definition
from goal_analysis.agents.profile_source_host import validate_source_evidence
from goal_analysis.jobs.agent_snapshot import FAMILIES, build_snapshot, digest
from goal_analysis.ticket_product_policy import product_contract


def _snapshot():
    ids = [f'TESTONLY-family-fixture-{index}' for index in range(4)]
    return build_snapshot(
        run_id='TESTONLY-family-source-state', target_date='2026-11-14',
        as_of='2026-11-13T12:00:00+00:00', authoritative_fixture_ids=ids,
        calendar_receipt={'target_date': '2026-11-14', 'scope': 'FULL_PROVIDER_DAY',
                          'status': 'COMPLETE', 'fixture_ids': ids,
                          'source_refs': ['TESTONLY-calendar']},
        fixtures=[{'fixture_id': key} for key in ids],
        family_values={
            'weather': {'status': 'AVAILABLE', 'value': {'local_fact': {'status': 'UNKNOWN'}},
                        'source_refs': ['TESTONLY-weather-envelope']},
            'sourced_news': {'status': 'STALE', 'value': None, 'reason': 'TESTONLY-old-news'},
            'numeric_models': {'status': 'CONFLICTING', 'value': None,
                               'reason': 'TESTONLY-conflicting-records'},
        }, source_manifest={'TESTONLY-calendar': 'a' * 64},
    )


def _state(snapshot):
    return next(row for row in start_sessions(snapshot, {
        agent.agent_id: agent.method_version for agent in ACTIVE_AGENTS})
        if row.agent_id == 'kronikas')


def _request(snapshot):
    data = snapshot.data()
    return {'phase': 'FINAL', 'agent_id': 'kronikas', 'session_id': _state(snapshot).session_id,
            'snapshot_id': snapshot.snapshot_id, 'persona': get_agent_definition('kronikas').as_dict(),
            'data': {'snapshot': {
                'view': {'version': 'owner-final-shared-reference-v1', 'phase': 'FINAL',
                         'source_snapshot_id': snapshot.snapshot_id},
                'owner_ids': data['owner_ids'], 'fixture_ids': data['fixture_ids'],
                'families': {key: {name: value for name, value in row.items() if name != 'value'}
                             for key, row in data['families'].items()},
            }}}


def test_bound_schema_matches_existing_validator_for_all_twelve_source_states():
    snapshot = _snapshot()
    request = _request(snapshot)
    original = deepcopy(request)
    states = bound_final_family_states(request)
    schema = profile_response_schema('FINAL', family_states=states)
    uses = schema['properties']['family_uses']
    assert uses['minItems'] == uses['maxItems'] == len(FAMILIES) == 12
    branches = uses['items']['anyOf']
    by_family = {row['properties']['family']['enum'][0]: row for row in branches}
    assert len(branches) == len(by_family) == 12 and set(by_family) == set(FAMILIES)
    state = _state(snapshot)
    for family, value in snapshot.data()['families'].items():
        branch = by_family[family]
        assert branch['required'] == list(branch['properties'])
        assert branch['additionalProperties'] is False
        assert branch['properties']['value_sha256']['enum'] == [value['value_sha256']]
        admitted = []
        for status in sorted(FAMILY_USES):
            try:
                consider_family(state, snapshot, family, status=status, reason='TESTONLY accounting',
                                value_sha256=value['value_sha256'], risk='TESTONLY explicit gap')
            except ValueError:
                pass
            else:
                admitted.append(status)
        assert set(branch['properties']['status']['enum']) == set(admitted)
    assert by_family['candidate_scenarios']['properties']['status']['enum'] == [
        'MISSING', 'UNUSABLE_FOR_THIS_METHOD']
    assert by_family['weather']['properties']['status']['enum'] == [
        'AVAILABLE_AND_CONSIDERED', 'AVAILABLE_NOT_RELEVANT', 'UNUSABLE_FOR_THIS_METHOD']
    assert request == original
    # This binding does not impose ten owners on a future separately validated roster.
    future = deepcopy(request)
    future['data']['snapshot']['owner_ids'] += ['TESTONLY-owner-11', 'TESTONLY-owner-12']
    for owner in future['data']['snapshot']['owner_ids']:
        future['agent_id'] = owner
        assert bound_final_family_states(future) == states


def test_bad_bound_snapshot_contract_fails_before_the_host_and_legacy_stays_unbound():
    request = _request(_snapshot())
    reviewer = codex_review.CodexReviewer.__new__(codex_review.CodexReviewer)
    calls = []

    def forbidden(body, usage):
        calls.append(body)
        raise AssertionError('Host must not be invoked for malformed bound input')

    reviewer._request = forbidden
    variants = []
    bad = deepcopy(request)
    bad['snapshot_id'] = 'f' * 64
    variants.append(bad)
    bad = deepcopy(request)
    bad['data']['snapshot']['families'].pop('weather')
    variants.append(bad)
    bad = deepcopy(request)
    bad['data']['snapshot']['families']['FOREIGN'] = {'status': 'UNKNOWN', 'value_sha256': 'a' * 64}
    variants.append(bad)
    bad = deepcopy(request)
    bad['data']['snapshot']['families']['weather']['status'] = 'READY'
    variants.append(bad)
    bad = deepcopy(request)
    bad['data']['snapshot']['families']['weather']['value_sha256'] = 'NOT-A-HASH'
    variants.append(bad)
    bad = deepcopy(request)
    bad['agent_id'] = 'FOREIGN-owner'
    variants.append(bad)
    bad = deepcopy(request)
    bad['data']['snapshot'].pop('families')
    variants.append(bad)
    bad = deepcopy(request)
    bad['data']['snapshot']['view']['phase'] = 'PLAN'
    variants.append(bad)
    for value in variants:
        with pytest.raises(ValueError):
            reviewer.request_profile_decision(value, timeout=5)
    assert calls == []
    legacy = {'phase': 'FINAL', 'data': {'validated_own_options': []}}
    assert bound_final_family_states(legacy) is None
    plan = deepcopy(request)
    plan['phase'] = 'PLAN'
    assert bound_final_family_states(plan) is None
    old = profile_response_schema('FINAL')
    assert 'anyOf' not in old['properties']['family_uses']['items']
    assert 'minItems' not in old['properties']['family_uses']
    assert old['properties']['family_uses']['items']['properties']['status']['enum'] == sorted(FAMILY_USES)
    with pytest.raises(ValueError, match='FAMILY_SCHEMA_ONLY_FINAL'):
        profile_response_schema('PLAN', family_states=bound_final_family_states(request))


def test_actual_schema_file_transport_preserves_nested_anyof_request_and_raw_reply(tmp_path, monkeypatch):
    snapshot = _snapshot()
    request = _request(snapshot)
    original = deepcopy(request)
    states = bound_final_family_states(request)
    allowed = {'AVAILABLE': 'AVAILABLE_AND_CONSIDERED', 'UNKNOWN': 'MISSING',
               'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}
    answer = {key: request[key] for key in ('phase', 'agent_id', 'session_id', 'snapshot_id')}
    answer.update(option_ids=[], reason='TESTONLY no admitted options', tool_interpretations=[],
                  family_uses=[{'family': key, 'status': allowed[row['status']],
                      'value_sha256': row['value_sha256'], 'option_ids': [],
                      'reason': 'TESTONLY immutable source state', 'risk': 'TESTONLY explicit gap'}
                      for key, row in states.items()])
    answer_hash = digest(answer)
    captured = []
    temporary = codex_review.tempfile.TemporaryDirectory
    monkeypatch.setattr(codex_review.tempfile, 'TemporaryDirectory', partial(temporary, dir=tmp_path))

    def fake_cli(command, *, prompt, cwd, timeout):
        assert 'exec' in command and command[-1] == '-'
        assert Path(cwd).is_relative_to(tmp_path)
        schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text('utf-8'))
        captured.append({'schema': schema, 'input': json.loads(prompt.split('INPUT DATA:\n', 1)[1]),
                         'instructions': prompt.split('INPUT DATA:\n', 1)[0], 'timeout': timeout})
        Path(command[command.index('--output-last-message') + 1]).write_text(
            json.dumps(answer), encoding='utf-8')
        return CompletedProcess(command, 0, json.dumps({'type': 'turn.completed', 'usage': {}}), '')

    reviewer = codex_review.CodexReviewer.__new__(codex_review.CodexReviewer)
    reviewer._ensure_login = lambda: None
    reviewer.command = ['TESTONLY-NO-NATIVE-EXECUTION']
    reviewer.runner = fake_cli
    reviewer.model = 'TESTONLY-model'
    reviewer.reasoning_effort = 'low'
    reviewer.progress = None
    returned = reviewer.request_profile_decision(request, timeout=5)
    assert len(captured) == 1
    body = captured[0]
    assert body['schema'] == profile_response_schema('FINAL', family_states=states)
    assert body['input'] == request == original
    assert 0 < body['timeout'] <= 5
    assert 'FINAL_SOURCE_FAMILY_STATES_V1' in body['instructions']
    assert 'An UNKNOWN family must use MISSING' in body['instructions']
    assert 'it never upgrades or replaces the snapshot family state or hash' in body['instructions']
    assert 'MISSING or UNKNOWN alone does not veto' in body['instructions']
    assert 'never invent support, weights or probabilities' in body['instructions']
    assert {key: returned[key] for key in answer} == answer and digest(answer) == answer_hash
    legacy = deepcopy(request)
    legacy['data'] = {'validated_own_options': []}
    reviewer.request_profile_decision(legacy, timeout=5)
    assert captured[1]['schema'] == profile_response_schema('FINAL')
    assert 'FINAL_SOURCE_FAMILY_STATES_V1' not in captured[1]['instructions']
    assert len(captured) == 2


def test_unknown_family_and_both_unknown_tools_keep_legal_selection_and_strict_proofs():
    snapshot = _snapshot()
    initial = _state(snapshot)
    legs = [{'fixture_id': key, 'market_key': 'totals', 'selection_key': 'over_2_5',
             'period': 'FULL_TIME', 'decimal_price': 2.0} for key in snapshot.fixture_ids]
    option = {'agent_id': initial.agent_id, 'option_id': 'TESTONLY-own-selected',
              'reason': 'TESTONLY legal research selection', 'legs': deepcopy(legs),
              'canonical_card': {'profile_id': initial.agent_id, 'combined_price': 16.0,
                  'legs': legs, 'ticket_product_contract': product_contract(), 'status': 'DRAFT'}}
    state = set_options(initial, snapshot, [option])
    for fixture in snapshot.fixture_ids:
        state = consider_fixture(state, snapshot, fixture, reason='TESTONLY considered', rejected=False)
    statuses = {'AVAILABLE': 'AVAILABLE_AND_CONSIDERED', 'UNKNOWN': 'MISSING',
                'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}
    for family, row in snapshot.data()['families'].items():
        state = consider_family(state, snapshot, family, status=statuses[row['status']],
            reason='TESTONLY actual source state; local facts may remain UNKNOWN',
            value_sha256=row['value_sha256'], option_ids=[option['option_id']],
            risk='No invented source support or probability')
    for role in SIMULATOR_ROLES:
        state, tool = request_tool(state, snapshot, role)
        payload = tool.data()
        result = {key: payload[key] for key in ('request_id', 'agent_id', 'snapshot_id', 'option_hash')}
        result.update(status='UNKNOWN', value=None, reason='TESTONLY no simulator evidence',
                      own_option_ids=payload['own_option_ids'])
        state = record_tool_result(state, snapshot, tool, result,
                                   interpretation='UNKNOWN, not invented support or a global veto')
    _, decision = finalize_decisions(state, snapshot, option_ids=[option['option_id']],
                                     reason='TESTONLY best legal own choice with explicit uncertainty')
    value = decision.data()
    assert value['status'] == 'FINAL_DECISION' and value['selected_options'] == [option]
    assert value['selected_options'][0]['canonical_card']['status'] == 'DRAFT'
    assert value['family_uses']['candidate_scenarios']['status'] == 'MISSING'
    assert all(row['status'] == 'UNKNOWN' and row['value'] is None for row in value['tool_receipts'])
    null_hash = snapshot.data()['families']['candidate_scenarios']['value_sha256']
    with pytest.raises(ValueError, match='UNAVAILABLE_FACT_CANNOT_BECOME_AVAILABLE'):
        consider_family(state, snapshot, 'candidate_scenarios', status='AVAILABLE_AND_CONSIDERED',
                        reason='TESTONLY original enum error', value_sha256=null_hash,
                        option_ids=[option['option_id']])
    with pytest.raises(ValueError, match='STALE_FAMILY_VALUE'):
        consider_family(state, snapshot, 'candidate_scenarios', status='MISSING',
                        reason='TESTONLY tampered hash', value_sha256='f' * 64)
    raw_request = _request(snapshot)
    raw_reply = {'family_uses': [{'family': 'candidate_scenarios',
                                'status': 'AVAILABLE_AND_CONSIDERED'}]}
    original = deepcopy(raw_reply)
    evidence = {'version': 'profile-source-host-evidence-v2',
                'binding': {key: raw_request[key] for key in
                            ('agent_id', 'session_id', 'snapshot_id', 'phase')},
                'request_sha256': digest(raw_request), 'reply_sha256': digest(raw_reply)}
    changed = deepcopy(raw_reply)
    changed['family_uses'][0]['status'] = 'MISSING'
    with pytest.raises(ValueError, match='SOURCE_EVIDENCE_BINDING_CHANGED'):
        validate_source_evidence(evidence, request=raw_request, reply=changed, bundle=None)
    assert raw_reply == original and evidence['reply_sha256'] == digest(original)
