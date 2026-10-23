"""Actual audit semantics, separate from selection, forecasts and probabilities."""
from copy import deepcopy

import pytest
from test_decision_trace import sample

from goal_analysis.decision_trace import build_decision_data_usage


def test_unknown_weather_shell_is_not_a_forecast_or_historical_team_review():
    report={'expert_advisory':{'weather':{'fixtures':[
        {'fixture_id':'f','status':'INSUFFICIENT_DATA','forecast':{},'teams':{'home':{},'away':{}}}]}}}
    result=build_decision_data_usage(report,{})
    assert result['sources']['open_meteo_weather']['status']=='MISSING'
    assert result['sources']['open_meteo_weather']['observations']==0
    assert result['sources']['team_specific_weather_history']['observations']==0
    assert result['coverage']['weather_rows_processed']==1


@pytest.mark.parametrize('change',[
    {'status':'UNKNOWN'}, {'weather_kind':'REANALYSIS'}, {'temperature_c':None},
    {'temperature_c':True}, {'wind_speed_kmh':-1}, {'observed_at':'2026-10-22T13:00:00+00:00'},
    {'observed_at':'2026-10-22T01:00:00+00:00'}, {'target_time':'2026-10-23T15:00:00+00:00'},
    {'observed_at':'2026-10-22T11:00:00'},
])
def test_unknown_invalid_stale_future_or_unaware_weather_never_increases_usage(change):
    report,artifacts,_=sample()
    report['expert_advisory']['weather']['fixtures'][0]['weather'].update(change)
    result=build_decision_data_usage(report,artifacts)
    assert result['sources']['open_meteo_weather']['status']=='MISSING'
    assert result['sources']['team_specific_weather_history']['status']=='MISSING'


def test_valid_weather_review_survives_unvalidated_or_missing_historical_effect():
    report,artifacts,_=sample();row=report['expert_advisory']['weather']['fixtures'][0]
    row.update(status='INSUFFICIENT_DATA',live_weight_eligible=False,teams={'home':{},'away':{}})
    report['expert_ranking']['weather_weighted_options']=0
    result=build_decision_data_usage(report,artifacts)
    assert result['sources']['open_meteo_weather']['status']=='USED_REVIEW'
    assert result['sources']['open_meteo_weather']['observations']==1
    assert result['sources']['team_specific_weather_history']['status']=='MISSING'


def test_eligible_weather_without_application_counter_is_review_only():
    report,artifacts,_=sample();report['expert_ranking']['weather_weighted_options']=0
    result=build_decision_data_usage(report,artifacts)
    assert result['sources']['open_meteo_weather']['status']=='USED_REVIEW'
    assert result['sources']['team_specific_weather_history']['status']=='USED_REVIEW'


def test_raw_forecast_without_expert_accepted_weather_is_not_confirmed_review():
    report,artifacts,_=sample();row=report['expert_advisory']['weather']['fixtures'][0]
    row['forecast']=row.pop('weather')
    assert build_decision_data_usage(report,artifacts)['sources']['open_meteo_weather']['status']=='MISSING'


def test_duplicate_weather_is_counted_once_and_conflicting_duplicates_are_quarantined():
    report,artifacts,_=sample();rows=report['expert_advisory']['weather']['fixtures']
    rows.append(deepcopy(rows[0]))
    assert build_decision_data_usage(report,artifacts)['sources']['open_meteo_weather']['observations']==1
    rows[1]['weather']['temperature_c']=13
    rows.append(deepcopy(rows[0]))
    assert build_decision_data_usage(report,artifacts)['sources']['open_meteo_weather']['status']=='MISSING'


@pytest.mark.parametrize('field',['team_season_stats','player_season_stats','injuries_suspensions','lineup','match_odds'])
def test_available_fact_alone_is_storage_not_review_or_application(field):
    report={'sports_supplement':{'the_stats_api':{'context_by_fixture':{
        'f':{'facts':[{'category':field,'status':'AVAILABLE'}]}}}}}
    result=build_decision_data_usage(report,{})
    key={'team_season_stats':'the_stats_team_xg','player_season_stats':'the_stats_player_stats',
         'injuries_suspensions':'the_stats_availability','lineup':'the_stats_availability',
         'match_odds':'the_stats_current_odds'}[field]
    assert result['sources'][key]['status']=='STORED_ONLY'
    assert result['coverage']['available_stats_facts'][field]==1


@pytest.mark.parametrize('counter',[0,-1,True,False,None,1.5])
def test_only_positive_integer_application_counters_prove_stats_ranking(counter):
    report,artifacts,_=sample()
    report['expert_ranking']['the_stats_api']['evidence_attached_options']=counter
    assert build_decision_data_usage(report,artifacts)['sources']['the_stats_team_xg']['status']=='STORED_ONLY'


def test_supplied_history_and_a_prediction_do_not_prove_recorded_training_inputs():
    report,artifacts,_=sample()
    result=build_decision_data_usage(report,artifacts)
    assert result['sources']['api_football_match_history']['status']=='STORED_ONLY'
    assert result['sources']['numeric_model_history']['status']=='STORED_ONLY'
    assert result['sources']['numeric_models']['status']=='USED_DIRECT'


def test_recorded_training_dataset_consumption_is_distinct_from_selected_daily_form():
    report,artifacts,_=sample()
    manifest={'dataset_id':'a'*64,'rows_sha256':'b'*64,'lineage_sha256':'c'*64,'row_count':80}
    artifacts['numeric_models']['gauss']['training_datasets']={'manifests':[manifest,manifest]}
    result=build_decision_data_usage(report,artifacts)
    assert result['sources']['api_football_match_history']['status']=='USED_DIRECT'
    assert result['sources']['numeric_model_history']['observations']==1
    assert result['sources']['daily_historical_form']['status']=='MISSING'


@pytest.mark.parametrize('change',[{'row_count':0},{'row_count':True},{'dataset_id':'not-a-digest'},
                                  {'lineage_sha256':None}])
def test_incomplete_training_manifest_is_not_consumption_proof(change):
    report,artifacts,_=sample()
    manifest={'dataset_id':'a'*64,'rows_sha256':'b'*64,'lineage_sha256':'c'*64,'row_count':80,**change}
    artifacts['numeric_models']['gauss']['training_datasets']={'manifests':[manifest]}
    assert build_decision_data_usage(report,artifacts)['sources']['numeric_model_history']['status']=='STORED_ONLY'


def test_daily_selected_history_evidence_is_bound_to_input_and_not_counted_per_view():
    report,artifacts,_=sample();artifacts['history']['snapshot_sha256']='d'*64
    card={'profile_id':'daily223','legs':[{'fixture_id':'f','evidence':[{
        'category':'historical','evidence_id':'history-v1:c:historical','source_id':'history-sha256:'+'d'*64}]}]}
    report.update(tickets=[deepcopy(card)],recommendations=[deepcopy(card)],profile_proposals=[deepcopy(card)])
    report['tickets'][0]['legs'][0]['evidence'][0]['fresh']=True
    before=deepcopy((report,artifacts))
    result=build_decision_data_usage(report,artifacts)
    assert result['sources']['daily_historical_form']['observations']==1
    assert result['sources']['api_football_match_history']['status']=='USED_DIRECT'
    assert result['sources']['numeric_model_history']['status']=='STORED_ONLY'
    assert (report,artifacts)==before
    artifacts['history']['snapshot_sha256']='e'*64
    assert build_decision_data_usage(report,artifacts)['sources']['daily_historical_form']['status']=='MISSING'
