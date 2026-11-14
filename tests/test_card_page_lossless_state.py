"""Lossless interactive projection; canonical evidence and money stay unchanged."""
from copy import deepcopy

from goal_analysis.jobs.agent_snapshot import json_text
from goal_analysis.workshop_views import DETAIL_BYTES, card_page


def _report(cards):
    return {'run_id': '2026-11-14-owned', 'date': '2026-11-14',
            'report_sha256': '1' * 64, 'generated_tickets': cards}


def test_large_canonical_ticket_and_its_state_are_sent_once_without_loss():
    card = {'ticket_id': 'large', 'legs': [{'fixture_id': 'f1', 'context': 'x' * 1_200_000}],
            'agent_decision': {'reason': 'own', 'full_evidence': 'y' * 150_000}}
    state = {**deepcopy(card), 'played': True, 'active': 1,
             'financial': {'gross_return': None, 'unknown_cost': True}, 'snapshot_sha256': '2' * 64}
    result = card_page(_report([card]), [state])
    assert result['card_wire_version'] == 2 and result['next_offset'] is None
    item = result['items'][0]
    assert item['ticket'] == card
    assert {**item['ticket'], **item['state']} == state
    assert item['state_projection'] == 'CANONICAL_TICKET_PLUS_STATE_DELTA_V2'
    assert item['decision_projection'] == 'CANONICAL_TICKET_V2'
    assert 'agent_decision' not in item and 'agent_decision' not in item['state']
    assert len(json_text(result).encode('utf-8')) < DETAIL_BYTES


def test_differing_state_values_and_unknown_money_are_retained_exactly():
    card = {'ticket_id': 'changed', 'status': 'DRAFT', 'value': 1, 'typed': [1, {'one': 1}],
            'legs': [{'fixture_id': 'f1'}], 'agent_decision': {'reason': 'original'}}
    state = {**deepcopy(card), 'value': None, 'status': 'SETTLED', 'typed': [True, {'one': 1.0}],
             'agent_decision': {'reason': 'different'}, 'played': False,
             'financial': {'return': None, 'stake': 1.0, 'net': None}, 'outcome': 'VOID'}
    before = deepcopy((card, state))
    item = card_page(_report([card]), [state])['items'][0]
    assert {**item['ticket'], **item['state']} == state
    assert json_text({**item['ticket'], **item['state']}) == json_text(state)
    assert item['state']['value'] is None and item['state']['played'] is False
    assert item['state']['agent_decision'] == {'reason': 'different'}
    assert (card, state) == before
    partial = {'ticket_id': 'changed', 'played': False, 'financial': None}
    plain = card_page(_report([card]), [partial])['items'][0]
    assert plain['state_projection'] == 'STATE_FULL_V1' and plain['state'] == partial


def test_canonical_oversize_keeps_explicit_bound_and_never_truncates():
    card = {'ticket_id': 'too-large', 'raw': 'x' * DETAIL_BYTES}
    report = _report([card])
    result = card_page(report, [])
    assert result['code'] == 'DETAIL_TOO_LARGE'
    assert result['blocked_offset'] == result['next_offset'] == 0
    assert result['items'] == [] and report['generated_tickets'] == [card]


def test_page_continuations_account_for_all_thirteen_cards_and_missing_states():
    cards = [{'ticket_id': f'ticket-{i}', 'raw': 'z' * 600_000} for i in range(13)]
    states = [{**card, 'played': False, 'outcome': None} for card in cards[:12]]
    offset, obtained = 0, []
    for _ in range(13):
        result = card_page(_report(cards), states, offset=offset, limit=32)
        assert len(json_text(result).encode('utf-8')) <= DETAIL_BYTES
        obtained.extend(result['items'])
        if result['next_offset'] is None:
            break
        assert result['next_offset'] > offset
        offset = result['next_offset']
    assert [row['ticket'] for row in obtained] == cards
    assert obtained[-1]['state'] is None
    assert [{**row['ticket'], **row['state']} for row in obtained[:-1]] == states
