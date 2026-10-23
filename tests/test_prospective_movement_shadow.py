"""Actual local source/receipt stores; controlled transports are synthetic."""
import gc
import socket
import sqlite3
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from unittest.mock import Mock

import pytest
from test_prospective_odds import NOW
from test_prospective_odds import env as source_environment

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import prospective_movement_shadow as job
from goal_analysis.jobs.odds_monitor import OddsMonitor, monitor_history
from goal_analysis.jobs.odds_monitor_providers import build_monitor_queries, fetch_monitor_query
from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient


@pytest.fixture
def env(tmp_path,monkeypatch):
    return source_environment.__wrapped__(tmp_path,monkeypatch)


def captured(env):
    root,clock,transport,cfg,_,run=env
    result=run()
    rows=monitor_history(root,clock[0],shadow=True)
    quotes=[q for row in rows for q in row['odds_history_snapshot']['quotes']]
    return root,clock,transport,cfg,result['fixtures'],quotes


def current(env,hours=25,*,rise=True):
    root,clock,transport,cfg,fixtures,old=captured(env)
    clock[0]=NOW+timedelta(hours=hours)
    def shape(url,headers,**kwargs):
        body,usage,status=transport(url,headers,**kwargs)
        if rise and url.endswith('/odds'):
            body['data']['bookmakers'][0]['markets']['match_odds']['home']['last_seen']=4.0
        return body,usage,status
    client=TheStatsPrimaryClient(root,api_key='recorded-test-only',transport=shape,
        clock=lambda:clock[0],max_requests=4)
    quotes=[]
    for q in build_monitor_queries(fixtures,clock[0],{**cfg,'monitor_providers':['the_stats_api']}):
        quotes.extend(fetch_monitor_query(q,football_client=client,now=clock[0])['quotes'])
    return root,clock,fixtures,quotes,old


def test_real_early_source_consumed_without_inventing_elapsed_time_or_mutating_any_inputs(env,monkeypatch):
    root,clock,_,_,fixtures,quotes=captured(env)
    before=deepcopy([fixtures,quotes])
    monkeypatch.setattr(socket,'socket',Mock(side_effect=AssertionError('NO_NETWORK')))
    result=job.review_prospective_movement(root,fixtures,quotes,{}, {},as_of=clock[0])
    assert result['status']=='USED_REVIEW' and result['history']['verified_quote_occurrences']==len(quotes)>0
    assert all(q['odds_movement']['status']=='INSUFFICIENT_HISTORY' for q in result['quote_assessments'])
    assert result['actual_elapsed_24h_pairs']==0 and [fixtures,quotes]==before
    assert result['production_writes']==result['additional_model_fits']==result['external_requests']==0
    assert result['new_grok_calls']==0 and result['live_promoted'] is False
    assert not (root/'data/arthur/odds-monitor.sqlite3').exists()


def test_genuine_distinct_receipts_prove_actual_25h_same_product_movement(env):
    root,clock,fixtures,quotes,_=current(env)
    result=job.review_prospective_movement(root,fixtures,quotes,{}, {},as_of=clock[0])
    assert result['status']=='USED_REVIEW' and result['actual_elapsed_24h_pairs']>0
    homes=[q for q in result['quote_assessments'] if q['odds_movement']['identity']['market_key']=='h2h'
           and q['odds_movement']['identity']['selection_key']=='home']
    assert homes and all(q['odds_movement']['status']=='VERIFIED_RISE' for q in homes)
    assert all(q['odds_movement']['window_hours']==25 for q in homes)
    assert all('baseline_quoted_at' not in q['odds_movement'] for q in homes)
    assert all(q['baseline_receipt']['receipt_id'] != q['current_receipt']['receipt_id'] for q in homes)


def test_24h_decision_baseline_age_does_not_fake_24h_between_observations(env):
    root,_clock,fixtures,quotes,_=current(env,hours=23.99)
    cutoff=NOW+timedelta(hours=24.01)
    result=job.review_prospective_movement(root,fixtures,quotes,{}, {},as_of=cutoff)
    assert result['actual_elapsed_24h_pairs']==0
    assert result['quote_assessments']
    assert all(q['odds_movement']['status']=='INSUFFICIENT_ELAPSED_HISTORY' for q in result['quote_assessments'])


@pytest.mark.parametrize('hours',[24,48])
def test_exact_24h_boundary_is_admitted_but_post_kickoff_cannot_create_48h_pair(env,hours):
    root,clock,fixtures,quotes,_=current(env,hours=hours)
    # At48h this genuine +40h fixture has already started. Lockout prevents a
    # current quote; never edit the historical kickoff to manufacture a pair.
    if hours==48:
        assert not quotes
        result=job.review_prospective_movement(root,fixtures,quotes,{}, {},as_of=clock[0])
        assert result['actual_elapsed_24h_pairs']==0
        return
    result=job.review_prospective_movement(root,fixtures,quotes,{}, {},as_of=clock[0])
    assert result['actual_elapsed_24h_pairs']>0


@pytest.mark.parametrize('change',['price','period','market','book','clock','kickoff','team','quoted_at','duplicate','stale','lockout'])
def test_forged_or_unavailable_current_source_cannot_create_positive_review(env,change):
    root,clock,fixtures,quotes,_=current(env)
    q=deepcopy(quotes[0]);fixtures=deepcopy(fixtures)
    if change=='price':q['decimal_price']=999.
    elif change=='period':q['period']='FIRST_HALF'
    elif change=='market':q['provider_market_id']='invented'
    elif change=='book':q['provider_bookmaker']='Invented'
    elif change=='clock':q['quote_observed_at']=(clock[0]-timedelta(seconds=1)).isoformat()
    elif change=='kickoff':q['kickoff']=(clock[0]+timedelta(hours=2)).isoformat()
    elif change=='team':q['home_team']='Invented'
    elif change=='quoted_at':q['quoted_at']=clock[0].isoformat()
    elif change=='stale':clock[0]+=timedelta(minutes=31)
    elif change=='lockout':clock[0]=NOW+timedelta(hours=40,minutes=-15)
    values=[q,q] if change=='duplicate' else [q]
    result=job.review_prospective_movement(root,fixtures,values,{}, {},as_of=clock[0])
    assert not result['quote_assessments'] and result['actual_elapsed_24h_pairs']==0


def test_original_live_store_not_read_or_written_and_new_history_scope_is_explicit(env):
    root,clock,_,_,fixtures,quotes=captured(env)
    live=OddsMonitor(root);live._connect().close()
    before=sha256(live.path.read_bytes()).hexdigest()
    result=job.review_prospective_movement(root,fixtures,quotes,{}, {},as_of=clock[0])
    assert result['history']['verified_quote_occurrences']>0
    assert monitor_history(root,clock[0])==[] and sha256(live.path.read_bytes()).hexdigest()==before


def test_earlier_prefix_never_admits_later_source_receipts(env):
    root,_clock,_,_,fixtures,quotes=captured(env)
    result=job.review_prospective_movement(root,fixtures,quotes,{}, {},as_of=NOW-timedelta(microseconds=1))
    assert not result['quote_assessments'] and not result['history']['source_receipts']


@pytest.mark.parametrize('breakage',['hash','source','receipt','bytes'])
def test_compact_and_original_source_corruption_is_rejected(env,breakage):
    root,clock,_,_,fixtures,quotes=captured(env)
    dbpath=root/'data/arthur/prospective-odds/shadow-monitor.sqlite3'
    if breakage=='hash':
        with sqlite3.connect(dbpath) as db:
            db.execute("update monitor_snapshots set snapshot_id=? where snapshot_id=(select snapshot_id from monitor_snapshots limit 1)",('0'*64,))
    elif breakage in {'source','receipt'}:
        path=root/'data/goal_analysis.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('drop table '+('raw_snapshots' if breakage=='source' else 'the_stats_http_witnesses'))
    else:
        with sqlite3.connect(root/'data/goal_analysis.sqlite3') as db:
            paths=[r[0] for r in db.execute('select relative_path from raw_snapshots where content_hash=?',(quotes[0]['quote_source_sha256'],))]
        for path in paths:(root/'data/raw'/path).write_bytes(b'{}')
    result=job.review_prospective_movement(root,fixtures,quotes,{}, {},as_of=clock[0])
    assert result['actual_elapsed_24h_pairs']==0
    if breakage in {'hash','source'}:assert result['status']=='UNAVAILABLE'
    elif breakage=='receipt':assert result['status']=='MISSING' and not result['quote_assessments']
    else:assert all(q['candidate_id']!=quotes[0]['candidate_id'] for q in result['quote_assessments'])


@pytest.mark.parametrize('limit',[0,1])
def test_partial_history_read_cannot_silently_become_a_positive_baseline(env,monkeypatch,limit):
    root,clock,fixtures,quotes,_=current(env)
    monkeypatch.setattr(job,'MAX_SNAPSHOTS',limit)
    result=job.review_prospective_movement(root,fixtures,quotes,{}, {},as_of=clock[0])
    assert result['status']=='UNAVAILABLE' and not result['quote_assessments']


def test_option_leg_gets_source_and_existing_prediction_binding_without_modifying_card(env):
    root,clock,fixtures,quotes,_=current(env)
    q=quotes[0];leg={'fixture_id':q['fixture_id'],'source_candidate_id':q['candidate_id'],'model_probability':.6}
    models={'gauss':{'predictions':[{'fixture_id':q['fixture_id'],'marker':'existing-shared-fit'}]}}
    options={'gauss':[{'recommendation':{'legs':[leg]}}]};before=deepcopy((models,options))
    result=job.review_prospective_movement(root,fixtures,[q],models,options,as_of=clock[0])
    binding=result['option_leg_bindings'][0]
    assert binding['status']=='USED_REVIEW' and binding['leg_sha256']==canonical_sha256(leg)
    assert binding['existing_native_prediction_sha256']==canonical_sha256(models['gauss']['predictions'][0])
    assert binding['assessment']['current_receipt']['receipt_id'] and (models,options)==before


@pytest.mark.parametrize('fail',[False,True])
def test_parent_calls_review_on_same_fit_and_keeps_original_option_cards(env,monkeypatch,fail):
    from test_native_options_shadow import NOW as PARENT_NOW
    from test_native_options_shadow import setup

    from goal_analysis.jobs import native_options_shadow as parent
    root=env[0]
    data,models,fit=setup(root/'parent',monkeypatch)
    seen=[]
    def review(_root,fixtures,candidates,actual_models,options,**kwargs):
        assert actual_models==models
        seen.append(deepcopy(options))
        if fail:
            raise ValueError('SYNTHETIC_OBSERVER_FAILURE')
        return {'status':'MISSING','actual_elapsed_24h_pairs':0}
    monkeypatch.setattr(parent,'review_prospective_movement',review)
    result=parent.replay_native_options(root/'parent',{},data,{},as_of=PARENT_NOW)
    assert fit.call_count==1 and len(seen)==1
    assert result['options_shadow']['options']==seen[0]
    assert result['options_shadow']['odds_movement_review']['status']==('UNAVAILABLE' if fail else 'MISSING')


@pytest.mark.parametrize('field,limit',[('MAX_BYTES',1),('MAX_QUOTES',1),('MAX_SOURCES',0)])
def test_read_budget_exhaustion_is_explicit_and_cannot_reuse_partial_positive_results(env,monkeypatch,field,limit):
    root,clock,fixtures,quotes,_=current(env)
    monkeypatch.setattr(job,field,limit)
    result=job.review_prospective_movement(root,fixtures,quotes,{}, {},as_of=clock[0])
    assert result['status']=='UNAVAILABLE' and result['actual_elapsed_24h_pairs']==0
    assert not result['quote_assessments'] and not result['option_leg_bindings']


def test_missing_history_is_explicit_and_never_creates_a_database(env):
    root,clock,_,_,fixtures,quotes=captured(env)
    # Existing monitor test helpers retain connection cycles until collection.
    gc.collect()
    path=root/'data/arthur/prospective-odds/shadow-monitor.sqlite3';path.unlink()
    result=job.review_prospective_movement(root,fixtures,quotes,{}, {},as_of=clock[0])
    assert result['gaps']['MISSING_PROSPECTIVE_HISTORY']==1
    assert result['history']['verified_quote_occurrences']==result['actual_elapsed_24h_pairs']==0
    assert not path.exists()
