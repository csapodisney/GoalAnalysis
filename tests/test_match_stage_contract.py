"""Explicit null is a documented provider value; absent is still unknown."""
import json
import socket
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_competition_types import competition, match, setup
from test_official_rules_dfl import setup as dfl_setup

from goal_analysis.jobs import match_stage_contract as module
from goal_analysis.jobs.competition_types import inspect_competition_types
from goal_analysis.jobs.stakes_shadow import inspect_stakes
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.competition_types import classify_competition
from goal_analysis.storage.temporal import TemporalStore

FIXTURES=Path(__file__).parent/'fixtures/match-stage-contract'
MANIFEST=json.loads((FIXTURES/'manifest.json').read_bytes())
NOW=utc(MANIFEST['available_at'])+timedelta(minutes=1)
SCOPE=module.SCOPE


def install(root,monkeypatch,changes=None):
    value={**deepcopy(MANIFEST),**(changes or {})};raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    digest=sha256(raw).hexdigest();folder=root/'data/arthur/provider-contracts/match-stage'
    folder.mkdir(parents=True,exist_ok=True);(folder/(digest+'.json')).write_bytes(raw)
    (folder/(value['content_sha256']+'.txt')).write_bytes((FIXTURES/'provider-llms.txt').read_bytes())
    monkeypatch.setattr(module,'PIN',digest)
    return folder,value


def prepared(root,monkeypatch,*,present=True,stage=None,group=None,group_present=True,scope=SCOPE):
    install(root,monkeypatch)
    m=match(competition_id=scope[0],season_id=scope[1],stage_name=stage,group_label=group,
        utc_date=(NOW+timedelta(hours=6)).isoformat(),status='scheduled',is_neutral=False)
    if not present:m.pop('stage_name')
    if not group_present:m.pop('group_label')
    db,raw,temporal,put,f,_,_=setup(root,catalogue=[competition(scope[0])],matches=[m])
    f.update(provider_league_id=scope[0],provider_season_id=scope[1],kickoff=m['utc_date'])
    return db,raw,temporal,put,f,m


def row(root,fixture,as_of=NOW):
    return inspect_competition_types(root,[fixture],as_of=as_of)['rows'][0]


def test_original_public_schema_and_review_pin_are_real():
    assert sha256((FIXTURES/'manifest.json').read_bytes()).hexdigest()==module.PIN
    assert sha256((FIXTURES/'provider-llms.txt').read_bytes()).hexdigest()==MANIFEST['content_sha256']
    text=(FIXTURES/'provider-llms.txt').read_text('utf-8')
    assert text.count('Null when the row is a regular league fixture')>=2


def test_exact_native_null_contract_is_consumed_and_receipt_traced_without_http(tmp_path,monkeypatch):
    *_,fixture,_=prepared(tmp_path,monkeypatch)
    monkeypatch.setattr(socket,'socket',Mock(side_effect=AssertionError('NO_NETWORK')))
    value=inspect_competition_types(tmp_path,[fixture],as_of=NOW);r=value['rows'][0]
    assert r['phase']=='LEAGUE_PHASE' and r['status']=='CLASSIFIED'
    assert r['stage_interpretation_basis']=='EXPLICIT_NULL_STAGE_REGULAR_LEAGUE_FIXTURE'
    proof=next(p for p in value['dataset']['lineage'] if p.get('version')==module.VERSION)
    assert proof['receipt_id'] in r['source_receipt_ids'] and proof['content_sha256']==MANIFEST['content_sha256']
    assert value['external_requests']==value['production_writes']==0 and not value['live_decision_effect']


@pytest.mark.parametrize('options',[{'present':False},{'group_present':False},{'group':'A'},
    {'scope':('comp_0406','sn_999')},{'scope':('comp_3039','sn_6190936')},
    {'scope':('comp_29967','sn_6190936')}])
def test_missing_key_group_other_scope_or_friendly_cannot_borrow_semantics(tmp_path,monkeypatch,options):
    *_,fixture,_=prepared(tmp_path,monkeypatch,**options)
    r=row(tmp_path,fixture)
    assert 'stage_interpretation_basis' not in r
    assert r['phase'] in {'UNKNOWN','FRIENDLY'}


@pytest.mark.parametrize('stage,phase',[('qualifying','QUALIFIER'),('knockout_playoff','UNKNOWN'),
    ('group_stage','GROUP_STAGE'),('round_of_16','KNOCKOUT'),('', 'UNKNOWN')])
def test_explicit_other_stage_never_gets_regular_season_fallback(tmp_path,monkeypatch,stage,phase):
    *_,fixture,_=prepared(tmp_path,monkeypatch,stage=stage)
    r=row(tmp_path,fixture);assert r['phase']==phase and 'stage_interpretation_basis' not in r


@pytest.mark.parametrize('delta',[-1,7*86400+1])
def test_new_contract_does_not_change_older_or_expired_cutoffs(tmp_path,monkeypatch,delta):
    *_,fixture,_=prepared(tmp_path,monkeypatch)
    r=row(tmp_path,fixture,as_of=utc(MANIFEST['available_at'])+timedelta(seconds=delta))
    assert r['phase']=='UNKNOWN' and 'stage_interpretation_basis' not in r


@pytest.mark.parametrize('fault',['manifest','document','source_url','scope','meaning','sections',
    'future_observed','future_review','review_days_float'])
def test_unreviewed_or_corrupt_doc_chain_is_not_phase_evidence(tmp_path,monkeypatch,fault):
    *_,fixture,_=prepared(tmp_path,monkeypatch);changes={}
    if fault=='source_url':changes['source_url']='https://untrusted.example/schema'
    if fault=='scope':changes['scope']=['comp_3039',SCOPE[1]]
    if fault=='meaning':changes['meaning']='NULL_IS_MISSING'
    if fault=='sections':changes['schema_sections']=['MatchDetail']
    if fault=='future_observed':changes['observed_at']=(NOW+timedelta(seconds=1)).isoformat()
    if fault=='future_review':changes.update(reviewed_at=(NOW+timedelta(seconds=1)).isoformat(),available_at=(NOW+timedelta(seconds=1)).isoformat())
    if fault=='review_days_float':changes['local_review_days']=7.0
    folder,value=install(tmp_path,monkeypatch,changes)
    if fault=='manifest':(folder/(module.PIN+'.json')).write_bytes(b'{}')
    if fault=='document':(folder/(value['content_sha256']+'.txt')).write_bytes(b'not source')
    assert row(tmp_path,fixture)['phase']=='UNKNOWN'


def test_name_date_and_matchday_cannot_repair_an_absent_stage_key(tmp_path,monkeypatch):
    db,_,_,put,fixture,m=prepared(tmp_path,monkeypatch,present=False)
    m['matchday']=9
    source=put([m],'matches','https://api.thestatsapi.com/api/football/matches?competition_id=comp_0406')
    fixture['fixture_source']['content_hash']=source.content_hash
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW-timedelta(seconds=1)).capture('raw',limit=100)
    assert row(tmp_path,fixture)['phase']=='UNKNOWN'


def test_real_stakes_reader_uses_documented_null_to_pass_the_existing_rule_gate(tmp_path,monkeypatch):
    db,put,fixture,_=dfl_setup(tmp_path,monkeypatch);install(tmp_path,monkeypatch)
    m=match(competition_id=SCOPE[0],season_id=SCOPE[1],stage_name=None,group_label=None,
        utc_date=(NOW+timedelta(hours=6)).isoformat(),status='scheduled',is_neutral=False)
    source=put([m],'matches','https://api.thestatsapi.com/api/football/matches?competition_id=comp_0406')
    fixture.update(kickoff=m['utc_date'],fixture_source={'content_hash':source.content_hash})
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW-timedelta(seconds=1)).capture('raw',limit=100)
    value=inspect_stakes(tmp_path,[fixture],as_of=NOW);f=value['features'][0]
    assert f['rules_status']=='VERIFIED_FIXED_TOP_K' and f['competition_context']['phase']=='LEAGUE_PHASE'
    assert not value['live_decision_effect'] and all(t['status']=='CONSERVATIVE_POINTS_BOUNDS_SHADOW' for t in f['teams'])


def test_pure_classifier_does_not_treat_unreviewed_null_as_known():
    assert classify_competition(competition(SCOPE[0]),None)['phase']=='UNKNOWN'
    assert classify_competition(competition(SCOPE[0]),None,reviewed_explicit_null=True)['phase']=='LEAGUE_PHASE'
    with pytest.raises(ValueError):classify_competition(competition(SCOPE[0]),None,reviewed_explicit_null=1)
