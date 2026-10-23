"""Source-backed feature pairs; controlled test fixtures are not production evidence."""
import json
import shutil
import socket
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock

import pytest
from test_native_forecast_capture import native, resave, sports_rows, write

from goal_analysis.jobs import daily_native_shadow as daily
from goal_analysis.jobs import native_forecast_capture as baseline
from goal_analysis.jobs import weather_feature_capture as module
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha, validate
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.sports_weather import WeatherStore
from goal_analysis.storage.temporal import TemporalStore


def environment(root, *, broken=None):
    run, report, analysis, summary, _at, ledger, output = native(root)
    model_at=utc(summary['as_of']); at=model_at+timedelta(minutes=3)
    f=analysis['source_view']['fixtures'][0]
    f['source_provider']='the_stats_api'
    db=Database(root/'data/goal_analysis.sqlite3')
    # The shared calibration fixture preinstalls a future result receipt.
    # Build this controlled prematch fixture without that unrelated future
    # witness; retain production append-only triggers for the actual tests.
    with closing(sqlite3.connect(db.path)) as connection,connection:
        connection.execute('DROP TRIGGER temporal_receipts_no_delete')
        connection.execute('DELETE FROM temporal_receipts WHERE julianday(available_at)>julianday(?)',
                           (model_at.isoformat(),))
    TemporalStore(db,root/'data/raw').initialize()
    raw={'id':f['provider_fixture_id'],'competition_id':f['provider_league_id'],
        'season_id':f['provider_season_id'],'utc_date':f['kickoff'],'status':'scheduled','is_neutral':False,
        'home_team':{'id':f['provider_home_team_id'],'name':'H'},
        'away_team':{'id':f['provider_away_team_id'],'name':'A'},
        'venue':{'name':'Explicit stadium','city':'Berlin','latitude':52.5,'longitude':13.4}}
    source=SnapshotStore(db,root/'data/raw').save('the_stats_api','football_matches',
        json.dumps({'data':[raw]}).encode(),'application/json',model_at-timedelta(minutes=2),
        'https://api.thestatsapi.com/api/football/matches?status=scheduled')
    f['fixture_source']={'content_hash':source.content_hash,'source_url':source.source_url,
                         'observed_at':source.fetched_at.isoformat()}
    TemporalStore(db,root/'data/raw',clock=lambda:model_at-timedelta(minutes=1)).capture('raw')
    resave(output,analysis,summary)
    baseline.capture(root,run,sha(report),clock=lambda:at)
    fixture=deepcopy(f); observed=model_at-timedelta(minutes=3)
    if broken=='identity': fixture['kickoff']=(utc(f['kickoff'])+timedelta(hours=1)).isoformat()
    if broken=='stale': observed=at-timedelta(hours=7)
    target=f['kickoff']; text=target[:16]
    weather={'fixture_id':f['fixture_id'],'provider':'open_meteo','weather_kind':'FORECAST',
        'status':'SOURCED','observed_at':observed.isoformat(),'forecast_time':target,
        'latitude':52.5,'longitude':13.4,'temperature_c':12.,'wind_speed_kmh':20.,
        'precipitation_mm':.3,'relative_humidity_pct':65.,
        'source_url':'https://api.open-meteo.com/v1/forecast?latitude=52.5&longitude=13.4&timezone=UTC'}
    payload={'latitude':52.49,'longitude':13.39,'utc_offset_seconds':0,
        'hourly':{'time':[text],'temperature_2m':[12.],'wind_speed_10m':[20.],
                  'precipitation':[.3],'relative_humidity_2m':[65.]},
        'hourly_units':{'temperature_2m':'°C','wind_speed_10m':'km/h','precipitation':'mm','relative_humidity_2m':'%'}}
    store=WeatherStore(db);store.initialize()
    store.ingest(weather,fixture,source_run_id='controlled-unit-weather',raw_payload=payload)
    if broken!='receipt':
        TemporalStore(db,root/'data/raw',clock=lambda:model_at-timedelta(seconds=30)).capture('weather')
    write(root/'config/weather-feature-capture.json',module.POLICY)
    return run,report,at+timedelta(seconds=1),ledger,output,db


def test_actual_temp_sources_and_full_identity_seven_products_no_fit_network_or_mutation(tmp_path,monkeypatch):
    run,report,at,ledger,output,_db=environment(tmp_path)
    original={p:p.read_bytes() for p in [ledger,output/'analysis.json',output/'summary.json']}
    before=sports_rows(tmp_path)
    forbidden=Mock(side_effect=AssertionError('NO_NETWORK_OR_FIT'))
    monkeypatch.setattr(socket,'socket',forbidden)
    a=module.capture(tmp_path,run,sha(report),clock=lambda:at)
    assert a['eligible_feature_pairs']==a['new_pairs']==7
    read=module.read_pairs(tmp_path,as_of=at+timedelta(seconds=1))
    assert read['eligible_feature_pairs']==7 and validate(read['dataset'])==read['dataset']
    for row in read['dataset']['rows']:
        assert row['baseline']['probability_kind']=='ORIGINAL_NUMERIC_MODEL_ESTIMATE'
        assert 'result_receipts' not in row['baseline']
        assert row['weather_context']['forecast']['temperature_c']==12.
        assert row['weather_model_probability'] is None and row['probability_adjustment']==0
        assert row['saved_at']==a['available_at'] and not row['live_decision_effect']
    assert sports_rows(tmp_path)==before and all(p.read_bytes()==v for p,v in original.items())
    forbidden.assert_not_called()
    b=module.capture(tmp_path,run,sha(report),clock=lambda:at+timedelta(minutes=1))
    assert b['new_pairs']==0 and b['receipt_id']==a['receipt_id'] and b['available_at']==a['available_at']


def test_real_own_presence_and_earlier_prefix_no_relabeling(tmp_path):
    run,report,at,*_=environment(tmp_path)
    stamps=iter([at,at+timedelta(seconds=1),at+timedelta(seconds=2)])
    a=module.capture(tmp_path,run,sha(report),clock=lambda:next(stamps))
    assert a['available_at']==(at+timedelta(seconds=2)).isoformat()
    assert module.read_pairs(tmp_path,as_of=at+timedelta(seconds=1))['eligible_feature_pairs']==0
    assert module.read_pairs(tmp_path,as_of=at+timedelta(seconds=3))['eligible_feature_pairs']==7


@pytest.mark.parametrize('broken',['identity','stale','receipt','missing','target_expired'])
def test_data_gaps_are_explicit_and_cannot_become_pairs(tmp_path,broken):
    run,report,at,_l,_o,db=environment(tmp_path,broken=broken)
    if broken=='missing':
        # A newer failed observation cannot expose a favorable older one.
        with closing(db.connect()) as connection:
            source=json.loads(connection.execute('SELECT snapshot_json FROM sports_weather_snapshots').fetchone()[0])
        WeatherStore(db).ingest({'fixture_id':source['fixture_id'],'provider':'open_meteo','weather_kind':'FORECAST',
            'status':'UNKNOWN','observed_at':at.isoformat()},source['fixture'],source_run_id='new-failure',
            time_basis='LOOKUP_ATTEMPT_AT')
        TemporalStore(db,tmp_path/'data/raw',clock=lambda:at).capture('weather')
    if broken=='target_expired':at+=timedelta(minutes=31)
    a=module.capture(tmp_path,run,sha(report),clock=lambda:at)
    assert a['status']=='GAPS' and a['eligible_feature_pairs']==0 and a['gaps']
    assert module.read_pairs(tmp_path,as_of=at)['eligible_feature_pairs']==0


@pytest.mark.parametrize('part',['baseline_bytes','weather_payload','new_receipt','new_claim','saved_pair'])
def test_reader_rechecks_physical_sources_not_just_new_hashes(tmp_path,part):
    run,report,at,_l,output,db=environment(tmp_path)
    module.capture(tmp_path,run,sha(report),clock=lambda:at)
    work=tmp_path/'data/arthur/weather-feature-capture'/run
    if part=='baseline_bytes':(output/'analysis.json').write_text('{}')
    elif part=='weather_payload':
        with closing(sqlite3.connect(db.path)) as connection,connection:
            # Deliberately simulate storage corruption; no production mutation.
            for name,sql in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall():
                if 'sports_weather_snapshots' in (sql or ''):connection.execute('DROP TRIGGER "'+name+'"')
            connection.execute("UPDATE sports_weather_snapshots SET snapshot_json='{}'")
    else:
        path=work/('claim.json' if part=='new_claim' else 'receipt.json' if part=='new_receipt' else 'capture.json')
        value=json.loads(path.read_bytes())
        if part=='new_claim':value['started_at']=(at+timedelta(days=1)).isoformat()
        elif part=='new_receipt':
            value['available_at']=(at-timedelta(seconds=1)).isoformat()
            value['receipt_id']=sha({k:v for k,v in value.items() if k!='receipt_id'})
        else:
            value['rows'][0]['baseline']['probability']=.01
            receipt=json.loads((work/'receipt.json').read_bytes());receipt['capture_sha256']=sha(value)
            receipt['receipt_id']=sha({k:v for k,v in receipt.items() if k!='receipt_id'})
            write(work/'receipt.json',receipt)
        write(path,value)
    result=module.read_pairs(tmp_path,as_of=at+timedelta(seconds=1))
    assert result['eligible_feature_pairs']==0 and result['gaps']


def test_expiration_between_verification_and_commit_is_not_admitted(tmp_path):
    run,report,at,*_=environment(tmp_path)
    stamps=iter([at,at,at+timedelta(minutes=30)])
    a=module.capture(tmp_path,run,sha(report),clock=lambda:next(stamps))
    assert a['eligible_feature_pairs']==0
    assert module.read_pairs(tmp_path,as_of=at+timedelta(minutes=31))['eligible_feature_pairs']==0


def test_uncertain_write_is_not_automatically_retried(tmp_path,monkeypatch):
    run,report,at,*_=environment(tmp_path);original=baseline._new
    def crash(path,value):
        if path.name=='receipt.json':raise OSError('interrupted')
        return original(path,value)
    monkeypatch.setattr(baseline,'_new',crash)
    with pytest.raises(OSError):module.capture(tmp_path,run,sha(report),clock=lambda:at)
    monkeypatch.setattr(module,'_source_pairs',Mock(side_effect=AssertionError('NO_RETRY')))
    assert module.capture(tmp_path,run,sha(report),clock=lambda:at)['status']=='UNCERTAIN_PREVIOUS_CAPTURE'


def test_deadline_exhaustion_never_seals_and_reader_propagates_budget(tmp_path,monkeypatch):
    run,report,at,*_=environment(tmp_path)
    ticks=iter([0.,100.]);monkeypatch.setattr(module,'monotonic',lambda:next(ticks))
    with pytest.raises(ValueError,match='BUDGET'):module.capture(tmp_path,run,sha(report),clock=lambda:at)
    assert not (tmp_path/'data/arthur/weather-feature-capture'/run/'receipt.json').exists()
    ticks=iter([0.,100.]);monkeypatch.setattr(module,'monotonic',lambda:next(ticks))
    with pytest.raises(ValueError,match='BUDGET'):module.read_pairs(tmp_path,as_of=at)


def test_clock_rollback_never_seals(tmp_path):
    run,report,at,*_=environment(tmp_path)
    stamps=iter([at,at-timedelta(seconds=1)])
    with pytest.raises(ValueError,match='CLOCK'):module.capture(tmp_path,run,sha(report),clock=lambda:next(stamps))
    assert not (tmp_path/'data/arthur/weather-feature-capture'/run/'receipt.json').exists()


@pytest.mark.parametrize('identifier',['../escape','a/b','', 'C:/escape'])
def test_invalid_new_capture_identifier_rejected_before_work(tmp_path,identifier):
    with pytest.raises(ValueError):module.capture(tmp_path,'normal','a'*64,capture_id=identifier)
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('delta,eligible',[(-1,7),(0,0),(1,0)])
def test_exact_fifteen_minute_commit_boundary_without_timestamp_rewriting(tmp_path,delta,eligible):
    run,report,at,_l,output,_db=environment(tmp_path)
    original=json.loads((output/'analysis.json').read_bytes())
    kickoff=utc(original['source_view']['fixtures'][0]['kickoff'])
    committed=kickoff-timedelta(minutes=15)+timedelta(seconds=delta)
    # Keep the source genuinely fresh at verification, then cross the commit
    # boundary. The commit rechecks lockout AND metadata freshness; even the
    # pre-boundary case is stale-target and therefore correctly admits zero.
    stamps=iter([at,at,committed])
    result=module.capture(tmp_path,run,sha(report),clock=lambda:next(stamps))
    assert result['eligible_feature_pairs']==0
    body=json.loads((tmp_path/'data/arthur/weather-feature-capture'/run/'capture.json').read_bytes())
    assert len(body['rows'])==7  # All seven were genuinely verified earlier.
    pair=body['rows'][0]
    pair['weather_context']['target_receipt']['times']['observed_at']=committed.isoformat()
    pair['weather_context']['forecast']['observed_at']=committed.isoformat()
    assert module._completion(pair,committed)==bool(eligible)


def test_new_observation_id_preserves_old_receipt_and_reader_keeps_observations_distinct(tmp_path):
    run,report,at,*_=environment(tmp_path)
    first=module.capture(tmp_path,run,sha(report),clock=lambda:at)
    second=module.capture(tmp_path,run,sha(report),capture_id='later-own-observation',clock=lambda:at+timedelta(seconds=1))
    assert second['receipt_id']!=first['receipt_id']
    read=module.read_pairs(tmp_path,as_of=at+timedelta(seconds=2))
    assert read['eligible_feature_pairs']==14
    # Repeated observations are NOT 14 independent fixtures or model estimates.
    assert len({r['baseline']['forecast_id'] for r in read['dataset']['rows']})==7
    assert len({r['fixture_id'] for r in read['dataset']['rows']})==1


def fresh_current(root,run,at,*,bad=None):
    body=json.loads((root/'data/arthur/native-calibration-capture'/run/'capture.json').read_bytes())
    f=deepcopy(body['rows'][0]['target']);db=Database(root/'data/goal_analysis.sqlite3')
    with closing(db.connect()) as c:
        original=json.loads(c.execute('SELECT snapshot_json FROM sports_weather_snapshots LIMIT 1').fetchone()[0])
    raw={'id':f['provider_fixture_id'],'competition_id':f['provider_league_id'],
        'season_id':'sn_999' if bad=='raw_season' else f['provider_season_id'],
        'utc_date':f['kickoff'],'status':'scheduled','is_neutral':False,
        'home_team':{'id':f['provider_home_team_id'],'name':'H'},
        'away_team':{'id':f['provider_away_team_id'],'name':'A'},
        'venue':{'name':'Explicit stadium','city':'Berlin','latitude':52.5,'longitude':13.4}}
    source=SnapshotStore(db,root/'data/raw').save('the_stats_api','football_match_detail',
        json.dumps({'data':raw}).encode(),'application/json',at-timedelta(minutes=2),
        'https://api.thestatsapi.com/api/football/matches/mt_900')
    f['fixture_source']={'content_hash':source.content_hash,'source_url':source.source_url,
                         'observed_at':source.fetched_at.isoformat()}
    TemporalStore(db,root/'data/raw',clock=lambda:at-timedelta(minutes=1)).capture('raw')
    weather=deepcopy(original['weather']);weather['observed_at']=(at-timedelta(minutes=2)).isoformat()
    WeatherStore(db).ingest(weather,f,source_run_id='new-genuine-test-observation',raw_payload=original['raw_payload'])
    TemporalStore(db,root/'data/raw',clock=lambda:at-timedelta(seconds=10)).capture('weather')
    if bad=='input_season':f['provider_season_id']='sn_999'
    if bad=='caller_probability':f.update(probability=.001,model_probability=.001,weather_model_probability=.999)
    return [f]


@pytest.mark.parametrize('bad',[None,'raw_season','input_season','caller_probability'])
def test_new_witnessed_detail_target_and_weather_refresh_bind_old_baseline_without_rewriting(tmp_path,bad):
    run,report,at,_l,output,_db=environment(tmp_path)
    original={p:p.read_bytes() for p in [output/'analysis.json',output/'summary.json',
        tmp_path/'data/arthur/native-calibration-capture'/run/'capture.json']}
    at+=timedelta(minutes=40)
    fixtures=fresh_current(tmp_path,run,at,bad=bad)
    result=module.capture(tmp_path,run,sha(report),fixtures=fixtures,clock=lambda:at)
    expected=7 if bad in (None,'caller_probability') else 0
    assert result['eligible_feature_pairs']==expected
    actual=module.read_pairs(tmp_path,as_of=at)
    assert actual['eligible_feature_pairs']==expected
    if expected:
        assert {r['baseline']['probability'] for r in actual['dataset']['rows']}=={.5,.2,.3,.7,.6,.4}
        assert all(r['weather_model_probability'] is None for r in actual['dataset']['rows'])
    else:assert result['gaps']
    assert all(p.read_bytes()==v for p,v in original.items())


def test_missing_or_duplicate_explicit_targets_do_not_fall_back_to_original_metadata(tmp_path):
    run,report,at,*_=environment(tmp_path)
    empty=module.capture(tmp_path,run,sha(report),fixtures=[],capture_id='empty',clock=lambda:at)
    assert empty['eligible_feature_pairs']==0 and empty['gaps']=={'MISSING_CURRENT_WEATHER_TARGET_INPUT':7}
    f=fresh_current(tmp_path,run,at+timedelta(minutes=1))[0]
    with pytest.raises(ValueError,match='DUPLICATE'):
        module.capture(tmp_path,run,sha(report),fixtures=[f,f],capture_id='duplicate',clock=lambda:at+timedelta(minutes=1))


@pytest.mark.parametrize('budget',[0,31,True,1.5])
def test_typed_bounded_budget_before_work(tmp_path,budget):
    with pytest.raises(ValueError,match='BUDGET'):module.capture(tmp_path,'ok','a'*64,max_seconds=budget)
    with pytest.raises(ValueError,match='BUDGET'):module.read_pairs(tmp_path,as_of='2026-10-23T12:00:00Z',max_seconds=budget)
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('policy',[{'enabled':1},{'mode':'LIVE'},{'max_seconds':31},{'extra':True}])
def test_only_exact_typed_policy_enables_collection(tmp_path,policy):
    write(tmp_path/'config/weather-feature-capture.json',{**module.POLICY,**policy})
    assert not module.enabled(tmp_path)


@pytest.mark.parametrize('case',['enabled','no_capture','failure','disabled'])
def test_daily_postsave_hook_no_new_worker_no_probability_change_or_veto(tmp_path,monkeypatch,case):
    run,report,_a,_s,_at,_ledger,output=native(tmp_path)
    prepared=tmp_path/'prepared';output.rename(prepared)
    write(tmp_path/'config/native-calibration-capture.json',baseline.POLICY)
    if case!='disabled':write(tmp_path/'config/weather-feature-capture.json',module.POLICY)
    def child(*args,**kwargs):
        for name in ('analysis.json','summary.json'):shutil.copy2(prepared/name,output/name)
        return Mock(returncode=0)
    runner=Mock(side_effect=child);monkeypatch.setattr(daily.subprocess,'run',runner)
    monkeypatch.setattr(baseline,'capture',lambda *a,**k:{} if case=='no_capture' else {'receipt_id':'a'*64})
    observer=Mock(return_value={'status':'CAPTURED_FEATURES_SHADOW'})
    if case=='failure':observer.side_effect=OSError('failed')
    monkeypatch.setattr(module,'capture',observer)
    result=daily.run(tmp_path,run,sha(report),{'enabled_profiles':['gauss']},as_of=utc(report['observed_at']))
    assert runner.call_count==1 and result['status']=='OBSERVED'
    assert observer.call_count==int(case in {'enabled','failure'})
    assert result['weather_feature_capture']['status']=={'enabled':'CAPTURED_FEATURES_SHADOW',
        'no_capture':'SKIPPED_NO_VERIFIED_CAPTURE','failure':'UNAVAILABLE','disabled':'DISABLED'}[case]
