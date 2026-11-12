"""TESTONLY output integration; no host, collector, model, HTTP server or real DB.

The pure fixture represents already completed decisions. It does not repeat the
runner/construction tests or claim that fixture reasoning used real sports data.
All persistence below is confined to the separately owned pytest directory.
"""

import json
import socket
import sqlite3
import subprocess
import threading
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from goal_analysis import portfolio_dashboard as dashboard
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.generated_tickets import CORE_MARKETS, refresh_generation_capacity
from goal_analysis.jobs.agent_portfolio import publish_agent_decisions
from goal_analysis.jobs.agent_snapshot import FAMILIES, digest
from goal_analysis.jobs.portfolio import write_portfolio_bundle
from goal_analysis.storage.portfolio_ledger import PortfolioLedger, _hash, _json
from goal_analysis.tipsters.catalog import CATALOG_VERSION

NOW = datetime(2026, 11, 12, 10, tzinfo=UTC)
DAY = NOW.date().isoformat()
SOURCE_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = Path('C:/AI-Work/GoalAnalysis').resolve()
SETTLEMENT = 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES'


@pytest.fixture(autouse=True)
def owned_output_boundary(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    assert not root.is_relative_to(SOURCE_ROOT)
    assert not root.is_relative_to(PRODUCTION_ROOT)
    marker = root / 'TESTONLY-TEN-AGENT-OUTPUT.json'
    marker.write_text(json.dumps({'TESTONLY': True, 'scope': 'ten-agent-output'}), encoding='utf-8')
    connect = sqlite3.connect

    def owned_connect(database, *args, **kwargs):
        assert Path(database).resolve().is_relative_to(root)
        assert marker.exists()
        return connect(database, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError('TESTONLY output integration forbids network and processes')

    monkeypatch.setattr(sqlite3, 'connect', owned_connect)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket.socket, 'bind', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def completed_output(*, run_id='TESTONLY-ten-output-one', variants=2, observed=NOW):
    """Handbuilt persisted-output shape, with explicit UNKNOWN tool evidence."""
    snapshot_id = digest({'TESTONLY': True, 'run_id': run_id})
    settings = {'operating_contract_version': 2, 'enabled_profiles': list(AGENT_IDS),
                'target_ticket_count': 15, 'max_ticket_count': 30, 'stake_eur': 1.0}
    report = {'schema_version': 3, 'module': 'ARTHUR_PORTFOLIO', 'run_id': run_id,
        'date': DAY, 'observed_at': observed.isoformat(), 'finished_at': observed.isoformat(),
        'settings': settings, 'policy': deepcopy(settings), 'construction_status': 'COMPLETE',
        'tickets': [], 'recommendations': [], 'preview_tickets': [], 'profile_proposals': [],
        'generated_tickets': [], 'real_wager_placed': False,
        'astra': {'status': 'COMPLETE', 'mode': 'TEN_INDEPENDENT_OWNER_DECISIONS',
                  'reviews': [], 'legacy_candidate_review_performed': False,
                  'usage': {'requests': None, 'cost_amount': None, 'usage_complete': False}},
        'agent_source_readers': {'news': {'status': 'UNKNOWN'},
                                 'historical_strategy': {'status': 'UNKNOWN'}},
        'ten_agent_decisions': {'version': 'ten-agent-decision-run-v1', 'snapshot_id': snapshot_id,
                               'agent_count': 10, 'status': 'COMPLETE', 'owners': []}}
    source_quotes = []
    for owner_index, owner in enumerate(AGENT_IDS):
        definition = get_agent_definition(owner)
        session = f'{run_id}-session-{owner}'
        options = []
        for variant in range(variants):
            option_id = f'{owner}-own-variant-{variant}'
            market, selection, period = CORE_MARKETS.get(owner, ('h2h', 'home', 'FULL_TIME'))
            selection = selection or 'home'
            bet = {'h2h': 1, 'totals_2_5': 5, 'btts': 8}[market]
            legs = []
            for number in range(4):
                fixture = 10000 + owner_index * 100 + variant * 10 + number
                legs.append({'fixture_id': f'api_football:{fixture}',
                    'source_provider': 'api_football', 'api_football_fixture_id': str(fixture),
                    'api_football_home_team_id': str(fixture * 2),
                    'api_football_away_team_id': str(fixture * 2 + 1),
                    'candidate_id': f'{run_id}-candidate-{fixture}',
                    'home_team': f'TESTONLY Home [{fixture}]', 'away_team': f'TESTONLY Away [{fixture}]',
                    'kickoff': (NOW + timedelta(hours=8)).isoformat(),
                    'competition': 'TESTONLY league', 'competition_type': 'LEAGUE',
                    'provider_status': 'NS', 'event_status': 'SCHEDULED',
                    'market_key': market, 'selection_key': selection, 'period': period,
                    'settlement': SETTLEMENT, 'decimal_price': 2.0,
                    'bookmaker': 'test_api_football_8', 'region': 'eu', 'currency': 'EUR',
                    'quote_provider': 'api_football', 'quote_available': True,
                    'quote_source_id': f'https://v3.football.api-sports.io/odds?fixture={fixture}&bookmaker=8&bet={bet}',
                    'quote_source_sha256': 'a' * 64, 'provider_market_id': bet,
                    'quote_observed_at': observed.isoformat(), 'quoted_at': None,
                    'sensitivity_note': 'TESTONLY: historical evaluation and forecast coverage UNKNOWN.'})
            source_quotes.extend(deepcopy(legs))
            card = {'ticket_id': f'{run_id}-ticket-{owner}-{variant}', 'profile_id': owner,
                'profile_name': definition.name, 'date': DAY, 'created_at': observed.isoformat(),
                'catalog_version': CATALOG_VERSION, 'combined_price': 16.0,
                'bookmaker': 'test_api_football_8', 'currency': 'EUR',
                'status': 'DRAFT', 'publication_scope': 'RESEARCH', 'playable': False,
                'stake_eur': None, 'real_wager_placed': False, 'construction_verified': True,
                'legs': legs, 'reasoning': ['TESTONLY completed own output; no real inference claimed.'],
                'quality_warnings': ['HISTORICAL_EVALUATION_UNKNOWN', 'PROVIDER_QUOTE_CLOCK_UNKNOWN'],
                'agent_product_binding': {'version': 'ten-agent-canonical-product-bridge-v1',
                    'snapshot_id': snapshot_id, 'agent_id': owner, 'option_id': option_id,
                    'run_id': run_id, 'target_date': DAY, 'source_as_of': observed.isoformat(),
                    'source_candidate_ids': [leg['candidate_id'] for leg in legs],
                    'source_candidate_input_sha256': digest({'TESTONLY': True, 'run_id': run_id}),
                    'physical_authentication': 'NOT_PERFORMED_BY_PURE_BRIDGE'}}
            options.append({'option_id': option_id, 'agent_id': owner, 'snapshot_id': snapshot_id,
                'reason': 'TESTONLY own output variant.', 'legs': deepcopy(legs),
                'canonical_card': card, 'canonical_card_sha256': digest(card)})
        option_hash = digest(options)
        receipts = [{'role': role, 'request_id': f'{session}-{role}', 'agent_id': owner,
            'session_id': session, 'snapshot_id': snapshot_id, 'option_hash': option_hash,
            'own_option_ids': [option['option_id'] for option in options], 'status': 'UNKNOWN',
            'value': None, 'reason': 'TESTONLY unavailable evaluation; no fabricated probability.',
            'interpretation': 'Keep missing historical evidence separate from a playable recommendation.'}
            for role in ('candidate_scenarios', 'historical_strategy_evaluation')]
        decision = {'version': 'profile-owner-decision-v2-ten-agents',
            'agent_id': owner, 'session_id': session, 'snapshot_id': snapshot_id,
            'status': 'FINAL_DECISION',
            'method_version': definition.method_version, 'option_hash': option_hash,
            'option_ids': [option['option_id'] for option in options], 'selected_options': options,
            'reason': 'TESTONLY completed own selections.', 'tool_receipts': receipts,
            'family_uses': {family: {'status': 'MISSING', 'value_sha256': digest(None),
                'reason': 'TESTONLY output fixture, not evidence of real data consumption.'}
                for family in FAMILIES}}
        report['ten_agent_decisions']['owners'].append({'agent_id': owner, 'session_id': session,
            'snapshot_id': snapshot_id, 'status': 'DECISION_COMPLETED', 'decision': decision})
        for option in options:
            card = deepcopy(option['canonical_card'])
            card.update(generation_source_status='DRAFT', generation_source_group='ten_agent_decisions')
            card['agent_decision'] = {'agent_id': owner, 'session_id': session,
                'snapshot_id': snapshot_id, 'method_version': definition.method_version,
                'option_id': option['option_id'], 'decision_sha256': digest(decision),
                'canonical_source_card_sha256': option['canonical_card_sha256'],
                'reason': decision['reason'], 'option_reason': option['reason'],
                'family_uses': deepcopy(decision['family_uses']), 'tool_receipts': deepcopy(receipts),
                'selection_authority': 'OWN_FINAL_DECISION_NO_COORDINATOR_RESELECTION'}
            report['generated_tickets'].append(card)
    # Bind the complete source cohort, rather than a fixture label pretending to
    # be its content hash. This still does not authenticate a physical provider.
    source_input = {'candidates': source_quotes}
    source_hash = digest(source_input)
    for outcome in report['ten_agent_decisions']['owners']:
        decision = outcome['decision']
        for option in decision['selected_options']:
            option['canonical_card']['agent_product_binding']['source_candidate_input_sha256'] = source_hash
            option['canonical_card_sha256'] = digest(option['canonical_card'])
        decision['option_hash'] = digest(decision['selected_options'])
        for receipt in decision['tool_receipts']:
            receipt['option_hash'] = decision['option_hash']
        by_option = {option['option_id']: option for option in decision['selected_options']}
        for card in report['generated_tickets']:
            if card['profile_id'] != outcome['agent_id']:
                continue
            option = by_option[card['agent_decision']['option_id']]
            card['agent_product_binding']['source_candidate_input_sha256'] = source_hash
            card['agent_decision'].update(decision_sha256=digest(decision),
                canonical_source_card_sha256=option['canonical_card_sha256'],
                tool_receipts=deepcopy(decision['tool_receipts']))
    report['ten_agent_decisions']['canonical_ticket_count'] = len(report['generated_tickets'])
    refresh_generation_capacity(report)
    report['report_sha256'] = digest(report)
    return report, {'candidate_input': source_input}


def test_profile_compatibility_retains_completed_abstained_and_interrupted_owner_counts():
    report, _ = completed_output()
    result = deepcopy(report['ten_agent_decisions'])
    result['status'] = 'PARTIAL'
    abstention = result['owners'][1]['decision']
    abstention.update(status='ABSTAIN', option_ids=[], selected_options=[],
                      reason='TESTONLY explicit own abstention, not a failed request.')
    for outcome in result['owners'][2:]:
        outcome.update(status='INCOMPLETE', reason='TESTONLY request stopped before completion.')
        outcome.pop('decision')
    # Only Krónikás' already chosen two exact FT over2.5 products are published.
    # No host/tool/generation invocation, alternative search or club construction.
    publish_agent_decisions(report, result, report['settings'], NOW)
    statuses = {row['profile_id']: row for row in report['profiles']}
    assert statuses[AGENT_IDS[0]]['status'] == 'COMPLETE'
    assert statuses[AGENT_IDS[0]]['ticket_count'] == 2 and statuses[AGENT_IDS[0]]['published']
    assert statuses[AGENT_IDS[1]]['status'] == 'ABSTAINED'
    assert statuses[AGENT_IDS[1]]['ticket_count'] == 0 and not statuses[AGENT_IDS[1]]['published']
    assert all(statuses[owner]['status'] == 'PARTIAL' and statuses[owner]['ticket_count'] == 0
               and not statuses[owner]['published'] for owner in AGENT_IDS[2:])
    assert report['construction_status'] == 'PARTIAL' and report['generation_capacity']['shortfall'] == 13
    assert report['tickets'] == []


def test_bundle_writes_twenty_own_variants_and_exact_unknowns_without_new_review(tmp_path):
    report, artifacts = completed_output()
    artifacts['report'] = report
    before = deepcopy(artifacts)
    output = tmp_path / 'TESTONLY-report-bundle'
    write_portfolio_bundle(output, {'artifacts': artifacts})

    assert json.loads((output / 'report.json').read_text('utf-8')) == before['report']
    assert json.loads((output / 'candidate_input.json').read_text('utf-8')) == before['candidate_input']
    markdown = (output / 'report.md').read_text('utf-8')
    assert markdown.count('## ') == 20
    assert markdown.count('DRAFT · RESEARCH') == 20
    assert markdown.count('Saját választás indoka: TESTONLY own output variant.') == 20
    assert markdown.count('Szelvényszcenáriók: UNKNOWN') == 20
    assert markdown.count('Történeti stratégiaellenőrzés: UNKNOWN') == 20
    assert markdown.count('Keep missing historical evidence separate from a playable recommendation.') == 40
    for card in report['generated_tickets']:
        for leg in card['legs']:
            assert markdown.count(leg['home_team']) == 1
            assert leg['quoted_at'] is None
    notes = json.loads((output / 'report-sensitivity-notes.json').read_text('utf-8'))
    assert len(notes['entries']) == 80
    assert {entry['status'] for entry in notes['entries']} == {'PENDING_EXACT_REVIEW_NOTE'}
    assert notes['new_provider_or_llm_requests'] == 0
    assert not notes['new_research_or_counterfactual_robustness_claim']
    assert artifacts == before


def test_owned_ledger_preserves_twenty_active_variants_recommendations_and_hashes(tmp_path):
    report, _ = completed_output()
    original = deepcopy(report)
    ledger = PortfolioLedger(tmp_path / 'TESTONLY-output.sqlite3', now=lambda: NOW)
    result = ledger.save_run(report)
    assert result == {'run_id': report['run_id'], 'saved': True, 'ticket_count': 20}
    assert ledger.save_run(deepcopy(report)) == {**result, 'saved': False}
    assert ledger.list_runs(date=DAY) == [original]
    views = ledger.list_tickets(date=DAY, monetary_contract='CURRENT_HASH_V2')
    assert len(views) == 20 and all(row['active'] and not row['played'] for row in views)
    assert Counter(row['profile_id'] for row in views) == dict.fromkeys(AGENT_IDS, 2)
    assert ledger.list_tickets(date=DAY, mode='actual') == []
    recommendations = ledger.list_recommendations(date=DAY)
    assert len(recommendations) == len({row['recommendation_id'] for row in recommendations}) == 20
    assert {row['portfolio_ticket_id'] for row in recommendations} == {
        row['ticket_id'] for row in report['generated_tickets']}
    source_by_id = {row['ticket_id']: row for row in report['generated_tickets']}
    for record in recommendations:
        source = source_by_id[record['portfolio_ticket_id']]
        assert record['legs'] == source['legs']
        assert record['agent_decision'] == source['agent_decision']
        assert record['active'] and not record['played'] and record['financial'] is None
    with ledger._connection() as db:
        stored = db.execute('SELECT * FROM portfolio_tickets ORDER BY rowid').fetchall()
        assert len(stored) == 20
        for row, source in zip(stored, report['generated_tickets'], strict=True):
            assert row['snapshot_json'] == _json(source) and row['snapshot_hash'] == _hash(source)
        assert db.execute('SELECT count(*) FROM portfolio_wagers').fetchone()[0] == 0
    reopened = PortfolioLedger(ledger.path, now=lambda: NOW)
    assert reopened.list_runs(date=DAY) == [original]
    assert len(reopened.list_tickets(date=DAY)) == len(reopened.list_recommendations(date=DAY)) == 20
    assert report == original


def test_new_own_variants_supersede_only_indexes_and_preserve_all_old_snapshots(tmp_path):
    old, _ = completed_output()
    new, _ = completed_output(run_id='TESTONLY-ten-output-two', variants=3,
                              observed=NOW + timedelta(minutes=1))
    ledger = PortfolioLedger(tmp_path / 'TESTONLY-supersession.sqlite3',
                             now=lambda: NOW + timedelta(minutes=2))
    ledger.save_run(old)
    with ledger._connection() as db:
        before_tickets = dict(db.execute('SELECT ticket_id,snapshot_json FROM portfolio_tickets'))
        before_recommendations = dict(db.execute(
            'SELECT recommendation_id,snapshot_json FROM portfolio_recommendations'))
    ledger.save_run(new)
    active = ledger.list_tickets(date=DAY)
    assert len(active) == 30 and {row['run_id'] for row in active} == {new['run_id']}
    assert Counter(row['profile_id'] for row in active) == dict.fromkeys(AGENT_IDS, 3)
    assert len(ledger.list_recommendations(date=DAY)) == 30
    assert len(ledger.list_tickets(date=DAY, mode='archive')) == 50
    assert len(ledger.list_recommendations(date=DAY, archive=True)) == 50
    with ledger._connection() as db:
        for table, key, before in (
            ('portfolio_tickets', 'ticket_id', before_tickets),
            ('portfolio_recommendations', 'recommendation_id', before_recommendations),
        ):
            current = dict(db.execute(f'SELECT {key},snapshot_json FROM {table}'))
            assert all(current[identifier] == payload for identifier, payload in before.items())
        assert db.execute('SELECT count(*) FROM portfolio_wagers').fetchone()[0] == 0
    assert ledger.list_runs(date=DAY) == [new, old]


def test_owned_freeze_preserves_all_twenty_variants_without_creating_wagers(tmp_path):
    report, _ = completed_output()
    ledger = PortfolioLedger(tmp_path / 'TESTONLY-freeze.sqlite3', now=lambda: NOW)
    ledger.save_run(report)
    frozen = ledger.freeze_run(report['run_id'])
    assert frozen['recommendation_count'] == 20
    packages = ledger.list_freezes(date=DAY)
    assert len(packages) == 1 and len(packages[0]['recommendations']) == 20
    assert {row['portfolio_ticket_id'] for row in packages[0]['recommendations']} == {
        row['ticket_id'] for row in report['generated_tickets']}
    with ledger._connection() as db:
        row = db.execute('SELECT snapshot_json,snapshot_hash FROM portfolio_freezes').fetchone()
        snapshot = json.loads(row['snapshot_json'])
        assert snapshot['report'] == report and row['snapshot_hash'] == _hash(snapshot)
        assert len(snapshot['recommendations']) == 20
        assert db.execute('SELECT count(*) FROM portfolio_wagers').fetchone()[0] == 0
    assert ledger.list_tickets(date=DAY, mode='actual') == []


@pytest.fixture
def persisted_dashboard(tmp_path, monkeypatch):
    from goal_analysis.agents import expert_registry
    from goal_analysis.jobs import expert_advisory, monitor_attempt, odds_monitor

    monkeypatch.setattr(expert_registry, 'network_registry', lambda *args: [])
    monkeypatch.setattr(expert_advisory, 'snapshot', lambda *args: {})
    monkeypatch.setattr(odds_monitor, 'monitor_status', lambda *args: {})
    monkeypatch.setattr(monitor_attempt, 'monitor_attempt_status', lambda *args: {})
    monkeypatch.setattr(dashboard, 'automation_snapshot', lambda *args: {'daily': 'OFF', 'legacy': 'OFF'})
    monkeypatch.setattr(dashboard, 'model_environment', dict)
    monkeypatch.setattr(dashboard, 'codex_installed', lambda: False)
    state = dashboard.ArthurState.__new__(dashboard.ArthurState)
    state.root = tmp_path
    state.config_path = tmp_path / 'TESTONLY-absent-config.json'
    state.settings_path = tmp_path / 'TESTONLY-absent-settings.json'
    state.lock = threading.Lock()
    state.runtime = {'running': False, 'last_started_at': None}
    state.team_snapshot = lambda *args: {}
    state.tipster_snapshot = lambda *args: {}
    state.learning_snapshot = lambda: {'learning': None, 'learning_history': []}
    state.source_health = lambda *args: {}
    state.ledger = PortfolioLedger(tmp_path / 'TESTONLY-dashboard.sqlite3', now=lambda: NOW)
    report, _ = completed_output()
    state.ledger.save_run(report)
    return state, report


def test_real_dashboard_reads_all_variants_and_german_text_without_live_promotion(persisted_dashboard):
    state, report = persisted_dashboard
    expected = deepcopy(report['generated_tickets'])
    for full_export in (True, False):
        view = state.snapshot(DAY, full_export=full_export)
        assert view['generated_tickets'] == expected
        assert view['latest']['generated_tickets'] == expected
        assert view['generation_capacity']['generated_count'] == 20
        assert len(view['generated_ticket_states']) == 20
        assert all(row['active'] and not row['played'] for row in view['generated_ticket_states'])
        assert view['tickets'] == []
        text = dashboard.german_ticket_text(view)
        assert text.count('DRAFT · PAPER') == 20
        for card in expected:
            for leg in card['legs']:
                assert text.count(leg['home_team']) == 1
    assert state.ledger.list_runs(date=DAY) == [report]


def test_actual_export_route_retains_twenty_cards_and_full_decisions_without_http_server(persisted_dashboard):
    state, report = persisted_dashboard
    handler_type = dashboard.make_handler(state, 'TESTONLY-no-server-token')
    handler = handler_type.__new__(handler_type)
    handler.server = SimpleNamespace(server_port=0)
    handler.headers = {'Host': '127.0.0.1:0'}
    handler.path = f'/api/export?date={DAY}'
    replies = []

    def capture(value, status=200, **kwargs):
        replies.append((deepcopy(value), status, kwargs))

    handler._send = capture
    handler.do_GET()
    assert len(replies) == 1
    payload, status, metadata = replies[0]
    assert status == 200 and metadata['download'] == f'Arthur-{DAY}.json'
    assert payload['generated_tickets'] == report['generated_tickets']
    assert payload['reports'][0]['ten_agent_decisions'] == report['ten_agent_decisions']
    assert payload['generation_capacity']['generated_count'] == 20
    assert len(payload['generated_ticket_states']) == 20
    assert len(payload['recommendations']) == len(payload['recommendation_archive']) == 20
    assert payload['tickets'] == [] and payload['freezes'] == []
    assert state.ledger.list_runs(date=DAY) == [report]
