"""Physical SQLite/raw/receipt -> real SHADOW model boundary, no API mocks as data."""
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from goal_analysis.jobs.native_history_shadow import native_history_view, replay_native_history
from goal_analysis.normalization.training_datasets import validate
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.temporal import TemporalStore

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
OBSERVED = NOW - timedelta(hours=1)


def raw_match(identifier='mt_1', **changes):
    return {'id': identifier, 'competition_id': 'comp_1', 'season_id': 'sn_1',
        'utc_date': (NOW - timedelta(days=5)).isoformat(), 'status': 'finished',
        'home_team': {'id': 'tm_1', 'name': 'H'}, 'away_team': {'id': 'tm_2', 'name': 'A'},
        'is_neutral': False, 'score': {'regulation': {'home': 2, 'away': 1}}, **changes}


def put(database, root, rows, *, comp='comp_1', finished=True, page=1, observed=OBSERVED):
    query = f'competition_id={comp}&date_from=2026-01-01&date_to=2026-10-22&page={page}&per_page=100&utc_offset=%2B00%3A00'
    if finished:
        query += '&status=finished'
    value = {'data': rows, 'meta': {'page': page, 'per_page': 100, 'total_pages': page if rows else 0}}
    source = SnapshotStore(database, root / 'data/raw').save('the_stats_api', 'football_matches',
        json.dumps(value).encode(), 'application/json', observed,
        'https://api.thestatsapi.com/api/football/matches?' + query)
    return {'content_hash': source.content_hash, 'source_url': source.source_url}


def setup(tmp_path, *, witness=True, history=None):
    database = Database(tmp_path / 'data/goal_analysis.sqlite3')
    database.initialize()
    target = raw_match('mt_900', status='scheduled', utc_date=(NOW + timedelta(hours=5)).isoformat())
    source = put(database, tmp_path, [target], finished=False)
    fixture = {'fixture_id': 'the_stats_api:mt_900', 'source_provider': 'the_stats_api',
        'provider_fixture_id': 'mt_900', 'provider_league_id': 'comp_1', 'provider_season_id': 'sn_1',
        'provider_home_team_id': 'tm_1', 'provider_away_team_id': 'tm_2',
        'kickoff': target['utc_date'], 'provider_status': 'NS', 'neutral_venue': False,
        'fixture_source': source}
    put(database, tmp_path, [raw_match()] if history is None else history)
    temporal = TemporalStore(database, tmp_path / 'data/raw', clock=lambda: NOW)
    temporal.initialize()
    if witness:
        temporal.capture('raw')
    return database, temporal, fixture


def test_readonly_deterministic_detached_input_dataset_and_actual_model_call(tmp_path):
    database, _, fixture = setup(tmp_path)
    from contextlib import closing

    # Flush setup WAL before asserting physical read-only behavior. Model imports
    # may collect setup's legacy context-manager connections and checkpoint it.
    with closing(database.connect()) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    file = tmp_path / 'data/goal_analysis.sqlite3'
    before = file.read_bytes(), deepcopy(fixture)
    view = native_history_view(tmp_path, [fixture], as_of=NOW)
    assert view['verified_target_count'] == 1 and len(view['history']) == 1
    assert view == native_history_view(tmp_path, [fixture], as_of=NOW)
    assert view['history'][0]['season'] == 'sn_1'
    assert view['history'][0]['available_at'] == NOW.isoformat(timespec='microseconds')
    validate(view['dataset'])
    result = replay_native_history(tmp_path, [fixture], as_of=NOW)
    assert all(m['status'] == 'INSUFFICIENT_HISTORY' and not m['predictions']
               for m in result['numeric_models'].values())
    assert result['external_requests'] == result['ledger_writes'] == 0
    view['history'][0]['full_time_home'] = 99
    assert native_history_view(tmp_path, [fixture], as_of=NOW)['history'][0]['full_time_home'] == 2
    assert before == (file.read_bytes(), fixture)


def test_absent_or_one_microsecond_late_witness_never_borrowed_as_old_knowledge(tmp_path):
    _, temporal, fixture = setup(tmp_path, witness=False)
    assert not native_history_view(tmp_path, [fixture], as_of=NOW)['fixtures']
    temporal.capture('raw')
    old = native_history_view(tmp_path, [fixture], as_of=NOW-timedelta(microseconds=1))
    assert not old['fixtures'] and not old['history']
    assert len(native_history_view(tmp_path, [fixture], as_of=NOW)['history']) == 1


@pytest.mark.parametrize('field,value', [
    ('provider_home_team_id', 'tm_9'), ('provider_away_team_id', 'tm_9'),
    ('provider_league_id', 'comp_9'), ('provider_season_id', 'sn_9'),
    ('kickoff', (NOW+timedelta(hours=6)).isoformat()), ('neutral_venue', True),
    ('neutral_venue', None), ('provider_status', 'FT'), ('provider_fixture_id', 'mt_2'),
])
def test_target_requires_exact_native_raw_identity_season_venue_and_status(tmp_path, field, value):
    _, _, fixture = setup(tmp_path)
    fixture[field] = value
    view = native_history_view(tmp_path, [fixture], as_of=NOW)
    assert not view['fixtures'] and not view['history']


def test_duplicate_and_malformed_targets_are_gaps_not_fallback_by_names(tmp_path):
    _, _, fixture = setup(tmp_path)
    assert not native_history_view(tmp_path, [fixture, fixture], as_of=NOW)['fixtures']
    assert not native_history_view(tmp_path, [None, {}, {'fixture_id': 'H-A'}], as_of=NOW)['fixtures']
    fixture['fixture_source']['source_url'] += '&forged=1'
    assert not native_history_view(tmp_path, [fixture], as_of=NOW)['fixtures']


@pytest.mark.parametrize('minutes,visible', [(15, False), (15+1/60, True), (0, False)])
def test_strict_fifteen_minute_boundary(tmp_path, minutes, visible):
    _, _, fixture = setup(tmp_path)
    # Target is five hours away; move analysis clock to the exact boundary.
    cutoff = NOW + timedelta(hours=5) - timedelta(minutes=minutes)
    assert bool(native_history_view(tmp_path, [fixture], as_of=cutoff)['fixtures']) is visible


@pytest.mark.parametrize('changes', [
    {'competition_id': 'comp_2'}, {'season_id': None}, {'is_neutral': None},
    {'status': 'live'}, {'utc_date': (NOW-timedelta(hours=2)).isoformat()},
    {'score': {'regulation': {'home': True, 'away': 1}}},
    {'score': {'aggregate': {'home': 2, 'away': 1}}},
    {'score': {'regulation': {'home': 51, 'away': 1}}},
])
def test_unproven_scope_neutral_time_or_nonregulation_score_never_enters_fit(tmp_path, changes):
    _, _, fixture = setup(tmp_path, history=[raw_match(**changes)])
    assert not native_history_view(tmp_path, [fixture], as_of=NOW)['history']


def test_unknown_or_conflicting_version_quarantines_known_row_and_future_does_not(tmp_path):
    database, temporal, fixture = setup(tmp_path)
    put(database, tmp_path, [raw_match(is_neutral=None)], page=2)
    temporal.capture('raw')
    assert not native_history_view(tmp_path, [fixture], as_of=NOW)['history']
    assert native_history_view(tmp_path, [fixture], as_of=NOW)['selection'][
        'unknown_eligible_history_ids_quarantined'] == ['mt_1']


def test_score_conflict_excluded_identical_duplicate_not_double_counted_and_other_season_allowed(tmp_path):
    database, temporal, fixture = setup(tmp_path)
    put(database, tmp_path, [raw_match(), raw_match('mt_2', season_id='sn_2')], page=2)
    temporal.capture('raw')
    view = native_history_view(tmp_path, [fixture], as_of=NOW)
    assert {r['fixture_id'] for r in view['history']} == {'mt_1', 'mt_2'}
    assert view['selection']['selected_history_rows'] == 2
    put(database, tmp_path, [raw_match(score={'regulation': {'home': 3, 'away': 1}})], page=3)
    temporal.capture('raw')
    assert {r['fixture_id'] for r in native_history_view(tmp_path, [fixture], as_of=NOW)['history']} == {'mt_2'}


def test_future_unknown_cannot_quarantine_and_target_id_never_in_history(tmp_path):
    database, _, fixture = setup(tmp_path, history=[raw_match(), raw_match('mt_900')])
    put(database, tmp_path, [raw_match(is_neutral=None)], page=2, observed=NOW+timedelta(seconds=1))
    # Witness clock must also be honest/later for this later source.
    TemporalStore(database, tmp_path / 'data/raw', clock=lambda: NOW+timedelta(seconds=2)).capture('raw')
    view = native_history_view(tmp_path, [fixture], as_of=NOW)
    assert {r['fixture_id'] for r in view['history']} == {'mt_1'}
    assert view['gaps']['TARGET_HISTORY_EXCLUDED'] == 1


def test_raw_bytes_cannot_borrow_good_receipt_and_reader_does_not_create_db(tmp_path):
    assert replay_native_history(tmp_path, [], as_of=NOW)['source_view']['status'] == 'MISSING_SOURCE'
    assert not (tmp_path/'data').exists()
    database, _, fixture = setup(tmp_path)
    with database.connect() as db:
        row = db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?',
                         (fixture['fixture_source']['content_hash'],)).fetchone()
    (tmp_path/'data/raw'/row['relative_path']).write_bytes(b'{}')
    assert not native_history_view(tmp_path, [fixture], as_of=NOW)['fixtures']


def test_limits_fail_closed_without_selection_biased_by_partial_scan(tmp_path, monkeypatch):
    database, temporal, fixture = setup(tmp_path)
    import goal_analysis.jobs.native_history_shadow as module

    monkeypatch.setattr(module, 'MAX_ROWS', 0)
    view = native_history_view(tmp_path, [fixture], as_of=NOW)
    assert view['status'] == 'LIMIT_REACHED' and not view['history']
    monkeypatch.setattr(module, 'MAX_METADATA', 1)
    put(database, tmp_path, [raw_match('mt_2')], page=2)
    temporal.capture('raw')
    assert native_history_view(tmp_path, [fixture], as_of=NOW)['status'] == 'LIMIT_REACHED'


def test_foreign_league_metadata_cannot_starve_current_competition_limit(tmp_path, monkeypatch):
    database, temporal, fixture = setup(tmp_path)
    import goal_analysis.jobs.native_history_shadow as module

    for i in range(5):
        put(database, tmp_path, [raw_match(f'mt_{i+20}', competition_id='comp_2')], comp='comp_2', page=i+1)
    temporal.capture('raw')
    monkeypatch.setattr(module, 'MAX_METADATA', 1)
    assert len(native_history_view(tmp_path, [fixture], as_of=NOW)['history']) == 1


def test_late_backfill_is_used_for_current_fit_but_kepler_earlier_features_withheld(tmp_path, monkeypatch):
    _, _, fixture = setup(tmp_path, history=[raw_match(), raw_match('mt_2',
        utc_date=(NOW-timedelta(days=3)).isoformat())])
    predictor = Mock(return_value={'gauss': {'predictions': []}, 'laplace': {'predictions': []}})
    monkeypatch.setattr('goal_analysis.models.predict_models', predictor)
    result = replay_native_history(tmp_path, [fixture], as_of=NOW)
    assert result['numeric_models']['kepler']['status'] == 'UNPROVEN_POINT_IN_TIME_HISTORY'
    args, kwargs = predictor.call_args
    assert kwargs['enabled_profiles'] == ('gauss', 'laplace') and len(args[1]) == 2
    assert not result['historical_calibration_certified'] and not result['live_promotion']


@pytest.mark.parametrize('enabled,fail', [(False, False), (True, False), (True, True)])
def test_coordinator_default_off_and_failures_never_veto_or_replace_cards(tmp_path, monkeypatch, enabled, fail):
    from test_model_integration import predictor_fixture
    from test_portfolio_flow import NOW as FLOW_NOW
    from test_portfolio_flow import RecordedReviewer
    from test_portfolio_flow import environment as flow_environment

    from goal_analysis.jobs import native_history_shadow
    from goal_analysis.jobs.portfolio import run_portfolio

    calls = []
    def inspect(*args, **kwargs):
        calls.append((args, kwargs))
        if fail:
            raise ValueError('Synthetic optional reader failure')
        # Deliberately contrary hypothetical output may not enter live models.
        return {'mode': 'SHADOW', 'decision_effect': 'NONE', 'numeric_models': {'gauss': 'TEST_ONLY'}}
    monkeypatch.setattr(native_history_shadow, 'replay_native_history', inspect)
    def run(path, flag):
        cfg, football, odds, cache, _, _, _ = flow_environment(path)
        return run_portfolio(cfg, {}, football, odds, cache, FLOW_NOW.date(),
            RecordedReviewer(), clock=lambda: FLOW_NOW, model_predictor=predictor_fixture,
            native_history_shadow_root=tmp_path if flag else None)['artifacts']
    control, actual = run(tmp_path/'control', False), run(tmp_path/'actual', enabled)
    assert control['numeric_models'] == actual['numeric_models']
    for group in ('tickets', 'recommendations', 'profile_proposals', 'preview_tickets'):
        assert control['report'].get(group) == actual['report'].get(group)
    assert len(calls) == int(enabled)
    if enabled:
        assert actual['report']['native_history_shadow']['decision_effect'] == 'NONE'
    else:
        assert 'native_history_shadow' not in actual['report']
