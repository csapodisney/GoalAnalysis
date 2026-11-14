"""Pure fixture-review protocol controls; no model, provider, DB or historical replay."""
import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from goal_analysis.agents.codex_review import CodexReviewer
from goal_analysis.agents.profile_decision_runner import (
    GROUPED_FIXTURE_REVIEWS_VERSION,
    bound_fixture_review_contract,
    expand_fixture_reviews,
    fixture_review_contract,
)
from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.jobs.ten_agent_normal import NormalAgentExecutor, make_normal_agent_executor

IDS = ['TESTONLY:z', 'TESTONLY:a', 'TESTONLY:m', 'TESTONLY:q']


def _wire():
    contract = fixture_review_contract(IDS)
    return contract, {'version': GROUPED_FIXTURE_REVIEWS_VERSION,
        'fixture_order_sha256': contract['fixture_order_sha256'],
        'groups': [
            {'fixture_indices': [3, 0], 'reason': 'UNKNOWN marad; nem nulla.', 'rejected': True},
            {'fixture_indices': [2], 'reason': 'Distinct original evidence.', 'rejected': False},
            {'fixture_indices': [1], 'reason': 'Actual counterevidence.', 'rejected': True},
        ]}


def test_grouped_reviews_restore_full_original_calendar_and_distinct_values_without_mutation():
    contract, wire = _wire()
    before = deepcopy((contract, wire, IDS))
    rows = expand_fixture_reviews(wire, IDS, contract=contract)
    assert rows == [
        {'fixture_id': IDS[0], 'reason': 'UNKNOWN marad; nem nulla.', 'rejected': True},
        {'fixture_id': IDS[1], 'reason': 'Actual counterevidence.', 'rejected': True},
        {'fixture_id': IDS[2], 'reason': 'Distinct original evidence.', 'rejected': False},
        {'fixture_id': IDS[3], 'reason': 'UNKNOWN marad; nem nulla.', 'rejected': True},
    ]
    rows[0]['reason'] = 'Changed detached expansion only.'
    assert (contract, wire, IDS) == before


@pytest.mark.parametrize('fault', [
    'duplicate', 'missing', 'out_of_range', 'negative', 'boolean_index', 'string_index',
    'wrong_wire_hash', 'unknown_wire_version', 'extra_default', 'empty_reason',
    'non_boolean_rejected', 'wrong_contract_hash', 'boolean_contract_count',
])
def test_grouped_reviews_reject_nonexact_partitions_and_binding_or_type_changes(fault):
    contract, wire = _wire()
    if fault == 'duplicate':
        wire['groups'][1]['fixture_indices'].append(0)
    elif fault == 'missing':
        wire['groups'].pop()
    elif fault == 'out_of_range':
        wire['groups'][0]['fixture_indices'][0] = len(IDS)
    elif fault == 'negative':
        wire['groups'][0]['fixture_indices'][0] = -1
    elif fault == 'boolean_index':
        wire['groups'][0]['fixture_indices'][0] = True
    elif fault == 'string_index':
        wire['groups'][0]['fixture_indices'][0] = '3'
    elif fault == 'wrong_wire_hash':
        wire['fixture_order_sha256'] = fixture_review_contract(list(reversed(IDS)))['fixture_order_sha256']
    elif fault == 'unknown_wire_version':
        wire['version'] = 'UNRECOGNIZED'
    elif fault == 'extra_default':
        wire['groups'][0]['default_remaining_fixtures'] = True
    elif fault == 'empty_reason':
        wire['groups'][0]['reason'] = ''
    elif fault == 'non_boolean_rejected':
        wire['groups'][0]['rejected'] = 1
    elif fault == 'wrong_contract_hash':
        contract['fixture_order_sha256'] = '0' * 64
    elif fault == 'boolean_contract_count':
        contract['fixture_count'] = True
    with pytest.raises(ValueError):
        expand_fixture_reviews(wire, IDS, contract=contract)


def test_markerless_flat_reviews_preserve_old_order_and_cannot_self_enable_grouped_mode():
    rows = [{'fixture_id': key, 'reason': 'Original legacy order.', 'rejected': False}
            for key in reversed(IDS)]
    before = deepcopy(rows)
    assert expand_fixture_reviews(rows, IDS) is rows
    assert rows == before
    _, wire = _wire()
    with pytest.raises(ValueError, match='GROUPED_FIXTURE_REVIEWS_REQUIRE_BOUND_CONTRACT'):
        expand_fixture_reviews(wire, IDS)
    with pytest.raises(ValueError, match='GROUPED_FIXTURE_REVIEWS_BINDING_CHANGED'):
        expand_fixture_reviews(rows, IDS, contract=fixture_review_contract(IDS))


def test_actual_reviewer_schema_is_selected_only_by_valid_bound_request_before_host():
    reviewer = CodexReviewer.__new__(CodexReviewer)
    calls = []
    contract, wire = _wire()
    request = {'phase': 'PLAN', 'agent_id': 'TESTONLY-owner', 'session_id': 'TESTONLY-session',
        'snapshot_id': 'TESTONLY-snapshot', 'data': {'snapshot': {'fixture_ids': IDS},
        'fixture_review_contract': contract}}

    def inert(body, usage):
        calls.append(deepcopy(body))
        answer = {'fixture_reviews': wire}
        return {'output': [{'content': [{'type': 'output_text', 'text': json.dumps(answer)}]}]}

    reviewer._request = inert
    reviewer.request_profile_decision(request, timeout=1)
    new_schema = calls[-1]['text']['format']['schema']['properties']['fixture_reviews']
    assert new_schema['type'] == 'object'
    assert new_schema['properties']['fixture_order_sha256']['enum'] == [contract['fixture_order_sha256']]
    assert json.loads(calls[-1]['input']) == request
    legacy = deepcopy(request)
    legacy['data'].pop('fixture_review_contract')
    reviewer.request_profile_decision(legacy, timeout=1)
    assert calls[-1]['text']['format']['schema']['properties']['fixture_reviews']['type'] == 'array'
    assert bound_fixture_review_contract(legacy) is None
    invalid = deepcopy(request)
    invalid['data']['snapshot']['fixture_ids'] = list(reversed(IDS))
    with pytest.raises(ValueError, match='FIXTURE_REVIEW_CONTRACT_CHANGED'):
        reviewer.request_profile_decision(invalid, timeout=1)
    assert len(calls) == 2
    invalid['phase'] = 'FINAL'
    with pytest.raises(ValueError, match='FIXTURE_REVIEW_CONTRACT_ONLY_PLAN'):
        bound_fixture_review_contract(invalid)


def test_real_normal_factory_opts_in_without_starting_any_host_or_changing_injected_default(tmp_path):
    now = datetime(2026, 11, 14, tzinfo=UTC)
    settings = {'operating_contract_version': 2, 'enabled_profiles': list(AGENT_IDS),
                'openai': {'web_search': False}}
    actual_factory = make_normal_agent_executor(tmp_path, settings, clock=lambda: now)
    assert actual_factory.enable_grouped_fixture_reviews is True
    injected = NormalAgentExecutor(host=lambda *args, **kwargs: None,
        journal_root=tmp_path, clock=lambda: now)
    assert injected.enable_grouped_fixture_reviews is False
    assert list(tmp_path.iterdir()) == []
