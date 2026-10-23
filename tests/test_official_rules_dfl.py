"""Original current publisher bytes + actual local source/receipt paths, synthetic tables."""
import json
import socket
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_competition_types import competition, match
from test_official_rules_shadow import prepared as uefa_prepared

from goal_analysis.features.stakes import build_stakes_features
from goal_analysis.jobs import official_rules_shadow as module
from goal_analysis.jobs.stakes_shadow import inspect_stakes
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha
from goal_analysis.storage.temporal import TemporalStore

FIXTURES=Path(__file__).parent/'fixtures/official-rules-de2-202627'
ORIGINAL=json.loads((FIXTURES/'reviewed-bundle.json').read_bytes())
NOW=utc(ORIGINAL['available_at'])+timedelta(minutes=1)
SCOPE=('comp_0406','sn_6190936')


def table():
    return [{'team':{'id':f'tm_{i}'},'group_label':None,'position':i,'matches_played':2,
        'wins':0,'draws':2,'losses':0,'goals_for':0,'goals_against':0,'goal_difference':0,'points':2}
        for i in range(1,19)]


def seal(root,bundle,monkeypatch):
    blob=json.dumps(bundle,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    digest=sha256(blob).hexdigest();path=root/'data/arthur/official-rules'/(digest+'.json')
    path.write_bytes(blob);monkeypatch.setattr(module,'DFL_BUNDLE',digest)
    return path


def setup(root,monkeypatch):
    db,put,f,_=uefa_prepared(root,monkeypatch);bundle=deepcopy(ORIGINAL);record=bundle['records'][0]
    source=put([record['native_season']],'daily_context',record['native_season_source']['source_url'])
    record['native_season_source']={'provider':'the_stats_api','source_url':source.source_url,
        'content_hash':source.content_hash,'observed_at':source.fetched_at.isoformat()}
    folder=root/'data/arthur/official-rules'
    for doc in record['documents']:
        (folder/(doc['sha256']+'.'+doc['extension'])).write_bytes((FIXTURES/(doc['kind']+'.'+doc['extension'])).read_bytes())
    m=match(competition_id=SCOPE[0],season_id=SCOPE[1],stage_name='regular_season',
        utc_date=(NOW+timedelta(hours=6)).isoformat(),status='scheduled',is_neutral=False)
    put([competition(SCOPE[0])],'competition_catalogue','https://api.thestatsapi.com/api/football/competitions?page=1&per_page=100')
    meta=put([m],'matches','https://api.thestatsapi.com/api/football/matches?competition_id=comp_0406')
    f.update(provider_league_id=SCOPE[0],provider_season_id=SCOPE[1],kickoff=m['utc_date'],
        fixture_source={'content_hash':meta.content_hash})
    url=f'https://api.thestatsapi.com/api/football/competitions/{SCOPE[0]}/seasons/{SCOPE[1]}/standings'
    put(table(),'daily_context',url,when=NOW-timedelta(minutes=5))
    TemporalStore(db,root/'data/raw',clock=lambda:utc(bundle['reviewed_at'])-timedelta(seconds=1)).capture('raw',limit=100)
    seal(root,bundle,monkeypatch)
    return db,put,f,bundle


def inputs(root):
    context=module.inspect_official_rules(root,[SCOPE],as_of=NOW)[SCOPE]
    target={'fixture_id':'mt_1','competition_id':SCOPE[0],'season_id':SCOPE[1],
        'home_team_id':'tm_1','away_team_id':'tm_2','kickoff':(NOW+timedelta(hours=6)).isoformat(),
        'phase':'LEAGUE_PHASE','status':'scheduled','is_neutral':False}
    payload={'competition_id':SCOPE[0],'season_id':SCOPE[1],'raw_rows':table()}
    envelope={'record_id':'synthetic-table','payload':payload,'payload_sha256':sha(payload),
        'times':module._times(NOW.isoformat(),NOW.isoformat())}
    return context,target,envelope


def test_production_pin_is_actual_current_pdf_and_preserves_old_uefa_pin():
    assert sha256((FIXTURES/'reviewed-bundle.json').read_bytes()).hexdigest()==module.DFL_BUNDLE
    assert module.REVIEWED_BUNDLE=='92064b47701b8c18f344d16aec750ad81212bd89006d39b8c16e628c9f3a3a00'
    assert ORIGINAL['records'][0]['publisher_revision']=='2026-08-04'
    for doc in ORIGINAL['records'][0]['documents']:
        assert sha256((FIXTURES/(doc['kind']+'.'+doc['extension'])).read_bytes()).hexdigest()==doc['sha256']


def test_three_publisher_documents_and_real_season_chain_are_used_without_network(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch);monkeypatch.setattr(socket,'socket',Mock(side_effect=AssertionError('NO_NETWORK')))
    context,target,envelope=inputs(tmp_path)
    assert context['status']=='REVIEWED_SOURCE_BOUND' and len(context['source_receipts'])==4
    assert context['rules']['participants']==18 and context['rules']['scheduled_games']==34
    view,rules=module.bind_rules(context,target,envelope,as_of=NOW)
    assert view['applicability']=='CONSERVATIVE_POINTS_BOUNDS_SHADOW'
    result=build_stakes_features({**target,'table_group':None},envelope,as_of=NOW,rules=rules)
    assert result['rules_status']=='VERIFIED_FIXED_TOP_K' and not result['live_decision_effect']
    assert [(o['kind'],o['top_k']) for o in result['teams'][0]['objectives']]==[
        ('TITLE',1),('DIRECT_PROMOTION',2),('DIRECT_SURVIVAL',15)]
    assert all(r['automatic_promotion'] is False for r in context['rules']['conditional_routes'] if r['rank']==3)
    assert all(r['automatic_relegation'] is False for r in context['rules']['conditional_routes'] if r['rank']==16)


def test_actual_stakes_pipeline_reads_new_scope_and_retains_uefa_contexts(tmp_path,monkeypatch):
    _,_,fixture,_=setup(tmp_path,monkeypatch)
    view=inspect_stakes(tmp_path,[fixture],as_of=NOW)
    assert len(view['features'])==1 and view['features'][0]['rules_status']=='VERIFIED_FIXED_TOP_K',view
    assert view['official_rule_scope_counts']=={'REVIEWED_SOURCE_BOUND':1}
    assert not view['live_decision_effect'] and view['external_requests']==view['production_writes']==0
    contexts=module.inspect_official_rules(tmp_path,[SCOPE,('comp_7739','sn_9624824')],as_of=NOW)
    assert all(c['status']=='REVIEWED_SOURCE_BOUND' for c in contexts.values())


@pytest.mark.parametrize('delta', [-1,7*86400+1])
def test_review_not_known_or_expired_never_becomes_historical_rules(tmp_path,monkeypatch,delta):
    setup(tmp_path,monkeypatch);at=utc(ORIGINAL['reviewed_at'])+timedelta(seconds=delta)
    assert module.inspect_official_rules(tmp_path,[SCOPE],as_of=at)[SCOPE]['status']=='UNKNOWN'


@pytest.mark.parametrize('fault', ['games','top3_promotion','rank16_survival','ties','edition','float_points',
    'old_pdf_url','publisher','pdf_bytes','index_bytes','participants_bytes','future_document','season','bundle'])
def test_unreviewed_semantics_or_broken_source_never_certify(tmp_path,monkeypatch,fault):
    _,_,_,bundle=setup(tmp_path,monkeypatch);r=bundle['records'][0];folder=tmp_path/'data/arthur/official-rules'
    if fault=='games':r['scheduled_games']=38
    elif fault=='top3_promotion':r['objectives'][1]['top_k']=3
    elif fault=='rank16_survival':r['objectives'][2]['top_k']=16
    elif fault=='ties':r['tie_break_order'].reverse()
    elif fault=='edition':r['edition']='2025/26'
    elif fault=='float_points':r['points']['win']=3.0
    elif fault=='old_pdf_url':r['documents'][1]['url']='https://media.dfl.de/sites/2/2025/12/Spielordnung-SpOL-2025-12-05-Stand-1.pdf'
    elif fault=='publisher':r['documents'][1]['url']='https://untrusted.example/rules.pdf'
    elif fault.endswith('_bytes'):
        kind={'pdf_bytes':'spol','index_bytes':'index','participants_bytes':'participants'}[fault]
        doc=next(d for d in r['documents'] if d['kind']==kind)
        p=folder/(doc['sha256']+'.'+doc['extension']);p.write_bytes(p.read_bytes()+b' ')
    elif fault=='future_document':r['documents'][0]['observed_at']=(NOW+timedelta(seconds=1)).isoformat()
    elif fault=='season':r['native_season_source']['content_hash']='f'*64
    elif fault=='bundle':(folder/(module.DFL_BUNDLE+'.json')).write_bytes(b'{}')
    if fault!='bundle':seal(tmp_path,bundle,monkeypatch)
    contexts=module.inspect_official_rules(tmp_path,[SCOPE,('comp_7739','sn_9624824')],as_of=NOW)
    assert contexts[SCOPE]['status']=='UNKNOWN'
    assert contexts[('comp_7739','sn_9624824')]['status']=='REVIEWED_SOURCE_BOUND'


@pytest.mark.parametrize('shared', ['first_two','all'])
def test_legitimate_shared_positions_do_not_create_a_permanent_missing_table(tmp_path,monkeypatch,shared):
    setup(tmp_path,monkeypatch);context,target,envelope=inputs(tmp_path);rows=envelope['payload']['raw_rows']
    if shared=='first_two':rows[1]['position']=1
    else:
        for row in rows:row['position']=1
    envelope['payload_sha256']=sha(envelope['payload'])
    view,rules=module.bind_rules(context,target,envelope,as_of=NOW)
    assert rules is not None and view['reason'] is None
    assert not rules['payload']['tie_resolution_implemented']


@pytest.mark.parametrize('fault',['gap','dense','conflicting_tie','reverse_points','partial','adjusted','phase','group'])
def test_malformed_or_missing_table_still_stays_unknown(tmp_path,monkeypatch,fault):
    setup(tmp_path,monkeypatch);context,target,envelope=inputs(tmp_path);rows=envelope['payload']['raw_rows']
    if fault=='gap':rows[-1]['position']=19
    elif fault=='dense':rows[1]['position']=1;rows[2]['position']=2
    elif fault=='conflicting_tie':rows[1]['position']=1;rows[1]['points']=3
    elif fault=='reverse_points':rows[-1]['points']=3
    elif fault=='partial':rows.pop()
    elif fault=='adjusted':rows[0]['points']=1
    elif fault=='phase':target['phase']='UNKNOWN'
    elif fault=='group':target['table_group']='A'
    envelope['payload_sha256']=sha(envelope['payload'])
    view,rules=module.bind_rules(context,target,envelope,as_of=NOW)
    assert rules is None and view['reason']
