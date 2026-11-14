"""TESTONLY new policy over existing pure constructors; no paid/source acquisition."""
import math
from copy import deepcopy

import pytest
from test_agent_portfolio import NOW, SETTINGS, decisions, inputs
from test_companion_ticket_channels import NOW as COMPANION_NOW
from test_companion_ticket_channels import _frame

from goal_analysis.agents.profile_decision import set_options, start_sessions
from goal_analysis.agents.profile_input import final_option_details
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.companion_tickets import run_companion_channels, validate_companion_cards
from goal_analysis.generated_tickets import _invalid, _materialize
from goal_analysis.jobs.agent_portfolio import (
    OwnOptionAdmissionRejected,
    build_agent_option_builder,
    publish_agent_decisions,
)
from goal_analysis.jobs.agent_snapshot import build_normal_snapshot, digest
from goal_analysis.jobs.ten_agent_normal import make_normal_agent_executor
from goal_analysis.operating_policy import effective_persona
from goal_analysis.ticket_product_policy import (
    feasible_search_ceiling,
    preference_key,
    product_contract,
    product_failure,
    report_contract,
    validate_option_product,
)
from goal_analysis.tipsters.catalog import TIPSTER_BY_ID
from goal_analysis.tipsters.contracts import valid_construction


def _new_frame(owner='merlin', prices=(2.0, 2.0, 2.0, 2.0), *, current=True):
    report, artifacts, enriched, club, _, _, proposals = inputs(owners=(owner,), variants=1)
    assert len(prices) == 4
    for container in (artifacts['candidate_input'], enriched, club):
        for row, price in zip(container['candidates'], prices, strict=True):
            row['decimal_price'] = price
    if current:
        report['ticket_product_contract'] = product_contract()
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
        snapshot_as_of=NOW.isoformat(), artifacts=artifacts, enriched=enriched, club_enriched=club)
    builder = build_agent_option_builder(report, artifacts, enriched, club, SETTINGS, NOW)
    return report, artifacts, enriched, club, snapshot, builder, proposals[owner][0]


def test_exact_new_marker_floor_ten_six_distinct_and_invalid_prices():
    card = {'profile_id': 'merlin', 'legs': [
        {'fixture_id': f'TESTONLY:{i}', 'decimal_price': price}
        for i, price in enumerate((2.0, 2.0, 2.5))], 'combined_price': 10.0}
    assert product_failure(card) is None
    card['legs'][-1]['decimal_price'] = 2.49
    card['combined_price'] = 9.96
    assert product_failure(card) == 'OUTSIDE_OWN_APPROVED_ODDS_RANGE'
    card['legs'] = [{'fixture_id': f'TESTONLY:{i}', 'decimal_price': 2.0} for i in range(6)]
    card['combined_price'] = 64.0
    assert product_failure(card) is None
    card['legs'].append({'fixture_id': 'TESTONLY:6', 'decimal_price': 2.0})
    card['combined_price'] = 128.0
    assert product_failure(card) == 'TICKET_PRODUCT_LEG_COUNT'
    card['legs'] = card['legs'][:6]
    card['legs'][-1]['fixture_id'] = card['legs'][0]['fixture_id']
    assert product_failure(card) == 'DUPLICATE_OR_INVALID_FIXTURE'
    card['legs'][-1]['fixture_id'] = 'TESTONLY:5'
    card['legs'][0]['decimal_price'] = float('nan')
    assert product_failure(card) == 'INVALID_PRICE_OR_QUOTE_SOURCE'
    with pytest.raises(ValueError, match='NULL_TICKET_PRODUCT_CONTRACT'):
        report_contract({'ticket_product_contract': None})
    invalid = product_contract()
    invalid['maximum_combined_price'] = 20
    with pytest.raises(ValueError, match='UNKNOWN_OR_CHANGED'):
        report_contract({'ticket_product_contract': invalid})


def test_actual_core_ten_is_ordinary_new_policy_and_old_fallback_is_preserved():
    report, _, _, _, snapshot, builder, proposal = _new_frame(prices=(2., 2., 2., 1.25))
    before = deepcopy(SETTINGS)
    option = builder('merlin', proposal, snapshot)
    card = option['canonical_card']
    assert card['combined_price'] == 10.0 and card['fallback'] is False
    assert _invalid(card, report, NOW) is None
    assert _materialize(card, 'ten_agent_decisions', report, {})['preferred_price_status'] == 'OUTSIDE_PREFERRED_RANGE_NOT_A_REJECTION'
    assert SETTINGS == before and card['status'] == 'DRAFT' and card['playable'] is False
    _, _, _, _, old_snapshot, old_builder, old_proposal = _new_frame(prices=(2., 2., 2., 1.25), current=False)
    with pytest.raises(OwnOptionAdmissionRejected, match='CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN'):
        old_builder('merlin', old_proposal, old_snapshot)


def test_actual_core_above_forty_and_own_search_keep_full_sources_without_global_ceiling():
    report, artifacts, _, _, snapshot, builder, proposal = _new_frame(prices=(41., 1.1, 1.1, 1.1))
    original = deepcopy(artifacts)
    option = builder('merlin', proposal, snapshot)
    assert option['canonical_card']['combined_price'] > 40
    assert _invalid(option['canonical_card'], report, NOW) is None
    contract = builder.construction_contract('merlin')
    assert contract['primary_combined_price'] == {'min': 10.0, 'max': None}
    assert contract['ticket_product_contract'] == product_contract()
    assert contract['full_candidate_catalog']['source_row_count'] == 4
    result = builder.strategy_search('merlin', snapshot)
    assert result['status'] == 'AVAILABLE'
    assert result['full_fixture_ids'] == list(snapshot.fixture_ids)
    assert any(row['combined_price'] > 40 for row in result['alternatives'])
    assert artifacts == original
    assert preference_key(18.) < preference_key(12.) and preference_key(18.) < preference_key(60.)


def test_original_specialist_fifty_ceiling_remains_local_not_global():
    _, _, _, _, snapshot, builder, proposal = _new_frame('kronikas', (41., 1.1, 1.1, 1.1))
    assert builder.construction_contract('kronikas')['primary_combined_price'] == {'min': 10.0, 'max': 50.0}
    with pytest.raises(OwnOptionAdmissionRejected, match='OUTSIDE_OWN_APPROVED_ODDS_RANGE'):
        builder('kronikas', proposal, snapshot)


def test_original_club_risk_and_model_rules_survive_with_twelve_outside_preference():
    owner = 'tipster_fek_tade'
    canonical = deepcopy(TIPSTER_BY_ID[owner])
    report, _, _, _, snapshot, builder, proposal = _new_frame(owner, (2., 2., 1.75, 1.75))
    option = builder(owner, proposal, snapshot)
    card = option['canonical_card']
    assert card['combined_price'] == 12.25 and valid_construction(card, now=NOW)
    assert _invalid(card, report, NOW) is None
    assert card['persona']['target_odds_band'] == {'min': 10., 'max': 14}
    assert card['persona']['model_weights'] == canonical['model_weights']
    assert card['persona']['selection_policy'] == canonical['selection_policy']
    assert TIPSTER_BY_ID[owner] == canonical
    _, _, _, _, over_snapshot, over_builder, over_proposal = _new_frame(owner)
    with pytest.raises(OwnOptionAdmissionRejected, match='OUTSIDE_OWN_APPROVED_ODDS_RANGE'):
        over_builder(owner, over_proposal, over_snapshot)


def test_state_tools_and_final_share_new_option_guard_without_changing_source_values():
    report, _, _, _, snapshot, builder, proposal = _new_frame()
    option = builder('merlin', proposal, snapshot)
    state = next(item for item in start_sessions(snapshot,
        {owner: get_agent_definition(owner).method_version for owner in AGENT_IDS}) if item.agent_id == 'merlin')
    set_options(state, snapshot, [option])
    details = final_option_details(snapshot, 'merlin', [option], source_access=True)
    assert set(details['candidate_ids']) == set(proposal['source_candidate_ids'])
    assert option['canonical_card']['ticket_product_contract'] == report['ticket_product_contract']
    broken = deepcopy(option)
    broken['legs'][0]['decimal_price'] = 100
    with pytest.raises(ValueError, match='NEW_PRODUCT_OPTION_LEGS_OR_OWNER_CHANGED'):
        validate_option_product(broken)
    with pytest.raises(ValueError, match='NEW_PRODUCT_OPTION_LEGS_OR_OWNER_CHANGED'):
        set_options(state, snapshot, [broken])
    with pytest.raises(ValueError, match='NEW_PRODUCT_OPTION_LEGS_OR_OWNER_CHANGED'):
        final_option_details(snapshot, 'merlin', [broken], source_access=True)
    tampered = {**deepcopy(report), 'ticket_product_contract': None}
    assert _invalid(option['canonical_card'], tampered, NOW) == 'INVALID_TICKET_PRODUCT_CONTRACT'


def test_saved_canonical_publisher_retains_exact_new_marker_and_never_promotes_ready():
    report, _, _, _, snapshot, builder, proposal = _new_frame(prices=(2., 2., 2., 1.25))
    option = builder('merlin', proposal, snapshot)
    publish_agent_decisions(report, decisions(snapshot, {'merlin': [option]}), SETTINGS, NOW)
    assert len(report['generated_tickets']) == 1
    card = report['generated_tickets'][0]
    assert card['ticket_product_contract'] == product_contract()
    assert card['combined_price'] == 10 and card['preferred_price_status'].startswith('OUTSIDE_PREFERRED')
    assert card['status'] == 'DRAFT' and card['stake_eur'] is None and not card['playable']
    assert card['agent_decision']['canonical_source_card_sha256'] == digest(option['canonical_card'])
    old_report = {key: value for key, value in report.items() if key != 'ticket_product_contract'}
    with pytest.raises(ValueError, match='TICKET_PRODUCT_CONTRACT_BINDING_MISMATCH'):
        _materialize(option['canonical_card'], 'ten_agent_decisions', old_report, {})


def test_actual_protected_companions_keep_their_original_twelve_product_and_new_label(tmp_path):
    report, artifacts, enriched, snapshot, journal = _frame(tmp_path)
    report['ticket_product_contract'] = product_contract()
    before = deepcopy((artifacts['candidate_input'], enriched, report['settings']))
    run_companion_channels(report, snapshot=snapshot, artifacts=artifacts, enriched=enriched,
                           settings=report['settings'], journal=journal)
    assert len(validate_companion_cards(report)) == 2
    assert {card['channel_id'] for card in report['generated_tickets']} == {'daily_portfolio', 'legacy_daily223'}
    for card in report['generated_tickets']:
        assert len(card['legs']) == 3 and 10 <= card['combined_price'] < 15
        assert card['preferred_price_status'] == 'OUTSIDE_PREFERRED_RANGE_NOT_A_REJECTION'
        assert _invalid(card, report, COMPANION_NOW) is None
    assert (artifacts['candidate_input'], enriched, report['settings']) == before
    assert report['generation_capacity']['filled_main_slots'] == 2
    assert report['generation_capacity']['target_met'] is False


def test_input_derived_search_ceiling_uses_all_distinct_fixtures_not_first_rows():
    rows = [{'fixture_id': f'F{i}', 'decimal_price': float(i + 2)} for i in range(8)]
    rows.append({'fixture_id': 'F0', 'decimal_price': 41.})
    bound = feasible_search_ceiling(rows)
    exact = 41 * 9 * 8 * 7 * 6 * 5
    assert exact <= bound <= math.nextafter(float(exact), math.inf)
    assert bound == feasible_search_ceiling(list(reversed(rows)))
    assert bound > 40


def test_production_factory_selects_new_contract_without_mutating_old_settings_or_catalog(tmp_path):
    settings = {**deepcopy(SETTINGS), 'openai': {'model': 'TESTONLY-model',
        'reasoning_effort': 'medium', 'web_search': False, 'max_output_tokens': 8000}}
    before = deepcopy(settings)
    executor = make_normal_agent_executor(tmp_path, settings, clock=lambda: NOW)
    assert executor.enable_ticket_product_contract is True
    assert executor.enable_companion_channels is True and executor.enable_source_access is True
    assert settings == before and not (tmp_path / 'data').exists()
    legacy = effective_persona(TIPSTER_BY_ID['tipster_fek_tade'], SETTINGS)
    assert legacy['target_odds_band'] == {'min': 15., 'max': 40.}
    assert 'ticket_product_contract' not in legacy
