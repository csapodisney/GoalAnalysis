"""Consumer boundaries with real pure product/state publication; no SQLite or paid host."""
from copy import deepcopy
from datetime import timedelta

import pytest
import test_twelve_pair_composition as pair_fixture
from test_agent_portfolio import NOW, SETTINGS, decisions, inputs
from test_companion_ticket_channels import _frame, _run

from goal_analysis.agents.profile_decision_runner import (
    DecisionBudget,
    DecisionJournal,
    ProfileDecisionRunner,
)
from goal_analysis.agents.profile_roster import AGENT_IDS, COMPANION_AGENT_IDS
from goal_analysis.agents.profile_source_host import SourceAccessScope
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.companion_agents import (
    JOURNAL_SUBDIRECTORY,
    append_companion_agent_cards,
    build_companion_option_builder,
    pair_snapshot_metadata,
    shared_data_binding,
    validate_companion_agent_cards,
)
from goal_analysis.companion_tickets import validate_companion_cards
from goal_analysis.jobs.agent_portfolio import build_agent_option_builder, publish_agent_decisions
from goal_analysis.jobs.agent_snapshot import build_normal_snapshot, companion_snapshot, digest
from goal_analysis.jobs.ten_agent_normal import NormalAgentExecutor
from goal_analysis.storage.portfolio_ledger import _agent_variant_keys
from goal_analysis.ticket_product_policy import product_contract
from goal_analysis.workshop_views import (
    INDEPENDENT_SLOT_VERSION,
    MAIN_SLOT_VERSION,
    main_slot_capacity,
    success_identity_coverage,
    workshop,
)


def _joint_fixture(tmp_path, monkeypatch):
    """Fresh same-snapshot fixture; no copied completed decisions from another run."""
    report, artifacts, enriched, club, _, _, proposals = inputs(variants=3)
    monkeypatch.setattr(pair_fixture, 'NOW', NOW)
    _, pair_artifacts, pair_enriched, pair_fixtures = pair_fixture._inputs()
    # Current Tade maximum remains 14; construct honest original quotes inside it.
    tade_ids = {key for row in proposals['tipster_fek_tade'] for key in row['source_candidate_ids']}
    for rows in (artifacts['candidate_input']['candidates'], enriched['candidates'], club['candidates']):
        for row in rows:
            if row['candidate_id'] in tade_ids:
                row['decimal_price'] = 1.9
    artifacts['candidate_input'] = {**deepcopy(pair_artifacts['candidate_input']),
        'candidates': artifacts['candidate_input']['candidates'] + deepcopy(pair_artifacts['candidate_input']['candidates'])}
    artifacts['history'] = deepcopy(pair_artifacts['history'])
    fixtures = artifacts['fixtures']['records'] + deepcopy(pair_fixtures)
    receipt = {**deepcopy(pair_artifacts['calendar']['scope_receipt']),
               'provider_fixture_ids': [row['fixture_id'] for row in fixtures], 'events': deepcopy(fixtures)}
    artifacts['fixtures'] = {'records': fixtures}
    artifacts['calendar'] = {'scope_receipt': receipt}
    enriched['candidates'] += deepcopy(pair_enriched['candidates'])
    enriched['history_analysis'] = deepcopy(pair_enriched['history_analysis'])
    enriched.update(calendar_universe=deepcopy(fixtures), calendar_scope_receipt=deepcopy(receipt))
    club['fixtures'] = deepcopy(fixtures)
    report.update(ticket_product_contract=product_contract(), finished_at=NOW.isoformat(), team={},
                  full_day_calendar=deepcopy(receipt))
    source = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
        snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched, club_enriched=club)
    core_builder = build_agent_option_builder(report, artifacts, enriched, club, SETTINGS, NOW)
    options = {owner: [core_builder(owner, proposal, source) for proposal in proposals[owner]] for owner in AGENT_IDS}
    publish_agent_decisions(report, decisions(source, options), SETTINGS, NOW)
    assert len(report['generated_tickets']) == 30
    historical_core = deepcopy(report)
    pair = companion_snapshot(source)
    builder = build_companion_option_builder(report, artifacts, enriched, club, SETTINGS, NOW)
    selected = [row['candidate_id'] for row in pair_artifacts['candidate_input']['candidates'][:3]]
    journal = DecisionJournal(tmp_path / 'pair', pair)
    bundle, source_scope_args = NormalAgentExecutor._stage_source_access(journal, pair)

    def host(request, *, timeout):
        assert 0 < timeout <= 120
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        source_access = SourceAccessScope(**source_scope_args, binding=identity)
        owner = request['agent_id']
        data = request['data'] if request['phase'] == 'PLAN' else pair_fixture._expand_final(request['data'])
        if request['phase'] == 'PLAN':
            answer = {**identity, 'options': [{'option_id': owner + '-TESTONLY-consumer',
                    'source_candidate_ids': selected, 'reason': 'TESTONLY own original-method choice'}],
                'fixture_reviews': [{'fixture_id': key, 'reason': 'TESTONLY full shared day', 'rejected': False}
                                    for key in pair.fixture_ids],
                'tool_requests': [{'role': role, 'parameters': {}}
                                  for role in ('candidate_scenarios', 'historical_strategy_evaluation')]}
            return pair_fixture.finish_fake_source_reply(source_access, request, selected, answer)
        own = data['validated_own_options']
        assert len(own) == 1
        answer = {**identity, 'option_ids': [own[0]['option_id']], 'reason': 'TESTONLY actual own tools considered',
            'tool_interpretations': [{'request_id': row['request']['request_id'],
                                     'interpretation': 'TESTONLY exact result, UNKNOWN remains UNKNOWN'}
                                    for row in data['tool_results']],
            'family_uses': [{'family': name, 'value_sha256': row['value_sha256'],
                'status': {'AVAILABLE': 'AVAILABLE_AND_CONSIDERED', 'UNKNOWN': 'MISSING',
                           'STALE': 'STALE', 'CONFLICTING': 'CONFLICTING'}[row['status']],
                'option_ids': [], 'reason': 'TESTONLY immutable source state', 'risk': 'TESTONLY no profit claim'}
                for name, row in data['snapshot']['families'].items()]}
        return pair_fixture.finish_fake_source_reply(source_access, request, [], answer)

    result = ProfileDecisionRunner(host=host, tools=ProfileToolService(pair, artifacts=artifacts),
        option_builder=builder, journal=journal, source_bundle=bundle,
        budget=DecisionBudget(4, NOW + timedelta(seconds=120), clock=lambda: NOW,
                              decision_group='COMPANION_PAIR'), decision_group='COMPANION_PAIR').run(pair)
    assert result['status'] == 'COMPLETE' and result['host_requests_reserved'] == 4
    binding = shared_data_binding(source, pair)
    result.update(runner_result_sha256=digest(result), shared_data_binding=binding,
                  journal_subdirectory=JOURNAL_SUBDIRECTORY)
    report['companion_agent_decisions'] = result
    report['companion_agent_snapshot_metadata'] = pair_snapshot_metadata(binding)
    append_companion_agent_cards(report)
    report['report_sha256'] = digest(report)
    return report, source.data(), historical_core


@pytest.fixture
def published(tmp_path, monkeypatch):
    return _joint_fixture(tmp_path, monkeypatch)


def test_real_pair_publication_joins_thirty_core_variants_as_twelve_owners(published):
    report, snapshot, _ = published
    before = deepcopy(report)
    pair = validate_companion_agent_cards(report)
    keys = _agent_variant_keys(report)
    assert len(pair) == 2 and len(keys) == len(set(keys.values())) == 32
    assert set(keys) == {row['ticket_id'] for row in report['generated_tickets']}
    assert {row['profile_id'] for row in report['generated_tickets']} == set(AGENT_IDS + COMPANION_AGENT_IDS)
    assert all(row['profile_id'] == row['channel_id'] != row['producer_profile_id']
               for row in report['generated_tickets'] if 'channel_id' in row)
    capacity = main_slot_capacity(report, snapshot)
    assert capacity['version'] == INDEPENDENT_SLOT_VERSION
    assert capacity['filled_agent_owner_slots'] == capacity['filled_main_slots'] == 12
    assert capacity['filled_core_owner_slots'] == 10 and capacity['filled_pair_owner_slots'] == 2
    assert capacity['filled_companion_slots'] == 0 and capacity['maximum_agent_variants'] == 32
    assert capacity['generated_ticket_count'] == 32 and capacity['target_met'] is True
    job = {'run_id': report['run_id'], 'target_date': report['date'], 'capacity': capacity,
           'generated_ticket_count': 32}
    assert success_identity_coverage(job) is True
    view = workshop(day=report['date'], report=report, states=[], snapshot=snapshot,
        operation=None, schedule={'enabled': False}, capabilities={'manual_generation': False}, now=NOW)
    assert [row['agent_id'] for row in view['agents']] == list(AGENT_IDS + COMPANION_AGENT_IDS)
    assert len(view['cards']) == 32 and view['companion_channels'] == []
    assert all(view['capacity'][key] == value for key, value in capacity.items() if key != 'companion_channels')
    assert view['capacity']['completed_owner_count'] == 12
    assert report == before


def test_many_variants_cannot_replace_one_missing_owner_or_exceed_pair_cap(published):
    report, snapshot, _ = published
    changed = deepcopy(report)
    changed['generated_tickets'] = [row for row in changed['generated_tickets'] if row['profile_id'] != AGENT_IDS[-1]]
    capacity = main_slot_capacity(changed, snapshot)
    assert len(_agent_variant_keys(changed)) == 29
    assert capacity['generated_ticket_count'] == 29 and capacity['filled_main_slots'] == 11
    assert capacity['missing_agent_ids'] == [AGENT_IDS[-1]] and capacity['target_met'] is False
    assert success_identity_coverage({'run_id': report['run_id'], 'target_date': report['date'],
        'generated_ticket_count': 29, 'capacity': capacity}) is False
    overflow = deepcopy(report)
    duplicate = deepcopy(overflow['generated_tickets'][-1])
    duplicate['ticket_id'] += '-TESTONLY-over-cap'
    overflow['generated_tickets'].append(duplicate)
    with pytest.raises(ValueError, match='PAIR_CANONICAL_COLLECTION_CHANGED'):
        _agent_variant_keys(overflow)
    assert main_slot_capacity(overflow, snapshot)['target_met'] is False


def test_historical_ten_and_real_protected_channels_keep_original_meaning(published, tmp_path):
    _, snapshot, historical = published
    before = deepcopy(historical)
    assert len(_agent_variant_keys(historical)) == 30
    capacity = main_slot_capacity(historical, snapshot)
    assert capacity['version'] == MAIN_SLOT_VERSION and capacity['filled_agent_owner_slots'] == 10
    assert capacity['state'] == 'HISTORICAL_TEN_OWNER_REPORT' and capacity['target_met'] is None
    frame = _frame(tmp_path / 'protected-old')
    legacy = _run(frame)
    assert len(validate_companion_cards(legacy)) == len(_agent_variant_keys(legacy)) == 2
    old = main_slot_capacity(legacy, frame[3].data())
    assert old['version'] == MAIN_SLOT_VERSION and old['filled_agent_owner_slots'] == 0
    assert old['filled_companion_slots'] == 2 and old['target_met'] is False
    assert all(card['profile_id'] == card['producer_profile_id'] == 'daily223'
               and 'agent_decision' not in card for card in legacy['generated_tickets'])
    assert historical == before


def test_foreign_pair_identity_and_pasted_old_channel_are_rejected(published, tmp_path):
    report, snapshot, _ = published
    wrong = deepcopy(report)
    wrong['generated_tickets'][-1]['profile_id'] = AGENT_IDS[0]
    with pytest.raises(ValueError, match='PAIR_CANONICAL_COLLECTION_CHANGED'):
        _agent_variant_keys(wrong)
    assert main_slot_capacity(wrong, snapshot)['target_met'] is False
    foreign = deepcopy(report)
    foreign['run_id'] += '-FOREIGN'
    with pytest.raises(ValueError, match='PAIR_SHARED_DATA_METADATA_CHANGED'):
        _agent_variant_keys(foreign)
    old = _run(_frame(tmp_path / 'old-paste'))
    pasted = deepcopy(report)
    pasted['generated_tickets'][-1] = deepcopy(old['generated_tickets'][-1])
    with pytest.raises(ValueError, match='PAIR_CANONICAL_COLLECTION_CHANGED'):
        _agent_variant_keys(pasted)
    mixed = deepcopy(report)
    mixed['companion_ticket_channels'] = deepcopy(old['companion_ticket_channels'])
    with pytest.raises(ValueError, match='MIXED_COMPANION_GENERATIONS_FORBIDDEN'):
        _agent_variant_keys(mixed)
