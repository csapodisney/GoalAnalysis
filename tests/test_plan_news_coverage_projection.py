"""Full-calendar news coverage stays lossless without repeated field text."""
from copy import deepcopy

import pytest

from goal_analysis.agents.profile_input import (
    NEWS_COVERAGE_REQUEST_PROJECTION,
    decode_table,
    final_request_data,
    news_coverage_request_data,
)
from goal_analysis.jobs.agent_snapshot import digest, json_text
from goal_analysis.jobs.ten_agent_ticket_evidence import _owner_evidence


def _daily_data():
    fixtures = [f'provider:fixture-{index}' for index in range(274)]
    rows = [{'fixture_id': fixture, 'topic': topic,
             'status': 'AVAILABLE' if index % 37 == 0 else 'UNKNOWN',
             **({'published_at': None} if index % 2 else {})}
            for index, fixture in enumerate(fixtures)
            for topic in ('coach', 'injury', 'lineup', 'team', 'venue')]
    return {
        'snapshot': {'fixture_ids': fixtures,
            'calendar': [{'fixture_id': fixture, 'kickoff': '2026-11-14T20:00:00+00:00'}
                         for fixture in fixtures],
            'families': {'sourced_news': {'status': 'AVAILABLE',
                'value_sha256': 'original-news-value-hash',
                'coverage': {'by_fixture_topic': rows, 'publication_time_inferred': False,
                             'all_fixtures_independently_researched': False}},
                'weather': {'status': 'UNKNOWN', 'value_sha256': 'original-weather-hash'}},
            'historical_tool_catalog': {'profit_or_PIT_claim': False},
            'source_manifest': {'source_clock': '2026-11-13T21:00:00+00:00'}},
        'own_construction_contract': {'full_candidate_catalog': {
            'all_candidate_ids': [f'candidate-{index}' for index in range(1660)],
            'candidate_filter_applied': False}},
        'own_strategy_search': {'suggestions': [], 'status': 'NO_COMPLIANT_OPTION'},
    }


def test_full_274_fixture_1370_topic_rows_round_trip_with_unknown_null_and_clocks():
    data = _daily_data()
    before = deepcopy(data)
    result = news_coverage_request_data(data)
    marker = result.pop('coverage_data_projection')
    assert marker['version'] == NEWS_COVERAGE_REQUEST_PROJECTION
    assert marker['source_data_sha256'] == digest(before)
    assert marker['path'] == ['snapshot', 'families', 'sourced_news', 'coverage', 'by_fixture_topic']
    coverage = result['snapshot']['families']['sourced_news']['coverage']
    table = coverage['by_fixture_topic']
    restored = decode_table(table)
    assert len(restored) == 1370 and table['row_count'] == 1370
    assert marker['source_rows_sha256'] == table['direct_rows_sha256'] == digest(restored)
    coverage['by_fixture_topic'] = restored
    assert result == before and digest(result) == marker['source_data_sha256']
    assert data == before
    assert result['snapshot']['fixture_ids'] == before['snapshot']['fixture_ids']
    assert len({row['fixture_id'] for row in restored}) == 274
    assert {row['status'] for row in restored} == {'UNKNOWN', 'AVAILABLE'}
    assert 'published_at' not in restored[0] and restored[5]['published_at'] is None
    assert result['own_construction_contract'] == before['own_construction_contract']
    assert len(json_text(news_coverage_request_data(data))) < len(json_text(data))

    # FINAL uses the same coverage path AFTER its existing lossless subtree codec.
    repeated = {'values': list(range(2000)), 'status': 'UNKNOWN', 'quoted_at': None}
    final = {'snapshot': deepcopy(before['snapshot']), 'own_plan': {'options': []},
        'validated_own_options': [], 'tool_results': [],
        'own_source_details': {'first': repeated, 'second': deepcopy(repeated)},
        'own_option_admission': {'accepted_option_ids': [], 'rejected_options': []}}
    final_before = deepcopy(final)
    subtree_encoded = final_request_data(final)
    assert 'final_data_projection' in subtree_encoded
    projected = news_coverage_request_data(subtree_encoded)
    final_marker = projected.pop('coverage_data_projection')
    assert final_marker['version'] == NEWS_COVERAGE_REQUEST_PROJECTION
    parent = projected
    for key in final_marker['path'][:-1]:
        parent = parent[key]
    parent[final_marker['path'][-1]] = decode_table(parent[final_marker['path'][-1]])
    assert projected == subtree_encoded and digest(projected) == final_marker['source_data_sha256']
    values = projected['exact_values']

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            if set(value) == {'__arthur_final_ref__'}:
                key = value['__arthur_final_ref__']
                restored_value = expand(values[key]['value'])
                assert digest(restored_value) == key == values[key]['source_value_sha256']
                return restored_value
            return {key: expand(item) for key, item in value.items()}
        return value

    restored_final = {key: expand(value) for key, value in projected.items()
                      if key not in {'exact_values', 'final_data_projection'}}
    assert restored_final == final_before
    assert digest(restored_final) == projected['final_data_projection']['source_data_sha256']
    assert final == final_before and data == before


def test_reserved_marker_unsupported_or_nonbeneficial_input_is_detached_exact_original():
    collision = _daily_data()
    collision['coverage_data_projection'] = {'original_source_value': 'Do not overwrite me.'}
    malformed = _daily_data()
    malformed['snapshot']['families']['sourced_news']['coverage']['by_fixture_topic'] = [None]
    tiny = {'snapshot': {'families': {'sourced_news': {
        'coverage': {'by_fixture_topic': [{'fixture_id': 'one', 'topic': 'coach', 'status': 'UNKNOWN'}]}}}}}
    for original in (collision, malformed, tiny, {'snapshot': None}, {}):
        before = deepcopy(original)
        result = news_coverage_request_data(original)
        assert result == before and result is not original
        result['test_only_mutation'] = True
        assert original == before


def test_unknown_plan_projection_cannot_reinterpret_saved_host_proof():
    owner = {'status': 'DECISION_COMPLETED', 'decision_evidence': {
        'coverage_request_projection': 'UNREVIEWED_OR_FOREIGN_PLAN_FORMAT'}}
    before = deepcopy(owner)
    with pytest.raises(ValueError, match='UNKNOWN_NEWS_COVERAGE_PROJECTION'):
        _owner_evidence(owner, None, {})
    assert owner == before
