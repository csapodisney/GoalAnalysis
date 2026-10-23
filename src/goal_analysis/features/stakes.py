"""Conservative on-field top-K bounds; no inference about mental motivation.

Full season rules/participants/remaining games are explicit evidence inputs.
Independent rival ceilings overestimate feasible outcomes: inconclusive bounds
must NEVER be described as a proven feasible title/promotion/qualification race.
"""
from datetime import timedelta

from goal_analysis.features.schedule import _match
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.identity import native_id
from goal_analysis.normalization.leakage import model_inputs
from goal_analysis.normalization.training_datasets import snapshot

VERSION='standings-stakes-shadow-v1'
TABLE_MAX_AGE_HOURS=6
OBJECTIVES={'TITLE','PROMOTION','EUROPE','SURVIVAL','ROUND_OF_16','KNOCKOUT_PHASE',
            'DIRECT_PROMOTION','DIRECT_SURVIVAL'}


def integer(value, minimum=0):
    if type(value) is not int or not minimum<=value<=1000000:
        raise ValueError('INVALID_STANDINGS_INTEGER')
    return value


def normalize_table(raw):
    if not isinstance(raw,list) or len(raw)>2000:
        raise TypeError('BOUNDED_STANDINGS_LIST_REQUIRED')
    rows=[];seen=set()
    for item in raw:
        team=native_id(item['team']['id'],'tm');group=item.get('group_label')
        if group is not None and (not isinstance(group,str) or not group or len(group)>100):
            raise ValueError('INVALID_STANDINGS_GROUP')
        if (team,group) in seen:
            raise ValueError('AMBIGUOUS_STANDINGS_TEAM_WITHIN_GROUP')
        seen.add((team,group))
        row={'team_id':team,'group':group,'rank':integer(item['position'],1),
             'played':integer(item['matches_played']),'points':integer(item['points'],-1000)}
        for name in ('wins','draws','losses','goals_for','goals_against'):
            row[name]=integer(item[name])
        row['goal_difference']=integer(item['goal_difference'],-10000)
        if row['played']!=sum(row[k] for k in ('wins','draws','losses')):
            raise ValueError('INCONSISTENT_PLAYED_MATCHES')
        if row['goal_difference']!=row['goals_for']-row['goals_against']:
            raise ValueError('INCONSISTENT_GOAL_DIFFERENCE')
        rows.append(row)
    return rows


def raw_diagnostics(raw):
    """Preserve ambiguity counts without silently selecting maximum played/rank."""
    if not isinstance(raw,list):
        return {'raw_rows':None,'duplicate_team_groups':None,'conflicting_team_groups':None}
    if len(raw)>2000:
        return {'raw_rows':len(raw),'duplicate_team_groups':None,'conflicting_team_groups':None,
                'diagnostics_truncated':True}
    groups={}
    for item in raw:
        if isinstance(item,dict) and isinstance(item.get('team'),dict):
            team,group=item['team'].get('id'),item.get('group_label')
            if isinstance(team,str) and (group is None or isinstance(group,str)):
                groups.setdefault((team,group),[]).append(item)
    return {'raw_rows':len(raw),'distinct_team_groups':len(groups),
            'duplicate_team_groups':sum(len(v)>1 for v in groups.values()),
            'conflicting_team_groups':sum(any(x!=v[0] for x in v) for v in groups.values())}


def _verified_rules(payload, target, rows):
    if (payload.get('verification_basis')!='REVIEWED_OFFICIAL_RULES_AND_COMPLETE_PARTICIPANTS'
            or payload.get('format')!='FIXED_POINTS_TOP_K'
            or payload.get('scope')!='ON_FIELD_POINTS_ONLY'
            or payload.get('phase') in {None,'UNKNOWN'}
            or payload.get('phase')!=target.get('phase')
            or 'table_group' not in target or payload.get('group')!=target['table_group']
            or payload.get('competition_id')!=target['competition_id']
            or payload.get('season_id')!=target['season_id']):
        raise ValueError('UNVERIFIED_OR_MISMATCHED_COMPETITION_RULES')
    kickoff=utc(target['kickoff'])
    if not utc(payload['valid_from'])<=kickoff<utc(payload['valid_to']):
        raise ValueError('RULES_NOT_VALID_FOR_FIXTURE')
    # No assumed 38-game/3-point league, inferred slots, playoff/cup exceptions,
    # arbitrary "remaining" scalar or caller-supplied challenger maximum.
    points=integer(payload['points_per_win'],1)
    totals=payload['scheduled_games_by_team']
    if not isinstance(totals,dict) or set(totals)!={r['team_id'] for r in rows}:
        raise ValueError('INCOMPLETE_CONTENDER_ROSTER')
    for team,total in totals.items():
        native_id(team,'tm');integer(total)
    if any(totals[r['team_id']]<r['played'] for r in rows):
        raise ValueError('PLAYED_EXCEEDS_VERIFIED_SCHEDULE')
    for side in ('home','away'):
        r=next((r for r in rows if r['team_id']==target[side+'_team_id']),None)
        if r is None or totals[r['team_id']]-r['played']<1:
            raise ValueError('TARGET_NOT_IN_REMAINING_PHASE_SCHEDULE')
    objectives=payload['objectives']
    if not isinstance(objectives,list) or not objectives:
        raise TypeError('RULE_OBJECTIVES_REQUIRED')
    kinds=set()
    for objective in objectives:
        kind=objective['kind'];top=integer(objective['top_k'],1)
        if (kind not in OBJECTIVES or kind in kinds or objective.get('unconditional') is not True
                or top>len(rows) or (kind=='TITLE' and top!=1)):
            raise ValueError('CONDITIONAL_OR_INVALID_TOP_K_OBJECTIVE')
        kinds.add(kind)
    return points,totals,objectives


def build_stakes_features(target, table, *, as_of, rules=None):
    cutoff=utc(as_of);_match(target)
    if cutoff>utc(target['kickoff'])-timedelta(minutes=15):
        raise ValueError('PREMATCH_LOCKOUT')
    envelopes=[] if table is None else [table]
    if rules is not None:envelopes.append(rules)
    payloads=model_inputs(envelopes,as_of=cutoff)
    rows=[];table_status='MISSING_WITNESSED_TABLE';rule_status='MISSING_VERIFIED_RULES'
    selected=[];valid=None
    if table is not None:
        p=payloads[0]
        if p['competition_id']!=target['competition_id'] or p['season_id']!=target['season_id']:
            raise ValueError('TABLE_SCOPE_MISMATCH')
        if cutoff-utc(table['times']['observed_at'])>timedelta(hours=TABLE_MAX_AGE_HOURS):
            table_status='STALE_TABLE'
        else:
            rows=normalize_table(p['raw_rows'])
            table_status='EMPTY_TABLE' if not rows else 'DESCRIPTIVE_TABLE_ONLY'
            if 'table_group' in target:
                selected=[r for r in rows if r['group']==target['table_group']]
            if rules is not None:
                try:
                    valid=_verified_rules(payloads[-1],target,selected)
                    rule_status='VERIFIED_FIXED_TOP_K'
                except (ValueError,KeyError,TypeError):
                    rule_status='UNVERIFIED_OR_INAPPLICABLE_RULES'
    teams=[]
    for side in ('home','away'):
        team=target[side+'_team_id'];matches=[r for r in rows if r['team_id']==team]
        row={'team_id':team,'side':side,'status':'UNKNOWN','table_row':None,'games_remaining':None,
             'maximum_on_field_points':None,'best_rank_lower_bound':None,'worst_rank_upper_bound':None,
             'objectives':[],'mental_motivation':None,'live_probability_effect':None}
        if len(matches)==1:
            row.update(status='DESCRIPTIVE_ONLY',table_row=matches[0])
        if valid is not None and len(matches)==1 and matches[0] in selected:
            per_win,totals,objectives=valid;r=matches[0]
            remaining=totals[team]-r['played'];ceiling=r['points']+remaining*per_win
            rivals=[other for other in selected if other['team_id']!=team]
            best=1+sum(other['points']>ceiling for other in rivals)
            worst=1+sum(other['points']+(totals[other['team_id']]-other['played'])*per_win>=r['points'] for other in rivals)
            row.update(status='CONSERVATIVE_POINTS_BOUNDS_SHADOW',games_remaining=remaining,
                       maximum_on_field_points=ceiling,best_rank_lower_bound=best,worst_rank_upper_bound=worst)
            row['objectives']=[{'kind':o['kind'],'top_k':o['top_k'],
                'status':'GUARANTEED_ON_FIELD_TOP_K' if worst<=o['top_k'] else
                         'ELIMINATED_FROM_ON_FIELD_TOP_K' if best>o['top_k'] else 'UNRESOLVED_BOUNDS',
                'ties':'NOT_RESOLVED','joint_fixture_feasibility':'NOT_SOLVED'} for o in objectives]
        teams.append(row)
    return {'version':VERSION,'mode':'SHADOW','fixture_id':target['fixture_id'],'as_of':cutoff.isoformat(),
            'table_status':table_status,'rules_status':rule_status,'teams':teams,'live_decision_effect':False,
            'dataset':snapshot([target,*payloads],role='standings_stakes_features_shadow',as_of=cutoff,
                recipe={'version':VERSION,'table_max_age_hours':TABLE_MAX_AGE_HOURS},lineage=envelopes)}
