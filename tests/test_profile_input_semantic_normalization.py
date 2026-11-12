"""Pure V3 source-preserving projections, not model/host/physical evidence."""
from copy import deepcopy

import pytest

from goal_analysis.agents.profile_input import (
    _candidate_values,
    _context,
    _history,
    _models,
    decode_candidate_features,
    decode_candidates,
    decode_context,
    decode_model_results,
    decode_table,
)
from goal_analysis.jobs.agent_snapshot import digest, json_text


def _history_record():
    return {'fixture_id': 'TESTONLY:h1', 'home_team_id': 'TESTONLY:home',
        'away_team_id': 'TESTONLY:away', 'kickoff': '2026-11-01T12:00:00+00:00',
        'full_time_home': 2, 'full_time_away': 0, 'source_id': 'TESTONLY:source'}


def _dataset():
    return {'manifest': {'dataset_id': 'TESTONLY:dataset', 'row_count': 1,
        'role': 'TESTONLY', 'as_of': '2026-11-12T10:00:00+00:00',
        'rows_sha256': digest([_history_record()])}, 'rows': [_history_record()],
        'lineage': {'input_partition': [{'fixture_id': 'TESTONLY:h1', 'partition': 'RETAINED'}],
            'selected_record_origins': [{'fixture_id': 'TESTONLY:h1', 'origin': 'RETAINED'}],
            'retained_lineage_sha256': 'a' * 64, 'online_clock_basis': 'NOT_PIT'}}


def test_repeated_model_dataset_rows_and_lineage_are_honest_full_backing_refs():
    data = {'consumed_model_input': {'history': [_history_record()], 'online_history': [],
        'dataset': _dataset(), 'selected_record_origins': [{'fixture_id': 'TESTONLY:h1'}],
        'selected_record_origin_counts': {'RETAINED': 1}, 'partition_counts': {'RETAINED': 1},
        'reader_diagnostics': {'actual_reader_calls': 1, 'availability': 'UNKNOWN'}}}
    before = deepcopy(data)
    view = _history(data)
    consumed = view['consumed_model_input']
    assert consumed['history']['row_count'] == 1
    assert consumed['dataset']['manifest'] == data['consumed_model_input']['dataset']['manifest']
    assert consumed['dataset']['rows']['canonical_value_sha256'] == digest([_history_record()])
    assert consumed['dataset']['rows']['row_count'] == 1
    assert consumed['dataset']['rows']['physical_source_validation'] == 'NOT_PERFORMED'
    assert consumed['dataset']['lineage']['input_partition']['row_count'] == 1
    assert consumed['dataset']['lineage']['online_clock_basis'] == 'NOT_PIT'
    assert consumed['selected_record_origins']['canonical_value_sha256'] == digest(
        data['consumed_model_input']['selected_record_origins'])
    assert consumed['selected_record_origin_counts'] == {'RETAINED': 1}
    assert consumed['reader_diagnostics'] == before['consumed_model_input']['reader_diagnostics']
    assert data == before


def test_unrecognized_dataset_and_nonstandard_history_are_not_guessed_or_dropped():
    source = {'consumed_model_input': {'history': [{'unknown_record': 1}],
        'dataset': {'dataset_id': 'TESTONLY:minimal', 'rows': [{'future_shape': True}]}}}
    assert _history(source) == source


def _model_result():
    manifest = {'dataset_id': 'TESTONLY:same-id', 'role': 'training', 'row_count': 40,
                'rows_sha256': 'a' * 64, 'recipe': {'version': 'existing'}}
    different = {**manifest, 'row_count': 41, 'rows_sha256': 'b' * 64}
    details = {'version': 'TESTONLY:actual-model-shape', 'training_count': 40,
        'cutoff': '2026-11-12T10:00:00+00:00', 'history_hash': 'c' * 64,
        'library_versions': {'solver': 'existing'}, 'windows': [730, 180],
        'window_training_counts': {'730': 40, '180': 20},
        'variants': {'dixon_coles': {'home': .6123456789012345, 'away': .2, 'draw': .1876543210987655}},
        'fits': {'dixon_coles': {'fitted': True, 'log_likelihood': -12.123456789, 'parameter_count': 13}},
        'calibration': 'UNCALIBRATED', 'ensemble': {'weights': [0.5, 0.5]},
        'stability_definition': 'EXISTING_METHOD', 'closure_delay_hours': 3,
        'venue_assumption': 'EXPLICIT_STANDARD_ONLY', 'custom_meaningful_field': {'keep': False}}
    prediction = {'fixture_id': 'TESTONLY:fixture', 'probabilities': {'home': .6123456789012345},
                  'stability': {'home': .0123}, 'model_details': details}
    first = {'status': 'READY', 'predictions': [prediction],
        'diagnostics': [{'code': 'TESTONLY:KNOWN_GAP', 'count': 1}],
        'training_datasets': {'manifests': [manifest], 'uses': [{'slot': 'home', 'dataset_id': manifest['dataset_id']}],
            'archive': {'status': 'SAVED', 'path': 'TESTONLY:path', 'bytes': 123, 'sha256': 'd' * 64}}}
    second = deepcopy(first)
    second['training_datasets']['manifests'] = [different]
    return {'status': 'PARTIAL', 'predictions': [deepcopy(prediction), deepcopy(prediction)],
        'diagnostics': deepcopy(first['diagnostics']) * 2,
        'training_datasets': {'manifests': [manifest, different], 'uses': first['training_datasets']['uses'] * 2},
        'competition_results': {'first': first, 'second': second}, 'message': 'One exact conflicted dataset ID.'}


def test_model_components_round_trip_all_meaningful_fields_and_conflicting_dataset_ids():
    source = {'results': {'gauss': _model_result()}, 'adapter_gaps': ['TESTONLY:local gap']}
    before = deepcopy(source)
    projected = _models(source, {})
    assert json_text(decode_model_results(projected)) == json_text(source['results'])
    tables = projected['named_model_tables']
    assert len(decode_table(tables['predictions'])) == 1
    manifests = decode_table(tables['training_manifests'])
    assert len(manifests) == 2 and manifests[0]['dataset_id'] == manifests[1]['dataset_id']
    assert manifests[0]['rows_sha256'] != manifests[1]['rows_sha256']
    top = projected['results']['gauss']
    assert top['predictions']['ordered_rows'] == [0, 0]
    assert top['competition_results']['first']['predictions']['ordered_rows'] == [0]
    assert projected['adapter_gaps'] == source['adapter_gaps']
    assert source == before


def test_unrecognized_model_details_and_dataset_metadata_stay_literal():
    source = {'results': {'new-model': {'predictions': [{'fixture_id': 'f',
        'model_details': {'expected_goals': 2.4, 'novel_fact': None}}],
        'training_datasets': {'dataset_id': 'TESTONLY:minimal'}}}}
    assert decode_model_results(_models(source, {})) == source['results']


def test_context_feature_snapshots_are_referenced_but_all_team_metrics_and_units_survive():
    feature = {'fixture_id': 'f', 'as_of': '2026-11-12T10:00:00+00:00',
        'units': {'travel': 'km'}, 'teams': {'home': {'rest_hours': None, 'distance_km': 156.12345}},
        'dataset': _dataset(), 'competition_context': {'unknown_lineup': True}}
    source = {'rotation': {'features': [deepcopy(feature)]},
        'categories': {'f': [{'category': 'players', 'status': 'SOURCED',
            'data': {'injuries': ['TESTONLY:player'], 'minutes': None},
            'reason': 'Lineup unavailable', 'source_urls': ['TESTONLY:url']}]},
        'by_fixture': {'f': {'temperature_c': -1.23456789, 'precipitation_probability_pct': 0,
                             'provider_issue_time': None, 'observed_at': '2026-11-12T10:00:00+00:00'}}}
    before = deepcopy(source)
    view = _context('players_squad_xi', source)
    expanded = decode_context(view)
    assert expanded['categories'] == source['categories']
    assert expanded['by_fixture'] == source['by_fixture']
    actual = decode_table(view['rotation']['features'])[0]
    assert {key: value for key, value in actual.items() if key != 'dataset'} == {
        key: value for key, value in feature.items() if key != 'dataset'}
    assert actual['dataset']['manifest'] == feature['dataset']['manifest']
    assert actual['dataset']['rows']['canonical_value_sha256'] == digest(feature['dataset']['rows'])
    assert source == before


def _evidence_source():
    sample = {'sample_size': 4, 'market_hits': 3, 'descriptive_hit_rate': .75,
        'fixture_ids': ['h1', 'h2', 'h3', 'h4'], 'source_ids': ['s'] * 4,
        'missing_period_scores': [], 'results_newest_first': ['W', 'W', 'L', 'W'],
        'goals_for_mean': 1.25, 'goals_against_mean': .75}
    feature = {'candidate_id': 'c', 'status': 'CALCULATED',
        'profiles': {'home': {'team_id': 'home', 'historical_20': deepcopy(sample)},
                     'away': {'team_id': 'away', 'historical_20': deepcopy(sample)}}}
    generated = {'evidence_id': 'history-v1:c:historical', 'category': 'historical',
        'claim_type': 'INFERENCE', 'statement': 'FULL_TIME totals_2_5 / over; historical_20: '
        'hazai oldal 3/4, vendég oldal 3/4. Megfigyelt piaci mintázat, nem becsült nyerési esély.',
        'observed_at': '2026-11-12T10:00:00+00:00', 'source_id': 'history-sha256:' + 'a' * 64,
        'strength': .75, 'reliability': .2}
    news = {'evidence_id': 'TESTONLY:news', 'category': 'players', 'claim_type': 'SOURCED',
        'statement': 'TESTONLY: the goalkeeper is unavailable; replacement is unknown.',
        'source_id': 'TESTONLY:club-news', 'observed_at': '2026-11-12T09:00:00+00:00'}
    quote = {'candidate_id': 'c', 'fixture_id': 'f', 'market_key': 'totals_2_5',
        'selection_key': 'over', 'period': 'FULL_TIME', 'decimal_price': 2.125,
        'quoted_at': None, 'evidence': [generated, news]}
    return {'candidate_input': {'candidates': [{**deepcopy(quote), 'evidence': []}]},
        'enriched': {'candidates': [quote], 'history_analysis': {'candidates': [feature]}}}


def test_only_exact_generated_history_text_uses_template_and_freeform_news_stays_literal():
    source = _evidence_source()
    before = deepcopy(source)
    odds = _candidate_values(source, {'f': {'fixture_id': 'f'}})
    decoded = decode_candidates(odds, 'enriched', [{'fixture_id': 'f'}])
    assert decoded == source['enriched']['candidates']
    evidence = decode_table(odds['evidence_values'])
    assert evidence[0]['generated_history_template'] is True
    assert evidence[1] == source['enriched']['candidates'][0]['evidence'][1]
    feature = decode_candidate_features(odds, 'enriched')[0]
    assert feature['profiles']['home']['historical_20']['descriptive_hit_rate'] == .75
    assert source == before


def test_unverified_history_prefix_cannot_hide_freeform_explanation():
    source = _evidence_source()
    source['enriched']['candidates'][0]['evidence'][0]['statement'] += ' Important extra injury fact.'
    odds = _candidate_values(source, {})
    evidence = decode_table(odds['evidence_values'])
    assert 'generated_history_template' not in evidence[0]
    assert evidence[0] == source['enriched']['candidates'][0]['evidence'][0]
    assert decode_candidates(odds, 'enriched', []) == source['enriched']['candidates']


@pytest.mark.parametrize('key', ['source_candidate_index', 'source_evidence_indexes'])
def test_reserved_projection_fields_are_rejected_before_they_can_overwrite_source(key):
    source = _evidence_source()
    source['candidate_input']['candidates'][0][key] = 'TESTONLY:producer-data'
    with pytest.raises(ValueError, match='RESERVED_CANDIDATE_VIEW_FIELD'):
        _candidate_values(source, {})
