"""Read only complete all-competition team calendars into SHADOW features.

The initial reader admits provider-declared complete single pages (<=100
matches per team/window); a multi-page first page is NEVER complete coverage.
No implicit requests, new receipts, calendar collection or live ticket changes.
"""
import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from goal_analysis.features.schedule import (
    MAX_AGE_HOURS,
    VERSION,
    WINDOW_DAYS,
    build_schedule_features,
)
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.identity import field_id, native_id
from goal_analysis.normalization.training_datasets import sha, snapshot
from goal_analysis.storage.context_sources import context_sources
from goal_analysis.storage.source_observations import read_source_raw
from goal_analysis.storage.training_datasets import DatasetStore


def calendar_query(team, kickoff):
    native_id(team, 'tm')
    kickoff = utc(kickoff)
    return {'team_id':team, 'date_from':(kickoff-timedelta(days=WINDOW_DAYS)).date().isoformat(),
            'date_to':(kickoff+timedelta(days=WINDOW_DAYS)).date().isoformat(),
            'utc_offset':'+00:00', 'page':1, 'per_page':100}


def _scope(url, team, kickoff):
    parts = urlsplit(url); query = parse_qs(parts.query)
    if (parts.path != '/api/football/matches' or any(len(v) != 1 for v in query.values())
            or set(query) != set(calendar_query(team,kickoff))):
        raise ValueError('FILTERED_OR_INCOMPLETE_CALENDAR_QUERY')
    query = {k:v[0] for k,v in query.items()}
    if (query['team_id'] != team or query['utc_offset'] != '+00:00'
            or query['page'] != '1' or query['per_page'] != '100'):
        raise ValueError('FILTERED_OR_INCOMPLETE_CALENDAR_QUERY')
    expected = calendar_query(team,kickoff)
    if query['date_from'] > expected['date_from'] or query['date_to'] < expected['date_to']:
        raise ValueError('INSUFFICIENT_CALENDAR_WINDOW')
    return query


def _calendar(body, proof, query):
    meta, matches = body.get('meta',{}), body.get('data')
    if (not isinstance(matches,list) or any(type(meta.get(k)) is not int for k in
            ('page','per_page','total_pages','total')) or meta['page'] != 1
            or meta['per_page'] != 100 or meta['total'] != len(matches)
            or len(matches) > 100 or meta['total_pages'] != (1 if matches else 0)):
        raise ValueError('INCOMPLETE_CALENDAR_PAGINATION')
    start = utc(datetime.combine(date.fromisoformat(query['date_from']),time(),tzinfo=utc(proof['times']['observed_at']).tzinfo))
    end = utc(datetime.combine(date.fromisoformat(query['date_to'])+timedelta(days=1),time(),tzinfo=start.tzinfo))
    rows = []
    for m in matches:
        rows.append({'fixture_id':native_id(m['id'],'mt'),
            'competition_id':native_id(field_id(m,'competition'),'comp'),
            'season_id':native_id(field_id(m,'season'),'sn'),
            'home_team_id':native_id(m['home_team']['id'],'tm'),
            'away_team_id':native_id(m['away_team']['id'],'tm'),
            'kickoff':utc(m['utc_date']).isoformat(),'status':m['status'],
            'venue_id':(m.get('venue') or {}).get('id')})
    payload = {'team_id':query['team_id'],'scope':'ALL_COMPETITIONS_ALL_STATUSES',
        'pagination_complete':True,'window_start':start.isoformat(),'window_end_exclusive':end.isoformat(),
        'observed_at':proof['times']['observed_at'],'available_at':proof['times']['available_at'],
        'matches':rows}
    return {'record_id':sha([payload,proof]),'payload':payload,'payload_sha256':sha(payload),
            'source_sha256':proof['source_sha256'],'receipt_id':proof['receipt_id'],
            'times':proof['times'],'source_receipts':[proof]}


def target_fixture(f):
    if f.get('source_provider') != 'the_stats_api':
        raise ValueError('MISSING_NATIVE_FIXTURE_LINK')
    native = native_id(f['provider_fixture_id'],'mt')
    if f['fixture_id'] != 'the_stats_api:'+native:
        raise ValueError('MISSING_NATIVE_FIXTURE_LINK')
    return {'fixture_id':native,'competition_id':f['provider_league_id'],
            'season_id':f['provider_season_id'],'home_team_id':f['provider_home_team_id'],
            'away_team_id':f['provider_away_team_id'],'kickoff':utc(f['kickoff']).isoformat(),
            'status':'scheduled','venue_id':f.get('venue_id')}


def inspect_schedule(root, fixtures, *, as_of):
    if not isinstance(fixtures,list) or len(fixtures) > 1000:
        raise ValueError('BOUNDED_SCHEDULE_FIXTURES_REQUIRED')
    cutoff, root = utc(as_of), Path(root).resolve()
    targets, issues = [], Counter()
    for f in fixtures:
        try: targets.append(target_fixture(f))
        except (ValueError,KeyError,TypeError): issues['MISSING_NATIVE_FIXTURE_LINK'] += 1
    if len({t['fixture_id'] for t in targets}) != len(targets):
        raise ValueError('DUPLICATE_SCHEDULE_TARGET')
    wanted = {(t[s+'_team_id'],t['kickoff']) for t in targets for s in ('home','away')}
    wanted_by_team = {}
    for team,kickoff in sorted(wanted):
        wanted_by_team.setdefault(team,[]).append(kickoff)
    candidates, sources = {}, []
    path = root/'data/goal_analysis.sqlite3'
    if path.exists():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {'raw_snapshots','temporal_receipts'} <= tables:
                if not wanted_by_team:
                    rows=[]
                else:
                    rows=context_sources(db,cutoff=cutoff,
                        where="""a.source_url LIKE 'https://api.thestatsapi.com/api/football/matches?%'
                            AND EXISTS (SELECT 1 FROM json_each(?) t WHERE
                                instr(a.source_url,'team_id='||t.value||'&')>0 OR
                                substr(a.source_url,-length('team_id='||t.value))='team_id='||t.value)""",
                        params=(json.dumps(sorted(wanted_by_team)),),
                        limit=1000,max_bytes=64*1024*1024,max_age_hours=MAX_AGE_HOURS)
                for row,source in rows:
                    scopes=[]
                    team = parse_qs(urlsplit(source['source_url']).query).get('team_id',[None])[0]
                    for kickoff in wanted_by_team.get(team,[]):
                        try: scopes.append(((team,kickoff),_scope(source['source_url'],team,kickoff)))
                        except (ValueError,KeyError): continue
                    if not scopes: continue
                    try:
                        body,proof=read_source_raw(db,row,root,cutoff,source)
                    except (ValueError,KeyError,TypeError,OSError):
                        issues['INVALID_OR_UNAVAILABLE_CALENDAR_SOURCE'] += 1
                        continue
                    for key,query in scopes:
                        candidates.setdefault(key,[]).append((body,proof,query))
    envelopes={}
    for key,options in candidates.items():
        newest=max(utc(p['times']['observed_at']) for _,p,_ in options)
        newest_rows=[r for r in options if utc(r[1]['times']['observed_at'])==newest]
        if len({sha(b) for b,_,_ in newest_rows}) != 1:
            issues['CONFLICTING_CALENDAR_SOURCES'] += 1
            continue
        body,proof,query=newest_rows[0]
        try: envelopes[key]=_calendar(body,proof,query)
        except (ValueError,KeyError,TypeError): issues['INCOMPLETE_OR_INVALID_CALENDAR'] += 1
    # Preserve calendar timestamps. Match-specific location envelopes carry
    # their own later availability; they never refresh an old calendar clock.
    from goal_analysis.jobs.stadium_locations_shadow import match_locations

    matches={};needed_by_target={}
    for target in targets:
        values=[envelopes[(target[s+'_team_id'],target['kickoff'])] for s in ('home','away')
                if (target[s+'_team_id'],target['kickoff']) in envelopes]
        try:
            base=build_schedule_features(target,values,as_of=cutoff)
            needed={target['fixture_id']}
            for team in base['teams']:
                if team['previous_fixture']:
                    previous=team['previous_fixture'];matches[previous['fixture_id']]=previous
                    needed.add(previous['fixture_id'])
            matches[target['fixture_id']]=target
            needed_by_target[target['fixture_id']]=needed
        except (ValueError,KeyError,TypeError):continue
    locations,location_issues=match_locations(root,list(matches.values()),as_of=cutoff)
    features=[]
    for target in targets:
        values=[envelopes[(target[s+'_team_id'],target['kickoff'])] for s in ('home','away')
                if (target[s+'_team_id'],target['kickoff']) in envelopes]
        venues=[locations[fid] for fid in sorted(needed_by_target.get(target['fixture_id'],set())) if fid in locations]
        try: features.append(build_schedule_features(target,values,as_of=cutoff,venues=venues))
        except (ValueError,KeyError,TypeError): issues['INVALID_OR_LOCKED_TARGET'] += 1
    for key in sorted(envelopes): sources.append(envelopes[key])
    counts=Counter(t['status'] for f in features for t in f['teams'])
    dataset=snapshot([{k:v for k,v in f.items() if k!='dataset'} for f in features],
        role='daily_team_schedule_shadow',as_of=cutoff,recipe={'version':VERSION},lineage=[*sources,*locations.values()])
    return {'version':VERSION,'mode':'SHADOW','live_decision_effect':False,'as_of':cutoff.isoformat(),
            'counts':dict(counts),'features':features,'issues':dict(issues),'dataset':dataset,
            'external_requests':0,'production_writes':0,'location_issues':location_issues,
            'status':'PARTIAL' if counts.get('AVAILABLE_SHADOW',0)!=2*len(fixtures) else 'CALENDAR_FEATURES_SHADOW'}


def archive_schedule(root, report, *, as_of):
    try:
        view=inspect_schedule(root,report.get('fixtures',[]),as_of=as_of)
        created=DatasetStore(Path(root)/'data/arthur/team-schedule/datasets').put(view['dataset'])
        return {k:view[k] for k in ('version','mode','status','counts','issues','external_requests','live_decision_effect')} | {
            'dataset_id':view['dataset']['manifest']['dataset_id'],'dataset_created':created}
    except Exception:  # noqa: BLE001 - optional post-save metadata cannot veto tickets
        return {'version':VERSION,'mode':'SHADOW','status':'UNAVAILABLE','external_requests':0,
                'live_decision_effect':False,'reason':'SCHEDULE_ARCHIVE_FAILED'}
