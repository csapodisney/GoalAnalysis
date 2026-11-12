"""TESTONLY full source-access -> own tools -> audit -> SQLite system fixture.

The host answers/native event objects are synthetic. Source pages, validators,
local simulator tools, canonical products, audit and owned persistence are real.
No provider, native CLI, fit, paid inference, production data or wager is used.
"""

import socket
import sqlite3
import subprocess
from collections import Counter
from copy import deepcopy
from pathlib import Path

import test_agent_portfolio as product_fixture
from test_profile_source_host import finish_fake_source_reply

from goal_analysis.agents.profile_decision import SIMULATOR_ROLES
from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.jobs.ten_agent_normal import NormalAgentExecutor
from goal_analysis.jobs.ticket_evidence import build_ticket_evidence
from goal_analysis.storage.portfolio_ledger import PortfolioLedger, _hash, _json


def test_full_day_source_access_ten_owners_twenty_cards_audit_and_owned_sqlite(tmp_path, monkeypatch):
    owned_root = tmp_path.resolve()
    source_root = Path(__file__).resolve().parents[1]
    assert not owned_root.is_relative_to(source_root)
    assert not owned_root.is_relative_to(Path('C:/AI-Work/GoalAnalysis').resolve())
    original_connect = sqlite3.connect

    def owned_connect(database, *args, **kwargs):
        assert Path(database).resolve().is_relative_to(owned_root)
        return original_connect(database, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError('TESTONLY source system forbids network and native processes')

    monkeypatch.setattr(sqlite3, 'connect', owned_connect)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket.socket, 'bind', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)

    _, artifacts, enriched, club, _, _, proposals = product_fixture.inputs()
    full_ids = artifacts['calendar']['scope_receipt']['provider_fixture_ids']
    assert len(full_ids) == len(set(full_ids)) == 80
    assert set(proposals) == set(AGENT_IDS)
    assert all(len(rows) == 2 for rows in proposals.values())
    calls = []

    def host(request, *, timeout, source_access):
        assert timeout > 0
        calls.append(deepcopy(request))
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        data = request['data']
        assert data['snapshot']['fixture_ids'] == full_ids
        assert len(data['snapshot']['families']) == 12
        if request['phase'] == 'PLAN':
            own = deepcopy(proposals[request['agent_id']])
            answer = {**identity, 'options': own,
                'fixture_reviews': [{'fixture_id': key,
                    'reason': 'TESTONLY complete supplied day reviewed by this owner',
                    'rejected': False} for key in full_ids],
                'tool_requests': [{'role': role, 'parameters': {}} for role in SIMULATOR_ROLES]}
            reads = [key for option in own for key in option['source_candidate_ids']]
        else:
            selected = data['own_option_admission']['accepted_option_ids']
            assert selected == [row['option_id'] for row in proposals[request['agent_id']]]
            answer = {**identity, 'option_ids': selected,
                'reason': 'TESTONLY owner selects its two distinct source-bound variants',
                'tool_interpretations': [{'request_id': row['request']['request_id'],
                    'interpretation': 'TESTONLY supplied own result; UNKNOWN remains uncertainty'}
                    for row in data['tool_results']],
                'family_uses': [{'family': key,
                    'status': {'AVAILABLE': 'AVAILABLE_AND_CONSIDERED', 'UNKNOWN': 'MISSING',
                               'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}[family['status']],
                    'value_sha256': family['value_sha256'], 'option_ids': selected,
                    'reason': 'TESTONLY declared interpretation, not causal or all-row use proof',
                    'risk': 'Missing information stays UNKNOWN, never a neutral probability'}
                    for key, family in data['snapshot']['families'].items()]}
            reads = []
        answer['host_usage'] = {'requests': 1, 'usage_complete': False, 'cost_amount': None,
                               'reason': 'TESTONLY synthetic host; no measured paid usage'}
        return finish_fake_source_reply(source_access, request, reads, answer)

    executor = NormalAgentExecutor(host=host, journal_root=tmp_path / 'journals',
        clock=lambda: product_fixture.NOW, enable_source_access=True)
    report = executor(run_id='TESTONLY-source-access-twenty-card-system',
        target_date=product_fixture.NOW.date(), as_of=product_fixture.NOW, artifacts=artifacts,
        fixture_records=artifacts['fixtures']['records'], enriched=enriched, club_enriched=club,
        candidate_context={}, weather_by_fixture={}, historical_runs=[], feedback={},
        settings=product_fixture.SETTINGS, team_plan={})

    owners = report['ten_agent_decisions']['owners']
    assert report['ten_agent_decisions']['status'] == 'COMPLETE'
    assert Counter((row['agent_id'], row['phase']) for row in calls) == {
        (owner, phase): 1 for owner in AGENT_IDS for phase in ('PLAN', 'FINAL')}
    assert len(list((tmp_path / 'journals').rglob('bundle.json'))) == 1
    assert len(list((tmp_path / 'journals').rglob('*.source-access.json'))) == 20
    assert len(owners) == 10 and all(row['status'] == 'DECISION_COMPLETED' for row in owners)
    for owner in owners:
        evidence = owner['decision_evidence']
        assert evidence['version'] == 'owner-decision-evidence-v3-source-access'
        assert len(evidence['validated_own_options']) == 2
        assert {row['request']['role'] for row in evidence['tool_results']} == set(SIMULATOR_ROLES)
        assert len(evidence['tool_results']) == len(owner['decision']['tool_receipts']) == 2
        assert all(row['result']['status'] in {'AVAILABLE', 'CACHED', 'UNKNOWN'}
                   for row in evidence['tool_results'])
        assert evidence['source_access']['PLAN']['receipt']['successful_reads'] >= 8
        assert evidence['source_access']['FINAL']['receipt']['successful_reads'] == 0

    cards = report['generated_tickets']
    assert len(cards) == len({row['ticket_id'] for row in cards}) == 20
    assert Counter(row['profile_id'] for row in cards) == dict.fromkeys(AGENT_IDS, 2)
    assert report['generation_capacity']['shortfall'] == 0
    assert report['construction_status'] == 'COMPLETE'
    assert all(row['status'] == 'DRAFT' and not row['playable'] and row['stake_eur'] is None
               and row['real_wager_placed'] is False for row in cards)

    audit = build_ticket_evidence(report, artifacts)
    assert len(audit['cards']) == 20 and len(audit['owner_checks']) == 10
    assert audit['external_requests'] == audit['new_tool_or_model_executions'] == 0
    assert all(row['own_selection']['status'] == 'EXACT_OWN_CANONICAL_SOURCE_BOUND'
               for row in audit['cards'])
    for checked in audit['owner_checks'].values():
        assert checked['status'] == 'OWN_REQUEST_REPLY_AND_CONTRACT_BOUND'
        assert checked['source_access_validation']['PLAN']['read_count'] >= 8
        assert checked['source_access_validation']['FINAL']['read_count'] == 0
        assert checked['source_access_validation']['PLAN']['causal_feature_use_proven'] is False
    report['ticket_evidence'] = audit
    original = deepcopy(report)
    ledger = PortfolioLedger(tmp_path / 'TESTONLY-source-system.sqlite3', now=lambda: product_fixture.NOW)
    assert ledger.save_run(report) == {'run_id': report['run_id'], 'saved': True, 'ticket_count': 20}
    with ledger._connection() as database:
        stored = database.execute('SELECT * FROM portfolio_tickets ORDER BY rowid').fetchall()
        assert len(stored) == 20
        for row, source in zip(stored, cards, strict=True):
            assert row['snapshot_json'] == _json(source) and row['snapshot_hash'] == _hash(source)
        assert database.execute('SELECT count(*) FROM portfolio_wagers').fetchone()[0] == 0
    reopened = PortfolioLedger(ledger.path, now=lambda: product_fixture.NOW)
    day = product_fixture.NOW.date().isoformat()
    assert reopened.list_runs(date=day) == [original]
    tickets = reopened.list_tickets(date=day, monetary_contract='CURRENT_HASH_V2')
    recommendations = reopened.list_recommendations(date=day)
    assert len(tickets) == len(recommendations) == 20
    assert Counter(row['profile_id'] for row in tickets) == dict.fromkeys(AGENT_IDS, 2)
    assert all(row['active'] and not row['played'] for row in tickets)
    assert {row['portfolio_ticket_id'] for row in recommendations} == {row['ticket_id'] for row in cards}
    assert len({row['recommendation_id'] for row in recommendations}) == 20
    by_id = {row['ticket_id']: row for row in cards}
    for row in recommendations:
        source = by_id[row['portfolio_ticket_id']]
        assert row['legs'] == source['legs'] and row['agent_decision'] == source['agent_decision']
        assert row['active'] and not row['played'] and row['financial'] is None
    assert reopened.list_tickets(date=day, mode='actual') == []
    assert report == original
