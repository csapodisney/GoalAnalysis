"""Bounded projections of supplied saved data; no collectors, models or I/O."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from urllib.parse import urlencode

from goal_analysis.agents.profile_roster import ACTIVE_AGENTS, AGENT_IDS
from goal_analysis.jobs.agent_snapshot import FAMILIES, digest, json_text
from goal_analysis.strategy_provenance import APPLICATION_VERSION

WORKSHOP_BYTES = 1024 * 1024
DETAIL_BYTES = 2 * 1024 * 1024


def _object(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _url(path, **values):
    return path + '?' + urlencode({key: value for key, value in values.items() if value is not None})


def _checked(value, maximum):
    if len(json_text(value).encode('utf-8')) > maximum:
        raise ValueError('DETAIL_TOO_LARGE_USE_EXPLICIT_EXPORT')
    return value


def page(items, *, offset, limit, maximum, metadata):
    """A byte-shortened page always preserves original indices and continuation."""
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= maximum:
        raise ValueError('EXACT_BOUNDED_PAGE_REQUIRED')
    if offset > len(items):
        raise ValueError('PAGE_OFFSET_OUT_OF_RANGE')
    output = {**deepcopy(metadata), 'total': len(items), 'offset': offset, 'items': [], 'next_offset': None}
    for index in range(offset, min(len(items), offset + limit)):
        candidate = {**output, 'items': [*output['items'], deepcopy(items[index])],
                     'next_offset': index + 1 if index + 1 < len(items) else None}
        if len(json_text(candidate).encode('utf-8')) > DETAIL_BYTES:
            if not output['items']:
                return {**output, 'code': 'DETAIL_TOO_LARGE', 'detail_omitted': True,
                        'blocked_offset': offset, 'next_offset': offset,
                        'export_url': (_url('/api/evidence/export', run_id=metadata.get('run_id'), family=metadata.get('family_id'))
                            if metadata.get('family_id') else _url('/api/export', date=metadata.get('target_date')))}
            output['next_offset'] = index
            break
        output = candidate
    return _checked(output, DETAIL_BYTES)


def canonical_cards(report):
    if not report or 'generated_tickets' not in report:
        return []
    cards = report['generated_tickets']
    if not isinstance(cards, list) or any(not isinstance(card, dict) for card in cards):
        raise ValueError('INVALID_CANONICAL_CARD_LIST')
    ids = [card.get('ticket_id') for card in cards]
    if any(not isinstance(key, str) or not key for key in ids) or len(ids) != len(set(ids)):
        raise ValueError('CANONICAL_TICKET_IDS_NOT_UNIQUE')
    return cards


MAIN_SLOT_VERSION = 'TWELVE_MAIN_SLOTS_V1'
COMPANION_ROLES = {'daily_portfolio': 'DAILY', 'legacy_daily223': 'LEGACY'}
COMPANION_VERSION = 'companion-ticket-channels-v1'
COMPANION_BINDING_FIELDS = ('channel_id', 'run_id', 'target_date', 'shared_snapshot_id',
    'calendar_ids_sha256', 'input_sha256', 'settings_sha256', 'producer', 'producer_report_sha256',
    'claim_sha256', 'canonical_source_card_sha256')


def _owner_card_bound(card, outcome, report):
    owner = card.get('profile_id')
    decision = _object(outcome.get('decision'))
    proof = _object(card.get('agent_decision'))
    binding = _object(card.get('agent_product_binding'))
    snapshot_id = _object(report.get('ten_agent_decisions')).get('snapshot_id')
    selected = _list(decision.get('selected_options'))
    options = [row for row in selected if isinstance(row, dict)
               and row.get('option_id') == proof.get('option_id')]
    if (owner not in AGENT_IDS or outcome.get('status') != 'DECISION_COMPLETED'
            or decision.get('status') != 'FINAL_DECISION' or len(options) != 1
            or not snapshot_id or card.get('date') != report.get('date')
            or ('run_id' in card and card['run_id'] != report.get('run_id'))
            or 'companion_decision' in card or card.get('status') not in {'DRAFT', 'READY'}
            or not isinstance(card.get('legs'), list) or not card['legs']):
        return False
    option = options[0]
    source = option.get('canonical_card')
    return (isinstance(source, dict)
        and option.get('canonical_card_sha256') == digest(source)
        == proof.get('canonical_source_card_sha256')
        and proof.get('decision_sha256') == digest(decision)
        and proof.get('agent_id') == outcome.get('agent_id') == decision.get('agent_id') == owner
        and proof.get('session_id') == outcome.get('session_id') == decision.get('session_id')
        and proof.get('snapshot_id') == outcome.get('snapshot_id') == decision.get('snapshot_id') == snapshot_id
        and binding.get('agent_id') == owner and binding.get('snapshot_id') == snapshot_id
        and binding.get('option_id') == proof.get('option_id')
        and binding.get('run_id') == report.get('run_id')
        and binding.get('target_date') == report.get('date')
        and proof.get('option_id') in _list(decision.get('option_ids')))



INDEPENDENT_SLOT_VERSION = 'TWELVE_INDEPENDENT_AGENT_SLOTS_V2'
PAIR_IDS = ('daily_portfolio', 'legacy_daily223')


def _independent_pair_capacity(report, snapshot):
    """Explicit new generation; historical constructors never count as agents."""
    from goal_analysis.companion_agents import validate_companion_agent_cards

    cards = canonical_cards(report)
    core_report = {key: value for key, value in report.items()
                   if key != 'companion_agent_decisions'}
    core_report['generated_tickets'] = [card for card in cards if 'channel_id' not in card]
    base = main_slot_capacity(core_report, snapshot)
    issues, invalid = list(base['identity_issues']), list(base['invalid_card_ids'])
    if 'companion_ticket_channels' in report:
        issues.append('MIXED_COMPANION_GENERATIONS_FORBIDDEN')
    pair = _object(report.get('companion_agent_decisions'))
    outcomes = _list(pair.get('owners'))
    if (len(outcomes) != 2 or {_object(row).get('agent_id') for row in outcomes} != set(PAIR_IDS)):
        issues.append('EXACT_TWO_INDEPENDENT_COMPANION_OUTCOMES_REQUIRED')
    try:
        bound = validate_companion_agent_cards(report)
    except (ValueError, TypeError, KeyError) as error:
        bound = {}
        issues.append('INVALID_INDEPENDENT_COMPANION_BINDING:' + type(error).__name__)
    pair_cards = [card for card in cards if 'channel_id' in card]
    invalid.extend(card['ticket_id'] for card in pair_cards if card['ticket_id'] not in bound)
    present_pair = {card['profile_id'] for card in pair_cards if card['ticket_id'] in bound}
    present = set(base['filled_agent_owner_ids']) | present_pair
    all_ids = (*AGENT_IDS, *PAIR_IDS)
    if len(pair_cards) > 2 or len(cards) > 32:
        issues.append('INDEPENDENT_CARD_CAPACITY_EXCEEDED')
    met = len(present) == 12 and not invalid and not issues
    return {**base, 'version': INDEPENDENT_SLOT_VERSION,
        'generated_ticket_count': len(cards), 'maximum_agent_variants': 32,
        'maximum_companion_tickets': 0, 'agent_variant_count': len(cards),
        'filled_agent_owner_slots': len(present),
        'filled_core_owner_slots': base['filled_agent_owner_slots'],
        'filled_pair_owner_slots': len(present_pair),
        'filled_agent_owner_ids': [key for key in all_ids if key in present],
        'missing_agent_ids': [key for key in all_ids if key not in present],
        'filled_companion_slots': 0, 'filled_companion_channel_ids': [],
        'missing_channel_ids': [], 'companion_channels': [],
        'filled_main_slots': len(present), 'target_met': met,
        'invalid_card_ids': invalid, 'identity_issues': issues,
        'state': 'TARGET_MET' if met else 'TARGET_NOT_MET'}


def main_slot_capacity(report, snapshot=None):
    """Saved identity coverage, not independent source validity or profit proof."""
    report, snapshot = _object(report), _object(snapshot)
    if 'companion_agent_decisions' in report:
        return _independent_pair_capacity(report, snapshot)
    cards = canonical_cards(report)
    outcomes = _list(_object(report.get('ten_agent_decisions')).get('owners'))
    valid_outcomes, issues, invalid_cards = {}, [], []
    for outcome in outcomes:
        owner = _object(outcome).get('agent_id')
        if owner not in AGENT_IDS or owner in valid_outcomes:
            issues.append('DUPLICATE_OR_FOREIGN_OWNER_OUTCOME')
        else:
            valid_outcomes[owner] = outcome
    agent_cards = [card for card in cards if 'channel_id' not in card]
    own_counts = Counter(card.get('profile_id') for card in agent_cards)
    present = set()
    own_options = set()
    for card in agent_cards:
        owner = card.get('profile_id')
        option = _object(card.get('agent_decision')).get('option_id')
        key = (owner, option)
        if (own_counts[owner] > 3 or key in own_options
                or not _owner_card_bound(card, valid_outcomes.get(owner, {}), report)):
            invalid_cards.append(card['ticket_id'])
        else:
            present.add(owner)
            own_options.add(key)
    generated = 'generated_tickets' in report
    base = {'version': MAIN_SLOT_VERSION, 'run_id': report.get('run_id'),
        'target_date': report.get('date'), 'canonical_report_sha256': report.get('report_sha256'),
        'main_slot_count': 12, 'generated_ticket_count': len(cards) if generated else None,
        'maximum_agent_variants': 30, 'maximum_companion_tickets': 2,
        'agent_variant_count': len(agent_cards), 'filled_agent_owner_slots': len(present),
        'filled_agent_owner_ids': [key for key in AGENT_IDS if key in present],
        'missing_agent_ids': [key for key in AGENT_IDS if key not in present],
        'invalid_card_ids': invalid_cards, 'identity_issues': issues,
        'independent_source_verification': 'NOT_PERFORMED_BY_VIEW'}
    envelope = report.get('companion_ticket_channels')
    if envelope is None:
        return {**base, 'filled_companion_slots': None, 'filled_main_slots': None,
            'filled_companion_channel_ids': [], 'missing_channel_ids': None,
            'target_met': None, 'state': 'HISTORICAL_TEN_OWNER_REPORT' if report else 'NOT_RUN',
            'companion_channels': []}
    rows = _object(envelope).get('channels')
    if (_object(envelope).get('version') != COMPANION_VERSION or not isinstance(rows, list)
            or [_object(row).get('channel_id') for row in rows] != list(COMPANION_ROLES)):
        issues.append('INVALID_COMPANION_CHANNEL_ENVELOPE')
        rows = []
    fixture_ids = snapshot.get('fixture_ids') if snapshot else _object(report.get('full_day_calendar')).get('provider_fixture_ids')
    calendar_hash = digest(fixture_ids) if isinstance(fixture_ids, list) else None
    snapshot_id = _object(report.get('ten_agent_decisions')).get('snapshot_id')
    filled, summaries = set(), []
    for row in rows:
        channel = row['channel_id']
        channel_cards = [card for card in cards if card.get('channel_id') == channel]
        bound = (row.get('run_id') == report.get('run_id') and row.get('target_date') == report.get('date')
            and row.get('shared_snapshot_id') == snapshot_id and bool(snapshot_id)
            and calendar_hash is not None and row.get('calendar_ids_sha256') == calendar_hash)
        valid = False
        if row.get('status') == 'COMPLETE' and bound and len(channel_cards) == 1:
            card = channel_cards[0]
            proof = _object(card.get('companion_decision'))
            valid = (card.get('profile_id') == card.get('producer_profile_id') == 'daily223'
                and card.get('date') == report.get('date') and 'agent_decision' not in card
                and card.get('status') in {'DRAFT', 'READY'}
                and isinstance(card.get('legs'), list) and bool(card['legs'])
                and ('run_id' not in card or card['run_id'] == report.get('run_id'))
                and row.get('ticket_ids') == [card['ticket_id']]
                and all(proof.get(key) == row.get(key) for key in COMPANION_BINDING_FIELDS)
                and proof.get('authority') == 'PROTECTED_CONSTRUCTOR_NOT_AGENT_REASONING'
                and all(isinstance(row.get(key), str) and bool(row[key]) for key in (
                    'input_sha256', 'producer_report_sha256', 'claim_sha256', 'canonical_source_card_sha256'))
                and isinstance(row.get('producer'), dict) and bool(row['producer']))
        if valid:
            filled.add(channel)
        elif channel_cards:
            invalid_cards.extend(card['ticket_id'] for card in channel_cards)
        if not bound or row.get('status') not in {'COMPLETE', 'CONSTRUCTION_INCOMPLETE', 'FAILED_NO_RETRY'}:
            issues.append('INVALID_COMPANION_CHANNEL_IDENTITY_OR_STATUS:' + channel)
        summaries.append({'channel_id': channel, 'role': COMPANION_ROLES[channel],
            'run_id': row.get('run_id'), 'ticket_ids': list(row.get('ticket_ids', [])) if valid else [],
            'status': row.get('status') if bound and (valid or row.get('status') != 'COMPLETE') else 'UNBOUND',
            'reason': row.get('reason') or (None if valid else 'NO_VALID_CURRENT_COMPANION_CARD'),
            'identity_coverage_status': 'VALID_CURRENT_CARD' if valid else 'NO_VALID_CURRENT_CARD'})
    known = set(COMPANION_ROLES)
    invalid_cards.extend(card['ticket_id'] for card in cards
                         if 'channel_id' in card and card.get('channel_id') not in known)
    if len(agent_cards) > 30 or len(cards) - len(agent_cards) > 2:
        issues.append('SEPARATE_AGENT_OR_COMPANION_CARD_CAP_EXCEEDED')
    total = len(present) + len(filled)
    met = total == 12 and not invalid_cards and not issues
    return {**base, 'filled_companion_slots': len(filled), 'filled_main_slots': total,
        'filled_companion_channel_ids': [key for key in COMPANION_ROLES if key in filled],
        'missing_channel_ids': [key for key in COMPANION_ROLES if key not in filled],
        'target_met': met, 'state': 'TARGET_MET' if met else 'TARGET_NOT_MET',
        'companion_channels': summaries}


def success_identity_coverage(job):
    """Pure durable-success guard; counts alone cannot fill absent identities."""
    coverage = _object(job.get('capacity'))
    independent = coverage.get('version') == INDEPENDENT_SLOT_VERSION
    expected_ids = list(AGENT_IDS) + list(PAIR_IDS) if independent else list(AGENT_IDS)
    return (coverage.get('version') in {MAIN_SLOT_VERSION, INDEPENDENT_SLOT_VERSION}
        and coverage.get('run_id') == job.get('run_id') and bool(job.get('run_id'))
        and coverage.get('target_date') == job.get('target_date')
        and isinstance(coverage.get('canonical_report_sha256'), str)
        and len(coverage['canonical_report_sha256']) == 64
        and all(char in '0123456789abcdef' for char in coverage['canonical_report_sha256'])
        and coverage.get('filled_agent_owner_ids') == expected_ids
        and coverage.get('filled_companion_channel_ids') == ([] if independent else list(COMPANION_ROLES))
        and coverage.get('filled_agent_owner_slots') == (12 if independent else 10)
        and coverage.get('filled_companion_slots') == (0 if independent else 2) and coverage.get('filled_main_slots') == 12
        and coverage.get('main_slot_count') == 12 and coverage.get('target_met') is True
        and coverage.get('invalid_card_ids') == [] and coverage.get('identity_issues') == []
        and type(job.get('generated_ticket_count')) is int
        and 12 <= job['generated_ticket_count'] <= 32
        and coverage.get('generated_ticket_count') == job['generated_ticket_count'])


def card_page(report, states, *, offset=0, limit=10):
    cards = canonical_cards(report)
    state_rows = _list(states)
    by_id = {row['ticket_id']: row for row in state_rows}
    if len(by_id) != len(state_rows):
        raise ValueError('DUPLICATE_TICKET_STATE')
    items = []
    for card in cards:
        state = by_id.get(card['ticket_id'])
        # The stored state already contains the entire ticket. Send that immutable
        # object once; every differing or additional state value remains exact.
        can_share = state is not None and card.keys() <= state.keys()
        delta = {key: value for key, value in state.items()
            if key not in card or json_text(value) != json_text(card[key])
            or key in {'ticket_id', 'run_id', 'snapshot_sha256'}} if can_share else state
        items.append({'ticket': card, 'state': delta,
            'state_projection': 'CANONICAL_TICKET_PLUS_STATE_DELTA_V2' if can_share else 'STATE_FULL_V1',
            'decision_projection': 'CANONICAL_TICKET_V2'})
    return page(items, offset=offset, limit=limit, maximum=32,
                metadata={'run_id': report['run_id'], 'target_date': report['date'],
                          'report_sha256': report.get('report_sha256'),
                          'card_wire_version': 2})


def calendar_page(snapshot, *, offset=0, limit=100):
    family = _object(_object(snapshot.get('families')).get('calendar'))
    rows = family.get('value')
    if not isinstance(rows, list):
        return {'run_id': snapshot['run_id'], 'snapshot_id': snapshot.get('snapshot_id'),
                'status': 'UNKNOWN', 'reason': 'NO_SAVED_FULL_CALENDAR', 'items': [],
                'total': None, 'offset': offset, 'next_offset': None}
    ids = [row.get('fixture_id') for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(snapshot.get('fixture_ids', [])):
        raise ValueError('FULL_CALENDAR_ORDER_OR_IDENTITY_CHANGED')
    # Every original calendar field remains available. No new eligibility inference.
    return page(rows, offset=offset, limit=limit, maximum=100,
                metadata={'run_id': snapshot['run_id'], 'target_date': snapshot['target_date'],
                          'snapshot_id': snapshot.get('snapshot_id'), 'as_of': snapshot.get('as_of')})


def family_page(snapshot, family, *, fixture_id=None, offset=0, limit=25):
    if family not in FAMILIES:
        raise ValueError('UNKNOWN_DATA_FAMILY')
    if fixture_id is not None and fixture_id not in snapshot.get('fixture_ids', []):
        raise ValueError('FIXTURE_NOT_IN_SAVED_FULL_CALENDAR')
    envelope = _object(_object(snapshot.get('families')).get(family))
    value = envelope.get('value')
    if fixture_id is None:
        # Explicit envelope, no arbitrary JSON-path/file access and no hidden raw truncation.
        rows = [{'family_id': family, **deepcopy(envelope)}]
    else:
        by_fixture = _object(value).get('by_fixture')
        if isinstance(by_fixture, dict):
            rows = [{'fixture_id': fixture_id, 'value': deepcopy(by_fixture[fixture_id])}] if fixture_id in by_fixture else []
        elif isinstance(value, list):
            rows = [row for row in value if isinstance(row, dict) and row.get('fixture_id') == fixture_id]
        else:
            rows = [{'fixture_id': fixture_id, 'status': 'UNKNOWN',
                     'reason': 'NO_DIRECT_SAVED_FIXTURE_PROJECTION',
                     'source_refs': envelope.get('source_refs', [])}]
    return page(rows, offset=offset, limit=limit, maximum=100,
                metadata={'run_id': snapshot['run_id'], 'target_date': snapshot['target_date'],
                          'snapshot_id': snapshot.get('snapshot_id'), 'family_id': family,
                          'fixture_id': fixture_id})


def _family_summary(name, envelope, run_id, audit):
    envelope = _object(envelope)
    coverage = envelope.get('coverage')
    if coverage is not None and len(json_text(coverage).encode('utf-8')) > 4096:
        coverage = {'detail_omitted': True, 'original_sha256': digest(coverage),
                    'reason': 'DETAILED_SAVED_COVERAGE_AVAILABLE_AT_FAMILY_ENDPOINT'}
    return {'family_id': name, 'status': envelope.get('status', 'UNKNOWN'),
            'reason': envelope.get('reason', 'NO_SAVED_ENVELOPE' if not envelope else None),
            **{key: envelope.get(key) for key in ('value_sha256', 'observed_at', 'published_at', 'as_of')},
            'coverage': deepcopy(coverage),
            'evidence_levels': {'available': envelope.get('status'),
                'actual_tool_reads': 'UNKNOWN', 'agent_reference': 'SEE_OWN_SAVED_DECLARATIONS',
                'numerical_input': 'UNKNOWN', 'causal_influence': 'NOT_PROVEN',
                'saved_audit_status': audit.get('status', 'UNKNOWN')},
            'detail_url': _url('/api/evidence', run_id=run_id, family=name)}


def _usage(report):
    usage = _object(_object(report.get('usage')).get('openai'))
    known = usage.get('cost_amount')
    currency = usage.get('cost_currency')
    monetary = {currency: known} if type(known) in (int, float) and currency else None
    return {'request_count': usage.get('requests'), 'token_count': usage.get('total_tokens'),
            'usage_complete': usage.get('usage_complete'), 'cost_by_currency': monetary,
            'cost_completeness': 'KNOWN_SUBSET_ONLY' if monetary is not None else 'UNKNOWN',
            'subscription_allocation_status': 'UNKNOWN', 'invoice_status': 'UNKNOWN',
            'fx_status': 'UNKNOWN', 'total_cost_eur': None, 'all_cost_net_eur': None,
            'reason_codes': ['INVOICE_SUBSCRIPTION_ALLOCATION_AND_FX_NOT_ESTABLISHED']}


def workshop(*, day, report, states, snapshot, operation, schedule, capabilities, now):
    if now.utcoffset() is None:
        raise ValueError('AWARE_CLOCK_REQUIRED')
    report, snapshot = _object(report), _object(snapshot)
    if report and report.get('date') != day:
        raise ValueError('REPORT_TARGET_DATE_MISMATCH')
    if snapshot and (snapshot.get('run_id') != report.get('run_id') or snapshot.get('target_date') != day):
        raise ValueError('SAVED_SNAPSHOT_IDENTITY_MISMATCH')
    cards = canonical_cards(report)
    state_map = {row['ticket_id']: row for row in _list(states)}
    owners = _list(_object(report.get('ten_agent_decisions')).get('owners'))
    definitions = ACTIVE_AGENTS
    if 'companion_agent_decisions' in report:
        from goal_analysis.agents.profile_roster import COMPANION_AGENTS

        owners = [*owners, *_list(_object(report['companion_agent_decisions']).get('owners'))]
        definitions = (*ACTIVE_AGENTS, *COMPANION_AGENTS)
    owner_map = {row['agent_id']: row for row in owners}
    if len(owner_map) != len(owners) or set(owner_map) - {row.agent_id for row in definitions}:
        raise ValueError('UNIQUE_ACTIVE_OWNER_OUTCOMES_REQUIRED')
    audit = _object(report.get('ticket_evidence'))
    agents, counts, reasons = [], Counter(), Counter()
    profiles = {row['profile_id']: row for row in _list(report.get('profiles'))}
    for definition in definitions:
        key = definition.agent_id
        owner = owner_map.get(key, {})
        decision = _object(owner.get('decision'))
        evidence = _object(owner.get('decision_evidence'))
        admission = _object(evidence.get('own_option_admission'))
        own_cards = [card for card in cards if card.get('profile_id') == key]
        status = owner.get('status', 'NOT_ATTEMPTED')
        if status == 'DECISION_COMPLETED':
            if decision.get('status') == 'ABSTAIN':
                counts['abstained'] += 1
            else:
                counts['completed'] += 1
        elif status in {'NOT_ATTEMPTED', 'UNATTEMPTED'}:
            counts['unattempted'] += 1
        else:
            counts['failed'] += 1
        reason = owner.get('reason')
        if reason:
            reasons[str(reason)] += 1
        agents.append({**definition.as_dict(), 'independent_models': False,
            'team_status': _object(profiles.get(key)).get('status'), 'decision_status': status,
            'final_decision_status': decision.get('status'),
            'plan_status': 'SAVED' if isinstance(evidence.get('plan'), dict) else 'UNKNOWN',
            'final_status': 'SAVED' if isinstance(evidence.get('final'), dict) else 'UNKNOWN',
            'proposed_count': len(evidence['plan']['options']) if isinstance(_object(evidence.get('plan')).get('options'), list) else None,
            'admitted_count': len(admission['accepted_option_ids']) if isinstance(admission.get('accepted_option_ids'), list) else None,
            'rejected_count': len(admission['rejected_options']) if isinstance(admission.get('rejected_options'), list) else None,
            'selected_count': len(decision['selected_options']) if isinstance(decision.get('selected_options'), list) else None,
            'published_count': len(own_cards), 'ticket_ids': [card['ticket_id'] for card in own_cards],
            'reason_codes': [str(reason)] if reason else []})
    summaries = []
    for card in cards:
        state = _object(state_map.get(card['ticket_id']))
        kickoffs = [row.get('kickoff') for row in card.get('legs', [])]
        aware_kickoffs = []
        for value in kickoffs:
            try:
                stamp = datetime.fromisoformat(value)
                if stamp.utcoffset() is not None:
                    aware_kickoffs.append(stamp.astimezone(UTC))
            except (AttributeError, TypeError, ValueError):
                pass
        summaries.append({**{key: card.get(key) for key in ('ticket_id', 'run_id', 'profile_id', 'producer_profile_id', 'channel_id', 'profile_name', 'status', 'publication_scope', 'combined_price')},
            **({'variant_id': card['variant_id']} if 'variant_id' in card else {}),
            'leg_count': len(card.get('legs', [])),
            'earliest_kickoff_utc': min(aware_kickoffs).isoformat() if len(aware_kickoffs) == len(kickoffs) and kickoffs else None,
            'played': state.get('played'), 'outcome': state.get('outcome'),
            'detail_url': _url('/api/cards', run_id=report.get('run_id'), offset=len(summaries), limit=1)})
    families = _object(snapshot.get('families'))
    calendar_ids = snapshot.get('fixture_ids')
    run_id = report.get('run_id')
    capacity = main_slot_capacity(report, snapshot)
    companion_channels = capacity.pop('companion_channels')
    run_operation = _object(operation) if run_id and _object(operation).get('run_id') == run_id else {}
    result = {'schema_version': 1, 'selected_date': day,
        'server_time': now.astimezone(UTC).isoformat(), 'timezone': 'Europe/Berlin',
        'application_version': APPLICATION_VERSION,
        'view_revision': digest({'report_sha256': report.get('report_sha256'), 'states': states, 'operation': operation, 'schedule': schedule}),
        'record_kind': 'GENERATED_CANONICAL' if 'generated_tickets' in report else 'LEGACY_REPORT' if report else 'NOT_RUN',
        'run': {**{key: report.get(key) for key in ('run_id', 'observed_at', 'finished_at', 'construction_status', 'report_sha256')},
                'target_date': day, 'process_state': run_operation.get('state', 'UNKNOWN'),
                'verification_state': run_operation.get('verification_state', 'UNKNOWN'), 'reason_codes': list(reasons)},
        'capacity': {**capacity,
            **{key + '_owner_count': counts[key] for key in ('completed', 'abstained', 'failed', 'unattempted')},
            'reason_counts': dict(reasons)},
        'companion_channels': companion_channels,
        'calendar': {'fixture_count': len(calendar_ids) if isinstance(calendar_ids, list) else None,
            'priced_fixture_count': None, 'admitted_fixture_count': None, 'unpriced_fixture_count': None,
            'ineligible_reason_counts': None, 'unknown_eligibility_count': None,
            'reason': 'NO_SAVED_AGGREGATE_ELIGIBILITY_COUNT_IN_THIS_PROJECTION',
            'categories_may_overlap': True, 'snapshot_id': snapshot.get('snapshot_id'),
            'as_of': snapshot.get('as_of'), 'detail_url': _url('/api/calendar', run_id=run_id)},
        'agents': agents, 'cards': summaries,
        'families': [_family_summary(name, families.get(name), run_id, audit) for name in FAMILIES],
        'operation': deepcopy(operation), 'schedule': deepcopy(schedule), 'usage': _usage(report),
        'capabilities': deepcopy(capabilities)}
    return _checked(result, WORKSHOP_BYTES)
