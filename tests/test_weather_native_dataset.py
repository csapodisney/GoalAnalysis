"""Native source/receipt/weather boundaries, never synthetic sports performance."""
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents import weather_native_dataset as native
from goal_analysis.storage import Database
from goal_analysis.storage.historical_results import HistoricalResults
from goal_analysis.storage.sports_weather import WeatherStore
from goal_analysis.storage.temporal import TemporalStore

KICKOFF = datetime(2026, 10, 21, 15, tzinfo=UTC)
OBSERVED = KICKOFF + timedelta(hours=3)
NOW = KICKOFF + timedelta(days=1)


def stamp(value):
    return value.isoformat(timespec='microseconds')


def record(number=1, **changes):
    return {'provider': 'the_stats_api', 'provider_id': f'mt_{number}',
            'competition_id': 'comp_1', 'season_id': 'sn_1', 'home_id': 'tm_1', 'away_id': 'tm_2',
            'home_team': 'Home FC', 'away_team': 'Away FC', 'kickoff': stamp(KICKOFF),
            'match_date': KICKOFF.date().isoformat(), 'kickoff_local_time': None,
            'status': 'finished', 'score_scope': 'REGULATION', 'home_goals': 2, 'away_goals': 1,
            'half_home_goals': None, 'half_away_goals': None,
            'observed_at': stamp(OBSERVED), 'source_url': 'https://api.thestatsapi.com/api/football/matches',
            'source_sha256': 'a' * 64, 'raw': {'id': f'mt_{number}'}, **changes}


@pytest.fixture
def storage(tmp_path):
    db = Database(tmp_path / 'data/goal_analysis.sqlite3')
    db.initialize()
    results = HistoricalResults(db, clock=lambda: NOW)
    results.initialize()
    weather = WeatherStore(db)
    weather.initialize()
    clock = {'now': NOW}
    temporal = TemporalStore(db, tmp_path / 'data/raw', clock=lambda: clock['now'])
    temporal.initialize()
    return tmp_path, db, results, weather, temporal, clock


def add_weather(storage, *, kind='FORECAST', observed=None, identity='the_stats_api:mt_1',
                kickoff=KICKOFF, target=KICKOFF, temperature=10, status='SOURCED', **changes):
    observed = observed or (KICKOFF - timedelta(hours=1) if kind == 'FORECAST' else OBSERVED)
    forecast = {'fixture_id': identity, 'provider': 'open_meteo', 'weather_kind': kind,
        'status': status, 'observed_at': stamp(observed), 'forecast_time': stamp(target),
        'temperature_c': temperature, 'wind_speed_kmh': 12, 'precipitation_mm': .2,
        'relative_humidity_pct': 70, 'source_url': 'https://api.open-meteo.com/v1/forecast', **changes}
    return storage[3].ingest(forecast, {'fixture_id': identity, 'kickoff': stamp(kickoff)},
        source_run_id='synthetic-source', raw_payload={'retained': True})


def load(storage, cutoff=NOW):
    return native.load_native_weather_dataset(storage[0], as_of=cutoff)


def test_readonly_native_pair_and_dataset_identity(storage):
    # Forecast must actually have a witness before kickoff, not just a claimed timestamp.
    storage[5]['now'] = KICKOFF - timedelta(minutes=30)
    add_weather(storage)
    assert storage[4].capture('weather')['created'] == 1
    storage[5]['now'] = NOW
    storage[2].ingest([record()])
    storage[4].capture('result')
    storage[4].capture('weather')
    before = storage[1].path.read_bytes()
    view = load(storage)
    assert view == load(storage) and storage[1].path.read_bytes() == before
    match = view['matches'][0]
    assert match['fixture_id'] == 'the_stats_api:mt_1'
    assert match['league_id'] == 'the_stats_api:comp_1' and match['home_team_id'] == 'the_stats_api:tm_1'
    assert match['half_home_goals'] is match['half_away_goals'] is None
    assert view['coverage']['matches_with_both'] == 0
    assert match['prematch_weather']['available_at'] == stamp(KICKOFF - timedelta(minutes=30))
    assert 'weather' not in match
    assert view['mode'] == 'SHADOW' and view['effective_data_path'] == 'LEGACY_UNCHANGED'
    assert view['external_requests'] == view['production_writes'] == 0
    assert view['live_decision_effect'] is False


def test_backfilled_forecast_is_not_historical_prematch_knowledge(storage):
    storage[2].ingest([record()])
    add_weather(storage)
    storage[4].capture('result')
    storage[4].capture('weather')
    value = load(storage)
    assert value['coverage']['completed_matches'] == 1
    assert value['coverage']['matches_with_prematch_forecast'] == 0
    assert any(key.startswith('WEATHER:') for key in value['gaps'])


def test_result_requires_its_actual_availability_and_does_not_create_receipt(storage):
    storage[2].ingest([record()])
    assert load(storage)['matches'] == []
    storage[4].capture('result')
    assert load(storage, NOW - timedelta(microseconds=1))['matches'] == []
    assert len(load(storage)['matches']) == 1


@pytest.mark.parametrize('change', [
    {'competition_id': '39'}, {'home_id': 'api_football:1'}, {'provider_id': '1'},
    {'score_scope': 'SOURCE_FULL_TIME_UNVERIFIED'}, {'home_goals': None, 'away_goals': None},
    {'status': 'AET'},
])
def test_unproven_scope_or_non_native_identity_cannot_enter(storage, change):
    storage[2].ingest([record(**change)])
    storage[4].capture('result')
    assert load(storage)['matches'] == []


def test_exact_half_scores_can_be_retained_but_are_never_inferred(storage):
    storage[2].ingest([record(), record(2, half_home_goals=1, half_away_goals=0)])
    storage[4].capture('result')
    rows = {row['fixture_id']: row for row in load(storage)['matches']}
    assert rows['the_stats_api:mt_1']['half_home_goals'] is None
    assert rows['the_stats_api:mt_2']['half_home_goals'] == 1


def test_ambiguous_same_time_result_does_not_choose_by_score(storage):
    storage[2].ingest([record(), record(home_goals=3)])
    storage[4].capture('result')
    view = load(storage)
    assert view['matches'] == [] and view['gaps']['RESULT:CONFLICTING_LATEST_RESULT'] == 1


def test_later_incomplete_result_does_not_fall_back_to_old_complete_score(storage):
    storage[2].ingest([record(), record(home_goals=None, away_goals=None,
                                      observed_at=stamp(OBSERVED + timedelta(hours=1)))])
    storage[4].capture('result')
    assert load(storage)['matches'] == []


@pytest.mark.parametrize('weather_changes', [
    {'identity': 'api_football:mt_1'}, {'identity': 'Home FC - Away FC'},
    {'kickoff': KICKOFF + timedelta(minutes=30)}, {'target': KICKOFF + timedelta(hours=2)},
    {'status': 'UNKNOWN'},
])
def test_no_fuzzy_cross_provider_time_or_value_join(storage, weather_changes):
    storage[2].ingest([record()])
    add_weather(storage, kind='REANALYSIS', **weather_changes)
    storage[4].capture('result')
    storage[4].capture('weather')
    assert load(storage)['coverage']['matches_with_reanalysis'] == 0


def test_partial_weather_is_retained_with_unknown_value_not_imputed(storage):
    storage[5]['now'] = KICKOFF - timedelta(minutes=30)
    add_weather(storage, temperature=None)
    storage[4].capture('weather')
    storage[5]['now'] = NOW
    storage[2].ingest([record()])
    storage[4].capture('result')
    value = load(storage)
    assert value['matches'][0]['prematch_weather']['temperature_c'] is None
    assert value['live_decision_effect'] is False


@pytest.mark.parametrize('delta,expected', [(-1, 1), (0, 1), (1, 0)])
def test_forecast_availability_boundary_is_exactly_fifteen_minutes(storage, delta, expected):
    storage[5]['now'] = KICKOFF - timedelta(minutes=15) + timedelta(microseconds=delta)
    add_weather(storage)
    storage[4].capture('weather')
    storage[5]['now'] = NOW
    storage[2].ingest([record()])
    storage[4].capture('result')
    assert load(storage)['coverage']['matches_with_prematch_forecast'] == expected


@pytest.mark.parametrize('field,value', [('receipt_id', 'bad-id'), ('observed_at', '2000-01-01T00:00:00+00:00')])
def test_tampered_receipt_metadata_cannot_certify_source(storage, field, value):
    storage[2].ingest([record()])
    storage[4].capture('result')
    with closing(sqlite3.connect(storage[1].path)) as db:
        db.execute('DROP TRIGGER temporal_receipts_no_update')
        db.execute(f'UPDATE temporal_receipts SET {field}=?', (value,))
        db.commit()
    assert load(storage)['matches'] == []


def test_same_time_conflicting_weather_is_not_cherry_picked(storage):
    storage[5]['now'] = KICKOFF - timedelta(minutes=30)
    add_weather(storage, temperature=10)
    add_weather(storage, temperature=30)
    storage[4].capture('weather')
    storage[5]['now'] = NOW
    storage[2].ingest([record()])
    storage[4].capture('result')
    storage[4].capture('weather')
    view = load(storage)
    assert view['coverage']['matches_with_prematch_forecast'] == 0
    assert view['gaps']['WEATHER:CONFLICTING_LATEST_WEATHER'] == 1


def test_source_corruption_is_not_hidden_by_receipt(storage):
    storage[2].ingest([record()])
    storage[4].capture('result')
    with closing(sqlite3.connect(storage[1].path)) as db:
        db.execute('DROP TRIGGER historical_result_versions_no_update')
        db.execute("UPDATE historical_result_versions SET record_json=replace(record_json,'Home FC','Changed')")
        db.commit()
    assert load(storage)['matches'] == []


def test_resource_limit_fails_explicitly_without_silent_partial_history(storage, monkeypatch):
    storage[2].ingest([record(), record(2)])
    storage[4].capture('result')
    monkeypatch.setattr(native, 'MAX_VERSIONS', 1)
    with pytest.raises(ValueError, match='BUDGET_EXCEEDED'):
        load(storage)


def test_missing_database_stays_missing_and_naive_clock_rejected(tmp_path):
    assert native.load_native_weather_dataset(tmp_path, as_of=NOW)['matches'] == []
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError):
        native.load_native_weather_dataset(tmp_path, as_of=NOW.replace(tzinfo=None))
