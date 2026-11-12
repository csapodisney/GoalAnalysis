"""TESTONLY variant persistence on owned SQLite; no inference, provider or wager API."""

import json
import socket
import sqlite3
import subprocess
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.jobs.agent_snapshot import FAMILIES, digest
from goal_analysis.storage.portfolio_ledger import PortfolioLedger, _hash, _json

NOW = datetime(2026, 11, 12, 10, tzinfo=UTC)
DAY = NOW.date().isoformat()


@pytest.fixture(autouse=True)
def owned_boundary(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    assert not root.is_relative_to(Path(__file__).resolve().parents[1])
    assert not root.is_relative_to(Path('C:/AI-Work/GoalAnalysis').resolve())
    marker = root / 'TESTONLY-TEN-AGENT-VARIANTS.json'
    marker.write_text('{"TESTONLY":true}', encoding='utf-8')
    connect = sqlite3.connect

    def owned_connect(database, *args, **kwargs):
        assert Path(database).resolve().is_relative_to(root) and marker.exists()
        return connect(database, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError('TESTONLY variant persistence forbids network and processes')

    monkeypatch.setattr(sqlite3, 'connect', owned_connect)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket.socket, 'bind', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def _materialized(report):
    """Bind handbuilt already-completed source choices; no product builder called."""
    cards = []
    for outcome in report['ten_agent_decisions']['owners']:
        decision = outcome['decision']
        options = decision['selected_options']
        for option in options:
            option['canonical_card_sha256'] = digest(option['canonical_card'])
        decision['option_ids'] = [option['option_id'] for option in options]
        decision['option_hash'] = digest(options)
        decision['tool_receipts'] = [{
            'role': role, 'agent_id': decision['agent_id'], 'session_id': decision['session_id'],
            'snapshot_id': decision['snapshot_id'], 'option_hash': decision['option_hash'],
            'own_option_ids': decision['option_ids'], 'status': 'UNKNOWN', 'value': None,
            'reason': 'TESTONLY unavailable evaluation; no real data or model claim.',
        } for role in ('candidate_scenarios', 'historical_strategy_evaluation')]
        for option in options:
            card = deepcopy(option['canonical_card'])
            card.update(generation_source_status=card['status'], generation_source_group='ten_agent_decisions')
            card['agent_decision'] = {
                'agent_id': decision['agent_id'], 'session_id': decision['session_id'],
                'snapshot_id': decision['snapshot_id'], 'method_version': decision['method_version'],
                'option_id': option['option_id'], 'decision_sha256': digest(decision),
                'canonical_source_card_sha256': option['canonical_card_sha256'],
                'reason': decision['reason'], 'option_reason': option['reason'],
                'family_uses': deepcopy(decision['family_uses']),
                'tool_receipts': deepcopy(decision['tool_receipts']),
                'selection_authority': 'OWN_FINAL_DECISION_NO_COORDINATOR_RESELECTION',
            }
            cards.append(card)
    report['generated_tickets'] = cards
    report['tickets'] = [deepcopy(card) for card in cards if card['publication_scope'] == 'LIVE']
    return report


def completed_report(*, run='TESTONLY-one', variants=2, observed=NOW, first_live=False,
                     first_kickoff=None):
    snapshot = digest({'TESTONLY': run})
    report = {
        'run_id': run, 'date': DAY, 'observed_at': observed.isoformat(),
        'finished_at': observed.isoformat(), 'settings': {'operating_contract_version': 2},
        'ten_agent_decisions': {'version': 'ten-agent-decision-run-v1', 'snapshot_id': snapshot,
                                'agent_count': 10, 'status': 'COMPLETE', 'owners': []},
    }
    for owner_index, owner in enumerate(AGENT_IDS):
        definition = get_agent_definition(owner)
        session = digest([owner, definition.method_version, snapshot])
        options = []
        for variant in range(variants):
            identifier = f'{owner}-variant-{variant}'
            first = owner_index == variant == 0
            live = first and first_live
            kickoff = first_kickoff if first and first_kickoff is not None else NOW + timedelta(hours=8)
            legs = [{
                'fixture_id': f'TESTONLY:{owner_index}:{variant}:{number}',
                'candidate_id': f'TESTONLY:{run}:{owner_index}:{variant}:{number}',
                'kickoff': kickoff.isoformat(), 'market_key': 'totals_2_5',
                'selection_key': 'over', 'period': 'FULL_TIME', 'decimal_price': 2.0,
                'quoted_at': None,
            } for number in range(4)]
            card = {
                'ticket_id': f'{run}-{owner}-{variant}', 'profile_id': owner, 'profile_name': definition.name,
                'date': DAY, 'created_at': observed.isoformat(), 'legs': legs,
                'combined_price': 16.0, 'currency': 'EUR', 'bookmaker': 'TESTONLY-no-bookmaker-order',
                'status': 'READY' if live else 'DRAFT', 'publication_scope': 'LIVE' if live else 'RESEARCH',
                'playable': live, 'stake_eur': 1.0 if live else None,
                'agent_product_binding': {
                    'version': 'ten-agent-canonical-product-bridge-v1', 'snapshot_id': snapshot,
                    'agent_id': owner, 'option_id': identifier, 'run_id': run, 'target_date': DAY,
                    'source_as_of': observed.isoformat(),
                    'source_candidate_ids': [leg['candidate_id'] for leg in legs],
                    'source_candidate_input_sha256': digest({'TESTONLY': run}),
                    'physical_authentication': 'NOT_PERFORMED_BY_PURE_BRIDGE',
                },
            }
            options.append({'option_id': identifier, 'agent_id': owner, 'snapshot_id': snapshot,
                            'reason': f'TESTONLY own variant {variant}', 'legs': deepcopy(legs),
                            'canonical_card': card})
        decision = {
            'version': 'profile-owner-decision-v2-ten-agents', 'agent_id': owner, 'session_id': session,
            'snapshot_id': snapshot, 'method_version': definition.method_version, 'status': 'FINAL_DECISION',
            'selected_options': options, 'reason': 'TESTONLY completed source choices, not real reasoning.',
            'family_uses': {family: {'status': 'MISSING', 'value_sha256': digest(None),
                                    'reason': 'TESTONLY unknown data.'} for family in FAMILIES},
        }
        report['ten_agent_decisions']['owners'].append({
            'agent_id': owner, 'session_id': session, 'snapshot_id': snapshot,
            'status': 'DECISION_COMPLETED', 'decision': decision,
        })
    return _materialized(report)


def _tables(ledger):
    with ledger._connection() as db:
        return {name: [tuple(row) for row in db.execute(f'SELECT * FROM {name} ORDER BY rowid')]
                for name in ('portfolio_runs', 'portfolio_tickets', 'portfolio_events',
                             'portfolio_recommendations', 'recommendation_imports', 'portfolio_wagers')}


@pytest.fixture
def ledger(tmp_path):
    return PortfolioLedger(tmp_path / 'TESTONLY-variants.sqlite3', now=lambda: NOW)


def test_three_own_variants_each_have_stable_distinct_recommendation_ids(ledger):
    report = completed_report(variants=3)
    before = deepcopy(report)
    ledger.save_run(report)
    recommendations = ledger.list_recommendations(date=DAY)
    assert len(recommendations) == 30
    assert Counter(row['profile_id'] for row in recommendations) == dict.fromkeys(AGENT_IDS, 3)
    assert len({row['recommendation_id'] for row in recommendations}) == 30
    for row in recommendations:
        assert row['active']
        assert row['recommendation_id'] == _hash([
            'ten-agent-variant', report['run_id'], row['profile_id'], row['agent_decision']['option_id'],
        ])[:24]
    after = _tables(ledger)
    assert ledger.save_run(deepcopy(report))['saved'] is False
    assert _tables(ledger) == after and report == before
    assert all(row['active'] for row in ledger.list_tickets(date=DAY))


@pytest.mark.parametrize('damage', [
    'fake_profile', 'foreign_option', 'duplicate_option', 'four_variants', 'decision_hash',
    'source_hash', 'mixed_card', 'old_roster_version', 'wrong_decision_version', 'changed_product',
    'forged_playable_promotion',
])
def test_unbound_or_duplicate_variants_fail_before_any_run_write(ledger, damage):
    report = completed_report(variants=4 if damage == 'four_variants' else 2)
    card = report['generated_tickets'][0]
    if damage == 'fake_profile':
        card['profile_id'] = 'kronikas-second-name'
    elif damage == 'foreign_option':
        card['agent_decision']['option_id'] = 'another-owner-option'
    elif damage == 'duplicate_option':
        other = deepcopy(card)
        other['ticket_id'] += '-different-name'
        report['generated_tickets'].append(other)
    elif damage == 'decision_hash':
        card['agent_decision']['decision_sha256'] = '0' * 64
    elif damage == 'source_hash':
        card['agent_decision']['canonical_source_card_sha256'] = '0' * 64
    elif damage == 'mixed_card':
        card.pop('agent_decision')
    elif damage == 'old_roster_version':
        report['ten_agent_decisions']['owners'][0]['decision']['method_version'] = 'legacy-thirty'
    elif damage == 'wrong_decision_version':
        report['ten_agent_decisions']['owners'][0]['decision']['version'] = 'old-owner-version'
    elif damage == 'changed_product':
        card['legs'][0]['decimal_price'] = 2.2
    elif damage == 'forged_playable_promotion':
        card.update(status='READY', publication_scope='LIVE', playable=True, stake_eur=1.0)
        report['tickets'] = [deepcopy(card)]
    before = _tables(ledger)
    with pytest.raises(ValueError):
        ledger.save_run(report)
    assert _tables(ledger) == before


def test_renamed_reordered_repriced_duplicate_is_not_a_distinct_own_option(ledger):
    report = completed_report()
    options = report['ten_agent_decisions']['owners'][0]['decision']['selected_options']
    options[1]['canonical_card']['legs'] = deepcopy(list(reversed(options[0]['canonical_card']['legs'])))
    options[1]['canonical_card']['legs'][0]['decimal_price'] = 2.1
    options[1]['legs'] = deepcopy(options[1]['canonical_card']['legs'])
    _materialized(report)
    before = _tables(ledger)
    with pytest.raises(ValueError, match='DUPLICATE_SELECTED_OWNER_EXPOSURE'):
        ledger.save_run(report)
    assert _tables(ledger) == before


def test_unmarked_current_contract_still_rejects_two_tickets_per_profile(ledger):
    report = completed_report()
    report.pop('ten_agent_decisions')
    before = _tables(ledger)
    with pytest.raises(ValueError, match='only one ticket per profile'):
        ledger.save_run(report)
    assert _tables(ledger) == before


def test_invalid_later_ticket_rolls_back_prior_inserted_ticket_and_run(ledger):
    report = completed_report()
    second = report['ten_agent_decisions']['owners'][0]['decision']['selected_options'][1]
    second['canonical_card']['legs'][0]['kickoff'] = NOW.isoformat()
    second['legs'] = deepcopy(second['canonical_card']['legs'])
    _materialized(report)
    before = _tables(ledger)
    with pytest.raises(ValueError, match='after fixture kickoff'):
        ledger.save_run(report)
    assert _tables(ledger) == before


@pytest.mark.parametrize('retained_reason', ['started', 'played'])
def test_one_old_started_or_played_ticket_blocks_all_new_variants_of_its_owner(ledger, retained_reason):
    original = completed_report(variants=1 if retained_reason == 'played' else 2,
                                first_live=retained_reason == 'played',
                                first_kickoff=NOW + timedelta(hours=1))
    if retained_reason == 'played':
        # An older non-agent LIVE recommendation was explicitly recorded as
        # played; the new agent run must respect that unchanged legacy state.
        original.pop('ten_agent_decisions')
    ledger.save_run(original)
    protected = original['generated_tickets'][0]['ticket_id']
    if retained_reason == 'played':
        ledger.record_wager(protected, actual_combined_price=16.0)
    old_snapshots = {row[0]: row[5:7] for row in _tables(ledger)['portfolio_tickets']}
    later = NOW + timedelta(hours=2 if retained_reason == 'started' else 0, minutes=1)
    ledger.now = lambda: later
    new = completed_report(run='TESTONLY-next', observed=later)
    ledger.save_run(new)
    all_tickets = ledger.list_tickets(date=DAY, mode='archive')
    assert all(not row['active'] for row in all_tickets
               if row['run_id'] == new['run_id'] and row['profile_id'] == AGENT_IDS[0])
    assert sum(row['active'] for row in all_tickets if row['run_id'] == new['run_id']) == 18
    assert ledger.get_ticket(protected)['active']
    assert all(row['active'] for row in all_tickets
               if row['run_id'] == original['run_id'] and row['profile_id'] == AGENT_IDS[0])
    old_after = {row[0]: row[5:7] for row in _tables(ledger)['portfolio_tickets'] if row[1] == original['run_id']}
    assert old_after == old_snapshots
    recommendations = ledger.list_recommendations(date=DAY, archive=True)
    assert all(not row['active'] for row in recommendations
               if row['run_id'] == new['run_id'] and row['profile_id'] == AGENT_IDS[0])
    assert sum(row['active'] for row in recommendations if row['run_id'] == new['run_id']) == 18
    assert next(row for row in recommendations if row['portfolio_ticket_id'] == protected)['active']
    assert all(row['active'] for row in recommendations
               if row['run_id'] == original['run_id'] and row['profile_id'] == AGENT_IDS[0])


def test_freeze_revision_preserves_exact_twenty_then_thirty_variant_sets(ledger):
    original = completed_report()
    ledger.save_run(original)
    assert ledger.freeze_run(original['run_id'])['recommendation_count'] == 20
    frozen = ledger.list_freezes()[0]
    with ledger._connection() as db:
        original_row = tuple(db.execute('SELECT * FROM portfolio_freezes').fetchone())
    later = NOW + timedelta(minutes=2)
    ledger.now = lambda: later
    report = completed_report(run='TESTONLY-revision', observed=later, variants=3)
    ledger.save_run(report)
    result = ledger.revise_freeze(report['run_id'], frozen['snapshot_hash'], 'TESTONLY reviewed variants')
    assert result['saved'] and result['revision'] == 2
    with ledger._connection() as db:
        assert tuple(db.execute('SELECT * FROM portfolio_freezes').fetchone()) == original_row
        row = db.execute('SELECT snapshot_json,snapshot_hash FROM portfolio_freeze_revisions').fetchone()
        corrected = json.loads(row[0])
        assert _hash(corrected) == row[1]
        actual = [json.loads(item[0]) for item in db.execute(
            'SELECT snapshot_json FROM portfolio_recommendations WHERE run_id=?', (report['run_id'],))]
    assert len(corrected['recommendations']) == 30
    assert sorted(map(_json, corrected['recommendations'])) == sorted(map(_json, actual))
    assert Counter(row['profile_id'] for row in actual) == dict.fromkeys(AGENT_IDS, 3)


def test_freeze_revision_refuses_missing_variant_index_without_changing_original(ledger):
    original = completed_report()
    ledger.save_run(original)
    ledger.freeze_run(original['run_id'])
    frozen = ledger.list_freezes()[0]
    later = NOW + timedelta(minutes=2)
    ledger.now = lambda: later
    report = completed_report(run='TESTONLY-incomplete-index', observed=later)
    ledger.save_run(report)
    # Deliberate corruption is confined to this test's freshly owned database.
    with ledger._connection() as db:
        db.execute('DELETE FROM portfolio_recommendations WHERE recommendation_id=(SELECT recommendation_id '
                   'FROM portfolio_recommendations WHERE run_id=? LIMIT 1)', (report['run_id'],))
    with pytest.raises(ValueError, match='ajánlatindex'):
        ledger.revise_freeze(report['run_id'], frozen['snapshot_hash'], 'TESTONLY incomplete index')
    assert len(ledger.list_freeze_history()) == 1


@pytest.mark.parametrize('new_owner_state', ['INCOMPLETE', 'ABSTAIN', 'NO_PUBLISHED_CARD'])
def test_owner_without_new_card_retains_both_prior_indexes_and_immutable_payloads(ledger, new_owner_state):
    old = completed_report()
    ledger.save_run(old)
    before = _tables(ledger)
    old_tickets = {row[0]: row[5:7] for row in before['portfolio_tickets']}
    old_recommendations = {row[0]: row[5] for row in before['portfolio_recommendations']}
    later = NOW + timedelta(minutes=1)
    ledger.now = lambda: later
    new = completed_report(run='TESTONLY-partial-replacement', observed=later)
    retained_owner = AGENT_IDS[-1]
    outcome = new['ten_agent_decisions']['owners'][-1]
    if new_owner_state == 'INCOMPLETE':
        outcome.update(status='INCOMPLETE_NO_RETRY', reason='TESTONLY interrupted owner')
        outcome.pop('decision')
        new['ten_agent_decisions']['status'] = 'PARTIAL'
    elif new_owner_state == 'ABSTAIN':
        decision = outcome['decision']
        decision.update(status='ABSTAIN', option_ids=[], selected_options=[], option_hash=digest([]))
        for receipt in decision['tool_receipts']:
            receipt.update(option_hash=decision['option_hash'], own_option_ids=[])
        # Exercise the compatibility record as well as the completed owner row.
        new['recommendations'] = [{'profile_id': retained_owner, 'status': 'ABSTAINED', 'legs': []}]
    new['generated_tickets'] = [card for card in new['generated_tickets']
                                if card['profile_id'] != retained_owner]
    new['tickets'] = [card for card in new['tickets'] if card['profile_id'] != retained_owner]
    new['ten_agent_decisions']['canonical_ticket_count'] = 18
    ledger.save_run(new)

    tickets = ledger.list_tickets(date=DAY)
    recommendations = ledger.list_recommendations(date=DAY)
    assert len(tickets) == len(recommendations) == 20
    assert Counter(row['profile_id'] for row in tickets) == dict.fromkeys(AGENT_IDS, 2)
    assert Counter(row['profile_id'] for row in recommendations) == dict.fromkeys(AGENT_IDS, 2)
    for rows in (tickets, recommendations):
        assert all(row['run_id'] == old['run_id'] for row in rows
                   if row['profile_id'] == retained_owner)
        assert all(row['run_id'] == new['run_id'] for row in rows
                   if row['profile_id'] != retained_owner)
    assert {row['ticket_id'] for row in tickets} == {
        row['portfolio_ticket_id'] for row in recommendations
    }
    after = _tables(ledger)
    assert {row[0]: row[5:7] for row in after['portfolio_tickets']
            if row[1] == old['run_id']} == old_tickets
    assert {row[0]: row[5] for row in after['portfolio_recommendations']
            if row[1] == old['run_id']} == old_recommendations
    assert ledger.list_runs(date=DAY) == [new, old]

