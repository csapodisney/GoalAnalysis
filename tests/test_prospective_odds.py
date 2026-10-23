"""Prospective collection uses real local stores and recorded transport shapes."""
import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit

import pytest
from test_daily_223_live import NOW, config
from test_the_stats_primary import Transport

from goal_analysis.jobs import prospective_odds as job
from goal_analysis.jobs.odds_monitor import OddsMonitor, monitor_history, monitor_status
from goal_analysis.jobs.odds_monitor_providers import build_monitor_queries
from goal_analysis.jobs.portfolio import RunLock


def activate(root, **changes):
    value = {'version':job.VERSION,'enabled':True,'activated_at':NOW.isoformat(),
             'source_sha256':job.implementation_hash(),**changes}
    path=root/'data/arthur/prospective-odds/activation.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf-8')


@pytest.fixture
def env(tmp_path, monkeypatch):
    clock=[NOW]
    transport=Transport()
    for row in transport.calendar:
        row['utc_date']=(NOW+timedelta(hours=40)).isoformat()
    cfg=config()
    folder=tmp_path/'config';folder.mkdir()
    (folder/'daily223-live.json').write_text(json.dumps(cfg),encoding='utf-8')
    policy={'max_requests':20,'max_quota_age_seconds':1800,'monthly_reserve':5000,
        'daily_reserve':2500,'minute_reserve':30,'daily_cap':400,'per_minute_cap':20}
    monkeypatch.setattr(job,'policy_at',lambda root:{'quota':policy})
    monkeypatch.setattr(job,'publication_gate',lambda root,now,seconds:{'status':'AVAILABLE',
        'stop_at':now+timedelta(seconds=30),'legacy_requests':7})
    claims=[]

    class Quota:
        def __init__(self,root,**kwargs):
            self.kwargs=kwargs;claims.append(kwargs)

        def allowance(self):
            return {'status':'AVAILABLE','requests':4}

        def begin(self):
            assert self.kwargs['source_check']()
            return 'test-claim'

        def finish(self,*args):
            pass

    monkeypatch.setattr(job,'SharedQuota',Quota)
    activate(tmp_path)
    run=lambda:job.tick(tmp_path,clock=lambda:clock[0],api_key='recorded-test-only',transport=transport)
    return tmp_path,clock,transport,cfg,claims,run


def test_three_real_store_first_observations_are_independent_of_any_saved_ticket(env):
    root,_,transport,_,claims,run=env
    result=run()
    assert result['status']=='OBSERVATION_STAGE_COMPLETE',result
    assert result['requests']==4 and len(transport.calls)==4
    assert result['eligible_fixtures']==result['first_captured_now']==3
    assert result['monitor']['provider_calls']==3
    assert result['calendar_receipt']['kind']=='ACTUAL_AUTHENTICATED_HTTP_RESPONSE_NOT_QUOTE_TIME'
    assert result['mode']=='SHADOW' and result['actual_24h_pair_proven'] is False
    assert result['ledger_writes']==result['llm_calls']==0 and result['live_promoted'] is False
    assert not (root/'data/arthur/ledger.sqlite3').exists()
    assert claims[0]['kind']=='history' and claims[0]['legacy_requests']==7
    assert claims[0]['policy']['monthly_reserve']==5000
    assert claims[0]['policy']['daily_reserve']==2500 and claims[0]['policy']['daily_cap']==400
    assert not (root/'data/arthur/odds-monitor.sqlite3').exists()
    with sqlite3.connect(root/'data/arthur/prospective-odds/shadow-monitor.sqlite3') as db:
        quotes=[q for row in db.execute('select quotes_json from monitor_snapshots') for q in json.loads(row[0])]
    assert quotes and all(q['quoted_at'] is None and q['quote_observed_at']==NOW.isoformat() for q in quotes)
    assert all(q['quote_provider']=='the_stats_api' for q in quotes)


def test_cooldown_and_successful_first_snapshot_do_not_create_a_second_refresh_loop(env):
    _,clock,transport,_,_,run=env
    assert run()['first_captured_now']==3
    calls=len(transport.calls)
    assert run()['status']=='COOLDOWN' and len(transport.calls)==calls
    clock[0]+=timedelta(minutes=16)
    result=run()
    assert result['already_captured']==3 and result['missing_first_observations']==0
    assert result['requests']==0 and len(transport.calls)==calls


@pytest.mark.parametrize('changes',[{'enabled':False},{'enabled':1},{'source_sha256':'0'*64},
    {'version':'old'},{'activated_at':(NOW+timedelta(seconds=1)).isoformat()}])
def test_disarmed_wrong_source_or_future_activation_never_calls_provider(env,changes):
    root,_,transport,_,_,run=env
    activate(root,**changes)
    assert run()['status']=='DISARMED_OR_SOURCE_CHANGED'
    assert not transport.calls


def test_missing_activation_is_read_only_and_never_creates_state(tmp_path):
    result=job.tick(tmp_path,clock=lambda:NOW)
    assert result['status']=='DISARMED_OR_SOURCE_CHANGED'
    assert not list(tmp_path.rglob('*'))


@pytest.mark.parametrize('reason',['DAILY_WORK_HAS_PRIORITY','NO_SPARE_TIME','MISSING_DAILY_FREEZE'])
def test_daily_publication_gates_have_priority(env,monkeypatch,reason):
    _,_,transport,_,_,run=env
    monkeypatch.setattr(job,'publication_gate',lambda *args:{'status':reason})
    assert run()['status']==reason and not transport.calls


def test_shared_quota_reservation_is_not_bootstrapped_with_an_extra_paid_request(env,monkeypatch):
    _,_,transport,_,_,run=env
    monkeypatch.setattr(job.SharedQuota,'allowance',lambda self:{'requests':0,'status':'QUOTA_RESERVED_FOR_DAILY_WORK'})
    assert run()['status']=='QUOTA_RESERVED_FOR_DAILY_WORK' and not transport.calls


def test_first_capture_has_headroom_inside_the_original_400_combined_spare_daily_cap(env,monkeypatch):
    _,_,_,_,claims,run=env
    original=job.policy_at(None)
    original['quota']['daily_cap']=300
    monkeypatch.setattr(job,'policy_at',lambda root:original)
    assert run()['first_captured_now']==3
    assert claims[0]['policy']['daily_cap']==400
    assert original['quota']['daily_cap']==300


def test_shared_run_lock_prevents_overlap(env):
    root,_,transport,_,_,run=env
    with RunLock(root/'data/arthur/run.lock'):
        assert run()['status']=='PROSPECTIVE_UNAVAILABLE'
    assert not transport.calls


@pytest.mark.parametrize('change',['old','too_far','not_scheduled','wrong_comp','duplicate','same_team','naive'])
def test_invalid_outside_window_or_ambiguous_calendar_has_no_odds_request(env,change):
    _,_,transport,_,_,run=env
    rows=transport.calendar
    if change=='old':
        for row in rows:row['utc_date']=(NOW+timedelta(hours=35,minutes=59)).isoformat()
    elif change=='too_far':
        for row in rows:row['utc_date']=(NOW+timedelta(hours=48,seconds=1)).isoformat()
    elif change=='not_scheduled':
        for row in rows:row['status']='postponed'
    elif change=='wrong_comp':
        for row in rows:row['competition_id']='comp_999999999'
    elif change=='duplicate':
        transport.calendar=[rows[0],deepcopy(rows[0])]
    elif change=='same_team':
        for row in rows:row['away_team']=deepcopy(row['home_team'])
    elif change=='naive':
        for row in rows:row['utc_date']=(NOW+timedelta(hours=40)).replace(tzinfo=None).isoformat()
    result=run()
    assert result['requests']==1 and result['eligible_fixtures']==0,result
    assert not any(url.endswith('/odds') for url in transport.calls)


@pytest.mark.parametrize('hours',[36,48])
def test_exact_window_boundaries_are_included(env,hours):
    _,_,transport,_,_,run=env
    for row in transport.calendar:row['utc_date']=(NOW+timedelta(hours=hours)).isoformat()
    assert run()['first_captured_now']==3


def test_invalid_pagination_does_not_silently_read_prefix_and_tracks_spent_request(env):
    _,_,transport,_,_,run=env
    transport.bad_pagination=True
    result=run()
    assert result['status']=='PROSPECTIVE_UNAVAILABLE' and result['requests']==1
    assert len(transport.calls)==1


def test_partial_bounded_pagination_advances_without_claiming_calendar_completeness(env):
    root,clock,transport,_,_,_=env
    def paged(url,headers,**kwargs):
        body,usage,status=transport(url,headers,**kwargs)
        if urlsplit(url).path.endswith('/football/matches'):
            page=int(parse_qs(urlsplit(url).query)['page'][0])
            body['meta'].update(page=page,total_pages=12)
        return body,usage,status
    result=job.tick(root,clock=lambda:clock[0],api_key='recorded-test-only',transport=paged)
    assert result['calendar_partial'] and result['calendar_pages_capped']
    assert result['calendar_complete'] is False and result['first_captured_now']==3
    state=json.loads((root/'data/arthur/prospective-odds/state.json').read_text('utf-8'))
    assert state['next_page']==2
    clock[0]+=timedelta(minutes=16)
    result=job.tick(root,clock=lambda:clock[0],api_key='recorded-test-only',transport=paged)
    assert result['calendar_page']==2 and result['requests']==1 and result['already_captured']==3


def test_unwitnessed_calendar_source_is_not_promoted_and_no_odds_are_fetched(env,monkeypatch):
    _,_,transport,_,_,run=env
    def reject(*args):raise ValueError('UNWITNESSED_SOURCE')
    monkeypatch.setattr(job,'witnessed_source',reject)
    result=run()
    assert result['status']=='PROSPECTIVE_UNAVAILABLE' and result['requests']==1
    assert len(transport.calls)==1


def test_corrupt_compact_snapshot_is_not_treated_as_an_existing_first_capture(env):
    root,clock,_,cfg,_,run=env
    result=run();fixture=result['fixtures'][0]
    query=build_monitor_queries([fixture],NOW,{**cfg,'monitor_providers':['the_stats_api']})[0]
    assert job.first_captured(root,query,NOW)
    with sqlite3.connect(root/'data/arthur/prospective-odds/shadow-monitor.sqlite3') as db:
        db.execute("update monitor_snapshots set quotes_json='[]' where query_key=?",(query['query_key'],))
    assert not job.first_captured(root,query,clock[0])


def test_calendar_error_survives_process_reentry_with_cooldown(env):
    root,_,transport,_,_,run=env
    transport.bad_pagination=True
    assert run()['status']=='PROSPECTIVE_UNAVAILABLE'
    assert (root/'data/arthur/prospective-odds/state.json').is_file()
    assert run()['status']=='COOLDOWN' and len(transport.calls)==1


def test_a_single_remaining_request_makes_bounded_progress_using_cached_calendar(env,monkeypatch):
    _,clock,transport,_,_,run=env
    monkeypatch.setattr(job.SharedQuota,'allowance',lambda self:{'requests':1,'status':'AVAILABLE'})
    first=run()
    assert first['requests']==1 and first['first_captured_now']==0
    assert first['eligible_fixtures']==3 and len(transport.calls)==1
    clock[0]+=timedelta(minutes=16)
    second=run()
    assert second['requests']==1 and second['first_captured_now']==1
    clock[0]+=timedelta(minutes=16)
    third=run()
    assert third['already_captured']==1 and third['first_captured_now']==1
    assert third['requests']==1 and len(transport.calls)==3


def test_utc_window_and_actual_berlin_target_day_survive_dst_transition(env):
    _,clock,transport,_,_,run=env
    clock[0]=datetime(2026,10,24,11,tzinfo=UTC)
    for row in transport.calendar:
        row['utc_date']=(clock[0]+timedelta(hours=40)).isoformat()
    result=run()
    assert result['first_captured_now']==3
    query=parse_qs(urlsplit(transport.calls[0]).query)
    assert query['utc_offset']==['+00:00']
    assert all(f['target_date']=='2026-10-26' for f in result['fixtures'])


def test_shadow_capture_does_not_leak_into_existing_live_monitor_or_history(env):
    root,_,_,_,_,run=env
    live=OddsMonitor(root)
    live._connect().close()
    before=sha256(live.path.read_bytes()).hexdigest()
    assert run()['first_captured_now']==3
    assert sha256(live.path.read_bytes()).hexdigest()==before
    assert monitor_history(root,NOW)==[] and monitor_status(root,NOW)['queries']==[]
    assert len(monitor_history(root,NOW,shadow=True))==3
    assert len(monitor_status(root,NOW,shadow=True)['queries'])==3


@pytest.mark.parametrize('bad',[1,'shadow'])
def test_monitor_scope_cannot_be_accidentally_enabled_by_truthy_values(tmp_path,bad):
    with pytest.raises(ValueError,match='Boolean'):
        OddsMonitor(tmp_path,shadow=bad)
    with pytest.raises(ValueError,match='Boolean'):
        monitor_history(tmp_path,NOW,shadow=bad)
