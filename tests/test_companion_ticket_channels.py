"""TESTONLY: protected construction and separate channel publication, never live I/O."""
import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from goal_analysis.agents.codex_review import CodexReviewer
from goal_analysis.agents.profile_decision_runner import DecisionJournal
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.companion_tickets import (
    CHANNELS,
    append_companion_cards,
    refresh_companion_capacity,
    run_companion_channels,
    validate_companion_cards,
)
from goal_analysis.features.daily_223_history import enrich_daily223_input
from goal_analysis.generated_tickets import finalize_generated
from goal_analysis.jobs.agent_snapshot import build_normal_snapshot, digest
from goal_analysis.jobs.ten_agent_ticket_evidence import build_ten_agent_ticket_evidence
from goal_analysis.storage.portfolio_ledger import PortfolioLedger, _agent_variant_keys

NOW = datetime(2026, 11, 14, 10, tzinfo=UTC)
SETTINGS = {'operating_contract_version': 2, 'enabled_profiles': list(AGENT_IDS),
            'target_ticket_count': 15, 'max_ticket_count': 30, 'stake_eur': 1.0}


@pytest.fixture(autouse=True)
def owned_database_only(tmp_path, monkeypatch):
    owned = tmp_path.resolve()
    assert not owned.is_relative_to(Path(__file__).resolve().parents[1])
    assert not owned.is_relative_to(Path('C:/AI-Work/GoalAnalysis').resolve())
    original = sqlite3.connect

    def connect(database, *args, **kwargs):
        assert Path(database).resolve().is_relative_to(owned)
        return original(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', connect)


def _frame(tmp_path, *, run='TESTONLY-companion-one', offset=0):
    observed = NOW + timedelta(minutes=offset)
    rows = []
    for team in range(1, 9):
        for sample in range(6):
            rows.append({'fixture_id': str(1000 + team * 10 + sample), 'league_id': '78',
                'home_team_id': str(team if team % 2 else 100 + team),
                'away_team_id': str(100 + team if team % 2 else team), 'status': 'FT',
                'kickoff': (NOW - timedelta(days=sample + 1)).isoformat(),
                'observed_at': (NOW - timedelta(hours=1)).isoformat(),
                'full_time_home': 2, 'full_time_away': 1, 'first_half_home': 1, 'first_half_away': 0,
                'source_id': f'TESTONLY-history-{team}-{sample}'})
    history = {'schema_version': 1, 'kind': 'DAILY223_HISTORY', 'provider': 'api_football',
        'collected_at': (NOW - timedelta(minutes=30)).isoformat(), 'records': rows, 'sources': []}
    history['snapshot_sha256'] = digest(history)
    candidates = []
    for index, price in enumerate((2.0, 2.05, 3.0, 1.1), start=1):
        identifier = 9000 + index
        candidates.append({'candidate_id': f'TESTONLY-quote-{identifier}',
            'fixture_id': f'api_football:{identifier}', 'api_football_fixture_id': str(identifier),
            'api_football_home_team_id': str(index * 2 - 1), 'api_football_away_team_id': str(index * 2),
            'api_football_league_id': '78', 'source_provider': 'api_football',
            'home_team': f'TESTONLY Home {index}', 'away_team': f'TESTONLY Away {index}',
            'competition': 'TESTONLY League', 'competition_type': 'LEAGUE', 'provider_status': 'NS',
            'kickoff': (NOW + timedelta(hours=6)).isoformat(), 'event_status': 'SCHEDULED',
            'market_key': 'totals_2_5', 'selection_key': 'over', 'period': 'FULL_TIME',
            'settlement': 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES',
            'decimal_price': price, 'bookmaker': 'test_api_football_8', 'region': 'eu', 'currency': 'EUR',
            'quote_available': True, 'quote_provider': 'api_football', 'provider_market_id': 5,
            'quote_source_id': f'https://v3.football.api-sports.io/odds?fixture={identifier}&bookmaker=8&bet=5',
            'quote_source_sha256': 'a' * 64, 'quote_observed_at': observed.isoformat(),
            'quoted_at': None, 'evidence': [], 'sensitivity_note': 'TESTONLY; no wager.'})
    candidate_input = {'schema_version': 1, 'date': NOW.date().isoformat(),
        'observed_at': observed.isoformat(), 'preferred_bookmakers': ['test_api_football_8'],
        'candidates': candidates}
    fixtures = [{key: row[key] for key in ('fixture_id', 'api_football_fixture_id',
        'api_football_home_team_id', 'api_football_away_team_id', 'source_provider', 'home_team',
        'away_team', 'competition', 'competition_type', 'provider_status', 'kickoff')} for row in candidates]
    fixtures.append({**fixtures[-1], 'fixture_id': 'api_football:9999', 'api_football_fixture_id': '9999'})
    ids = [row['fixture_id'] for row in fixtures]
    scope = {'scope': 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY', 'date': candidate_input['date'],
        'status': 'COMPLETE', 'provider_calendar_status': 'COMPLETE', 'routing_status': 'COMPLETE',
        'full_product_run': True, 'provider_fixture_ids': ids, 'events': deepcopy(fixtures)}
    enriched = enrich_daily223_input(candidate_input, history)
    enriched.update(calendar_universe=deepcopy(fixtures), calendar_scope_receipt=deepcopy(scope))
    artifacts = {'candidate_input': candidate_input, 'history': history, 'calendar': {'scope_receipt': scope},
                 'fixtures': {'records': fixtures}, 'numeric_models': {}}
    snapshot = build_normal_snapshot(run_id=run, target_date=candidate_input['date'],
        snapshot_as_of=observed.isoformat(), artifacts=artifacts, enriched=enriched)
    owners = []
    for owner in AGENT_IDS:
        identity = {'agent_id': owner, 'session_id': run + '-' + owner, 'snapshot_id': snapshot.snapshot_id}
        owners.append({**identity, 'status': 'DECISION_COMPLETED', 'decision': {**identity,
            'version': 'profile-owner-decision-v2-ten-agents',
            'method_version': get_agent_definition(owner).method_version, 'option_ids': [],
            'selected_options': [], 'status': 'ABSTAIN'}})
    report = {'run_id': run, 'date': candidate_input['date'], 'observed_at': observed.isoformat(),
        'finished_at': observed.isoformat(), 'settings': deepcopy(SETTINGS), 'team': {},
        'generated_tickets': [], 'tickets': [], 'profiles': [], 'recommendations': [],
        'generation_capacity': {}, 'ten_agent_decisions': {'version': 'ten-agent-decision-run-v1',
            'agent_count': 10, 'snapshot_id': snapshot.snapshot_id, 'status': 'COMPLETE', 'owners': owners}}
    artifacts['independent_agent_snapshot'] = snapshot.data()
    artifacts['independent_agent_decisions'] = deepcopy(report['ten_agent_decisions'])
    report['ten_agent_decisions']['canonical_ticket_count'] = 0
    journal = DecisionJournal(tmp_path / run, snapshot)
    return report, artifacts, enriched, snapshot, journal


def _run(frame):
    report, artifacts, enriched, snapshot, journal = frame
    return run_companion_channels(report, snapshot=snapshot, artifacts=artifacts, enriched=enriched,
                                  settings=SETTINGS, journal=journal)


def test_actual_protected_channels_use_full_input_once_and_preserve_unknown_quote_clock(tmp_path, monkeypatch):
    from goal_analysis.engine import portfolio
    from goal_analysis.jobs import daily_223_live

    frame = _frame(tmp_path)
    report, artifacts, enriched, snapshot, journal = frame
    originals = deepcopy((artifacts['candidate_input'], artifacts['history'], enriched, SETTINGS))
    calls = []
    daily, legacy = portfolio.build_portfolio, daily_223_live._build_for_one_bookmaker

    def daily_capture(payload, settings, **kwargs):
        calls.append(('daily', deepcopy(payload), deepcopy(settings)))
        return daily(payload, settings, **kwargs)

    def legacy_capture(payload, history):
        calls.append(('legacy', deepcopy(payload), deepcopy(history)))
        return legacy(payload, history)

    monkeypatch.setattr(portfolio, 'build_portfolio', daily_capture)
    monkeypatch.setattr(daily_223_live, '_build_for_one_bookmaker', legacy_capture)
    _run(frame)
    assert [row[0] for row in calls] == ['daily', 'legacy']
    assert calls[0][1] == enriched and calls[1][1] == artifacts['candidate_input']
    assert calls[1][2] == artifacts['history'] and len(calls[0][1]['calendar_universe']) == 5
    assert (artifacts['candidate_input'], artifacts['history'], enriched, SETTINGS) == originals
    channels = report['companion_ticket_channels']['channels']
    assert [row['channel_id'] for row in channels] == list(CHANNELS)
    assert all(row['status'] == 'COMPLETE' and row['input_counts'] == {'calendar': 5, 'candidates': 4}
               and row['shared_snapshot_id'] == snapshot.snapshot_id for row in channels)
    cards = report['generated_tickets']
    assert len(cards) == 2 and len({card['ticket_id'] for card in cards}) == 2
    assert all(card['profile_id'] == card['producer_profile_id'] == 'daily223' and card['status'] == 'DRAFT'
               and card['publication_scope'] == 'RESEARCH' and card['playable'] is False
               and 'agent_decision' not in card and card['combined_price'] == pytest.approx(12.3)
               and all(leg['quoted_at'] is None for leg in card['legs']) for card in cards)
    assert report['companion_ticket_channels']['shared_exposure']
    assert all((journal.directory / (key + '.companion-result.json')).exists() for key in CHANNELS)
    assert len(_agent_variant_keys(report)) == 2 and report['construction_status'] == 'PARTIAL'
    before = deepcopy(report)
    append_companion_cards(report)
    assert report == before
    with pytest.raises(ValueError, match='ALREADY_ATTEMPTED'):
        _run(frame)
    assert len(calls) == 2


def test_existing_claim_prevents_retry_and_one_failed_constructor_does_not_erase_other_channel(tmp_path, monkeypatch):
    from goal_analysis.engine import portfolio

    frame = _frame(tmp_path)

    def failed(*args, **kwargs):
        raise ValueError('TESTONLY optional constructor failure')

    monkeypatch.setattr(portfolio, 'build_portfolio', failed)
    _run(frame)
    report = frame[0]
    assert [row['status'] for row in report['companion_ticket_channels']['channels']] == ['FAILED_NO_RETRY', 'COMPLETE']
    assert [row['channel_id'] for row in report['generated_tickets']] == ['legacy_daily223']
    fresh = _frame(tmp_path, run='TESTONLY-exclusive-claim')
    fresh[-1].write_once('daily_portfolio.companion-claim.json', {'TESTONLY': 'already attempted'})
    with pytest.raises(FileExistsError):
        _run(fresh)
    assert 'companion_ticket_channels' not in fresh[0]


def test_channel_binding_rejects_foreign_products_and_preserves_all_agent_decisions(tmp_path):
    frame = _frame(tmp_path)
    _run(frame)
    report = frame[0]
    owner_before = deepcopy(report['ten_agent_decisions'])

    def matched_retag(value, field, changed):
        value['companion_ticket_channels']['channels'][0][field] = deepcopy(changed)
        value['generated_tickets'][0]['companion_decision'][field] = deepcopy(changed)

    for mutate in (
        lambda value: value['generated_tickets'][0].update(channel_id='legacy_daily223'),
        lambda value: value['generated_tickets'][0].update(status='READY', playable=True),
        lambda value: value['companion_ticket_channels']['channels'][0].update(run_id='FOREIGN'),
        lambda value: value['generated_tickets'][0]['legs'][0].update(decimal_price=99),
        lambda value: matched_retag(value, 'producer_report_sha256', 'a' * 64),
        lambda value: matched_retag(value, 'claim_sha256', 'b' * 64),
        lambda value: matched_retag(value, 'producer', {'module': 'foreign', 'function': 'FOREIGN', 'version': '0'}),
        lambda value: matched_retag(value, 'input_sha256', 'c' * 64),
        lambda value: matched_retag(value, 'calendar_ids_sha256', 'd' * 64),
    ):
        tampered = deepcopy(report)
        mutate(tampered)
        with pytest.raises(ValueError):
            _agent_variant_keys(tampered)
    assert report['ten_agent_decisions'] == owner_before
    assert set(validate_companion_cards(report)) == {row['ticket_id'] for row in report['generated_tickets']}
    evidence = build_ten_agent_ticket_evidence(report, frame[1])
    assert all(row['own_selection']['status'] == 'BOUND_PROTECTED_CONSTRUCTOR_PRODUCT'
               for row in evidence['cards'])
    changed = deepcopy(report)
    changed['companion_ticket_channels']['channels'][0]['selected_source_rows'][0]['TESTONLY_extra'] = 'unbound'
    # Product metadata alone is unchanged; the independent artifact join rejects the substituted raw row.
    assert validate_companion_cards(changed)
    checked = build_ten_agent_ticket_evidence(changed, frame[1])
    assert checked['cards'][0]['own_selection']['status'] == 'UNKNOWN'
    assert 'COMPANION_SHARED_INPUT_CHANGED' in checked['cards'][0]['own_selection']['reason']
    old = {**deepcopy(report), 'generated_tickets': [], 'tickets': []}
    del old['companion_ticket_channels']
    assert _agent_variant_keys(old) == {}
    old['generated_tickets'] = deepcopy(report['generated_tickets'])
    with pytest.raises(ValueError, match='COMPANION_MARKER_REQUIRED'):
        _agent_variant_keys(old)


def test_same_profile_channels_persist_reopen_and_failed_channel_retains_its_previous_index(tmp_path, monkeypatch):
    from goal_analysis.engine import portfolio

    first = _frame(tmp_path)
    _run(first)
    original = deepcopy(first[0])
    database = tmp_path / 'TESTONLY-companions.sqlite3'
    ledger = PortfolioLedger(database, now=lambda: NOW + timedelta(minutes=1))
    assert ledger.save_run(first[0])['ticket_count'] == 2
    assert ledger.save_run(deepcopy(first[0]))['saved'] is False
    reopened = PortfolioLedger(database, now=lambda: NOW + timedelta(minutes=2))
    assert reopened.list_runs() == [original]
    assert reopened.freeze_run(original['run_id'])['recommendation_count'] == 2
    with reopened._connection() as db:
        freeze_before = db.execute('SELECT snapshot_json,snapshot_hash FROM portfolio_freezes').fetchone()
        frozen_bytes = tuple(freeze_before)
    with reopened._connection() as db:
        recs = db.execute('SELECT recommendation_id,snapshot_json,active FROM portfolio_recommendations').fetchall()
    assert len(recs) == len({row[0] for row in recs}) == 2 and all(row[2] == 1 for row in recs)
    assert {json.loads(row[1])['channel_id'] for row in recs} == set(CHANNELS)
    next_frame = _frame(tmp_path, run='TESTONLY-companion-two', offset=2)

    def failed(*args, **kwargs):
        raise ValueError('TESTONLY daily unavailable on later run')

    monkeypatch.setattr(portfolio, 'build_portfolio', failed)
    _run(next_frame)
    assert reopened.save_run(next_frame[0])['ticket_count'] == 1
    expected = {original['generated_tickets'][0]['ticket_id'], next_frame[0]['generated_tickets'][0]['ticket_id']}
    with reopened._connection() as db:
        active = db.execute('SELECT ticket_id FROM portfolio_tickets WHERE active=1').fetchall()
        recommendations = db.execute('SELECT snapshot_json FROM portfolio_recommendations WHERE active=1').fetchall()
    assert {row[0] for row in active} == expected
    assert {json.loads(row[0])['portfolio_ticket_id'] for row in recommendations} == expected
    assert reopened.list_runs() == [next_frame[0], original]
    assert first[0] == original
    with reopened._connection() as db:
        assert tuple(db.execute('SELECT snapshot_json,snapshot_hash FROM portfolio_freezes').fetchone()) == frozen_bytes


def test_main_slot_coverage_counts_owners_and_channels_not_twelve_variants(tmp_path):
    frame = _frame(tmp_path)
    _run(frame)
    report = frame[0]
    companions = deepcopy(report['generated_tickets'])
    report['generated_tickets'] = [{'profile_id': AGENT_IDS[0]} for _ in range(12)] + companions
    refresh_companion_capacity(report)
    assert report['generation_capacity']['filled_main_slots'] == 3
    assert report['generation_capacity']['target_met'] is False
    report['generated_tickets'] = [{'profile_id': owner} for owner in AGENT_IDS] + companions
    refresh_companion_capacity(report)
    assert report['generation_capacity']['filled_main_slots'] == 12
    assert report['generation_capacity']['filled_agent_owner_slots'] == 10
    assert report['generation_capacity']['filled_companion_slots'] == 2
    assert report['generation_capacity']['target_met'] is True
    assert report['generation_capacity']['financial_wager_cap_changed'] is False
    # The existing default finalizer has no companion marker and retains its prior behavior.
    old = {'settings': {'operating_contract_version': 1}, 'sentinel': 'TESTONLY unchanged'}
    assert finalize_generated(old, old['settings']) == old


def test_actual_profile_prompt_uses_one_main_per_owner_and_twelve_places_without_fifteen_target():
    reviewer = CodexReviewer.__new__(CodexReviewer)
    seen = []

    def capture(payload, usage):
        seen.append(payload)
        return {'output': [{'content': [{'type': 'output_text', 'text': '{}'}]}]}

    reviewer._request = capture
    reviewer.request_profile_decision({'phase': 'PLAN'}, timeout=30)
    assert len(seen) == 1
    instructions = seen[0]['instructions']
    assert 'fifteen-ticket' not in instructions and 'one main card' in instructions
    assert 'ten owners plus the separate Daily and legacy channels' in instructions
    assert 'Do not add filler' in instructions and 'All twelve data families' in instructions
