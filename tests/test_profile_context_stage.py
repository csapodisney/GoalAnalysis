"""Producer stage boundaries: no shadow calls, no failed-review Daily overwrite."""
import json
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_astra_review import KEY, Transport, response
from test_candidate_context_review import NOW, assessed
from test_profile_context_review import card, prepared, quote, witnessed

from goal_analysis.agents.astra_review import AstraReviewer
from goal_analysis.jobs import profile_context_stage as stage
from goal_analysis.jobs.portfolio import _merge_astra_reviews


@pytest.fixture(autouse=True)
def mock_clock(monkeypatch):
    monkeypatch.setattr('goal_analysis.agents.astra_review._utcnow',lambda:NOW)


def inputs():
    first,other=quote('initial'),quote('extra','the_stats_api:mt_124')
    artifacts,_=witnessed([first]);sources=prepared([first,other])
    review=artifacts['candidate_review_response_batches'][0]['response']
    report={'tickets':[card([first],'daily223')],'recommendations':[card([other])],
        'astra':deepcopy(review)}
    return report,{},artifacts,sources,[first,other],review


def execute(values,reviewer,mode='ACTIVE',remaining=3,clock=None):
    return stage.execute(*values,mode=mode,reviewer=reviewer,target_date=NOW.date().isoformat(),
        feedback=None,maximum=30,remaining_rounds=remaining,clock=clock or (lambda:NOW),merge=_merge_astra_reviews)


def test_shadow_has_no_new_reviewer_calls_and_returns_identical_report_and_options():
    values=inputs();before=deepcopy((values[0],values[1],values[-1]))
    def forbidden(*_args,**_kwargs):pytest.fail('SHADOW may not call a model')
    report,options,review=execute(values,SimpleNamespace(review=forbidden),mode='SHADOW')
    assert (report,options,review)==before
    assert values[2]['profile_context_review_attempts']==[]
    assert [len(batch) for batch in values[2]['profile_context_review_plan']['batches']]==[1]


def test_actual_mock_wire_supplement_binds_generated_profile_without_changing_daily():
    values=inputs();daily=deepcopy(values[0]['tickets']);probability=values[0]['recommendations'][0]['legs'][0]['model_probability']
    calls=[]
    def actual_mock(packets,date,feedback=None):
        calls.append(deepcopy(packets))
        transport=Transport(response({'reviews':[assessed(packet) for packet in packets]}))
        return AstraReviewer(KEY,web_search=False,transport=transport).review(packets,date,feedback=feedback)
    report,_options,review=execute(values,SimpleNamespace(review=actual_mock))
    assert len(calls)==1 and calls[0][0]['candidate_id']=='extra'
    assert report['tickets']==daily and review['status']=='COMPLETE'
    assert len(review['reviews'])==2 and report['profile_context_review']['actual_bound_original_quotes']==2
    leg=report['recommendations'][0]['legs'][0]
    assert leg['model_probability']==probability
    assert leg['astra_review']['candidate_id']=='extra'
    assert report['recommendations'][0]['profile_context_review']['status']=='SOURCE_BOUND'


def test_failed_supplement_keeps_successful_primary_answers_and_actual_usage():
    values=inputs();primary=deepcopy(values[-1]);calls=[]
    def failed(*args,**kwargs):
        calls.append(1)
        return {'status':'FAILED','usage':{'requests':2,'input_tokens':50},'reviews':[]}
    report,_,review=execute(values,SimpleNamespace(review=failed))
    assert len(calls)==1 and review['status']=='COMPLETE'
    assert review['reviews']==primary['reviews']
    assert review['usage']['requests']==primary['usage']['requests']+2
    assert report['tickets']==values[0]['tickets']
    assert report['recommendations'][0]['status']=='DRAFT'
    assert values[2]['profile_context_review_attempts'][0]['exact_context_bindings_complete'] is False


def test_unknown_transport_usage_is_disclosed_not_fabricated_as_zero_http():
    def failed(*_a,**_k):raise OSError('synthetic network failure')
    values=inputs();report,_,review=execute(values,SimpleNamespace(review=failed))
    assert review['status']=='COMPLETE' and report['profile_context_review']['request_usage_unknown'] is True
    assert values[2]['profile_context_review_attempts'][0]['response_usage']=={}


def test_no_remaining_daily_followup_budget_means_no_generic_retry_or_call():
    def forbidden(*_a,**_k):pytest.fail('Existing followup budget exhausted')
    values=inputs();report,_,review=execute(values,SimpleNamespace(review=forbidden),remaining=0)
    assert review['status']=='COMPLETE' and report['recommendations'][0]['status']=='DRAFT'
    assert values[2]['profile_context_review_attempts']==[]


def test_model_only_run_starts_first_actual_review_without_invented_complete_primary():
    values=list(inputs())
    values[2]={'candidate_review_actual_packets':[]}
    values[-1]={'status':'UNAVAILABLE','reviews':[],'usage':{}}
    original=deepcopy(values[-1]);calls=[]
    def actual_mock(packets,date,feedback=None):
        calls.append(1)
        transport=Transport(response({'reviews':[assessed(packet) for packet in packets]}))
        return AstraReviewer(KEY,web_search=False,transport=transport).review(packets,date)
    report,_,review=execute(values,SimpleNamespace(review=actual_mock))
    assert len(calls)==1 and review['status']=='COMPLETE' and len(review['reviews'])==1
    assert original['status']=='UNAVAILABLE'
    assert report['recommendations'][0]['profile_context_review']['status']=='SOURCE_BOUND'


def test_failed_first_model_only_review_does_not_create_a_complete_response():
    values=list(inputs());values[2]={'candidate_review_actual_packets':[]}
    values[-1]={'status':'UNAVAILABLE','reviews':[],'usage':{}}
    report,_,review=execute(values,SimpleNamespace(review=lambda *_a,**_k:{'status':'FAILED','usage':{}}))
    assert review['status']=='UNAVAILABLE' and review['reviews']==[]
    assert report['recommendations'][0]['status']=='DRAFT'


@pytest.mark.parametrize('mode',['SHADOW','ACTIVE'])
def test_only_explicit_small_versioned_policy_is_accepted(tmp_path,mode):
    assert stage.policy(tmp_path)=='OFF'
    path=tmp_path/'config/profile-context-review.json';path.parent.mkdir()
    path.write_text(json.dumps({'version':'profile-context-review-v1','mode':mode}))
    assert stage.policy(tmp_path)==mode
    path.write_text(json.dumps({'version':'profile-context-review-v1','mode':mode,'raise_budget':True}))
    with pytest.raises(ValueError,match='INVALID_PROFILE_CONTEXT_POLICY'):stage.policy(tmp_path)


def test_portfolio_producer_invokes_stage_after_source_prepare_before_final_allocation(tmp_path,monkeypatch):
    from test_portfolio_flow import NOW as FLOW_NOW
    from test_portfolio_flow import RecordedReviewer, environment

    from goal_analysis.jobs import candidate_context_review, portfolio
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger

    cfg,football,odds,cache,*_=environment(tmp_path)
    ledger=PortfolioLedger(tmp_path/'data/arthur/ledger.sqlite3',now=lambda:FLOW_NOW)
    monkeypatch.setattr(candidate_context_review,'policy',lambda _root:'ACTIVE')
    frozen={'as_of':FLOW_NOW.isoformat(),'contexts':{},'collection':{},'weather_by_fixture':{}}
    monkeypatch.setattr(candidate_context_review,'prepare',lambda *_a,**_k:deepcopy(frozen))
    monkeypatch.setattr(stage,'policy',lambda _root:'SHADOW')
    events=[]
    original=stage.execute
    def recorded(report,options,artifacts,prepared,*args,**kwargs):
        assert report['numeric_models'] is not None and prepared==frozen
        events.append('profile_context_stage')
        result=original(report,options,artifacts,prepared,*args,**kwargs)
        assert result[0]==report and result[1]==options
        return result
    monkeypatch.setattr(stage,'execute',recorded)
    reviewer=RecordedReviewer()
    bundle=portfolio.run_portfolio(cfg,{},football,odds,cache,FLOW_NOW.date(),reviewer,ledger,clock=lambda:FLOW_NOW)
    assert events==['profile_context_stage'] and len(reviewer.calls)==1
    assert bundle['artifacts']['profile_context_review_attempts']==[]
    assert bundle['artifacts']['report']['tickets'][0]['profile_id']=='daily223'


def test_empty_initial_shortlist_prepares_shared_context_for_independent_model_review(tmp_path,monkeypatch):
    from test_model_integration import predictor_fixture
    from test_portfolio_flow import NOW as FLOW_NOW
    from test_portfolio_flow import RecordedReviewer, environment

    from goal_analysis.engine import recommendations
    from goal_analysis.jobs import candidate_context_review, portfolio
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger

    cfg,football,odds,cache,*_=environment(tmp_path)
    ledger=PortfolioLedger(tmp_path/'data/arthur/ledger.sqlite3',now=lambda:FLOW_NOW)
    calls=[];primary=[]
    monkeypatch.setattr(candidate_context_review,'policy',lambda _root:'ACTIVE')
    monkeypatch.setattr(stage,'policy',lambda _root:'ACTIVE')
    monkeypatch.setattr(portfolio,'_review_packets',lambda *_a,**_k:[])
    monkeypatch.setattr(recommendations,'recommendation_packets',lambda *_a,**_k:[])
    def prepare(*args,**kwargs):
        primary.append(deepcopy(args[2]))
        return {'as_of':FLOW_NOW.isoformat(),'contexts':{},'collection':{},'weather_by_fixture':{}}
    monkeypatch.setattr(candidate_context_review,'prepare',prepare)
    def execute_without_transport(report,options,artifacts,prepared,quotes,review,**kwargs):
        assert review['status']=='UNAVAILABLE' and artifacts['candidate_review_actual_packets']==[]
        calls.append([card['profile_id'] for card in report['recommendations']])
        return report,options,review
    monkeypatch.setattr(stage,'execute',execute_without_transport)
    reviewer=RecordedReviewer()
    portfolio.run_portfolio(cfg,{'enabled_profiles':['daily223','gauss']},football,odds,cache,FLOW_NOW.date(),
        reviewer,ledger,clock=lambda:FLOW_NOW,model_predictor=predictor_fixture)
    assert len(calls)==1 and 'gauss' in calls[0] and primary==[[]] and reviewer.calls==[]


def test_elapsed_profile_review_cannot_leave_a_stale_publication_clock(tmp_path,monkeypatch):
    from test_portfolio_flow import NOW as FLOW_NOW
    from test_portfolio_flow import RecordedReviewer, environment

    from goal_analysis.jobs import candidate_context_review, portfolio
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger

    cfg,football,odds,cache,*_=environment(tmp_path);clock=[FLOW_NOW]
    ledger=PortfolioLedger(tmp_path/'data/arthur/ledger.sqlite3',now=lambda:clock[0])
    monkeypatch.setattr(candidate_context_review,'policy',lambda _root:'ACTIVE')
    monkeypatch.setattr(stage,'policy',lambda _root:'SHADOW')
    monkeypatch.setattr(candidate_context_review,'prepare',lambda *_a,**_k:{
        'as_of':FLOW_NOW.isoformat(),'contexts':{},'collection':{},'weather_by_fixture':{}})
    def elapsed(report,options,artifacts,*_a,**kwargs):
        clock[0]=FLOW_NOW+timedelta(days=1)
        return report,options,report['astra']
    monkeypatch.setattr(stage,'execute',elapsed)
    report=portfolio.run_portfolio(cfg,{},football,odds,cache,FLOW_NOW.date(),RecordedReviewer(),ledger,
        clock=lambda:clock[0])['artifacts']['report']
    assert report['tickets']==[]
    assert report['finished_at']==clock[0].isoformat()
