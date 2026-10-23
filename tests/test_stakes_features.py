"""Synthetic fixed-format mathematics, not actual football outcome performance."""
from copy import deepcopy
from datetime import timedelta

import pytest
from test_schedule_features import KICK, NOW, envelope, match, rehash

from goal_analysis.features.stakes import build_stakes_features, normalize_table, raw_diagnostics
from goal_analysis.normalization.leakage import LookAheadError


def target():return {**match(),'phase':'LEAGUE_PHASE','table_group':None}


def rows():
    return [{'team':{'id':f'tm_{i}'},'group_label':None,'position':rank,'matches_played':4,
             'wins':w,'draws':d,'losses':4-w-d,'goals_for':7,'goals_against':5,'goal_difference':2,
             'points':3*w+d} for i,rank,w,d in [(1,1,3,1),(2,4,0,0),(3,2,2,1),(4,3,1,1)]]


def table():return envelope({'competition_id':'comp_1','season_id':'sn_1','raw_rows':rows()})


def rules():
    return envelope({'competition_id':'comp_1','season_id':'sn_1','phase':'LEAGUE_PHASE','group':None,
        'format':'FIXED_POINTS_TOP_K','scope':'ON_FIELD_POINTS_ONLY',
        'verification_basis':'REVIEWED_OFFICIAL_RULES_AND_COMPLETE_PARTICIPANTS',
        'valid_from':(KICK-timedelta(days=90)).isoformat(),'valid_to':(KICK+timedelta(days=90)).isoformat(),
        'points_per_win':3,'scheduled_games_by_team':{f'tm_{i}':5 for i in range(1,5)},
        'objectives':[{'kind':kind,'top_k':k,'unconditional':True}
                      for kind,k in [('TITLE',1),('PROMOTION',2),('EUROPE',2),('SURVIVAL',3)]]})


def test_complete_rules_generate_only_provable_conservative_bounds():
    r=build_stakes_features(target(),table(),rules=rules(),as_of=NOW);h,a=r['teams']
    assert h['games_remaining']==1 and h['maximum_on_field_points']==13
    assert h['objectives'][0]['status']=='UNRESOLVED_BOUNDS' # rival3 can tie10, no guessed tie-break
    assert h['objectives'][1]['status']=='GUARANTEED_ON_FIELD_TOP_K'
    assert a['best_rank_lower_bound']==a['worst_rank_upper_bound']==4
    assert all(o['status']=='ELIMINATED_FROM_ON_FIELD_TOP_K' for o in a['objectives'])
    assert h['mental_motivation'] is h['live_probability_effect'] is None and not r['live_decision_effect']


def test_strictly_higher_points_can_prove_title_without_tiebreak_assumption():
    t=table();t['payload']['raw_rows'][2].update(points=6,wins=2,draws=0,losses=2);rehash(t)
    h=build_stakes_features(target(),t,rules=rules(),as_of=NOW)['teams'][0]
    assert h['worst_rank_upper_bound']==1 and h['objectives'][0]['status']=='GUARANTEED_ON_FIELD_TOP_K'


def test_descriptive_provider_rank_is_not_remaining_games_or_proof_of_any_race():
    r=build_stakes_features(target(),table(),as_of=NOW)
    assert r['rules_status']=='MISSING_VERIFIED_RULES'
    for team in r['teams']:
        assert team['status']=='DESCRIPTIVE_ONLY' and team['games_remaining'] is None and team['objectives']==[]


@pytest.mark.parametrize('change',[{'season_id':'sn_9'},{'competition_id':'comp_9'},
    {'phase':'QUALIFIER'},{'group':'A'},{'scope':'MENTAL_STATE'},
    {'verification_basis':'CALLER_SAYS_VERIFIED'},{'format':'PLAYOFF'},
    {'valid_to':(KICK-timedelta(seconds=1)).isoformat()}])
def test_inapplicable_or_unverified_rules_never_create_stakes(change):
    r=rules();r['payload'].update(change);rehash(r)
    v=build_stakes_features(target(),table(),rules=r,as_of=NOW)
    assert v['rules_status']=='UNVERIFIED_OR_INAPPLICABLE_RULES' and v['teams'][0]['objectives']==[]


@pytest.mark.parametrize('fault',['missing_rival','played_exceeds','target_no_remaining','conditional_slot','invalid_title','no_objectives'])
def test_roster_schedule_and_objective_errors_do_not_reach_mathematics(fault):
    r=rules();p=r['payload']
    if fault=='missing_rival':del p['scheduled_games_by_team']['tm_4']
    if fault=='played_exceeds':p['scheduled_games_by_team']['tm_3']=3
    if fault=='target_no_remaining':p['scheduled_games_by_team']['tm_1']=4
    if fault=='conditional_slot':p['objectives'][2]['unconditional']=False
    if fault=='invalid_title':p['objectives'][0]['top_k']=2
    if fault=='no_objectives':p['objectives']=[]
    rehash(r);v=build_stakes_features(target(),table(),rules=r,as_of=NOW)
    assert v['rules_status']=='UNVERIFIED_OR_INAPPLICABLE_RULES'


def test_unknown_phase_or_missing_group_is_not_inferred_from_table():
    for changes in ({'phase':'UNKNOWN'},{'phase':None}):
        t=target();t.update(changes)
        assert build_stakes_features(t,table(),rules=rules(),as_of=NOW)['teams'][0]['objectives']==[]
    t=target();del t['table_group']
    assert build_stakes_features(t,table(),rules=rules(),as_of=NOW)['teams'][0]['objectives']==[]


def test_negative_current_deduction_is_retained_not_forced_to_three_times_wins():
    t=table();t['payload']['raw_rows'][1]['points']=-2;rehash(t)
    a=build_stakes_features(target(),t,rules=rules(),as_of=NOW)['teams'][1]
    assert a['maximum_on_field_points']==1


@pytest.mark.parametrize('fault',['duplicate','contradictory_duplicate','played','goals','bool','bad_id'])
def test_raw_duplicates_and_invalid_counts_are_not_cleaned_by_rank_or_played_preference(fault):
    r=rows()
    if fault in {'duplicate','contradictory_duplicate'}:
        r.append(deepcopy(r[0]))
        if fault=='contradictory_duplicate':r[-1].update(matches_played=0,wins=0,draws=0,losses=0,points=0)
        assert raw_diagnostics(r)['duplicate_team_groups']==1
    if fault=='played':r[0]['matches_played']=10
    if fault=='goals':r[0]['goal_difference']=99
    if fault=='bool':r[0]['points']=True
    if fault=='bad_id':r[0]['team']['id']='Ajax'
    with pytest.raises(ValueError):normalize_table(r)


def test_empty_missing_stale_future_tampered_and_wrong_scope_inputs_have_no_fallback():
    assert build_stakes_features(target(),None,as_of=NOW)['teams'][0]['status']=='UNKNOWN'
    t=table();t['payload']['raw_rows']=[];rehash(t)
    assert build_stakes_features(target(),t,as_of=NOW)['table_status']=='EMPTY_TABLE'
    t=table();t['times']['observed_at']=(NOW-timedelta(hours=7)).isoformat()
    assert build_stakes_features(target(),t,as_of=NOW)['table_status']=='STALE_TABLE'
    for kind in ('future','tampered'):
        t=table()
        if kind=='future':t['times']['available_at']=(NOW+timedelta(seconds=1)).isoformat()
        if kind=='tampered':t['payload']['season_id']='sn_9'
        with pytest.raises(LookAheadError):build_stakes_features(target(),t,as_of=NOW)
    t=table();t['payload']['season_id']='sn_9';rehash(t)
    with pytest.raises(ValueError,match='SCOPE'):build_stakes_features(target(),t,as_of=NOW)
    with pytest.raises(ValueError,match='LOCKOUT'):
        build_stakes_features(target(),table(),as_of=KICK-timedelta(minutes=14))


def test_other_groups_do_not_count_as_contenders_and_same_team_in_two_groups_is_ambiguous():
    t=table();extra=deepcopy(rows()[0]);extra['group_label']='B';extra['team']['id']='tm_99'
    t['payload']['raw_rows'].append(extra);rehash(t)
    assert build_stakes_features(target(),t,rules=rules(),as_of=NOW)['rules_status']=='VERIFIED_FIXED_TOP_K'
    extra['team']['id']='tm_1';t['payload']['raw_rows'][-1]=extra;rehash(t)
    assert build_stakes_features(target(),t,rules=rules(),as_of=NOW)['teams'][0]['status']=='UNKNOWN'


def test_proven_bounds_hold_for_every_joint_result_and_every_possible_points_tie_order():
    from itertools import product

    v=build_stakes_features(target(),table(),rules=rules(),as_of=NOW)
    for first,second in product(((3,0),(1,1),(0,3)),repeat=2):
        final={'tm_1':10+first[0],'tm_2':first[1],'tm_3':7+second[0],'tm_4':4+second[1]}
        for team in v['teams']:
            points=final[team['team_id']];rivals=[p for key,p in final.items() if key!=team['team_id']]
            actual_best=1+sum(p>points for p in rivals);actual_worst=1+sum(p>=points for p in rivals)
            assert team['best_rank_lower_bound']<=actual_best<=actual_worst<=team['worst_rank_upper_bound']
            for objective in team['objectives']:
                if objective['status']=='GUARANTEED_ON_FIELD_TOP_K':assert actual_worst<=objective['top_k']
                if objective['status']=='ELIMINATED_FROM_ON_FIELD_TOP_K':assert actual_best>objective['top_k']


def test_malformed_group_and_oversized_table_are_bounded_and_explicitly_unusable():
    r=rows();r[0]['group_label']={};assert raw_diagnostics(r)['raw_rows']==4
    with pytest.raises(ValueError,match='GROUP'):normalize_table(r)
    r=rows()*501
    assert raw_diagnostics(r)['diagnostics_truncated'] is True
    with pytest.raises(TypeError,match='BOUNDED'):normalize_table(r)
