"""Chronology, deduplication and physical source binding, never profit evidence."""
import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs.calibration_shadow import (
    admitted_forecasts,
    archive_calibration,
    inspect_calibration,
)
from goal_analysis.jobs.the_stats_live import normalize_history
from goal_analysis.jobs.the_stats_results import _result
from goal_analysis.learning.calibration import GROUP, POLICY, shadow_probability, walk_forward
from goal_analysis.models._data import prepare_history
from goal_analysis.models.datasets import lineage
from goal_analysis.normalization.training_datasets import sha, snapshot, validate
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.portfolio_ledger import _hash
from goal_analysis.storage.temporal import TemporalStore
from goal_analysis.storage.training_datasets import DatasetStore

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
SMALL = {**POLICY, 'min_train_fixtures': 4, 'min_train_days': 2,
         'min_eval_fixtures': 2, 'min_eval_days': 1, 'fold_count': 2,
         'fold_days': 2, 'embargo_hours': 1}


def row(index, day, p=None, y=None):
    saved = NOW.replace(hour=10) - timedelta(days=day)
    kickoff = saved + timedelta(hours=6)
    outcome = index % 2 if y is None else y
    return {**dict(zip(GROUP, ('expert', 'fixed-v1', 'gauss-v1', 'totals_2_5',
        'over', 'FULL_TIME', 'REGULATION_TIME', '2.5'), strict=True)),
        'forecast_id': sha(['forecast', index]), 'run_sha256': sha(['run', index]),
        'fixture_id': 'the_stats_api:mt_' + str(index), 'probability': .9 if p is None else p,
        'probability_kind': 'ORIGINAL_NUMERIC_MODEL_ESTIMATE',
        'kickoff': kickoff.isoformat(), 'model_as_of': (saved-timedelta(minutes=2)).isoformat(),
        'created_at': (saved-timedelta(minutes=1)).isoformat(), 'saved_at': saved.isoformat(),
        'dataset_ids': [sha(['dataset', index])], 'source_receipt_ids': [sha(['source', index])],
        'result_receipts': [{'outcome': outcome, 'receipt_id': sha(['label', index]),
            'source_sha256': sha(['result', index]), 'observed_at': (kickoff+timedelta(hours=2)).isoformat(),
            'available_at': (kickoff+timedelta(hours=3)).isoformat()}]}


def corpus():
    return [row(100 + i, 10 - i//2) for i in range(20)]


def data(rows, as_of=NOW):
    return snapshot(rows, role='admitted_calibration_forecasts', as_of=as_of,
                    recipe={'test': 'CONTROLLED_NOT_REAL_FOOTBALL_EVIDENCE'}, lineage={})


def fitted(rows=None):
    return walk_forward(data(corpus() if rows is None else rows), as_of=NOW, policy=SMALL)


def test_real_fit_and_untouched_holdout_scores_deterministic_and_versioned():
    original = corpus(); before = deepcopy(original)
    result = fitted(original)
    assert result == fitted(original)
    reordered = fitted(list(reversed(original)))['groups'][0]
    assert reordered['calibrated_scores'] == result['groups'][0]['calibrated_scores']
    assert [f['parameters'] for f in reordered['folds']] == [f['parameters'] for f in result['groups'][0]['folds']]
    group = result['groups'][0]
    assert group['status'] == 'CANDIDATE_SHADOW'
    assert group['calibrated_scores']['brier'] < group['original_scores']['brier']
    assert group['calibrated_scores']['log_loss'] < group['original_scores']['log_loss']
    assert result['live_decision_effect'] is result['parent_30_day_gate_complete'] is False
    assert original == before
    assert result['report_sha256'] == sha({k: v for k, v in result.items() if k != 'report_sha256'})
    for fold in group['folds']:
        train = validate(fold['training_dataset'])
        assert {r['fixture_id'] for r in train['rows']}.isdisjoint(r['fixture_id'] for r in fold['predictions'])
        assert all(datetime.fromisoformat(r['label_available_at']) <= datetime.fromisoformat(
            fold['training_labels_available_by']) for r in train['rows'])


def test_holdout_labels_do_not_select_first_fold_parameters():
    original = corpus(); first = fitted(original)['groups'][0]['folds'][0]
    changed = deepcopy(original)
    for r in changed:
        if datetime.fromisoformat(r['saved_at']) >= datetime.fromisoformat(first['start']):
            r['result_receipts'][0]['outcome'] = 1-r['result_receipts'][0]['outcome']
    second = fitted(changed)['groups'][0]['folds'][0]
    assert first['parameters'] == second['parameters']
    assert first['training_dataset']['manifest']['rows_sha256'] == second['training_dataset']['manifest']['rows_sha256']


def test_later_result_corrections_not_leaked_into_prior_training_or_persisted_train_rows():
    original = corpus(); changed = deepcopy(original)
    for r in changed:
        r['result_receipts'].append({**r['result_receipts'][0],
            'receipt_id': sha(['correction', r['forecast_id']]), 'outcome': 1-r['result_receipts'][0]['outcome'],
            'observed_at': (NOW-timedelta(hours=2)).isoformat(), 'available_at': (NOW-timedelta(hours=1)).isoformat()})
    a = fitted(original)['groups'][0]['folds'][0]; b = fitted(changed)['groups'][0]['folds'][0]
    assert a['parameters'] == b['parameters']
    assert all(len(r['result_receipts']) == 1 for r in b['training_dataset']['rows'])
    assert a['training_dataset']['manifest']['rows_sha256'] == b['training_dataset']['manifest']['rows_sha256']


def test_delayed_unknown_or_same_time_conflicting_labels_are_not_training_losses():
    rows = corpus()
    for r in rows:
        if datetime.fromisoformat(r['saved_at']) < NOW-timedelta(days=4):
            r['result_receipts'][0]['available_at'] = NOW.isoformat()
    assert all(f['status'] == 'NOT_FIT' for f in fitted(rows)['groups'][0]['folds'])
    rows = corpus()
    for r in rows:
        r['result_receipts'].append({**r['result_receipts'][0], 'outcome': 1-r['result_receipts'][0]['outcome'],
                                    'receipt_id': sha(['opposite', r['forecast_id']])})
    assert fitted(rows)['groups'][0]['status'] == 'NOT_FIT'


def test_duplicate_cards_and_later_forecasts_not_extra_independent_samples():
    rows = corpus(); original = fitted(rows)
    for r in list(rows):
        rows.append({**r, 'forecast_id': sha(['duplicate', r['forecast_id']])})
        rows.append({**r, 'saved_at': (datetime.fromisoformat(r['saved_at'])+timedelta(minutes=1)).isoformat(),
                     'forecast_id': sha(['later', r['forecast_id']]), 'probability': .1})
    result = fitted(rows)
    assert result['accepted_unique_forecasts'] == original['accepted_unique_forecasts']
    assert result['groups'][0]['calibrated_scores'] == original['groups'][0]['calibrated_scores']


def test_same_time_conflicting_first_forecasts_quarantined():
    rows = corpus(); duplicate = deepcopy(rows[0]); duplicate['probability'] = .1
    duplicate['forecast_id'] = sha('conflicting')
    result = fitted(rows+[duplicate])
    assert result['excluded']['CONFLICTING_FIRST_FORECASTS'] == 1


def test_missing_first_probability_cannot_be_replaced_by_good_later_forecast():
    original = row(1, 8); original['probability'] = None
    later = {**original, 'probability': .8, 'saved_at': (datetime.fromisoformat(original['saved_at'])+timedelta(minutes=1)).isoformat()}
    result = fitted([original, later])
    assert result['accepted_unique_forecasts'] == 0


@pytest.mark.parametrize('field,value', [
    ('probability', True), ('probability', None), ('probability', 1.1),
    ('probability_kind', 'HEURISTIC'), ('dataset_ids', []), ('source_receipt_ids', []),
    ('model_version', 'UNKNOWN_LEGACY'), ('strategy_version', 'UNKNOWN'), ('fixture_id', 'api_football:1'),
    ('run_sha256', 'unproven'), ('saved_at', '2026-10-10T10:00:00'),
    ('model_as_of', '2027-01-01T10:00:00+00:00')])
def test_invalid_provenance_or_score_never_fits(field, value):
    r = row(1, 8); r[field] = value
    assert fitted([r])['accepted_unique_forecasts'] == 0


@pytest.mark.parametrize('delta, accepted', [(0, False), (1, True), (-1, False)])
def test_saved_before_strict_fifteen_minute_lockout(delta, accepted):
    r = row(1, 8)
    r['saved_at'] = (datetime.fromisoformat(r['kickoff'])-timedelta(minutes=15, microseconds=delta)).isoformat()
    assert bool(fitted([r])['accepted_unique_forecasts']) is accepted


def test_timezone_normalized_original_order_and_utc_folds():
    r = row(1, 8); earlier = deepcopy(r)
    earlier['saved_at'] = (datetime.fromisoformat(r['saved_at'])-timedelta(minutes=1)).astimezone(
        __import__('datetime').timezone(timedelta(hours=10))).isoformat()
    earlier['probability'] = None
    assert fitted([r, earlier])['accepted_unique_forecasts'] == 0


def test_profiles_versions_markets_and_sides_are_distinct_cohorts():
    a = row(1, 8); b = {**a, 'model_version': 'laplace-v1'}; c = {**a, 'profile_id': 'other'}
    d = {**a, 'strategy_version': 'fixed-v2'}; e = {**a, 'selection_key': 'under'}
    result = fitted([a,b,c,d,e])
    assert result['accepted_unique_forecasts'] == len(result['groups']) == 5
    assert all(g['status'] == 'NOT_FIT' and g['candidate'] is None for g in result['groups'])


def test_default_policy_never_lowered_for_small_data_and_one_class_never_fitted():
    assert walk_forward(data(corpus()), as_of=NOW)['groups'][0]['status'] == 'NOT_FIT'
    rows = corpus()
    for r in rows:
        r['result_receipts'][0]['outcome'] = 1
    assert fitted(rows)['groups'][0]['status'] == 'NOT_FIT'


def test_explicit_shadow_application_exact_cohort_clock_and_hash_only():
    candidate = fitted()['groups'][0]['candidate']; r = row(999, -1)
    r['model_as_of'] = NOW.isoformat()
    result = shadow_probability(candidate, r, as_of=NOW)
    assert 0 < result['probability'] < 1 and result['live_decision_effect'] is False
    for change in ({'group': {**candidate['group'], 'strategy_version': 'different'}}, {'live_approved': True}):
        with pytest.raises(ValueError):
            shadow_probability({**candidate, **change}, r, as_of=NOW)
    with pytest.raises(ValueError):
        shadow_probability(candidate, {**r, 'profile_id': 'different'}, as_of=NOW)
    with pytest.raises(ValueError):
        shadow_probability(candidate, {**r, 'kickoff': (NOW+timedelta(minutes=15)).isoformat()}, as_of=NOW)


def test_dataset_tampering_future_asof_and_policy_bounds_rejected():
    current = data(corpus()); current['rows'][0]['probability'] = .1
    with pytest.raises(ValueError):
        walk_forward(current, as_of=NOW)
    with pytest.raises(ValueError):
        walk_forward(data([]), as_of=NOW, policy={**SMALL, 'fold_count': True})
    with pytest.raises(ValueError):
        walk_forward(data([]), as_of=NOW+timedelta(hours=1))


def physical(root):
    """Actual temporary SQLite/raw files/datasets/receipts, controlled forecasts."""
    model_at = NOW-timedelta(days=5)
    observed = model_at-timedelta(hours=1)
    database = Database(root/'data/goal_analysis.sqlite3'); database.initialize()
    def raw(identifier, kickoff, status='finished'):
        return {'id': identifier, 'competition_id': 'comp_1', 'season_id': 'sn_1',
            'utc_date': kickoff.isoformat(), 'status': status, 'is_neutral': False,
            'home_team': {'id': 'tm_1', 'name': 'H'}, 'away_team': {'id': 'tm_2', 'name': 'A'},
            'score': {'regulation': {'home': 2, 'away': 1}, 'half_time_home': 1, 'half_time_away': 0}}
    def save(value, url, when):
        source = SnapshotStore(database, root/'data/raw').save('the_stats_api', 'football_matches',
            json.dumps({'data': value}).encode(), 'application/json', when,
            'https://api.thestatsapi.com/api/football/' + url)
        return {'content_hash': source.content_hash, 'source_url': source.source_url,
                'observed_at': when.isoformat()}
    history = raw('mt_1', model_at-timedelta(days=10))
    history_source = save([history], 'matches?competition_id=comp_1', observed)
    target = raw('mt_900', model_at+timedelta(hours=6), 'scheduled')
    target_source = save([target], 'matches?status=scheduled', observed)
    temporal = TemporalStore(database, root/'data/raw', clock=lambda: model_at-timedelta(minutes=1))
    temporal.initialize(); temporal.capture('raw')
    normalized, _ = prepare_history([normalize_history(history, 'comp_1', history_source, model_at)], model_at, set())
    training = snapshot([[2,1,'tm_1','tm_2']], role='poisson_730', as_of=model_at,
        recipe={'model_version': 'gauss-unit-v1'}, lineage=lineage(normalized))
    DatasetStore(root/'data/arthur/models/datasets').put(training)
    leg = {'fixture_id': 'the_stats_api:mt_900', 'market_key': 'totals_2_5', 'selection_key': 'over',
        'period': 'FULL_TIME', 'settlement': 'REGULATION_TIME', 'line': '2.5',
        'kickoff': target['utc_date'], 'provider_home_team_id': 'tm_1', 'provider_away_team_id': 'tm_2',
        'provider_league_id': 'comp_1', 'provider_season_id': 'sn_1',
        'neutral_venue': False,
        'fixture_source': target_source, 'model_probability': .7,
        'confidence_kind': 'UNCALIBRATED_MODEL_ESTIMATE', 'model_details': {'version': 'gauss-unit-v1'}}
    manifest = {'schema_version': 1, 'source_files': {}}
    manifest['manifest_sha256'] = sha(manifest)
    manifest['profiles'] = {'gauss': {'version_id': 'gauss-fixed-unit-v1'}}
    card = {'recommendation_id': _hash(['controlled-1', 'gauss'])[:24], 'profile_id': 'gauss', 'strategy_version': 'gauss-fixed-unit-v1',
        'strategy_manifest_sha256': manifest['manifest_sha256'], 'created_at': (model_at+timedelta(minutes=1)).isoformat(),
        'legs': [leg]}
    model = {'predictions': [{'fixture_id': leg['fixture_id'], 'probabilities': {'over25': .7},
        'model_details': {'version': 'gauss-unit-v1', 'cutoff': model_at.isoformat()}}],
        'training_datasets': {'archive': {'status': 'ARCHIVED'}, 'manifests': [training['manifest']],
            'uses': [{'dataset_id': training['manifest']['dataset_id'], 'status': 'FIT_CALL_RETURNED'}]}}
    run = {'run_id': 'controlled-1', 'observed_at': model_at.isoformat(), 'finished_at': (model_at+timedelta(minutes=2)).isoformat(),
        'strategy_manifest': manifest, 'numeric_models': {'gauss': model}, 'recommendations': [card]}
    saved = model_at+timedelta(minutes=3)
    path = root/'data/arthur/ledger.sqlite3'; path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''CREATE TABLE portfolio_runs(run_id,saved_at,snapshot_json,snapshot_hash);
            CREATE TABLE portfolio_recommendations(run_id,snapshot_json);
            CREATE TABLE portfolio_results(id,fixture_id,observed_at,payload_json,payload_hash);
            CREATE TABLE portfolio_result_observations(id,fixture_id,result_id,observed_at);''')
        db.execute('INSERT INTO portfolio_runs VALUES(?,?,?,?)', (run['run_id'], saved.isoformat(), json.dumps(run), _hash(run)))
        db.execute('INSERT INTO portfolio_recommendations VALUES(?,?)', (run['run_id'], json.dumps(card)))
        result_raw = {**target, 'status': 'finished'}
        receipt_at = model_at+timedelta(hours=9)
        source = save(result_raw, 'matches/mt_900', receipt_at)
        temporal.clock = lambda: receipt_at+timedelta(minutes=1)
        temporal.capture('raw')
        result = _result(result_raw, source)
        db.execute('INSERT INTO portfolio_results VALUES(1,?,?,?,?)', (leg['fixture_id'],
            receipt_at.isoformat(), json.dumps(result), _hash(result)))
        db.execute('INSERT INTO portfolio_result_observations VALUES(1,?,1,?)', (leg['fixture_id'], receipt_at.isoformat()))
    return path, run, card, database, training


def test_physical_readonly_ledger_raw_receipt_dataset_pair_and_idempotent_candidate_archive(tmp_path):
    path, _, _, _, _ = physical(tmp_path)
    before = path.read_bytes()
    result = inspect_calibration(tmp_path, as_of=NOW, enabled=True, clock=lambda: NOW)
    admission = result['source_admission']
    assert admission['admitted_forecasts'] == admission['inspected_original_legs'] == 1
    assert admission['excluded'] == {}
    actual = admission['dataset']['rows'][0]
    assert actual['result_receipts'][0]['outcome'] == 1
    assert result['status'] == 'NOT_FIT' and result['calibration']['groups'][0]['candidate'] is None
    assert path.read_bytes() == before
    a = archive_calibration(tmp_path, result); b = archive_calibration(tmp_path, result)
    assert a['created'] and not b['created'] and a['dataset_id'] == b['dataset_id']
    assert not a['live_promoted'] and path.read_bytes() == before


@pytest.mark.parametrize('failure', ['dataset_missing', 'dataset_tamper', 'late_witness', 'raw_tamper',
                                    'target_id', 'prediction_probability', 'result_hash', 'result_source',
                                    'heuristic', 'model_version', 'manifest_hash', 'lockout'])
def test_physical_missing_or_corrupt_original_evidence_never_fallbacks(tmp_path, failure):
    path, run, card, database, training = physical(tmp_path)
    if failure.startswith('dataset'):
        file = DatasetStore(tmp_path/'data/arthur/models/datasets').path(training['manifest']['dataset_id'])
        file.unlink() if failure == 'dataset_missing' else file.write_text('{}')
    elif failure in {'late_witness', 'raw_tamper'}:
        with closing(database.connect()) as db:
            raw = dict(db.execute('SELECT * FROM raw_snapshots ORDER BY rowid LIMIT 1').fetchone())
            if failure == 'late_witness':
                db.execute('DROP TRIGGER temporal_receipts_no_update')
                db.execute('UPDATE temporal_receipts SET available_at=? WHERE source_key=?',
                           (NOW.isoformat(), raw['content_hash'])); db.commit()
            else:
                (tmp_path/'data/raw'/raw['relative_path']).write_text('{}')
    elif failure in {'result_hash', 'result_source'}:
        with closing(sqlite3.connect(path)) as db, db:
            payload = json.loads(db.execute('SELECT payload_json FROM portfolio_results').fetchone()[0])
            payload['source']['source_url'] += '&forged=1'
            db.execute('UPDATE portfolio_results SET payload_json=?,payload_hash=?',
                       (json.dumps(payload), _hash(payload) if failure == 'result_source' else 'bad'))
    else:
        leg = card['legs'][0]
        if failure == 'target_id': leg['provider_home_team_id'] = 'tm_99'
        if failure == 'prediction_probability': run['numeric_models']['gauss']['predictions'][0]['probabilities']['over25'] = .3
        if failure == 'heuristic': leg['confidence_kind'] = 'HEURISTIC_NOT_PROBABILITY'
        if failure == 'model_version': leg['model_details']['version'] = 'gauss-wrong-v2'
        if failure == 'manifest_hash': run['strategy_manifest']['source_files']['extra'] = 'bad'
        if failure == 'lockout':
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('UPDATE portfolio_runs SET saved_at=?', ((datetime.fromisoformat(leg['kickoff'])-timedelta(minutes=15)).isoformat(),))
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('UPDATE portfolio_runs SET snapshot_json=?,snapshot_hash=?', (json.dumps(run), _hash(run)))
            db.execute('UPDATE portfolio_recommendations SET snapshot_json=?', (json.dumps(card),))
    result = admitted_forecasts(tmp_path, as_of=NOW)
    assert result['admitted_forecasts'] == 0 and sum(result['excluded'].values()) > 0


def test_disabled_has_no_database_or_file_reads_and_future_cutoff_refused(tmp_path):
    assert inspect_calibration(tmp_path, as_of=NOW, clock=lambda: NOW)['status'] == 'DISABLED'
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError, match='FUTURE'):
        inspect_calibration(tmp_path, as_of=NOW+timedelta(seconds=1), enabled=True, clock=lambda: NOW)


def replace_run(path, run, card):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('UPDATE portfolio_runs SET snapshot_json=?,snapshot_hash=?', (json.dumps(run), _hash(run)))
        db.execute('UPDATE portfolio_recommendations SET snapshot_json=?', (json.dumps(card),))


def test_actual_ledger_generated_id_and_published_time_do_not_hide_original_forecast(tmp_path):
    path, run, card, _, _ = physical(tmp_path)
    original = deepcopy(card)
    original['recommendation_id'] = 'report-own-id'
    original['created_at'] = run['observed_at']
    run['recommendations'] = [original]
    stored = {**card, 'run_id': run['run_id'], 'created_at': run['finished_at']}
    stored['legs'][0]['settlement'] = 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES'
    original['legs'][0]['settlement'] = stored['legs'][0]['settlement']
    # Explicit totals_2_5 already identifies the exact 2.5 line.
    stored['legs'][0].pop('line'); original['legs'][0].pop('line')
    replace_run(path, run, stored)
    actual = admitted_forecasts(tmp_path, as_of=NOW)
    assert actual['admitted_forecasts'] == 1
    assert actual['dataset']['rows'][0]['line'] == '2.5'
    stored['legs'][0]['selection_key'] = 'under'
    replace_run(path, run, stored)
    assert admitted_forecasts(tmp_path, as_of=NOW)['admitted_forecasts'] == 0


def test_shared_numeric_components_need_both_original_predictions_and_datasets(tmp_path):
    path, run, card, _, training = physical(tmp_path)
    second = snapshot(training['rows'], role='poisson_730', as_of=training['manifest']['as_of'],
        recipe={'model_version': 'laplace-unit-v1'}, lineage=training['lineage'])
    DatasetStore(tmp_path/'data/arthur/models/datasets').put(second)
    model = deepcopy(run['numeric_models']['gauss'])
    model['predictions'][0]['model_details']['version'] = 'laplace-unit-v1'
    model['training_datasets']['manifests'] = [second['manifest']]
    model['training_datasets']['uses'][0]['dataset_id'] = second['manifest']['dataset_id']
    run['numeric_models']['laplace'] = model
    leg = card['legs'][0]
    leg['confidence_kind'] = 'UNCALIBRATED_SHARED_MODEL_ESTIMATE'
    leg['model_details'] = {'method': 'SHARED_MODEL_WEIGHTED_MEAN',
        'shared_model_probabilities': {'gauss': .7, 'laplace': .7},
        'shared_model_weights': {'gauss': .5, 'laplace': .5}}
    replace_run(path, run, card)
    actual = admitted_forecasts(tmp_path, as_of=NOW)
    assert actual['admitted_forecasts'] == 1
    assert len(actual['dataset']['rows'][0]['dataset_ids']) == 2
    assert actual['dataset']['rows'][0]['model_version'].startswith('shared-')
    run['numeric_models']['laplace']['predictions'][0]['probabilities']['over25'] = .8
    replace_run(path, run, card)
    assert admitted_forecasts(tmp_path, as_of=NOW)['admitted_forecasts'] == 0


def test_first_original_missing_evidence_cannot_be_replaced_by_later_successful_run(tmp_path):
    path, run, card, _, _ = physical(tmp_path)
    later_run = deepcopy(run); later_card = later_run['recommendations'][0]
    later_run['run_id'] = 'controlled-2'
    later_card['recommendation_id'] = _hash(['controlled-2', 'gauss'])[:24]
    run['numeric_models']['gauss']['training_datasets']['archive']['status'] = 'UNAVAILABLE'
    replace_run(path, run, card)
    with closing(sqlite3.connect(path)) as db, db:
        first_saved = datetime.fromisoformat(db.execute('SELECT saved_at FROM portfolio_runs').fetchone()[0])
        db.execute('INSERT INTO portfolio_runs VALUES(?,?,?,?)', (later_run['run_id'],
            (first_saved+timedelta(minutes=1)).isoformat(), json.dumps(later_run), _hash(later_run)))
        db.execute('INSERT INTO portfolio_recommendations VALUES(?,?)', (later_run['run_id'], json.dumps(later_card)))
    result = admitted_forecasts(tmp_path, as_of=NOW)
    assert result['admitted_forecasts'] == 0
    assert result['excluded']['DUPLICATE_OR_LATER_ORIGINAL_FORECAST'] == 1


def test_consumed_goal_rows_cannot_borrow_a_different_raw_lineage(tmp_path):
    path, run, card, _, training = physical(tmp_path)
    changed = snapshot([[0,0,'tm_1','tm_2']], role=training['manifest']['role'],
        as_of=training['manifest']['as_of'], recipe=training['manifest']['recipe'], lineage=training['lineage'])
    DatasetStore(tmp_path/'data/arthur/models/datasets').put(changed)
    captured = run['numeric_models']['gauss']['training_datasets']
    captured['manifests'] = [changed['manifest']]
    captured['uses'][0]['dataset_id'] = changed['manifest']['dataset_id']
    replace_run(path, run, card)
    actual = admitted_forecasts(tmp_path, as_of=NOW)
    assert actual['admitted_forecasts'] == 0
    assert actual['excluded']['CONSUMED_GOAL_ROWS_DIFFER_FROM_RAW_BOUND_LINEAGE'] == 1


def test_distribution_shift_degrades_untouched_holdout_and_cannot_be_released():
    training = [row(1000+i, 12-i//4, y=i%2) for i in range(24)]
    holdout = [row(2000+i, 4-i//10, y=0 if i%10 == 0 else 1) for i in range(40)]
    result = fitted(training+holdout)['groups'][0]
    assert result['status'] == 'REJECTED_SHADOW'
    assert result['calibrated_scores']['brier'] > result['original_scores']['brier']
    assert result['release_allowed'] is False


def test_endpoint_probabilities_keep_finite_explicit_scores():
    rows = corpus()
    for i,r in enumerate(rows): r['probability'] = i%2
    result = fitted(rows)
    assert all(f['original_scores']['log_loss'] == 0 for f in result['groups'][0]['folds'])
    json.dumps(result, allow_nan=False)


def test_unrelated_input_cannot_be_archived_as_another_calibration_report(tmp_path):
    physical(tmp_path)
    report = inspect_calibration(tmp_path, as_of=NOW, enabled=True, clock=lambda: NOW)
    report['source_admission']['dataset'] = data([])
    with pytest.raises(ValueError, match='INPUT_POLICY'):
        archive_calibration(tmp_path, report)


@pytest.mark.parametrize('mutation', ['goals', 'lineage_neutral', 'manifest_count'])
def test_bool_integer_alias_never_passes_original_dataset_binding(tmp_path, mutation):
    path, run, card, _, training = physical(tmp_path)
    changed = deepcopy(training)
    if mutation == 'goals':
        # 1 and True compare equal in Python; their canonical source types differ.
        changed['rows'][0][1] = True
    elif mutation == 'lineage_neutral':
        changed['lineage'][0]['neutral_venue'] = 0
    else:
        run['numeric_models']['gauss']['training_datasets']['manifests'][0]['row_count'] = True
        replace_run(path, run, card)
        assert admitted_forecasts(tmp_path, as_of=NOW)['admitted_forecasts'] == 0
        return
    changed = snapshot(changed['rows'], role=training['manifest']['role'],
        as_of=training['manifest']['as_of'], recipe=training['manifest']['recipe'], lineage=changed['lineage'])
    DatasetStore(tmp_path/'data/arthur/models/datasets').put(changed)
    captured = run['numeric_models']['gauss']['training_datasets']
    captured['manifests'] = [changed['manifest']]
    captured['uses'][0]['dataset_id'] = changed['manifest']['dataset_id']
    replace_run(path, run, card)
    assert admitted_forecasts(tmp_path, as_of=NOW)['admitted_forecasts'] == 0


def test_default_windows_remain_reachable_for_a_single_selection_per_day_profile():
    # Sparse fixed personas must not face an impossible ten-picks/day holdout.
    # This is a controlled chronology test, not historical football evidence.
    rows = [row(10000+i, 150-i) for i in range(150)]
    result = walk_forward(data(rows), as_of=NOW)
    cohort = result['groups'][0]
    assert cohort['status'] == 'CANDIDATE_SHADOW'
    assert len(cohort['folds']) == 3
    assert all(f['evaluation_count'] == 7 for f in cohort['folds'])
    assert cohort['release_allowed'] is False
