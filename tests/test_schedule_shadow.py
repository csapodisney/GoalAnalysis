"""Actual raw/cache/receipt boundaries using synthetic all-status calendar pages."""
import json
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from test_schedule_features import NOW, calendars, match

from goal_analysis.jobs.schedule_shadow import archive_schedule, calendar_query, inspect_schedule
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.temporal import TemporalStore


def setup(tmp_path, *, meta_changes=None, filtered=False):
    db=Database(tmp_path/'data/goal_analysis.sqlite3');db.initialize()
    raw=SnapshotStore(db,tmp_path/'data/raw')
    temporal=TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW-timedelta(hours=1));temporal.initialize()
    saved=[]
    for c in calendars():
        p=c['payload'];query=calendar_query(p['team_id'],match()['kickoff'])
        if filtered:query['status']='finished'
        matches=[{'id':m['fixture_id'],'competition_id':m['competition_id'],'season_id':m['season_id'],
                  'home_team':{'id':m['home_team_id']},'away_team':{'id':m['away_team_id']},
                  'utc_date':m['kickoff'],'status':m['status']} for m in p['matches']]
        body={'data':matches,'meta':{'page':1,'per_page':100,'total_pages':1,'total':len(matches),**(meta_changes or {})}}
        saved.append(raw.save('the_stats_api','daily_context',json.dumps(body).encode(),'application/json',
            fetched_at=NOW-timedelta(hours=2),source_url='https://api.thestatsapi.com/api/football/matches?'+urlencode(query)))
    temporal.capture('raw',limit=100)
    f={'fixture_id':'the_stats_api:mt_100','source_provider':'the_stats_api',
       'provider_fixture_id':'mt_100','provider_league_id':'comp_1','provider_season_id':'sn_1',
       'provider_home_team_id':'tm_1','provider_away_team_id':'tm_2','kickoff':match()['kickoff']}
    return db,raw,temporal,f,saved


def test_verified_complete_team_calendar_joins_actual_raw_bytes(tmp_path):
    _,_,_,f,_=setup(tmp_path)
    v=inspect_schedule(tmp_path,[f],as_of=NOW)
    assert v['counts']=={'AVAILABLE_SHADOW':2} and len(v['dataset']['lineage'])==2
    assert v['external_requests']==v['production_writes']==0
    assert v['features'][0]['teams'][0]['previous_kickoff_gap_hours']==24


@pytest.mark.parametrize('changes',[{'total_pages':2},{'page':2},{'total':9},{'per_page':50}])
def test_partial_pagination_is_not_mistaken_for_rest_or_empty_fixture_list(tmp_path,changes):
    _,_,_,f,_=setup(tmp_path,meta_changes=changes)
    assert inspect_schedule(tmp_path,[f],as_of=NOW)['counts']=={'MISSING_CALENDAR':2}


def test_finished_only_query_is_not_a_full_calendar(tmp_path):
    _,_,_,f,_=setup(tmp_path,filtered=True)
    assert inspect_schedule(tmp_path,[f],as_of=NOW)['counts']=={'MISSING_CALENDAR':2}


def test_unwitnessed_future_and_corrupt_files_are_not_usable(tmp_path):
    _,_,_,f,saved=setup(tmp_path)
    assert inspect_schedule(tmp_path,[f],as_of=NOW-timedelta(hours=1,minutes=30))['counts']=={'MISSING_CALENDAR':2}
    file=tmp_path/'data/raw'/saved[0].relative_path;file.write_bytes(file.read_bytes()+b' ')
    assert inspect_schedule(tmp_path,[f],as_of=NOW)['counts']=={'MISSING_CALENDAR':1,'AVAILABLE_SHADOW':1}


def test_archiving_is_immutable_and_never_modifies_the_saved_report(tmp_path,monkeypatch):
    from copy import deepcopy

    from goal_analysis.storage.training_datasets import DatasetStore
    _,_,_,f,_=setup(tmp_path);report={'fixtures':[f]};before=deepcopy(report)
    a=archive_schedule(tmp_path,report,as_of=NOW);b=archive_schedule(tmp_path,report,as_of=NOW)
    assert a['dataset_created'] and not b['dataset_created'] and report==before
    monkeypatch.setattr(DatasetStore,'put',lambda *args:(_ for _ in ()).throw(OSError('private-detail')))
    r=archive_schedule(tmp_path,report,as_of=NOW)
    assert r['status']=='UNAVAILABLE' and 'private-detail' not in json.dumps(r)
