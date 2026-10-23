"""Real cache/raw/HTTP/receipt/quota plumbing with synthetic provider responses."""
import json
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from test_xg_specialist_shadow import NOW, environment

from goal_analysis.jobs import calendar_collection_shadow as module
from goal_analysis.jobs import daily_native_shadow
from goal_analysis.jobs.the_stats_context import Resources
from goal_analysis.normalization.training_datasets import validate
from goal_analysis.providers.the_stats_api import TheStatsApiClient, TheStatsApiUsage
from goal_analysis.storage.provider_quota import SharedQuota


def setup(root,monkeypatch,*,fault=None,quota=True,expired=False):
    data,_,db,_=environment(root,history_teams=[])
    calls=[]
    def transport(url,headers,**kwargs):
        assert headers['Authorization']=='Bearer synthetic-calendar-test'
        assert kwargs['timeout']==3
        query=parse_qs(urlsplit(url).query);calls.append(query)
        assert set(query)=={'team_id','date_from','date_to','page','per_page','utc_offset'}
        team=query['team_id'][0]
        rows=[{'id':f['provider_fixture_id'],'competition_id':f['provider_league_id'],
               'season_id':f['provider_season_id'],'home_team':{'id':f['provider_home_team_id']},
               'away_team':{'id':f['provider_away_team_id']},'status':'scheduled',
               'utc_date':f['kickoff']} for f in data['fixtures'] if team in
              (f['provider_home_team_id'],f['provider_away_team_id'])]
        body={'data':rows,'meta':{'page':1,'per_page':100,'total':len(rows),'total_pages':2 if fault=='pagination' else 1}}
        status=429 if fault=='quota' else 200
        if fault=='quota':body={'error':{'code':'RATE_LIMITED'}}
        h={'X-RateLimit-Limit':'120','X-RateLimit-Remaining':str(119-len(calls)),
           'X-RateLimit-Reset':str(int((NOW+timedelta(minutes=1)).timestamp())),
           'X-Monthly-Quota-Limit':'100000','X-Monthly-Quota-Remaining':'90000',
           'X-Monthly-Quota-Reset':str(int((NOW+timedelta(days=30)).timestamp()))}
        return body,h,status
    def factory(root,**kwargs):
        return TheStatsApiClient('synthetic-calendar-test',transport=transport,**kwargs)
    monkeypatch.setattr(module,'TheStatsPrimaryClient',factory)
    if quota:
        q=SharedQuota(root,kind='daily',clock=lambda:NOW-timedelta(minutes=2) if expired else NOW)
        identifier=q.begin();observed=NOW-timedelta(minutes=2) if expired else NOW
        q.finish(identifier,{'authenticated':True,'http_status':200,'observed_at':observed.isoformat()},
            TheStatsApiUsage(minute_limit=120,minute_remaining=119,
                minute_reset=int((observed+timedelta(minutes=1)).timestamp()),monthly_limit=100000,
                monthly_remaining=90000,monthly_reset=int((NOW+timedelta(days=30)).timestamp())))
    return data['fixtures'],calls,db,factory


def run(root,fixtures,**kwargs):
    return module.collect(root,fixtures,clock=lambda:NOW,**kwargs)


def test_actual_resource_pipeline_collects_complete_calendars_without_input_or_decision_mutation(tmp_path,monkeypatch):
    fixtures,calls,_,_=setup(tmp_path,monkeypatch);before=deepcopy(fixtures)
    result=run(tmp_path,fixtures)
    assert result['status']=='COMPLETE' and len(calls)==6
    assert result['calendar_counts_at_completion']=={'AVAILABLE_SHADOW':6}
    assert result['counts']=={'VERIFIED_CALENDAR_SOURCE':6}
    assert result['authenticated_requests']==result['external_requests']==6
    assert result['model_fits']==result['llm_requests']==result['ledger_writes']==0
    assert not result['live_decision_effect'] and fixtures==before
    assert all(r['source']['http_observation_status']=='VERIFIED' for r in result['records'])
    validate(result['dataset'])


def test_deterministic_round_robin_request_limit_is_not_first_fixture_drained(tmp_path,monkeypatch):
    fixtures,calls,_,_=setup(tmp_path,monkeypatch)
    result=run(tmp_path,list(reversed(fixtures)),max_requests=2)
    plan=module._plan(fixtures)
    assert [c['team_id'][0] for c in calls]==[q['team_id'] for _,q in plan[:2]]
    assert plan[0][0]['fixture_id']!=plan[1][0]['fixture_id']
    assert result['status']=='GAPS' and result['counts']['REQUEST_BUDGET']==4


def test_plan_deduplicates_team_and_date_window_but_retains_distinct_windows():
    base={'fixture_id':'a','kickoff':(NOW+timedelta(hours=8)).isoformat(),
          'provider_home_team_id':'tm_1','provider_away_team_id':'tm_2'}
    other={**base,'fixture_id':'b','provider_away_team_id':'tm_3'}
    assert len(module._plan([base,other]))==3
    later={**base,'fixture_id':'c','kickoff':(NOW+timedelta(days=2)).isoformat()}
    assert len(module._plan([base,later]))==4


def test_cache_first_has_zero_new_requests_even_without_shared_quota(tmp_path,monkeypatch):
    fixtures,calls,_,factory=setup(tmp_path,monkeypatch)
    resources=Resources(tmp_path,factory(tmp_path,max_requests=6,timeout=3,clock=lambda:NOW),lambda:NOW,6)
    for _,query in module._plan(fixtures):resources.get('/football/matches',query)
    assert len(calls)==6
    calls.clear()
    result=run(tmp_path,fixtures,max_requests=0)
    assert result['external_requests']==0 and not calls
    assert result['usage']['cache_hits']==6 and result['status']=='COMPLETE'


def test_cache_prepass_covers_later_fixture_before_spending_small_network_budget(tmp_path,monkeypatch):
    fixtures,calls,_,factory=setup(tmp_path,monkeypatch)
    _,query=module._plan(fixtures)[-1]
    resources=Resources(tmp_path,factory(tmp_path,max_requests=1,timeout=3,clock=lambda:NOW),lambda:NOW,1)
    resources.get('/football/matches',query);calls.clear()
    result=run(tmp_path,fixtures,max_requests=1)
    assert len(calls)==1 and result['usage']['cache_hits']==1
    assert result['counts']['VERIFIED_CALENDAR_SOURCE']==2


def test_unknown_quota_is_explicit_and_never_bootstraps_a_request(tmp_path,monkeypatch):
    fixtures,calls,_,_=setup(tmp_path,monkeypatch,quota=False)
    result=run(tmp_path,fixtures)
    assert not calls and result['counts']=={'UNKNOWN_QUOTA':6}
    assert result['external_requests']==result['quota_bootstrap_requests']==0


def test_monthly_reserve_is_not_spent_on_optional_calendars(tmp_path,monkeypatch):
    fixtures,calls,_,_=setup(tmp_path,monkeypatch)
    q=SharedQuota(tmp_path,kind='daily',clock=lambda:NOW)
    attempt=q.begin()
    q.finish(attempt,{'authenticated':True,'http_status':200,'observed_at':NOW.isoformat()},
        TheStatsApiUsage(minute_remaining=119,minute_reset=int((NOW+timedelta(minutes=1)).timestamp()),
            monthly_remaining=10,monthly_reset=int((NOW+timedelta(days=30)).timestamp())))
    result=run(tmp_path,fixtures)
    assert not calls and result['counts']=={'QUOTA_RESERVED_FOR_DAILY_WORK':6}


def test_expired_minute_header_uses_one_necessary_get_then_shared_gate(tmp_path,monkeypatch):
    fixtures,calls,_,_=setup(tmp_path,monkeypatch,expired=True)
    result=run(tmp_path,fixtures)
    assert result['status']=='COMPLETE' and len(calls)==6
    assert result['quota_bootstrap_requests']==1 and result['quota_bootstrap_attempt_claimed']


@pytest.mark.parametrize('fault',['pagination','quota'])
def test_partial_calendar_and_rate_limit_are_not_hidden_as_empty_schedule(tmp_path,monkeypatch,fault):
    fixtures,calls,_,_=setup(tmp_path,monkeypatch,fault=fault)
    result=run(tmp_path,fixtures)
    assert result['status']=='GAPS' and result['calendar_counts_at_completion']=={'MISSING_CALENDAR':6}
    if fault=='quota':
        assert len(calls)==1 and result['counts']=={'RATE_LIMITED':6}
    else:
        assert len(calls)==6 and result['counts']=={'INVALID_OR_INCOMPLETE_CALENDAR_SOURCE':6}


@pytest.mark.parametrize('fault',['stale','identity','locked'])
def test_missing_or_invalid_target_proof_never_triggers_speculative_calendar_get(tmp_path,monkeypatch,fault):
    fixtures,calls,_,_=setup(tmp_path,monkeypatch)
    if fault=='stale':
        result=module.collect(tmp_path,fixtures,clock=lambda:NOW+timedelta(minutes=31))
    else:
        fixtures=deepcopy(fixtures)
        for f in fixtures:
            if fault=='identity':f['provider_home_team_id']='tm_999'
            else:f['kickoff']=(NOW+timedelta(minutes=15)).isoformat()
        result=run(tmp_path,fixtures)
    assert not calls and result['external_requests']==0 and result['status']=='NO_VERIFIED_TARGETS'


def test_wall_budget_is_checked_before_network_and_reports_all_omitted_sources(tmp_path,monkeypatch):
    fixtures,calls,_,_=setup(tmp_path,monkeypatch)
    ticks=iter(range(100));monkeypatch.setattr(module,'monotonic',lambda:next(ticks)*10)
    result=run(tmp_path,fixtures,max_seconds=4)
    assert not calls and result['counts']=={'COLLECTION_TIME_LIMIT':6}


@pytest.mark.parametrize('value',[None,{'enabled':True,'mode':'LIVE','max_requests':20,'max_seconds':45},
    {'enabled':True,'mode':'SHADOW','max_requests':21,'max_seconds':45}])
def test_collection_flag_is_exact_and_defaults_disabled(tmp_path,value):
    if value is not None:
        (tmp_path/'config').mkdir();(tmp_path/'config/calendar-shadow.json').write_text(json.dumps(value))
    assert module.enabled(tmp_path) is False


def test_duplicate_or_unbounded_input_is_rejected_before_any_get(tmp_path,monkeypatch):
    fixtures,calls,_,_=setup(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='DUPLICATE_CALENDAR_TARGET'):run(tmp_path,[fixtures[0],fixtures[0]])
    with pytest.raises(ValueError,match='INVALID_CALENDAR_COLLECTION_BUDGET'):run(tmp_path,fixtures*334)
    assert not calls


def test_parent_preparation_archives_collection_before_isolated_fit_and_keeps_staging(tmp_path,monkeypatch):
    from test_daily_native_shadow import staged

    from goal_analysis.agents import canonical_sha256
    run_id,report,path,_=staged(tmp_path);before=path.read_bytes()
    (tmp_path/'config').mkdir();(tmp_path/'config/calendar-shadow.json').write_text(json.dumps(module.POLICY))
    calls=[]
    def collect(*args,**kwargs):
        calls.append('collection');assert kwargs['max_seconds']==45
        return {'status':'COMPLETE','external_requests':2,'records':[],'dataset':{}}
    monkeypatch.setattr(module,'collect',collect)
    def execute(command,**kwargs):
        import subprocess
        calls.append('fit')
        assert (tmp_path/'data/arthur/native-shadow'/run_id/'calendar-collection.json').is_file()
        assert 'THE_STATS_API_KEY' not in kwargs['env']
        raise subprocess.TimeoutExpired(command,kwargs['timeout'])
    monkeypatch.setattr(daily_native_shadow.subprocess,'run',execute)
    result=daily_native_shadow.run(tmp_path,run_id,canonical_sha256(report),{'enabled_profiles':[]},
                                   as_of=NOW,prepare_sources=True)
    assert calls==['collection','fit'] and path.read_bytes()==before
    assert result['calendar_collection']['external_requests']==2 and result['external_requests']==0
    with pytest.raises(FileExistsError):
        daily_native_shadow.run(tmp_path,run_id,canonical_sha256(report),{'enabled_profiles':[]},
                                as_of=NOW,prepare_sources=True)
    assert calls==['collection','fit']


def test_parent_collection_failure_isolated_and_uncertain_calls_not_reported_zero(tmp_path,monkeypatch):
    import subprocess

    from test_daily_native_shadow import staged

    from goal_analysis.agents import canonical_sha256
    run_id,report,_,_=staged(tmp_path)
    (tmp_path/'config').mkdir();(tmp_path/'config/calendar-shadow.json').write_text(json.dumps(module.POLICY))
    monkeypatch.setattr(module,'collect',Mock(side_effect=ValueError('sensitive-secret')))
    monkeypatch.setattr(daily_native_shadow.subprocess,'run',Mock(side_effect=subprocess.TimeoutExpired('fit',1)))
    result=daily_native_shadow.run(tmp_path,run_id,canonical_sha256(report),{'enabled_profiles':[]},
                                   as_of=NOW,prepare_sources=True)
    assert result['status']=='TIMEOUT' and result['calendar_collection']['status']=='UNAVAILABLE'
    assert result['calendar_collection']['external_requests'] is None
    assert 'sensitive-secret' not in json.dumps(result)
