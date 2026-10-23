"""Real local receipts/recomputed math; synthetic football fixtures, no network."""
import json
import socket
import sqlite3
import subprocess
from contextlib import closing
from datetime import timedelta
from unittest.mock import Mock

import pytest
from test_xg_specialist_shadow import NOW, environment

from goal_analysis.jobs import xg_forecast_capture as module
from goal_analysis.jobs.calibration_shadow import inspect_calibration
from goal_analysis.normalization.training_datasets import sha
from goal_analysis.storage import SnapshotStore
from goal_analysis.storage.historical_results import HistoricalResults
from goal_analysis.storage.temporal import TemporalStore


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), 'utf-8')


def setup(root, **kwargs):
    data, _, db, meta = environment(root, **kwargs)
    return data['fixtures'], db, meta


def run(root, fixtures, **kwargs):
    return module.capture(root, 'prospective-xg', 'a'*64, fixtures, clock=lambda: NOW, **kwargs)


def work(root):
    return root/'data/arthur/xg-forecast-capture/prospective-xg'


def dbrows(root):
    with closing(sqlite3.connect(root/'data/goal_analysis.sqlite3')) as db:
        return tuple(db.iterdump())


def test_actual_recomputed_forecasts_all_markets_and_readonly_reader(tmp_path, monkeypatch):
    fixtures, _, _ = setup(tmp_path)
    before = dbrows(tmp_path)
    monkeypatch.setattr(socket, 'socket', Mock(side_effect=AssertionError('NO_NETWORK')))
    result = run(tmp_path, fixtures)
    assert result['new_forecasts'] == result['prospectively_eligible_forecasts'] == 21
    assert result['additional_xg_rate_fits'] == 1 and result['external_requests'] == result['llm_requests'] == 0
    assert not result['live_decision_effect']
    sealed = {p:p.read_bytes() for p in work(tmp_path).rglob('*.json')}
    read = module.read_forecasts(tmp_path, as_of=NOW)
    assert read['eligible_forecasts'] == 21 and read['labeled_forecasts'] == 0, read
    assert not read['gaps'] and read['external_requests'] == read['ledger_writes'] == 0
    for row in read['dataset']['rows']:
        assert row['profile_id'] == 'native_shadow_xg' and row['model_version'] == module.XG_VERSION
        assert row['population'] == module.POPULATION and not row['calibrated']
        assert row['model_as_of'] == row['created_at'] == row['saved_at'] == NOW.isoformat()
        assert row['dataset_ids'] and row['source_receipt_ids'] and 0 < row['probability'] < 1
        assert not row['result_receipts']
    assert dbrows(tmp_path) == before and all(p.read_bytes() == blob for p,blob in sealed.items())
    repeated = module.capture(tmp_path,'prospective-xg','a'*64,fixtures,clock=lambda:NOW+timedelta(minutes=1))
    assert repeated['new_forecasts'] == repeated['additional_xg_rate_fits'] == 0
    assert repeated['receipt_id'] == result['receipt_id'] and repeated['available_at'] == NOW.isoformat()
    assert module.read_forecasts(tmp_path,as_of=NOW-timedelta(seconds=1))['eligible_forecasts'] == 0


def test_calibration_admits_separate_xg_population_but_never_weakens_sample_gates(tmp_path):
    fixtures, _, _ = setup(tmp_path); run(tmp_path,fixtures)
    value = inspect_calibration(tmp_path,as_of=NOW,enabled=True,clock=lambda:NOW)
    native = value['source_admission']['native_capture']
    assert native['xg_capture']['eligible_forecasts'] == native['eligible_forecasts'] == 21
    assert value['source_admission']['admitted_forecasts'] == 21
    assert value['status'] == 'NOT_FIT' and not value['live_decision_effect']
    assert all(not g['release_allowed'] and g['candidate'] is None for g in value['calibration']['groups'])


@pytest.mark.parametrize('reason', ['stale','identity','no_witness','thin'])
def test_insufficient_or_unproven_inputs_do_not_manufacture_forecasts(tmp_path,reason):
    kwargs = {'metadata_when':NOW-timedelta(minutes=31)} if reason == 'stale' else (
        {'witness':False} if reason == 'no_witness' else {'history_teams':range(2,4)} if reason == 'thin' else {})
    fixtures, _, _ = setup(tmp_path,**kwargs)
    if reason == 'identity':
        for f in fixtures: f['provider_home_team_id'] = 'tm_999'
    result = run(tmp_path,fixtures)
    assert result['new_forecasts'] == 0
    assert module.read_forecasts(tmp_path,as_of=NOW)['eligible_forecasts'] == 0


def test_duplicate_target_and_invalid_scope_fail_before_claim(tmp_path):
    fixtures, _, _ = setup(tmp_path)
    with pytest.raises(ValueError,match='DUPLICATE'): run(tmp_path,fixtures+[fixtures[0]])
    assert not work(tmp_path).exists()
    for bad in ('../escape','x/y','',None):
        with pytest.raises(ValueError): module.capture(tmp_path,bad,'a'*64,fixtures)
    for seconds in (True,0,11):
        with pytest.raises(ValueError): run(tmp_path,fixtures,max_seconds=seconds)


@pytest.mark.parametrize('file', ['inputs','fit','dataset','capture','receipt','claim','raw'])
def test_changed_bytes_or_receipts_are_rejected(tmp_path,file):
    fixtures, _, meta = setup(tmp_path); run(tmp_path,fixtures)
    path = (next((work(tmp_path)/'datasets').glob('*.json')) if file == 'dataset' else
        tmp_path/'data/raw'/meta.relative_path if file == 'raw' else work(tmp_path)/(file+'.json'))
    path.write_text('{}','utf-8')
    value = module.read_forecasts(tmp_path,as_of=NOW)
    assert value['eligible_forecasts'] == 0 and value['gaps']


@pytest.mark.parametrize('change', ['probability','math','manifest'])
def test_resigned_invented_predictions_cannot_replace_actual_numerical_reconstruction(tmp_path,change):
    fixtures, _, _ = setup(tmp_path); run(tmp_path,fixtures)
    body = json.loads((work(tmp_path)/'capture.json').read_bytes())
    if change == 'probability': body['rows'][0]['probability'] = .001
    if change == 'math': body['math_sha256']['models/xg_rates.py'] = 'b'*64
    if change == 'manifest': body['files'] = {}
    write(work(tmp_path)/'capture.json',body)
    receipt = json.loads((work(tmp_path)/'receipt.json').read_bytes())
    receipt['capture_sha256'] = sha(body)
    receipt['receipt_id'] = sha({k:v for k,v in receipt.items() if k != 'receipt_id'})
    write(work(tmp_path)/'receipt.json',receipt)
    read = module.read_forecasts(tmp_path,as_of=NOW)
    assert read['eligible_forecasts'] == 0 and read['gaps']


def test_interrupted_publication_is_not_retried_or_backdated(tmp_path,monkeypatch):
    fixtures, _, _ = setup(tmp_path); original = module._new
    def crash(path,value):
        if path.name == 'receipt.json': raise OSError('interrupted')
        return original(path,value)
    monkeypatch.setattr(module,'_new',crash)
    with pytest.raises(OSError): run(tmp_path,fixtures)
    result = run(tmp_path,fixtures)
    assert result['status'] == 'UNCERTAIN_PREVIOUS_CAPTURE' and result['automatic_retry'] is False
    assert module.read_forecasts(tmp_path,as_of=NOW)['eligible_forecasts'] == 0


def test_actual_save_clock_after_prematch_lockout_is_not_eligible(tmp_path):
    fixtures, _, _ = setup(tmp_path)
    late = NOW+timedelta(hours=6)-timedelta(minutes=14)
    stamps = iter([NOW,NOW,NOW,late])
    value = module.capture(tmp_path,'prospective-xg','a'*64,fixtures,clock=lambda:next(stamps))
    assert value['archived_forecasts'] == 21 and value['prospectively_eligible_forecasts'] == 0
    read = module.read_forecasts(tmp_path,as_of=late)
    assert read['eligible_forecasts'] == 0 and read['gaps']['CAPTURED_AFTER_PREMATCH_LOCKOUT'] == 21


def test_clock_rollback_cannot_publish_receipt(tmp_path):
    fixtures, _, _ = setup(tmp_path); stamps = iter([NOW,NOW-timedelta(seconds=1)])
    with pytest.raises(ValueError,match='CLOCK'):
        module.capture(tmp_path,'prospective-xg','a'*64,fixtures,clock=lambda:next(stamps))
    assert not (work(tmp_path)/'receipt.json').exists()


@pytest.mark.parametrize('change', [{'enabled':1},{'mode':'LIVE'},{'max_seconds':11},{'extra':True}])
def test_only_exact_shadow_policy_can_enable(tmp_path,change):
    assert not module.enabled(tmp_path)
    write(tmp_path/'config/xg-forecast-capture.json',{**module.POLICY,**change})
    assert not module.enabled(tmp_path)
    write(tmp_path/'config/xg-forecast-capture.json',module.POLICY)
    assert module.enabled(tmp_path)


def result_source(root, fixtures, db):
    f = fixtures[0]; at = NOW+timedelta(hours=9)
    raw = {'id':f['provider_fixture_id'],'competition_id':f['provider_league_id'],
        'season_id':f['provider_season_id'],'home_team':{'id':f['provider_home_team_id'],'name':f['home_team']},
        'away_team':{'id':f['provider_away_team_id'],'name':f['away_team']},'utc_date':f['kickoff'],
        'status':'finished','is_neutral':False,'score':{'regulation':{'home':2,'away':1},
            'went_to_extra_time':False,'went_to_penalties':False}}
    source = SnapshotStore(db,root/'data/raw').save('the_stats_api','later_result',
        json.dumps({'data':raw}).encode(),'application/json',fetched_at=at,
        source_url='https://api.thestatsapi.com/api/football/matches/'+raw['id'])
    TemporalStore(db,root/'data/raw',clock=lambda:at+timedelta(seconds=1)).capture('raw',limit=1000)
    record = {'provider':'the_stats_api','provider_id':raw['id'],'competition_id':raw['competition_id'],
        'season_id':raw['season_id'],'home_id':raw['home_team']['id'],'away_id':raw['away_team']['id'],
        'home_team':f['home_team'],'away_team':f['away_team'],'kickoff':f['kickoff'],
        'match_date':NOW.date().isoformat(),'kickoff_local_time':None,'status':'finished','score_scope':'REGULATION',
        'home_goals':2,'away_goals':1,'half_home_goals':None,'half_away_goals':None,
        'observed_at':at.isoformat(),'source_url':source.source_url,'source_sha256':source.content_hash,'raw':raw}
    history = HistoricalResults(db,clock=lambda:at+timedelta(seconds=2)); history.initialize(); history.ingest([record])
    return at


def test_only_genuine_later_known_result_labels_all_markets_for_one_fixture(tmp_path):
    fixtures, db, _ = setup(tmp_path); run(tmp_path,fixtures)
    at = result_source(tmp_path,fixtures,db)
    assert module.read_forecasts(tmp_path,as_of=at)['labeled_forecasts'] == 0
    read = module.read_forecasts(tmp_path,as_of=at+timedelta(minutes=1))
    assert read['eligible_forecasts'] == 21 and read['labeled_forecasts'] == 7, read['gaps']
    labeled = [r for r in read['dataset']['rows'] if r['result_receipts']]
    assert len({r['fixture_id'] for r in labeled}) == 1
    assert {r['selection_key']:r['result_receipts'][0]['outcome'] for r in labeled if r['market_key']=='h2h'} == {'home':1,'draw':0,'away':0}


def test_result_error_late_in_batch_does_not_admit_partial_run(tmp_path,monkeypatch):
    fixtures, _, _ = setup(tmp_path); run(tmp_path,fixtures)
    calls = []
    def broken(*args):
        calls.append(1)
        if len(calls) == 2: raise ValueError('INVALID_LATER_RESULT')
        return []
    monkeypatch.setattr(module,'_results',broken)
    value = module.read_forecasts(tmp_path,as_of=NOW)
    assert value['eligible_forecasts'] == 0 and value['gaps']['INVALID_LATER_RESULT'] == 1


@pytest.mark.parametrize('fail', [False,True])
def test_parent_captures_before_isolated_worker_and_failure_is_local(tmp_path,monkeypatch,fail):
    from test_daily_native_shadow import staged

    from goal_analysis.jobs import daily_native_shadow, native_source_admission

    run_id,report,path,_ = staged(tmp_path); original = path.read_bytes(); order = []
    write(tmp_path/'config/xg-forecast-capture.json',module.POLICY)
    monkeypatch.setattr(native_source_admission,'admit_history',lambda *_a:{'status':'NO_DATA'})
    def capture(*a,**k):
        order.append('capture')
        if fail: raise ValueError('sensitive-secret')
        return {'status':'CAPTURED_SHADOW','external_requests':0}
    monkeypatch.setattr(module,'capture',capture)
    def worker(command,**kwargs):
        order.append('worker')
        assert 'THE_STATS_API_KEY' not in kwargs['env']
        raise subprocess.TimeoutExpired(command,kwargs['timeout'])
    monkeypatch.setattr(daily_native_shadow.subprocess,'run',worker)
    value = daily_native_shadow.run(tmp_path,run_id,sha(report),{},as_of=NOW,prepare_sources=True)
    assert order == ['capture','worker'] and path.read_bytes() == original
    assert value['xg_forecast_capture']['status'] == ('UNAVAILABLE' if fail else 'CAPTURED_SHADOW')
    assert 'sensitive-secret' not in json.dumps(value)


def test_source_and_wall_time_bounds_fail_closed(tmp_path,monkeypatch):
    fixtures, _, _ = setup(tmp_path)
    ticks = iter([0.,100.]); monkeypatch.setattr(module,'monotonic',lambda:next(ticks))
    with pytest.raises(ValueError,match='BUDGET'): run(tmp_path,fixtures)
    assert not (work(tmp_path)/'receipt.json').exists()
