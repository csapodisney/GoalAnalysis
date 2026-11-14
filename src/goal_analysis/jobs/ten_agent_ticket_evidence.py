"""Pure audit of exact owner output; no new reasoning, simulation or source I/O.

Correspondence to caller-supplied envelopes is not physical source authentication,
causal feature influence, calibrated probability, historical PIT or profitability.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

from goal_analysis.agents.profile_decision import (
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
    expand_fixture_reviews,
    validate_fixture_review_contract,
)
from goal_analysis.agents.profile_input import (
    FINAL_REQUEST_PROJECTION,
    FINAL_REQUEST_PROJECTION_V1,
    FINAL_REQUEST_PROJECTION_V2,
    NEWS_COVERAGE_REQUEST_PROJECTION,
    SOURCE_DETAILS_POLICY,
    SOURCE_DETAILS_POLICY_V1,
    decision_view,
    final_option_details,
    final_request_data,
    final_shared_reference,
    news_coverage_request_data,
    public_tool_results,
)
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.agents.profile_tools import VERSION as TOOL_VERSION
from goal_analysis.generated_tickets import _materialize
from goal_analysis.jobs.agent_portfolio import (
    own_option_admission_partition,
    validate_source_catalog_contract,
)
from goal_analysis.jobs.agent_snapshot import (
    FAMILIES,
    SharedSnapshot,
    digest,
    identifiers,
    json_text,
    require,
)

VERSION = 'ten-agent-ticket-evidence-v2-shared-cohort'


def _unknown(reason, error=None):
    return {'status': 'UNKNOWN', 'reason': reason,
            **({'error_type': type(error).__name__} if error is not None else {})}


def _plan_auxiliary_inputs(evidence, state, snapshot):
    """Reconstruct only saved exact own PLAN fields; never infer missing old inputs."""
    envelope = evidence.get('plan_auxiliary_inputs')
    if envelope is None:
        return {}  # Historical protocol remains historical, including original gaps.
    require(isinstance(envelope, dict) and set(envelope) == {'version', 'values', 'sha256'}
            and envelope['version'] == 'owner-plan-auxiliary-inputs-v1'
            and isinstance(envelope['values'], dict)
            and set(envelope['values']) <= {'own_construction_contract', 'own_strategy_search',
                                            'fixture_review_contract'}
            and digest(envelope['values']) == envelope['sha256'], 'SAVED_PLAN_AUXILIARY_INPUTS_CHANGED')
    values = envelope['values']
    data = snapshot.data()
    if 'fixture_review_contract' in values:
        validate_fixture_review_contract(values['fixture_review_contract'], list(snapshot.fixture_ids))
    contract = values.get('own_construction_contract')
    if contract is not None:
        require(isinstance(contract, dict) and contract.get('agent_id') == state.agent_id
                and contract.get('method_version') == state.method_version
                and contract.get('run_id') == data['run_id'] and contract.get('target_date') == data['target_date']
                and contract.get('source_as_of') == data['as_of'], 'FOREIGN_SAVED_CONSTRUCTION_CONTRACT')
        validate_source_catalog_contract(contract, snapshot.snapshot_id,
            data['families']['odds_and_price_history'].get('value'),
            source_access=evidence.get('version') == 'owner-decision-evidence-v3-source-access')
    search = values.get('own_strategy_search')
    if search is not None:
        require(isinstance(search, dict) and search.get('agent_id') == state.agent_id
                and search.get('session_id') == state.session_id and search.get('snapshot_id') == snapshot.snapshot_id
                and search.get('version') == 'own-strategy-search-plan-seeds-v1',
                'FOREIGN_SAVED_STRATEGY_SEARCH_INPUT')
    return deepcopy(values)


def _owner_evidence(owner, snapshot, shared):
    """Rebind saved calls using the original pure contracts; never call a tool."""
    require(owner.get('status') == 'DECISION_COMPLETED', 'OWNER_DECISION_INCOMPLETE')
    evidence = owner.get('decision_evidence')
    require(isinstance(evidence, dict), 'OWN_REQUEST_REPLY_ENVELOPE_NOT_SAVED')
    product_policy = shared.get('ticket_product_contract')
    if product_policy is not None:
        from goal_analysis.ticket_product_policy import report_contract, validate_option_product

        auxiliary = evidence['plan_auxiliary_inputs']['values']['own_construction_contract']
        require(report_contract(auxiliary) == product_policy, 'OWN_PLAN_PRODUCT_CONTRACT_CHANGED')
        for option in evidence['validated_own_options']:
            require(report_contract(option['canonical_card']) == product_policy,
                    'SAVED_OPTION_PRODUCT_CONTRACT_CHANGED')
            validate_option_product(option)
    coverage_projection = evidence.get('coverage_request_projection')
    require(coverage_projection in {None, NEWS_COVERAGE_REQUEST_PROJECTION}, 'UNKNOWN_NEWS_COVERAGE_PROJECTION')
    projection = evidence.get('final_request_projection')
    require(projection in {None, FINAL_REQUEST_PROJECTION_V1, FINAL_REQUEST_PROJECTION_V2, FINAL_REQUEST_PROJECTION}, 'UNKNOWN_FINAL_REQUEST_PROJECTION')
    source_policy = evidence.get('source_details_policy')
    require(source_policy in {None, SOURCE_DETAILS_POLICY_V1, SOURCE_DETAILS_POLICY}, 'UNKNOWN_SOURCE_DETAILS_POLICY')
    source_mode = evidence.get('version') == 'owner-decision-evidence-v3-source-access'
    require(source_policy is None or source_mode, 'SOURCE_DETAILS_REQUIRE_SOURCE_ACCESS')
    require(evidence.get('version') in {'owner-decision-evidence-v2-compact-views',
                                      'owner-decision-evidence-v3-source-access'}
            and evidence.get('snapshot_id') == snapshot.snapshot_id, 'OWN_INPUT_VIEW_BINDING_CHANGED')
    state = shared['states'][owner['agent_id']]
    plan, final, options = evidence['plan'], evidence['final'], evidence['validated_own_options']
    details = final_option_details(snapshot, owner['agent_id'], options,
        source_access=source_policy is not None, source_policy=source_policy)
    proposals = plan['options']
    admission = evidence.get('own_option_admission')
    if admission is not None:
        require(isinstance(admission, dict) and admission == own_option_admission_partition(
            snapshot, owner['agent_id'], proposals, options, admission['rejected_options']),
            'SAVED_OWN_ADMISSION_PARTITION_CHANGED')
    else:
        # Earlier all-accepted envelopes retain their original exact contract.
        require(len(proposals) == len(options), 'UNRECORDED_OWN_PROPOSAL_REJECTION')
    require(evidence.get('input_views') == {
        'PLAN': {'kind': ('profile_source_access.build_source_seed' if source_mode
                         else 'profile_input.decision_view'),
                 'sha256': shared['source_seed_sha256'] if source_mode else shared['plan_view_sha256']},
        'FINAL': {'kind': ('profile_decision_runner.source_final_reference+own_details' if source_mode
                           else 'profile_input.final_shared_reference+own_details'),
            'shared_sha256': shared['source_final_shared_sha256'] if source_mode else shared['final_shared_sha256'],
            'own_details_sha256': digest(details),
            'public_tools_sha256': digest(public_tool_results(evidence['tool_results'])),
            **({'admission_sha256': digest(admission)} if admission is not None else {})}},
        'OWN_PLAN_OR_FINAL_INPUT_BINDING_CHANGED')
    for reply, phase in ((plan, 'PLAN'), (final, 'FINAL')):
        require(reply.get('phase') == phase and all(reply.get(key) == getattr(state, key)
                for key in ('agent_id', 'session_id', 'snapshot_id')), 'FOREIGN_SAVED_HOST_REPLY')
    saved_auxiliary = evidence.get('plan_auxiliary_inputs')
    grouped_declared = (isinstance(saved_auxiliary, dict)
        and isinstance(saved_auxiliary.get('values'), dict)
        and 'fixture_review_contract' in saved_auxiliary['values'])
    plan_auxiliary = (_plan_auxiliary_inputs(evidence, state, snapshot)
                      if source_mode or grouped_declared else {})
    source_checks = None
    if source_mode:
        from goal_analysis.agents.profile_source_access import require_candidate_reads
        from goal_analysis.agents.profile_source_host import validate_source_evidence

        proofs = evidence.get('source_access')
        require(isinstance(proofs, dict) and set(proofs) == {'PLAN', 'FINAL'},
                'BOTH_VERIFIED_SOURCE_PHASE_PROOFS_REQUIRED')
        phase_data = {
            'PLAN': {'snapshot': shared['source_seed'],
                     **plan_auxiliary},
            'FINAL': {'snapshot': shared['source_final_shared'], 'own_plan': plan,
                'validated_own_options': options,
                'tool_results': public_tool_results(evidence['tool_results']),
                'own_source_details': details,
                'own_option_admission': admission},
        }
        if projection is not None:
            phase_data['FINAL'] = final_request_data(phase_data['FINAL'], projection_policy=projection)
        if coverage_projection == NEWS_COVERAGE_REQUEST_PROJECTION:
            phase_data = {phase: news_coverage_request_data(value) for phase, value in phase_data.items()}
        source_checks = {}
        for phase, reply in (('PLAN', plan), ('FINAL', final)):
            request = {**{key: getattr(state, key) for key in ('agent_id', 'session_id', 'snapshot_id')},
                'phase': phase, 'persona': get_agent_definition(state.agent_id,
                    decision_group=snapshot.decision_data().get('decision_group', 'CORE_TEN')).as_dict(),
                'data': phase_data[phase]}
            source_checks[phase] = validate_source_evidence(proofs[phase], request=request,
                reply=reply, bundle=shared['source_bundle'])
        selected_sources = []
        for proposal in proposals:
            for identifier in identifiers(proposal.get('source_candidate_ids')):
                if identifier not in selected_sources:
                    selected_sources.append(identifier)
        source_checks['proposed_candidates'] = require_candidate_reads(
            proofs['PLAN']['journal_records'], candidate_ids=selected_sources, bundle=shared['source_bundle'])
    identifiers([row['option_id'] for row in proposals])
    require(len(proposals) <= 6 and len(options) <= len(proposals), 'OWN_OPTIONS_ENVELOPE_CHANGED')
    accepted_ids = {row['option_id'] for row in options}
    accepted_proposals = [row for row in proposals if row['option_id'] in accepted_ids]
    require(len(accepted_proposals) == len(options), 'OWN_OPTIONS_ENVELOPE_CHANGED')
    for proposal, option in zip(accepted_proposals, options, strict=True):
        binding = option['canonical_card']['agent_product_binding']
        require(proposal['option_id'] == option['option_id'] and proposal['reason'] == option['reason']
                and proposal['source_candidate_ids'] == binding['source_candidate_ids'],
                'OWN_PROPOSAL_PRODUCT_CHANGED')
    state = set_options(state, snapshot, options)
    reviews = expand_fixture_reviews(plan['fixture_reviews'], list(snapshot.fixture_ids),
        contract=plan_auxiliary.get('fixture_review_contract'))
    require(set(identifiers([row['fixture_id'] for row in reviews])) == set(snapshot.fixture_ids),
            'SAVED_FULL_CALENDAR_REVIEW_CHANGED')
    for row in reviews:
        state = consider_fixture(state, snapshot, row['fixture_id'], reason=row['reason'], rejected=row['rejected'])
    commands, pairs = plan['tool_requests'], evidence['tool_results']
    require(len(commands) == len(pairs) == len(SIMULATOR_ROLES)
            and {row['role'] for row in commands} == set(SIMULATOR_ROLES), 'BOTH_SAVED_TOOL_REQUESTS_REQUIRED')
    interpretations = final['tool_interpretations']
    identifiers([row['request_id'] for row in interpretations])
    explanations = {row['request_id']: row['interpretation'] for row in interpretations}
    require(len(explanations) == len(pairs), 'TOOL_INTERPRETATION_COHORT_CHANGED')
    for command, pair in zip(commands, pairs, strict=True):
        state, request = request_tool(state, snapshot, command['role'], parameters=command.get('parameters'))
        require(request.data() == pair['request'], 'SAVED_TOOL_REQUEST_CHANGED')
        tool = pair['result']
        if tool['status'] in {'AVAILABLE', 'CACHED'}:
            require(tool.get('tool_version') == TOOL_VERSION and tool['result_id'] == digest({
                'request': pair['request'], 'value': tool['value'],
                'bindings': tool['source_bindings'], 'version': TOOL_VERSION}), 'SAVED_TOOL_RESULT_ID_CHANGED')
            manifest_hash = tool['source_bindings'].get('source_manifest_sha256')
            require(manifest_hash is None or manifest_hash == shared['manifest_sha256'],
                    'TOOL_SOURCE_MANIFEST_CHANGED')
        state = record_tool_result(state, snapshot, request, pair['result'],
                                   interpretation=explanations[request.data()['request_id']])
    uses = final['family_uses']
    require(set(identifiers([row['family'] for row in uses])) == set(FAMILIES), 'ALL_TWELVE_FAMILY_USES_REQUIRED')
    for row in uses:
        state = consider_family(state, snapshot, row['family'], status=row['status'], reason=row['reason'],
            value_sha256=row['value_sha256'], option_ids=row.get('option_ids', ()), risk=row.get('risk'))
    _, decision = finalize_decisions(state, snapshot, option_ids=final['option_ids'], reason=final['reason'])
    require(decision.data() == owner['decision'], 'SAVED_FINAL_DECISION_CHANGED')
    return {'status': 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND', 'envelope_sha256': digest(evidence),
            'declared_fixture_count': len(reviews), 'family_count': len(uses),
            'tool_count': len(pairs), 'host_provenance': 'PRODUCER_ENVELOPE_NOT_INDEPENDENT_AUTHENTICATION',
            'own_option_admission': deepcopy(admission),
            **({'source_access_validation': source_checks,
                'source_read_semantics': 'VERIFIED_RETURNED_SOURCE_PAGES_NOT_ALL_ROWS_OR_CAUSAL_INFLUENCE'}
               if source_mode else {}),
            'admission_reason_validation': 'PRODUCER_REPORTED_GATE_WITH_SOURCE_AND_PARTITION_BINDING',
            'family_declarations': deepcopy(owner['decision']['family_uses']),
            'tool_interpretations': [{key: deepcopy(receipt.get(key)) for key in (
                'role', 'status', 'reason', 'own_option_ids', 'result_id', 'result_sha256',
                'source_refs', 'interpretation')} for receipt in owner['decision']['tool_receipts']],
            'consideration_semantics': 'MODEL_DECLARED_NOT_CAUSAL_FEATURE_INFLUENCE'}


def _canonical_card(card, owner, report, snapshot_id, candidate_input_sha256, *,
                    source_group='ten_agent_decisions'):
    decision, binding = owner['decision'], card['agent_decision']
    matching = [row for row in decision['selected_options'] if row['option_id'] == binding['option_id']]
    require(len(matching) == 1, 'SELECTED_OWN_OPTION_NOT_UNIQUE')
    option = matching[0]
    source, product = option['canonical_card'], option['canonical_card']['agent_product_binding']
    exact_legs = [{key: value for key, value in leg.items()
                   if key not in {'model_probability', 'model_probability_id'}} for leg in source['legs']]
    require(option['canonical_card_sha256'] == digest(source) and product['snapshot_id'] == snapshot_id
            and option['snapshot_id'] == snapshot_id and option['agent_id'] == owner['agent_id']
            and option['legs'] == exact_legs
            and product['agent_id'] == owner['agent_id'] == card['profile_id']
            and product['option_id'] == option['option_id'] and product['run_id'] == report['run_id']
            and product['target_date'] == report['date']
            and product['source_candidate_input_sha256'] == candidate_input_sha256,
            'OWN_CANONICAL_SOURCE_BINDING_CHANGED')
    expected = {'agent_id': owner['agent_id'], 'session_id': decision['session_id'],
        'snapshot_id': snapshot_id, 'method_version': decision['method_version'],
        'option_id': option['option_id'], 'decision_sha256': digest(decision),
        'canonical_source_card_sha256': option['canonical_card_sha256'],
        'reason': decision.get('reason'), 'option_reason': option.get('reason'),
        'family_uses': decision.get('family_uses'), 'tool_receipts': decision.get('tool_receipts'),
        'selection_authority': 'OWN_FINAL_DECISION_NO_COORDINATOR_RESELECTION'}
    require(binding == expected and card == {
        **_materialize(source, source_group, report, report.get('team', {})),
        'agent_decision': expected}, 'PUBLISHED_OWN_CARD_OR_DECISION_CHANGED')
    return {'status': 'EXACT_OWN_CANONICAL_SOURCE_BOUND', 'option_id': option['option_id'],
            'canonical_source_card_sha256': option['canonical_card_sha256']}


def _legacy_card(report, artifacts, card, index):
    """Reuse existing joins per card; one corrupt optional input stays local."""
    from goal_analysis.jobs.ticket_evidence import _build_ticket_evidence

    bound = _build_ticket_evidence({**report, 'generated_tickets': [card]}, artifacts,
                                   groups=('generated_tickets',))
    remap, records = {}, {}
    for key, record in bound['sources'].items():
        record = deepcopy(record)
        if record['root'] == 'report' and record['path'][:2] == ['generated_tickets', 0]:
            record['path'][1] = index
        identity = {name: record[name] for name in ('root', 'path', 'sha256')}
        remap[key] = digest(identity)
        records[remap[key]] = record

    def relocate(value):
        if isinstance(value, dict):
            return {key: relocate(child) for key, child in value.items()}
        if isinstance(value, list):
            return [relocate(child) for child in value]
        return remap.get(value, value) if isinstance(value, str) else value

    entry = relocate(bound['cards'][0])
    entry['location'] = ['generated_tickets', index]
    return entry, records


def _source_context(data):
    """Check shared values once; malformed optional sources stay local UNKNOWN."""
    manifests, models, families, checked_refs = data['source_manifest'], {}, {}, {}
    for key, record in data['model_probabilities'].items():
        try:
            rows = []
            for reference in record['source_refs']:
                if reference not in checked_refs:
                    source = manifests[reference]
                    value = source.get('producer_value')
                    require(value is not None and digest(value) == source['canonical_value_sha256'],
                            'ADMITTED_MODEL_SOURCE_ENVELOPE_CHANGED')
                    checked_refs[reference] = {'source_ref': reference, 'producer_path': source['producer_path'],
                                              'value_sha256': source['canonical_value_sha256']}
                rows.append(deepcopy(checked_refs[reference]))
            link = {'probability_id': key, 'record': deepcopy(record), 'sources': rows}
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            link = {'probability_id': key, **_unknown(str(error), error)}
        fixture = record.get('fixture_id') if isinstance(record, dict) else None
        models.setdefault(fixture, []).append(link)
    for name in FAMILIES:
        try:
            family = data['families'][name]
            require(family['value_sha256'] == digest(family.get('value')), 'SNAPSHOT_FAMILY_HASH_CHANGED')
            require(all(reference in manifests for reference in family['source_refs']), 'UNRESOLVED_FAMILY_SOURCE')
            families[name] = {'status': family['status'], 'value_sha256': family['value_sha256'],
                'source_refs': list(family['source_refs']), 'reason': family.get('reason'),
                'source_binding': 'SHARED_VALUE_HASH_AND_REFERENCES_BOUND'}
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            families[name] = _unknown(str(error), error)
    return {'models_by_fixture': models, 'families': families}


def _snapshot_links(card, data, sources):
    selected = {leg['fixture_id'] for leg in card['legs']}
    model_links = [deepcopy(row) for fixture, rows in sources['models_by_fixture'].items()
                   if fixture in selected for row in rows]
    families = {}
    for name in FAMILIES:
        header = sources['families'][name]
        if header.get('source_binding') != 'SHARED_VALUE_HASH_AND_REFERENCES_BOUND':
            families[name] = deepcopy(header)
            continue
        family = data['families'][name]
        value, fixture_values = family.get('value'), {}
        if isinstance(value, dict):
            for field in ('categories', 'by_fixture'):
                rows = value.get(field)
                if isinstance(rows, dict):
                    fixture_values[field] = {key: deepcopy(rows[key]) for key in selected if key in rows}
            if name == 'sourced_news':
                envelopes = value.get('envelopes')
                if isinstance(envelopes, list):
                    fixture_values['envelopes'] = [deepcopy(row) for row in envelopes
                        if isinstance(row, dict) and row.get('fixture_id') in selected]
                elif envelopes is not None:
                    fixture_values['envelopes'] = _unknown('MALFORMED_OPTIONAL_NEWS_ENVELOPES')
        families[name] = {**deepcopy(header),
            'selected_fixture_values': fixture_values,
            'usage_semantics': 'SOURCE_AVAILABLE_OR_UNKNOWN_SEPARATE_FROM_OWNER_DECLARATION'}
    return {'families': families, 'admitted_selected_fixture_models': model_links,
        'training_dataset_binding': 'EXACT_PRODUCER_METADATA_NOT_PHYSICAL_DATASET_VALIDATION',
        'selected_numeric_influence': 'NOT_ESTABLISHED_BY_AVAILABILITY_OR_HOST_ASSERTION',
        'weather_strict_reader_validation': 'NOT_PERFORMED_BY_THIS_PURE_AUDIT',
        'historical_pit_or_profit_certification': False}


def _companion_evidence_context(report, artifacts, core_snapshot):
    """Derive the exact fixed pair and reuse original source/decision contracts."""
    from goal_analysis.agents.profile_decision_runner import source_final_reference
    from goal_analysis.agents.profile_roster import COMPANION_AGENT_IDS, COMPANION_DECISION_VERSION
    from goal_analysis.agents.profile_source_access import SourceBundle, build_source_seed
    from goal_analysis.companion_agents import (
        pair_snapshot_metadata,
        restore_companion_snapshot,
        shared_data_binding,
        validate_companion_agent_cards,
    )
    from goal_analysis.ticket_product_policy import report_contract

    run = report['companion_agent_decisions']
    require(run == artifacts['companion_agent_decisions']
            and run.get('version') == COMPANION_DECISION_VERSION
            and run.get('agent_count') == 2 and run.get('journal_subdirectory') == 'companion-agents'
            and 'companion_ticket_channels' not in report, 'SAVED_PAIR_RUN_CHANGED')
    raw_run = {key: value for key, value in run.items()
               if key not in {'runner_result_sha256', 'shared_data_binding', 'journal_subdirectory'}}
    require(digest(raw_run) == run.get('runner_result_sha256'), 'SAVED_PAIR_RUNNER_HASH_CHANGED')
    snapshot = restore_companion_snapshot(core_snapshot, artifacts['companion_agent_snapshot'])
    binding = shared_data_binding(core_snapshot, snapshot)
    require(run.get('snapshot_id') == snapshot.snapshot_id and run.get('shared_data_binding') == binding
            and report.get('companion_agent_snapshot_metadata') == pair_snapshot_metadata(binding),
            'SAVED_PAIR_SHARED_DATA_CHANGED')
    owners = run['owners']
    require(isinstance(owners, list) and [row.get('agent_id') for row in owners] == list(COMPANION_AGENT_IDS),
            'EXACT_PAIR_SAVED_OWNERS_REQUIRED')
    require(all(owner.get('status') != 'DECISION_COMPLETED'
                or owner.get('decision_evidence', {}).get('version') == 'owner-decision-evidence-v3-source-access'
                for owner in owners), 'PAIR_REQUIRES_NATIVE_SOURCE_PHASES')
    validate_companion_agent_cards(report)
    data, view = snapshot.data(), decision_view(snapshot)
    source_bundle = SourceBundle.from_snapshot(snapshot, view)
    seed = build_source_seed(snapshot, view)
    final_shared = final_shared_reference(snapshot, view)
    source_final = source_final_reference(snapshot, seed)
    storage = artifacts['companion_agent_source_storage']
    require(isinstance(storage, dict) and set(storage) == {
                'version', 'snapshot_id', 'view_sha256', 'bundle_sha256', 'bundle_relative_path'}
            and storage['version'] == 'companion-source-storage-reference-v1'
            and storage['snapshot_id'] == snapshot.snapshot_id and storage['view_sha256'] == digest(view)
            and storage['bundle_relative_path'] == 'companion-agents/source-access/bundle.json'
            and isinstance(storage['bundle_sha256'], str) and len(storage['bundle_sha256']) == 64
            and all(c in '0123456789abcdef' for c in storage['bundle_sha256']), 'PAIR_SOURCE_STORAGE_REFERENCE_CHANGED')
    shared = {'plan_view_sha256': digest(view), 'final_shared': final_shared,
        'final_shared_sha256': digest(final_shared), 'manifest_sha256': digest(data['source_manifest']),
        'candidate_input_sha256': digest(artifacts['candidate_input']),
        'source_bundle': source_bundle, 'source_seed': seed, 'source_seed_sha256': digest(seed),
        'source_final_shared': source_final, 'source_final_shared_sha256': digest(source_final),
        'ticket_product_contract': report_contract(report),
        'states': {state.agent_id: state for state in start_sessions(snapshot,
            {key: get_agent_definition(key, decision_group='COMPANION_PAIR').method_version
             for key in COMPANION_AGENT_IDS})}}
    return snapshot, shared, owners, {'status': 'SAVED_COMPANION_SNAPSHOT_BOUND',
        'snapshot_id': snapshot.snapshot_id, 'core_snapshot_id': core_snapshot.snapshot_id,
        'view_sha256': digest(view), 'fixture_count': len(snapshot.fixture_ids),
        'shared_data_binding': binding, 'source_storage': deepcopy(storage)}


def build_ten_agent_ticket_evidence(report, artifacts):
    cards = report.get('generated_tickets')
    require(isinstance(cards, list), 'CANONICAL_GENERATED_COHORT_REQUIRED')
    result = {'version': VERSION, 'mode': 'AUDIT_ONLY', 'decision_effect': 'NONE',
        'run_id': report.get('run_id'), 'date': report.get('date'),
        'as_of': report.get('finished_at', report.get('observed_at')), 'status': 'COMPLETE',
        'coverage_claim': 'COHORT_AND_BINDINGS_ONLY_NOT_ALL_DATA_AVAILABLE_OR_CAUSALLY_USED',
        'source_boundary': 'SUPPLIED_IMMUTABLE_ENVELOPES_NOT_PHYSICAL_AUTHENTICATION',
        'external_requests': 0, 'new_tool_or_model_executions': 0, 'cards': [], 'sources': {},
        'owner_checks': {}, 'snapshot_binding': None}
    data = snapshot = shared = source_context = None
    try:
        run = report['ten_agent_decisions']
        saved = artifacts['independent_agent_decisions']
        require({**saved, 'canonical_ticket_count': run['canonical_ticket_count']} == run,
                'SAVED_OWNER_RESULT_CHANGED')
        snapshot = SharedSnapshot(run['snapshot_id'], json_text(artifacts['independent_agent_snapshot']))
        data = snapshot.data()
        require(data['run_id'] == report['run_id'] and data['target_date'] == report['date'],
                'SNAPSHOT_RUN_OR_DATE_CHANGED')
        odds = data['families']['odds_and_price_history']['value']
        models = data['families']['numeric_models']['value']
        require(isinstance(odds, dict) and odds.get('candidate_input') == artifacts.get('candidate_input'),
                'SNAPSHOT_PRICE_INPUT_CHANGED')
        if isinstance(models, dict):
            require(models.get('results') == artifacts.get('numeric_models', {}), 'SNAPSHOT_MODEL_INPUT_CHANGED')
        owners = run['owners']
        require(set(identifiers([row['agent_id'] for row in owners])) == set(AGENT_IDS), 'EXACT_TEN_OWNERS_REQUIRED')
        view = decision_view(snapshot)
        view_hash = digest(view)
        final_shared = final_shared_reference(snapshot, view)
        shared = {'plan_view_sha256': view_hash,
            'final_shared_sha256': digest(final_shared), 'final_shared': final_shared,
            'manifest_sha256': digest(data['source_manifest']),
            'candidate_input_sha256': digest(artifacts['candidate_input']),
            'states': {state.agent_id: state for state in start_sessions(snapshot,
                {key: get_agent_definition(key).method_version for key in AGENT_IDS})}}
        if any(isinstance(owner.get('decision_evidence'), dict)
               and owner['decision_evidence'].get('version') == 'owner-decision-evidence-v3-source-access'
               for owner in owners):
            from goal_analysis.agents.profile_decision_runner import source_final_reference
            from goal_analysis.agents.profile_source_access import SourceBundle, build_source_seed

            shared['source_bundle'] = SourceBundle.from_snapshot(snapshot, view)
            shared['source_seed'] = build_source_seed(snapshot, view)
            shared['source_seed_sha256'] = digest(shared['source_seed'])
            shared['source_final_shared'] = source_final_reference(snapshot, shared['source_seed'])
            shared['source_final_shared_sha256'] = digest(shared['source_final_shared'])
        source_context = _source_context(data)
        result['snapshot_binding'] = {'status': 'SAVED_SNAPSHOT_BOUND', 'snapshot_id': snapshot.snapshot_id,
            'view_sha256': view_hash, 'fixture_count': len(data['fixture_ids'])}
        from goal_analysis.ticket_product_policy import report_contract

        shared['ticket_product_contract'] = report_contract(report)
        for owner_index, owner in enumerate(owners):
            try:
                result['owner_checks'][owner['agent_id']] = _owner_evidence(owner, snapshot, shared)
                result['owner_checks'][owner['agent_id']].update(
                    source_location=['independent_agent_decisions', 'owners', owner_index, 'decision_evidence'],
                    decision_sha256=digest(owner['decision']))
            except (KeyError, TypeError, ValueError, AttributeError, StopIteration) as error:
                result['owner_checks'][owner['agent_id']] = _unknown(str(error), error)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        data = snapshot = shared = source_context = None
        result['snapshot_binding'] = _unknown(str(error), error)
    pair_snapshot = pair_shared = None
    pair_owners = []
    if 'companion_agent_decisions' in report:
        try:
            require(snapshot is not None, 'CORE_SNAPSHOT_REQUIRED_FOR_PAIR')
            pair_snapshot, pair_shared, pair_owners, pair_binding = _companion_evidence_context(
                report, artifacts, snapshot)
            result['companion_snapshot_binding'] = pair_binding
            for owner_index, owner in enumerate(pair_owners):
                try:
                    result['owner_checks'][owner['agent_id']] = _owner_evidence(owner, pair_snapshot, pair_shared)
                    result['owner_checks'][owner['agent_id']].update(
                        source_location=['companion_agent_decisions', 'owners', owner_index, 'decision_evidence'],
                        decision_sha256=digest(owner['decision']))
                except (KeyError, TypeError, ValueError, AttributeError, StopIteration) as error:
                    result['owner_checks'][owner['agent_id']] = _unknown(str(error), error)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            pair_snapshot = pair_shared = None
            result['companion_snapshot_binding'] = _unknown(str(error), error)
    legacy_cohort = None
    try:
        from goal_analysis.jobs.ticket_evidence import _build_ticket_evidence

        legacy_cohort = _build_ticket_evidence(report, artifacts, groups=('generated_tickets',))
        require(len(legacy_cohort['cards']) == len(cards) and all(
            row['location'] == ['generated_tickets', index] and row['card_sha256'] == digest(card)
            for index, (row, card) in enumerate(zip(legacy_cohort['cards'], cards, strict=True))),
            'WHOLE_CANONICAL_JOIN_COHORT_CHANGED')
        result['sources'].update(legacy_cohort['sources'])
        result['legacy_source_join_mode'] = 'ONE_FULL_CANONICAL_COHORT'
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        legacy_cohort = None
        result['legacy_source_join_mode'] = 'PER_CARD_FALLBACK_AFTER_SHARED_JOIN_ERROR'
        result['shared_source_join_error'] = _unknown(str(error), error)
    counts = Counter(card['ticket_id'] for card in cards if isinstance(card, dict)
                     and isinstance(card.get('ticket_id'), str))
    for index, card in enumerate(cards):
        entry = {'location': ['generated_tickets', index], 'card_sha256': digest(card),
                 'ticket_id': card.get('ticket_id') if isinstance(card, dict) else None}
        try:
            if legacy_cohort is not None:
                entry.update(deepcopy(legacy_cohort['cards'][index]))
            else:
                legacy, sources = _legacy_card(report, artifacts, card, index)
                entry.update(legacy)
                result['sources'].update(sources)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            entry['source_joins'] = _unknown(str(error), error)
        try:
            require(snapshot is not None, 'SAVED_SNAPSHOT_NOT_BOUND')
            require(isinstance(card, dict) and isinstance(card.get('ticket_id'), str)
                    and counts[card['ticket_id']] == 1, 'UNIQUE_CANONICAL_TICKET_ID_REQUIRED')
            if 'channel_id' in card and 'companion_agent_decisions' in report:
                require(pair_snapshot is not None, 'SAVED_COMPANION_SNAPSHOT_NOT_BOUND')
                owner = next(row for row in pair_owners if row['agent_id'] == card['channel_id'])
                entry['own_selection'] = _canonical_card(card, owner, report, pair_snapshot.snapshot_id,
                    pair_shared['candidate_input_sha256'], source_group='companion_agent_decisions')
                entry['owner_evidence'] = result['owner_checks'][owner['agent_id']]
            elif 'channel_id' in card:
                from goal_analysis.companion_tickets import validate_companion_cards

                require(card['ticket_id'] in validate_companion_cards(report), 'COMPANION_BINDING_NOT_FOUND')
                produced = artifacts['companion_producer_results'][card['channel_id']]['report']
                channel = next(row for row in report['companion_ticket_channels']['channels']
                               if row['channel_id'] == card['channel_id'])
                require(digest(produced) == card['companion_decision']['producer_report_sha256']
                        and produced == channel['producer_report'], 'COMPANION_PRODUCER_RESULT_CHANGED')
                actual_inputs = {'candidate_input': digest(artifacts['candidate_input']),
                    'history': digest(artifacts['history']),
                    'enriched': digest(data['families']['odds_and_price_history']['value']['enriched']),
                    'settings': digest(report['settings'])}
                chosen_ids = {leg['candidate_id'] for leg in card['legs']}
                actual_selected_rows = [row for row in artifacts['candidate_input']['candidates']
                                        if row['candidate_id'] in chosen_ids]
                require(channel['input_hashes'] == actual_inputs
                        and channel['calendar_ids'] == data['fixture_ids']
                        and channel['selected_source_rows'] == actual_selected_rows,
                        'COMPANION_SHARED_INPUT_CHANGED')
                entry['own_selection'] = {'status': 'BOUND_PROTECTED_CONSTRUCTOR_PRODUCT',
                    'channel_id': card['channel_id'], 'not_agent_reasoning': True}
                entry['owner_evidence'] = {'status': 'NOT_APPLICABLE_PROTECTED_CONSTRUCTOR',
                    'all_families_causally_used': False, 'model_or_simulator_use': 'NOT_EVALUATED'}
            else:
                owner = next(row for row in report['ten_agent_decisions']['owners'] if row['agent_id'] == card['profile_id'])
                entry['own_selection'] = _canonical_card(card, owner, report, snapshot.snapshot_id,
                                                          shared['candidate_input_sha256'])
                entry['owner_evidence'] = result['owner_checks'][owner['agent_id']]
            entry['snapshot_sources'] = _snapshot_links(card, data, source_context)
        except (KeyError, TypeError, ValueError, AttributeError, StopIteration) as error:
            entry['own_selection'] = _unknown(str(error), error)
        result['cards'].append(entry)
    result['accounting'] = {'canonical_card_count': len(cards), 'audited_card_count': len(result['cards']),
        'canonical_cohort_sha256': digest(cards), 'all_card_locations_accounted': True,
        'legacy_compatibility_copies_counted': False}
    if snapshot is None or any(row.get('own_selection', {}).get('status') == 'UNKNOWN'
            or row.get('owner_evidence', {}).get('status') == 'UNKNOWN'
            or 'source_joins' in row for row in result['cards']):
        result['status'] = 'PARTIAL'
    if 'companion_agent_decisions' in report and (pair_snapshot is None or any(
            result['owner_checks'].get(owner['agent_id'], {}).get('status')
            != 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND' for owner in pair_owners)):
        result['status'] = 'PARTIAL'
    result['bundle_sha256'] = digest(result)
    return result


def attach_ten_agent_ticket_evidence(report, artifacts):
    """One additive report field; no card mutation and no audit-driven retry."""
    try:
        report['ticket_evidence'] = build_ten_agent_ticket_evidence(report, artifacts)
    except Exception as error:  # noqa: BLE001 - optional audit failure must not erase valid own cards.
        report['ticket_evidence'] = {'version': VERSION, 'mode': 'AUDIT_ONLY', 'decision_effect': 'NONE',
            **_unknown('TEN_AGENT_AUDIT_FAILED', error),
            'canonical_card_count': len(report.get('generated_tickets', [])),
            'all_card_locations_accounted': False}
    return report['ticket_evidence']
