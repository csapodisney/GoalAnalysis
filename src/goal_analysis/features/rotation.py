"""Explainable SHADOW indicators, never a rotation probability or a B-team claim.

Workload thresholds are explicit diagnostic conventions, not fitted estimates.
Confirmed XI turnover describes changes, not their cause or player quality.
"""
from datetime import timedelta

from goal_analysis.features.schedule import build_schedule_features
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.identity import native_id
from goal_analysis.normalization.leakage import model_inputs
from goal_analysis.normalization.training_datasets import snapshot

VERSION = 'rotation-shadow-v1'
SHORT_GAP_HOURS = 72
BUSY_PREV7D = 3
LINEUP_MAX_AGE_HOURS = 2


def lineup_payload(raw, fixture, *, historical):
    """Normalize an exact provider fixture; no names, predicted XI or bench fill."""
    kinds = {'team_sheet', 'played', 'predicted'} if historical else {'team_sheet', 'predicted'}
    if raw.get('match_id') != fixture['fixture_id'] or raw.get('type') not in kinds:
        raise ValueError('LINEUP_FIXTURE_OR_TYPE_CONFLICT')
    result = {'fixture_id':fixture['fixture_id'], 'kickoff':fixture['kickoff'],
              'type':raw['type'], 'confirmed':raw.get('confirmed'), 'teams':{}}
    for side in ('home','away'):
        team = raw[side]
        expected = fixture[side+'_team_id']
        if team.get('id') != expected:
            raise ValueError('LINEUP_PARTICIPANT_CONFLICT')
        players = team.get('starting_xi')
        if not isinstance(players,list):
            raise TypeError('MISSING_STARTING_XI')
        ids = [native_id(p['id'],'pl') for p in players]
        if len(ids) != len(set(ids)) or len(ids) > 11:
            raise ValueError('INVALID_STARTING_XI')
        result['teams'][expected] = sorted(ids)
    a,b = result['teams'].values()
    if set(a) & set(b):
        raise ValueError('PLAYER_ON_BOTH_TEAMS')
    return result


def _select(envelopes, fixture, team, cutoff, *, historical):
    candidates = []
    for envelope, payload in zip(envelopes, model_inputs(envelopes,as_of=cutoff),strict=True):
        if payload.get('fixture_id') != fixture['fixture_id']:
            continue
        if (utc(payload['kickoff']) != utc(fixture['kickoff'])
                or set(payload['teams']) != {fixture['home_team_id'],fixture['away_team_id']}):
            raise ValueError('LINEUP_EXACT_LINK_CONFLICT')
        seen=set()
        for key,ids in payload['teams'].items():
            native_id(key,'tm')
            if not isinstance(ids,list) or len(ids)>11 or len(ids)!=len(set(ids)):
                raise ValueError('INVALID_STARTING_XI')
            for player in ids:
                native_id(player,'pl')
            if seen & set(ids):
                raise ValueError('PLAYER_ON_BOTH_TEAMS')
            seen.update(ids)
        observed = utc(envelope['times']['observed_at'])
        if payload.get('type')=='played' and observed<utc(fixture['kickoff']):
            raise ValueError('PLAYED_LINEUP_BEFORE_KICKOFF')
        if not historical and cutoff-observed > timedelta(hours=LINEUP_MAX_AGE_HOURS):
            continue
        candidates.append((observed,payload))
    if not candidates:
        return None,'MISSING_OR_STALE_LINEUP'
    newest = max(t for t,_ in candidates)
    rows = [p for t,p in candidates if t==newest]
    if any(p != rows[0] for p in rows):
        return None,'CONFLICTING_LINEUPS'
    value=rows[0]
    allowed={'team_sheet','played'} if historical else {'team_sheet'}
    if value['confirmed'] is not True or value['type'] not in allowed:
        return None,'UNCONFIRMED_OR_PREDICTED_LINEUP'
    ids=value['teams'][team]
    if len(ids)!=11 or len(set(ids))!=11:
        return None,'INCOMPLETE_STARTING_XI'
    for player in ids:
        native_id(player,'pl')
    return set(ids),'CONFIRMED_PROVIDER_XI'


def build_rotation_features(target, calendars, lineups, *, as_of):
    cutoff=utc(as_of)
    schedule=build_schedule_features(target,calendars,as_of=cutoff)
    # Fail at the evidence boundary even when no schedule is usable.
    model_inputs(lineups,as_of=cutoff)
    teams=[]
    for workload in schedule['teams']:
        team=workload['team_id'];previous=workload['previous_fixture']
        row={'team_id':team,'side':workload['side'],'status':'PARTIAL_SHADOW',
             'workload_status':workload['status'], 'workload_indicators':None,
             'current_lineup_status':'MISSING_OR_STALE_LINEUP',
             'baseline_lineup_status':'NO_CONFIRMED_PREVIOUS_MATCH',
             'baseline_fixture_id':None,'starting_xi_changes':None,'retained_starters':None,
             'rotation_probability':None,'rotation_cause':None,'team_strength_effect':None}
        current,current_status=_select(lineups,target,team,cutoff,historical=False)
        row['current_lineup_status']=current_status
        if workload['status']=='AVAILABLE_SHADOW':
            prev=workload['previous_kickoff_gap_hours'];nxt=workload['next_kickoff_gap_hours']
            row['workload_indicators']={
                'previous_kickoff_gap_hours':prev,'next_kickoff_gap_hours':nxt,
                'short_previous_known_gap':None if prev is None else prev<SHORT_GAP_HOURS,
                'short_next_known_gap':None if nxt is None else nxt<SHORT_GAP_HOURS,
                'busy_known_prev7d':workload['planned_or_confirmed_prev7d']>=BUSY_PREV7D,
                'planned_or_confirmed_prev7d':workload['planned_or_confirmed_prev7d'],
                'confirmed_played_prev7d_lower_bound':workload['confirmed_played_prev7d_lower_bound'],
                'unconfirmed_past_scheduled_count':workload['unconfirmed_past_scheduled_count'],
                'future_planned_before_target_count':workload['future_planned_before_target_count'],
                'previous_neighbor_status':previous['status'] if previous else None}
        if previous is not None and previous['status']=='finished':
            row['baseline_fixture_id']=previous['fixture_id']
            baseline,baseline_status=_select(lineups,previous,team,cutoff,historical=True)
            row['baseline_lineup_status']=baseline_status
            if current is not None and baseline is not None:
                row.update(starting_xi_changes=len(current-baseline),
                           retained_starters=len(current & baseline))
                row['status']='XI_COMPARISON_SHADOW'
        teams.append(row)
    return {'version':VERSION,'mode':'SHADOW','as_of':cutoff.isoformat(),
            'fixture_id':target['fixture_id'],'live_decision_effect':False,'teams':teams,
            'dataset':snapshot([target,*model_inputs(calendars,as_of=cutoff),
                               *model_inputs(lineups,as_of=cutoff)],
                role='rotation_features_shadow',as_of=cutoff,
                recipe={'version':VERSION,'short_gap_hours':SHORT_GAP_HOURS,
                        'busy_prev7d':BUSY_PREV7D,'lineup_max_age_hours':LINEUP_MAX_AGE_HOURS},
                lineage=[*calendars,*lineups])}
