"""Pure supplied-record coverage fixtures; no host, DB, fit or provider."""
from copy import deepcopy

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs.profile_historical_scenarios import (
    HistoricalScenarioEngine,
    build_history_view,
    policy_catalog,
)

NOW = '2026-11-12T18:00:00+00:00'
SETTLEMENT = 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES'


def _row(identifier, home, away, *, known=True):
    return {'fixture_id': str(identifier), 'league_id': '1', 'home_team_id': home,
        'away_team_id': away, 'status': 'FT', 'kickoff': '2026-10-15T12:00:00+00:00',
        'observed_at': '2026-10-15T15:00:00+00:00', 'neutral_venue': False,
        'full_time_home': 2 if known else None, 'full_time_away': 1 if known else None,
        'first_half_home': 1 if known else None, 'first_half_away': 0 if known else None,
        'source_id': 'TESTONLY-record-' + str(identifier)}


def _evaluate(rows):
    history = {'kind': 'DAILY223_HISTORY', 'schema_version': 1, 'provider': 'api_football',
        'collected_at': NOW, 'records': deepcopy(rows), 'sources': ['TESTONLY:coverage']}
    history['snapshot_sha256'] = canonical_sha256(history)
    view = build_history_view({'history': history}, as_of=NOW)
    fixture = {'fixture_id': 'api_football:9000', 'source_provider': 'api_football',
        'api_football_fixture_id': '9000', 'api_football_league_id': '1',
        'api_football_home_team_id': '10', 'api_football_away_team_id': '20',
        'neutral_venue': False, 'kickoff': '2026-11-13T18:00:00+00:00'}
    quote = {**fixture, 'candidate_id': 'TESTONLY-over', 'market_key': 'totals_2_5',
        'selection_key': 'over', 'period': 'FULL_TIME', 'settlement': SETTLEMENT,
        'decimal_price': 2.0, 'bookmaker': 'TESTONLY', 'quote_provider': 'TESTONLY',
        'quote_source_id': 'TESTONLY:quote', 'quote_source_sha256': 'a' * 64,
        'quote_observed_at': NOW, 'quoted_at': None}
    option = {'agent_id': 'kronikas', 'option_id': 'TESTONLY-own-over', 'legs': [deepcopy(quote)]}
    policy = next(row for row in policy_catalog() if row['scope'] == 'team_any_venue')
    payload = {'agent_id': 'kronikas', 'own_option_ids': [option['option_id']], 'as_of': NOW,
        'parameters': {'policy_hash': policy['policy_hash'],
            'window': {'start': '2026-10-01T00:00:00+00:00', 'end': NOW}}}
    result = HistoricalScenarioEngine(view).evaluate(payload, [option], candidates=[quote], fixtures=[fixture])
    return result, result['by_option'][option['option_id']]['legs'][0]['scope_samples']


def test_one_requested_team_missing_is_partial_and_keeps_known_counts():
    result, samples = _evaluate([_row(1, '10', '30')])
    assert result['status'] == 'AVAILABLE' and result['coverage_status'] == 'PARTIAL'
    assert (samples['home']['sample_size'], samples['home']['market_hits'],
            samples['home']['market_misses'], samples['home']['descriptive_hit_rate']) == (1, 1, 0, 1.0)
    assert samples['home']['coverage_status'] == 'AVAILABLE' and samples['home']['reason'] is None
    assert samples['away']['matching_row_count'] == samples['away']['sample_size'] == 0
    assert samples['away']['coverage_status'] == 'UNKNOWN'
    assert samples['away']['reason'] == 'NO_MATCHING_HISTORICAL_ROWS'
    assert samples['away']['descriptive_hit_rate'] is None
    assert result['profit_or_PIT_claim'] is False


def test_both_requested_teams_absent_stay_unknown_without_zero_probability():
    result, samples = _evaluate([_row(1, '30', '40')])
    assert result['status'] == result['coverage_status'] == 'UNKNOWN'
    assert set(samples) == {'home', 'away'}
    assert all(row['coverage_status'] == 'UNKNOWN' and row['descriptive_hit_rate'] is None
               and row['reason'] == 'NO_MATCHING_HISTORICAL_ROWS' for row in samples.values())
    own = result['by_option']['TESTONLY-own-over']
    assert own['historical_roi'] is None and own['whole_ticket_historical_probability'] is None


def test_both_requested_teams_with_known_results_keep_complete_coverage():
    result, samples = _evaluate([_row(1, '10', '20')])
    assert result['status'] == result['coverage_status'] == 'AVAILABLE'
    assert all(row['coverage_status'] == 'AVAILABLE' and row['reason'] is None
               and row['sample_size'] == row['market_hits'] == 1
               and row['unknown_period_scores'] == 0 for row in samples.values())
    assert result['original_strategy_decision_history'] is False


def test_unknown_period_scores_remain_partial_with_original_measured_subset():
    result, samples = _evaluate([_row(1, '10', '20'), _row(2, '10', '20', known=False)])
    assert result['status'] == 'AVAILABLE' and result['coverage_status'] == 'PARTIAL'
    for row in samples.values():
        assert (row['matching_row_count'], row['sample_size'], row['market_hits'],
                row['unknown_period_scores']) == (2, 1, 1, 1)
        assert row['coverage_status'] == 'PARTIAL'
        assert row['reason'] == 'SOME_MATCHING_PERIOD_RESULTS_UNKNOWN'
        assert row['descriptive_hit_rate'] == 1.0
