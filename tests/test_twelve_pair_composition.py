"""TESTONLY fixed-pair composition: real pure builders/tools, inert native events."""
import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from goal_analysis.agents.profile_decision import SIMULATOR_ROLES
from goal_analysis.agents.profile_roster import AGENT_IDS, COMPANION_AGENT_IDS
from goal_analysis.agents.profile_source_access import SourceAccessKernel
from goal_analysis.agents.profile_source_host import (
    SourceAccessReply,
    canonical,
    finish_source_evidence,
    sha,
)
from goal_analysis.companion_agents import (
    append_companion_agent_cards,
    restore_companion_snapshot,
    shared_data_binding,
    snapshot_reference,
    validate_companion_agent_cards,
)
from goal_analysis.features.daily_223_history import enrich_daily223_input
from goal_analysis.jobs.agent_portfolio import (
    OwnOptionAdmissionRejected,
    build_agent_option_builder,
)
from goal_analysis.jobs.agent_snapshot import (
    FAMILIES,
    build_normal_snapshot,
    companion_snapshot,
    digest,
)
from goal_analysis.jobs.ten_agent_normal import NormalAgentExecutor
from goal_analysis.ticket_product_policy import product_contract, product_failure

NOW = datetime(2026, 11, 14, 10, tzinfo=UTC)
SETTINGS = {'operating_contract_version': 2, 'enabled_profiles': list(AGENT_IDS),
            'target_ticket_count': 15, 'max_ticket_count': 30, 'stake_eur': 1.0}


def _write(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(canonical(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())

def _fake_source_files(prepared, reads):
    """Actual kernel/fsynced TESTONLY files; native event objects are explicit fixtures."""
    events, records = [], []
    with prepared['journal_path'].open('x', encoding='utf-8', newline='\n') as journal:
        def append(record):
            records.append(deepcopy(record))
            journal.write(canonical(record) + '\n')
            journal.flush()
            os.fsync(journal.fileno())
            return sha(record)

        kernel = SourceAccessKernel(prepared['bundle'], prepared['document']['binding'], append, lambda: NOW)
        for read in reads:
            offset = 0
            while True:
                arguments = ({**prepared['document']['binding'], 'namespace': 'candidates',
                    'path': [read], 'mode': 'text', 'offset': offset, 'limit': 24_000}
                    if isinstance(read, str) else deepcopy(read))
                identifier = f'TESTONLY-native-{len(events)}'
                response = kernel.read_data(arguments, rpc_id=len(events) + 1)
                result = response['result']
                events.append({'type': 'item.completed', 'item': {'type': 'mcp_tool_call',
                    'id': identifier, 'server': 'arthur_data', 'tool': 'read_data',
                    'arguments': arguments, 'result': {'content': result['content'], 'structured_content': None},
                    'status': 'completed'}})
                payload = json.loads(result['content'][0]['text'])
                offset = payload.get('page', {}).get('next_offset')
                if not isinstance(read, str) or offset is None:
                    break
        receipt = {**kernel.receipt(), 'stdio_closed': True, 'stdio_outcome': 0,
            'protocol_error_type': None, 'forbidden_operations': [], 'stdio_protocol': '2025-06-18',
            'closed_at': NOW.isoformat()}
        _write(prepared['receipt_path'], receipt)
    return events, records

def finish_fake_source_reply(scope, request, reads, answer):
    """Support for other pure integration fixtures, never an actual CLI result.

    reads is a list of original candidate-ID strings (complete pages), or exact
    request dictionaries (one bounded query). Empty reads is valid for abstention
    or FINAL. The real host validator is always called; it is never mocked.
    """
    prepared = scope.prepare(request)
    events, _ = _fake_source_files(prepared, reads)
    proof = finish_source_evidence(prepared, events, request, answer)
    return SourceAccessReply.create(answer, proof)

def _inputs(*, run='TESTONLY-pair-composition', offset=0):
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
    return run, artifacts, enriched, fixtures



def _expand_final(data):
    data = deepcopy(data)
    current = data.get('final_data_projection')
    if current is not None and current['version'] == 'FINAL_EXACT_NAMED_COLUMNS_V3':
        columns, used = data.pop('exact_columns'), set()
        assert set(columns) == {f'k{index}' for index in range(len(columns))}
        assert all(isinstance(fields, list) and all(isinstance(key, str) for key in fields)
                   and fields == sorted(set(fields)) for fields in columns.values())

        def decode_rows(value):
            if isinstance(value, dict) and set(value) == {'$row'}:
                row = value['$row']
                assert isinstance(row, list) and len(row) == 2
                key, cells = row
                assert isinstance(key, str) and key in columns
                assert isinstance(cells, list) and len(cells) == len(columns[key])
                used.add(key)
                return dict(zip(columns[key], [decode_rows(cell) for cell in cells], strict=True))
            if isinstance(value, dict):
                return {key: decode_rows(item) for key, item in value.items()}
            if isinstance(value, list):
                return [decode_rows(item) for item in value]
            return value

        data.pop('final_data_projection')
        data = decode_rows(data)
        assert used == set(columns)
        if current['values_projection'] is not None:
            data['final_data_projection'] = current['values_projection']
        assert digest(data) == current['values_wire_sha256']
        restored = _expand_final(data)
        assert digest(restored) == current['source_data_sha256']
        return restored
    marker = data.pop('final_data_projection', None)
    if marker is None:
        return data
    values, active = data.pop('exact_values'), set()
    assert marker['version'] in {'FINAL_EXACT_SUBTREES_V1', 'FINAL_EXACT_VALUES_V2'}
    if marker['version'] == 'FINAL_EXACT_VALUES_V2':
        assert set(values) == {f'v{index}' for index in range(len(values))}

    def expand(value):
        if isinstance(value, dict) and set(value) == {'__arthur_final_ref__'}:
            key = value['__arthur_final_ref__']
            assert key not in active
            active.add(key)
            result = expand(values[key]['value'])
            active.remove(key)
            if marker['version'] == 'FINAL_EXACT_SUBTREES_V1':
                assert digest(result) == key == values[key]['source_value_sha256']
            else:
                assert digest(result) == values[key]['source_value_sha256']
            return result
        if isinstance(value, dict):
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    restored = expand(data)
    assert digest(restored) == marker['source_data_sha256']
    return restored


def _execute(tmp_path, *, expire_after_pair=False, choose_pair=True):
    run, artifacts, enriched, fixtures = _inputs()
    clock, calls, finals = [NOW], [], []
    original = digest((artifacts['candidate_input'], artifacts['history'], enriched))

    def host(request, *, timeout, source_access):
        calls.append(deepcopy(request))
        owner, phase = request['agent_id'], request['phase']
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        assert 0 < timeout <= (600 if phase == 'PLAN' else 300)
        data = request['data'] if phase == 'PLAN' else _expand_final(request['data'])
        is_pair = owner in COMPANION_AGENT_IDS
        if phase == 'PLAN':
            assert len(data['snapshot']['fixture_ids']) == 5
            assert set(data['snapshot']['families']) == set(FAMILIES)
            suggestions = data['own_strategy_search']['suggestions']
            if is_pair and choose_pair:
                assert len(suggestions) == 1
                seed = suggestions[0]
                options = [{'option_id': seed['search_option_id'],
                    'source_candidate_ids': seed['source_candidate_ids'],
                    'reason': 'TESTONLY own choice of complete original-method product.'}]
            else:
                options = []
            answer = {**identity, 'options': options,
                'fixture_reviews': [{'fixture_id': key, 'rejected': not options,
                    'reason': 'TESTONLY complete calendar considered.'}
                    for key in data['snapshot']['fixture_ids']],
                'tool_requests': [{'role': role, 'parameters': {'window': None, 'policy_hash': None}}
                                  for role in SIMULATOR_ROLES]}
            reads = [key for option in options for key in option['source_candidate_ids']]
        else:
            options = data['validated_own_options']
            assert len(options) == (1 if is_pair and choose_pair else 0)
            assert {row['request']['role'] for row in data['tool_results']} == set(SIMULATOR_ROLES)
            selected = [row['option_id'] for row in options]
            answer = {**identity, 'option_ids': selected,
                'reason': 'TESTONLY actual tools considered with explicit uncertainty.',
                'tool_interpretations': [{'request_id': row['request']['request_id'],
                    'interpretation': 'Actual pure result read; UNKNOWN does not mean neutral probability.'}
                    for row in data['tool_results']],
                'family_uses': [{'family': name,
                    'status': ('AVAILABLE_AND_CONSIDERED' if family['status'] == 'AVAILABLE' else
                                {'UNKNOWN': 'MISSING', 'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}[family['status']]),
                    'value_sha256': family['value_sha256'],
                    'option_ids': selected if family['status'] == 'AVAILABLE' else [],
                    'reason': 'TESTONLY exact source availability or local gap considered.',
                    'risk': 'Optional data and unproved historical profitability remain UNKNOWN.'}
                    for name, family in data['snapshot']['families'].items()]}
            reads = []
        reply = finish_fake_source_reply(source_access, request, reads, answer)
        if is_pair and phase == 'FINAL':
            finals.append(owner)
            if expire_after_pair and len(finals) == 2:
                clock[0] = NOW + timedelta(seconds=121)
        return reply

    executor = NormalAgentExecutor(host=host, journal_root=tmp_path / 'journals', clock=lambda: clock[0],
        owner_deadline_at=NOW + timedelta(seconds=120), enable_source_access=True,
        enable_companion_agents=True, enable_ticket_product_contract=True, timeout_seconds=6000)
    report = executor(run_id=run, target_date=NOW.date(), as_of=NOW, artifacts=artifacts,
        fixture_records=fixtures, enriched=enriched, club_enriched={}, candidate_context=None,
        weather_by_fixture={}, historical_runs=[], feedback={}, settings=SETTINGS, team_plan={})
    assert digest((artifacts['candidate_input'], artifacts['history'], enriched)) == original
    return report, artifacts, calls, tmp_path / 'journals' / run


def test_actual_pair_then_core_uses_own_plan_two_tools_final_and_canonical_cards(tmp_path):
    report, artifacts, calls, journal = _execute(tmp_path)
    assert len(calls) == 24 and report['astra']['usage']['host_requests_reserved'] == 24
    assert {row['agent_id'] for row in calls[:4]} == set(COMPANION_AGENT_IDS)
    assert {row['agent_id'] for row in calls[4:]} == set(AGENT_IDS)
    assert report['astra']['mode'] == 'TWELVE_INDEPENDENT_OWNER_DECISIONS'
    assert 'companion_ticket_channels' not in report
    assert len(report['ten_agent_decisions']['owners']) == 10
    pair = report['companion_agent_decisions']
    assert pair == artifacts['companion_agent_decisions']
    assert pair == json.loads((journal / 'companion-agents/companion-agent-decisions.json').read_text('utf-8'))
    assert all(row['status'] == 'DECISION_COMPLETED' for row in pair['owners'])
    assert all(len(row['decision_evidence']['tool_results']) == 2 for row in pair['owners'])
    assert all(len(row['decision']['selected_options']) == 1 for row in pair['owners'])
    cards = report['generated_tickets']
    assert len(cards) == len(validate_companion_agent_cards(report)) == 2
    assert {row['profile_id'] for row in cards} == set(COMPANION_AGENT_IDS)
    assert all(row['channel_id'] == row['profile_id'] and row['producer_profile_id'] == 'daily223'
        and row['generation_source_group'] == 'companion_agent_decisions'
        and 'companion_decision' not in row and product_failure(row) is None
        and row['combined_price'] == pytest.approx(12.3) and len(row['legs']) == 3
        and row['status'] == 'DRAFT' and row['playable'] is False for row in cards)
    assert report['generation_capacity']['filled_agent_owner_slots'] == 2
    assert report['generation_capacity']['filled_companion_slots'] == 0
    assert report['generation_capacity']['target_met'] is False
    assert artifacts['companion_agent_snapshot']['format'] == 'CORE_SNAPSHOT_IDENTITY_DERIVATION_V1'
    assert not (journal / 'companion-agents/shared-data.json').exists()
    assert (journal / 'companion-agents/shared-data-reference.json').is_file()
    before = deepcopy(report)
    append_companion_agent_cards(report)
    assert report == before
    changed = deepcopy(report)
    changed['generated_tickets'][0]['legs'][0]['decimal_price'] += .1
    with pytest.raises(ValueError, match='PAIR_CANONICAL_COLLECTION_CHANGED'):
        validate_companion_agent_cards(changed)


def test_fixed_pair_snapshot_reference_rejects_changed_data_and_identity():
    run, artifacts, enriched, _ = _inputs()
    core = build_normal_snapshot(run_id=run, target_date=NOW.date().isoformat(),
        snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched)
    pair = companion_snapshot(core)
    binding = shared_data_binding(core, pair)
    reference = snapshot_reference(pair, binding)
    assert core.snapshot_id != pair.snapshot_id
    assert restore_companion_snapshot(core, reference).payload_json == pair.payload_json
    assert pair.data()['families'] == core.data()['families']
    assert len(pair.fixture_ids) == len(core.fixture_ids) == 5
    bad = deepcopy(reference)
    bad['identity_overrides']['owner_ids'].reverse()
    with pytest.raises(ValueError, match='REFERENCE_CHANGED'):
        restore_companion_snapshot(core, bad)
    bad = deepcopy(reference)
    bad['shared_data_binding']['family_envelopes_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='REFERENCE_CHANGED'):
        restore_companion_snapshot(core, bad)


def test_original_method_admission_does_not_publish_a_different_selection():
    run, artifacts, enriched, _ = _inputs()
    core = build_normal_snapshot(run_id=run, target_date=NOW.date().isoformat(),
        snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched)
    pair = companion_snapshot(core)
    report = {'run_id': run, 'date': NOW.date().isoformat(), 'observed_at': NOW.isoformat(),
              'settings': SETTINGS, 'ticket_product_contract': product_contract()}
    builder = build_agent_option_builder(report, artifacts, enriched, {}, SETTINGS, NOW,
                                         decision_group='COMPANION_PAIR')
    for owner in COMPANION_AGENT_IDS:
        seed = builder.strategy_search(owner, pair)['alternatives'][0]
        proposal = {'option_id': 'TESTONLY-own-option', 'source_candidate_ids': seed['source_candidate_ids'],
                    'reason': 'TESTONLY explicit choice.'}
        option = builder(owner, proposal, pair)
        assert option['agent_id'] == option['canonical_card']['profile_id'] == owner
        assert {row['source_candidate_id'] for row in option['legs']} == set(seed['source_candidate_ids'])
        bad = {**proposal, 'source_candidate_ids': [row['candidate_id']
            for row in artifacts['candidate_input']['candidates'][1:]]}
        with pytest.raises(OwnOptionAdmissionRejected, match='OWN_STRATEGY_CONSTRUCTION_INVALID'):
            builder(owner, bad, pair)
    with pytest.raises(KeyError, match='UNKNOWN_ACTIVE_AGENT'):
        builder(AGENT_IDS[0], proposal, pair)


def test_common_deadline_preserves_pair_cards_and_skips_all_later_core_owners(tmp_path):
    report, artifacts, calls, journal = _execute(tmp_path, expire_after_pair=True)
    assert len(calls) == 4 and {row['agent_id'] for row in calls} == set(COMPANION_AGENT_IDS)
    assert len(report['generated_tickets']) == 2 and report['construction_status'] == 'PARTIAL'
    assert all(row['status'] == 'NOT_STARTED_DEADLINE' for row in report['ten_agent_decisions']['owners'])
    assert len(list(journal.glob('*.owner.json'))) == 10
    assert len(list((journal / 'companion-agents').glob('*.owner.json'))) == 2
    assert report['independent_owner_composition']['pair_deadline_resets_core_clock'] is False
    assert report['independent_owner_composition']['maximum_owner_requests'] == 24
    assert 'unattempted_agent_ids' not in report
    assert len(validate_companion_agent_cards(report)) == 2
    assert artifacts['companion_agent_decisions'] == report['companion_agent_decisions']


def test_pair_agents_may_abstain_without_automatic_protected_fallback(tmp_path):
    report, _, calls, _ = _execute(tmp_path, choose_pair=False)
    assert len(calls) == 24 and report['generated_tickets'] == []
    assert all(row['decision']['status'] == 'ABSTAIN' for row in report['companion_agent_decisions']['owners'])
    assert report['generation_capacity']['filled_agent_owner_slots'] == 0
    assert 'companion_ticket_channels' not in report
    assert validate_companion_agent_cards(report) == {}
