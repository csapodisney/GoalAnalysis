"""Exact quote/context review coverage; independent numeric ranks stay untouched.

These pure readers/planners perform no provider/model calls or state writes.
Publication integration must explicitly choose SHADOW or ACTIVE separately.
"""
import json
import math
from collections import Counter
from copy import deepcopy
from hashlib import sha256

from goal_analysis.agents import canonical_sha256
from goal_analysis.agents.astra_review import _expand_shared_facts, _ReviewError, _validate_reviews
from goal_analysis.engine.publication_window import allows_new_leg
from goal_analysis.jobs.candidate_context_review import CATEGORIES, attach
from goal_analysis.normalization.canonical import utc

VERSION='profile-context-review-v1'
QUOTE_FIELDS=('fixture_id','home_team','away_team','kickoff','market_key','selection_key',
    'period','decimal_price','bookmaker','region','currency','quote_provider','quote_source_id')
PROVENANCE_FIELDS=('quote_source_sha256','quote_observed_at','quote_time_basis','provider_bookmaker',
    'provider_fixture_id','provider_home_team_id','provider_away_team_id','provider_league_id','provider_season_id')


def quote_index(quotes):
    """Keep duplicate original IDs ambiguous, even if their payloads are equal."""
    if not isinstance(quotes,list) or len(quotes)>10000:
        raise ValueError('INVALID_ORIGINAL_QUOTE_LIST')
    indexed={}
    for quote in quotes:
        identifier=quote.get('candidate_id') if isinstance(quote,dict) else None
        if not isinstance(identifier,str) or not identifier:
            raise ValueError('MISSING_ORIGINAL_QUOTE_ID')
        indexed.setdefault(identifier,[]).append(quote)
    return indexed


def original_quote(leg,index):
    """Never transfer another bookmaker's/product's response to a generated leg."""
    source=leg.get('source_candidate_id');quote=leg.get('quote_candidate_id')
    if source and quote and source!=quote:
        raise ValueError('CONFLICTING_ORIGINAL_QUOTE_IDS')
    identifier=source or quote or leg.get('candidate_id')
    rows=index.get(identifier,[])
    if len(rows)!=1:
        raise ValueError('ORIGINAL_QUOTE_NOT_UNIQUE')
    original=rows[0]
    if any(not original.get(field) for field in QUOTE_FIELDS if field!='decimal_price'):
        raise ValueError('ORIGINAL_QUOTE_IDENTITY_INCOMPLETE')
    if (type(original.get('decimal_price')) not in (int,float)
            or not math.isfinite(original['decimal_price']) or original['decimal_price']<=1):
        raise ValueError('ORIGINAL_QUOTE_NOT_PRICED')
    for field in (*QUOTE_FIELDS,*PROVENANCE_FIELDS):
        if (field in original or field in leg) and leg.get(field)!=original.get(field):
            raise ValueError('ORIGINAL_QUOTE_FIELD_MISMATCH:'+field)
    return identifier,original


def _context(prepared,fixture_id):
    value=prepared.get('contexts',{}).get(fixture_id)
    if (not isinstance(value,dict) or value.get('fixture_id')!=fixture_id
            or not isinstance(value.get('categories'),list)
            or not all(isinstance(row,dict) for row in value['categories'])
            or canonical_sha256({k:v for k,v in value.items() if k!='context_id'})!=value.get('context_id')
            or Counter(row.get('category') for row in value.get('categories',[]))!=Counter(CATEGORIES)):
        raise ValueError('FROZEN_PROFILE_CONTEXT_NOT_PROVEN')
    if utc(value['as_of'])!=utc(prepared['as_of']):
        raise ValueError('FROZEN_PROFILE_CONTEXT_CUTOFF_DIFFERS')
    return value


def bindings(artifacts,prepared,quotes):
    """Revalidate exact actual wire and parsed response; return only proven IDs.

    A bad batch is recorded as a gap, never retried or represented as coverage.
    Sources may be unavailable: their explicit unknown assessments are preserved.
    """
    index=quote_index(quotes)
    requested=[artifacts.get('candidate_review_actual_packets',[]),
        *artifacts.get('candidate_review_follow_up_packets',[])]
    by_hash={canonical_sha256(batch):batch for batch in requested if batch}
    accepted={};issues=[];ambiguous=set()
    for number,batch in enumerate(artifacts.get('candidate_review_response_batches',[])):
        try:
            packets=by_hash.get(batch.get('packet_sha256'))
            response=batch.get('response',{})
            if not packets or response.get('status')!='COMPLETE':
                raise ValueError('NO_COMPLETE_REQUEST_RESPONSE_BATCH')
            wire=response['decision_context_input']
            digest=sha256(json.dumps(wire,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
            if digest!=response.get('decision_context_input_sha256'):
                raise ValueError('ACTUAL_PROFILE_REVIEW_WIRE_HASH_MISMATCH')
            expanded=_expand_shared_facts(wire)
            sent=expanded.get('candidates',[])
            if Counter(p.get('candidate_id') for p in sent)!=Counter(p.get('candidate_id') for p in packets):
                raise ValueError('ACTUAL_PROFILE_REVIEW_CANDIDATES_DIFFER')
            wire_contexts={c['context_id']:c for c in expanded.get('decision_contexts',[])}
            if len(wire_contexts)!=len(expanded.get('decision_contexts',[])):
                raise ValueError('DUPLICATE_WIRE_CONTEXT')
            sent_by_id={p['candidate_id']:p for p in sent}
            if len(sent_by_id)!=len(sent):
                raise ValueError('DUPLICATE_REQUESTED_QUOTE')
            answers=_validate_reviews({'reviews':response.get('reviews')},sent,response.get('sources',[]))
            by_id={answer['candidate_id']:answer for answer in answers}
            pending=[]
            for packet in packets:
                identifier,_=original_quote(packet,index)
                context=_context(prepared,packet['fixture_id'])
                request=sent_by_id[identifier]
                if (packet.get('decision_context')!=context
                        or request.get('decision_context',{}).get('context_id')!=context['context_id']
                        or wire_contexts.get(context['context_id'])!=context
                        or any(request.get(field)!=packet.get(field) for field in QUOTE_FIELDS)
                        or utc(context['as_of'])>utc(response['reviewed_at'])):
                    raise ValueError('ACTUAL_PROFILE_SOURCE_CONTEXT_BINDING_DIFFERS')
                answer=by_id[identifier]
                pending.append((identifier,{'source_candidate_id':identifier,
                    'fixture_id':packet['fixture_id'],'context_id':context['context_id'],
                    'packet_batch_sha256':batch['packet_sha256'],'actual_wire_sha256':digest,
                    'response_batch_index':number,'answer':deepcopy(answer),
                    'sourced_categories':[r['category'] for r in context['categories'] if r['status']=='SOURCED'],
                    'unknown_categories':[r['category'] for r in context['categories'] if r['status']!='SOURCED']}))
            for identifier,record in pending:
                if identifier in accepted:
                    ambiguous.add(identifier)
                else:accepted[identifier]=record
        except (_ReviewError,ValueError,KeyError,TypeError,OverflowError) as error:
            issues.append({'response_batch_index':number,'reason':str(error) or type(error).__name__})
    for identifier in ambiguous:accepted.pop(identifier,None)
    if ambiguous:issues.append({'reason':'AMBIGUOUS_MULTIPLE_ACTUAL_REVIEWS','candidate_ids':sorted(ambiguous)})
    return accepted,issues


def cards(report,options):
    """Primary cards first, then whole alternatives in their original rank order."""
    for group in ('tickets','recommendations','profile_proposals','preview_tickets'):
        for number,card in enumerate(report.get(group,[])):
            yield (group,number),card
    for profile,values in options.items():
        for number,option in enumerate(values):
            for group in ('recommendation','ticket'):
                if group in option:yield ('options',profile,number,group),option[group]


def plan(report,options,quotes,prepared,covered,*,now,maximum,remaining_rounds):
    """Fit whole-card missing quote cohorts into the existing remaining budget."""
    if (type(maximum) is not int or not 1<=maximum<=40
            or type(remaining_rounds) is not int or not 0<=remaining_rounds<=3):
        raise ValueError('INVALID_EXISTING_PROFILE_REVIEW_BUDGET')
    index=quote_index(quotes);seen=set(covered);batches=[];current=[];gaps=[]
    for location,card in cards(report,options):
        if card.get('profile_id')=='daily223' or not card.get('legs'):continue
        missing=[]
        try:
            for leg in card['legs']:
                identifier,quote=original_quote(leg,index)
                _context(prepared,quote['fixture_id'])
                if not allows_new_leg(quote,now):raise ValueError('PROFILE_REVIEW_PREMATCH_LOCKOUT')
                if identifier not in seen and identifier not in missing:missing.append(identifier)
            if len(missing)>maximum:raise ValueError('WHOLE_PROFILE_EXCEEDS_REVIEW_BATCH')
            if missing and len(current)+len(missing)>maximum:
                batches.append(current);current=[]
            if missing and len(batches)>=remaining_rounds:
                raise ValueError('PROFILE_REVIEW_ROUND_BUDGET')
            current.extend(missing);seen.update(missing)
        except (ValueError,KeyError,TypeError) as error:
            gaps.append({'location':list(location),'profile_id':card.get('profile_id'),'reason':str(error)})
    if current:batches.append(current)
    assert len(batches)<=remaining_rounds and all(0<len(batch)<=maximum for batch in batches)
    packets=[attach([deepcopy(index[identifier][0]) for identifier in batch],prepared) for batch in batches]
    return {'version':VERSION,'batches':packets,'gaps':gaps,
        'new_quote_count':sum(map(len,packets)),'maximum_candidates':maximum,
        'remaining_followup_rounds':remaining_rounds,'sports_requests':0,'numeric_fits':0,
        'new_probability_weights':False}


def audit(report,options,quotes,prepared,covered,*,now):
    """Evaluate primary and alternative source coverage without changing choices."""
    index=quote_index(quotes);rows=[]
    for location,card in cards(report,options):
        if card.get('profile_id')=='daily223' or not card.get('legs'):continue
        legs=[]
        for number,leg in enumerate(card['legs']):
            row={'leg_index':number,'candidate_id':leg.get('candidate_id'),'fixture_id':leg.get('fixture_id')}
            try:
                identifier,quote=original_quote(leg,index)
                context=_context(prepared,quote['fixture_id'])
                binding=covered.get(identifier)
                if not allows_new_leg(quote,now):raise ValueError('PROFILE_REVIEW_PREMATCH_LOCKOUT')
                if not binding or binding.get('context_id')!=context['context_id']:
                    raise ValueError('NO_EXACT_SOURCE_BOUND_PROFILE_REVIEW')
                row.update(status='EXACT_PROFILE_CONTEXT_REVIEW_BOUND',source_candidate_id=identifier,
                    context_id=context['context_id'],actual_wire_sha256=binding['actual_wire_sha256'],
                    packet_batch_sha256=binding['packet_batch_sha256'],
                    response_batch_index=binding['response_batch_index'],
                    sourced_categories=deepcopy(binding['sourced_categories']),
                    unknown_categories=deepcopy(binding['unknown_categories']))
            except (ValueError,KeyError,TypeError) as error:
                row.update(status='PENDING',reason=str(error))
            legs.append(row)
        rows.append({'location':list(location),'profile_id':card.get('profile_id'),
            'original_card_sha256':canonical_sha256(card),'legs':legs,
            'eligible':all(row['status']=='EXACT_PROFILE_CONTEXT_REVIEW_BOUND' for row in legs)})
    return {'version':VERSION,'mode':'SHADOW','cards':rows,
        'counts':dict(Counter('BOUND' if row['eligible'] else 'PENDING' for row in rows)),
        'decision_effect':'NONE','numeric_probabilities_or_rank_changed':False,
        'sports_requests':0,'llm_requests':0}


def apply(report,options,coverage,covered,*,mode):
    """Separate publication eligibility from the retained independent selection.

    SHADOW returns the original objects unchanged. ACTIVE withholds unreviewed
    alternatives and labels primary gaps; it never edits numeric fields or DAILY.
    """
    if mode not in {'SHADOW','ACTIVE'}:raise ValueError('INVALID_PROFILE_CONTEXT_MODE')
    if mode=='SHADOW':return report,options
    result,pools=deepcopy(report),deepcopy(options)
    by_location={tuple(row['location']):row for row in coverage['cards']}
    rejected=set()
    materialized=list(cards(result,pools))
    for location,card in materialized:
        if card.get('profile_id')=='daily223' or not card.get('legs'):continue
        row=by_location.get(tuple(location))
        if row is None or row['original_card_sha256']!=canonical_sha256(card):
            raise ValueError('PROFILE_CARD_CHANGED_AFTER_COVERAGE_AUDIT')
    for location,card in materialized:
        if card.get('profile_id')=='daily223' or not card.get('legs'):continue
        row=by_location[tuple(location)]
        card['profile_context_review']={'version':VERSION,'mode':'ACTIVE',
            'status':'SOURCE_BOUND' if row['eligible'] else 'PENDING','legs':deepcopy(row['legs']),
            'numeric_probabilities_or_rank_changed':False,'new_probability_weights':False}
        if row['eligible']:
            for leg,binding in zip(card['legs'],row['legs'],strict=True):
                leg['astra_review']=deepcopy(covered[binding['source_candidate_id']]['answer'])
        else:
            if location[0]=='options':rejected.add((location[1],location[2]))
            # Preserve the exact independent model/selection in the frozen input
            # and this non-playable card; never upgrade it via attached prose.
            card.update(status='DRAFT',playable=False,selected_for_portfolio=False)
            warning='A pontos ajánlat kontextusértékelése hiányos; publikációra még nem jogosult.'
            if warning not in card.setdefault('quality_warnings',[]):card['quality_warnings'].append(warning)
    for profile,values in pools.items():
        pools[profile]=[option for number,option in enumerate(values) if (profile,number) not in rejected]
    return result,pools
