"""Unknown optional metrics must not poison the independent review batch."""
import json
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import pytest
from test_grok_concurrence import Cli
from test_grok_gate import NOW
from test_grok_native_gate import config, saved

from goal_analysis.agents.grok_concurrence import ConcurrenceReviewer, _packet
from goal_analysis.jobs import grok_gate


def test_captured_eleven_concern_batch_routes_without_fabricating_unknown_stability():
    original=json.loads((Path(__file__).parent/'fixtures/grok-concurrence-null-stability-20261022.json').read_bytes())
    unchanged=deepcopy(original)
    with pytest.raises(ValueError,match='Invalid leg metric'):_packet(original)
    cards={};critiques={};fixtures={}
    for row in original['concerns']:
        profile=row['profile_id'];leg=deepcopy(row['leg'])
        card=cards.setdefault(profile,{'profile_id':profile,'status':'READY','legs':[]})
        critique=critiques.setdefault(profile,{'profile_id':profile,'recommendation_id':'review-'+profile,
            'verdict':'QUESTIONABLE','actionability':True,'review_validation':'COMPLETE',
            'strengths':'Captured source facts.','risks':'Captured regression concern.','flagged_legs':[]})
        critique['flagged_legs'].append({'leg_index':len(card['legs']),'fixture_id':leg['fixture_id'],
            'reason':row['reason'],'claim_ids':[]})
        card['legs'].append(leg);fixtures[leg['fixture_id']]={'kickoff':leg['kickoff']}
    report={'date':original['date'],'recommendations':list(cards.values())}
    before=deepcopy(report);cutoff=datetime.fromisoformat(original['cutoff'])
    reviewed={'cutoff':cutoff,'fixtures':fixtures,'recommendations':{
        'review-'+profile:card for profile,card in cards.items()}}
    routed=grok_gate._concerns(report,{'result':{'critiques':list(critiques.values()),'claims':[]}},cutoff,reviewed=reviewed)
    packed,accepted=_packet(routed)
    assert len(accepted)==11
    laplace=next(c for c in json.loads(packed)['concerns'] if c['profile_id']=='laplace')['leg']
    assert 'model_stability' not in laplace
    assert laplace['model_probability']==0.44677400714344984
    assert laplace['estimated_edge']==0.054386656858541604
    assert laplace['decimal_price']==2.36 and laplace['quoted_at'] is None
    assert report==before and original==unchanged


@pytest.mark.parametrize('fields',[
    {'model_stability':None},{'estimated_edge':None},
    {'model_stability':None,'estimated_edge':None},
])
def test_unknown_optional_metrics_are_absent_without_changing_source_card(tmp_path,fields):
    report,advice,_=saved(tmp_path);leg=report['recommendations'][1]['legs'][0];leg.update(fields)
    before=deepcopy(report)
    packet=grok_gate._concerns(report,advice,NOW,reviewed=grok_gate._reviewed_packet(tmp_path,'review-1'))
    _,concerns=_packet(packet);clean=next(iter(concerns.values()))['leg']
    assert not set(fields)&set(clean)
    assert clean['quoted_at'] is None and clean['quote_observed_at']==NOW.isoformat()
    assert report==before


@pytest.mark.parametrize('field,value',[
    ('model_stability',0),('model_stability',.5),
    ('estimated_edge',-1),('estimated_edge',2),
])
def test_real_optional_metric_values_including_zero_and_negative_edge_are_preserved(tmp_path,field,value):
    report,advice,_=saved(tmp_path);report['recommendations'][1]['legs'][0][field]=value
    packet=grok_gate._concerns(report,advice,NOW,reviewed=grok_gate._reviewed_packet(tmp_path,'review-1'))
    _,concerns=_packet(packet)
    assert next(iter(concerns.values()))['leg'][field]==value


@pytest.mark.parametrize('field,value',[
    ('model_stability',True),('model_stability','unknown'),
    ('model_stability',float('nan')),('model_stability',float('inf')),
    ('model_stability',1.0001),('model_stability',-.0001),
    ('estimated_edge',-1.0001),('estimated_edge',float('inf')),
])
def test_invalid_non_null_metrics_still_fail_before_any_runner(tmp_path,field,value):
    report,advice,_=saved(tmp_path);report['recommendations'][1]['legs'][0][field]=value
    packet=grok_gate._concerns(report,advice,NOW,reviewed=grok_gate._reviewed_packet(tmp_path,'review-1'))
    assert len(packet['concerns'])==1
    with pytest.raises(ValueError,match='Invalid leg metric'):_packet(packet)


def test_unknown_probability_is_not_treated_as_an_optional_metric(tmp_path):
    report,advice,_=saved(tmp_path);report['recommendations'][1]['legs'][0]['model_probability']=None
    packet=grok_gate._concerns(report,advice,NOW,reviewed=grok_gate._reviewed_packet(tmp_path,'review-1'))
    assert packet['concerns'][0]['leg']['model_probability'] is None
    with pytest.raises(ValueError,match='Invalid numeric evidence'):_packet(packet)


def test_existing_valid_packet_cache_identity_survives_optional_metric_fix(tmp_path):
    report,advice,_=saved(tmp_path)
    leg=report['recommendations'][1]['legs'][0];leg.update(model_stability=0,estimated_edge=-.1)
    packet=grok_gate._concerns(report,advice,NOW,reviewed=grok_gate._reviewed_packet(tmp_path,'review-1'))
    legacy=deepcopy(packet);legacy['concerns'][0]['leg'].update(model_stability=0,estimated_edge=-.1)
    old_key=sha256(grok_gate._json(['grok-one-batch-concurrence-v1','review-1',legacy['concerns']]).encode()).hexdigest()
    new_key=sha256(grok_gate._json([grok_gate.VERSION,'review-1',packet['concerns']]).encode()).hexdigest()
    assert old_key==new_key


def test_null_metric_reaches_one_mocked_native_review_and_preserves_shadow_only_cache(tmp_path,monkeypatch):
    report,advice,_=saved(tmp_path);config(tmp_path)
    report['recommendations'][1]['legs'][0].update(model_stability=None,estimated_edge=None)
    before=deepcopy(report);monkeypatch.setattr(grok_gate,'analyze',lambda *args,**kw:advice)
    packet=grok_gate._concerns(report,advice,NOW,reviewed=grok_gate._reviewed_packet(tmp_path,'review-1'))
    concern=packet['concerns'][0]
    cli=Cli(result={'decisions':[{'concern_id':concern['concern_id'],'fixture_id':concern['leg']['fixture_id'],
        'agree':False,'basis':'NOT_CONFIRMED','reason':'No independently confirmed issue.','source_urls':[]}]})
    reviewer=ConcurrenceReviewer(command=['codex'],runner=cli)
    first=grok_gate.review_and_repair(tmp_path,report,{},reviewer=reviewer,clock=lambda:NOW)
    second=grok_gate.review_and_repair(tmp_path,report,{},reviewer=reviewer,clock=lambda:NOW)
    assert first['grok_review']['concurrence']['status']=='COMPLETE'
    assert second['grok_review']['concurrence']['cached']
    assert len([call for call in cli.calls if 'exec' in call[0]])==1
    assert report==before and first['recommendations']==before['recommendations']
    assert first['grok_review']['repair_rounds']==0 and not first['grok_review']['changed_profiles']
