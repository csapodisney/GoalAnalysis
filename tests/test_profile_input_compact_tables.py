from copy import deepcopy

import pytest

from goal_analysis.agents.profile_input import (
    decision_view,
    decode_candidate_features,
    decode_candidates,
    decode_table,
    encode_table,
    final_option_details,
    final_shared_reference,
    public_tool_results,
)
from goal_analysis.jobs.agent_snapshot import OWNER_IDS, build_snapshot, digest, json_text


def _sample(hits):
    return {'sample_size': 4, 'market_hits': hits, 'descriptive_hit_rate': hits / 4,
            'goals_for_mean': 1.25, 'goals_against_mean': 0.75,
            'results_newest_first': ['W', 'D', 'L', 'W'],
            'fixture_ids': ['h1', 'h2', 'h3', 'h4'],
            'source_ids': ['https://fixture.invalid/history'] * 4,
            'missing_period_scores': []}


def _snapshot(fixture_count=2, candidate_count=4, *, duplicate=False):
    fixtures = [{'fixture_id': f'fixture-{index}', 'home_team': f'Home {index}',
                 'away_team': f'Away {index}', 'kickoff': '2026-11-12T21:00:00+00:00',
                 'competition': 'Fixture competition', 'venue_name': None, 'neutral_venue': False,
                 'fixture_source': {'source_url': 'https://fixture.invalid/calendar',
                                    'observed_at': '2026-11-12T10:00:00+00:00'}}
                for index in range(fixture_count)]
    candidates = [{'candidate_id': f'candidate-{index}',
                   **deepcopy(fixtures[index % fixture_count]),
                   'market_key': 'totals_2_5', 'selection_key': 'over' if index % 2 == 0 else 'under',
                   'period': 'FULL_TIME', 'settlement': 'REGULATION_90_PLUS_STOPPAGE',
                   'bookmaker': 'fixture-bookmaker', 'currency': 'EUR', 'region': 'DE',
                   'decimal_price': 1.875, 'quoted_at': None, 'quote_observed_at': '2026-11-12T10:00:00+00:00',
                   'quote_source_id': 'https://fixture.invalid/odds', 'quote_time_basis': 'RECEIPT_NOT_QUOTE_CLOCK',
                   'evidence': []} for index in range(candidate_count)]
    if duplicate:
        candidates.append({**deepcopy(candidates[0]), 'decimal_price': 2.125})
    analysis = [{'candidate_id': row['candidate_id'], 'status': 'CALCULATED',
                 'history_league_id': 'fixture-league', 'period': 'FULL_TIME',
                 'profiles': {'home': {'team_id': row['home_team'],
                    'historical_20': _sample(3 if row['selection_key'] == 'over' else 1)},
                              'away': {'team_id': row['away_team'], 'historical_20': _sample(2)}},
                 'missing_support': [], 'limitations': ['Not a calibrated future probability.']}
                for row in candidates]
    enriched = {'candidates': deepcopy(candidates), 'history_analysis': {'candidates': analysis}}
    enriched['candidates'][0]['decimal_price'] = 2.25
    enriched['candidates'][0]['quoted_at'] = '2026-11-12T09:59:00+00:00'
    enriched['candidates'][0]['home_team'] = 'Conflicting producer team name'
    value = {'candidate_input': {'candidates': candidates}, 'enriched': enriched,
             'club_enriched': {}, 'odds_bulk': {'raw_provider_body': 'not a new HTTP response'},
             'historical_runs': []}
    families = {
        'odds_and_price_history': {'status': 'AVAILABLE', 'source_refs': ['fixture-source'], 'value': value},
        'retained_history': {'status': 'AVAILABLE', 'source_refs': ['fixture-source'],
            'value': {'collected_online': {'records': [{'fixture_id': f'h{index}', 'score': index % 3,
                                                       'home_team_id': 'home', 'away_team_id': 'away',
                                                       'kickoff': '2026-11-01T10:00:00+00:00'}
                                                       for index in range(40)]},
                      'consumed_model_input': {'history': [{'fixture_id': 'h1', 'home_team_id': 'home',
                          'away_team_id': 'away', 'kickoff': '2026-11-01T10:00:00+00:00'}],
                                               'online_history': [{'fixture_id': 'h1', 'home_team_id': 'home',
                          'away_team_id': 'away', 'kickoff': '2026-11-01T10:00:00+00:00'}]}}},
        'weather': {'status': 'AVAILABLE', 'source_refs': ['fixture-source'],
            'value': {'by_fixture': {row['fixture_id']: {'status': 'SOURCED', 'temperature_c': 5.125,
                                                      'precipitation_probability_pct': None}
                                     for row in fixtures}, 'categories': {}}},
        'historical_strategy_evaluation': {'status': 'AVAILABLE', 'source_refs': ['fixture-source'],
            'value': {'evidence_kind': 'DESCRIPTIVE_RECORDED_RESULTS_V1', 'own_result_status': 'NOT_REQUESTED',
                      'available_window': None, 'admitted_record_count': 40,
                      'tool_catalog': [{'policy_hash': 'fixture-policy', 'probability_or_profit': False}],
                      'original_strategy_decision_history': False, 'profit_or_PIT_claim': False}},
    }
    snapshot = build_snapshot(run_id='TESTONLY-compact-20261112', target_date='2026-11-12',
        as_of='2026-11-12T10:01:00+00:00', authoritative_fixture_ids=[row['fixture_id'] for row in fixtures],
        calendar_receipt={'target_date': '2026-11-12', 'scope': 'FULL_PROVIDER_DAY', 'status': 'COMPLETE',
                          'fixture_ids': [row['fixture_id'] for row in fixtures], 'source_refs': ['fixture-source']},
        fixtures=fixtures, family_values=families,
        source_manifest={'fixture-source': {'kind': 'TESTONLY_IN_MEMORY', 'physical_validation': False}})
    return snapshot, candidates, enriched


def test_named_tables_reconstruct_missing_null_false_zero_empty_and_exact_float():
    records = [{'identity': 'same-long-identity-value', 'mixed': False, 'number': 0, 'empty': ''},
               {'identity': 'same-long-identity-value', 'mixed': 0, 'number': 1.0000000000000002, 'empty': []},
               {'identity': 'same-long-identity-value', 'mixed': None, 'number': None},
               {'identity': 'another-long-identity-value', 'empty': {}}]
    encoded = encode_table(records)
    assert json_text(decode_table(encoded)) == json_text(records)
    assert 'number' not in decode_table(encoded)[3]
    assert decode_table(encoded)[2]['number'] is None


def test_complete_133_fixture_3533_candidate_inventory_and_snapshot_remain_exact():
    snapshot, source_rows, _ = _snapshot(133, 3533)
    before = snapshot.payload_json
    view = decision_view(snapshot)
    fixtures = decode_table(view['families']['calendar']['value']['fixtures'])
    decoded = decode_candidates(view['families']['odds_and_price_history']['value'], 'candidate_input', fixtures)
    assert view['fixture_ids'] == [f'fixture-{index}' for index in range(133)]
    assert [row['candidate_id'] for row in decoded] == [row['candidate_id'] for row in source_rows]
    assert len(decoded) == 3533 and len(fixtures) == 133
    assert view['view']['fixture_or_candidate_shortlist_applied'] is False
    assert snapshot.payload_json == before
    # This fixture is a coverage/reconstruction check, not real-calendar fit proof.


def test_producer_price_clock_and_team_conflicts_survive_exact_base_deltas():
    snapshot, original, enriched = _snapshot()
    view = decision_view(snapshot)
    fixtures = decode_table(view['families']['calendar']['value']['fixtures'])
    odds = view['families']['odds_and_price_history']['value']
    baseline = decode_candidates(odds, 'candidate_input', fixtures)
    changed = decode_candidates(odds, 'enriched', fixtures)
    assert baseline == original
    assert changed == enriched['candidates']
    assert baseline[0]['quoted_at'] is None and changed[0]['quoted_at'] is not None
    assert baseline[0]['decimal_price'] == 1.875 and changed[0]['decimal_price'] == 2.25
    assert changed[0]['home_team'] == 'Conflicting producer team name'


def test_ambiguous_base_ids_are_not_silently_resolved_to_one_price():
    snapshot, original, enriched = _snapshot(duplicate=True)
    view = decision_view(snapshot)
    fixtures = decode_table(view['families']['calendar']['value']['fixtures'])
    odds = view['families']['odds_and_price_history']['value']
    assert decode_candidates(odds, 'candidate_input', fixtures) == original
    assert decode_candidates(odds, 'enriched', fixtures) == enriched['candidates']
    assert [row['decimal_price'] for row in decode_candidates(odds, 'candidate_input', fixtures)
            if row['candidate_id'] == 'candidate-0'] == [1.875, 2.125]


def test_same_fixture_distinct_market_rates_and_sample_metrics_are_not_merged():
    snapshot, _, _ = _snapshot(1, 2)
    odds = decision_view(snapshot)['families']['odds_and_price_history']['value']
    links = decode_table(odds['candidate_feature_links']['enriched'])
    features = decode_table(odds['feature_values'])
    profiles = decode_table(odds['history_profiles'])
    windows = decode_table(odds['history_windows'])
    over_profile = profiles[features[links[0]['feature_ref']]['profiles']['home']['profile_view_ref']]
    under_profile = profiles[features[links[1]['feature_ref']]['profiles']['home']['profile_view_ref']]
    over_ref = windows[over_profile['historical_20']['window_view_ref']]
    under_ref = windows[under_profile['historical_20']['window_view_ref']]
    expanded = decode_candidate_features(odds, 'enriched')
    over = expanded[0]['profiles']['home']['historical_20']
    under = expanded[1]['profiles']['home']['historical_20']
    assert (over['market_hits'], over['descriptive_hit_rate']) == (3, 0.75)
    assert (under['market_hits'], under['descriptive_hit_rate']) == (1, 0.25)
    assert over_ref['sample_ref'] == under_ref['sample_ref']
    sample = over
    assert sample['sample_size'] == 4 and sample['goals_for_mean'] == 1.25
    assert sample['results_newest_first'] == ['W', 'D', 'L', 'W']
    assert sample['fixture_ids']['row_count'] == 4
    assert sample['fixture_ids']['raw_rows_read_by_host'] is False


def test_historical_raw_lists_named_and_tool_public_values_not_downgraded_to_counts():
    snapshot, _, _ = _snapshot()
    view = decision_view(snapshot)
    retained = view['families']['retained_history']['value']
    assert retained['collected_online']['records']['row_count'] == 40
    assert retained['consumed_model_input']['online_history']['row_count'] == 1
    assert retained['collected_online']['records']['detail_level'] == 'COMPLETE_COHORT_COUNTS_NOT_INDIVIDUAL_MATCH_READ'
    assert view['families']['historical_strategy_evaluation']['value'] == snapshot.data()[
        'families']['historical_strategy_evaluation']['value']
    assert view['families']['sourced_news']['status'] == 'UNKNOWN'
    assert view['families']['sourced_news']['value'] is None


def test_final_details_expand_only_this_owners_existing_options_and_preserve_unknowns():
    snapshot, _, _ = _snapshot()
    option = {'agent_id': OWNER_IDS[0], 'option_id': 'own-only',
              'legs': [{'candidate_id': 'candidate-0', 'fixture_id': 'fixture-0'}]}
    details = final_option_details(snapshot, OWNER_IDS[0], [option])
    assert details['candidate_ids'] == ['candidate-0']
    assert details['fixture_ids'] == ['fixture-0']
    assert details['source_candidates_by_producer']['candidate_input'][0]['quoted_at'] is None
    assert details['fixture_context']['weather']['selected_fixture_values']['by_fixture'] == {
        'fixture-0': {'status': 'SOURCED', 'temperature_c': 5.125, 'precipitation_probability_pct': None}}
    assert details['history_features_by_producer']['enriched'][0]['profiles']['home']['historical_20'][
        'market_hits'] == 3
    with pytest.raises(ValueError, match='ONLY_THIS_OWNERS_PROPOSED_OPTIONS'):
        final_option_details(snapshot, OWNER_IDS[1], [option])


def test_unknown_source_candidate_is_rejected_without_fabricated_context():
    snapshot, _, _ = _snapshot()
    option = {'agent_id': OWNER_IDS[0], 'option_id': 'foreign-source',
              'legs': [{'candidate_id': 'not-in-snapshot', 'fixture_id': 'fixture-0'}]}
    with pytest.raises(ValueError, match='OWN_OPTION_SOURCE_CANDIDATE_NOT_FOUND'):
        final_option_details(snapshot, OWNER_IDS[0], [option])


def test_final_reference_retains_full_day_and_all_family_hashes_without_other_options():
    snapshot, _, _ = _snapshot(4, 8)
    before = snapshot.payload_json
    plan_view = decision_view(snapshot)
    shared = final_shared_reference(snapshot, plan_view)
    details = final_option_details(snapshot, OWNER_IDS[0], [{'agent_id': OWNER_IDS[0], 'option_id': 'own-a',
        'legs': [{'candidate_id': 'candidate-0', 'fixture_id': 'fixture-0'}]}])
    assert shared['fixture_ids'] == ['fixture-0', 'fixture-1', 'fixture-2', 'fixture-3']
    assert len(shared['families']) == 12
    assert shared['view']['new_candidate_selection_allowed'] is False
    for name, envelope in snapshot.data()['families'].items():
        assert shared['families'][name]['status'] == envelope['status']
        assert shared['families'][name]['value_sha256'] == envelope['value_sha256']
        assert 'value' not in shared['families'][name]
    assert details['option_ids'] == ['own-a']
    assert 'candidate-1' not in json_text(details)
    assert details['source_candidates_by_producer']['enriched'][0]['home_team'] == 'Conflicting producer team name'
    assert snapshot.payload_json == before
    changed = deepcopy(plan_view)
    changed['families']['weather']['value_sha256'] = 'forged'
    with pytest.raises(ValueError, match='PLAN_FAMILY_BINDING_CHANGED'):
        final_shared_reference(snapshot, changed)


def test_final_candidate_cannot_be_relabelled_as_another_fixture():
    snapshot, _, _ = _snapshot()
    with pytest.raises(ValueError, match='OWN_OPTION_FIXTURE_IDENTITY_MISMATCH'):
        final_option_details(snapshot, OWNER_IDS[0], [{'agent_id': OWNER_IDS[0], 'option_id': 'wrong-fixture',
            'legs': [{'candidate_id': 'candidate-0', 'fixture_id': 'fixture-1'}]}])


def test_existing_tipster_constructor_alias_resolves_to_the_original_quote_only():
    snapshot, _, _ = _snapshot()
    owner = 'tipster_archiv_nora'
    source = 'candidate-0'
    alias = owner + '-' + digest([owner, source])[:20]
    option = {'agent_id': owner, 'option_id': 'club-own', 'legs': [{
        'candidate_id': alias, 'source_candidate_id': source, 'quote_candidate_id': source,
        'fixture_id': 'fixture-0'}]}
    details = final_option_details(snapshot, owner, [option])
    assert details['candidate_ids'] == [source]
    assert details['source_candidates_by_producer']['candidate_input'][0]['candidate_id'] == source
    changed = deepcopy(option)
    changed['legs'][0]['quote_candidate_id'] = 'candidate-1'
    with pytest.raises(ValueError, match='CONFLICTING_OR_INVALID_SOURCE_CANDIDATE_ALIASES'):
        final_option_details(snapshot, owner, [changed])
    changed = deepcopy(option)
    changed['legs'][0]['candidate_id'] = 'some-other-owner-alias'
    with pytest.raises(ValueError, match='UNRECOGNIZED_OWNER_CANDIDATE_ALIAS'):
        final_option_details(snapshot, owner, [changed])


def test_internal_inheritance_metadata_never_silently_overwrites_a_source_field():
    snapshot, _, _ = _snapshot()
    data = snapshot.data()
    data['families']['odds_and_price_history']['value']['candidate_input']['candidates'][0][
        'inherited_fixture_fields'] = ['producer-owned-field']
    from goal_analysis.jobs.agent_snapshot import SharedSnapshot
    changed = SharedSnapshot(digest(data), json_text(data))
    with pytest.raises(ValueError, match='RESERVED_CANDIDATE_VIEW_FIELD'):
        decision_view(changed)


def test_public_tool_projection_keeps_all_option_metrics_and_unknown_gaps_exact():
    metrics = {'own-a': {'legs': [{'scope_samples': {'home': {'sample_size': 7, 'market_hits': 4,
        'market_misses': 3, 'unknown_period_scores': 2, 'descriptive_hit_rate': 4 / 7,
        'score_histogram_original_home_away': {'1-0': 4, '0-1': 3}, 'sample_manifest_sha256': 'sample-1'}},
        'status': 'AVAILABLE'}], 'historical_roi': None}}
    rows = [{'request': {'request_id': 'request-a'}, 'result': {'status': 'AVAILABLE', 'value': {
        'by_option': metrics, 'sample_manifests': {'sample-1': [{'record_id': 'history-1'}]}},
        'diagnostics': {'gap': 'UNKNOWN_FX', 'sample_manifests': {'unknown': []}}}}]
    original = deepcopy(rows)
    projected = public_tool_results(rows)
    assert rows == original
    assert projected[0]['result']['value']['by_option'] == metrics
    assert projected[0]['result']['diagnostics']['gap'] == 'UNKNOWN_FX'
    assert projected[0]['result']['value']['sample_manifests']['manifest_rows'] == {'sample-1': 1}
    assert projected[0]['result']['prompt_projection']['original_result_sha256'] == digest(original[0]['result'])
    assert projected[0]['result']['prompt_projection']['raw_manifest_rows_read_by_host'] is False


def test_final_oversize_stops_before_budget_claim_journal_or_host():
    from types import SimpleNamespace

    from goal_analysis.agents.profile_decision_runner import ProfileDecisionRunner

    def forbidden(*args, **kwargs):
        raise AssertionError('OVERSIZE_MUST_STOP_BEFORE_ANY_CLAIM_OR_HOST')

    runner = ProfileDecisionRunner(host=forbidden, tools=None, option_builder=forbidden,
        journal=SimpleNamespace(claim=forbidden), budget=SimpleNamespace(reserve=forbidden))
    state = SimpleNamespace(agent_id=OWNER_IDS[0], session_id='fixture-session', snapshot_id='fixture-snapshot')
    with pytest.raises(ValueError, match='OWNER_INPUT_BUDGET_EXCEEDED'):
        runner._call(state, 'FINAL', {'own_source_details': 'x' * 360001})
