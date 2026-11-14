"""Pure saved DTO fixtures, not real producer/math/physical source acceptance."""
from copy import deepcopy
from datetime import UTC, datetime

from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.jobs.agent_snapshot import FAMILIES, digest
from goal_analysis.workshop_views import (
    COMPANION_BINDING_FIELDS,
    COMPANION_ROLES,
    card_page,
    main_slot_capacity,
    success_identity_coverage,
    workshop,
)

DAY = '2026-11-14'
NOW = datetime(2026, 11, 13, 23, tzinfo=UTC)


def _report(variants=1):
    run, snapshot = 'TESTONLY-workshop-twelve', 'a' * 64
    fixtures = ['the_stats_api:mt_1', 'the_stats_api:mt_2']
    report = {'run_id': run, 'date': DAY, 'observed_at': NOW.isoformat(),
        'generated_tickets': [], 'full_day_calendar': {'provider_fixture_ids': fixtures},
        'ten_agent_decisions': {'snapshot_id': snapshot, 'owners': []},
        'companion_ticket_channels': {'version': 'companion-ticket-channels-v1', 'channels': []}}
    for owner in AGENT_IDS:
        session = 'TESTONLY-session-' + owner
        options = []
        for index in range(variants):
            option = owner + '-variant-' + str(index)
            source = {'ticket_id': run + '-' + option, 'profile_id': owner, 'date': DAY,
                'status': 'DRAFT', 'publication_scope': 'RESEARCH', 'playable': False,
                'legs': [{'fixture_id': fixtures[0], 'decimal_price': 2.0}],
                'combined_price': 16.0, 'agent_product_binding': {'agent_id': owner,
                    'option_id': option, 'snapshot_id': snapshot, 'run_id': run, 'target_date': DAY}}
            options.append({'option_id': option, 'canonical_card': source,
                            'canonical_card_sha256': digest(source)})
        decision = {'status': 'FINAL_DECISION', 'agent_id': owner, 'session_id': session,
            'snapshot_id': snapshot, 'option_ids': [row['option_id'] for row in options],
            'selected_options': options}
        report['ten_agent_decisions']['owners'].append({'agent_id': owner,
            'session_id': session, 'snapshot_id': snapshot, 'status': 'DECISION_COMPLETED',
            'decision': decision})
        for option in options:
            card = deepcopy(option['canonical_card'])
            card['agent_decision'] = {'agent_id': owner, 'session_id': session,
                'snapshot_id': snapshot, 'option_id': option['option_id'],
                'decision_sha256': digest(decision),
                'canonical_source_card_sha256': option['canonical_card_sha256']}
            report['generated_tickets'].append(card)
    for channel in COMPANION_ROLES:
        ticket = run + '-' + channel
        row = {'channel_id': channel, 'run_id': run, 'target_date': DAY,
            'shared_snapshot_id': snapshot, 'calendar_ids_sha256': digest(fixtures),
            'input_sha256': 'b' * 64, 'settings_sha256': 'f' * 64, 'producer': {'module': 'TESTONLY', 'function': channel, 'version': 1},
            'producer_report_sha256': 'c' * 64, 'claim_sha256': 'd' * 64,
            'canonical_source_card_sha256': 'e' * 64, 'status': 'COMPLETE', 'reason': None,
            'ticket_ids': [ticket]}
        report['companion_ticket_channels']['channels'].append(row)
        report['generated_tickets'].append({'ticket_id': ticket, 'channel_id': channel,
            'profile_id': 'daily223', 'producer_profile_id': 'daily223', 'date': DAY,
            'status': 'DRAFT', 'publication_scope': 'RESEARCH', 'playable': False,
            'legs': [{'fixture_id': fixtures[1], 'decimal_price': 2.0}], 'combined_price': 16.0,
            'companion_decision': {**{key: deepcopy(row[key]) for key in COMPANION_BINDING_FIELDS},
                                  'authority': 'PROTECTED_CONSTRUCTOR_NOT_AGENT_REASONING'}})
    report['report_sha256'] = digest(report)
    return report


def _view(report):
    snapshot = {'run_id': report['run_id'], 'target_date': DAY,
        'fixture_ids': report['full_day_calendar']['provider_fixture_ids'],
        'families': {key: {'status': 'UNKNOWN', 'value': None, 'reason': 'TESTONLY_NO_SOURCE'}
                     for key in FAMILIES}}
    return workshop(day=DAY, report=report, states=[], snapshot=snapshot, operation=None,
        schedule={'enabled': False}, capabilities={'manual_generation': False}, now=NOW)


def test_ten_distinct_owners_and_two_channels_fill_twelve_with_all_variants_retained():
    report = _report(variants=3)
    before = deepcopy(report)
    result = _view(report)
    capacity = result['capacity']
    assert capacity['version'] == 'TWELVE_MAIN_SLOTS_V1'
    assert capacity['filled_agent_owner_slots'] == 10 and capacity['filled_companion_slots'] == 2
    assert capacity['filled_main_slots'] == capacity['main_slot_count'] == 12
    assert capacity['generated_ticket_count'] == len(result['cards']) == 32
    assert capacity['maximum_agent_variants'] == 30 and capacity['maximum_companion_tickets'] == 2
    assert capacity['target_met'] is True
    assert [row['role'] for row in result['companion_channels']] == ['DAILY', 'LEGACY']
    assert len(result['agents']) == 10 and len(result['families']) == 12
    assert result['calendar']['fixture_count'] == 2 and report == before


def test_extra_variants_cannot_replace_missing_owner_or_companion_main_slot():
    report = _report(variants=3)
    missing = AGENT_IDS[-1]
    report['generated_tickets'] = [card for card in report['generated_tickets'] if card['profile_id'] != missing]
    row = report['companion_ticket_channels']['channels'][1]
    report['generated_tickets'] = [card for card in report['generated_tickets'] if card.get('channel_id') != row['channel_id']]
    row.update(status='CONSTRUCTION_INCOMPLETE', reason='NO_PROTECTED_CARD', ticket_ids=[])
    capacity = main_slot_capacity(report)
    assert capacity['generated_ticket_count'] == 28
    assert capacity['filled_agent_owner_slots'] == 9 and capacity['filled_companion_slots'] == 1
    assert capacity['filled_main_slots'] == 10 and capacity['target_met'] is False
    assert capacity['missing_agent_ids'] == [missing]
    assert capacity['missing_channel_ids'] == ['legacy_daily223']


def test_foreign_run_and_duplicate_companion_cards_never_count_as_valid_channels():
    report = _report()
    report['companion_ticket_channels']['channels'][0]['run_id'] = 'OTHER-RUN'
    duplicate = deepcopy(report['generated_tickets'][-1])
    duplicate['ticket_id'] += '-duplicate'
    report['generated_tickets'].append(duplicate)
    capacity = main_slot_capacity(report)
    assert capacity['filled_companion_slots'] == 0 and capacity['target_met'] is False
    assert len(capacity['invalid_card_ids']) == 3
    assert capacity['identity_issues']
    assert all(row['ticket_ids'] == [] for row in capacity['companion_channels'])


def test_actual_abstention_is_explicit_and_historical_reports_are_not_reclassified():
    report = _report()
    owner = report['ten_agent_decisions']['owners'][0]
    report['generated_tickets'] = [card for card in report['generated_tickets'] if card['profile_id'] != owner['agent_id']]
    owner['decision'].update(status='ABSTAIN', selected_options=[], option_ids=[])
    view = _view(report)
    assert view['agents'][0]['decision_status'] == 'DECISION_COMPLETED'
    assert view['agents'][0]['final_decision_status'] == 'ABSTAIN'
    assert view['capacity']['target_met'] is False
    historical = _report(variants=2)
    historical.pop('companion_ticket_channels')
    historical['generated_tickets'] = [card for card in historical['generated_tickets'] if 'channel_id' not in card]
    old = _view(historical)
    assert old['capacity']['generated_ticket_count'] == 20
    assert old['capacity']['target_met'] is None
    assert old['capacity']['state'] == 'HISTORICAL_TEN_OWNER_REPORT'
    assert old['companion_channels'] == []


def test_card_page_keeps_companion_identity_and_existing_financial_state_exact():
    report = _report()
    companion = report['generated_tickets'][-1]
    state = {'ticket_id': companion['ticket_id'], 'played': True,
        'settlement': {'status': 'VOID', 'gross_return_eur': None},
        'bookmaker_return_contract': {'version': 'CURRENT_HASH_V2', 'expected_settlement_hash': 'f' * 64}}
    result = card_page(report, [state], offset=10, limit=2)
    assert result['total'] == 12 and result['next_offset'] is None
    item = result['items'][1]
    assert item['ticket'] == companion and item['state'] == state
    assert item['agent_decision'] is None and item['companion_decision'] == companion['companion_decision']
    assert item['ticket']['profile_id'] == item['ticket']['producer_profile_id'] == 'daily223'
    assert item['ticket']['channel_id'] == 'legacy_daily223'


def test_durable_success_requires_identity_coverage_not_a_large_count_or_foreign_run():
    report = _report()
    capacity = main_slot_capacity(report)
    job = {'run_id': report['run_id'], 'target_date': DAY, 'generated_ticket_count': 12,
           'capacity': capacity}
    assert success_identity_coverage(job) is True
    invalid = deepcopy(job)
    invalid['capacity']['filled_agent_owner_ids'] = list(AGENT_IDS[:-1])
    invalid['generated_ticket_count'] = invalid['capacity']['generated_ticket_count'] = 32
    assert success_identity_coverage(invalid) is False
    invalid = deepcopy(job)
    invalid['capacity']['run_id'] = 'OTHER-RUN'
    assert success_identity_coverage(invalid) is False
    invalid = deepcopy(job)
    invalid['capacity']['filled_companion_channel_ids'] = ['daily_portfolio', 'daily_portfolio']
    assert success_identity_coverage(invalid) is False
