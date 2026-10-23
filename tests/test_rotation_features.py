"""Synthetic rotation evidence boundaries; no football performance claims."""
from copy import deepcopy
from datetime import timedelta

import pytest
from test_schedule_features import NOW, calendars, envelope, match, rehash

from goal_analysis.features.rotation import build_rotation_features, lineup_payload
from goal_analysis.normalization.leakage import LookAheadError


def raw(f, *, kind='team_sheet', confirmed=True, shift=0):
    return {'match_id':f['fixture_id'],'type':kind,'confirmed':confirmed,
        'home':{'id':f['home_team_id'],'starting_xi':[{'id':f'pl_{i+shift}'} for i in range(1,12)]},
        'away':{'id':f['away_team_id'],'starting_xi':[{'id':f'pl_{i+shift}'} for i in range(101,112)]}}


def lineups():
    c=calendars();previous=c[0]['payload']['matches'][-1]
    return [envelope(lineup_payload(raw(match(),shift=3),match(),historical=False)),
            envelope(lineup_payload(raw(previous,kind='played'),previous,historical=True))]


def test_confirmed_xi_turnover_is_exact_and_does_not_infer_b_team_cause_or_probability():
    r=build_rotation_features(match(),calendars(),lineups(),as_of=NOW);h,a=r['teams']
    assert h['starting_xi_changes']==3 and h['retained_starters']==8
    assert h['status']=='XI_COMPARISON_SHADOW' and a['starting_xi_changes'] is None
    assert h['rotation_cause'] is h['team_strength_effect'] is h['rotation_probability'] is None
    assert not r['live_decision_effect'] and r['mode']=='SHADOW'


@pytest.mark.parametrize('kind,confirmed',[('predicted',False),('predicted',True),('team_sheet',False),('team_sheet',None)])
def test_predictions_and_unconfirmed_xis_never_count_as_actual_rotation(kind,confirmed):
    l=lineups();l[0]['payload'].update(type=kind,confirmed=confirmed);rehash(l[0])
    h=build_rotation_features(match(),calendars(),l,as_of=NOW)['teams'][0]
    assert h['starting_xi_changes'] is None and h['current_lineup_status']=='UNCONFIRMED_OR_PREDICTED_LINEUP'


@pytest.mark.parametrize('which',[0,1])
def test_missing_one_of_two_xis_stays_unknown(which):
    l=lineups();l.pop(which)
    assert build_rotation_features(match(),calendars(),l,as_of=NOW)['teams'][0]['starting_xi_changes'] is None


def test_short_gap_is_a_known_schedule_proxy_not_confirmed_play_or_empty_missing_week():
    r=build_rotation_features(match(),calendars()[:1],[],as_of=NOW);h,a=r['teams']
    assert h['workload_indicators']['short_previous_known_gap'] is True
    assert h['workload_indicators']['short_next_known_gap'] is False # exactly72h
    assert a['workload_indicators'] is None and a['workload_status']=='MISSING_CALENDAR'
    assert h['starting_xi_changes'] is None


def test_planned_previous_match_is_not_skipped_to_find_a_convenient_finished_baseline():
    c=calendars();c[0]['payload']['matches'].append(match(99,1,8,-.1));rehash(c[0])
    h=build_rotation_features(match(),c,lineups(),as_of=NOW)['teams'][0]
    assert h['baseline_fixture_id'] is None and h['starting_xi_changes'] is None
    assert h['workload_indicators']['future_planned_before_target_count']==1


def test_three_known_prior_matches_trigger_diagnostic_busy_flag_not_live_score():
    c=calendars();c[0]['payload']['matches'] += [match(88,1,8,-3),match(89,1,9,-4)]
    rehash(c[0]);h=build_rotation_features(match(),c,[],as_of=NOW)['teams'][0]
    assert h['workload_indicators']['busy_known_prev7d'] is True
    assert h['workload_indicators']['confirmed_played_prev7d_lower_bound']==1


def test_stale_current_xi_is_unknown_and_no_neighbor_is_censored_not_zero_risk():
    l=lineups();l[0]['times']['observed_at']=(NOW-timedelta(hours=3)).isoformat()
    h=build_rotation_features(match(),calendars(),l,as_of=NOW)['teams'][0]
    assert h['current_lineup_status']=='MISSING_OR_STALE_LINEUP' and h['starting_xi_changes'] is None
    c=calendars();c[0]['payload']['matches']=[match()];rehash(c[0])
    h=build_rotation_features(match(),c,[],as_of=NOW)['teams'][0]
    assert h['workload_indicators']['short_previous_known_gap'] is None


@pytest.mark.parametrize('change',['future','hash','missing_receipt'])
def test_unavailable_or_tampered_evidence_is_never_usable(change):
    l=lineups()
    if change=='future':l[0]['times']['available_at']=(NOW+timedelta(seconds=1)).isoformat()
    if change=='hash':l[0]['payload']['confirmed']=False
    if change=='missing_receipt':l[0]['times']['available_at']=None
    with pytest.raises(LookAheadError):build_rotation_features(match(),calendars(),l,as_of=NOW)


@pytest.mark.parametrize('kind',['wrong_team','duplicate','same_both','too_many','invalid_player','wrong_match'])
def test_raw_identity_duplicate_and_impossible_xis_are_rejected(kind):
    f=match();r=raw(f)
    if kind=='wrong_team':r['home']['id']='tm_99'
    if kind=='duplicate':r['home']['starting_xi'][1]=r['home']['starting_xi'][0]
    if kind=='same_both':r['away']['starting_xi'][1]=r['home']['starting_xi'][0]
    if kind=='too_many':r['home']['starting_xi'].append({'id':'pl_999'})
    if kind=='invalid_player':r['home']['starting_xi'][0]['id']='John'
    if kind=='wrong_match':r['match_id']='mt_99'
    with pytest.raises(ValueError):lineup_payload(r,f,historical=False)


def test_incomplete_confirmed_xi_and_same_time_conflicts_are_explicit_gaps():
    l=lineups();l[0]['payload']['teams']['tm_1'].pop();rehash(l[0])
    assert build_rotation_features(match(),calendars(),l,as_of=NOW)['teams'][0]['current_lineup_status']=='INCOMPLETE_STARTING_XI'
    l=lineups();other=deepcopy(l[0]);other['record_id']='other';other['payload']['confirmed']=False;rehash(other)
    assert build_rotation_features(match(),calendars(),[*l,other],as_of=NOW)['teams'][0]['current_lineup_status']=='CONFLICTING_LINEUPS'


def test_future_played_lineup_and_prematch_lockout_are_rejected():
    l=lineups();l[0]['payload']['type']='played';rehash(l[0])
    with pytest.raises(ValueError,match='BEFORE_KICKOFF'):build_rotation_features(match(),calendars(),l,as_of=NOW)
    with pytest.raises(ValueError,match='LOCKOUT'):
        build_rotation_features(match(),calendars(),[],as_of=NOW+timedelta(hours=11,minutes=46))
