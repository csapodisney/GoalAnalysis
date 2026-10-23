from datetime import UTC, datetime

from goal_analysis.jobs import weather_market_readiness as readiness


def test_empty_real_source_is_pending_and_does_not_create_database(tmp_path):
    result = readiness.inspect_weather_market_readiness(tmp_path, as_of=datetime(2026, 10, 22, tzinfo=UTC))
    assert len(result['markets']) == 6
    assert all(row['result_targets_available'] == 0 and row['heldout_market_validation_complete'] is False
               for row in result['markets'])
    assert result['production_writes'] == result['external_requests'] == 0
    assert list(tmp_path.iterdir()) == []


def test_full_time_targets_do_not_turn_missing_half_or_forecasts_into_validation(tmp_path, monkeypatch):
    match = {'result_period': 'FULL_TIME_90_MINUTES', 'status': 'FT', 'home_goals': 2, 'away_goals': 1,
             'half_home_goals': None, 'half_away_goals': None}
    monkeypatch.setattr(readiness, 'load_native_weather_dataset', lambda *a, **k: {
        'matches': [match], 'as_of': '2026-10-22T00:00:00+00:00', 'coverage': {},
        'gaps': {}, 'dataset_manifest': {'dataset_id': 'synthetic-source-only'}})
    value = readiness.inspect_weather_market_readiness(tmp_path, as_of=datetime(2026, 10, 22, tzinfo=UTC))
    by_family = {row['family']: row for row in value['markets']}
    assert by_family['BTTS_FT']['result_targets_available'] == 1
    assert by_family['1X2_FT']['result_targets_available'] == 1
    assert all(row['result_targets_available'] == 0 for family, row in by_family.items() if family.endswith('H1'))
    assert all(row['result_reanalysis_prematch_forecast_pairs'] == 0 and row['live_weight_eligible'] is False
               for row in value['markets'])
