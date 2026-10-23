"""Non-executing, fixed-policy review of one calibration parameter family.

Physical evidence is supplied by jobs.calibration_release. Self-declared PASS
flags are never evidence. Even a successful review has no application authority.
"""
from datetime import timedelta

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha, validate

from .calibration import (
    GROUP,
    POLICY,
    _label,
    _scores,
    _select,
    group,
    shadow_probability,
    walk_forward,
)

VERSION = 'calibration-release-shadow-gate-v1'
POLICY_RELEASE = {'forecast_days': 30, 'shadow_consecutive_closed_utc_days': 7,
                  'minimum_shadow_fixtures': 7, 'maximum_registrations': 8}
NATIVE = {'native_shadow_gauss', 'native_shadow_kepler', 'native_shadow_laplace'}


def assess(dataset, calibration, observations, *, as_of):
    """Produce reviewable diffs, not permission to edit settings or bet."""
    cutoff = utc(as_of); validate(dataset)
    if utc(calibration['as_of']) > cutoff or len(observations) > 10000:
        raise ValueError('RELEASE_FUTURE_REPORT_OR_OBSERVATION_BUDGET')
    actual = walk_forward(dataset, as_of=calibration['as_of'])
    if (sha(actual) != sha(calibration) or calibration['policy'] != POLICY
            or calibration['automatic_parameter_writes'] is not False
            or calibration['live_decision_effect'] is not False):
        raise ValueError('RELEASE_CALIBRATION_NOT_REPRODUCED_UNDER_FIXED_POLICY')
    rows, _ = _select(dataset['rows'], utc(calibration['as_of']))
    groups = []
    for value in actual['groups']:
        cohort = value['group']; key = tuple(cohort[k] for k in GROUP)
        matched = [r for r in rows if group(r) == key and _label(r, utc(actual['as_of'])) is not None]
        days = sorted({utc(r['saved_at']).date() for r in matched})
        candidate = value['candidate']
        checks = {'walk_forward': value['status'] == 'CANDIDATE_SHADOW',
            'supported_observer_cohort': cohort['profile_id'] in NATIVE,
            'thirty_real_forecast_days': len(days) >= POLICY_RELEASE['forecast_days']
                and (days[-1]-days[0]).days >= POLICY_RELEASE['forecast_days']-1,
            'parameter_change': candidate is not None and any(
                candidate['parameters'][k] != v for k,v in {'temperature':1.,'bias':0.}.items())}
        shadow = _shadow(candidate, observations, cutoff) if candidate is not None else _empty_shadow()
        checks.update(shadow_seven_closed_days=shadow['consecutive_closed_days'] == 7,
                      shadow_settled=shadow['unsettled_forecasts'] == 0 and shadow['unique_fixtures'] >= 7,
                      shadow_integrity=shadow['invalid_observations'] == 0,
                      shadow_proper_scores=shadow['scores_not_degraded'])
        blockers = sorted(k for k,v in checks.items() if not v)
        groups.append({'group':cohort,'candidate':candidate,'checks':checks,'blocked_by':blockers,
            'status':'ELIGIBLE_FOR_MANUAL_REVIEW' if not blockers else 'BLOCKED',
            'parameter_family':'PROBABILITY_CALIBRATION_ONLY','real_forecast_days':len(days),
            'baseline':{'kind':'ORIGINAL_UNTRANSFORMED_MODEL_ESTIMATE','temperature':1.,'bias':0.},
            'proposed_parameters':None if candidate is None else {
                k:candidate['parameters'][k] for k in ('temperature','bias')},
            'shadow':shadow,'live_approved':False,'apply_allowed':False})
    result = {'version':VERSION,'as_of':cutoff.isoformat(),'mode':'SHADOW',
        'status':'REVIEW_AVAILABLE' if any(g['status']=='ELIGIBLE_FOR_MANUAL_REVIEW' for g in groups) else 'BLOCKED',
        'groups':groups,'blockers':[] if groups else ['NO_SOURCE_BOUND_CALIBRATION_CANDIDATE'],
        'policy':POLICY_RELEASE,'input_dataset_id':dataset['manifest']['dataset_id'],
        'calibration_report_sha256':actual['report_sha256'],
        'remaining_promotion_gates':['FULL_INTEGRATION','THIRTY_DAY_REPLAY','THIRTY_DAY_PAPER',
            'ONE_FAMILY_CANARY','AUDITED_ROLLBACK','SEPARATE_EXPLICIT_APPROVAL'],
        'automatic_parameter_writes':False,'live_decision_effect':False,'apply_allowed':False}
    result['report_sha256'] = sha(result)
    return result


def _empty_shadow():
    return {'consecutive_closed_days':0,'unique_fixtures':0,'unsettled_forecasts':0,
            'original_scores':_scores([]),'candidate_scores':_scores([]),
            'scores_not_degraded':False,'excluded':0,'invalid_observations':0}


def _shadow(candidate, observations, cutoff):
    end = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end-timedelta(days=7)
    first, excluded, invalid = {}, 0, 0
    for item in sorted(observations,key=lambda r:(utc(r['shadow_available_at']),sha(r))):
        if item['candidate_id'] != candidate['candidate_id']:
            continue
        row = item['forecast']; available = utc(item['shadow_available_at'])
        if available > cutoff:
            continue
        if row['fixture_id'] in first:
            # One native fixture cannot count as several prospective trials.
            previous=first[row['fixture_id']]
            if (previous is not None and utc(previous['shadow_available_at'])==available
                    and (sha(previous['probability'])!=sha(item['calibrated_probability'])
                         or sha(previous['original_probability'])!=sha(row['probability']))):
                raise ValueError('RELEASE_CONFLICTING_FIRST_SHADOW_FORECASTS')
            excluded += 1
            continue
        first[row['fixture_id']] = None
        registered = utc(item['registered_at'])
        if not registered < utc(row['model_as_of']) <= utc(row['created_at']) <= utc(row['saved_at']) <= available:
            raise ValueError('RELEASE_SHADOW_NOT_PROSPECTIVE_OR_LOCKED')
        if available >= utc(row['kickoff'])-timedelta(minutes=15):
            invalid += int(start <= available < end)
            continue
        expected = shadow_probability(candidate,row,as_of=available)['probability']
        if sha(expected) != sha(item['calibrated_probability']):
            raise ValueError('RELEASE_SHADOW_PROBABILITY_DIFFERENCE')
        first[row['fixture_id']] = {**row,'probability':expected,'original_probability':row['probability'],
            'shadow_available_at':available.isoformat()}
    evaluated = [r for r in first.values() if r is not None and start <= utc(r['shadow_available_at']) < end]
    days = {utc(r['shadow_available_at']).date() for r in evaluated}
    known = [v for r in evaluated if (v:=_label(r,cutoff)) is not None]
    old = _scores([{**r,'probability':r['original_probability']} for r in known]); new = _scores(known)
    complete = len(days)==7 and len(known)==len(evaluated) and len(known)>=7 and {r['outcome'] for r in known}=={0,1}
    return {'consecutive_closed_days':len(days),'unique_fixtures':len(evaluated),
        'unsettled_forecasts':len(evaluated)-len(known),'original_scores':old,'candidate_scores':new,
        'scores_not_degraded':complete and all(new[k]<=old[k]+1e-12 for k in ('brier','log_loss')),
        'closed_window_start':start.isoformat(),'closed_window_stop':end.isoformat(),
        'excluded':excluded,'invalid_observations':invalid}
