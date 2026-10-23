"""Read-only, exact fixture-bound news/availability receipts; never new research.

X snowflake/author/citation checks verify identity and timestamps, not truth.
All assertion versions and import provenance are preserved, not vote-counted.
"""
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from goal_analysis.features.human_factors import VERSION, build_human_factors
from goal_analysis.jobs.competition_types import inspect_competition_types
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.leakage import require_known_before
from goal_analysis.normalization.temporal import project_times
from goal_analysis.normalization.training_datasets import sha, snapshot
from goal_analysis.storage.canonical_registry import digest, encoded
from goal_analysis.storage.sports_news import _assertion, _digest
from goal_analysis.storage.temporal import _key, _select, _source_hash
from goal_analysis.storage.training_datasets import DatasetStore


def _read_news(db, row, cutoff):
    key=_key('news',row)
    witness=db.execute("SELECT * FROM temporal_receipts WHERE namespace='news' AND source_key=?",(key,)).fetchone()
    if witness is None:raise ValueError('MISSING_NEWS_AVAILABILITY_RECEIPT')
    if (_source_hash(row)!=witness['source_sha256'] or witness['receipt_id']!=
            digest(['news',key,witness['source_sha256']])):
        raise ValueError('NEWS_SOURCE_CHANGED_AFTER_WITNESS')
    times=project_times('news',row,available_at=witness['available_at'])
    require_known_before(times,as_of=cutoff)
    if encoded(times)!=witness['times_json']:raise ValueError('NEWS_TIME_PROOF_MISMATCH')
    assertion,_,fetched=_assertion({**row,'claim_id':row['origin_claim_id'],
                                  'fetched_at':row['source_fetched_at']},
                                 utc(row['observed_at']).isoformat(timespec='microseconds'))
    if any(row.get(k)!=v for k,v in assertion.items()) or row['content_hash']!=_digest(assertion):
        raise ValueError('NEWS_ASSERTION_CONTENT_HASH_OR_STATUS_MISMATCH')
    payload={**assertion,'assertion_hash':row['content_hash'],'source_fetched_at':fetched,
             'observed_at':utc(row['observed_at']).isoformat(timespec='microseconds'),
             'request_id':row['request_id'],'model':row['model']}
    return {'record_id':witness['receipt_id'],'payload':payload,'payload_sha256':sha(payload),
            'receipt_id':witness['receipt_id'],'source_sha256':witness['source_sha256'],
            'times':times,'source_key':key}


def inspect_human_factors(root, fixtures, *, as_of):
    root,cutoff=Path(root).resolve(),utc(as_of)
    competition=inspect_competition_types(root,fixtures,as_of=cutoff)
    metadata={r['fixture_id']:r for r in competition['rows']};targets=[];issues=Counter()
    for f in fixtures:
        row=metadata[f['fixture_id']]
        if row.get('native_fixture_id')!=f.get('provider_fixture_id'):
            issues['MISSING_WITNESSED_FIXTURE_IDENTITY']+=1;continue
        if cutoff>utc(f['kickoff'])-timedelta(minutes=15):
            issues['PREMATCH_LOCKOUT']+=1;continue
        targets.append({'fixture_id':f['fixture_id'],'kickoff':utc(f['kickoff']).isoformat()})
    envelopes=[];path=root/'data/goal_analysis.sqlite3'
    if path.exists() and targets:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {'sports_news_assertions','sports_news_imports','sports_news_import_claims','temporal_receipts'}<=names:
                ids=[t['fixture_id'] for t in targets];marks=','.join('?' for _ in ids)
                rows=[dict(r) for r in db.execute(_select('news')+
                    f" WHERE a.fixture_namespace='arthur' AND a.fixture_id IN ({marks}) LIMIT 2001",ids)]
                if len(rows)>2000:raise ValueError('NEWS_SOURCE_BUDGET_EXCEEDED')
                for row in rows:
                    try:envelopes.append(_read_news(db,row,cutoff))
                    except (ValueError,TypeError,KeyError,OverflowError):
                        issues['INVALID_OR_UNAVAILABLE_NEWS_SOURCE']+=1
    view=build_human_factors(targets,envelopes,as_of=cutoff)
    dataset=snapshot(view['rows'],role='daily_human_factors_shadow',as_of=cutoff,recipe={'version':VERSION},
        lineage={'competition_dataset':competition['dataset'],'news':envelopes})
    return {'version':VERSION,'mode':'SHADOW','status':'PARTIAL','as_of':cutoff.isoformat(),
            'rows':view['rows'],'counts':dict(Counter(r['status'] for r in view['rows'])),
            'issues':dict(issues),'dataset':dataset,'source_channel':'X_MODEL_SUMMARIES_ONLY',
            'accepted_assertion_imports':len(envelopes),'external_requests':0,'production_writes':0,
            'live_decision_effect':False,'new_receipts_created':0}


def archive_human_factors(root, report, *, as_of):
    try:
        view=inspect_human_factors(root,report.get('fixtures',[]),as_of=as_of)
        created=DatasetStore(Path(root)/'data/arthur/human-factors/datasets').put(view['dataset'])
        return {k:view[k] for k in ('version','mode','status','counts','issues','external_requests',
                                   'live_decision_effect','accepted_assertion_imports','source_channel')} | {
            'dataset_id':view['dataset']['manifest']['dataset_id'],'dataset_created':created}
    except Exception:  # noqa: BLE001 - optional metadata cannot veto a saved daily ticket
        return {'version':VERSION,'mode':'SHADOW','status':'UNAVAILABLE','external_requests':0,
                'live_decision_effect':False,'reason':'HUMAN_FACTORS_ARCHIVE_FAILED'}
