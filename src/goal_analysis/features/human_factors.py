"""Sourced human-factor summaries, never verified events or causal uplifts."""
from datetime import timedelta

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.leakage import model_inputs
from goal_analysis.normalization.news_kinds import HUMAN_KINDS
from goal_analysis.normalization.training_datasets import snapshot
from goal_analysis.storage.sports_news import _assertion, _digest

VERSION='human-factors-evidence-shadow-v1'


def build_human_factors(fixtures, envelopes, *, as_of):
    cutoff=utc(as_of)
    if not isinstance(fixtures,list) or len(fixtures)>1000 or not isinstance(envelopes,list) or len(envelopes)>2000:
        raise ValueError('HUMAN_FACTORS_INPUT_BUDGET_EXCEEDED')
    targets={f['fixture_id']:dict(f) for f in fixtures}
    if len(targets)!=len(fixtures):raise ValueError('DUPLICATE_HUMAN_FACTORS_TARGET')
    for fixture in targets.values():
        if cutoff>utc(fixture['kickoff'])-timedelta(minutes=15):raise ValueError('PREMATCH_LOCKOUT')
    payloads=model_inputs(envelopes,as_of=cutoff)
    grouped={fid:{} for fid in targets}
    for p,e in zip(payloads,envelopes,strict=True):
        fid=p['fixture_id']
        if fid not in targets:raise ValueError('UNKNOWN_FIXTURE_CLAIM')
        assertion,_,fetched=_assertion({**p,'claim_id':'local-validation','fetched_at':p['source_fetched_at']},
                                      utc(e['times']['observed_at']).isoformat(timespec='microseconds'))
        if fetched!=e['times'].get('source_fetched_at'):
            raise ValueError('NEWS_FETCH_TIME_DIFFERS_FROM_PROOF')
        if any(p.get(k)!=value for k,value in assertion.items()) or p.get('assertion_hash')!=_digest(assertion):
            raise ValueError('INVALID_OR_PROMOTED_MODEL_ASSERTION')
        key=p['assertion_hash'];old=grouped[fid].get(key)
        if old is None:
            old={**assertion,'assertion_hash':key,'status':'SOURCED_CLAIM_UNVERIFIED',
                 'current_applicability':'UNASSESSED','event_time':None,'causal_effect':None,
                 'published_age_hours':(cutoff-utc(p['published_at'])).total_seconds()/3600,
                 'provenances':[]}
            grouped[fid][key]=old
        old['provenances'].append({'receipt_id':e['receipt_id'],'source_sha256':e['source_sha256'],
            'request_id':p['request_id'],'model':p['model'],'source_fetched_at':fetched,
            'observed_at':e['times']['observed_at'],'available_at':e['times']['available_at']})
    rows=[]
    for fid in sorted(targets):
        values=[grouped[fid][k] for k in sorted(grouped[fid])]
        human=[v for v in values if v['kind'] in HUMAN_KINDS]
        counts={kind:sum(v['kind']==kind for v in human) for kind in sorted(HUMAN_KINDS)}
        rows.append({'fixture_id':fid,'status':'SOURCED_UNVERIFIED' if human else 'NO_ACCEPTED_HUMAN_CLAIMS',
            'human_factors':human,'other_context_claims':[v for v in values if v['kind'] not in HUMAN_KINDS],
            'distinct_post_count':len({v['source_key'] for v in human}),
            'independent_corroboration':'NOT_ESTABLISHED','kind_counts':counts,
            'review_required':any(count>1 for count in counts.values()),
            'review_policy':'MULTIPLE_DISTINCT_ASSERTIONS_SAME_KIND_ARE_NOT_PROVEN_CONTRADICTIONS',
            'coverage_interpretation':'NO_ACCEPTED_CLAIM_IS_NOT_EVIDENCE_OF_NO_NEWS_OR_NO_RISK',
            'mental_motivation':None,'probability_effect':None})
    return {'version':VERSION,'mode':'SHADOW','as_of':cutoff.isoformat(),'rows':rows,
            'live_decision_effect':False,'content_policy':'MODEL_GENERATED_SUMMARY_NOT_VERIFIED_POST_CONTENT',
            'dataset':snapshot([*fixtures,*payloads],role='human_factors_features_shadow',as_of=cutoff,
                recipe={'version':VERSION,'human_kinds':sorted(HUMAN_KINDS)},lineage=envelopes)}
