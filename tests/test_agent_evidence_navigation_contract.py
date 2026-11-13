"""Pure complete-catalogue/navigation fixtures; no host, network, DB or fit."""
from copy import deepcopy

from test_agent_construction_contract import NOW, _inputs

from goal_analysis.agents.profile_input import decode_table
from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.jobs.agent_portfolio import build_agent_option_builder
from goal_analysis.jobs.agent_snapshot import build_normal_snapshot


def test_every_owner_gets_every_original_candidate_in_order_without_shared_mutation():
    _, artifacts, _, _, _, _, builder = _inputs()
    first = builder.construction_contract(AGENT_IDS[0])['full_candidate_catalog']
    expected = [row['candidate_id'] for row in artifacts['candidate_input']['candidates']]
    for owner in AGENT_IDS:
        actual = builder.construction_contract(owner)['full_candidate_catalog']
        assert actual == first
        assert [row['candidate_id'] for row in decode_table(actual['table'])] == expected
        assert actual['source_row_count'] == 80 and not actual['candidate_filter_applied']
        assert actual['full_record_read_proven'] is False
        assert actual['full_calendar_replaced_by_priced_candidates'] is False
    first['table']['rows'].clear()
    assert builder.construction_contract(AGENT_IDS[-1])['full_candidate_catalog']['table']['row_count'] == 80


def test_duplicate_ids_and_missing_versus_null_clocks_are_not_hidden():
    report, artifacts, enriched, club, settings, _, _ = _inputs()
    original = artifacts['candidate_input']['candidates'][0]
    original.pop('quoted_at')
    duplicate = {**deepcopy(original), 'quoted_at': None, 'decimal_price': 9.0}
    artifacts['candidate_input']['candidates'].append(duplicate)
    builder = build_agent_option_builder(report, artifacts, enriched, club, settings, NOW)
    catalog = builder.construction_contract('kronikas')['full_candidate_catalog']
    decoded = decode_table(catalog['table'])
    assert len(decoded) == 81 and catalog['ambiguous_candidate_ids'] == {original['candidate_id']: 2}
    assert 'quoted_at' not in decoded[0] and decoded[-1]['quoted_at'] is None
    assert decoded[0]['decimal_price'] == 2.0 and decoded[-1]['decimal_price'] == 9.0
    assert decoded[-1]['source_row_index'] == 80


def test_navigation_exposes_actual_probability_address_and_preserves_club_policy():
    _, _, _, _, _, _, builder = _inputs()
    core = builder.construction_contract('ritmusor')
    club = builder.construction_contract('tipster_fek_tade')
    assert core['evidence_interpretation']['core_weighted_support'] == 'RANKING_AND_EXPLANATORY_METADATA_NOT_A_MINIMUM_ADMISSION_GATE'
    assert club['evidence_interpretation']['core_weighted_support'] == 'OWN_CLUB_POLICY_REMAINS_REQUIRED'
    assert core['source_navigation']['probability_by_id'] == {'namespace': 'raw', 'path': ['model_probabilities', '<model_probability_id>']}
    assert core['source_navigation']['probability_view']['identifier_column'] == 'probability_id'
    assert club['own_policy']['effective_policy']['selection_policy']['min_models'] > 0


def test_real_core_builder_keeps_missing_quote_clock_and_draft_with_low_descriptive_support():
    report, artifacts, enriched, club, settings, _, _ = _inputs()
    for producer in (artifacts['candidate_input'], enriched, club):
        for row in producer['candidates']:
            row['quoted_at'] = None
            row['quote_timestamp_status'] = 'MISSING'
            row['veto_override_context'] = {'minimum_weighted_support': 7.2}
            row['weighted_support_score'] = 0.0
    snapshot = build_normal_snapshot(run_id=report['run_id'], target_date=report['date'],
                                    snapshot_as_of=NOW.isoformat(), artifacts=artifacts,
                                    enriched=enriched, club_enriched=club)
    builder = build_agent_option_builder(report, artifacts, enriched, club, settings, NOW)
    selected = [row['candidate_id'] for row in artifacts['candidate_input']['candidates'][:4]]
    option = builder('kronikas', {'option_id': 'TESTONLY-missing-clock',
                     'source_candidate_ids': selected, 'reason': 'TESTONLY source verified four-leg draft.'}, snapshot)
    card = option['canonical_card']
    assert card['combined_price'] == 16.0 and card['status'] == 'DRAFT' and card['playable'] is False
    assert all(leg.get('quoted_at') is None for leg in card['legs'])
    assert card['agent_product_binding']['source_candidate_ids'] == selected
