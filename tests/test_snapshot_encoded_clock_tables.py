"""Pure source-codec clock regressions; no DB, model, network or host calls."""
from copy import deepcopy
from datetime import datetime

from goal_analysis.agents.profile_input import decode_table, encode_table
from goal_analysis.jobs.agent_snapshot import (
    FAMILIES,
    OWNER_IDS,
    _normal_clock_issues,
    build_normal_snapshot,
)

CUTOFF = datetime.fromisoformat('2026-11-14T08:52:18.057837+00:00')
EARLY = '2026-11-13T21:43:51.725657+00:00'
LATE = '2026-11-13T21:44:10.780165+00:00'
FUTURE = '2026-11-14T08:53:00+00:00'


def _table(second=LATE):
    rows = [{'candidate_id': f'TESTONLY-{index}', 'quote_observed_at': EARLY if index % 2 else second}
            for index in range(64)]
    table = encode_table(rows)
    assert next(row for row in table['columns'] if row['name'] == 'quote_observed_at')['encoding'] == 'lookup'
    return table


def _frame(table):
    events = [{'fixture_id': f'TESTONLY-fixture-{index}', 'kickoff': '2026-11-14T19:00:00+00:00'}
              for index in range(3)]
    prior = {'ten_agent_decisions': {'owners': [{'decision_evidence': {'plan_auxiliary_inputs': {
        'values': {'own_construction_contract': {'full_candidate_catalog': {'table': deepcopy(table)},
        'source_navigation': {'probability_view': {'namespace': 'view', 'path': ['model_probabilities'],
            'format': 'NAMED_COLUMNS_V1', 'identifier_column': 'probability_id'}}}}}}}
        for _ in range(7)]}}
    return {'run_id': 'TESTONLY-encoded-clock-normal', 'target_date': '2026-11-14',
        'snapshot_as_of': CUTOFF.isoformat(), 'historical_runs': [prior],
        'artifacts': {'calendar': {'scope_receipt': {'scope': 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY',
            'date': '2026-11-14', 'status': 'COMPLETE', 'provider_calendar_status': 'COMPLETE',
            'routing_status': 'COMPLETE', 'provider_fixture_ids': [row['fixture_id'] for row in events],
            'events': events}}, 'candidate_input': {'candidates': [{'candidate_id': 'TESTONLY-current',
                'fixture_id': events[0]['fixture_id'], 'quoted_at': None, 'quote_observed_at': EARLY}]}},
        'weather_by_fixture': {events[0]['fixture_id']: {'status': 'SOURCED', 'observed_at': EARLY,
            'location_precision': 'CITY_APPROXIMATION', 'temperature_c': 5.0}}}


def test_normal_families_decode_historical_table_without_changing_full_inputs():
    inputs = _frame(_table())
    original = deepcopy(inputs)
    data = build_normal_snapshot(**inputs).data()
    assert inputs == original
    assert data['fixture_ids'] == original['artifacts']['calendar']['scope_receipt']['provider_fixture_ids']
    assert data['owner_ids'] == list(OWNER_IDS) and len(data['owner_ids']) == 10
    assert set(data['families']) == set(FAMILIES)
    for name in ('odds_and_price_history', 'own_outcomes_and_costs'):
        family = data['families'][name]
        assert family['status'] == 'AVAILABLE' and family['clock_issues'] == []
        assert family['value']['historical_runs'] == original['historical_runs']
    assert data['families']['weather']['status'] == 'AVAILABLE'
    assert data['as_of'] == original['snapshot_as_of']


def test_future_lookup_clock_is_detected_in_every_represented_row():
    table = _table(FUTURE)
    original = deepcopy(table)
    issues = _normal_clock_issues(table, CUTOFF)
    assert len(issues) == 32
    assert all(row['reason'] == 'KNOWLEDGE_AFTER_SNAPSHOT' and '/decoded_rows/' in row['path'] for row in issues)
    assert table == original


def test_naive_default_clock_remains_invalid():
    table = encode_table([{'observed_at': '2026-11-13T21:44:00', 'candidate_id': f'row-{index}'} for index in range(4)])
    assert table['defaults']['observed_at'] == '2026-11-13T21:44:00'
    issues = _normal_clock_issues(table, CUTOFF)
    assert len(issues) == 4 and all(row['reason'] == 'NAIVE_KNOWLEDGE_CLOCK' for row in issues)


def test_actual_clock_lists_in_literal_cells_are_not_treated_as_dictionaries():
    rows = [{'quoted_at': [EARLY]}, {'quoted_at': [LATE]}]
    table = encode_table(rows)
    assert table['columns'] == [{'name': 'quoted_at', 'encoding': 'literal'}]
    assert decode_table(table) == rows
    issues = _normal_clock_issues(table, CUTOFF)
    assert len(issues) == 2 and all(row['reason'] == 'INVALID_KNOWLEDGE_CLOCK' for row in issues)
    assert _normal_clock_issues({'quoted_at': [EARLY]}, CUTOFF) == [
        {'path': '/quoted_at', 'reason': 'INVALID_KNOWLEDGE_CLOCK'}]


def test_missing_null_false_and_zero_clock_values_stay_distinct():
    rows = [{}, {'quoted_at': None}, {'quoted_at': False}, {'quoted_at': 0}]
    table = encode_table(rows)
    assert decode_table(table) == rows
    issues = _normal_clock_issues(table, CUTOFF)
    assert issues == [{'path': '/decoded_rows/2/quoted_at', 'reason': 'INVALID_KNOWLEDGE_CLOCK'},
                      {'path': '/decoded_rows/3/quoted_at', 'reason': 'INVALID_KNOWLEDGE_CLOCK'}]
    assert _normal_clock_issues(encode_table([{}, {'quoted_at': None}]), CUTOFF) == []


def test_corrupt_table_is_local_and_does_not_hide_future_metadata():
    for defect in ('hash', 'boolean_index', 'unused_lookup', 'missing_index'):
        table = _table()
        if defect == 'hash':
            table['direct_rows_sha256'] = '0' * 64
        elif defect == 'boolean_index':
            column = next(index for index, row in enumerate(table['columns']) if row['name'] == 'quote_observed_at')
            table['rows'][0][column] = True
        elif defect == 'unused_lookup':
            table['lookups']['quote_observed_at'].append(FUTURE)
        else:
            table['missing_rows']['quote_observed_at'] = [table['row_count']]
        table['audit_metadata'] = {'published_at': FUTURE}
        issues = _normal_clock_issues(table, CUTOFF)
        assert any(row['reason'] == 'INVALID_ENCODED_CLOCK_TABLE' for row in issues)
        assert any(row['reason'] == 'KNOWLEDGE_AFTER_SNAPSHOT' for row in issues)
        data = build_normal_snapshot(**_frame(table)).data()
        assert data['families']['odds_and_price_history']['status'] == 'CONFLICTING'
        assert data['families']['weather']['status'] == 'AVAILABLE'
        assert len(data['fixture_ids']) == 3


def test_format_descriptor_and_extra_metadata_are_walked_normally():
    descriptor = {'namespace': 'view', 'path': ['model_probabilities'],
                  'format': 'NAMED_COLUMNS_V1', 'identifier_column': 'probability_id'}
    assert _normal_clock_issues(descriptor, CUTOFF) == []
    descriptor['retrieved_at'] = FUTURE
    assert _normal_clock_issues(descriptor, CUTOFF) == [
        {'path': '/retrieved_at', 'reason': 'KNOWLEDGE_AFTER_SNAPSHOT'}]
    table = _table()
    table['producer_metadata'] = {'available_at': FUTURE}
    assert _normal_clock_issues(table, CUTOFF) == [
        {'path': '/producer_metadata/available_at', 'reason': 'KNOWLEDGE_AFTER_SNAPSHOT'}]


def test_nested_tables_empty_tables_and_missing_schema_remain_explicit():
    inner = encode_table([{'quote_observed_at': FUTURE}])
    outer = encode_table([{'nested': inner}, {'nested': inner}])
    issues = _normal_clock_issues(outer, CUTOFF)
    assert len(issues) == 2 and all(row['reason'] == 'KNOWLEDGE_AFTER_SNAPSHOT' for row in issues)
    assert _normal_clock_issues(encode_table([]), CUTOFF) == []
    assert _normal_clock_issues({'format': 'NAMED_COLUMNS_V1', 'rows': []}, CUTOFF) == [
        {'path': '', 'reason': 'INVALID_ENCODED_CLOCK_TABLE'}]
