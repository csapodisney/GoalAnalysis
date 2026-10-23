"""Physical calendar/XI witnesses -> exact research-leg context; no extra fit."""
import json
import socket
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock
from urllib.parse import urlencode

import pytest
from test_xg_specialist_shadow import NOW
from test_xg_specialist_shadow import environment as base

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import native_options_shadow as pipeline
from goal_analysis.jobs import rotation_option_shadow as module
from goal_analysis.jobs.schedule_shadow import calendar_query
from goal_analysis.storage import SnapshotStore
from goal_analysis.storage.temporal import TemporalStore


def environment(root, *, fault=None):
    data,models,db,_=base(root,history_teams=[])
    store=SnapshotStore(db,root/'data/raw');saved=[]
    observed=NOW-timedelta(minutes=2)
    def save(body,url,when=observed):
        row=store.save('the_stats_api','rotation_test',json.dumps(body).encode(),'application/json',
            fetched_at=when,source_url='https://api.thestatsapi.com/api/football/'+url)
        saved.append(row);return row
    def lineups(m,shift=0,current=False):
        value={'match_id':m['id'],'type':'team_sheet' if current else 'played','confirmed':True}
        if fault=='predicted' and current:value.update(type='predicted',confirmed=False)
        for side in ('home','away'):
            tid=m[side+'_team']['id'];n=int(tid.removeprefix('tm_'))
            value[side]={'id':tid,'starting_xi':[{'id':f'pl_{n*100+i+shift}'} for i in range(1,12)]}
        when=NOW-timedelta(hours=3) if fault=='stale_xi' and current else observed
        if fault=='near_xi' and current:when=NOW-timedelta(hours=1,minutes=59)
        save({'data':value},'matches/'+m['id']+'/lineups',when)
    for i,f in enumerate(data['fixtures']):
        current={'id':f['provider_fixture_id'],'competition_id':f['provider_league_id'],
            'season_id':f['provider_season_id'],'utc_date':f['kickoff'],'status':'scheduled',
            'home_team':{'id':f['provider_home_team_id']},'away_team':{'id':f['provider_away_team_id']}}
        lineups(current,3,current=True)
        for side in ('home','away'):
            team=f['provider_'+side+'_team_id'];n=int(team.removeprefix('tm_'))
            previous={**current,'id':f'mt_{100+n}','utc_date':(NOW-timedelta(days=2)).isoformat(),
                'status':'finished','home_team':{'id':team},'away_team':{'id':f'tm_{900+n}'}}
            lineups(previous)
            following={**previous,'id':f'mt_{200+n}','status':'scheduled',
                'utc_date':(NOW+timedelta(days=3)).isoformat()}
            query=calendar_query(team,f['kickoff'])
            if fault=='filtered':query['status']='finished'
            meta={'page':1,'per_page':100,'total_pages':2 if fault=='pagination' else 1,'total':3}
            when=NOW-timedelta(hours=7) if fault=='stale_calendar' else observed
            if fault=='near_calendar':when=NOW-timedelta(hours=5,minutes=59)
            save({'data':[previous,current,following],'meta':meta},'matches?'+urlencode(query),when)
    if fault!='receipt':
        available=NOW+timedelta(seconds=1) if fault=='future_receipt' else NOW-timedelta(seconds=5)
        TemporalStore(db,root/'data/raw',clock=lambda:available).capture('raw',limit=1000)
    if fault=='bytes':
        path=root/'data/raw'/saved[-1].relative_path;path.write_bytes(path.read_bytes()+b' ')
    if fault=='target':data['fixtures'][0]['fixture_source']['content_hash']='a'*64
    if fault=='identity':data['fixtures'][0]['provider_home_team_id']='tm_999'
    options={'p':[{'recommendation':{'profile_id':'p','legs':deepcopy(data['candidates'])}}]}
    specialists={name:{'candidate_legs':deepcopy(data['candidates'][:1])}
                 for name in ('player','cup','xg','motivation')}
    return data,models,db,options,specialists


def test_actual_calendar_and_confirmed_xi_context_bound_to_all_leg_kinds_without_effect(tmp_path,monkeypatch):
    data,_,db,options,specialists=environment(tmp_path);before=deepcopy((data,options,specialists))
    def stored_rows():
        with closing(sqlite3.connect(db.path.resolve().as_uri()+'?mode=ro',uri=True)) as connection:
            return tuple(connection.iterdump())
    rows_before=stored_rows();connect=sqlite3.connect
    def readonly(path,*args,**kwargs):
        assert '?mode=ro' in str(path) and kwargs.get('uri') is True
        return connect(path,*args,**kwargs)
    monkeypatch.setattr(sqlite3,'connect',readonly)
    monkeypatch.setattr(socket,'socket',Mock(side_effect=AssertionError('NO_NETWORK')))
    result=module.review_rotation_options(tmp_path,data['fixtures'],options,specialists,as_of=NOW)
    assert result['status']=='OBSERVED',result
    assert result['summary']=={'fixture_contexts':3,'calendar_teams_verified':6,'xi_comparison_teams':6}
    assert result['leg_bindings']==7 and stored_rows()==rows_before
    assert (data,options,specialists)==before
    for row in result['contexts']:
        assert row['target_receipt']['receipt_id'] and len(row['source_envelopes'])==5
        assert row['rotation_probability'] is row['rotation_cause'] is row['team_strength_effect'] is None
        assert all(t['starting_xi_changes']==3 for t in row['teams'])
    for binding in result['option_reviews'][0]['legs']:
        row=result['contexts'][binding['context_row_index']]
        assert binding['context_sha256']==canonical_sha256(row) and binding['feature_dataset_id']==row['feature_dataset_id']
        assert binding['data_use']=='USED_REVIEW' and binding['ranking_multiplier']==1
        assert not binding['live_decision_effect']


@pytest.mark.parametrize('fault',['filtered','pagination','stale_calendar','receipt','future_receipt','target','identity'])
def test_partial_unwitnessed_future_or_identity_conflicts_never_become_confirmed_context(tmp_path,fault):
    data,_,_,options,specialists=environment(tmp_path,fault=fault)
    result=module.review_rotation_options(tmp_path,data['fixtures'],options,specialists,as_of=NOW)
    assert result['status']=='OBSERVED',result
    assert result['contexts'][0]['status']=='UNKNOWN' and result['contexts'][0]['calendar_teams_verified']==0
    assert result['option_reviews'][0]['legs'][0]['data_use']=='MISSING'
    assert result['ranking_multiplier']==1 and result['probability_adjustment']==0


@pytest.mark.parametrize('fault',['predicted','stale_xi'])
def test_available_calendar_survives_missing_confirmed_xi_without_invented_rotation(tmp_path,fault):
    data,_,_,options,specialists=environment(tmp_path,fault=fault)
    result=module.review_rotation_options(tmp_path,data['fixtures'],options,specialists,as_of=NOW)
    assert result['summary']['calendar_teams_verified']==6 and result['summary']['xi_comparison_teams']==0
    assert all(t['starting_xi_changes'] is None for r in result['contexts'] for t in r['teams'])


def test_corrupt_one_team_calendar_preserves_the_other_without_zero_rest_assumption(tmp_path):
    data,_,_,options,specialists=environment(tmp_path,fault='bytes')
    result=module.review_rotation_options(tmp_path,data['fixtures'],options,specialists,as_of=NOW)
    assert result['summary']['calendar_teams_verified']==5
    partial=result['contexts'][-1]
    assert partial['calendar_teams_verified']==1 and partial['xi_comparison_teams']==1
    assert partial['teams'][1]['workload_indicators'] is None


def test_completion_rechecks_target_calendar_xi_expiry_and_actual_leg_bindings(tmp_path):
    data,_,_,options,specialists=environment(tmp_path)
    result=module.review_rotation_options(tmp_path,data['fixtures'],options,specialists,as_of=NOW)
    a=module.completion_checks(result,NOW,options=options,specialists=specialists)
    assert a['leg_bindings_current'] and all(c['xi_context_eligible_at_completion'] for c in a['context_checks'])
    assert all(not c['publication_eligible'] for c in a['context_checks'])
    late=module.completion_checks(result,NOW+timedelta(minutes=31),options=options,specialists=specialists)
    assert not any(c['calendar_context_eligible_at_completion'] for c in late['context_checks'])
    changed=deepcopy(options);changed['p'][0]['recommendation']['legs'][0]['decimal_price']=9
    broken=module.completion_checks(result,NOW,options=changed,specialists=specialists)
    assert not broken['leg_bindings_current'] and not any(c['calendar_context_eligible_at_completion'] for c in broken['context_checks'])


@pytest.mark.parametrize('fault',['near_calendar','near_xi'])
def test_completion_expiry_distinguishes_calendar_from_current_xi_with_fresh_target(tmp_path,fault):
    data,_,_,options,specialists=environment(tmp_path,fault=fault)
    result=module.review_rotation_options(tmp_path,data['fixtures'],options,specialists,as_of=NOW)
    assert result['summary']['calendar_teams_verified']==result['summary']['xi_comparison_teams']==6
    checks=module.completion_checks(result,NOW+timedelta(minutes=2),options=options,specialists=specialists)
    assert checks['leg_bindings_current']
    assert all(c['calendar_context_eligible_at_completion']==(fault=='near_xi') for c in checks['context_checks'])
    assert not any(c['xi_context_eligible_at_completion'] for c in checks['context_checks'])


def test_daily_pipeline_uses_one_fit_and_context_failure_isolated_from_every_other_branch(tmp_path,monkeypatch):
    data,models,_,_,_=environment(tmp_path)
    fit=Mock(side_effect=lambda *_a,**_k:{'numeric_models':deepcopy(models),
        'source_view':{'fixtures':deepcopy(data['fixtures']),'dataset':{'manifest':{'dataset_id':'1'*64}}}})
    monkeypatch.setattr(pipeline,'replay_native_history',fit)
    result=pipeline.replay_native_options(tmp_path,{},data,{},as_of=NOW)['options_shadow']
    assert fit.call_count==1 and result['rotation_review']['status']=='OBSERVED'
    assert result['rotation_review']['leg_bindings']>=3
    monkeypatch.setattr(module,'inspect_rotation',Mock(side_effect=ValueError('private detail')))
    failed=pipeline.replay_native_options(tmp_path,{},data,{},as_of=NOW)['options_shadow']
    assert failed['rotation_review']['status']=='UNAVAILABLE' and 'private detail' not in str(failed)
    for key in ('cards','options','option_source_bindings','player_review','cup_review','xg_review','motivation_review','weather_review'):
        assert failed[key]==result[key]


@pytest.mark.parametrize('fault',['dataset','teams','causal_effect'])
def test_modified_reader_dataset_or_unvalidated_effect_is_not_accepted(tmp_path,monkeypatch,fault):
    data,_,_,options,specialists=environment(tmp_path)
    original=module.inspect_rotation(tmp_path,data['fixtures'],as_of=NOW)
    if fault=='dataset':original['dataset']['rows'][0]['fixture_id']='mt_999'
    elif fault=='teams':original['features'][0]['teams'][0]['team_id']='tm_999'
    else:original['features'][0]['teams'][0]['rotation_probability']=.9
    monkeypatch.setattr(module,'inspect_rotation',lambda *a,**k:original)
    result=module.review_rotation_options(tmp_path,data['fixtures'],options,specialists,as_of=NOW)
    assert result['status']=='UNAVAILABLE' and not result['live_decision_effect']


def test_bounded_input_and_unavailable_context_have_no_publication_authority(tmp_path,monkeypatch):
    read=Mock(side_effect=AssertionError('NO_READ'));monkeypatch.setattr(module,'inspect_rotation',read)
    result=module.review_rotation_options(tmp_path,[{}]*1001,{}, {},as_of=NOW)
    assert result['status']=='UNAVAILABLE' and read.call_count==0
    assert not module.completion_checks(result,NOW,options={},specialists={})['publication_eligible']
