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
from goal_analysis.operating_policy import current_contract

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
                        or request.get('profile_scenario_context')!=packet.get('profile_scenario_context')
                        or any(request.get(field)!=packet.get(field) for field in QUOTE_FIELDS)
                        or utc(context['as_of'])>utc(response['reviewed_at'])):
                    raise ValueError('ACTUAL_PROFILE_SOURCE_CONTEXT_BINDING_DIFFERS')
                answer=by_id[identifier]
                pending.append((identifier,{'source_candidate_id':identifier,
                    'fixture_id':packet['fixture_id'],'context_id':context['context_id'],
                    'packet_batch_sha256':batch['packet_sha256'],'actual_wire_sha256':digest,
                    'response_batch_index':number,'answer':deepcopy(answer),
                    **({'profile_scenario_context_sha256':canonical_sha256(packet['profile_scenario_context'])}
                       if 'profile_scenario_context' in packet else {}),
                    'sourced_categories':[r['category'] for r in context['categories'] if r['status']=='SOURCED'],
                    'unknown_categories':[r['category'] for r in context['categories'] if r['status']!='SOURCED']}))
            for identifier,record in pending:
                if identifier in accepted:
                    prior=accepted[identifier]
                    if (record.get('profile_scenario_context_sha256') and not prior.get('profile_scenario_context_sha256')
                            and record['context_id']==prior['context_id']):
                        accepted[identifier]=record
                    else:
                        ambiguous.add(identifier)
                else:accepted[identifier]=record
        except (_ReviewError,ValueError,KeyError,TypeError,OverflowError) as error:
            issues.append({'response_batch_index':number,'reason':str(error) or type(error).__name__})
    for identifier in ambiguous:accepted.pop(identifier,None)
    if ambiguous:issues.append({'reason':'AMBIGUOUS_MULTIPLE_ACTUAL_REVIEWS','candidate_ids':sorted(ambiguous)})
    return accepted,issues


def cards(report,options):
    """Current canonical cohort first; legacy order and alternative ranks unchanged."""
    if current_contract(report.get('settings',report.get('policy',{}))):
        for number,card in enumerate(report.get('generated_tickets',[])):
            yield ('generated_tickets',number),card
    for group in ('tickets','recommendations','profile_proposals','preview_tickets'):
        for number,card in enumerate(report.get(group,[])):
            yield (group,number),card
    if current_contract(report.get('settings',report.get('policy',{}))):
        for number,source in enumerate(report.get('profile_decision_sources',[])):
            yield ('profile_decision_sources',number,'card'),source['card']
    for profile,values in options.items():
        for number,option in enumerate(values):
            for group in ('recommendation','ticket'):
                if group in option:yield ('options',profile,number,group),option[group]


def plan(report,options,quotes,prepared,covered,*,now,maximum,remaining_rounds):
    """Fit whole-card missing quote cohorts into the existing remaining budget."""
    if (type(maximum) is not int or not 1<=maximum<=40
            or type(remaining_rounds) is not int or not 0<=remaining_rounds<=3):
        raise ValueError('INVALID_EXISTING_PROFILE_REVIEW_BUDGET')
    scenarios=report.get('profile_predecision_scenarios',{}).get('reviewer_context_by_source_candidate_id',{})
    index=quote_index(quotes)
    seen={identifier for identifier,binding in covered.items()
          if identifier not in scenarios or binding.get('profile_scenario_context_sha256')==canonical_sha256(scenarios[identifier])}
    batches=[];current=[];gaps=[]
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
    for batch in packets:
        for packet in batch:
            facts=scenarios.get(packet['candidate_id'])
            if facts:
                packet['profile_scenario_context']=deepcopy(facts)
    return {'version':VERSION,'batches':packets,'gaps':gaps,
        'new_quote_count':sum(map(len,packets)),'maximum_candidates':maximum,
        'remaining_followup_rounds':remaining_rounds,'sports_requests':0,'numeric_fits':0,
        'new_probability_weights':False}


def audit(report,options,quotes,prepared,covered,*,now):
    """Evaluate primary and alternative source coverage without changing choices."""
    index=quote_index(quotes);rows=[]
    scenarios=(report.get('profile_predecision_scenarios',{}).get('reviewer_context_by_source_candidate_id',{})
               if current_contract(report.get('settings',report.get('policy',{}))) else {})
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
                expected_scenarios=scenarios.get(identifier)
                if (expected_scenarios is not None and binding.get('profile_scenario_context_sha256')
                        !=canonical_sha256(expected_scenarios)):
                    raise ValueError('NO_EXACT_CURRENT_PROFILE_SCENARIO_REVIEW')
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


def apply(report,options,coverage,covered,*,mode,final_review=None):
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
            from goal_analysis.jobs.review_completion import complete_card

            complete_card(card, final_review if final_review is not None else result.get('astra', {}))
        else:
            if location[0]=='options' and not current_contract(result.get('settings',result.get('policy',{}))):
                rejected.add((location[1],location[2]))
            # Preserve the exact independent model/selection in the frozen input
            # and this non-playable card; never upgrade it via attached prose.
            card.update(status='DRAFT',playable=False,selected_for_portfolio=False)
            warning='A pontos ajánlat kontextusértékelése hiányos; publikációra még nem jogosult.'
            if warning not in card.setdefault('quality_warnings',[]):card['quality_warnings'].append(warning)
    for profile,values in pools.items():
        pools[profile]=[option for number,option in enumerate(values) if (profile,number) not in rejected]
    return result,pools


def attach_decision_inputs(report,options,prepared,scenarios):
    """Use source facts in the persona's risk reasoning without inventing weights.

    Choices retain their own strategy and price/model maths. Numerical scenarios
    are uncalibrated evidence; missing context is explicitly disclosed locally.
    """
    assessments={row['profile_id']:row for row in scenarios.get('profiles',[])}
    from goal_analysis.jobs.profile_scenarios import source_leg_signature

    for _location,card in cards(report,options):
        if not card.get('legs'):
            continue
        assessment=assessments.get(card.get('profile_id'),{})
        scenario_bound=(assessment.get('source_card_sha256')==canonical_sha256(card)
                        or assessment.get('source_leg_signature')==source_leg_signature(card))
        facts=deepcopy(assessment.get('risk_facts',[])) if scenario_bound else []
        unknowns=deepcopy(assessment.get('uncertainties',[])) if scenario_bound else ['EXACT_PROFILE_SCENARIO_UNAVAILABLE']
        contexts=[]
        for fixture in dict.fromkeys(leg.get('fixture_id') for leg in card['legs']):
            context=(prepared or {}).get('contexts',{}).get(fixture)
            if not context:
                unknowns.append(str(fixture)+':OPTIONAL_CONTEXT_UNAVAILABLE')
                continue
            contexts.append(deepcopy(context))
            unknowns.extend(str(fixture)+':'+str(row.get('category'))+':UNKNOWN'
                            for row in context.get('categories',[]) if row.get('status')!='SOURCED')
        visible=[]
        if scenario_bound:
            for name in ('gauss','kepler','laplace'):
                model=assessment.get('models',{}).get(name,{})
                known,unknown=model.get('known_leg_count'),model.get('unknown_leg_count')
                if (type(known) is not int or type(unknown) is not int
                        or known<0 or unknown<0 or known+unknown==0):
                    continue
                probability=model.get('scenario_model_full_card_probability')
                sentence=(f'[S07] {name.upper()}: forráskötött, feltételes, nem kalibrált szcenárió; '
                          f'{known}/{known+unknown} láb modellezett, {unknown} UNKNOWN. ')
                if (unknown==0 and type(probability) in (int,float)
                        and math.isfinite(probability) and 0<=probability<=1):
                    sentence+=f'A teljes szelvény feltételes modell-esélye {100*probability:.2f}%.'
                else:
                    sentence+='A teljes szelvény esélye UNKNOWN.'
                visible.append(sentence)
        if unknowns:
            visible.append('[S07] Adat-/szcenárióhiány maradt; a név szerinti UNKNOWN-ok a mentett indoklásban szerepelnek.')
        previous=card.get('decision_reasoning',{}).get('visible_scenario_lines',[])
        original_reasoning=card.get('reasoning',[])
        base=deepcopy(original_reasoning) if isinstance(original_reasoning,list) else [deepcopy(original_reasoning)]
        card['reasoning']=[line for line in base if line not in previous]+visible
        card['decision_reasoning']={
            'phase':'BEFORE_FINAL_PROFILE_DECISION','choice':'RETAIN_OWN_STRATEGY_WITH_SOURCE_RISK_DISCLOSURE',
            'strategy':{key:deepcopy(card[key]) for key in ('method_kind','method_version','strategy_version','catalog_version') if key in card},
            'source_contexts':contexts,'scenario_risk_facts':facts,
            'scenario_context_sha256':assessment.get('context_sha256') if scenario_bound else None,
            'source_leg_signature':assessment.get('source_leg_signature') if scenario_bound else None,
            'visible_scenario_lines':deepcopy(visible),
            'uncertainties':sorted(set(unknowns)),
            'numeric_probability_or_rank_changed':False,'missing_context_is_neutral':False,
        }
    return report,options


def ensure_final_decision_inputs(report,options,artifacts,prepared,*,now):
    """Bind late own choices once, using existing predictions and no new requests.

    Allocation, an optional package reviewer or final lockout can change a
    tentative choice. Its old review cannot silently cover new scenario facts.
    """
    from goal_analysis.jobs.profile_scenarios import (
        build_profile_scenario_context,
        source_leg_signature,
    )

    prior=report.get('profile_predecision_scenarios',{})
    by_profile={row['profile_id']:row for row in prior.get('profiles',[])}
    changed=[card for card in report.get('generated_tickets',[])
             if by_profile.get(card['profile_id'],{}).get('source_leg_signature')!=source_leg_signature(card)]
    if changed:
        actual=build_profile_scenario_context(report,artifacts,changed,as_of=now)
        identities={card['profile_id'] for card in changed}
        merged=deepcopy(prior)
        merged['profiles']=[row for row in merged.get('profiles',[]) if row['profile_id'] not in identities]+actual['profiles']
        contexts={identifier:[row for row in rows if row['profile_id'] not in identities]
                  for identifier,rows in merged.get('reviewer_context_by_source_candidate_id',{}).items()}
        for identifier,rows in actual['reviewer_context_by_source_candidate_id'].items():
            contexts.setdefault(identifier,[]).extend(rows)
        merged['reviewer_context_by_source_candidate_id']={key:value for key,value in contexts.items() if value}
        merged.setdefault('late_rebindings',[]).append(actual)
        merged['sha256']=canonical_sha256({key:value for key,value in merged.items() if key!='sha256'})
        report['profile_predecision_scenarios']=merged
        artifacts['profile_predecision_scenarios']=deepcopy(merged)
    # Also refresh materialized aliases when their economic choice is unchanged.
    report,options=attach_decision_inputs(report,options,prepared,report.get('profile_predecision_scenarios',{}))
    if changed:
        quotes=artifacts.get('candidate_input',{}).get('candidates',[])
        covered,issues=bindings(artifacts,prepared or {},quotes)
        coverage=audit(report,options,quotes,prepared or {},covered,now=now)
        report,options=apply(report,options,coverage,covered,mode='ACTIVE',final_review=report.get('astra',{}))
        report['final_scenario_rebinding']={'profiles':sorted(identities),'llm_requests':0,'model_fits':0,
            'actual_existing_review_only':True,'unseen_new_facts_remain_DRAFT':True,'binding_issues':issues}
    return report,options
