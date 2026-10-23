"""Real raw/HTTP/receipt readers; synthetic football values, no live requests."""
import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from test_rotation_features import raw as lineup
from test_schedule_features import NOW, calendars, match

from goal_analysis.jobs.rotation_shadow import inspect_rotation
from goal_analysis.jobs.schedule_shadow import calendar_query, inspect_schedule
from goal_analysis.normalization.canonical import utc
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.context_sources import context_sources
from goal_analysis.storage.http_observations import HttpObservations
from goal_analysis.storage.raw_evidence import read_witnessed_raw
from goal_analysis.storage.temporal import TemporalStore

BASE='https://api.thestatsapi.com'


def environment(root, *, different_url=False, confirmed=True, filtered=False, partial=False):
    db=Database(root/'data/goal_analysis.sqlite3');db.initialize()
    store=SnapshotStore(db,root/'data/raw')
    items=[]
    for calendar in calendars():
        payload=calendar['payload'];query=calendar_query(payload['team_id'],match()['kickoff'])
        if filtered:query['status']='finished'
        rows=[{'id':m['fixture_id'],'competition_id':m['competition_id'],'season_id':m['season_id'],
               'home_team':{'id':m['home_team_id']},'away_team':{'id':m['away_team_id']},
               'utc_date':m['kickoff'],'status':m['status']} for m in payload['matches']]
        body={'data':rows,'meta':{'page':1,'per_page':100,'total_pages':2 if partial else 1,'total':len(rows)}}
        items.append((BASE+'/api/football/matches?'+urlencode(query),body))
    for m in (match(),calendars()[0]['payload']['matches'][-1]):
        data=lineup(m,shift=3 if m['fixture_id']=='mt_100' else 0)
        if m['fixture_id']=='mt_100' and not confirmed:
            data.update(type='predicted',confirmed=False)
        items.append((BASE+f"/api/football/matches/{m['fixture_id']}/lineups",{'data':data}))
    sources=[]
    for url,body in items:
        old_url=BASE+'/api/football/matches?status=finished' if different_url else url
        saved=store.save('the_stats_api','daily_context',json.dumps(body).encode(),'application/json',
            fetched_at=NOW-timedelta(days=2),source_url=old_url)
        sources.append((url,saved))
    temporal=TemporalStore(db,root/'data/raw',clock=lambda:NOW-timedelta(days=1))
    temporal.initialize();temporal.capture('raw',limit=100)
    f={'fixture_id':'the_stats_api:mt_100','source_provider':'the_stats_api',
       'provider_fixture_id':'mt_100','provider_league_id':'comp_1','provider_season_id':'sn_1',
       'provider_home_team_id':'tm_1','provider_away_team_id':'tm_2','kickoff':match()['kickoff']}
    return db,f,sources


def observe(root,db,sources,*,observed=None,available=None):
    observed=observed or NOW-timedelta(minutes=5)
    available=available or NOW-timedelta(minutes=4)
    observer=HttpObservations(db,root,clock=lambda:available)
    return [observer.admit({'provider':'the_stats_api','authenticated':True,'http_status':200,
        'response_id':uuid4().hex,'payload_sha256':saved.content_hash,'source_url':url,
        'observed_at':observed.isoformat()}) for url,saved in sources]


def raw_rows(db):
    with closing(db.connect()) as connection:
        return [dict(r) for r in connection.execute('SELECT * FROM raw_snapshots ORDER BY content_hash')]


@pytest.mark.parametrize('different_url',[False,True])
def test_later_exact_http_alias_recovers_old_calendar_and_xi_without_rewriting_raw(tmp_path,different_url):
    db,f,sources=environment(tmp_path,different_url=different_url)
    before=raw_rows(db)
    assert inspect_schedule(tmp_path,[f],as_of=NOW)['counts']=={'MISSING_CALENDAR':2}
    observe(tmp_path,db,sources)
    result=inspect_rotation(tmp_path,[f],as_of=NOW)
    assert result['counts']=={'XI_COMPARISON_SHADOW':1,'PARTIAL_SHADOW':1}
    assert result['features'][0]['teams'][0]['starting_xi_changes']==3
    schedule=result['dataset']['lineage']['schedule_dataset']
    assert len(schedule['lineage'])==2
    for envelope in schedule['lineage']:
        proof=envelope['source_receipts'][0]
        assert proof['kind']=='ACTUAL_AUTHENTICATED_HTTP_RESPONSE_NOT_QUOTE_TIME'
        assert utc(proof['times']['observed_at'])==NOW-timedelta(minutes=5)
        assert proof['times']['published_at'] is None
    assert raw_rows(db)==before
    assert result['external_requests']==result['production_writes']==0 and not result['live_decision_effect']


def test_reproduction_first_raw_receipt_stays_old_after_real_later_response(tmp_path):
    db,f,sources=environment(tmp_path)
    observe(tmp_path,db,sources)
    with closing(db.connect()) as connection:
        row=dict(connection.execute('SELECT * FROM raw_snapshots WHERE content_hash=?',
                                    (sources[0][1].content_hash,)).fetchone())
        _,old=read_witnessed_raw(connection,row,tmp_path,NOW)
    assert utc(old['times']['observed_at'])==NOW-timedelta(days=2)
    assert inspect_schedule(tmp_path,[f],as_of=NOW)['counts']=={'AVAILABLE_SHADOW':2}


@pytest.mark.parametrize('fault',['stale','future_observed','future_available','unwitnessed','corrupt'])
def test_invalid_observations_do_not_refresh_calendar_coverage(tmp_path,fault):
    db,f,sources=environment(tmp_path)
    kw={}
    if fault=='stale':kw={'observed':NOW-timedelta(hours=7)}
    if fault=='future_observed':kw={'observed':NOW+timedelta(seconds=1),'available':NOW+timedelta(seconds=2)}
    if fault=='future_available':kw={'available':NOW+timedelta(seconds=2)}
    aliases=observe(tmp_path,db,sources[:2],**kw)
    if fault=='unwitnessed':
        with db.transaction() as connection:
            connection.execute('DROP TRIGGER the_stats_http_witnesses_no_delete')
            for alias in aliases:
                connection.execute('DELETE FROM the_stats_http_witnesses WHERE response_id=?',
                                   (alias['http_observation_id'],))
    if fault=='corrupt':
        for _,saved in sources[:2]:
            path=tmp_path/'data/raw'/saved.relative_path;path.write_bytes(path.read_bytes()+b' ')
    assert inspect_schedule(tmp_path,[f],as_of=NOW)['counts']=={'MISSING_CALENDAR':2}


@pytest.mark.parametrize('fault',['filtered','partial'])
def test_http_receipt_does_not_override_calendar_query_or_pagination(tmp_path,fault):
    db,f,sources=environment(tmp_path,**{fault:True});observe(tmp_path,db,sources)
    assert inspect_schedule(tmp_path,[f],as_of=NOW)['counts']=={'MISSING_CALENDAR':2}


def test_receipt_is_not_confirmation_and_newest_prediction_is_not_replaced(tmp_path):
    db,f,sources=environment(tmp_path,confirmed=False);observe(tmp_path,db,sources)
    result=inspect_rotation(tmp_path,[f],as_of=NOW)['features'][0]['teams'][0]
    assert result['current_lineup_status']=='UNCONFIRMED_OR_PREDICTED_LINEUP'
    assert result['starting_xi_changes'] is None and result['workload_indicators'] is not None


def test_same_bytes_at_wrong_url_cannot_substitute_for_exact_scope(tmp_path):
    db,f,sources=environment(tmp_path)
    wrong=[(url.replace('tm_1','tm_999').replace('tm_2','tm_998'),saved) for url,saved in sources[:2]]
    observe(tmp_path,db,wrong)
    assert inspect_schedule(tmp_path,[f],as_of=NOW)['counts']=={'MISSING_CALENDAR':2}


def test_readers_use_only_readonly_sqlite_and_create_no_receipts(tmp_path,monkeypatch):
    db,f,sources=environment(tmp_path);observe(tmp_path,db,sources)
    with closing(db.connect()) as connection:
        before='\n'.join(connection.iterdump())
    original=sqlite3.connect
    def connect(path,*args,**kwargs):
        assert str(path).endswith('?mode=ro') and kwargs.get('uri') is True
        return original(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3,'connect',connect)
        assert inspect_rotation(tmp_path,[f],as_of=NOW)['features']
    with closing(db.connect()) as connection:
        assert '\n'.join(connection.iterdump())==before


def test_discovery_deduplicates_repeated_http_bytes_before_budget_and_keeps_latest(tmp_path):
    db,_,sources=environment(tmp_path)
    observe(tmp_path,db,sources[:1],observed=NOW-timedelta(minutes=10))
    last=observe(tmp_path,db,sources[:1])
    with closing(db.connect()) as connection:
        selected=context_sources(connection,cutoff=NOW,where='a.source_url=?',params=(sources[0][0],),
                                 limit=1,max_bytes=2_000_000,max_age_hours=6)
    assert len(selected)==1 and selected[0][1]['http_observation_id']==last[0]['http_observation_id']


@pytest.mark.parametrize('budget',[{'limit':0,'max_bytes':2_000_000},{'limit':10,'max_bytes':1}])
def test_discovery_enforces_metadata_and_byte_budgets_before_raw_decode(tmp_path,budget):
    db,_,sources=environment(tmp_path);observe(tmp_path,db,sources[:1])
    with closing(db.connect()) as connection,pytest.raises(ValueError,match='CONTEXT_SOURCE_BUDGET_EXCEEDED'):
        context_sources(connection,cutoff=NOW,where='a.source_url=?',params=(sources[0][0],),**budget)


def test_missing_http_tables_preserves_original_witnessed_raw_path(tmp_path):
    from test_schedule_shadow import setup
    _,_,_,f,_=setup(tmp_path)
    assert inspect_schedule(tmp_path,[f],as_of=NOW)['counts']=={'AVAILABLE_SHADOW':2}


def test_future_http_alias_does_not_hide_known_earlier_observation(tmp_path):
    db,f,sources=environment(tmp_path)
    aliases=observe(tmp_path,db,sources[:2])
    observe(tmp_path,db,sources[:2],observed=NOW+timedelta(seconds=1),available=NOW+timedelta(seconds=2))
    report=inspect_schedule(tmp_path,[f],as_of=NOW)
    assert report['counts']=={'AVAILABLE_SHADOW':2}
    assert {e['source_receipts'][0]['observation_id'] for e in report['dataset']['lineage']}=={
        a['http_observation_id'] for a in aliases}


def test_alias_proof_detects_changed_original_metadata(tmp_path):
    db,f,sources=environment(tmp_path);observe(tmp_path,db,sources[:2])
    with db.transaction() as connection:
        connection.execute('UPDATE raw_snapshots SET source_url=? WHERE content_hash=?',
                           (BASE+'/api/football/matches?status=scheduled',sources[0][1].content_hash))
    report=inspect_schedule(tmp_path,[f],as_of=NOW)
    assert report['counts']=={'MISSING_CALENDAR':1,'AVAILABLE_SHADOW':1}
    assert report['issues']['INVALID_OR_UNAVAILABLE_CALENDAR_SOURCE']==1


def test_latest_actual_predicted_version_is_not_replaced_by_earlier_confirmed_bytes(tmp_path):
    db,f,sources=environment(tmp_path);observe(tmp_path,db,sources)
    store=SnapshotStore(db,tmp_path/'data/raw')
    body={'data':lineup(match(),kind='predicted',confirmed=False)}
    url=BASE+'/api/football/matches/mt_100/lineups'
    saved=store.save('the_stats_api','daily_context',json.dumps(body).encode(),'application/json',
                     fetched_at=NOW-timedelta(minutes=3),source_url=url)
    observe(tmp_path,db,[(url,saved)],observed=NOW-timedelta(minutes=3),available=NOW-timedelta(minutes=2))
    row=inspect_rotation(tmp_path,[f],as_of=NOW)['features'][0]['teams'][0]
    assert row['current_lineup_status']=='UNCONFIRMED_OR_PREDICTED_LINEUP'
    assert row['starting_xi_changes'] is None
