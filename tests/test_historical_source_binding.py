"""Exact original raw/page evidence, append-only NOW admission, no API."""
import json
import socket
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from test_historical_result_sync import NOW, legacy_rows, match, setup, version_count, watermark

from goal_analysis.jobs import historical_result_sync as sync
from goal_analysis.jobs.the_stats_history import _atomic, _read, _record
from goal_analysis.storage.sports_archive import _FIELDS, _hash
from goal_analysis.storage.temporal import TemporalStore


def alias(root,*,same_url=False,kind='daily_context'):
    database,archive,snapshots=setup(root)
    query={'competition_id':'comp_4643','status':'finished','sort':'-utc_date',
           'date_from':'2026-10-01','date_to':'2026-10-19','utc_offset':'+00:00'}
    params={**query,'page':1,'per_page':100}
    url='https://api.thestatsapi.com/api/football/matches?'+urlencode(params)
    first=url if same_url else 'https://api.thestatsapi.com/api/football/matches?team_id=tm_1'
    body={'data':[match(1)],'meta':{'page':1,'per_page':100,'total_pages':1}}
    saved=snapshots.save('the_stats_api',kind,json.dumps(body).encode(),'application/json',
                        NOW-timedelta(hours=2),first)
    observed=NOW-timedelta(hours=1);plan='a'*64
    receipt={'plan_sha256':plan,'params':params,'content_hash':saved.content_hash,
             'source_url':url,'observed_at':observed.isoformat()}
    path=root/'data/arthur/the-stats-history'/plan/'page-00000-000001.json'
    _atomic(path,receipt)
    record=_record(body['data'][0],query,NOW)
    result=archive.ingest({'provider':'the_stats_api','league_code':'comp_4643','season_code':'sn_1',
        'records':[record],'columns':sorted(body['data'][0])},source_url=url,fetched_at=observed,
        content_hash=saved.content_hash,metadata={'plan_sha256':plan,'query':query,
                                                'raw_response_sha256':saved.content_hash})
    return database,saved,path,receipt,result['import_id']


def run(root,**kwargs):
    return sync.sync_stats_results(root,**{'clock':lambda:NOW,**kwargs})


@pytest.mark.parametrize('same_url,kind',[(False,'daily_context'),(True,'daily_context'),(False,'historical_matches')])
def test_exact_later_page_receipt_recovers_raw_alias_without_mutation_or_backdated_availability(tmp_path,monkeypatch,same_url,kind):
    db,saved,_,_,number=alias(tmp_path,same_url=same_url,kind=kind)
    before=legacy_rows(db);old_bytes=(tmp_path/'data/raw'/saved.relative_path).read_bytes()
    monkeypatch.setattr(socket.socket,'connect',lambda *a:(_ for _ in ()).throw(AssertionError('NO_API')))
    result=run(tmp_path)
    assert result['status']=='COMPLETE' and result['after_import_id']==number
    assert result['counts']['versions_created']==result['counts']['accepted_rows']==1
    proof=_read(tmp_path/result['source_bindings'][0]['proof_path'])
    assert proof['kind']=='EXACT_DURABLE_PAGE_ALIAS_AND_RAW_RECORDS'
    assert proof['page_receipt']['kind']=='ORIGINAL_DURABLE_HISTORICAL_PAGE_RECEIPT_NOT_HTTP_OBSERVATION'
    assert proof['raw_presence_proof']['times']['available_at']==NOW.isoformat(timespec='microseconds')
    assert proof['observed_at']==(NOW-timedelta(hours=1)).isoformat(timespec='microseconds')
    assert legacy_rows(db)==before and (tmp_path/'data/raw'/saved.relative_path).read_bytes()==old_bytes
    with closing(db.connect()) as connection:
        assert connection.execute("SELECT count(*) FROM temporal_receipts WHERE namespace='raw'").fetchone()[0]==1
        assert connection.execute('SELECT ingested_at FROM historical_result_versions').fetchone()[0]==NOW.isoformat(timespec='microseconds')
    assert run(tmp_path)['processed_imports']==0 and version_count(db)==1


@pytest.mark.parametrize('fault',['missing','checksum','clock','params','url','hash','extra','filename','ambiguous','plan'])
def test_missing_or_forged_page_proof_stops_without_sink_or_watermark(tmp_path,fault):
    db,_,path,receipt,_=alias(tmp_path);bad=deepcopy(receipt)
    if fault=='missing':path.unlink()
    elif fault=='checksum':path.write_text(json.dumps({'data':receipt,'sha256':'0'*64}))
    elif fault=='filename':path.rename(path.with_name('page-00000-000002.json'))
    elif fault=='ambiguous':_atomic(path.with_name('page-00001-000001.json'),receipt)
    else:
        if fault=='clock':bad['observed_at']=(NOW+timedelta(seconds=1)).isoformat()
        elif fault=='params':bad['params']['competition_id']='comp_999'
        elif fault=='url':bad['source_url']+='&status=scheduled'
        elif fault=='hash':bad['content_hash']='0'*64
        elif fault=='extra':bad['invented_confirmation']=True
        elif fault=='plan':bad['plan_sha256']='b'*64
        _atomic(path,bad)
    before=legacy_rows(db);result=run(tmp_path)
    assert result['status']=='INVALID_SOURCE' and result['after_import_id']==0
    assert not watermark(tmp_path).exists() and version_count(db)==0
    assert legacy_rows(db)==before


def test_self_consistent_archive_row_must_actually_exist_in_original_raw_including_json_types(tmp_path):
    db,_,_,_,_=alias(tmp_path)
    with db.transaction() as connection:
        row=dict(connection.execute('SELECT * FROM sports_archive_observations').fetchone())
        raw=json.loads(row['raw_json']);raw['retained'][0]=0  # false != JSON number0
        archived={k:row[k] for k in _FIELDS};archived.update(raw=raw,odds_snapshot_types={})
        connection.execute('DROP TRIGGER sports_archive_observations_no_update')
        connection.execute('UPDATE sports_archive_observations SET raw_json=?,normalized_content_hash=?',
                           (json.dumps(raw),_hash(archived)))
    result=run(tmp_path)
    assert result['error_code']=='ARCHIVE_RECORD_NOT_IN_ORIGINAL_RAW'
    assert not watermark(tmp_path).exists() and version_count(db)==0


def test_changed_original_raw_metadata_after_presence_witness_is_not_rewitnessed(tmp_path):
    db,saved,_,_,_=alias(tmp_path)
    temporal=TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW-timedelta(minutes=1))
    temporal.initialize();temporal.capture('raw',limit=100)
    with db.transaction() as connection:
        old=[tuple(r) for r in connection.execute('SELECT * FROM temporal_receipts')]
        connection.execute('UPDATE raw_snapshots SET source_url=? WHERE content_hash=?',
            ('https://api.thestatsapi.com/api/football/matches?team_id=tm_2',saved.content_hash))
    result=run(tmp_path)
    assert result['error_code']=='HISTORICAL_RAW_PRESENCE_ADMISSION_FAILED'
    with closing(db.connect()) as connection:
        assert old==[tuple(r) for r in connection.execute('SELECT * FROM temporal_receipts')]
    assert not watermark(tmp_path).exists() and version_count(db)==0


def test_crash_after_sink_before_watermark_keeps_presence_and_source_proof_idempotent(tmp_path,monkeypatch):
    db,_,_,_,_=alias(tmp_path);atomic=sync._atomic
    def crash(path,value):
        if path.name=='watermark.json':raise OSError('after-proof-before-cursor')
        atomic(path,value)
    monkeypatch.setattr(sync,'_atomic',crash)
    with pytest.raises(OSError,match='before-cursor'):run(tmp_path)
    assert version_count(db)==1
    with closing(db.connect()) as connection:
        old=[tuple(r) for r in connection.execute('SELECT * FROM temporal_receipts')]
    proof=(tmp_path/'data/arthur/historical-results-sync/source-proofs/import-1.json').read_bytes()
    monkeypatch.setattr(sync,'_atomic',atomic)
    result=run(tmp_path,clock=lambda:NOW+timedelta(hours=1))
    assert result['status']=='COMPLETE' and result['counts']['versions_created']==0
    assert (tmp_path/'data/arthur/historical-results-sync/source-proofs/import-1.json').read_bytes()==proof
    with closing(db.connect()) as connection:
        assert old==[tuple(r) for r in connection.execute('SELECT * FROM temporal_receipts')]


def test_page_scope_cannot_use_unfiltered_or_wrong_competition_receipt(tmp_path):
    db,_,_,_,_=alias(tmp_path)
    with db.transaction() as connection:
        source=dict(connection.execute('SELECT * FROM sports_archive_imports').fetchone())
        connection.execute('DROP TRIGGER sports_archive_imports_no_update')
        connection.execute('UPDATE sports_archive_imports SET source_url=?',
                           (source['source_url'].replace('status=finished','status=scheduled'),))
    assert run(tmp_path)['error_code']=='INVALID_HISTORICAL_PAGE_SCOPE'
    assert version_count(db)==0


def test_receipt_search_is_bounded_before_source_admission(tmp_path,monkeypatch):
    from goal_analysis.jobs import historical_source_binding
    db,_,_,_,_=alias(tmp_path)
    monkeypatch.setattr(historical_source_binding,'MAX_RECEIPTS',0)
    assert run(tmp_path)['error_code']=='HISTORICAL_PAGE_RECEIPT_BUDGET'
    assert version_count(db)==0 and not watermark(tmp_path).exists()


def test_actual_file_byte_budget_precedes_full_read(tmp_path):
    db,saved,_,_,_=alias(tmp_path)
    (tmp_path/'data/raw'/saved.relative_path).write_bytes(b' '*2_000_001)
    assert run(tmp_path)['error_code']=='MISSING_OR_OVERSIZED_HISTORICAL_RAW'
    assert version_count(db)==0


def test_source_dataset_hash_cannot_be_ignored(tmp_path):
    db,_,_,_,_=alias(tmp_path)
    with db.transaction() as connection:
        connection.execute('DROP TRIGGER sports_archive_imports_no_update')
        connection.execute("UPDATE sports_archive_imports SET dataset_hash=?",('0'*64,))
    assert run(tmp_path)['error_code']=='ARCHIVE_DATASET_HASH_MISMATCH'
    assert version_count(db)==0 and not watermark(tmp_path).exists()
