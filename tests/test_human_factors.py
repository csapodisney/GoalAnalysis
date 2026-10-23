"""Synthetic cited summaries through real immutable news and availability stores."""
import json
from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from test_competition_types import NOW, setup
from test_grok_research import answer, packet, review

from goal_analysis.features.human_factors import build_human_factors
from goal_analysis.jobs.human_factors_shadow import (
    _read_news,
    archive_human_factors,
    inspect_human_factors,
)
from goal_analysis.normalization.leakage import LookAheadError
from goal_analysis.normalization.news_kinds import HUMAN_KINDS
from goal_analysis.normalization.training_datasets import sha
from goal_analysis.storage.sports_news import NewsStore
from goal_analysis.storage.temporal import TemporalStore, _select
from goal_analysis.storage.training_datasets import DatasetStore

PUBLISHED=NOW-timedelta(hours=2)
FETCHED=NOW-timedelta(minutes=50)
OBSERVED=NOW-timedelta(minutes=45)
AVAILABLE=NOW-timedelta(minutes=30)
POST=(int(PUBLISHED.timestamp()*1000)-1288834974657)<<22
SOURCE=f'https://x.com/ClubNews/status/{POST}'


def result(kind='SALARY_PAYMENT', text='A sourced model summary reports a payment issue.'):
    return {'status':'COMPLETE','model':'grok-4.7','citations':[SOURCE],
        'claims':[{'claim_id':'claim-1','fixture_id':'the_stats_api:mt_1','kind':kind,'claim':text,
                   'source_url':SOURCE,'author':'ClubNews','published_at':PUBLISHED.isoformat(),
                   'fetched_at':FETCHED.isoformat(),'content_status':'UNVERIFIED',
                   'provenance_status':'CITATION_AND_TIMESTAMP_CHECKED'}]}


def saved(tmp_path, *, kind='SALARY_PAYMENT', witness=True, available=AVAILABLE):
    db,_,_,_,fixture,_,_=setup(tmp_path)
    archive=NewsStore(db);archive.initialize()
    archive.ingest_grok_result(result(kind),request_id='test-1',observed_at=OBSERVED)
    if witness:TemporalStore(db,tmp_path/'data/raw',clock=lambda:available).capture('news',limit=10)
    return db,archive,fixture


def inputs(db,fixture):
    with db.connect() as d:
        rows=[dict(r) for r in d.execute(_select('news'))]
        envelopes=[_read_news(d,r,NOW) for r in rows]
    targets=[{'fixture_id':fixture['fixture_id'],'kickoff':fixture['kickoff']}]
    return targets,envelopes


@pytest.mark.parametrize('kind',sorted(HUMAN_KINDS))
def test_explicit_human_kinds_survive_import_and_only_become_sourced_unverified_summaries(tmp_path,kind):
    _,_,f=saved(tmp_path,kind=kind);v=inspect_human_factors(tmp_path,[f],as_of=NOW)
    row=v['rows'][0];claim=row['human_factors'][0]
    assert row['status']=='SOURCED_UNVERIFIED' and claim['kind']==kind
    assert claim['source_url']==SOURCE and claim['status']=='SOURCED_CLAIM_UNVERIFIED'
    assert claim['representation']=='MODEL_GENERATED_SUMMARY' and claim['current_applicability']=='UNASSESSED'
    assert claim['event_time'] is claim['causal_effect'] is row['mental_motivation'] is row['probability_effect'] is None
    assert not v['live_decision_effect'] and v['external_requests']==v['production_writes']==v['new_receipts_created']==0


@pytest.mark.parametrize('kind',['SALARY_PAYMENT','COACH_CHANGE','TEAM_CONFLICT','OTHER_HUMAN_FACTOR'])
def test_grok_accepts_new_explicit_kinds_without_relaxing_citations_or_timestamp_rules(kind):
    out=answer();out['claims'][0]['kind']=kind
    valid=review(out)
    assert valid['claims'][0]['kind']==kind and valid['claims'][0]['content_status']=='UNVERIFIED'
    out['claims'][0]['author']='WrongAuthor'
    assert review(out)['claims']==[]


def test_grok_json_schema_and_shared_archive_taxonomy_include_new_fields_without_extra_calls():
    from test_grok_research import response

    from goal_analysis.agents.grok_research import GrokAdvisor

    captured=[]
    def transport(payload,**kwargs):captured.append(payload);return response()
    GrokAdvisor(api_key='dummy',transport=transport).review(packet())
    enum=captured[0]['text']['format']['schema']['properties']['claims']['items']['properties']['kind']['enum']
    assert set(HUMAN_KINDS)<=set(enum) and len(captured)==1
    assert captured[0]['max_turns']==2


@pytest.mark.parametrize('kind',['OTHER','INJURY','LINEUP','ROTATION','TRAVEL'])
def test_old_or_other_context_is_not_keyword_reclassified_as_payment_or_conflict(tmp_path,kind):
    _,_,f=saved(tmp_path,kind=kind)
    v=inspect_human_factors(tmp_path,[f],as_of=NOW)['rows'][0]
    assert not v['human_factors'] and len(v['other_context_claims'])==1
    assert v['status']=='NO_ACCEPTED_HUMAN_CLAIMS'


def test_empty_news_store_never_means_no_risk_and_does_not_issue_a_paid_research_request(tmp_path):
    _,_,_,_,f,_,_=setup(tmp_path)
    v=inspect_human_factors(tmp_path,[f],as_of=NOW)
    assert v['rows'][0]['coverage_interpretation']=='NO_ACCEPTED_CLAIM_IS_NOT_EVIDENCE_OF_NO_NEWS_OR_NO_RISK'
    assert v['counts']=={'NO_ACCEPTED_HUMAN_CLAIMS':1} and v['accepted_assertion_imports']==0


@pytest.mark.parametrize('witness,available',[(False,AVAILABLE),(True,NOW+timedelta(seconds=1))])
def test_missing_or_future_local_availability_does_not_borrow_the_publication_timestamp(tmp_path,witness,available):
    _,_,f=saved(tmp_path,witness=witness,available=available)
    v=inspect_human_factors(tmp_path,[f],as_of=NOW)
    assert v['counts']=={'NO_ACCEPTED_HUMAN_CLAIMS':1} and v['issues']['INVALID_OR_UNAVAILABLE_NEWS_SOURCE']==1


def test_same_assertion_reimports_keep_receipts_but_do_not_create_independent_corroboration(tmp_path):
    db,archive,f=saved(tmp_path)
    archive.ingest_grok_result(result(),request_id='test-2',observed_at=NOW-timedelta(minutes=20))
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW-timedelta(minutes=10)).capture('news',limit=10)
    row=inspect_human_factors(tmp_path,[f],as_of=NOW)['rows'][0]
    assert len(row['human_factors'])==1 and len(row['human_factors'][0]['provenances'])==2
    assert row['distinct_post_count']==1 and row['independent_corroboration']=='NOT_ESTABLISHED'


def test_different_summaries_of_the_same_post_are_kept_for_review_not_automatically_superseded(tmp_path):
    db,archive,f=saved(tmp_path)
    archive.ingest_grok_result(result(text='Another summary reports the payment issue is resolved.'),
        request_id='test-2',observed_at=NOW-timedelta(minutes=20))
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW-timedelta(minutes=10)).capture('news',limit=10)
    row=inspect_human_factors(tmp_path,[f],as_of=NOW)['rows'][0]
    assert len(row['human_factors'])==2 and row['distinct_post_count']==1 and row['review_required']
    assert 'NOT_PROVEN_CONTRADICTIONS' in row['review_policy']


@pytest.mark.parametrize('fault',['hash','future','fetch_time','promoted_status','wrong_author','unknown_fixture'])
def test_model_boundary_rejects_tampered_future_or_promoted_claims(tmp_path,fault):
    db,_,f=saved(tmp_path);targets,envelopes=inputs(db,f);e=envelopes[0]
    if fault=='hash':e['payload']['claim']='tampered'
    if fault=='future':e['times']['available_at']=(NOW+timedelta(seconds=1)).isoformat()
    if fault=='fetch_time':e['payload']['source_fetched_at']=(FETCHED-timedelta(minutes=1)).isoformat()
    if fault=='promoted_status':e['payload']['content_status']='VERIFIED'
    if fault=='wrong_author':e['payload']['author']='WrongAuthor'
    if fault=='unknown_fixture':e['payload']['fixture_id']='the_stats_api:mt_99'
    if fault not in {'hash','future'}:e['payload_sha256']=sha(e['payload'])
    with pytest.raises((ValueError,LookAheadError)):build_human_factors(targets,envelopes,as_of=NOW)


def test_source_mutation_or_bad_receipt_digest_cannot_borrow_a_previous_witness(tmp_path):
    db,_,_=saved(tmp_path)
    with db.connect() as d:
        row=dict(d.execute(_select('news')).fetchone());row['claim']='changed'
        with pytest.raises(ValueError,match='CHANGED_AFTER_WITNESS'):_read_news(d,row,NOW)


def test_exact_fixture_link_lockout_and_input_budget_are_enforced(tmp_path):
    db,_,f=saved(tmp_path);targets,envelopes=inputs(db,f)
    f['provider_home_team_id']='tm_99'
    assert inspect_human_factors(tmp_path,[f],as_of=NOW)['rows']==[]
    with pytest.raises(ValueError,match='LOCKOUT'):
        build_human_factors(targets,envelopes,as_of=datetime.fromisoformat(targets[0]['kickoff'])-timedelta(minutes=14))
    with pytest.raises(ValueError,match='BUDGET'):build_human_factors(targets,envelopes*2001,as_of=NOW)


def test_readback_idempotence_safe_archive_failure_and_saved_tickets_unchanged(tmp_path,monkeypatch):
    _,_,f=saved(tmp_path);report={'fixtures':[f],'tickets':['frozen']};original=deepcopy(report)
    view=inspect_human_factors(tmp_path,[f],as_of=NOW)
    a=archive_human_factors(tmp_path,report,as_of=NOW);again=archive_human_factors(tmp_path,report,as_of=NOW)
    assert a['dataset_created'] and not again['dataset_created'] and report==original
    assert DatasetStore(tmp_path/'data/arthur/human-factors/datasets').read(a['dataset_id'])==view['dataset']
    monkeypatch.setattr(DatasetStore,'put',lambda *a:(_ for _ in ()).throw(OSError('private-detail')))
    failure=archive_human_factors(tmp_path,report,as_of=NOW)
    assert failure['status']=='UNAVAILABLE' and 'private-detail' not in json.dumps(failure)
