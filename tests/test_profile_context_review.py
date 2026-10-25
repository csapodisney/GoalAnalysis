"""Exact original quote and actual wire barriers; all model transport is mocked."""
from copy import deepcopy
from datetime import timedelta

import pytest
from test_astra_review import KEY, Transport, candidate, response
from test_candidate_context_review import NOW, assessed, context

from goal_analysis.agents import canonical_sha256
from goal_analysis.agents.astra_review import AstraReviewer
from goal_analysis.jobs import candidate_context_review as context_job
from goal_analysis.jobs import profile_context_review as job


def quote(identifier='quote-1',fixture='the_stats_api:mt_123'):
    return {**candidate(identifier),'fixture_id':fixture,'kickoff':(NOW+timedelta(hours=2)).isoformat(),
        'period':'FULL_TIME','bookmaker':'synthetic_book','region':'eu','currency':'EUR',
        'quote_provider':'the_stats_api','quote_source_id':'https://api.thestatsapi.com/api/football/matches/mt_123/odds',
        'quote_source_sha256':'a'*64,'quote_observed_at':(NOW-timedelta(minutes=1)).isoformat(),
        'quote_time_basis':'LAST_SEEN_PRICE_WITH_LOCAL_RECEIPT','provider_fixture_id':'mt_123',
        'provider_home_team_id':'tm_1','provider_away_team_id':'tm_2',
        'provider_league_id':'comp_1','provider_season_id':'sn_1'}


def prepared(quotes):
    return {'as_of':NOW.isoformat(),'contexts':{q['fixture_id']:context(q['fixture_id']) for q in quotes}}


def generated(source,profile='gauss'):
    return {**deepcopy(source),'candidate_id':profile+'-'+source['candidate_id'],
        'source_candidate_id':source['candidate_id'],'quote_candidate_id':source['candidate_id'],
        'model_probability':.734,'estimated_edge':.2}


def card(quotes,profile='gauss'):
    return {'profile_id':profile,'legs':[generated(q,profile) for q in quotes]}


@pytest.fixture(autouse=True)
def actual_mock_clock(monkeypatch):
    monkeypatch.setattr('goal_analysis.agents.astra_review._utcnow',lambda:NOW)


def witnessed(quotes):
    sources=prepared(quotes);packets=context_job.attach(deepcopy(quotes),sources)
    transport=Transport(response({'reviews':[assessed(packet) for packet in packets]}))
    result=AstraReviewer(KEY,web_search=False,transport=transport).review(packets,NOW.date().isoformat())
    assert result['status']=='COMPLETE'
    artifacts={'candidate_review_actual_packets':packets,'candidate_review_response_batches':[
        {'packet_sha256':canonical_sha256(packets),'response':result}]}
    return artifacts,sources


@pytest.mark.parametrize('field',list(job.QUOTE_FIELDS)+list(job.PROVENANCE_FIELDS))
def test_generated_leg_cannot_borrow_review_from_different_quote_field(field):
    source=quote();leg=generated(source)
    leg[field]=2.2 if field=='decimal_price' else 'different'
    with pytest.raises(ValueError,match='ORIGINAL_QUOTE_FIELD_MISMATCH'):
        job.original_quote(leg,job.quote_index([source]))


def test_conflicting_original_quote_ids_and_duplicate_source_records_are_ambiguous():
    source=quote();leg=generated(source);leg['quote_candidate_id']='another'
    with pytest.raises(ValueError,match='CONFLICTING'):
        job.original_quote(leg,job.quote_index([source]))
    with pytest.raises(ValueError,match='NOT_UNIQUE'):
        job.original_quote(generated(source),job.quote_index([source,deepcopy(source)]))


@pytest.mark.parametrize('price',[None,True,float('nan'),float('inf'),1.,-1.])
def test_missing_or_invalid_price_cannot_become_reviewable_quote(price):
    source=quote();source['decimal_price']=price
    with pytest.raises(ValueError,match='NOT_PRICED'):
        job.original_quote(generated(source),job.quote_index([source]))


def test_model_probability_is_neither_a_quote_identity_field_nor_a_ranking_input():
    source=quote();leg=generated(source);before=deepcopy(leg)
    assert job.original_quote(leg,job.quote_index([source]))[0]==source['candidate_id']
    assert leg==before and leg['model_probability']==.734


def test_whole_card_batches_deduplicate_exact_source_quotes_across_profiles():
    quotes=[quote(str(i),'the_stats_api:mt_'+str(100+i)) for i in range(5)]
    report={'recommendations':[card(quotes[:3]),card(quotes[:3],'kepler'),card(quotes[3:],'laplace')]}
    before=deepcopy(report)
    result=job.plan(report,{},quotes,prepared(quotes),{},now=NOW,maximum=3,remaining_rounds=2)
    assert [len(batch) for batch in result['batches']]==[3,2]
    assert result['new_quote_count']==5 and result['gaps']==[]
    assert report==before
    assert all('model_probability' not in packet for batch in result['batches'] for packet in batch)


def test_daily_quotes_are_not_added_to_generic_followup_plan():
    source=quote()
    result=job.plan({'tickets':[card([source],'daily223')]},{},[source],prepared([source]),{},
        now=NOW,maximum=30,remaining_rounds=3)
    assert result['batches']==[]


def test_exhausted_existing_round_budget_stays_pending_without_partial_card_review():
    quotes=[quote(str(i),'the_stats_api:mt_'+str(i+100)) for i in range(4)]
    result=job.plan({'recommendations':[card(quotes[:2]),card(quotes[2:],'kepler')]},{},
        quotes,prepared(quotes),{},now=NOW,maximum=3,remaining_rounds=1)
    assert [len(batch) for batch in result['batches']]==[2]
    assert result['gaps'][0]['reason']=='PROFILE_REVIEW_ROUND_BUDGET'
    zero=job.plan({'recommendations':[card(quotes)]},{},quotes,prepared(quotes),{},
        now=NOW,maximum=30,remaining_rounds=0)
    assert zero['batches']==[] and zero['gaps']


def test_same_fixture_another_bookmaker_still_requires_its_own_review():
    first=quote('book-a');other=quote('book-b');other['bookmaker']='second_book'
    result=job.plan({'recommendations':[card([first]),card([other],'laplace')]},{},
        [first,other],prepared([first,other]),{first['candidate_id']:{}},
        now=NOW,maximum=30,remaining_rounds=3)
    assert [[p['candidate_id'] for p in batch] for batch in result['batches']]==[['book-b']]


@pytest.mark.parametrize('seconds',[0,1])
def test_review_cannot_start_at_or_inside_the_fifteen_minute_lockout(seconds):
    source=quote();source['kickoff']=(NOW+timedelta(minutes=15)-timedelta(seconds=seconds)).isoformat()
    result=job.plan({'recommendations':[card([source])]},{},[source],prepared([source]),{},
        now=NOW,maximum=30,remaining_rounds=3)
    assert result['batches']==[] and result['gaps'][0]['reason']=='PROFILE_REVIEW_PREMATCH_LOCKOUT'


def test_actual_mock_transport_wire_and_all_category_assessments_are_required():
    source=quote();artifacts,sources=witnessed([source]);before=deepcopy(artifacts)
    bound,issues=job.bindings(artifacts,sources,[source])
    assert issues==[] and list(bound)==[source['candidate_id']]
    assert bound[source['candidate_id']]['sourced_categories']==['players']
    assert set(bound[source['candidate_id']]['unknown_categories'])==set(context_job.CATEGORIES)-{'players'}
    assert artifacts==before


@pytest.mark.parametrize('change',['wire_hash','request_hash','candidate','missing_answer','wrong_evidence',
    'unknown_as_sourced','wrong_context','future_cutoff','partial_failure'])
def test_unproven_or_falsified_response_never_counts_as_source_coverage(change):
    source=quote();artifacts,sources=witnessed([source])
    batch=artifacts['candidate_review_response_batches'][0];result=batch['response']
    if change=='wire_hash':result['decision_context_input_sha256']='0'*64
    elif change=='request_hash':batch['packet_sha256']='0'*64
    elif change=='candidate':result['reviews'][0]['candidate_id']='invented'
    elif change=='missing_answer':result['reviews']=[]
    elif change=='wrong_evidence':result['reviews'][0]['context_assessments'][0]['evidence_ids']=['invented']
    elif change=='unknown_as_sourced':result['reviews'][0]['context_assessments'][1]['status']='considered'
    elif change=='wrong_context':sources['contexts'][source['fixture_id']]['categories'][0]['data']={'invented':True}
    elif change=='future_cutoff':result['reviewed_at']=(NOW-timedelta(seconds=1)).isoformat()
    else:result['status']='FAILED'
    bound,issues=job.bindings(artifacts,sources,[source])
    assert bound=={} and issues


def test_two_actual_responses_for_one_quote_are_not_silently_selected():
    source=quote();artifacts,sources=witnessed([source])
    artifacts['candidate_review_response_batches']*=2
    bound,issues=job.bindings(artifacts,sources,[source])
    assert bound=={} and issues[-1]['reason']=='AMBIGUOUS_MULTIPLE_ACTUAL_REVIEWS'


def test_shadow_never_changes_any_card_option_or_numeric_rank():
    source=quote();report={'recommendations':[card([source])]}
    options={'gauss':[{'quality':.79,'recommendation':deepcopy(report['recommendations'][0])}]}
    before=deepcopy((report,options))
    coverage=job.audit(report,options,[source],prepared([source]),{},now=NOW)
    assert coverage['counts']=={'PENDING':2}
    returned=job.apply(report,options,coverage,{},mode='SHADOW')
    assert returned[0] is report and returned[1] is options
    assert (report,options)==before


def test_active_withholds_unreviewed_alternative_preserving_independent_numeric_estimate():
    source=quote();primary=card([source]);primary['status']='MODEL_READY'
    report={'recommendations':[primary]}
    options={'gauss':[{'quality':.79,'recommendation':deepcopy(primary)}]}
    coverage=job.audit(report,options,[source],prepared([source]),{},now=NOW)
    gated,pools=job.apply(report,options,coverage,{},mode='ACTIVE')
    assert pools['gauss']==[]
    assert gated['recommendations'][0]['status']=='DRAFT'
    assert gated['recommendations'][0]['legs']==primary['legs']
    assert gated['recommendations'][0]['legs'][0]['model_probability']==.734
    assert primary['status']=='MODEL_READY' and options['gauss'][0]['quality']==.79


def test_covered_card_retains_numeric_score_order_and_actual_answer_without_daily_edits():
    source=quote();artifacts,sources=witnessed([source]);covered,issues=job.bindings(artifacts,sources,[source])
    assert not issues
    daily=card([source],'daily223');primary=card([source]);primary['status']='MODEL_READY'
    report={'tickets':[daily],'recommendations':[primary]}
    options={'gauss':[{'quality':.79,'recommendation':deepcopy(primary)}],
        'daily223':[{'ticket':deepcopy(daily),'quality':1.}]}
    coverage=job.audit(report,options,[source],sources,covered,now=NOW)
    gated,pools=job.apply(report,options,coverage,covered,mode='ACTIVE')
    assert gated['tickets']==report['tickets'] and pools['daily223']==options['daily223']
    selected=gated['recommendations'][0]
    assert selected['status']=='MODEL_READY' and selected['legs'][0]['model_probability']==.734
    assert pools['gauss'][0]['quality']==.79
    assert selected['legs'][0]['astra_review']==covered[source['candidate_id']]['answer']
    assert selected['profile_context_review']['status']=='SOURCE_BOUND'


def test_quote_changed_after_audit_cannot_use_earlier_coverage():
    source=quote();report={'recommendations':[card([source])]}
    coverage=job.audit(report,{},[source],prepared([source]),{},now=NOW)
    report['recommendations'][0]['legs'][0]['decimal_price']=9.9
    with pytest.raises(ValueError,match='CHANGED_AFTER_COVERAGE'):
        job.apply(report,{},coverage,{},mode='ACTIVE')


def test_shared_card_objects_do_not_bypass_or_break_coverage_validation():
    source=quote();shared=card([source]);report={'profile_proposals':[shared],'recommendations':[shared]}
    coverage=job.audit(report,{},[source],prepared([source]),{},now=NOW)
    gated,_=job.apply(report,{},coverage,{},mode='ACTIVE')
    assert all(gated[group][0]['status']=='DRAFT' for group in ('profile_proposals','recommendations'))
    assert 'status' not in shared
