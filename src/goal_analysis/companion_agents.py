"""Two fixed reasoning owners using the original Daily and Legacy pure methods.

This module constructs proposed products and validates supplied saved decisions.
It never invokes a provider, model, database or the protected companion publisher.
Physical source/native evidence is independently checked by the saved verifier.
"""
import json
from copy import deepcopy
from datetime import datetime

from goal_analysis.agents.profile_roster import (
    AGENT_IDS,
    COMPANION_AGENT_IDS,
    COMPANION_DECISION_VERSION,
    get_agent_definition,
)
from goal_analysis.jobs.agent_snapshot import FAMILIES, SharedSnapshot, digest, identifiers, require
from goal_analysis.ticket_product_policy import report_contract

GROUP = 'COMPANION_PAIR'
OWNER_VERSION = 'profile-owner-decision-v1-companion-pair'
CAPACITY_VERSION = 'TWELVE_INDEPENDENT_AGENT_SLOTS_V2'
JOURNAL_SUBDIRECTORY = 'companion-agents'


def shared_data_binding(core_snapshot, pair_snapshot):
    """Compare every original sports/provenance value, not merely declared hashes."""
    core, pair = core_snapshot.data(), pair_snapshot.data()
    require(core.get('decision_group', 'CORE_TEN') == 'CORE_TEN'
            and pair.get('decision_group') == GROUP
            and tuple(core['owner_ids']) == AGENT_IDS
            and tuple(pair['owner_ids']) == COMPANION_AGENT_IDS,
            'EXACT_CORE_AND_PAIR_SNAPSHOT_IDENTITIES_REQUIRED')
    omitted = {'version', 'owner_ids', 'decision_group'}
    original = {key: value for key, value in core.items() if key not in omitted}
    paired = {key: value for key, value in pair.items() if key not in omitted}
    require(original == paired, 'COMPANION_SHARED_SPORTS_DATA_CHANGED')
    return {'version': 'exact-core-pair-shared-data-v1', 'run_id': core['run_id'],
        'target_date': core['target_date'], 'source_as_of': core['as_of'],
        'core_snapshot_id': core_snapshot.snapshot_id, 'pair_snapshot_id': pair_snapshot.snapshot_id,
        'shared_data_sha256': digest(original), 'fixture_count': len(core['fixture_ids']),
        'fixture_ids_sha256': digest(core['fixture_ids']),
        'family_envelopes_sha256': digest(core['families']),
        'source_manifest_sha256': digest(core['source_manifest']),
        'identity_only_fields': sorted(omitted)}


def pair_snapshot_metadata(binding):
    return {'snapshot_id': binding['pair_snapshot_id'],
        'core_snapshot_id': binding['core_snapshot_id'], 'decision_group': GROUP,
        'owner_ids': list(COMPANION_AGENT_IDS), 'fixture_count': binding['fixture_count'],
        'fixture_ids_sha256': binding['fixture_ids_sha256'],
        'shared_data_sha256': binding['shared_data_sha256']}


def snapshot_reference(pair_snapshot, binding):
    """An explicit derivation receipt, never a second pretty raw-data artifact."""
    data = pair_snapshot.data()
    require(pair_snapshot.snapshot_id == binding['pair_snapshot_id'], 'PAIR_REFERENCE_SNAPSHOT_CHANGED')
    return {'format': 'CORE_SNAPSHOT_IDENTITY_DERIVATION_V1',
        'core_snapshot_id': binding['core_snapshot_id'], 'snapshot_id': pair_snapshot.snapshot_id,
        'core_shared_data_relative_path': '../shared-data.json',
        'identity_overrides': {key: deepcopy(data[key]) for key in ('version', 'owner_ids', 'decision_group')},
        'shared_data_binding': deepcopy(binding)}


def restore_companion_snapshot(core_snapshot, reference):
    """Only the fixed engine transformation is allowed; no arbitrary patch decoder."""
    from goal_analysis.jobs.agent_snapshot import companion_snapshot

    paired = companion_snapshot(core_snapshot)
    binding = shared_data_binding(core_snapshot, paired)
    require(reference == snapshot_reference(paired, binding), 'COMPANION_SNAPSHOT_REFERENCE_CHANGED')
    return paired


def _construct(owner, candidate_input, enriched, history, settings, run_id, selected=None):
    """Use original scoring/role rules; a selected subset cannot add a different leg."""
    from goal_analysis.companion_tickets import _source_card
    from goal_analysis.engine.portfolio import build_portfolio
    from goal_analysis.jobs.daily_223_live import _build_for_one_bookmaker

    raw, source = deepcopy(candidate_input), deepcopy(enriched)
    if selected is not None:
        raw['candidates'] = [row for row in raw['candidates'] if row['candidate_id'] in selected]
        source['candidates'] = [row for row in source['candidates'] if row['candidate_id'] in selected]
    if owner == COMPANION_AGENT_IDS[0]:
        produced = build_portfolio(source, {**deepcopy(settings), 'enabled_profiles': ['daily223']},
                                   allowed_profiles=['daily223'])
    else:
        require(owner == COMPANION_AGENT_IDS[1], 'EXACT_COMPANION_OWNER_REQUIRED')
        produced, _ = _build_for_one_bookmaker(raw, deepcopy(history))
    card = _source_card(owner, produced, raw, run_id)
    if (card is not None and selected is not None
            and (set(selected) != {row['candidate_id'] for row in card['legs']}
                 or len(selected) != len(card['legs']))):
        return None, digest(produced)
    return card, digest(produced)


def build_companion_option_builder(report, artifacts, enriched, club_enriched, settings, as_of):
    """Same callable/contract/search interface as the ten-owner builder."""
    from goal_analysis.jobs.agent_portfolio import (
        ADMISSION_VERSION,
        SOURCE_CATALOG_POLICY,
        VERSION,
        OwnOptionAdmissionRejected,
        _admission_binding,
        _full_candidate_catalog,
        option_legs,
        source_candidate_catalog_reference,
    )

    report, artifacts, enriched, club_enriched, settings = deepcopy(
        (report, artifacts, enriched, club_enriched, settings))
    now = as_of if isinstance(as_of, datetime) else datetime.fromisoformat(as_of)
    policy = report_contract(report)
    require(policy is not None, 'PAIR_REQUIRES_CURRENT_TICKET_PRODUCT_CONTRACT')
    bound, search_cache = {}, {}

    def bind(owner, snapshot):
        get_agent_definition(owner, decision_group=GROUP)
        require(isinstance(snapshot, SharedSnapshot), 'SHARED_SNAPSHOT_REQUIRED')
        if not bound:
            data = snapshot.data()
            odds = data['families']['odds_and_price_history'].get('value')
            require(data.get('decision_group') == GROUP
                    and tuple(data['owner_ids']) == COMPANION_AGENT_IDS
                    and data['run_id'] == report['run_id'] and data['target_date'] == report['date']
                    and datetime.fromisoformat(data['as_of']) == now
                    and isinstance(odds, dict) and odds.get('candidate_input') == artifacts['candidate_input']
                    and odds.get('enriched') == enriched and odds.get('club_enriched') == club_enriched,
                    'PAIR_PRODUCT_SNAPSHOT_MISMATCH')
            family = data['families']['retained_history']
            history = family.get('value')
            if history is None:
                history = data['source_manifest'][family['source_refs'][0]]['producer_value']
            require(history['collected_online'] == artifacts['history'], 'PAIR_HISTORY_NOT_SHARED')
            bound.update(snapshot_id=snapshot.snapshot_id, data=data)
        require(snapshot.snapshot_id == bound['snapshot_id'], 'PRODUCT_BUILDER_SNAPSHOT_CHANGED')
        return bound['data']

    def contract(owner):
        definition = get_agent_definition(owner, decision_group=GROUP)
        return {'version': 'own-complete-ticket-construction-v1', 'agent_id': owner,
            'method_version': definition.method_version, 'run_id': report['run_id'],
            'target_date': report['date'], 'source_as_of': now.isoformat(), 'settings_sha256': digest(settings),
            'ticket_product_contract': deepcopy(policy), 'option_means': 'ONE_COMPLETE_ACCUMULATOR_NOT_ONE_LEG',
            'minimum_legs': 3, 'maximum_legs': 3, 'maximum_source_candidate_ids': 6,
            'maximum_proposals': 6, 'maximum_final_variants': 1, 'preferred_distinct_variants': [1, 1],
            'primary_combined_price': {'min': 10, 'max': None},
            'preferred_combined_price': [15, 20], 'preferred_range_is_hard': False,
            'distinct_fixtures_required': True, 'same_quote_cohort_fields': ['bookmaker', 'region', 'currency'],
            'own_policy': {'source': ('engine.portfolio.build_portfolio/daily223'
                if owner == COMPANION_AGENT_IDS[0] else 'jobs.daily_223_live._build_for_one_bookmaker'),
                'original_three_role_method': '2x2x3_WITH_ORIGINAL_NEAR_TARGET_TOLERANCE',
                'original_score_and_method_gates_preserved': True},
            'instruction': 'Choose your own complete ticket using the original Daily or Legacy method. '
                'Original-method seeds are suggestions, never an automatic choice. You may change all '
                'selected IDs if the exact original method admits them, or abstain. Read complete selected '
                'candidate records, then run both existing simulations. Minimum10 and maximum6 distinct '
                'fixtures are global; the original own method still uses three roles. Prefer15-20 softly; '
                'never add filler, fabricate probabilities or promote unproved profit to fact.',
            'missing_optional_data': 'Keep UNKNOWN local; consider other evidence without inventing values.',
            'candidate_filter_applied': False, 'automatic_selection_or_publication': False}

    def construction_contract(owner):
        return {**contract(owner), 'full_candidate_catalog': _full_candidate_catalog(artifacts['candidate_input'])}

    def source_construction_contract(owner, snapshot):
        data = bind(owner, snapshot)
        return {**contract(owner), 'source_catalog_policy': SOURCE_CATALOG_POLICY,
            'full_candidate_catalog': source_candidate_catalog_reference(
                data['families']['odds_and_price_history']['value'], snapshot.snapshot_id)}

    def build(owner, proposal, snapshot):
        from goal_analysis.generated_tickets import _invalid

        data = bind(owner, snapshot)
        admission = _admission_binding(owner, proposal, snapshot, data)
        require(admission['version'] == ADMISSION_VERSION, 'BOUND_PAIR_ADMISSION_REQUIRED')
        selected = identifiers(proposal['source_candidate_ids'])
        card, producer_hash = _construct(owner, artifacts['candidate_input'], enriched,
                                         artifacts['history'], settings, report['run_id'], selected)
        if card is None:
            raise OwnOptionAdmissionRejected({**admission, 'reason': 'OWN_STRATEGY_CONSTRUCTION_INVALID'})
        card.update(profile_id=owner, profile_name=get_agent_definition(owner, decision_group=GROUP).name,
            channel_id=owner, producer_profile_id='daily223', pair_agent_contract_version=COMPANION_DECISION_VERSION,
            status='DRAFT', publication_scope='RESEARCH', playable=False, stake_eur=None,
            real_wager_placed=False, construction_verified=True, ticket_product_contract=deepcopy(policy))
        card.setdefault('reasoning', []).append(proposal['reason'])
        card['agent_product_binding'] = {'version': VERSION, 'snapshot_id': snapshot.snapshot_id,
            'agent_id': owner, 'option_id': proposal['option_id'], 'run_id': report['run_id'],
            'target_date': report['date'], 'source_as_of': now.isoformat(), 'source_candidate_ids': list(selected),
            'source_candidate_input_sha256': digest(artifacts['candidate_input']),
            'original_method_result_sha256': producer_hash,
            'physical_authentication': 'NOT_PERFORMED_BY_PURE_BRIDGE'}
        failure = _invalid(card, report, now)
        if failure in {'DAILY223_CONSTRUCTION_INVALID', 'OWN_STRATEGY_CONSTRUCTION_INVALID'}:
            raise OwnOptionAdmissionRejected({**admission, 'reason': 'OWN_STRATEGY_CONSTRUCTION_INVALID'})
        require(failure is None, 'CANONICAL_PAIR_PRODUCT_REJECTED:' + str(failure))
        return {'option_id': proposal['option_id'], 'agent_id': owner, 'reason': proposal['reason'],
            'snapshot_id': snapshot.snapshot_id, 'legs': option_legs(card),
            'canonical_card': card, 'canonical_card_sha256': digest(card)}

    def strategy_search(owner, snapshot):
        data = bind(owner, snapshot)
        if owner not in search_cache:
            result = {'version': 'own-existing-strategy-search-v1', 'agent_id': owner,
                'method_version': get_agent_definition(owner, decision_group=GROUP).method_version,
                'snapshot_id': snapshot.snapshot_id, 'run_id': data['run_id'], 'target_date': data['target_date'],
                'source_as_of': data['as_of'], 'full_fixture_ids': list(data['fixture_ids']),
                'source_candidate_rows': len(artifacts['candidate_input']['candidates']),
                'source_candidate_input_sha256': digest(artifacts['candidate_input']),
                'all_family_states': {key: {'status': value['status'], 'value_sha256': value['value_sha256']}
                    for key, value in data['families'].items()}, 'common_shortlist_applied': False,
                'agent_may_reject_modify_or_choose_outside_results': True,
                'canonical_admission_still_required': True, 'selected_raw_record_reads_still_required': True,
                'both_simulations_still_required': True, 'automatic_selection_or_publication': False,
                'actual_host_tool_invocation': False}
            try:
                card, producer_hash = _construct(owner, artifacts['candidate_input'], enriched,
                    artifacts['history'], settings, report['run_id'])
                ids = [row['source_candidate_id'] for row in card['legs']] if card is not None else []
                result.update(status='AVAILABLE' if ids else 'NO_COMPLIANT_OPTION',
                    reason=None if ids else 'OWN_ORIGINAL_METHOD_RETURNED_NO_COMPLETE_OPTION',
                    original_method_result_sha256=producer_hash,
                    alternatives=[{'source_candidate_ids': ids, 'combined_price': card['combined_price'],
                        'search_option_id': digest([owner, snapshot.snapshot_id, ids]),
                        'reason': 'Original own method seed; selected raw reads and both simulations remain required.'}]
                        if ids else [])
            except (ValueError, KeyError, TypeError, OverflowError) as error:
                result.update(status='UNKNOWN', reason='OWN_SEARCH_SOURCE_OR_POLICY_UNAVAILABLE',
                              error_type=type(error).__name__, alternatives=[])
            result['result_id'] = digest(result)
            search_cache[owner] = json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False)
        return json.loads(search_cache[owner])

    build.construction_contract = construction_contract
    build.source_construction_contract = source_construction_contract
    build.strategy_search = strategy_search
    return build


def _selected_pair_cards(report, *, rejected=None):
    from goal_analysis.agents.profile_decision import SIMULATOR_ROLES, option_exposure
    from goal_analysis.generated_tickets import _invalid, _materialize
    from goal_analysis.jobs.agent_portfolio import VERSION, option_legs

    run = report['companion_agent_decisions']
    require('companion_ticket_channels' not in report and run.get('version') == COMPANION_DECISION_VERSION
            and run.get('agent_count') == 2 and run.get('journal_subdirectory') == JOURNAL_SUBDIRECTORY,
            'EXACT_INDEPENDENT_PAIR_RUN_REQUIRED')
    runner_result = {key: value for key, value in run.items()
                     if key not in {'shared_data_binding', 'journal_subdirectory', 'runner_result_sha256'}}
    require(digest(runner_result) == run.get('runner_result_sha256'), 'PAIR_RUNNER_RESULT_CHANGED')
    binding = run.get('shared_data_binding', {})
    require(binding.get('version') == 'exact-core-pair-shared-data-v1'
            and binding.get('run_id') == report['run_id'] and binding.get('target_date') == report['date']
            and binding.get('core_snapshot_id') == report['ten_agent_decisions']['snapshot_id']
            and binding.get('pair_snapshot_id') == run.get('snapshot_id')
            and report.get('companion_agent_snapshot_metadata') == pair_snapshot_metadata(binding),
            'PAIR_SHARED_DATA_METADATA_CHANGED')
    owners = run.get('owners')
    require(isinstance(owners, list) and len(owners) == 2
            and [row.get('agent_id') for row in owners] == list(COMPANION_AGENT_IDS),
            'EXACT_PAIR_OWNER_OUTCOMES_REQUIRED')
    cutoff = datetime.fromisoformat(report.get('finished_at', report['observed_at']))
    expected = []
    for outcome in owners:
        if outcome.get('status') != 'DECISION_COMPLETED':
            continue
        owner = outcome['agent_id']
        definition = get_agent_definition(owner, decision_group=GROUP)
        decision = outcome.get('decision', {})
        require(decision.get('version') == OWNER_VERSION
                and decision.get('method_version') == definition.method_version
                and decision.get('session_id') == digest([owner, definition.method_version, run['snapshot_id']])
                and all(decision.get(key) == outcome.get(key)
                    for key in ('agent_id', 'session_id', 'snapshot_id'))
                and decision.get('snapshot_id') == run['snapshot_id'], 'FOREIGN_PAIR_OWNER_DECISION')
        options = decision.get('selected_options')
        require(isinstance(options, list) and len(options) <= 1
                and decision.get('option_ids') == [row.get('option_id') for row in options]
                and decision.get('status') == ('FINAL_DECISION' if options else 'ABSTAIN')
                and set(decision.get('family_uses', {})) == set(FAMILIES), 'INVALID_PAIR_FINAL_VARIANTS')
        receipts = decision.get('tool_receipts', [])
        require(isinstance(receipts, list) and set(SIMULATOR_ROLES) <= {row.get('role') for row in receipts}
                and all(row.get('option_hash') == decision.get('option_hash') for row in receipts),
                'PAIR_CURRENT_TOOL_RECEIPTS_REQUIRED')
        for option in options:
            team = report.get('team', {})
            if owner in team.get('blocked_profiles', []) or team.get('profile_states', {}).get(owner) == 'SUSPENDED':
                if rejected is not None:
                    rejected.append({'agent_id': owner, 'option_id': option['option_id'], 'reason': 'SUSPENDED_PROFILE'})
                continue
            card = option.get('canonical_card', {})
            product = card.get('agent_product_binding', {})
            require(option.get('agent_id') == owner and option.get('snapshot_id') == run['snapshot_id']
                    and card.get('profile_id') == card.get('channel_id') == owner
                    and card.get('producer_profile_id') == 'daily223'
                    and card.get('pair_agent_contract_version') == COMPANION_DECISION_VERSION
                    and 'companion_decision' not in card
                    and option.get('canonical_card_sha256') == digest(card)
                    and option.get('legs') == option_legs(card) and product.get('version') == VERSION
                    and all(product.get(key) == value for key, value in (
                        ('agent_id', owner), ('option_id', option['option_id']),
                        ('snapshot_id', run['snapshot_id']), ('run_id', report['run_id']),
                        ('target_date', report['date']))), 'UNBOUND_PAIR_CANONICAL_PRODUCT')
            option_exposure(option)
            require(all(any(row.get('role') == role and row.get('agent_id') == owner
                and row.get('snapshot_id') == run['snapshot_id'] and (row.get('status') == 'UNKNOWN'
                    or option['option_id'] in row.get('own_option_ids', [])) for row in receipts)
                for role in SIMULATOR_ROLES), 'PAIR_SELECTED_VARIANT_TOOL_NOT_COVERED')
            failure = _invalid(card, report, cutoff)
            if failure is not None:
                if rejected is not None:
                    rejected.append({'agent_id': owner, 'option_id': option['option_id'], 'reason': failure})
                continue
            ticket = _materialize(card, 'companion_agent_decisions', report, report.get('team', {}))
            ticket['agent_decision'] = {'agent_id': owner, 'session_id': decision['session_id'],
                'snapshot_id': run['snapshot_id'], 'method_version': decision['method_version'],
                'option_id': option['option_id'], 'decision_sha256': digest(decision),
                'canonical_source_card_sha256': option['canonical_card_sha256'], 'reason': decision['reason'],
                'option_reason': option.get('reason'), 'family_uses': deepcopy(decision['family_uses']),
                'tool_receipts': deepcopy(receipts),
                'selection_authority': 'OWN_FINAL_DECISION_NO_COORDINATOR_RESELECTION'}
            expected.append(ticket)
    return expected


def validate_companion_agent_cards(report):
    """Whole saved products/decisions bound; native/file proof is a separate check."""
    rejected = []
    expected = _selected_pair_cards(report, rejected=rejected)
    actual = [row for row in report['generated_tickets'] if row.get('channel_id') in COMPANION_AGENT_IDS]
    require(actual == expected and len({row['ticket_id'] for row in actual}) == len(actual)
            and not any('channel_id' in row and row.get('channel_id') not in COMPANION_AGENT_IDS
                        for row in report['generated_tickets'])
            and report.get('companion_agent_publication_exclusions') == rejected,
            'PAIR_CANONICAL_COLLECTION_CHANGED')
    return {row['ticket_id']: row['agent_decision']['option_id'] for row in actual}


def refresh_companion_agent_capacity(report):
    validate_companion_agent_cards(report)
    cards = report['generated_tickets']
    core = [row for row in cards if 'channel_id' not in row]
    pair = [row for row in cards if row.get('channel_id') in COMPANION_AGENT_IDS]
    core_ids = {row['profile_id'] for row in core} & set(AGENT_IDS)
    pair_ids = {row['profile_id'] for row in pair}
    require(len(core) <= 30 and len(pair) <= 2, 'SEPARATE_PAIR_CAPACITY_EXCEEDED')
    present = core_ids | pair_ids
    report['generation_capacity'].update(version=CAPACITY_VERSION, contract=CAPACITY_VERSION,
        minimum=12, maximum=32, main_slot_count=12, filled_main_slots=len(present),
        filled_agent_owner_slots=len(present), filled_core_owner_slots=len(core_ids),
        filled_pair_owner_slots=len(pair_ids), filled_companion_slots=0,
        target_met=len(present) == 12, shortfall=12-len(present),
        status='IN_TARGET' if len(present) == 12 else 'BELOW_TARGET', generated_ticket_count=len(cards),
        agent_variant_count=len(cards), maximum_agent_variants=32, maximum_companion_tickets=0,
        missing_agent_ids=[owner for owner in (*AGENT_IDS, *COMPANION_AGENT_IDS) if owner not in present])
    groups = (report['ten_agent_decisions'], report['companion_agent_decisions'])
    report['construction_status'] = 'COMPLETE' if len(present) == 12 and all(
        group.get('status') == 'COMPLETE' and all(row.get('status') == 'DECISION_COMPLETED'
        for row in group['owners']) for group in groups) else 'PARTIAL'
    return report


def append_companion_agent_cards(report):
    from goal_analysis.engine.diversification import ticket_signature
    from goal_analysis.generated_tickets import refresh_generation_capacity

    rejected = []
    cards = _selected_pair_cards(report, rejected=rejected)
    core = [row for row in report['generated_tickets'] if 'channel_id' not in row]
    require(not {row['ticket_id'] for row in core} & {row['ticket_id'] for row in cards},
            'PAIR_CORE_TICKET_ID_COLLISION')
    report['generated_tickets'] = core + cards
    report['companion_agent_publication_exclusions'] = rejected
    report['profiles'] = [row for row in report.get('profiles', [])
                          if row.get('profile_id') not in COMPANION_AGENT_IDS] + [
        {'profile_id': row['agent_id'],
         'profile_name': get_agent_definition(row['agent_id'], decision_group=GROUP).name,
         'status': ('ABSTAINED' if row.get('decision', {}).get('status') == 'ABSTAIN' else
                    'COMPLETE' if row['status'] == 'DECISION_COMPLETED' else 'PARTIAL'),
         'agent_status': row['status'], 'reason': row.get('reason', row.get('decision', {}).get('reason')),
         'ticket_count': sum(card['profile_id'] == row['agent_id'] for card in cards),
         'published': any(card['profile_id'] == row['agent_id'] for card in cards)}
        for row in report['companion_agent_decisions']['owners']]
    report['companion_agent_shared_exposure'] = [
        {'ticket_ids': [left['ticket_id'], right['ticket_id']], 'status': 'IDENTICAL_EXPOSURE_NOT_INDEPENDENT'}
        for index, left in enumerate(report['generated_tickets'])
        for right in report['generated_tickets'][index+1:]
        if ('channel_id' in left or 'channel_id' in right) and ticket_signature(left) == ticket_signature(right)]
    report['tickets'] = [row for row in report['generated_tickets']
                         if row['status'] == 'READY' and row['publication_scope'] == 'LIVE']
    refresh_generation_capacity(report)
    return report
