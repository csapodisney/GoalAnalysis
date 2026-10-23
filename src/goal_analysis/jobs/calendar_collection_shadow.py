"""Post-freeze calendar acquisition only; models and published cards untouched."""
import json
import math
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic

from goal_analysis.features.schedule import build_schedule_features
from goal_analysis.jobs.schedule_shadow import (
    _calendar,
    _scope,
    calendar_query,
    inspect_schedule,
    target_fixture,
)
from goal_analysis.jobs.the_stats_context import Resources
from goal_analysis.jobs.xg_specialist_shadow import _targets
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha
from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
from goal_analysis.storage.provider_quota import SharedQuota
from goal_analysis.storage.source_observations import read_source_raw

VERSION='daily-calendar-collection-shadow-v1'
POLICY={'enabled':True,'mode':'SHADOW','max_requests':20,'max_seconds':45}
QUOTA={'max_requests':20,'max_quota_age_seconds':3600,'monthly_reserve':1000,
       'daily_reserve':50,'minute_reserve':10,'daily_cap':2000,'per_minute_cap':20}


def enabled(root):
    path=Path(root)/'config/calendar-shadow.json'
    return path.is_file() and path.stat().st_size<1000 and json.loads(path.read_bytes())==POLICY


def _bootstrap_allowed(quota,now):
    """One necessary GET may renew an expired minute header, never unknown quota."""
    if not quota.path.is_file():
        return False
    with closing(sqlite3.connect(quota.path.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        month=quota._dimension(db,'monthly_remaining')
        minute=quota._dimension(db,'minute_remaining')
        denial=quota._dimension(db,'http_status');retry=quota._dimension(db,'retry_after')
        if month is None or minute is None or denial is None or denial['http_status'] in {401,403,429}:
            return False
        if not timedelta(0)<=now-utc(month['observed_at'])<=timedelta(hours=1):
            return False
        m=json.loads(month['usage_json']);p=json.loads(minute['usage_json'])
        if (m.get('monthly_reset') is None or p.get('minute_reset') is None
                or now.timestamp()>=m['monthly_reset'] or now.timestamp()<p['minute_reset']):
            return False
        if retry and now<utc(retry['observed_at'])+timedelta(seconds=json.loads(retry['usage_json'])['retry_after']):
            return False
        reserve=max(QUOTA['monthly_reserve'],math.ceil((m['monthly_reset']-now.timestamp())/86400)*QUOTA['daily_reserve'])
        if m['monthly_remaining']!=-1 and m['monthly_remaining']-quota._debit(db,month)<reserve+POLICY['max_requests']:
            return False
        recent=db.execute('SELECT count(*) FROM quota_attempts WHERE started_at>?',
                          ((now-timedelta(minutes=1)).isoformat(timespec='microseconds'),)).fetchone()[0]
        return recent<10


def _plan(fixtures):
    queues=[];seen=set()
    for i,f in enumerate(sorted(fixtures,key=lambda f:(utc(f['kickoff']),f['fixture_id']))):
        sides=('home','away') if i%2==0 else ('away','home')
        queues.append([(f,calendar_query(f['provider_'+side+'_team_id'],f['kickoff'])) for side in sides])
    plan=[]
    for index in range(2):
        for queue in queues:
            f,query=queue[index];key=sha(query)
            if key not in seen:
                seen.add(key);plan.append((f,query))
    return plan


def collect(root,fixtures,*,clock=None,max_requests=20,max_seconds=45):
    """Read cache first, then fair bounded GETs after normal daily collection.

    No target refresh, odds request, lineup fetch, LLM or model fit. Missing
    target proof stays a gap. Complete calendars must pass the existing reader.
    """
    if (not isinstance(fixtures,list) or len(fixtures)>1000 or type(max_requests) is not int
            or not 0<=max_requests<=POLICY['max_requests'] or type(max_seconds) is not int
            or not 1<=max_seconds<=POLICY['max_seconds']):
        raise ValueError('INVALID_CALENDAR_COLLECTION_BUDGET')
    root=Path(root).resolve();clock=clock or (lambda:datetime.now(UTC))
    started=utc(clock());timer=monotonic();issues=Counter();by_id={}
    for f in fixtures:
        try:
            target_fixture(f)
            if f['fixture_id'] in by_id:
                raise ValueError('DUPLICATE_CALENDAR_TARGET')
            if utc(f['kickoff'])<=started+timedelta(minutes=15):
                issues['PREMATCH_LOCKOUT']+=1;continue
            by_id[f['fixture_id']]=f
        except (KeyError,TypeError,ValueError) as error:
            if str(error)=='DUPLICATE_CALENDAR_TARGET':
                raise
            issues['INVALID_NATIVE_TARGET']+=1
    targets,_,target_issues=_targets(root,by_id,started)
    issues.update(target_issues)
    accepted=[by_id[fid] for fid in targets];plan=_plan(accepted)
    if not plan:
        return {'version':VERSION,'mode':'SHADOW','status':'NO_VERIFIED_TARGETS',
            'live_decision_effect':False,'input_fixtures':len(fixtures),'verified_target_fixtures':0,
            'target_issues':dict(issues),'planned_distinct_calendars':0,'records':[],'counts':{},
            'calendar_counts_at_completion':{},'usage':{'requests':0,'cache_hits':0,'max_requests':max_requests},
            'external_requests':0,'authenticated_requests':0,'quota_bootstrap_requests':0,
            'llm_requests':0,'model_fits':0,'ledger_writes':0}
    quota=SharedQuota(root,kind='daily',policy=QUOTA,clock=clock)
    client=TheStatsPrimaryClient(root,max_requests=max(1,max_requests),timeout=3,clock=clock,quota_observer=quota)
    resources=Resources(root,client,clock,max_requests)
    records={};bootstrap_used=False;bootstrap_requests=0
    for cache_only in (True,False):
        for f,query in plan:
            key=sha(query)
            if key in records:
                continue
            now=utc(clock())
            if now<started or monotonic()-timer>=max_seconds-3:
                records[key]={'query':query,'status':'COLLECTION_TIME_LIMIT'};continue
            if utc(f['kickoff'])<=now+timedelta(minutes=15):
                records[key]={'query':query,'status':'PREMATCH_LOCKOUT'};continue
            bootstrap=False
            if not cache_only:
                if resources.usage['requests']>=max_requests:
                    records[key]={'query':query,'status':'REQUEST_BUDGET'};continue
                if resources.usage['stopped_reason']:
                    records[key]={'query':query,'status':resources.usage['stopped_reason']};continue
                allowance=quota.allowance()
                if allowance['status']=='QUOTA_REFRESH_REQUIRED' and not bootstrap_used and _bootstrap_allowed(quota,now):
                    # Still records an append-only actual request claim/receipt.
                    client.quota_observer=SharedQuota(root,kind='daily',clock=clock)
                    bootstrap_used=True;bootstrap=True
                elif allowance['requests']>0:
                    client.quota_observer=quota
                else:
                    records[key]={'query':query,'status':allowance['status']};continue
            previous=client.authenticated_request_count
            data,source,error=resources.get('/football/matches',query,ttl=timedelta(minutes=30),cache_only=cache_only)
            if bootstrap:
                bootstrap_requests+=client.authenticated_request_count-previous
            if cache_only and error=='CACHE_MISS':
                continue
            record={'query':query,'status':error or 'INVALID_SOURCE','source':source}
            if error is None:
                try:
                    with closing(sqlite3.connect((root/'data/goal_analysis.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
                        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
                        raw=db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?',(source['content_hash'],)).fetchone()
                        body,proof=read_source_raw(db,dict(raw),root,utc(clock()),source)
                    assert body['data']==data
                    scoped=_scope(source['source_url'],query['team_id'],f['kickoff'])
                    envelope=_calendar(body,proof,scoped)
                    feature=build_schedule_features(target_fixture(f),[envelope],as_of=utc(clock()))
                    team=next(t for t in feature['teams'] if t['team_id']==query['team_id'])
                    if team['status']!='AVAILABLE_SHADOW':
                        raise ValueError('CALENDAR_NOT_AVAILABLE_FOR_TARGET')
                    record.update(status='VERIFIED_CALENDAR_SOURCE',receipt_id=proof['receipt_id'])
                except (ValueError,TypeError,KeyError,OSError,AssertionError,sqlite3.Error):
                    record['status']='INVALID_OR_INCOMPLETE_CALENDAR_SOURCE'
            records[key]=record
    completed=utc(clock());view=inspect_schedule(root,accepted,as_of=completed)
    assert client.request_count<=max_requests
    return {'version':VERSION,'mode':'SHADOW','live_decision_effect':False,'started_at':started.isoformat(),
        'completed_at':completed.isoformat(),'seconds':monotonic()-timer,'input_fixtures':len(fixtures),
        'verified_target_fixtures':len(accepted),'target_issues':dict(issues),'planned_distinct_calendars':len(plan),
        'records':list(records.values()),'counts':dict(Counter(r['status'] for r in records.values())),
        'calendar_counts_at_completion':view['counts'],'dataset':view['dataset'],
        'status':'COMPLETE' if len(plan)>0 and not issues and view['counts']=={'AVAILABLE_SHADOW':2*len(accepted)}
                 and all(r['status']=='VERIFIED_CALENDAR_SOURCE' for r in records.values()) else 'GAPS',
        'usage':resources.usage,'external_requests':client.request_count,'authenticated_requests':client.authenticated_request_count,
        'quota_bootstrap_requests':bootstrap_requests,'quota_bootstrap_attempt_claimed':bootstrap_used,
        'llm_requests':0,'model_fits':0,'ledger_writes':0,
        'existing_player_xg_priority':'COLLECTION_AFTER_DAILY_FREEZE','provider_quote_times_created':0}
