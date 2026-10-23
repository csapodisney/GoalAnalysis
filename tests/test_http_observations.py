"""Real raw/SQLite/append-only receipts, synthetic HTTP transport and clocks."""
import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from goal_analysis.jobs.the_stats_context import Resources
from goal_analysis.providers.the_stats_api import TheStatsApiClient
from goal_analysis.storage import SnapshotStore
from goal_analysis.storage.http_observations import HttpObservations, read_http_observation
from goal_analysis.storage.raw_evidence import read_witnessed_raw

NOW = datetime(2026, 10, 22, 14, tzinfo=UTC)
URL = 'https://api.thestatsapi.com/api/football/competitions/comp_3039'
ENDPOINT = '/football/competitions/comp_3039'
RAW = b'{ "data": {"id":"comp_3039","value":1} }'


def environment(root):
    current = [NOW]
    transport = Mock(return_value=(RAW, {}, 200))
    client = TheStatsApiClient('safe-test-key', transport, clock=lambda: current[0], max_requests=10)
    resources = Resources(root, client, lambda: current[0], 10)
    return current, transport, client, resources


def read(root, source, now):
    with closing(sqlite3.connect((root/'data/goal_analysis.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        return read_http_observation(db,root,source['http_observation_id'],now,source=source)


def counts(database):
    with closing(database.connect()) as db:
        return tuple(db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in
                     ('raw_snapshots','the_stats_http_observations','the_stats_http_witnesses','temporal_receipts'))


def test_actual_response_committed_and_raw_witnessed_without_inventing_quote_time(tmp_path):
    _, transport, client, resource=environment(tmp_path)
    data,source,error=resource.get(ENDPOINT)
    assert error is None and data=={'id':'comp_3039','value':1}
    assert transport.call_count==client.request_count==1
    assert source['http_observation_status']=='VERIFIED'
    body,proof=read(tmp_path,source,NOW)
    assert body==json.loads(RAW) and proof['times']['observed_at']==NOW.isoformat(timespec='microseconds')
    assert proof['times']['published_at'] is None and 'quoted_at' not in source
    assert proof['kind']=='ACTUAL_AUTHENTICATED_HTTP_RESPONSE_NOT_QUOTE_TIME'
    with closing(resource.database.connect()) as db:
        raw=dict(db.execute('SELECT * FROM raw_snapshots').fetchone())
        body,original=read_witnessed_raw(db,raw,tmp_path,NOW)
    assert body==json.loads(RAW) and original['times']['available_at']
    assert counts(resource.database)==(1,1,1,1)


def test_new_process_cache_and_memo_never_create_fresh_receipt_or_change_source_clock(tmp_path):
    current,transport,client,resource=environment(tmp_path)
    original=resource.get(ENDPOINT)
    baseline=counts(resource.database)
    current[0]+=timedelta(minutes=5)
    assert resource.get(ENDPOINT)==original
    warm=Resources(tmp_path,client,lambda:current[0],0)
    assert warm.get(ENDPOINT)==original and warm.usage['cache_hits']==1
    assert transport.call_count==1 and counts(resource.database)==baseline


def test_identical_bytes_really_received_twice_preserve_old_raw_and_distinct_actual_times(tmp_path):
    current,transport,client,resource=environment(tmp_path)
    first=resource.get(ENDPOINT)[1]
    with closing(resource.database.connect()) as db:
        raw_before=dict(db.execute('SELECT * FROM raw_snapshots').fetchone())
    current[0]+=timedelta(minutes=31)
    second=Resources(tmp_path,client,lambda:current[0],1).get(ENDPOINT)[1]
    assert transport.call_count==2 and first['content_hash']==second['content_hash']
    assert first['http_observation_id']!=second['http_observation_id']
    assert first['observed_at']!=second['observed_at']
    assert counts(resource.database)==(1,2,2,1)
    assert read(tmp_path,first,NOW)[1]['times']['observed_at']==NOW.isoformat(timespec='microseconds')
    with pytest.raises(ValueError,match='FUTURE_'):
        read(tmp_path,second,NOW)
    assert read(tmp_path,second,current[0])[1]['times']['observed_at']==current[0].isoformat(timespec='microseconds')
    with closing(resource.database.connect()) as db:
        assert dict(db.execute('SELECT * FROM raw_snapshots').fetchone())==raw_before


def test_same_body_different_actual_url_is_separate_observation_not_overwritten_snapshot(tmp_path):
    current,transport,_,resource=environment(tmp_path)
    first=resource.get(ENDPOINT)[1]
    current[0]+=timedelta(seconds=1)
    second=resource.get('/football/competitions/comp_0256')[1]
    assert first['content_hash']==second['content_hash'] and transport.call_count==2
    assert read(tmp_path,second,current[0])[1]['source_url'].endswith('comp_0256')
    with closing(resource.database.connect()) as db:
        assert db.execute('SELECT source_url FROM raw_snapshots').fetchone()[0]==URL


def test_retry_of_same_actual_client_receipt_is_idempotent_but_changed_identity_is_rejected(tmp_path):
    current,_,client,resource=environment(tmp_path)
    source=resource.get(ENDPOINT)[1]
    receipt=deepcopy(client.last_response)
    baseline=counts(resource.database)
    current[0]+=timedelta(seconds=1)
    store=HttpObservations(resource.database,tmp_path,clock=lambda:current[0])
    result=store.admit(receipt)
    assert result['http_observation_id']==source['http_observation_id']
    assert counts(resource.database)==baseline
    receipt['source_url']=URL+'?bad=1'
    with pytest.raises(ValueError):
        store.admit(receipt)
    receipt['source_url']=URL.replace('comp_3039','comp_0256')
    with pytest.raises(ValueError,match='CONFLICTING_HTTP_RESPONSE_ID'):
        store.admit(receipt)


@pytest.mark.parametrize('field,value', [('observed_at',(NOW+timedelta(seconds=1)).isoformat()),
    ('source_url',URL.replace('comp_3039','comp_0256')),('content_hash','a'*64),
    ('http_receipt_id','a'*64),('http_observation_id','a'*32)])
def test_cache_source_cannot_borrow_different_observation_even_with_matching_body(tmp_path,field,value):
    _,_,_,resource=environment(tmp_path)
    source=deepcopy(resource.get(ENDPOINT)[1]);source[field]=value
    with pytest.raises(ValueError):
        read(tmp_path,source,NOW)


@pytest.mark.parametrize('table', ['the_stats_http_observations','the_stats_http_witnesses'])
@pytest.mark.parametrize('operation', ['UPDATE','DELETE'])
def test_append_only_database_guards(tmp_path,table,operation):
    _,_,_,resource=environment(tmp_path);resource.get(ENDPOINT)
    with resource.database.transaction() as db, pytest.raises(sqlite3.IntegrityError,match='append-only'):
        db.execute(f'UPDATE {table} SET response_id=response_id' if operation=='UPDATE' else f'DELETE FROM {table}')


def test_two_real_responses_at_same_clock_have_distinct_ids_not_synthetic_duplicate_cache_hits(tmp_path):
    _,transport,client,resource=environment(tmp_path)
    first=resource.get(ENDPOINT)[1]
    client.request(ENDPOINT)
    new=HttpObservations(resource.database,tmp_path,clock=lambda:NOW).admit(client.last_response)
    assert new['http_observation_id']!=first['http_observation_id'] and transport.call_count==2
    assert counts(resource.database)==(1,2,2,1)


def test_observer_failure_isolated_and_cache_has_no_new_availability_claim(tmp_path,monkeypatch):
    _,transport,_,resource=environment(tmp_path)
    monkeypatch.setattr(HttpObservations,'admit',Mock(side_effect=ValueError('controlled failure')))
    data,source,error=resource.get(ENDPOINT)
    assert data and error is None and transport.call_count==1
    assert source['http_observation_status']=='UNAVAILABLE' and 'http_observation_id' not in source
    assert 'quoted_at' not in source


def test_legacy_warm_cache_is_not_retroactively_certified_as_a_new_response(tmp_path):
    current,transport,client,resource=environment(tmp_path)
    source=resource.get(ENDPOINT)[1]
    legacy={k:v for k,v in source.items() if not k.startswith('http_')}
    from goal_analysis.jobs.the_stats_context import VERSION
    key=json.dumps([ENDPOINT,{}],sort_keys=True,separators=(',',':'))
    resource.cache.set(VERSION,key,{'source':legacy},timedelta(minutes=30),now=NOW)
    before=counts(resource.database)
    current[0]+=timedelta(seconds=1)
    _,returned,error=Resources(tmp_path,client,lambda:current[0],0).get(ENDPOINT)
    assert error is None and returned==legacy
    assert transport.call_count==1 and counts(resource.database)==before


@pytest.mark.parametrize('field,value',[('authenticated',False),('http_status',429),
                                      ('payload_sha256','x'),('response_id',None)])
def test_unsuccessful_or_invented_client_metadata_is_never_admitted(tmp_path,field,value):
    _,_,client,resource=environment(tmp_path);resource.get(ENDPOINT)
    receipt={**client.last_response,field:value}
    before=counts(resource.database)
    with pytest.raises(ValueError):
        HttpObservations(resource.database,tmp_path,clock=lambda:NOW).admit(receipt)
    assert counts(resource.database)==before


def test_raw_corruption_or_metadata_mutation_cannot_borrow_prior_http_witness(tmp_path):
    _,_,_,resource=environment(tmp_path)
    source=resource.get(ENDPOINT)[1]
    with closing(resource.database.connect()) as db:
        row=dict(db.execute('SELECT * FROM raw_snapshots').fetchone())
    path=tmp_path/'data/raw'/row['relative_path']
    path.write_bytes(RAW+b' ')
    with pytest.raises(ValueError,match='SIZE_MISMATCH'):
        read(tmp_path,source,NOW)
    path.write_bytes(RAW)
    with resource.database.transaction() as db:
        db.execute('UPDATE raw_snapshots SET resource_type=?',('modified',))
    with pytest.raises(ValueError,match='METADATA_CHANGED'):
        read(tmp_path,source,NOW)


def test_failure_after_observation_commit_is_not_an_availability_witness_and_resume_is_idempotent(tmp_path):
    _,_,client,resource=environment(tmp_path)
    # Persist only raw data, then use an actual synthetic client response.
    client.request(ENDPOINT)
    SnapshotStore(resource.database,tmp_path/'data/raw').save('the_stats_api','daily_context',RAW,
        'application/json',NOW,URL)
    ticks=iter([NOW,NOW-timedelta(seconds=1)])
    store=HttpObservations(resource.database,tmp_path,clock=lambda:next(ticks))
    with pytest.raises(ValueError,match='FUTURE_'):
        store.admit(client.last_response)
    assert counts(resource.database)==(1,1,0,0)
    with closing(resource.database.connect()) as db, pytest.raises(ValueError,match='UNWITNESSED'):
        read_http_observation(db,tmp_path,client.last_response['response_id'],NOW)
    result=HttpObservations(resource.database,tmp_path,clock=lambda:NOW).admit(client.last_response)
    assert result['http_observation_status']=='VERIFIED' and counts(resource.database)==(1,1,1,1)


def test_collected_source_with_new_receipt_remains_valid_for_existing_context_schema(tmp_path):
    from goal_analysis.agents.grok_stats_data import _source

    _,_,_,resource=environment(tmp_path)
    source=resource.get(ENDPOINT)[1]
    assert _source(source,NOW)==source
    legacy={k:v for k,v in source.items() if not k.startswith('http_')}
    assert _source(legacy,NOW)==legacy
    for changed in ({'http_receipt_id':'bad'}, {'http_observation_status':'FRESH_QUOTE'},
                    {'unknown_extra':'forbidden'}):
        with pytest.raises(ValueError):
            _source({**source,**changed},NOW)
