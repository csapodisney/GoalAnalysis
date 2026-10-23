"""Bind archive observations to original raw bytes and durable page receipts.

A historical page receipt is not a reconstructed HTTP observation. Any new
physical presence witness and sink ingestion are recorded at actual NOW.
"""
import json
import re
from collections import Counter
from datetime import date
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.live_source import stats_source_url
from goal_analysis.storage.sports_archive import _hash

from .historical_results import _raw
from .the_stats_history import _read

MAX_BYTES=2_000_000
MAX_RECEIPTS=2000


def _page_receipt(root,source,body):
    meta=json.loads(source['metadata_json']);plan=meta.get('plan_sha256')
    if not isinstance(plan,str) or not re.fullmatch('[0-9a-f]{64}',plan):
        raise ValueError('MISSING_HISTORICAL_PAGE_RECEIPT')
    url=urlsplit(stats_source_url(source['source_url']));query=parse_qs(url.query)
    allowed={'competition_id','season_id','status','sort','date_from','date_to','utc_offset','page','per_page'}
    required=allowed-{'season_id'}
    if (url.path!='/api/football/matches' or not required<=set(query)<=allowed
            or any(len(v)!=1 for v in query.values())):
        raise ValueError('INVALID_HISTORICAL_PAGE_SCOPE')
    params={k:v[0] for k,v in query.items()}
    for key in ('page','per_page'):
        if not re.fullmatch('[1-9][0-9]{0,5}',params[key]):
            raise ValueError('INVALID_HISTORICAL_PAGE_SCOPE')
        params[key]=int(params[key])
    if (params['competition_id']!=source['league_code'] or params.get('season_id',source['season_code'])!=source['season_code']
            or params['status']!='finished' or params['sort']!='-utc_date'
            or params['utc_offset']!='+00:00' or params['per_page']!=100):
        raise ValueError('INVALID_HISTORICAL_PAGE_SCOPE')
    for key in ('date_from','date_to'):
        if date.fromisoformat(params[key]).isoformat()!=params[key]:
            raise ValueError('INVALID_HISTORICAL_PAGE_SCOPE')
    if params['date_from']>params['date_to']:
        raise ValueError('INVALID_HISTORICAL_PAGE_SCOPE')
    if (meta.get('query')!={k:v for k,v in params.items() if k not in ('page','per_page')}
            or meta.get('raw_response_sha256')!=source['content_hash']):
        raise ValueError('HISTORICAL_ARCHIVE_RECEIPT_SCOPE_MISMATCH')
    paging=body.get('meta',{})
    if (type(paging.get('page')) is not int or type(paging.get('per_page')) is not int
            or paging.get('page')!=params['page'] or paging.get('per_page')!=100
            or type(paging.get('total_pages')) is not int or paging['total_pages']<params['page']):
        raise ValueError('HISTORICAL_RAW_PAGE_MISMATCH')
    folder=(root/'data/arthur/the-stats-history'/plan).resolve()
    if not folder.is_relative_to((root/'data/arthur/the-stats-history').resolve()):
        raise ValueError('HISTORICAL_RECEIPT_PATH_ESCAPE')
    matches=[];count=0;size=0
    for path in folder.glob('page-*.json'):
        if not path.resolve().is_relative_to(folder):
            raise ValueError('HISTORICAL_RECEIPT_PATH_ESCAPE')
        count+=1;size+=path.stat().st_size
        if count>MAX_RECEIPTS or size>8_000_000:
            raise ValueError('HISTORICAL_PAGE_RECEIPT_BUDGET')
        name=re.fullmatch(r'page-([0-9]{5})-([0-9]{6})\.json',path.name)
        if name is None or int(name[2])!=params['page']:
            continue
        receipt=_read(path)
        if receipt.get('content_hash')!=source['content_hash']:
            continue
        expected={'plan_sha256':plan,'params':params,'content_hash':source['content_hash'],
                  'source_url':source['source_url']}
        if ({k:v for k,v in receipt.items() if k!='observed_at'}!=expected
                or utc(receipt['observed_at'])!=utc(source['fetched_at'])):
            continue
        matches.append({'path':str(path.relative_to(root)),'sha256':sha256(path.read_bytes()).hexdigest(),
            'receipt':receipt,'kind':'ORIGINAL_DURABLE_HISTORICAL_PAGE_RECEIPT_NOT_HTTP_OBSERVATION'})
    if len(matches)!=1:
        raise ValueError('MISSING_OR_AMBIGUOUS_HISTORICAL_PAGE_RECEIPT')
    return matches[0]


def bind_source(root,db,source,now):
    if source['raw_provider']!='the_stats_api' or not source['relative_path']:
        raise ValueError('MISSING_RAW_SOURCE_LINK')
    if source['raw_snapshot_hash']!=source['content_hash']:
        raise ValueError('ARCHIVE_RAW_HASH_MISMATCH')
    if utc(source['raw_fetched_at'])>utc(source['fetched_at']):
        raise ValueError('ARCHIVE_RECEIPT_PRECEDES_RAW_RECEIPT')
    row=db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?',(source['content_hash'],)).fetchone()
    if row is None or not 0<row['size_bytes']<=MAX_BYTES:
        raise ValueError('MISSING_OR_OVERSIZED_HISTORICAL_RAW')
    path=(root/'data/raw'/source['relative_path']).resolve()
    if not path.is_relative_to((root/'data/raw').resolve()):
        raise ValueError('RAW_PATH_ESCAPES_ARCHIVE')
    if not 0<path.stat().st_size<=MAX_BYTES:
        raise ValueError('MISSING_OR_OVERSIZED_HISTORICAL_RAW')
    raw=_raw(root,source,now=now)
    if len(raw)!=row['size_bytes']:
        raise ValueError('HISTORICAL_RAW_SIZE_MISMATCH')
    body=json.loads(raw)
    if (not isinstance(body,dict) or not isinstance(body.get('data'),list)
            or len(body['data'])>100 or any(not isinstance(r,dict) for r in body['data'])):
        raise ValueError('BOUNDED_HISTORICAL_RAW_ROWS_REQUIRED')
    alias=(row['resource_type']!='historical_matches' or row['source_url']!=source['source_url']
           or utc(row['fetched_at'])!=utc(source['fetched_at']))
    proof={'kind':'ORIGINAL_HISTORICAL_RAW_RECEIPT','import_id':source['import_id'],
        'content_hash':source['content_hash'],'raw_first_resource_type':row['resource_type'],
        'source_url':source['source_url'],'observed_at':source['fetched_at'],
        'physical_presence_admission_required':alias}
    if alias:
        proof['page_receipt']=_page_receipt(root,source,body)
        proof['kind']='EXACT_DURABLE_PAGE_ALIAS_AND_RAW_RECORDS'
    return Counter(_hash(r) for r in body['data']),proof
