"""Generated model/persona IDs require exact source quote and actual response."""
from copy import deepcopy

import pytest
from test_astra_review import KEY, Transport, response
from test_candidate_context_review import NOW, assessed
from test_profile_context_review import card, prepared, quote, witnessed
from test_profile_context_stage import execute, inputs

from goal_analysis.agents.astra_review import AstraReviewer
from goal_analysis.jobs import profile_context_review as contract
from goal_analysis.jobs.ticket_evidence import build_ticket_evidence, validate_ticket_evidence


@pytest.fixture(autouse=True)
def mock_clock(monkeypatch):
    monkeypatch.setattr('goal_analysis.agents.astra_review._utcnow',lambda:NOW)


def bound_profile():
    values=inputs()
    class Reviewer:
        def review(self,packets,date,feedback=None):
            transport=Transport(response({'reviews':[assessed(packet) for packet in packets]}))
            return AstraReviewer(KEY,web_search=False,transport=transport).review(packets,date)
    report,_,_=execute(values,Reviewer())
    report.update(run_id='synthetic-profile-evidence',date=NOW.date().isoformat(),finished_at=NOW.isoformat())
    report['candidate_context_review']={'mode':'ACTIVE'}
    artifacts=values[2]
    artifacts['candidate_context_inputs']=values[3]
    artifacts['candidate_input']={'candidates':values[4]}
    return report,artifacts


def selected_context(bundle):
    card=next(card for card in bundle['cards'] if card['profile_id']=='gauss')
    return card['legs'][0]['sections']['context']['preselection_review']


def test_generated_id_is_bound_through_exact_quote_and_actual_wire_response():
    report,artifacts=bound_profile();before=deepcopy((report,artifacts))
    bundle=build_ticket_evidence(report,artifacts)
    validate_ticket_evidence(bundle,report,artifacts)
    assert selected_context(bundle)['status']=='EXACT_SELECTED_LEG_REVIEW_BOUND'
    sourced=[r for r in selected_context(bundle)['categories'] if r['source_status']=='SOURCED']
    assert sourced and all(r['response_considered'] and r['context_evidence_cited'] for r in sourced)
    assert (report,artifacts)==before


@pytest.mark.parametrize('field',['bookmaker','decimal_price','quote_source_sha256','quote_observed_at'])
def test_borrowed_source_review_does_not_make_a_changed_quote_sourced(field):
    report,artifacts=bound_profile()
    leg=report['recommendations'][0]['legs'][0]
    leg[field]=9.9 if field=='decimal_price' else 'different'
    bundle=build_ticket_evidence(report,artifacts)
    assert selected_context(bundle)['status']=='NO_EXACT_ORIGINAL_QUOTE_CONTEXT_LINK'
    assert selected_context(bundle)['categories']==[]


def test_changed_quote_with_same_fixture_and_product_is_pending_even_when_other_quote_is_bound():
    first=quote('first');second=quote('second');second['bookmaker']='second_book'
    artifacts,sources=witnessed([first]);covered,issues=contract.bindings(artifacts,sources,[first,second])
    assert not issues
    coverage=contract.audit({'recommendations':[card([second])]}, {},[first,second],prepared([first,second]),covered,now=NOW)
    assert coverage['counts']=={'PENDING':1}
    assert coverage['cards'][0]['legs'][0]['reason']=='NO_EXACT_SOURCE_BOUND_PROFILE_REVIEW'
