"""Witnessed native tables, explicit ambiguity, SHADOW-only stakes diagnostics.

No automatic rule invention: only receipt-bound reviewed editions and complete
unambiguous phase tables admit conservative on-field bounds. Missing rules,
phase, group or participants remain descriptive/UNKNOWN, never mental-state
claims or a probability adjustment based on provider position alone.
"""
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from goal_analysis.features.stakes import (
    VERSION,
    build_stakes_features,
    normalize_table,
    raw_diagnostics,
)
from goal_analysis.jobs.competition_types import inspect_competition_types
from goal_analysis.jobs.official_rules_shadow import bind_rules, inspect_official_rules
from goal_analysis.jobs.schedule_shadow import target_fixture
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha, snapshot
from goal_analysis.storage.raw_evidence import read_witnessed_raw
from goal_analysis.storage.training_datasets import DatasetStore


def _tables(root, scopes, cutoff):
    path=root/'data/goal_analysis.sqlite3';tables={};diagnostics=[];proofs=[]
    if not path.exists() or not scopes:return tables,diagnostics,proofs
    urls={'https://api.thestatsapi.com/api/football/competitions/'+comp+'/seasons/'+season+'/standings':
          (comp,season) for comp,season in scopes}
    candidates={}
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'raw_snapshots','temporal_receipts'}<=names:return tables,diagnostics,proofs
        marks=','.join('?' for _ in urls)
        rows=[dict(r) for r in db.execute(f"""SELECT rowid AS _sequence,* FROM raw_snapshots
            WHERE provider='the_stats_api' AND source_url IN ({marks}) LIMIT 1001""",tuple(urls))]
        if len(rows)>1000 or sum(r['size_bytes'] for r in rows)>64*1024*1024:
            raise ValueError('STANDINGS_SOURCE_BUDGET_EXCEEDED')
        for row in rows:
            try:
                body,proof=read_witnessed_raw(db,row,root,cutoff)
                key=urls[row['source_url']]
                candidates.setdefault(key,[]).append((body,proof))
            except (ValueError,KeyError,TypeError,OSError):
                diagnostics.append({'scope':list(urls[row['source_url']]),'status':'INVALID_OR_UNAVAILABLE_TABLE_SOURCE'})
    for key,values in sorted(candidates.items()):
        newest=max(utc(p['times']['observed_at']) for _,p in values)
        selected=[(body,p) for body,p in values if utc(p['times']['observed_at'])==newest]
        if len({sha(body) for body,_ in selected})!=1:
            diagnostics.append({'scope':list(key),'status':'CONFLICTING_TABLE_SOURCES'})
            continue
        body,proof=selected[0];proofs.append(proof)
        raw=body.get('data') if isinstance(body,dict) else None
        detail={'scope':list(key),**raw_diagnostics(raw),'receipt_id':proof['receipt_id']}
        try:
            normalize_table(raw)
            payload={'competition_id':key[0],'season_id':key[1],'raw_rows':raw}
            tables[key]={'record_id':sha([payload,proof]),'payload':payload,'payload_sha256':sha(payload),
                'receipt_id':proof['receipt_id'],'source_sha256':proof['source_sha256'],
                'times':proof['times'],'source_receipts':[proof]}
            detail['status']='SOURCE_SHAPE_VALIDATED_NOT_COMPLETE_ROSTER_CERTIFICATION'
        except (ValueError,TypeError,KeyError,AttributeError) as error:
            detail['status']=str(error) if isinstance(error,ValueError) else 'INVALID_TABLE_SCHEMA'
        diagnostics.append(detail)
    return tables,diagnostics,proofs


def inspect_stakes(root, fixtures, *, as_of):
    root,cutoff=Path(root).resolve(),utc(as_of)
    competition=inspect_competition_types(root,fixtures,as_of=cutoff)
    contexts={r['fixture_id']:r for r in competition['rows']};targets=[];issues=Counter()
    for f in fixtures:
        try:
            target=target_fixture(f);context=contexts[f['fixture_id']]
            if context.get('native_fixture_id')!=target['fixture_id']:
                raise ValueError('MISSING_WITNESSED_FIXTURE_IDENTITY')
            # A missing stage/group cannot be filled from table rank, UEFA name,
            # matchday, calendar date or the highest played count in duplicate rows.
            target['phase']=context['phase'];targets.append(target)
        except (ValueError,TypeError,KeyError):issues['MISSING_WITNESSED_FIXTURE_IDENTITY']+=1
    scopes=sorted({(t['competition_id'],t['season_id']) for t in targets})
    tables,diagnostics,proofs=_tables(root,scopes,cutoff)
    registry=inspect_official_rules(root,scopes,as_of=cutoff)
    features=[]
    for target in targets:
        key=(target['competition_id'],target['season_id']);table=tables.get(key)
        try:
            context=contexts['the_stats_api:'+target['fixture_id']]
            rule_view,rules=bind_rules(registry[key],target,table,as_of=cutoff,
                source_group=context.get('source_group_label'))
            if rules is not None:target={**target,'table_group':None}
            feature=build_stakes_features(target,table,as_of=cutoff,rules=rules)
            feature['official_rules_context']=rule_view
        except (ValueError,TypeError,KeyError):
            issues['INVALID_OR_LOCKED_TARGET']+=1;continue
        failures=[d for d in diagnostics if tuple(d['scope'])==key and d['status']!=
                  'SOURCE_SHAPE_VALIDATED_NOT_COMPLETE_ROSTER_CERTIFICATION']
        if table is None and failures:feature['table_status']='UNUSABLE_WITNESSED_TABLE'
        feature['competition_context']=contexts['the_stats_api:'+target['fixture_id']]
        features.append(feature)
    counts=Counter(t['status'] for f in features for t in f['teams'])
    dataset=snapshot([{k:v for k,v in f.items() if k!='dataset'} for f in features],
        role='daily_standings_stakes_shadow',as_of=cutoff,recipe={'version':VERSION},
        lineage={'competition_dataset':competition['dataset'],'accepted_tables':list(tables.values()),
                 'table_source_proofs':proofs,'table_diagnostics':diagnostics})
    return {'version':VERSION,'mode':'SHADOW','as_of':cutoff.isoformat(),'status':'PARTIAL',
            'counts':dict(counts),'features':features,'table_diagnostics':diagnostics,
            'issues':dict(issues),'dataset':dataset,'external_requests':0,'production_writes':0,
            'live_decision_effect':False,'official_rules_registry':'SOURCE_BOUND_SHADOW_V1',
            'official_rule_scope_counts':dict(Counter(v['status'] for v in registry.values()))}


def archive_stakes(root, report, *, as_of):
    try:
        view=inspect_stakes(root,report.get('fixtures',[]),as_of=as_of)
        created=DatasetStore(Path(root)/'data/arthur/standings-stakes/datasets').put(view['dataset'])
        return {k:view[k] for k in ('version','mode','status','counts','issues','external_requests',
                                   'live_decision_effect','official_rules_registry')} | {
            'dataset_id':view['dataset']['manifest']['dataset_id'],'dataset_created':created}
    except Exception:  # noqa: BLE001 - optional post-save metadata cannot veto tickets
        return {'version':VERSION,'mode':'SHADOW','status':'UNAVAILABLE','external_requests':0,
                'live_decision_effect':False,'reason':'STANDINGS_STAKES_ARCHIVE_FAILED'}
