"""Read witnessed fixture calendars, taxonomy and XIs; archive diagnostics only.

Never fetch lineups, manufacture missing receipts or alter existing tickets.
Provider-confirmed XIs are not a claim that an official club source was read.
"""
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from goal_analysis.features.rotation import VERSION, build_rotation_features, lineup_payload
from goal_analysis.jobs.competition_types import inspect_competition_types
from goal_analysis.jobs.schedule_shadow import inspect_schedule, target_fixture
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha, snapshot
from goal_analysis.storage.context_sources import context_sources
from goal_analysis.storage.source_observations import read_source_raw
from goal_analysis.storage.training_datasets import DatasetStore


def _lineups(root, wanted, cutoff):
    path=root/'data/goal_analysis.sqlite3'
    issues=Counter();options={}
    if not path.exists() or not wanted:
        return [],issues
    urls={'https://api.thestatsapi.com/api/football/matches/'+key+'/lineups':key for key in wanted}
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'raw_snapshots','temporal_receipts'} <= tables:
            return [],issues
        marks=','.join('?' for _ in urls)
        rows=context_sources(db,cutoff=cutoff,where=f'a.source_url IN ({marks})',params=tuple(urls),
                             limit=3000,max_bytes=64*1024*1024)
        for row,source in rows:
            try:
                body,proof=read_source_raw(db,row,root,cutoff,source)
                key=urls[source['source_url']]
                options.setdefault(key,[]).append((body,proof))
            except (ValueError,KeyError,TypeError,OSError):
                issues['INVALID_OR_UNAVAILABLE_LINEUP_SOURCE']+=1
    envelopes=[]
    for key,values in sorted(options.items()):
        newest=max(utc(proof['times']['observed_at']) for _,proof in values)
        selected=[(body,proof) for body,proof in values if utc(proof['times']['observed_at'])==newest]
        if len({sha(body) for body,_ in selected})!=1:
            issues['CONFLICTING_LINEUP_SOURCES']+=1
            continue
        body,proof=selected[0];fixture=wanted[key]
        try:
            payload=lineup_payload(body['data'],fixture,historical=fixture['status']=='finished')
            envelopes.append({'record_id':sha([payload,proof]),'payload':payload,
                'payload_sha256':sha(payload),'receipt_id':proof['receipt_id'],
                'source_sha256':proof['source_sha256'],'times':proof['times'],'source_receipts':[proof]})
        except (ValueError,KeyError,TypeError):
            issues['INVALID_LINEUP_SCHEMA_OR_IDENTITY']+=1
    return envelopes,issues


def inspect_rotation(root, fixtures, *, as_of):
    root,cutoff=Path(root).resolve(),utc(as_of)
    schedule=inspect_schedule(root,fixtures,as_of=cutoff)
    competition=inspect_competition_types(root,fixtures,as_of=cutoff)
    contexts={r['fixture_id']:r for r in competition['rows']}
    native={}
    for f in fixtures:
        try:
            t=target_fixture(f);native[t['fixture_id']]=t
        except (ValueError,TypeError,KeyError):
            continue
    wanted=dict(native)
    for feature in schedule['features']:
        for team in feature['teams']:
            prev=team['previous_fixture']
            if prev is not None and prev['status']=='finished':
                old=wanted.get(prev['fixture_id'])
                if old is not None and old!=prev:
                    raise ValueError('CONFLICTING_PREVIOUS_MATCH_IDENTITY')
                wanted[prev['fixture_id']]=prev
    lineups,issues=_lineups(root,wanted,cutoff)
    features=[]
    for feature in schedule['features']:
        target=native[feature['fixture_id']]
        relevant={target['fixture_id']} | {t['previous_fixture']['fixture_id'] for t in feature['teams']
                                         if t['previous_fixture'] is not None}
        values=[e for e in lineups if e['payload']['fixture_id'] in relevant]
        result=build_rotation_features(target,feature['dataset']['lineage'],values,as_of=cutoff)
        result['competition_context']=contexts['the_stats_api:'+target['fixture_id']]
        # Context only: neither cup nor international fixtures automatically mean
        # rotation. Unknown stage remains unknown; no B-team/strength inference.
        features.append(result)
    counts=Counter(t['status'] for f in features for t in f['teams'])
    dataset=snapshot([{k:v for k,v in f.items() if k!='dataset'} for f in features],
        role='daily_rotation_shadow',as_of=cutoff,recipe={'version':VERSION},
        lineage={'schedule_dataset':schedule['dataset'],'competition_dataset':competition['dataset'],
                 'lineups':lineups})
    return {'version':VERSION,'mode':'SHADOW','as_of':cutoff.isoformat(),
            'status':'XI_COMPARISONS_SHADOW' if features and counts.get('XI_COMPARISON_SHADOW',0)==2*len(fixtures)
                     else 'PARTIAL',
            'counts':dict(counts),'features':features,'issues':dict(issues),
            'schedule_issues':schedule['issues'],'competition_issues':competition['issues'],
            'dataset':dataset,'external_requests':0,'production_writes':0,'live_decision_effect':False}


def archive_rotation(root, report, *, as_of):
    try:
        view=inspect_rotation(root,report.get('fixtures',[]),as_of=as_of)
        created=DatasetStore(Path(root)/'data/arthur/rotation/datasets').put(view['dataset'])
        return {k:view[k] for k in ('version','mode','status','counts','issues','external_requests',
                                   'live_decision_effect')} | {
            'dataset_id':view['dataset']['manifest']['dataset_id'],'dataset_created':created}
    except Exception:  # noqa: BLE001 - optional post-save diagnostics cannot veto tickets
        return {'version':VERSION,'mode':'SHADOW','status':'UNAVAILABLE','external_requests':0,
                'live_decision_effect':False,'reason':'ROTATION_ARCHIVE_FAILED'}
