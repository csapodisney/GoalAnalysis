"""Reviewed explicit-null semantics, independently pinned to original provider docs.

This is not a league-name/date/matchday or competition-rules fallback. Missing
JSON keys are not explicit null. Current rollout is exactly one native scope.
"""
import json
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.leakage import require_known_before
from goal_analysis.normalization.training_datasets import sha

VERSION = 'reviewed-thestats-explicit-null-stage-v1'
PIN = '15c251449cb3913eeb2fab1fa27e41a030b7108d0b16dc4a62eb7d7826cbcf29'
SCOPE = ('comp_0406','sn_6190936')


def read_contract(root, *, as_of):
    root=Path(root).resolve();cutoff=utc(as_of);folder=root/'data/arthur/provider-contracts/match-stage'
    try:
        if not folder.resolve().is_relative_to(root):raise ValueError('CONTRACT_PATH_ESCAPE')
        path=folder/(PIN+'.json')
        if path.is_symlink() or not 0<path.stat().st_size<=16000:raise ValueError('CONTRACT_MANIFEST_SIZE')
        blob=path.read_bytes()
        if sha256(blob).hexdigest()!=PIN:raise ValueError('CONTRACT_MANIFEST_HASH')
        value=json.loads(blob)
        if (value['version']!=VERSION or value['source_url']!='https://api.thestatsapi.com/llms.txt'
                or value['scope']!=list(SCOPE) or value['meaning']!='EXPLICIT_NULL_STAGE_REGULAR_LEAGUE_FIXTURE'
                or type(value['local_review_days']) is not int or value['local_review_days']!=7
                or value['available_at']!=value['reviewed_at']
                or value['schema_sections']!=['Match','MatchDetail']):
            raise ValueError('UNREVIEWED_STAGE_SEMANTICS')
        times={'observed_at':value['observed_at'],'available_at':value['available_at'],
            'published_at':None,'ingested_at':None,'event_time':None,'event_time_kind':'UNKNOWN',
            'availability_basis':'SOURCE_PRESENCE_UPPER_BOUND'}
        require_known_before(times,as_of=cutoff)
        if cutoff-utc(value['reviewed_at'])>timedelta(days=7):raise ValueError('STAGE_CONTRACT_EXPIRED')
        digest=value['content_sha256']
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('INVALID_STAGE_DOCUMENT_DIGEST')
        source=folder/(digest+'.txt')
        if not source.resolve().is_relative_to(folder.resolve()) or not 0<source.stat().st_size<=500000:
            raise ValueError('STAGE_DOCUMENT_PATH_OR_SIZE')
        raw=source.read_bytes()
        if len(raw)!=value['size_bytes'] or sha256(raw).hexdigest()!=digest:raise ValueError('STAGE_DOCUMENT_HASH')
        return {'receipt_id':sha([VERSION,PIN]),'source_url':value['source_url'],
            'content_sha256':digest,'review_bundle_sha256':PIN,'times':times,
            'version':VERSION,'scope':list(SCOPE),'basis':value['meaning']}
    except (ValueError,KeyError,TypeError,OSError):
        return None
