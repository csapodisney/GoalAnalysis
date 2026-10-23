"""Physical prospective receipts: no backdating, new model fits or wagers."""
import json
import shutil
import socket
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock

import pytest
from test_calibration_shadow import NOW, physical

from goal_analysis.jobs import daily_native_shadow as daily
from goal_analysis.jobs import native_forecast_capture as module
from goal_analysis.jobs.calibration_shadow import archive_calibration, inspect_calibration
from goal_analysis.learning.calibration_sources import Sources
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha
from goal_analysis.storage.historical_results import HistoricalResults
from goal_analysis.storage.training_datasets import DatasetStore


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), 'utf-8')


def sports_rows(root):
    with closing(sqlite3.connect((root/'data/goal_analysis.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        return tuple(db.iterdump())


def native(root):
    ledger, report, card, database, dataset = physical(root)
    report = deepcopy(report); report['date'] = utc(report['observed_at']).date().isoformat()
    run_id = report['run_id']; model_at = utc(report['observed_at'])
    output = root/'data/arthur/native-shadow'/run_id
    DatasetStore(output/'fit/datasets').put(dataset)
    model = deepcopy(report['numeric_models']['gauss']); model['status'] = 'READY'
    model['predictions'][0]['probabilities'] = {
        'home': .5, 'draw': .2, 'away': .3, 'over25': .7, 'under25': .3, 'btts_yes': .6}
    analysis = {'mode': 'SHADOW', 'live_promotion': False, 'as_of': model_at.isoformat(),
        'numeric_models': {'gauss': model}, 'source_view': {'fixtures': [{**card['legs'][0], 'provider_fixture_id': 'mt_900'}]}}
    summary = {'run_id': run_id, 'mode': 'SHADOW', 'status': 'OBSERVED',
        'live_decision_effect': False, 'as_of': model_at.isoformat(), 'completed_at': report['finished_at'],
        'report_sha256': sha(report), 'analysis_sha256': sha(analysis),
        'version': 'original-worker-v1', 'implementation_sha256': 'a'*64}
    write(output/'analysis.json', analysis); write(output/'summary.json', summary)
    write(output/'settings.json', {'enabled_profiles': ['gauss']})
    write(root/'data/arthur/nightly-staging'/f'{run_id}.json', {'artifacts': {'report': report,
        'fixtures': {'records': []}, 'candidate_input': {'candidates': []}}})
    # Unified native result is already downloaded; capture never fetches it.
    with closing(sqlite3.connect(ledger)) as db:
        payload = json.loads(db.execute('SELECT payload_json FROM portfolio_results').fetchone()[0])
    raw = payload['native_result']; source = payload['source']; score = raw['score']['regulation']
    record = {'provider': 'the_stats_api', 'provider_id': raw['id'], 'competition_id': raw['competition_id'],
        'season_id': raw['season_id'], 'home_id': raw['home_team']['id'], 'away_id': raw['away_team']['id'],
        'home_team': raw['home_team']['name'], 'away_team': raw['away_team']['name'], 'kickoff': raw['utc_date'],
        'match_date': utc(raw['utc_date']).date().isoformat(), 'kickoff_local_time': None,
        'status': 'finished', 'score_scope': 'REGULATION', 'home_goals': score['home'], 'away_goals': score['away'],
        'half_home_goals': None, 'half_away_goals': None, 'observed_at': source['observed_at'],
        'source_url': source['source_url'], 'source_sha256': source['content_hash'], 'raw': raw}
    store = HistoricalResults(database, clock=lambda: utc(source['observed_at'])+timedelta(seconds=5))
    store.initialize(); store.ingest([record])
    return run_id, report, analysis, summary, model_at+timedelta(hours=1), ledger, output


def resave(output, analysis, summary):
    summary['analysis_sha256'] = sha(analysis)
    write(output/'analysis.json', analysis); write(output/'summary.json', summary)


def test_capture_own_committed_receipt_and_all_seven_original_products_without_writes_or_calls(tmp_path, monkeypatch):
    run_id, report, _analysis, _summary, at, ledger, output = native(tmp_path)
    originals = {p: p.read_bytes() for p in [ledger, output/'analysis.json', output/'summary.json',
        output/'settings.json',
        tmp_path/'data/arthur/nightly-staging'/f'{run_id}.json']}
    sports_before = sports_rows(tmp_path)
    forbidden = Mock(side_effect=AssertionError('network/model process forbidden'))
    monkeypatch.setattr(socket, 'socket', forbidden)
    stamps = iter([at, at+timedelta(seconds=1), at+timedelta(seconds=2)])
    captured = module.capture(tmp_path, run_id, sha(report), clock=lambda: next(stamps))
    assert captured['archived_forecasts'] == captured['prospectively_eligible_forecasts'] == 7
    assert captured['available_at'] == (at+timedelta(seconds=2)).isoformat()
    assert captured['external_requests'] == captured['additional_model_fits'] == captured['ledger_writes'] == 0
    result = module.read_forecasts(tmp_path, as_of=NOW)
    assert result['eligible_forecasts'] == result['labeled_forecasts'] == 7 and result['gaps'] == {}
    rows = result['dataset']['rows']
    assert {r['profile_id'] for r in rows} == {'native_shadow_gauss'}
    outcomes = {(r['market_key'],r['selection_key']): r['result_receipts'][0]['outcome'] for r in rows}
    assert outcomes == {('h2h','home'):1, ('h2h','draw'):0, ('h2h','away'):0,
        ('totals_2_5','over'):1, ('totals_2_5','under'):0, ('btts','yes'):1, ('btts','no'):0}
    assert next(r for r in rows if r['selection_key'] == 'no')['probability'] == .4
    for p, body in originals.items(): assert p.read_bytes() == body
    assert sports_rows(tmp_path) == sports_before
    forbidden.assert_not_called()
    before = {p:p.read_bytes() for p in (tmp_path/'data/arthur/native-calibration-capture'/run_id).glob('*.json')}
    repeat = module.capture(tmp_path, run_id, sha(report), clock=lambda: NOW)
    assert repeat['new_forecasts'] == 0 and repeat['receipt_id'] == captured['receipt_id']
    assert {p:p.read_bytes() for p in before} == before


@pytest.mark.parametrize('minute,eligible', [(344,7),(345,0),(346,0),(360,0),(800,0)])
def test_real_capture_clock_controls_strict_fifteen_minute_gate_not_old_summary_or_mtime(tmp_path, minute, eligible):
    run_id, report, _, summary, _, _, _ = native(tmp_path)
    at = utc(summary['as_of'])+timedelta(minutes=minute)
    result = module.capture(tmp_path, run_id, sha(report), clock=lambda: at)
    assert result['archived_forecasts'] == 7
    assert result['prospectively_eligible_forecasts'] == eligible
    read = module.read_forecasts(tmp_path, as_of=NOW)
    assert read['eligible_forecasts'] == eligible
    assert read['gaps'].get('CAPTURED_AFTER_PREMATCH_LOCKOUT',0) == 7-eligible


def test_future_results_not_known_before_receipt_and_prospective_capture_never_backdates(tmp_path):
    run_id, report, _, _, at, _, _ = native(tmp_path)
    module.capture(tmp_path, run_id, sha(report), clock=lambda: at)
    before = module.read_forecasts(tmp_path, as_of=at-timedelta(seconds=1))
    assert before['eligible_forecasts'] == 0
    pending = module.read_forecasts(tmp_path, as_of=at+timedelta(hours=6))
    assert pending['eligible_forecasts'] == 7 and pending['labeled_forecasts'] == 0
    assert module.read_forecasts(tmp_path, as_of=NOW)['labeled_forecasts'] == 7


@pytest.mark.parametrize('failure', ['probability_bool','probability_nan','not_normalized','missing_btts',
    'disabled_profile','model_not_ready','duplicate_prediction','cutoff','target_team',
    'no_dataset','dataset_corrupt','missing_lineage','late_raw_witness'])
def test_no_partial_market_rows_when_original_forecast_or_source_invalid(tmp_path, failure):
    run_id, report, analysis, summary, at, _, output = native(tmp_path)
    model = analysis['numeric_models']['gauss']; prediction = model['predictions'][0]
    if failure == 'probability_bool': prediction['probabilities']['home'] = True
    if failure == 'probability_nan': prediction['probabilities']['home'] = float('nan')
    if failure == 'not_normalized': prediction['probabilities']['home'] = .9
    if failure == 'missing_btts': prediction['probabilities'].pop('btts_yes')
    if failure == 'disabled_profile': write(output/'settings.json', {'enabled_profiles': []})
    if failure == 'model_not_ready': model['status'] = 'UNPROVEN'
    if failure == 'duplicate_prediction': model['predictions'].append(prediction.copy())
    if failure == 'cutoff': prediction['model_details']['cutoff'] = at.isoformat()
    if failure == 'target_team': analysis['source_view']['fixtures'][0]['provider_home_team_id'] = 'tm_999'
    if failure in {'no_dataset','dataset_corrupt'}:
        path = next((output/'fit/datasets').glob('*.json'))
        path.unlink() if failure == 'no_dataset' else path.write_text('{}')
    if failure == 'missing_lineage': model.pop('training_datasets')
    if failure == 'late_raw_witness':
        with closing(sqlite3.connect(tmp_path/'data/goal_analysis.sqlite3')) as db, db:
            db.execute('DROP TRIGGER temporal_receipts_no_update')
            db.execute('UPDATE temporal_receipts SET available_at=?', (NOW.isoformat(),))
    if failure == 'probability_nan':
        # Invalid JSON is refused at the canonical source hash boundary.
        with pytest.raises(ValueError): resave(output, analysis, summary)
        return
    resave(output, analysis, summary)
    result = module.capture(tmp_path, run_id, sha(report), clock=lambda: at)
    assert result['archived_forecasts'] == 0 and result['gaps']
    assert module.read_forecasts(tmp_path, as_of=NOW)['eligible_forecasts'] == 0


@pytest.mark.parametrize('file', ['analysis.json','summary.json','settings.json','dataset','staging','receipt','capture'])
def test_sealed_bytes_and_first_commit_cannot_be_replaced(tmp_path, file):
    run_id, report, _, _, at, _, output = native(tmp_path)
    module.capture(tmp_path, run_id, sha(report), clock=lambda: at)
    work = tmp_path/'data/arthur/native-calibration-capture'/run_id
    path = (next((output/'fit/datasets').glob('*.json')) if file == 'dataset' else
        tmp_path/'data/arthur/nightly-staging'/f'{run_id}.json' if file == 'staging' else
        work/(file+'.json') if file in {'receipt','capture'} else output/file)
    path.write_text('{}')
    assert module.read_forecasts(tmp_path, as_of=NOW)['eligible_forecasts'] == 0
    before = path.read_bytes()
    with pytest.raises((ValueError, KeyError)):
        module.capture(tmp_path, run_id, sha(report), clock=lambda: NOW)
    assert path.read_bytes() == before


def test_interrupted_claim_no_receipt_never_auto_retries(tmp_path, monkeypatch):
    run_id, report, _, _, at, _, _ = native(tmp_path)
    original = module._new
    def crash(path, value):
        if path.name == 'receipt.json': raise OSError('interrupted')
        return original(path, value)
    monkeypatch.setattr(module, '_new', crash)
    with pytest.raises(OSError): module.capture(tmp_path, run_id, sha(report), clock=lambda: at)
    result = module.capture(tmp_path, run_id, sha(report), clock=lambda: NOW)
    assert result['status'] == 'UNCERTAIN_PREVIOUS_CAPTURE' and not result['automatic_retry']
    assert module.read_forecasts(tmp_path, as_of=NOW)['eligible_forecasts'] == 0


def test_clock_rollback_and_future_worker_completion_do_not_get_receipts(tmp_path):
    run_id, report, _, _, at, _, _ = native(tmp_path)
    stamps = iter([at,at-timedelta(seconds=1)])
    with pytest.raises(ValueError, match='CLOCK'):
        module.capture(tmp_path, run_id, sha(report), clock=lambda: next(stamps))
    assert not (tmp_path/'data/arthur/native-calibration-capture'/run_id/'receipt.json').exists()


def test_capture_deadline_and_path_escape_are_explicit(tmp_path, monkeypatch):
    for bad in ('../escape','x/y','C:/escape','',None):
        with pytest.raises(ValueError): module.capture(tmp_path,bad,'a'*64)
    run_id, report, _, _, at, _, _ = native(tmp_path)
    ticks = iter([0.,100.]); monkeypatch.setattr(module,'monotonic',lambda: next(ticks))
    with pytest.raises(ValueError,match='BUDGET'):
        module.capture(tmp_path, run_id, sha(report), clock=lambda: at)


@pytest.mark.parametrize('change', [{'enabled':1},{'mode':'LIVE'},{'max_seconds':11},{'extra':True}])
def test_only_exact_typed_shadow_policy_enables_capture(tmp_path, change):
    assert not module.enabled(tmp_path)
    write(tmp_path/'config/native-calibration-capture.json', {**module.POLICY, **change})
    assert not module.enabled(tmp_path)
    write(tmp_path/'config/native-calibration-capture.json',module.POLICY)
    assert module.enabled(tmp_path)


def test_verified_manifest_cache_does_not_accept_different_recipe_with_same_id(tmp_path):
    _, _, analysis, summary, _, _, output = native(tmp_path)
    model = analysis['numeric_models']['gauss']
    with closing(sqlite3.connect((tmp_path/'data/goal_analysis.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory = sqlite3.Row
        sources = Sources(tmp_path,db,dataset_root=output/'fit/datasets')
        sources.training(model,'gauss-unit-v1',utc(summary['as_of']))
        model['training_datasets']['manifests'][0]['recipe']['forged'] = True
        with pytest.raises(ValueError,match='MANIFEST'):
            sources.training(model,'gauss-unit-v1',utc(summary['as_of']))


def test_all_market_cohort_is_stable_across_run_ids_and_first_capture_deduplicates(tmp_path):
    run_id, report, _, summary, at, _, output = native(tmp_path)
    module.capture(tmp_path,run_id,sha(report),clock=lambda:at)
    second = 'controlled-2'; report = deepcopy(report); report['run_id'] = second
    cloned = output.with_name(second); shutil.copytree(output,cloned)
    summary = {**summary,'run_id':second,'report_sha256':sha(report)}
    write(cloned/'summary.json',summary)
    write(tmp_path/'data/arthur/nightly-staging'/f'{second}.json',{'artifacts':{'report':report}})
    module.capture(tmp_path,second,sha(report),clock=lambda:at+timedelta(hours=1))
    read = module.read_forecasts(tmp_path,as_of=NOW)
    assert read['eligible_forecasts'] == 14
    assert len({r['strategy_version'] for r in read['dataset']['rows']}) == 1
    result = inspect_calibration(tmp_path,as_of=NOW,enabled=True,clock=lambda:NOW)
    assert result['calibration']['accepted_unique_forecasts'] == 8
    assert sum(result['calibration']['excluded'].values()) == 7


def test_calibration_reader_includes_separate_native_population_with_no_live_release(tmp_path):
    run_id, report, _, _, at, ledger, _ = native(tmp_path)
    module.capture(tmp_path, run_id, sha(report), clock=lambda: at)
    before = ledger.read_bytes()
    result = inspect_calibration(tmp_path,as_of=NOW,enabled=True,clock=lambda:NOW)
    assert result['source_admission']['native_capture']['eligible_forecasts'] == 7
    assert result['source_admission']['admitted_forecasts'] == 8
    assert result['calibration']['selection_basis'] == 'PUBLISHED_LEGS_AND_SEPARATE_NATIVE_ALL_MARKET_COHORTS'
    assert result['status'] == 'NOT_FIT'
    assert all(not g['release_allowed'] and g['candidate'] is None for g in result['calibration']['groups'])
    assert archive_calibration(tmp_path,result)['created']
    assert not archive_calibration(tmp_path,result)['created'] and ledger.read_bytes() == before


@pytest.mark.parametrize('failure', ['hash','identity','normalized_score','raw','team','future'])
def test_invalid_result_is_not_used_and_no_partial_forecasts_escape_batch(tmp_path, failure):
    run_id, report, _, _, at, _, _ = native(tmp_path)
    module.capture(tmp_path, run_id, sha(report), clock=lambda: at)
    from goal_analysis.storage.canonical_registry import digest
    from goal_analysis.storage.historical_results import identity
    with closing(sqlite3.connect(tmp_path/'data/goal_analysis.sqlite3')) as db, db:
        record = json.loads(db.execute('SELECT record_json FROM historical_result_versions').fetchone()[0])
        db.execute('DROP TRIGGER historical_result_versions_no_update')
        if failure == 'raw': record['raw']['score']['regulation']['home'] = 0
        if failure == 'team': record['home_id'] = 'tm_999'
        if failure == 'normalized_score': record['home_goals'] = 99
        db.execute('UPDATE historical_result_versions SET record_json=?,version_id=?,identity_sha256=?,ingested_at=?',
            (json.dumps(record),'b'*64 if failure == 'hash' else digest(record),
             'c'*64 if failure == 'identity' else identity(record),
             (NOW+timedelta(days=1)).isoformat() if failure == 'future' else (NOW-timedelta(days=1)).isoformat()))
    read = module.read_forecasts(tmp_path,as_of=NOW)
    assert read['labeled_forecasts'] == 0
    assert read['eligible_forecasts'] == (7 if failure == 'future' else 0)


@pytest.mark.parametrize('mode', ['enabled','disabled','failed','no_time'])
def test_parent_post_success_capture_optional_and_isolated_with_no_second_worker(tmp_path, monkeypatch, mode):
    run_id, report, _, _, _, _, output = native(tmp_path)
    prepared = tmp_path/'prepared'; output.rename(prepared)
    if mode != 'disabled': write(tmp_path/'config/native-calibration-capture.json',module.POLICY)
    def child(*args, **kwargs):
        for name in ('analysis.json','summary.json'): shutil.copy2(prepared/name,output/name)
        return Mock(returncode=0)
    runner = Mock(side_effect=child); monkeypatch.setattr(daily.subprocess,'run',runner)
    capture = Mock(return_value={'status':'CAPTURED_SHADOW'})
    if mode == 'failed': capture.side_effect = OSError('failure')
    monkeypatch.setattr(module,'capture',capture)
    if mode == 'no_time':
        ticks = iter([0,0,180]); monkeypatch.setattr(daily,'monotonic',lambda:next(ticks))
    result = daily.run(tmp_path,run_id,sha(report),{'enabled_profiles':['gauss']},as_of=NOW)
    assert runner.call_count == 1 and result['status'] == 'OBSERVED'
    assert capture.call_count == int(mode in {'enabled','failed'})
    assert result['calibration_capture']['status'] == {
        'enabled':'CAPTURED_SHADOW','disabled':'DISABLED','failed':'UNAVAILABLE',
        'no_time':'SKIPPED_NO_SPARE_WINDOW'}[mode]
