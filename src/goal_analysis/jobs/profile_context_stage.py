"""Bounded profile review stage over an already frozen daily source snapshot."""
import json
from copy import deepcopy
from pathlib import Path

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import profile_context_review as contract
from goal_analysis.normalization.canonical import utc


def policy(root):
    path=Path(root)/'config/profile-context-review.json'
    if not path.is_file():return 'OFF'
    if path.stat().st_size>1000:raise ValueError('INVALID_PROFILE_CONTEXT_POLICY')
    value=json.loads(path.read_bytes())
    if value not in ({'version':contract.VERSION,'mode':'SHADOW'},
                     {'version':contract.VERSION,'mode':'ACTIVE'}):
        raise ValueError('INVALID_PROFILE_CONTEXT_POLICY')
    return value['mode']


def execute(report,options,artifacts,prepared,quotes,review,*,mode,reviewer,target_date,
            feedback,maximum,remaining_rounds,clock,merge):
    """No sports collection; failed supplements cannot replace the primary review."""
    if mode not in {'SHADOW','ACTIVE'}:raise ValueError('INVALID_PROFILE_CONTEXT_MODE')
    before=canonical_sha256(report)
    if mode=='ACTIVE':
        artifacts['profile_context_independent_cards']={
            group:deepcopy(report.get(group,[]))
            for group in ('tickets','recommendations','profile_proposals','preview_tickets')}
    covered,issues=contract.bindings(artifacts,prepared,quotes)
    requested=contract.plan(report,options,quotes,prepared,covered,now=utc(clock()),
        maximum=maximum,remaining_rounds=remaining_rounds)
    artifacts['profile_context_review_plan']=deepcopy(requested)
    attempts=[]
    no_primary_request=(not artifacts.get('candidate_review_actual_packets') and not review.get('reviews')
        and not artifacts.get('candidate_review_response_batches'))
    if mode=='ACTIVE' and reviewer is not None and (review.get('status')=='COMPLETE' or no_primary_request):
        for planned in requested['batches']:
            # Do not partially send a card whose event entered lockout while a
            # previous batch was running. The final audit records pending gaps.
            if any(not contract.allows_new_leg(packet,utc(clock())) for packet in planned):
                attempts.append({'status':'PREMATCH_LOCKOUT_NO_REQUEST'});break
            batch=deepcopy(planned)
            artifacts.setdefault('candidate_review_follow_up_packets',[]).append(batch)
            try:
                response=reviewer.review(batch,target_date,feedback=feedback)
            except Exception as error:  # noqa: BLE001 - never retry a failed optional review
                response={'status':'UNAVAILABLE','failure_class':type(error).__name__,
                    'usage':{},'request_usage_unknown':True}
            artifacts.setdefault('candidate_review_response_batches',[]).append({
                'packet_sha256':canonical_sha256(batch),'response':deepcopy(response)})
            current,new_issues=contract.bindings(artifacts,prepared,quotes)
            expected={packet['candidate_id'] for packet in batch}
            completed=response.get('status')=='COMPLETE' and expected<=set(current)
            attempts.append({'packet_sha256':canonical_sha256(batch),'candidates':len(batch),
                'response_status':response.get('status'),'exact_context_bindings_complete':completed,
                'response_usage':deepcopy(response.get('usage',{})),
                'request_usage_unknown':response.get('request_usage_unknown') is True,'automatic_retry':False})
            issues=new_issues
            if not completed:
                # Keep the successful primary answers/status. Account only the
                # usage actually returned by the failed supplementary attempt.
                usage=deepcopy(review.get('usage',{}))
                for key,value in response.get('usage',{}).items():
                    if type(value) in (int,float):
                        usage[key]=usage.get(key,0)+value if type(usage.get(key,0)) in (int,float) else value
                review={**review,'usage':usage}
                break
            review=merge(review,response) if review.get('status')=='COMPLETE' else deepcopy(response)
            covered=current
    coverage=contract.audit(report,options,quotes,prepared,covered,now=utc(clock()))
    coverage['mode']=mode
    artifacts['profile_context_review_coverage']=coverage
    artifacts['profile_context_review_attempts']=attempts
    result,pools=contract.apply(report,options,coverage,covered,mode=mode,final_review=review)
    if mode=='SHADOW':
        assert canonical_sha256(result)==before
    else:
        result['astra']=deepcopy(review)
        result['profile_context_review']={'version':contract.VERSION,'mode':mode,
            'status':'APPLIED_EXACT_SOURCE_PUBLICATION_GATE','counts':coverage['counts'],
            'actual_bound_original_quotes':len(covered),'binding_issues':issues,
            'supplementary_batches':len(attempts),'planned_batches':len(requested['batches']),
            'request_usage_unknown':any(a.get('request_usage_unknown') for a in attempts),
            'remaining_original_followup_round_budget':remaining_rounds,
            'maximum_candidates_per_batch':maximum,'sports_requests':0,
            'independent_numeric_rank_and_probability_unchanged':True,
            'new_probability_weights':False,'automatic_retry':False}
    return result,pools,review
