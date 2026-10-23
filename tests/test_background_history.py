"""Real freeze evidence, common quota claims, bounded queue and crash recovery."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from test_nightly import setup_workflow

from goal_analysis.jobs import background_history as background
from goal_analysis.jobs import the_stats_history as history
from goal_analysis.providers.the_stats_api import (
    TheStatsApiClient,
    TheStatsApiError,
    TheStatsApiUsage,
)
from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
from goal_analysis.storage.provider_quota import SharedQuota

NOW=datetime(2026,10,17,20,tzinfo=UTC)
KEY='synthetic-background-key'
ROOT=Path(__file__).resolve().parents[1]


def policy():
    return json.loads((ROOT/'config/historical-background.json').read_bytes())


def arm(root, now=NOW, **changes):
    path=root/'data/arthur/historical-background/activation.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    value={'version':background.VERSION,'tracking_enabled':True,'background_enabled':True,
           'source_sha256':background.implementation_hash(),'activated_at':now.isoformat(),**changes}
    path.write_text(json.dumps(value),encoding='utf-8')


def seed(root, now=NOW, *, remaining=90000, minute=100, reset=None, retry=None, status=200):
    store=SharedQuota(root,clock=lambda:now)
    token=store.begin()
    usage=TheStatsApiUsage(monthly_limit=100000,monthly_remaining=remaining,
        monthly_reset=int((now+timedelta(days=2)).timestamp()),minute_limit=120,
        minute_remaining=minute,minute_reset=int((reset or now+timedelta(seconds=60)).timestamp()),retry_after=retry)
    store.finish(token,{'authenticated':True,'observed_at':now,'http_status':status},usage)
    return store


def background_quota(root, *, clock=lambda:NOW, **changes):
    return SharedQuota(root,kind='history',policy={**policy()['quota'],**changes},clock=clock)


def counts(store):
    with closing(sqlite3.connect(store.path)) as db:
        return (db.execute('SELECT count(*) FROM quota_attempts').fetchone()[0],
                db.execute('SELECT count(*) FROM quota_receipts').fetchone()[0])


def prepared(root, monkeypatch):
    workflow, clock, _=setup_workflow(root)
    clock[0]=NOW
    monkeypatch.setattr(workflow,'_weather_history',lambda job:{'status':'DISABLED'})
    assert workflow.run()['status']=='COMPLETED'
    from goal_analysis.storage import Database
    from goal_analysis.storage.sports_archive import SportsArchive

    database=Database(root/'data/goal_analysis.sqlite3')
    database.initialize()
    SportsArchive(database).initialize()
    (root/'config/historical-background.json').write_text(json.dumps(policy()),encoding='utf-8')
    history.save_plan(root/'data/arthur/the-stats-history/active-plan.json',
                      history.prepare_plan(['comp_4643'],as_of=NOW-timedelta(days=2),years=5))
    arm(root)
    seed(root)
    return workflow,clock


class Transport:
    def __init__(self):
        self.calls=[]

    def __call__(self,url,headers,*,timeout):
        self.calls.append(url)
        assert headers['Authorization']=='Bearer '+KEY and timeout==20
        query={k:v[0] for k,v in parse_qs(urlsplit(url).query).items()}
        day=query['date_to']
        row={'id':'mt_'+day.replace('-',''),'competition_id':query['competition_id'],
             'season_id':'sn_1','status':'finished','utc_date':day+'T12:00:00Z',
             'home_team':{'id':'tm_1','name':'Synthetic Home'},
             'away_team':{'id':'tm_2','name':'Synthetic Away'},
             'score':{'regulation':{'home':2,'away':1}}}
        return {'data':[row],'meta':{'page':int(query['page']),'per_page':100,'total_pages':1}}, {},200


def test_disarmed_tick_is_read_only_and_does_not_create_data(tmp_path):
    assert background.tick(tmp_path)['status']=='DISARMED'
    assert list(tmp_path.iterdir())==[]


def test_unknown_expired_low_quota_retry_and_access_denial_never_spend(tmp_path):
    quota=background_quota(tmp_path)
    assert quota.allowance()=={'status':'UNKNOWN_QUOTA','requests':0}
    seed(tmp_path,remaining=5000)
    before=counts(quota)
    with pytest.raises(TheStatsApiError,match='QUOTA_RESERVED'):
        quota.begin()
    assert counts(quota)==before
    seed(tmp_path,NOW+timedelta(seconds=1),retry=300)
    assert background_quota(tmp_path,clock=lambda:NOW+timedelta(seconds=2)).allowance()['status']=='PROVIDER_RETRY_AFTER'
    seed(tmp_path,NOW+timedelta(seconds=400),status=403)
    assert background_quota(tmp_path,clock=lambda:NOW+timedelta(seconds=401)).allowance()['status']=='PROVIDER_ACCESS_REQUIRES_RECHECK'


def test_headerless_response_cannot_clear_monthly_fence_and_auth_success_can_clear_denial(tmp_path):
    seed(tmp_path,remaining=0,status=403)
    observer=SharedQuota(tmp_path,clock=lambda:NOW+timedelta(seconds=1))
    token=observer.begin()
    observer.finish(token,{'authenticated':True,'observed_at':NOW+timedelta(seconds=1),'http_status':200},TheStatsApiUsage())
    assert background_quota(tmp_path,clock=lambda:NOW+timedelta(seconds=2)).allowance()['requests']==0
    seed(tmp_path,NOW+timedelta(seconds=3))
    assert background_quota(tmp_path,clock=lambda:NOW+timedelta(seconds=4)).allowance()['requests']>0


def test_unknown_reset_or_stale_fact_is_not_invented_as_spare_capacity(tmp_path):
    seed(tmp_path)
    assert background_quota(tmp_path,clock=lambda:NOW+timedelta(seconds=61)).allowance()['status']=='QUOTA_REFRESH_REQUIRED'
    assert background_quota(tmp_path,clock=lambda:NOW+timedelta(hours=1)).allowance()['status']=='STALE_MONTHLY_QUOTA'
    assert background_quota(tmp_path,clock=lambda:NOW-timedelta(seconds=1)).allowance()['requests']==0


def test_concurrent_clients_cannot_overbook_daily_and_minute_claims(tmp_path):
    seed(tmp_path)
    def claim(_):
        try:
            return background_quota(tmp_path,daily_cap=7).begin()
        except TheStatsApiError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(claim,range(30)))
    assert len([r for r in results if r])==7
    assert counts(background_quota(tmp_path))==(8,1)


def test_minute_reserve_and_clock_rollback_stop_before_network_or_cursor_work(tmp_path):
    seed(tmp_path,minute=30)
    quota=background_quota(tmp_path)
    assert quota.allowance()['requests']==0
    seed(tmp_path,NOW+timedelta(seconds=1),minute=31)
    quota=background_quota(tmp_path,clock=lambda:NOW+timedelta(seconds=2))
    assert quota.allowance()['requests']==1
    quota.begin()
    assert quota.allowance()['requests']==0
    assert background_quota(tmp_path,clock=lambda:NOW).allowance()['status']=='CLOCK_MOVED_BACKWARDS'


def test_default_retry_fence_and_source_change_stop_before_the_provider(tmp_path):
    seed(tmp_path,status=429)
    assert background_quota(tmp_path).allowance()['status']=='PROVIDER_RETRY_AFTER'
    seed(tmp_path,NOW+timedelta(seconds=1))
    quota=SharedQuota(tmp_path,kind='history',policy=policy()['quota'],clock=lambda:NOW+timedelta(seconds=2),
                      source_check=lambda:False)
    before=counts(quota)
    with pytest.raises(TheStatsApiError,match='VALIDATED_SOURCE_CHANGED'):
        quota.begin()
    assert counts(quota)==before


def test_uncertain_transport_claim_survives_and_health_or_cache_never_makes_a_receipt(tmp_path):
    seed(tmp_path)
    observer=background_quota(tmp_path)
    def fail(*args,**kwargs):
        raise OSError(KEY)
    client=TheStatsApiClient(KEY,transport=fail,quota_observer=observer,clock=lambda:NOW)
    with pytest.raises(TheStatsApiError,match='TRANSPORT_UNAVAILABLE'):
        client.request('/coverage/summary')
    assert counts(observer)==(2,1) and observer.allowance()['history_claimed_today']==1
    client=TheStatsApiClient(KEY,transport=lambda *a,**kw:({'status':'healthy'}, {},200),
                            quota_observer=observer,clock=lambda:NOW)
    client.health()
    assert counts(observer)==(2,1)
    assert KEY.encode() not in observer.path.read_bytes()


def test_optional_tracking_arms_daily_factory_and_keeps_legacy_counter(tmp_path):
    cfg=tmp_path/'config'
    cfg.mkdir()
    (cfg/'historical-background.json').write_text(json.dumps(policy()),encoding='utf-8')
    client=TheStatsPrimaryClient(tmp_path,api_key=KEY,clock=lambda:NOW)
    assert client.quota_observer is None
    arm(tmp_path,legacy_history_requests={'date':NOW.date().isoformat(),'requests':20})
    client=TheStatsPrimaryClient(tmp_path,api_key=KEY,clock=lambda:NOW,
        transport=lambda *a,**kw:({'data':[]},{'X-Monthly-Quota-Remaining':'90000'},200))
    client.request('/coverage/summary')
    assert counts(client.quota_observer)==(1,1)
    from goal_analysis.storage.provider_quota import observer_for
    assert observer_for(tmp_path,kind='history',clock=lambda:NOW).legacy_requests==20


def test_real_freeze_precedes_bounded_import_and_repeat_is_cooldown(tmp_path,monkeypatch):
    workflow,clock=prepared(tmp_path,monkeypatch)
    before=deepcopy(workflow.ledger.list_freezes())
    transport=Transport()
    proof=background.tick(tmp_path,clock=lambda:clock[0],api_key=KEY,transport=transport,max_requests=3)
    assert proof['requests']==len(transport.calls)==3
    assert proof['daily_gate']['run_id']==before[0]['run_id']
    assert workflow.ledger.list_freezes()==before
    same=background.tick(tmp_path,clock=lambda:clock[0],api_key=KEY,transport=transport)
    assert same['status']=='COOLDOWN' and len(transport.calls)==3
    state=history._read(tmp_path/'data/arthur/historical-background/state.json')
    assert state['result']['processed_pages']==3 and state['result']['plans'][0]['unified_results']['counts']['versions_created']>0


@pytest.mark.parametrize('broken',['archive','file','busy','source','future_activation'])
def test_incomplete_or_changed_daily_proof_never_calls_importer(tmp_path,monkeypatch,broken):
    workflow,_clock=prepared(tmp_path,monkeypatch)
    job=workflow.store.get('2026-10-18')
    if broken=='archive':
        workflow._stage(job,'archive')['status']='PARTIAL'
        workflow.store.save(job)
    elif broken=='file':
        run=workflow._stage(job,'generation')['run_id']
        (tmp_path/'reports/arthur'/run/'report.json').write_text('{}',encoding='utf-8')
    elif broken=='busy':
        workflow._stage(job,'learning')['status']='RUNNING'
        workflow.store.save(job)
    elif broken=='source':
        arm(tmp_path,source_sha256='a'*64)
    else:
        arm(tmp_path,now=NOW+timedelta(days=1))
    calls=[]
    result=background.tick(tmp_path,clock=lambda:NOW,runner=lambda *a,**kw:calls.append(1))
    assert result['requests']==0 and not calls
    assert not (tmp_path/'data/arthur/historical-background/state.json').exists()


def test_post_archive_partial_package_is_valid_and_due_auxiliary_work_has_priority(tmp_path,monkeypatch):
    workflow,_=prepared(tmp_path,monkeypatch)
    job=workflow.store.get('2026-10-18')
    workflow._stage(job,'generation')['status']='PARTIAL'
    stage=workflow._stage(job,'settlement')
    stage.update(status='RETRY_WAIT',next_retry_at=(NOW+timedelta(minutes=15)).isoformat())
    workflow._stage(job,'learning')['status']='PENDING'
    workflow.store.save(job)
    assert background.publication_gate(tmp_path,NOW,55)['status']=='AVAILABLE'
    stage['next_retry_at']=NOW.isoformat()
    workflow.store.save(job)
    assert background.publication_gate(tmp_path,NOW,55)['status']=='DAILY_WORK_HAS_PRIORITY'


def test_crash_retains_claim_and_next_batch_reuses_frozen_queue(tmp_path,monkeypatch):
    _,clock=prepared(tmp_path,monkeypatch)
    called=[]
    def crash(root,queue,**kwargs):
        called.append(queue['queue_sha256'])
        raise OSError('synthetic interrupted background batch')
    with pytest.raises(OSError):
        background.tick(tmp_path,clock=lambda:clock[0],runner=crash)
    assert background.tick(tmp_path,clock=lambda:clock[0],runner=crash)['status']=='COOLDOWN'
    clock[0]+=timedelta(minutes=31)
    seed(tmp_path,clock[0])
    def complete(root,queue,**kwargs):
        called.append(queue['queue_sha256'])
        return {'status':'COMPLETE','requests':0,'processed_pages':0}
    background.tick(tmp_path,clock=lambda:clock[0],runner=complete)
    assert called[0]==called[1]


def test_nightly_hook_cannot_start_a_second_independent_history_budget(tmp_path,monkeypatch):
    workflow,_=prepared(tmp_path,monkeypatch)
    monkeypatch.setattr(history,'run_history',lambda *a,**kw:pytest.fail('old hook must not execute'))
    assert workflow._the_stats_history({})=={'status':'MANAGED_BY_BACKGROUND_TICK','requests':0}


def test_queue_can_use_fresh_daily_receipt_instead_of_a_stale_access_fence(tmp_path,monkeypatch):
    _,clock=prepared(tmp_path,monkeypatch)
    main=history.load_plan(tmp_path/'data/arthur/the-stats-history/active-plan.json')
    folder=tmp_path/'data/arthur/the-stats-history'/main['plan_sha256']
    history._atomic(folder/'quota-state.json',{'provider':'the_stats_api','observed_at':(NOW-timedelta(minutes=1)).isoformat(),
                    'http_status':403,'usage':{}})
    transport=Transport()
    proof=background.tick(tmp_path,clock=lambda:clock[0],api_key=KEY,transport=transport,max_requests=2)
    assert proof['requests']==2
