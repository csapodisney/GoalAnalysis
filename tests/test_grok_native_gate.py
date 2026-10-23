"""Native quote binding and SHADOW concurrence; all analyst transports are local."""
import json
from contextlib import closing
from copy import deepcopy
from datetime import timedelta

import pytest
from test_grok_concurrence import SOURCE, Cli
from test_grok_gate import NOW, Reviewer, advice, report

from goal_analysis.agents.grok_concurrence import ConcurrenceReviewer
from goal_analysis.agents.grok_concurrence import _packet as concurrence_packet
from goal_analysis.agents.grok_research import _clean_leg
from goal_analysis.agents.grok_research import _packet as grok_packet
from goal_analysis.jobs import grok_advisory, grok_gate
from goal_analysis.providers.the_stats_odds import QUOTE_PROVENANCE, bookmaker_key


def native_report():
    r=report();leg=r['recommendations'][1]['legs'][0]
    leg.update(fixture_id='the_stats_api:mt_1',quote_provider='the_stats_api',quoted_at=None,
        quote_source_id='https://api.thestatsapi.com/api/football/matches/mt_1/odds',
        quote_observed_at=NOW.isoformat(),quote_source_sha256='a'*64,
        quote_time_basis='PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME',provider_bookmaker='Bet365',
        bookmaker=bookmaker_key('Bet365'),provider_market_id='match_odds')
    return r


def saved(root):
    r=native_report();p,coverage,run,report_hash=grok_advisory._packet(r,NOW)
    with closing(grok_advisory._open(root)) as db:
        db.execute('INSERT INTO grok_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            ('review-1','cache',run,report_hash,r['date'],r['date'],'2026-10',NOW.isoformat(),0,
             'grok-4.7',json.dumps(p),json.dumps(coverage)));db.commit()
    a=advice(r);mapping={rec['profile_id']:rec['recommendation_id'] for rec in p['recommendations']}
    for c in a['result']['critiques']:
        c.update(recommendation_id=mapping[c['profile_id']],strengths='Megadott modelladatok.',risks='Forrásolt kezdőkérdés.')
    return r,a,p


def config(root, mode='SHADOW'):
    (root/'config').mkdir(exist_ok=True)
    (root/'config/grok-native-gate.json').write_text(json.dumps({'enabled':True,'mode':mode}))


def test_exact_persisted_request_binds_whole_ticket_and_all_native_quote_fields(tmp_path):
    r,a,_=saved(tmp_path);bound=grok_gate._reviewed_packet(tmp_path,'review-1')
    p=grok_gate._concerns(r,a,NOW,reviewed=bound);assert len(p['concerns'])==1
    leg=p['concerns'][0]['leg'];original=r['recommendations'][1]['legs'][0]
    for key in (*QUOTE_PROVENANCE,'bookmaker','quote_source_id'):
        assert leg[key]==original[key]
    packed,concerns=concurrence_packet(p)
    assert concerns[p['concerns'][0]['concern_id']]['leg']['quoted_at'] is None
    assert json.loads(packed)['concerns'][0]['leg']['quote_observed_at']==NOW.isoformat()


def test_explicit_null_quote_clock_roundtrips_primary_and_independent_validators(tmp_path):
    _,_,p=saved(tmp_path);q=p['quote_sources'][0];q['quoted_at']=None
    clean,_,_,_=grok_packet(p)
    assert clean['quote_sources'][0]['quoted_at'] is None
    original=native_report()['recommendations'][1]['legs'][0]
    assert _clean_leg(original,{original['fixture_id']:None})['quoted_at'] is None


@pytest.mark.parametrize('field,value',[
    ('market_key','btts'),('selection_key','away'),('period','FIRST_HALF'),('decimal_price',3.0),
    ('bookmaker','wrong'),('quote_source_sha256','b'*64),('provider_market_id','btts'),
    ('quote_observed_at',(NOW+timedelta(seconds=1)).isoformat())])
def test_changed_selection_or_receipt_cannot_reuse_a_review_of_the_old_ticket(tmp_path,field,value):
    r,a,_=saved(tmp_path);bound=grok_gate._reviewed_packet(tmp_path,'review-1')
    r['recommendations'][1]['legs'][0][field]=value
    assert not grok_gate._concerns(r,a,NOW,reviewed=bound)['concerns']


@pytest.mark.parametrize('fault',['wrong_recommendation','wrong_profile','no_pro','no_contra','future_request','missing_request'])
def test_unbound_or_incomplete_pro_contra_review_never_becomes_a_concern(tmp_path,fault):
    r,a,_=saved(tmp_path);bound=grok_gate._reviewed_packet(tmp_path,'review-1')
    c=a['result']['critiques'][1]
    if fault=='wrong_recommendation':c['recommendation_id']='other'
    if fault=='wrong_profile':c['profile_id']='kepler'
    if fault=='no_pro':c['strengths']=''
    if fault=='no_contra':c['risks']=''
    if fault=='future_request':bound['cutoff']=NOW+timedelta(seconds=1)
    if fault=='missing_request':bound=None
    assert not grok_gate._concerns(r,a,NOW,reviewed=bound)['concerns']


def test_native_concurrence_uses_existing_one_batch_cli_but_cannot_publish_a_repair(tmp_path,monkeypatch):
    r,a,_=saved(tmp_path);config(tmp_path);original=deepcopy(r)
    monkeypatch.setattr(grok_gate,'analyze',lambda *args,**kw:a)
    p=grok_gate._concerns(r,a,NOW,reviewed=grok_gate._reviewed_packet(tmp_path,'review-1'))
    c=p['concerns'][0]
    cli=Cli(result={'decisions':[{'concern_id':c['concern_id'],'fixture_id':c['leg']['fixture_id'],
        'agree':True,'basis':'VERIFIED_CONTEXT','reason':'Lokális tesztben forrásolt, konkrét kérdés.',
        'source_urls':[SOURCE]}]})
    reviewer=ConcurrenceReviewer(command=['codex'],runner=cli)
    out=grok_gate.review_and_repair(tmp_path,r,{},reviewer=reviewer,clock=lambda:NOW)
    again=grok_gate.review_and_repair(tmp_path,r,{},reviewer=reviewer,clock=lambda:NOW)
    assert out['recommendations']==r['recommendations']==original['recommendations']
    assert out['grok_review']['repair_rounds']==0 and not out['grok_review']['changed_profiles']
    assert out['grok_review']['native_quote_gate']['mode']=='SHADOW'
    assert out['grok_review']['native_quote_gate']['confirmed_shadow_concerns']==1
    assert out['grok_review']['native_quote_gate']['would_repair_profiles']==['gauss']
    assert out['grok_review']['repair_status']=='NATIVE_CONCURRENCE_SHADOW_ONLY'
    assert again['grok_review']['concurrence']['cached']
    assert len([call for call in cli.calls if 'exec' in call[0]])==1


@pytest.mark.parametrize('mode',['LIVE','DISABLED'])
def test_missing_disabled_or_unpromoted_live_flag_does_not_start_native_analyst(tmp_path,monkeypatch,mode):
    r,a,_=saved(tmp_path);config(tmp_path,mode=mode)
    monkeypatch.setattr(grok_gate,'analyze',lambda *args,**kw:a);reviewer=Reviewer()
    out=grok_gate.review_and_repair(tmp_path,r,{},reviewer=reviewer,clock=lambda:NOW)
    assert not reviewer.calls and out['recommendations']==r['recommendations']


@pytest.mark.parametrize('fault',['no_source','wrong_basis','missing_basis','duplicate','wrong_fixture'])
def test_native_agreement_with_unobserved_source_or_wrong_basis_never_creates_shadow_proposal(tmp_path,monkeypatch,fault):
    r,a,_=saved(tmp_path);config(tmp_path);monkeypatch.setattr(grok_gate,'analyze',lambda *args,**kw:a)
    class Invalid(Reviewer):
        def review(self,packet):
            result=super().review(packet)
            for d in result['decisions']:
                d['basis']='VERIFIED_CONTEXT'
                if fault=='wrong_basis':d['basis']='INSUFFICIENT_EVIDENCE'
                if fault=='missing_basis':d.pop('basis')
                if fault=='wrong_fixture':d['fixture_id']='the_stats_api:mt_99'
            if fault=='duplicate':result['decisions']*=2
            return result
    out=grok_gate.review_and_repair(tmp_path,r,{},reviewer=Invalid(source=fault!='no_source'),clock=lambda:NOW)
    assert out['grok_review']['native_quote_gate']['confirmed_shadow_concerns']==0


def test_future_native_receipt_and_malformed_source_are_rejected_before_cli(tmp_path):
    r,a,_=saved(tmp_path);p=grok_gate._concerns(r,a,NOW,reviewed=grok_gate._reviewed_packet(tmp_path,'review-1'))
    p['concerns'][0]['leg']['quote_observed_at']=(NOW+timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError,match='Future native'):concurrence_packet(p)
    p['concerns'][0]['leg']['quote_observed_at']=NOW.isoformat()
    del p['concerns'][0]['leg']['quote_source_sha256']
    with pytest.raises(ValueError):concurrence_packet(p)


def test_native_request_loader_and_existing_legacy_protection_fail_closed_without_network(tmp_path):
    assert grok_gate._reviewed_packet(tmp_path,'missing') is None
    assert len(grok_gate._concerns(report(),advice(report()),NOW)['concerns'])==1
    p=report()['recommendations'][1]['legs'][0];p['quoted_at']=None
    with pytest.raises(ValueError):_clean_leg(p,{p['fixture_id']:None})


def test_equivalent_utc_clock_encodings_keep_exact_semantic_binding(tmp_path):
    r,a,_=saved(tmp_path);bound=grok_gate._reviewed_packet(tmp_path,'review-1')
    leg=r['recommendations'][1]['legs'][0]
    leg['kickoff']=leg['kickoff'].replace('+00:00','Z')
    leg['quote_observed_at']=leg['quote_observed_at'].replace('+00:00','Z')
    assert len(grok_gate._concerns(r,a,NOW,reviewed=bound)['concerns'])==1
