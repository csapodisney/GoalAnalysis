"""Pure own existing-search controls; no provider, host, DB or model fit."""
from copy import deepcopy

import pytest
from test_agent_construction_contract import NOW, _inputs
from test_agent_portfolio import (
    NOW as CLUB_NOW,
)
from test_agent_portfolio import (
    SETTINGS as CLUB_SETTINGS,
)
from test_agent_portfolio import (
    inputs as club_inputs,
)

from goal_analysis.agents.profile_decision import finalize_decisions, set_options, start_sessions
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.engine import portfolio as core
from goal_analysis.jobs.agent_portfolio import build_agent_option_builder
from goal_analysis.jobs.agent_snapshot import build_normal_snapshot, digest
from goal_analysis.tipsters import recommendations as club


def _source(snapshot, enriched):
    data = snapshot.data()
    return {**deepcopy(enriched), 'fixtures': data['families']['calendar']['value'],
            'calendar_universe': data['families']['calendar']['value'],
            'calendar_scope_receipt': data['calendar_receipt']['producer_receipt']}


def test_all_ten_share_full_inventory_but_get_only_own_search_results():
    report, artifacts, enriched, club_source, settings, snapshot, builder = _inputs()
    before = digest((report, artifacts, enriched, club_source, settings, snapshot.data()))
    for owner in AGENT_IDS:
        result = builder.strategy_search(owner, snapshot)
        assert result['agent_id'] == owner and result['snapshot_id'] == snapshot.snapshot_id
        assert result['full_fixture_ids'] == list(snapshot.fixture_ids)
        assert result['source_candidate_rows'] == 80 and len(result['all_family_states']) == 12
        assert result['source_candidate_input_sha256'] == digest(artifacts['candidate_input'])
        assert result['status'] in {'AVAILABLE', 'NO_COMPLIANT_OPTION'}
        assert result['common_shortlist_applied'] is False
        assert result['automatic_selection_or_publication'] is False
        assert result['actual_host_tool_invocation'] is False
    assert digest((report, artifacts, enriched, club_source, settings, snapshot.data())) == before
    assert report['tickets'] == report['recommendations'] == []


def test_core_search_matches_original_own_alternatives_and_preserves_deferred_rows():
    _, _, enriched, _, settings, snapshot, builder = _inputs()
    expected = {}
    core.build_portfolio(_source(snapshot, enriched), settings,
                         allowed_profiles=['kronikas'], diversity_options=expected)
    result = builder.strategy_search('kronikas', snapshot)
    assert result['status'] == 'AVAILABLE' and result['alternatives']
    assert [row['source_candidate_ids'] for row in result['alternatives']] == [
        [leg['candidate_id'] for leg in item['ticket']['legs']] for item in expected['kronikas']]
    assert [row['scoring'] for row in result['alternatives']] == [row['scoring'] for row in expected['kronikas']]
    assert all(3 <= len(row['source_candidate_ids']) <= 6 and 15 <= row['combined_price'] <= 40
               for row in result['alternatives'])
    diagnostics = result['diagnostics']
    assert len(diagnostics['searched_candidate_ids']) == 64
    assert len(diagnostics['deferred_candidate_ids']) == 16
    assert len(diagnostics['fixture_decisions']) == 80
    first = result['alternatives'][0]
    option = builder('kronikas', {'option_id': first['search_option_id'],
        'source_candidate_ids': first['source_candidate_ids'], 'reason': 'TESTONLY own acceptance.'}, snapshot)
    assert option['canonical_card']['status'] == 'DRAFT'
    assert option['canonical_card']['combined_price'] == first['combined_price']


def test_club_search_uses_actual_original_persona_alternatives_and_models():
    _, _, _, club_source, snapshot, builder, _ = club_inputs(owners=('tipster_fek_tade',))
    result = builder.strategy_search('tipster_fek_tade', snapshot)
    from goal_analysis.agents.profile_tools import ProfileToolService

    models = ProfileToolService(snapshot)._admitted_models(
        snapshot.data()['families']['numeric_models']['value']['results'])
    expected = {}
    club.build_tipster_recommendations(_source(snapshot, club_source), models,
        {**CLUB_SETTINGS, 'enabled_profiles': ['tipster_fek_tade']}, CLUB_NOW,
        diversity_options=expected, support_guard_mode='LIVE', option_search_mode='OFF')
    assert result['status'] == 'AVAILABLE' and result['alternatives']
    assert [row['source_candidate_ids'] for row in result['alternatives']] == [
        item['recommendation']['selection_diagnostics']['selected_source_candidate_ids']
        for item in expected['tipster_fek_tade']]
    assert [row['scoring'] for row in result['alternatives']] == [row['scoring'] for row in expected['tipster_fek_tade']]
    assert result['shadow_challenger_executed'] is False
    first = result['alternatives'][0]
    option = builder('tipster_fek_tade', {'option_id': first['search_option_id'],
        'source_candidate_ids': first['source_candidate_ids'], 'reason': 'TESTONLY own consensus choice.'}, snapshot)
    assert option['canonical_card']['combined_price'] == first['combined_price']
    assert option['canonical_card']['profile_id'] == 'tipster_fek_tade'


def test_result_is_detached_cached_and_foreign_snapshot_or_owner_is_rejected():
    report, artifacts, enriched, club_source, _, snapshot, builder = _inputs()
    original = builder.strategy_search('kronikas', snapshot)
    changed = builder.strategy_search('kronikas', snapshot)
    changed['alternatives'].clear()
    changed['all_family_states'].clear()
    assert builder.strategy_search('kronikas', snapshot) == original
    with pytest.raises(KeyError, match="^'UNKNOWN_ACTIVE_AGENT:FOREIGN-owner'$"):
        builder.strategy_search('FOREIGN-owner', snapshot)
    foreign = build_normal_snapshot(run_id=report['run_id'] + '-foreign', target_date=report['date'],
        snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched, club_enriched=club_source)
    with pytest.raises(ValueError, match='PRODUCT_BUILDER_SNAPSHOT_CHANGED'):
        builder.strategy_search('kronikas', foreign)


def test_required_metadata_rejection_is_local_and_never_invented():
    report, artifacts, enriched, club_source, settings, _, _ = _inputs()
    identifier = artifacts['candidate_input']['candidates'][0]['candidate_id']
    for producer in (artifacts['candidate_input'], enriched, club_source):
        producer['candidates'][0].pop('competition_type')
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
        snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched, club_enriched=club_source)
    builder = build_agent_option_builder(report, artifacts, enriched, club_source, settings, NOW)
    result = builder.strategy_search('kronikas', snapshot)
    assert result['status'] == 'AVAILABLE' and result['source_candidate_rows'] == 80
    assert 'competition_type' in result['diagnostics']['candidate_rejections'][identifier]
    assert all(identifier not in row['source_candidate_ids'] for row in result['alternatives'])
    assert 'competition_type' not in artifacts['candidate_input']['candidates'][0]


def test_search_does_not_replace_required_own_simulation_receipts():
    _, _, _, _, _, snapshot, builder = _inputs()
    first = builder.strategy_search('kronikas', snapshot)['alternatives'][0]
    option = builder('kronikas', {'option_id': first['search_option_id'],
        'source_candidate_ids': first['source_candidate_ids'], 'reason': 'TESTONLY own option.'}, snapshot)
    state = next(row for row in start_sessions(snapshot,
        {owner: get_agent_definition(owner).method_version for owner in AGENT_IDS})
        if row.agent_id == 'kronikas')
    state = set_options(state, snapshot, [option])
    with pytest.raises(ValueError, match='MISSING_SIMULATION_RECEIPT'):
        finalize_decisions(state, snapshot, option_ids=[option['option_id']], reason='TESTONLY no simulation bypass.')


def test_malformed_optional_history_remains_local_unknown_with_valid_core_routes():
    for historical in (['TESTONLY-invalid-optional-shape'], {'candidates': None}):
        report, artifacts, enriched, club_source, settings, _, _ = _inputs()
        enriched['history_analysis'] = historical
        snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
            snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched, club_enriched=club_source)
        builder = build_agent_option_builder(report, artifacts, enriched, club_source, settings, NOW)
        result = builder.strategy_search('kronikas', snapshot)
        assert result['status'] == 'AVAILABLE' and result['alternatives']
        assert enriched['history_analysis'] == historical
