"""Production raw/receipt classes with synthetic native tables; no paid calls."""
import json
from copy import deepcopy
from datetime import timedelta

import pytest
from test_competition_types import NOW, WITNESS, setup
from test_stakes_features import rows

from goal_analysis.jobs.stakes_shadow import archive_stakes, inspect_stakes
from goal_analysis.storage.temporal import TemporalStore
from goal_analysis.storage.training_datasets import DatasetStore

URL='https://api.thestatsapi.com/api/football/competitions/comp_7739/seasons/sn_1/standings'


def tables(tmp_path, *, fault=None):
    db,raw,temporal,put,f,_,_=setup(tmp_path);r=rows()
    if fault=='duplicate':r.append(deepcopy(r[0]))
    source=put(r,'daily_context',URL+'?group=A' if fault=='filtered' else URL)
    if fault!='no_receipt':
        if fault=='future_receipt':temporal=TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW+timedelta(seconds=1))
        temporal.capture('raw',limit=100)
    if fault=='bytes':
        p=tmp_path/'data/raw'/source.relative_path;p.write_bytes(p.read_bytes()+b' ')
    return db,raw,put,f


def test_actual_native_scope_has_descriptive_rows_but_no_inferred_remaining_games_or_uefa_slots(tmp_path):
    _,_,_,f=tables(tmp_path);v=inspect_stakes(tmp_path,[f],as_of=NOW)
    assert v['counts']=={'DESCRIPTIVE_ONLY':2}
    assert v['features'][0]['rules_status']=='MISSING_VERIFIED_RULES'
    assert all(t['games_remaining'] is None and not t['objectives'] for t in v['features'][0]['teams'])
    assert v['external_requests']==v['production_writes']==0 and not v['live_decision_effect']


@pytest.mark.parametrize('fault',['bytes','no_receipt','future_receipt','filtered','duplicate'])
def test_invalid_future_filtered_or_ambiguous_tables_are_not_mathematical_evidence(tmp_path,fault):
    _,_,_,f=tables(tmp_path,fault=fault);v=inspect_stakes(tmp_path,[f],as_of=NOW)
    assert v['counts']=={'UNKNOWN':2}
    assert all(t['table_row'] is None and not t['objectives'] for t in v['features'][0]['teams'])
    if fault=='duplicate':assert v['table_diagnostics'][-1]['duplicate_team_groups']==1


def test_same_time_contradictions_and_newer_ambiguous_version_never_choose_convenient_ranks(tmp_path):
    db,_,put,f=tables(tmp_path);r=rows();r[0]['points']=8
    put(r,'daily_context',URL)
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:WITNESS).capture('raw',limit=100)
    v=inspect_stakes(tmp_path,[f],as_of=NOW)
    assert v['counts']=={'UNKNOWN':2} and any(d['status']=='CONFLICTING_TABLE_SOURCES' for d in v['table_diagnostics'])
    r=rows();r.append(deepcopy(r[0]));put(r,'daily_context',URL,when=NOW-timedelta(minutes=20))
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW-timedelta(minutes=10)).capture('raw',limit=100)
    assert inspect_stakes(tmp_path,[f],as_of=NOW)['counts']=={'UNKNOWN':2}


def test_unavailable_new_version_does_not_crowd_a_known_old_prefix(tmp_path):
    db,_,put,f=tables(tmp_path);r=rows();r[0]['points']=8
    put(r,'daily_context',URL,when=NOW-timedelta(minutes=20))
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW+timedelta(minutes=1)).capture('raw',limit=100)
    v=inspect_stakes(tmp_path,[f],as_of=NOW)
    assert v['features'][0]['teams'][0]['table_row']['points']==10


def test_forged_fixture_metadata_cannot_borrow_a_real_table(tmp_path):
    _,_,_,f=tables(tmp_path);f['provider_home_team_id']='tm_99'
    v=inspect_stakes(tmp_path,[f],as_of=NOW)
    assert not v['features'] and v['issues']=={'MISSING_WITNESSED_FIXTURE_IDENTITY':1}


def test_empty_table_stays_unknown_and_untrusted_verified_config_is_ignored(tmp_path):
    db,_,put,f=tables(tmp_path);put([],'daily_context',URL,when=NOW-timedelta(minutes=20))
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW-timedelta(minutes=10)).capture('raw',limit=100)
    (tmp_path/'config').mkdir();(tmp_path/'config/competition-rules.json').write_text('{"verified":true}')
    v=inspect_stakes(tmp_path,[f],as_of=NOW)
    assert v['features'][0]['table_status']=='EMPTY_TABLE' and v['counts']=={'UNKNOWN':2}
    assert v['official_rules_registry']=='SOURCE_BOUND_SHADOW_V1'
    assert v['official_rule_scope_counts']=={'UNKNOWN':1}


def test_archive_readback_idempotence_and_failure_cannot_alter_saved_tickets(tmp_path,monkeypatch):
    _,_,_,f=tables(tmp_path);report={'fixtures':[f],'tickets':['frozen']};before=deepcopy(report)
    a=archive_stakes(tmp_path,report,as_of=NOW);again=archive_stakes(tmp_path,report,as_of=NOW)
    view=inspect_stakes(tmp_path,[f],as_of=NOW)
    assert a['dataset_created'] and not again['dataset_created'] and report==before
    assert DatasetStore(tmp_path/'data/arthur/standings-stakes/datasets').read(a['dataset_id'])==view['dataset']
    monkeypatch.setattr(DatasetStore,'put',lambda *a:(_ for _ in ()).throw(OSError('private-detail')))
    failure=archive_stakes(tmp_path,report,as_of=NOW)
    assert failure['status']=='UNAVAILABLE' and 'private-detail' not in json.dumps(failure)
