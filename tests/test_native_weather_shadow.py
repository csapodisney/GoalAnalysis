"""Real local weather/raw witnesses and daily bindings, no network or forecasts."""
import json
import socket
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock

import pytest
from test_xg_specialist_shadow import NOW
from test_xg_specialist_shadow import environment as base

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import native_options_shadow as pipeline
from goal_analysis.jobs import weather_option_shadow
from goal_analysis.jobs.native_weather_shadow import inspect_native_weather
from goal_analysis.normalization.training_datasets import validate
from goal_analysis.storage import SnapshotStore
from goal_analysis.storage.sports_weather import WeatherStore
from goal_analysis.storage.temporal import TemporalStore


def environment(root, *, venue=True, weather_kind='FORECAST', witness=True, available=None,
                temperature=12, wrong_units=False, legacy=False, observed=None, conflict=None):
    data, models, db, _ = base(root, history_teams=[])
    obs = NOW-timedelta(minutes=5) if observed is None else observed
    raw = SnapshotStore(db, root/'data/raw')
    matches = [{'id': f['provider_fixture_id'], 'competition_id': f['provider_league_id'],
        'season_id': f['provider_season_id'], 'utc_date': f['kickoff'], 'status': 'scheduled', 'is_neutral': False,
        'home_team': {'id': f['provider_home_team_id']}, 'away_team': {'id': f['provider_away_team_id']},
        'venue': {'name': 'Explicit stadium', 'city': 'Berlin', **({'latitude':52.5,'longitude':13.4} if venue else {})}}
        for f in data['fixtures']]
    source = raw.save('the_stats_api', 'current_targets', json.dumps({'data': matches}).encode(),
        'application/json', fetched_at=NOW-timedelta(minutes=2),
        source_url='https://api.thestatsapi.com/api/football/matches?status=scheduled')
    for f in data['fixtures']:
        f['fixture_source'] = {'provider':'the_stats_api','content_hash':source.content_hash,
            'source_url':source.source_url,'observed_at':(NOW-timedelta(minutes=2)).isoformat()}
    TemporalStore(db, root/'data/raw', clock=lambda:NOW-timedelta(seconds=8)).capture('raw', limit=100)
    store = WeatherStore(db);store.initialize()
    saved=[]
    for f in data['fixtures']:
        fid = 'api_football:'+f['provider_fixture_id'] if legacy else f['fixture_id']
        target=f['kickoff'];text=target[:16]
        payload={'latitude':52.49,'longitude':13.39,'utc_offset_seconds':0,
            'hourly':{'time':[text],'temperature_2m':[temperature],'wind_speed_10m':[20],
                      'precipitation':[.3],'relative_humidity_2m':[65]},
            'hourly_units':{'temperature_2m':'°C','wind_speed_10m':'m/s' if wrong_units else 'km/h',
                            'precipitation':'mm','relative_humidity_2m':'%'}}
        weather={'fixture_id':fid,'provider':'open_meteo','weather_kind':weather_kind,'status':'SOURCED',
            'observed_at':obs.isoformat(),'forecast_time':target,'latitude':52.5,'longitude':13.4,
            'temperature_c':temperature,'wind_speed_kmh':20,'precipitation_mm':.3,'relative_humidity_pct':65,
            'source_url':'https://api.open-meteo.com/v1/forecast?latitude=52.5&longitude=13.4&timezone=UTC'}
        if conflict=='utc':payload['utc_offset_seconds']=3600
        if conflict=='coordinates':
            weather['latitude']=53.5
            weather['source_url']=weather['source_url'].replace('52.5','53.5')
        if conflict=='value':weather['temperature_c']=13
        if conflict=='gust':
            weather['wind_gusts_kmh']=30
            payload['hourly']['wind_gusts_10m']=[30]
            payload['hourly_units']['wind_gusts_10m']='m/s'
        # Reanalysis cannot refer to a future target; it is deliberately for an
        # earlier kickoff and must never be borrowed as the current forecast.
        fixture={**f,'fixture_id':fid}
        if weather_kind=='REANALYSIS':
            fixture['kickoff']=(NOW-timedelta(hours=3)).isoformat()
            weather['forecast_time']=fixture['kickoff']
        saved.append(store.ingest(weather,fixture,source_run_id='synthetic-weather',raw_payload=payload))
    if witness:
        TemporalStore(db,root/'data/raw',clock=lambda:available or NOW-timedelta(seconds=5)).capture('weather',limit=100)
    return data,models,db,store,saved


def test_actual_source_hour_values_venue_and_availability_are_bound_without_effect(tmp_path,monkeypatch):
    data,_,db,_,_=environment(tmp_path)
    before=db.path.read_bytes()
    monkeypatch.setattr(socket,'socket',Mock(side_effect=AssertionError('NO_NETWORK')))
    result=inspect_native_weather(tmp_path,data['fixtures'],as_of=NOW)
    assert result['summary']=={'fixture_contexts':3,'verified_forecast_sources':3,'verified_venue_forecast_contexts':3}
    assert validate(result['dataset'])==result['dataset'] and db.path.read_bytes()==before
    for row in result['rows']:
        assert row['status']=='FORECAST_CONTEXT_VERIFIED_SHADOW'
        assert row['forecast']['temperature_c']==12 and row['forecast']['provider_issue_time'] is None
        assert row['venue_location']['latitude']==52.5 and row['forecast']['provider_grid_latitude']==52.49
        assert row['forecast_receipt']['receipt_id'] and row['target_receipt']['receipt_id']
        assert row['probability_adjustment']==0 and row['ranking_multiplier']==1
        assert not row['live_decision_effect']


@pytest.mark.parametrize('broken',['venue','weather_receipt','future_receipt','unit','stale_forecast',
                                 'legacy','reanalysis','stale_target','raw_hash','utc','coordinates','value','gust'])
def test_unverified_context_is_unknown_not_neutral_weather_or_positive_effect(tmp_path,broken):
    kwargs={}
    if broken=='venue':kwargs['venue']=False
    if broken=='weather_receipt':kwargs['witness']=False
    if broken=='future_receipt':kwargs['available']=NOW+timedelta(seconds=1)
    if broken=='unit':kwargs['wrong_units']=True
    if broken=='stale_forecast':kwargs['observed']=NOW-timedelta(hours=7)
    if broken=='legacy':kwargs['legacy']=True
    if broken=='reanalysis':kwargs['weather_kind']='REANALYSIS'
    if broken in ('utc','coordinates','value','gust'):kwargs['conflict']=broken
    data,_,_,_,_=environment(tmp_path,**kwargs)
    cutoff=NOW+timedelta(minutes=31) if broken=='stale_target' else NOW
    if broken=='raw_hash':
        source=data['fixtures'][0]['fixture_source'];source['content_hash']='a'*64
    result=inspect_native_weather(tmp_path,data['fixtures'],as_of=cutoff)
    row=result['rows'][0]
    assert row['status']=='UNKNOWN' and row['reason']
    assert result['live_decision_effect'] is False and result['ranking_multiplier']==1
    if broken=='venue':
        assert row['forecast']['temperature_c']==12  # source exists; venue link still unproven
        assert row['reason']=='UNPROVEN_NATIVE_VENUE_COORDINATES'


def test_newer_unknown_or_conflicting_forecast_cannot_restore_an_old_favourable_one(tmp_path):
    data,_,db,store,_=environment(tmp_path)
    f=data['fixtures'][0]
    store.ingest({'fixture_id':f['fixture_id'],'provider':'open_meteo','weather_kind':'FORECAST',
                  'status':'UNKNOWN','observed_at':(NOW-timedelta(minutes=1)).isoformat()},f,
                 source_run_id='later-missing',time_basis='LOOKUP_ATTEMPT_AT')
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW-timedelta(seconds=5)).capture('weather',limit=100)
    assert inspect_native_weather(tmp_path,data['fixtures'],as_of=NOW)['rows'][0]['status']=='UNKNOWN'


def test_same_time_conflicting_snapshots_are_not_selected_by_hash_order(tmp_path):
    data,_,db,store,_=environment(tmp_path)
    import sqlite3
    with sqlite3.connect(db.path) as connection:
        stored=json.loads(connection.execute('SELECT snapshot_json FROM sports_weather_snapshots LIMIT 1').fetchone()[0])
    weather=deepcopy(stored['weather']);raw=deepcopy(stored['raw_payload'])
    weather['temperature_c']=13;raw['hourly']['temperature_2m']=[13]
    store.ingest(weather,stored['fixture'],source_run_id='same-time-conflict',raw_payload=raw)
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW-timedelta(seconds=4)).capture('weather',limit=100)
    result=inspect_native_weather(tmp_path,data['fixtures'],as_of=NOW)
    row=next(r for r in result['rows'] if r['fixture_id']==weather['fixture_id'])
    assert row['status']=='UNKNOWN' and row['reason']=='AMBIGUOUS_LATEST_NATIVE_FORECAST'


def test_pipeline_binds_each_leg_to_context_and_preserves_all_expert_outputs(tmp_path,monkeypatch):
    data,models,_,_,_=environment(tmp_path)
    original=deepcopy((data,models));fit=Mock(side_effect=lambda *_a,**_k:{
        'numeric_models':deepcopy(models),'source_view':{'fixtures':deepcopy(data['fixtures']),
            'dataset':{'manifest':{'dataset_id':'1'*64}}}})
    monkeypatch.setattr(pipeline,'replay_native_history',fit)
    result=pipeline.replay_native_options(tmp_path,{},data,{},as_of=NOW)['options_shadow']
    weather=result['weather_review']
    assert fit.call_count==1 and (data,models)==original and weather['status']=='OBSERVED'
    assert weather['leg_bindings']>=3 and not weather['heldout_effect_validation_complete']
    for review in weather['option_reviews']:
        option=result['options'][review['profile_id']][review['option_index']]
        assert review['option_sha256']==canonical_sha256(option)
        for leg,binding in zip(option['recommendation']['legs'],review['legs'],strict=True):
            assert binding['leg_sha256']==canonical_sha256(leg)
            row=weather['source_audit']['rows'][binding['context_row_index']]
            assert binding['context_sha256']==canonical_sha256(row)
            assert binding['data_use']=='CONTEXT_ONLY_NO_VALIDATED_EFFECT'
            assert binding['ranking_multiplier']==1
    monkeypatch.setattr(weather_option_shadow,'inspect_native_weather',Mock(side_effect=ValueError('sensitive')))
    failed=pipeline.replay_native_options(tmp_path,{},data,{},as_of=NOW)['options_shadow']
    assert failed['weather_review']['status']=='UNAVAILABLE' and 'sensitive' not in str(failed)
    for key in ('cards','options','option_source_bindings','player_review','cup_review','xg_review','motivation_review'):
        assert failed[key]==result[key]


def test_weather_context_rechecked_at_completion_without_publication_authority(tmp_path):
    data,_,_,_,_=environment(tmp_path)
    review=weather_option_shadow.review_weather_options(tmp_path,data['fixtures'],{}, {},as_of=NOW)
    checks=weather_option_shadow.completion_checks(review,NOW)
    assert all(c['context_eligible_at_completion'] and not c['publication_eligible'] for c in checks)
    assert not any(c['context_eligible_at_completion'] for c in
        weather_option_shadow.completion_checks(review,NOW+timedelta(minutes=31)))


def test_boundaries_fail_before_database_work(tmp_path):
    with pytest.raises(ValueError,match='BOUNDED'):
        inspect_native_weather(tmp_path,[{}]*1001,as_of=NOW)
    data,*_=environment(tmp_path)
    with pytest.raises(ValueError,match='UNIQUE'):
        inspect_native_weather(tmp_path,data['fixtures']*2,as_of=NOW)
