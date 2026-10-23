"""Synthetic calendars: UTC plans, confirmed lower bounds, censoring and travel proxies."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.features.schedule import build_schedule_features
from goal_analysis.normalization.leakage import LookAheadError
from goal_analysis.normalization.training_datasets import sha

NOW=datetime(2026,10,22,8,tzinfo=UTC)
KICK=NOW+timedelta(hours=12)


def envelope(payload):
    observed=NOW-timedelta(hours=2);available=NOW-timedelta(hours=1)
    return {'record_id':sha(payload),'payload':payload,'payload_sha256':sha(payload),
            'source_sha256':'a'*64,'receipt_id':'b'*64,
            'times':{'observed_at':observed.isoformat(),'available_at':available.isoformat(),
                     'published_at':None,'ingested_at':None,'availability_basis':'SOURCE_PRESENCE_UPPER_BOUND'}}


def match(n=100,home=1,away=2,days=0,status='scheduled',**extra):
    return {'fixture_id':f'mt_{n}','competition_id':'comp_1','season_id':'sn_1',
            'home_team_id':f'tm_{home}','away_team_id':f'tm_{away}',
            'kickoff':(KICK+timedelta(days=days)).isoformat(),'status':status,'venue_id':'v2',**extra}


def calendars():
    out=[]
    for team,other,side in ((1,3,'home'),(2,4,'away')):
        before=match(team,team,other,-team,'finished',competition_id='comp_2',venue_id='v1')
        following=match(team+10,team,other,3)
        payload={'team_id':f'tm_{team}','scope':'ALL_COMPETITIONS_ALL_STATUSES','pagination_complete':True,
                 'window_start':(KICK-timedelta(days=14)).isoformat(),
                 'window_end_exclusive':(KICK+timedelta(days=15)).isoformat(),
                 'observed_at':(NOW-timedelta(hours=2)).isoformat(),
                 'available_at':(NOW-timedelta(hours=1)).isoformat(),'matches':[following,match(),before]}
        out.append(envelope(payload))
    return out


def rehash(e):
    e['payload_sha256']=sha(e['payload']);return e


def test_full_cross_competition_schedule_uses_cup_games_and_keeps_rest_itinerary_unknown():
    r=build_schedule_features(match(),calendars(),as_of=NOW)
    h,a=r['teams'];assert h['previous_kickoff_gap_hours']==24 and a['previous_kickoff_gap_hours']==48
    assert h['previous_fixture']['competition_id']=='comp_2'
    assert h['next_kickoff_gap_hours']==72
    assert h['confirmed_played_prev7d_lower_bound']==h['planned_or_confirmed_prev14d']==1
    assert h['actual_rest_hours'] is h['actual_travel_km'] is None
    assert r['mode']=='SHADOW' and not r['live_decision_effect']


def test_missing_calendar_is_not_an_empty_free_week_and_valid_team_is_preserved():
    r=build_schedule_features(match(),calendars()[:1],as_of=NOW)
    assert r['teams'][0]['status']=='AVAILABLE_SHADOW'
    assert r['teams'][1]['status']=='MISSING_CALENDAR'
    assert r['teams'][1]['planned_or_confirmed_prev7d'] is None


@pytest.mark.parametrize('change', [{'scope':'LEAGUE_ONLY'},{'pagination_complete':False},
    {'window_start':(KICK-timedelta(days=3)).isoformat()},
    {'window_end_exclusive':(KICK+timedelta(days=4)).isoformat()}])
def test_partial_filtered_or_short_calendars_never_claim_complete_rest(change):
    c=calendars();c[0]['payload'].update(change);rehash(c[0])
    r=build_schedule_features(match(),c,as_of=NOW)['teams'][0]
    assert r['status']!='AVAILABLE_SHADOW' and r['previous_kickoff_gap_hours'] is None


def test_verified_no_neighbor_in_window_is_censored_not_infinite_rest():
    c=calendars();c[0]['payload']['matches']=[match()];rehash(c[0])
    h=build_schedule_features(match(),c,as_of=NOW)['teams'][0]
    assert h['planned_or_confirmed_prev14d']==0 and h['previous_kickoff_gap_hours'] is None
    assert h['previous_window_left_censored'] is h['next_window_right_censored'] is True


def test_postponed_cancelled_games_are_excluded_and_plans_are_not_confirmed_play():
    c=calendars();p=c[0]['payload']
    p['matches'] += [match(50,1,5,-.1),match(51,1,5,-.2,'cancelled'),match(52,1,5,-.3,'postponed')]
    rehash(c[0]);h=build_schedule_features(match(),c,as_of=NOW)['teams'][0]
    assert h['previous_fixture']['fixture_id']=='mt_50' and h['previous_fixture']['status']=='scheduled'
    assert h['planned_or_confirmed_prev7d']==2 and h['confirmed_played_prev7d_lower_bound']==1
    assert h['actual_rest_hours'] is None


@pytest.mark.parametrize('kind', ['duplicate','target','team','future_played','same_time','unknown_status'])
def test_calendar_conflicts_are_flagged_without_fallback(kind):
    c=calendars();p=c[0]['payload'];row=p['matches'][-1]
    if kind=='duplicate':p['matches'].append(deepcopy(row))
    if kind=='target':p['matches']=[r for r in p['matches'] if r['fixture_id']!='mt_100']
    if kind=='team':row['home_team_id']='tm_99'
    if kind=='future_played':p['matches'][0]['status']='finished'
    if kind=='same_time':p['matches'].append(match(60,1,5,-1,'finished'))
    if kind=='unknown_status':row['status']='unknown'
    rehash(c[0]);h=build_schedule_features(match(),c,as_of=NOW)['teams'][0]
    assert h['status']!='AVAILABLE_SHADOW' and h['planned_or_confirmed_prev14d'] is None


def test_utc_offset_and_elapsed_hours_are_used_instead_of_calendar_day_subtraction():
    c=calendars();c[0]['payload']['matches'][-1]['kickoff']=(KICK-timedelta(hours=23)).astimezone(
        __import__('datetime').timezone(timedelta(hours=2))).isoformat();rehash(c[0])
    assert build_schedule_features(match(),c,as_of=NOW)['teams'][0]['previous_kickoff_gap_hours']==23


def test_future_missing_receipt_hash_tamper_and_stale_calendar_are_not_valid_data():
    for change in ('future','missing','tamper'):
        c=calendars()
        if change=='future':c[0]['times']['available_at']=(NOW+timedelta(minutes=1)).isoformat()
        if change=='missing':c[0]['times']['available_at']=None
        if change=='tamper':c[0]['payload']['team_id']='tm_9'
        with pytest.raises(LookAheadError):build_schedule_features(match(),c,as_of=NOW)
    c=calendars();old=(NOW-timedelta(hours=7)).isoformat()
    c[0]['times']['observed_at']=c[0]['payload']['observed_at']=old;rehash(c[0])
    assert build_schedule_features(match(),c,as_of=NOW)['teams'][0]['status']=='STALE_CALENDAR'


def test_verified_stadium_separation_is_a_distance_proxy_and_never_actual_team_travel():
    venues=[envelope({'provider':'the_stats_api','venue_id':key,'latitude':0,'longitude':lon,
                     'coordinate_scope':'VERIFIED_STADIUM'}) for key,lon in (('v1',0),('v2',1))]
    h=build_schedule_features(match(),calendars(),as_of=NOW,venues=venues)['teams'][0]
    assert h['previous_to_target_venue_great_circle_km']==pytest.approx(111.19508,rel=1e-6)
    assert h['actual_travel_km'] is None
    venues[0]['payload']['coordinate_scope']='CITY_CENTROID';rehash(venues[0])
    with pytest.raises(ValueError,match='STADIUM_COORDINATES'):
        build_schedule_features(match(),calendars(),as_of=NOW,venues=venues)


def test_unlinked_target_venue_does_not_borrow_coordinates_even_when_caller_supplies_them():
    venues=[envelope({'provider':'the_stats_api','venue_id':key,'latitude':0,'longitude':lon,
                     'coordinate_scope':'VERIFIED_STADIUM'}) for key,lon in (('v1',0),('other',1))]
    h=build_schedule_features(match(venue_id='other'),calendars(),as_of=NOW,venues=venues)['teams'][0]
    assert h['previous_to_target_venue_great_circle_km'] is None


def test_duplicate_team_calendar_and_lockout_are_hard_boundary_errors():
    c=calendars();c.append(deepcopy(c[0]));c[-1]['record_id']='other'
    with pytest.raises(ValueError,match='AMBIGUOUS_TEAM_CALENDAR'):build_schedule_features(match(),c,as_of=NOW)
    with pytest.raises(ValueError,match='PREMATCH_LOCKOUT'):
        build_schedule_features(match(),calendars(),as_of=KICK-timedelta(minutes=14))


def test_inputs_are_detached_and_repeated_fit_is_deterministic():
    c=calendars();before=deepcopy(c)
    r=build_schedule_features(match(),c,as_of=NOW)
    assert r==build_schedule_features(match(),c,as_of=NOW) and c==before
