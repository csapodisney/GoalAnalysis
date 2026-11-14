"""TESTONLY complete source catalogues: no provider, DB, fit or native model call."""
import hashlib
import json
import sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from test_agent_portfolio import NOW, SETTINGS, inputs
from test_profile_source_host import finish_fake_source_reply

from goal_analysis.agents import profile_source_access, profile_source_stdio
from goal_analysis.agents.profile_decision import SIMULATOR_ROLES, start_sessions
from goal_analysis.agents.profile_decision_runner import (
    DecisionBudget,
    DecisionJournal,
    ProfileDecisionRunner,
    source_final_reference,
)
from goal_analysis.agents.profile_input import decision_view, decode_table, final_shared_reference
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.agents.profile_source_access import (
    SourceAccessKernel,
    SourceBundle,
    build_source_seed,
    require_candidate_reads,
    validate_read_journal,
)
from goal_analysis.agents.profile_source_host import SourceAccessScope, canonical
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.jobs.agent_portfolio import (
    SOURCE_CATALOG_POLICY,
    _full_candidate_catalog,
    build_agent_option_builder,
    source_candidate_catalog_reference,
    validate_source_catalog_contract,
)
from goal_analysis.jobs.agent_snapshot import build_snapshot, digest, json_text
from goal_analysis.jobs.ten_agent_ticket_evidence import _owner_evidence


def _states(snapshot):
    return {state.agent_id: state for state in start_sessions(snapshot,
        {owner: get_agent_definition(owner).method_version for owner in AGENT_IDS})}


def _builder_without_search(builder, *, source):
    def wrapped(owner, proposal, snapshot):
        return builder(owner, proposal, snapshot)

    wrapped.construction_contract = builder.construction_contract
    if source:
        wrapped.source_construction_contract = builder.source_construction_contract
    return wrapped


def _large_fixture():
    fixtures = [{'fixture_id': f'TESTONLY:f:{i}', 'home_team': f'TESTONLY Home {i}',
        'away_team': f'TESTONLY Away {i}', 'kickoff': (NOW + timedelta(hours=8)).isoformat()}
        for i in range(273)]
    rows = [{'candidate_id': f'd223-{i:024x}', 'fixture_id': fixtures[i % len(fixtures)]['fixture_id'],
        'market_key': 'h2h', 'selection_key': 'home', 'period': 'FULL_TIME',
        'decimal_price': 2.0, 'competition_type': 'LEAGUE', 'quoted_at': None,
        'quote_observed_at': NOW.isoformat()} for i in range(18_000)]
    artifacts = {'candidate_input': {'candidates': rows}}
    enriched, club = {'candidates': []}, {'candidates': []}
    odds = {'candidate_input': artifacts['candidate_input'], 'enriched': enriched, 'club_enriched': club}
    report = {'run_id': 'TESTONLY-large-source-catalogue', 'date': NOW.date().isoformat()}
    ids = [row['fixture_id'] for row in fixtures]
    snapshot = build_snapshot(run_id=report['run_id'], target_date=report['date'], as_of=NOW.isoformat(),
        authoritative_fixture_ids=ids, fixtures=fixtures,
        calendar_receipt={'target_date': report['date'], 'scope': 'FULL_PROVIDER_DAY',
            'status': 'COMPLETE', 'fixture_ids': ids, 'source_refs': ['TESTONLY-source']},
        family_values={'odds_and_price_history': {'status': 'AVAILABLE', 'value': odds,
            'source_refs': ['TESTONLY-source']}}, source_manifest={'TESTONLY-source': {'kind': 'TESTONLY'}})
    builder = build_agent_option_builder(report, artifacts, enriched, club, SETTINGS, NOW)
    return snapshot, builder, rows


def test_large_full_catalogue_plan_fits_all_owners_and_late_source_remains_queryable(tmp_path):
    snapshot, builder, rows = _large_fixture()
    view = decision_view(snapshot)
    seed = build_source_seed(snapshot, view)
    bundle = SourceBundle.from_snapshot(snapshot, view)
    original = bundle.raw_json
    calls = []

    class CapturedBeforeReply(BaseException):
        pass

    def host(request, *, timeout):
        calls.append(deepcopy(request))
        raise CapturedBeforeReply

    budget = DecisionBudget(10, NOW + timedelta(seconds=120), clock=lambda: NOW)
    runner = ProfileDecisionRunner(host=host, tools=None,
        option_builder=_builder_without_search(builder, source=True),
        journal=DecisionJournal(tmp_path / 'j', snapshot), budget=budget,
        source_bundle=bundle, grouped_fixture_reviews=True)
    for state in _states(snapshot).values():
        with pytest.raises(CapturedBeforeReply):
            runner._owner(state, snapshot, view, seed)
    assert len(calls) == budget.used_requests == 10
    assert {call['agent_id'] for call in calls} == set(AGENT_IDS)
    for call in calls:
        assert len(json_text(call)) <= 360_000 and len(json_text(call).encode('utf-8')) <= 360_000
        assert call['data']['snapshot']['fixture_ids'] == list(snapshot.fixture_ids)
        assert len(call['data']['snapshot']['fixture_ids']) == 273
        assert len(call['data']['snapshot']['families']) == 12
        catalog = call['data']['own_construction_contract']['full_candidate_catalog']
        source = catalog['query_sources']['candidate_input']
        assert source['source_row_count'] == source['unique_candidate_id_count'] == 18_000
        assert source['source_value_sha256'] == digest(rows)
        assert source['valid_candidate_ids_sha256'] == digest([row['candidate_id'] for row in rows])
        assert catalog['candidate_filter_applied'] is False and catalog['full_record_read_proven'] is False
        assert 'table' not in catalog
    old = deepcopy(calls[0])
    old['data']['own_construction_contract'] = builder.construction_contract(old['agent_id'])
    assert len(json_text(old)) > 360_000  # Synthetic counterexample, not a future live-size claim.
    candidates = bundle.data('candidates')
    assert set(candidates) == {row['candidate_id'] for row in rows}
    identity = {key: calls[0][key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
    records = []

    def append(record):
        records.append(deepcopy(record))
        return digest(record)

    kernel = SourceAccessKernel(bundle, identity, append, clock=lambda: NOW)
    locator = calls[0]['data']['own_construction_contract']['full_candidate_catalog']['query_sources']['candidate_input']
    selected = rows[-1]['candidate_id']
    query = {**identity, 'namespace': locator['namespace'], 'path': locator['path'],
        'filters': [{'path': ['candidate_id'], 'type': 'string', 'op': 'eq', 'value': selected}],
        'fields': [['candidate_id'], ['quoted_at']], 'unknown_policy': 'include', 'limit': 100, 'cursor': None}
    response = kernel.query_data(query, rpc_id=1)
    page = json.loads(response['result']['content'][0]['text'])
    assert page['scope_count'] == 18_000 and page['matched_count'] == page['returned_count'] == 1
    assert page['items'][0]['fields'][0]['value'] == selected
    assert page['items'][0]['fields'][1]['state'] == 'NULL'
    assert page['items'][0]['source']['source_value_sha256'] == digest(rows[-1])
    assert page['full_selected_candidate_read_proven'] is False
    with pytest.raises(ValueError, match='SELECTED_CANDIDATE_RECORD_NOT_FULLY_READ'):
        require_candidate_reads(records, candidate_ids=[selected], bundle=bundle)
    kernel.read_data({**identity, 'namespace': 'candidates', 'path': [selected],
        'mode': 'text', 'offset': 0, 'limit': 12_000}, rpc_id=2)
    assert require_candidate_reads(records, candidate_ids=[selected], bundle=bundle)['candidates'][
        selected]['producer_rows'] == 1
    validate_read_journal(records, kernel.receipt(), binding=identity, bundle=bundle)
    assert bundle.raw_json == original and kernel.receipt()['attempts'] == 2


def test_reference_preserves_unknown_duplicate_rows_and_rejects_forged_bindings():
    rows = [{'candidate_id': 'TESTONLY:dup', 'quoted_at': None},
            {'candidate_id': 'TESTONLY:dup', 'quoted_at': NOW.isoformat()}, None, {'candidate_id': ''}]
    odds = {'candidate_input': {'candidates': rows}, 'enriched': {'candidates': None}, 'club_enriched': {}}
    before = deepcopy(odds)
    reference = source_candidate_catalog_reference(odds, 'TESTONLY-snapshot')
    source = reference['query_sources']['candidate_input']
    assert source['source_row_count'] == 4 and source['unique_candidate_id_count'] == 1
    assert source['duplicate_candidate_id_count'] == 1 and source['rows_without_valid_candidate_id'] == 2
    assert source['source_value_sha256'] == digest(rows)
    assert reference['query_sources']['enriched']['status'] == 'UNKNOWN'
    assert reference['query_sources']['enriched']['source_field_present'] is True
    assert reference['query_sources']['club_enriched']['source_field_present'] is False
    assert all(reference['query_sources'][key]['source_row_count'] is None for key in ('enriched', 'club_enriched'))
    contract = {'source_catalog_policy': SOURCE_CATALOG_POLICY, 'full_candidate_catalog': reference}
    validate_source_catalog_contract(contract, 'TESTONLY-snapshot', odds, source_access=True)
    for defect in ('hash', 'path', 'count', 'snapshot', 'version', 'policy', 'missing_policy'):
        forged = deepcopy(contract)
        catalog = forged['full_candidate_catalog']
        fields = catalog['query_sources']['candidate_input']
        if defect == 'hash':
            fields['source_value_sha256'] = '0' * 64
        elif defect == 'path':
            fields['path'] = ['families', 'missing', 'value']
        elif defect == 'count':
            fields['source_row_count'] = True
        elif defect == 'snapshot':
            catalog['snapshot_id'] = 'FOREIGN'
        elif defect == 'version':
            catalog['version'] = 'UNKNOWN_VERSION'
        elif defect == 'policy':
            forged['source_catalog_policy'] = 'UNKNOWN_POLICY'
        else:
            forged.pop('source_catalog_policy')
        with pytest.raises(ValueError, match='SOURCE_CATALOG'):
            validate_source_catalog_contract(forged, 'TESTONLY-snapshot', odds, source_access=True)
    with pytest.raises(ValueError, match='NON_SOURCE_CATALOG'):
        validate_source_catalog_contract(contract, 'TESTONLY-snapshot', odds, source_access=False)
    assert odds == before


def test_legacy_inline_contract_remains_detached_and_exact_after_source_mode():
    _, artifacts, _, _, snapshot, builder, _ = inputs(owners=('kronikas',), variants=1)
    before = {owner: builder.construction_contract(owner) for owner in AGENT_IDS}
    for owner in AGENT_IDS:
        assert before[owner]['full_candidate_catalog'] == _full_candidate_catalog(artifacts['candidate_input'])
        assert 'source_catalog_policy' not in before[owner]
        source = builder.source_construction_contract(owner, snapshot)
        assert source['source_catalog_policy'] == SOURCE_CATALOG_POLICY
        source['full_candidate_catalog']['query_sources']['candidate_input']['path'].append('FOREIGN')
        assert builder.construction_contract(owner) == before[owner]
        assert builder.source_construction_contract(owner, snapshot)['full_candidate_catalog'][
            'query_sources']['candidate_input']['path'][-1] == 'candidates'
        validate_source_catalog_contract(before[owner], 'UNUSED', None, source_access=False)
    assert all('optional_data_instruction' not in row for row in before.values())


def _original_data(wire):
    data = deepcopy(wire)
    if 'coverage_data_projection' in data:
        marker = data.pop('coverage_data_projection')
        coverage = data['snapshot']['families']['sourced_news']['coverage']
        coverage['by_fixture_topic'] = decode_table(coverage['by_fixture_topic'])
        assert digest(data) == marker['source_data_sha256']
    if 'final_data_projection' not in data:
        return data
    table = data['exact_values']

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            if set(value) == {'__arthur_final_ref__'}:
                entry = table[value['__arthur_final_ref__']]
                original = expand(entry['value'])
                assert digest(original) == entry['source_value_sha256'] == value['__arthur_final_ref__']
                return original
            return {key: expand(item) for key, item in value.items()}
        return value

    original = {key: expand(value) for key, value in data.items()
                if key not in {'exact_values', 'final_data_projection'}}
    assert digest(original) == data['final_data_projection']['source_data_sha256']
    return original


def test_actual_source_wire_private_proofs_audit_and_legacy_inline_compatibility(tmp_path):
    _, _, _, _, snapshot, builder, proposals = inputs(owners=('kronikas',), variants=3)
    view = decision_view(snapshot)
    seed = build_source_seed(snapshot, view)
    bundle = SourceBundle.from_snapshot(snapshot, view)
    states = _states(snapshot)
    source_final = source_final_reference(snapshot, seed)
    shared = {'states': states, 'source_bundle': bundle, 'source_seed': seed,
        'source_seed_sha256': digest(seed), 'source_final_shared': source_final,
        'source_final_shared_sha256': digest(source_final), 'plan_view_sha256': digest(view),
        'final_shared_sha256': digest(final_shared_reference(snapshot, view)),
        'manifest_sha256': digest(snapshot.data()['source_manifest'])}

    def file_sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    for source_mode in (True, False):
        root = tmp_path / ('s' if source_mode else 'l')
        root.mkdir()
        bundle_path = root / 'b.json'
        bundle_path.write_text(canonical(bundle.as_dict()) + '\n', encoding='utf-8')
        calls, raw_plans = [], []

        def host(request, *, timeout, current_mode=source_mode, current_root=root, current_bundle=bundle_path,
                 current_calls=calls, current_plans=raw_plans):
            current_calls.append(deepcopy(request))
            identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
            data = _original_data(request['data'])
            if request['phase'] == 'PLAN':
                contract = data['own_construction_contract']
                if current_mode:
                    assert contract['source_catalog_policy'] == SOURCE_CATALOG_POLICY
                    assert 'table' not in contract['full_candidate_catalog']
                else:
                    assert contract == builder.construction_contract(request['agent_id'])
                    assert 'source_catalog_policy' not in contract
                answer = {**identity, 'options': deepcopy(proposals['kronikas'][:1]),
                    'fixture_reviews': [{'fixture_id': key, 'reason': 'TESTONLY full own review.',
                        'rejected': False} for key in reversed(snapshot.fixture_ids)],
                    'tool_requests': [{'role': role, 'parameters': {}} for role in SIMULATOR_ROLES]}
                reads = list(proposals['kronikas'][0]['source_candidate_ids'])
            else:
                assert data['own_plan'] == current_plans[0]
                selected = [row['option_id'] for row in data['validated_own_options']]
                assert len(selected) == 1
                answer = {**identity, 'option_ids': selected, 'reason': 'TESTONLY own supported option.',
                    'tool_interpretations': [{'request_id': pair['request']['request_id'],
                        'interpretation': 'TESTONLY UNKNOWN remains uncertainty, not zero or profit.'}
                        for pair in data['tool_results']],
                    'family_uses': [{'family': name, 'status': {'AVAILABLE': 'AVAILABLE_AND_CONSIDERED',
                        'UNKNOWN': 'MISSING', 'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}[family['status']],
                        'value_sha256': family['value_sha256'], 'option_ids': selected,
                        'reason': 'TESTONLY available evidence considered.', 'risk': 'TESTONLY optional gaps retained.'}
                        for name, family in data['snapshot']['families'].items()]}
                reads = []
            server, kernel = Path(profile_source_stdio.__file__), Path(profile_source_access.__file__)
            scope = SourceAccessScope(current_root, current_bundle, file_sha(current_bundle), identity,
                Path(sys.executable), server, file_sha(server), kernel, file_sha(kernel))
            reply = finish_fake_source_reply(scope, request, reads, answer)
            if request['phase'] == 'PLAN':
                current_plans.append(reply.reply())
            return reply

        journal = DecisionJournal(root / 'j', snapshot)
        runner = ProfileDecisionRunner(host=host, tools=ProfileToolService(snapshot),
            option_builder=_builder_without_search(builder, source=source_mode),
            journal=journal, source_bundle=bundle,
            budget=DecisionBudget(2, NOW + timedelta(seconds=120), clock=lambda: NOW))
        owner = runner._owner(states['kronikas'], snapshot, view, seed)
        assert owner['status'] == 'DECISION_COMPLETED'
        before = deepcopy(owner)
        evidence = owner['decision_evidence']
        assert evidence['plan'] == raw_plans[0]
        assert evidence['source_access']['PLAN']['reply_sha256'] == digest(evidence['plan'])
        checked = _owner_evidence(owner, snapshot, shared)
        assert checked['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
        assert checked['declared_fixture_count'] == 12 and checked['tool_count'] == 2
        assert checked['family_count'] == 12 and len(owner['decision']['selected_options']) == 1
        assert owner == before and [call['phase'] for call in calls] == ['PLAN', 'FINAL']
        if source_mode:
            forged = deepcopy(owner)
            auxiliary = forged['decision_evidence']['plan_auxiliary_inputs']
            catalog = auxiliary['values']['own_construction_contract']['full_candidate_catalog']
            catalog['query_sources']['candidate_input']['path'][-1] = 'FOREIGN'
            auxiliary['sha256'] = digest(auxiliary['values'])
            with pytest.raises(ValueError, match='SOURCE_CATALOG_REFERENCE_CHANGED'):
                _owner_evidence(forged, snapshot, shared)
        else:
            assert [row['fixture_id'] for row in evidence['plan']['fixture_reviews']] == list(
                reversed(snapshot.fixture_ids))
