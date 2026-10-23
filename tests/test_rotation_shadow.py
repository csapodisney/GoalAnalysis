"""Source-bound local integration, immutability and safe post-save failure."""
import json
from copy import deepcopy
from datetime import timedelta

import pytest
from test_rotation_features import raw
from test_schedule_features import NOW, calendars, match
from test_schedule_shadow import setup

from goal_analysis.jobs.rotation_shadow import archive_rotation, inspect_rotation
from goal_analysis.storage.temporal import TemporalStore
from goal_analysis.storage.training_datasets import DatasetStore


def save_xis(tmp_path, *, fault=None):
    db,store,temporal,f,_=setup(tmp_path)
    saved=[]
    for m in (match(),calendars()[0]['payload']['matches'][-1]):
        data=raw(m,shift=3 if m['fixture_id']=='mt_100' else 0)
        if fault=='identity' and m['fixture_id']=='mt_100':
            data['home']['id']='tm_999'
        saved.append(store.save('the_stats_api','daily_context',json.dumps({'data':data}).encode(),
            'application/json',fetched_at=NOW-timedelta(hours=2),
            source_url=f"https://api.thestatsapi.com/api/football/matches/{m['fixture_id']}/lineups"))
    if fault!='no_receipt':
        if fault=='future_receipt':
            temporal=TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW+timedelta(seconds=1))
        temporal.capture('raw',limit=100)
    return f,saved,db


def test_actual_witnessed_raw_xis_join_calendar_native_ids_and_never_claim_unmapped_competition(tmp_path):
    f,_,_=save_xis(tmp_path);v=inspect_rotation(tmp_path,[f],as_of=NOW)
    h=v['features'][0]['teams'][0]
    assert h['starting_xi_changes']==3 and v['counts']=={'XI_COMPARISON_SHADOW':1,'PARTIAL_SHADOW':1}
    assert v['features'][0]['competition_context']['family']=='UNKNOWN'
    assert not v['live_decision_effect'] and v['external_requests']==v['production_writes']==0


@pytest.mark.parametrize('fault',['bytes','no_receipt','future_receipt','wrong_url','identity'])
def test_corrupt_unwitnessed_future_wrong_route_or_identity_do_not_produce_comparisons(tmp_path,fault):
    f,saved,db=save_xis(tmp_path,fault=fault)
    if fault=='bytes':
        path=tmp_path/'data/raw'/saved[0].relative_path;path.write_bytes(path.read_bytes()+b' ')
    elif fault=='wrong_url':
        with db.transaction() as c:
            c.execute('UPDATE raw_snapshots SET source_url=? WHERE content_hash=?',
                ('https://api.thestatsapi.com/api/football/matches/mt_999/lineups',saved[0].content_hash))
    v=inspect_rotation(tmp_path,[f],as_of=NOW)
    assert v['features'][0]['teams'][0]['starting_xi_changes'] is None


def test_missing_raw_is_unknown_not_rotation_and_report_archive_is_idempotent(tmp_path,monkeypatch):
    _,_,_,f,_=setup(tmp_path);report={'fixtures':[f]};before=deepcopy(report)
    a=archive_rotation(tmp_path,report,as_of=NOW);b=archive_rotation(tmp_path,report,as_of=NOW)
    assert a['dataset_created'] and not b['dataset_created'] and a['status']=='PARTIAL' and report==before
    v=inspect_rotation(tmp_path,[f],as_of=NOW)
    assert DatasetStore(tmp_path/'data/arthur/rotation/datasets').read(a['dataset_id'])==v['dataset']
    monkeypatch.setattr(DatasetStore,'put',lambda *args:(_ for _ in ()).throw(OSError('private-details')))
    result=archive_rotation(tmp_path,report,as_of=NOW)
    assert result['status']=='UNAVAILABLE' and 'private-details' not in json.dumps(result)


def test_latest_prediction_is_not_replaced_by_older_convenient_confirmed_xi(tmp_path):
    from goal_analysis.storage import SnapshotStore

    f,_,db=save_xis(tmp_path)
    store=SnapshotStore(db,tmp_path/'data/raw')
    data=raw(match(),kind='predicted',confirmed=False)
    store.save('the_stats_api','daily_context',json.dumps({'data':data}).encode(),'application/json',
        fetched_at=NOW-timedelta(minutes=30),
        source_url='https://api.thestatsapi.com/api/football/matches/mt_100/lineups')
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW-timedelta(minutes=10)).capture('raw',limit=100)
    r=inspect_rotation(tmp_path,[f],as_of=NOW)['features'][0]['teams'][0]
    assert r['current_lineup_status']=='UNCONFIRMED_OR_PREDICTED_LINEUP' and r['starting_xi_changes'] is None


def test_sources_not_yet_available_do_not_crowd_known_prefix(tmp_path):
    f,_,_=save_xis(tmp_path,fault='future_receipt')
    early=inspect_rotation(tmp_path,[f],as_of=NOW)
    later=inspect_rotation(tmp_path,[f],as_of=NOW+timedelta(seconds=2))
    assert early['features'][0]['teams'][0]['starting_xi_changes'] is None
    assert later['features'][0]['teams'][0]['starting_xi_changes'] is None # current XI now stale >2h
    assert early['features'][0]['teams'][0]['workload_indicators'] is not None


def test_optional_post_save_hook_preserves_tickets_and_exposes_nested_rotation(tmp_path,monkeypatch):
    from goal_analysis.jobs import (
        competition_types,
        fixture_contexts,
        rotation_shadow,
        schedule_shadow,
    )

    (tmp_path/'config').mkdir();(tmp_path/'config/canonical-contexts.json').write_text(
        json.dumps({'enabled':True,'mode':'SHADOW'}))
    monkeypatch.setattr(fixture_contexts,'link_report',lambda *a,**kw:{'gaps':0})
    monkeypatch.setattr(competition_types,'archive_competition_types',lambda *a,**kw:{'status':'PARTIAL'})
    monkeypatch.setattr(schedule_shadow,'archive_schedule',lambda *a,**kw:{'status':'PARTIAL'})
    monkeypatch.setattr(rotation_shadow,'archive_rotation',lambda *a,**kw:{'status':'PARTIAL','mode':'SHADOW'})
    report={'tickets':[{'untouched':True}]};before=deepcopy(report)
    r=fixture_contexts.shadow_archive(tmp_path,report,clock=lambda:NOW)
    assert r['rotation']=={'status':'PARTIAL','mode':'SHADOW'} and report==before
