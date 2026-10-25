"""Original geographic bytes; controlled test-only receipt clocks and matches.

Private pins apply only to temporary test packets. They are not claims that
the original web documents were available at these synthetic earlier cutoffs.
"""
import json
import socket
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_native_venue_shadow import NOW, environment
from test_schedule_features import NOW as SCHEDULE_NOW
from test_schedule_features import calendars, envelope, match

from goal_analysis.agents import canonical_sha256
from goal_analysis.features.schedule import build_schedule_features
from goal_analysis.jobs import stadium_locations_shadow as geo
from goal_analysis.jobs.native_venue_shadow import review_venue_options
from goal_analysis.jobs.native_weather_shadow import inspect_native_weather
from goal_analysis.jobs.schedule_shadow import target_fixture
from goal_analysis.jobs.sports_context_archive import make_weather_provider
from goal_analysis.normalization.training_datasets import sha, validate
from goal_analysis.providers.open_meteo import OpenMeteoForecast
from goal_analysis.storage import SnapshotStore
from goal_analysis.storage.temporal import TemporalStore

FIXTURES=Path(__file__).parent/'fixtures/stadium-locations-20261024-sixth'
ORIGINAL=json.loads((FIXTURES/'reviewed-locations.json').read_bytes())


def seal(root, packet, monkeypatch):
    raw=json.dumps(packet,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    digest=sha256(raw).hexdigest();folder=root/'data/arthur/stadium-locations';folder.mkdir(parents=True,exist_ok=True)
    (folder/(digest+'.json')).write_bytes(raw)
    monkeypatch.setattr(geo,'REVIEWED_BUNDLE',digest)
    monkeypatch.setattr(geo,'REVIEWED_AT',packet['available_at'])
    return folder/(digest+'.json')


def sources(root, monkeypatch, *, now=NOW):
    folder=root/'data/arthur/stadium-locations';folder.mkdir(parents=True,exist_ok=True)
    packet=deepcopy(ORIGINAL)
    packet['reviewed_at']=packet['available_at']=(now-timedelta(minutes=1)).isoformat()
    for record in packet['records']:
        for name in ('wikidata_source','identity_source'):
            source=record[name];filename=source['sha256']+'.bin'
            (folder/filename).write_bytes((FIXTURES/filename).read_bytes())
            source['observed_at']=(now-timedelta(minutes=2)).isoformat()
    seal(root,packet,monkeypatch)
    return packet


def prepared(root,monkeypatch,**kwargs):
    data,models,db,receipts=environment(root,change=lambda m:m['venue'].update(name='Stade Francis-Le Ble',city='Brest'),**kwargs)
    return data,models,db,receipts,sources(root,monkeypatch)


def test_original_packet_pin_and_all_original_coordinate_sources_are_reproducible():
    assert sha256((FIXTURES/'reviewed-locations.json').read_bytes()).hexdigest()==geo.REVIEWED_BUNDLE
    for record in ORIGINAL['records']:
        for key in ('wikidata_source','identity_source'):
            digest=record[key]['sha256']
            assert sha256((FIXTURES/(digest+'.bin')).read_bytes()).hexdigest()==digest


def test_reviewed_free_wgs84_entities_keep_provenance_precision_and_real_external_ids(tmp_path,monkeypatch):
    sources(tmp_path,monkeypatch)
    monkeypatch.setattr(socket,'socket',Mock(side_effect=AssertionError('NO_NETWORK')))
    result=geo.read_locations(tmp_path,as_of=NOW)
    assert len(result)==69
    brest=result[('Stade Francis-Le Ble','Brest')]
    assert brest['payload']['venue_id']=='wikidata:Q1432517'
    assert brest['payload']['latitude']==48.40293 and brest['payload']['longitude']==-4.46148
    assert brest['payload']['native_venue_id'] is None and brest['payload']['license']=='CC0-1.0'
    assert all(len(e['source_receipts'])==2 for e in result.values())
    assert 'TOMBLAINE' in result[('Stade Marcel Picot','Nancy')]['payload']['alias_note']
    assert all(e['payload_sha256']==sha(e['payload']) for e in result.values())


@pytest.mark.parametrize('fault',['bundle_bytes','geographic_bytes','official_bytes','wrong_id','revision',
    'coordinate_claim','wrong_datum','precision','future_source','future_review','duplicate_alias','expired'])
def test_no_changed_ambiguous_future_or_expired_coordinate_claim_is_accepted(tmp_path,monkeypatch,fault):
    p=sources(tmp_path,monkeypatch);r=p['records'][0];folder=tmp_path/'data/arthur/stadium-locations'
    cutoff=NOW
    if fault=='bundle_bytes':
        f=folder/(geo.REVIEWED_BUNDLE+'.json');f.write_bytes(f.read_bytes()+b' ')
    elif fault in {'geographic_bytes','official_bytes'}:
        source=r['wikidata_source' if fault=='geographic_bytes' else 'identity_source']
        f=folder/(source['sha256']+'.bin');f.write_bytes(f.read_bytes()+b' ')
    elif fault=='wrong_id':r['wikidata_id']='Q9999999'
    elif fault=='revision':r['lastrevid']+=1
    elif fault=='coordinate_claim':r['coordinate_statement']['latitude']=0
    elif fault=='wrong_datum':r['coordinate_statement']['globe']='http://www.wikidata.org/entity/Q111'
    elif fault=='precision':r['coordinate_statement']['precision']=1
    elif fault=='future_source':r['identity_source']['observed_at']=(NOW+timedelta(seconds=1)).isoformat()
    elif fault=='future_review':p['reviewed_at']=p['available_at']=(NOW+timedelta(seconds=1)).isoformat()
    elif fault=='duplicate_alias':p['records'][1]['native_name']=r['native_name'];p['records'][1]['native_city']=r['native_city']
    elif fault=='expired':cutoff=NOW+timedelta(days=91)
    if fault not in {'bundle_bytes','geographic_bytes','official_bytes'}:seal(tmp_path,p,monkeypatch)
    with pytest.raises((ValueError,KeyError)):geo.read_locations(tmp_path,as_of=cutoff)


@pytest.mark.parametrize('http,pointer',[(False,False),(True,False),(True,True)])
def test_exact_native_fixture_to_coordinate_binding_uses_actual_raw_or_http_receipt(tmp_path,monkeypatch,http,pointer):
    data,_,_,_,_=prepared(tmp_path,monkeypatch,http=http,pointer=pointer)
    original=deepcopy(data)
    records,issues=geo.match_locations(tmp_path,[target_fixture(f) for f in data['fixtures']],as_of=NOW)
    assert len(records)==3 and not issues
    assert all(r['payload']['venue_id']=='wikidata:Q1432517' and len(r['source_receipts'])==3 for r in records.values())
    assert data==original


@pytest.mark.parametrize('fault',['team','competition','season','kickoff','status','city','venue','stale','explicit_pointer'])
def test_source_identity_name_city_freshness_and_pointer_are_binding(tmp_path,monkeypatch,fault):
    data,_,_,_,_=prepared(tmp_path,monkeypatch,http=True,pointer=True)
    f=deepcopy(data['fixtures'][0]);m=target_fixture(f)
    cutoff=NOW
    if fault=='team':m['home_team_id']='tm_999'
    elif fault=='competition':m['competition_id']='comp_999'
    elif fault=='season':m['season_id']='sn_999'
    elif fault=='kickoff':m['kickoff']=(NOW+timedelta(days=2)).isoformat()
    elif fault=='status':m['status']='finished'
    elif fault=='city':
        p=json.loads((tmp_path/'data/arthur/stadium-locations'/(geo.REVIEWED_BUNDLE+'.json')).read_bytes())
        p['records'][0]['native_city']='Brest-guess';seal(tmp_path,p,monkeypatch)
    elif fault=='venue':
        p=json.loads((tmp_path/'data/arthur/stadium-locations'/(geo.REVIEWED_BUNDLE+'.json')).read_bytes())
        p['records'][0]['native_name']='Other stadium';seal(tmp_path,p,monkeypatch)
    elif fault=='stale':cutoff=NOW+timedelta(minutes=31)
    elif fault=='explicit_pointer':
        m['_venue_source']={**f['_venue_source'],'http_receipt_id':'f'*64}
    values,issues=geo.match_locations(tmp_path,[m],as_of=cutoff)
    assert not values and issues


def test_geography_is_bound_to_every_option_leg_with_no_score_or_option_change(tmp_path,monkeypatch):
    data,_,_,_,_=prepared(tmp_path,monkeypatch)
    legs=[{'fixture_id':f['fixture_id'],'candidate_id':str(i),'selection_score':.7} for i,f in enumerate(data['fixtures'])]
    options={'profile':[{'recommendation':{'legs':legs}}]};before=canonical_sha256([data,options])
    result=review_venue_options(tmp_path,data['fixtures'],options,{},as_of=NOW)
    assert result['status']=='OBSERVED' and result['source_audit']['summary']['verified_venue_coordinate_pairs']==3
    assert all(r['location_ready'] for r in result['option_reviews'][0]['legs'])
    assert all(r['ranking_multiplier']==1 for r in result['option_reviews'][0]['legs'])
    assert validate(result['source_audit']['dataset'])==result['source_audit']['dataset']
    assert before==canonical_sha256([data,options])


def test_conflicting_descriptors_with_same_match_id_are_both_withheld(tmp_path,monkeypatch):
    data,_,_,_,_=prepared(tmp_path,monkeypatch)
    first=target_fixture(data['fixtures'][0]);second={**first,'home_team_id':'tm_999'}
    values,issues=geo.match_locations(tmp_path,[first,second],as_of=NOW)
    assert not values and issues=={'CONFLICTING_REQUESTED_MATCH_LOCATIONS':1}


def test_explicit_stadium_forecast_bypasses_city_geocoding_and_keeps_cache_clock():
    calls=[]
    def transport(url):
        assert 'geocoding' not in url;calls.append(url)
        return {'hourly':{'time':['2026-10-23T12:00'],'temperature_2m':[10],
            'precipitation_probability':[20],'precipitation':[.1],'weather_code':[3],
            'wind_speed_10m':[12],'wind_gusts_10m':[20],'relative_humidity_2m':[75]}}
    provider=OpenMeteoForecast(transport,clock=lambda:NOW)
    fixture={'fixture_id':'the_stats_api:mt_1','venue_city':'Brest','kickoff':'2026-10-23T12:00:00+00:00'}
    a=provider.forecast(fixture,coordinates=(48.40293,-4.46148))
    b=provider.forecast(fixture,coordinates=(48.40293,-4.46148))
    assert len(calls)==1 and a['observed_at']==b['observed_at']
    assert a['latitude']==48.40293 and a['longitude']==-4.46148


def test_match_specific_external_venue_distance_is_not_a_team_itinerary_or_clock_refresh():
    calendars_=calendars();before=deepcopy(calendars_)
    locations=[]
    for m,lat,lon in [(match(),48.40293,-4.46148),(calendars_[0]['payload']['matches'][-1],45.041667,7.65)]:
        p={**m,'provider':'wikidata','coordinate_scope':'VERIFIED_STADIUM','venue_id':'wikidata:Q1',
           'latitude':lat,'longitude':lon}
        locations.append(envelope(p))
    result=build_schedule_features(match(),calendars_,venues=locations,as_of=SCHEDULE_NOW)
    home,away=result['teams']
    assert 900<home['previous_to_target_venue_great_circle_km']<1200
    assert home['actual_travel_km'] is None and home['actual_rest_hours'] is None
    assert away['previous_to_target_venue_great_circle_km'] is None
    assert calendars_==before
    locations[0]['payload']['home_team_id']='tm_99';locations[0]['payload_sha256']=sha(locations[0]['payload'])
    conflict=build_schedule_features(match(),calendars_,venues=locations,as_of=SCHEDULE_NOW)
    assert conflict['teams'][0]['previous_to_target_venue_great_circle_km'] is None


def test_archived_stadium_forecast_is_actually_consumed_by_native_weather_reader(tmp_path,monkeypatch):
    data,_,db,_,_=prepared(tmp_path,monkeypatch,http=True,pointer=True)
    fixture={**data['fixtures'][0],'venue_name':'caller guess ignored','venue_city':'caller guess ignored'}
    calls=[]
    def transport(url):
        assert 'geocoding' not in url;calls.append(url)
        text=fixture['kickoff'][:16]
        return {'latitude':48.40,'longitude':-4.46,'utc_offset_seconds':0,
            'hourly':{'time':[text],'temperature_2m':[10],'precipitation_probability':[20],
                'precipitation':[.1],'weather_code':[3],'wind_speed_10m':[12],
                'wind_gusts_10m':[20],'relative_humidity_2m':[75]},
            'hourly_units':{'temperature_2m':'°C','precipitation_probability':'%',
                'precipitation':'mm','weather_code':'wmo code','wind_speed_10m':'km/h',
                'wind_gusts_10m':'km/h','relative_humidity_2m':'%'}}
    provider=make_weather_provider(tmp_path,source_run_id='controlled-stadium-source-test',clock=lambda:NOW,transport=transport)
    result=provider.forecast(fixture)
    assert len(calls)==1 and result['location_basis']=='EXACT_MATCH_AND_REVIEWED_STADIUM_WGS84'
    assert result['latitude']==48.40293 and result['venue_city']=='Brest'
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW+timedelta(seconds=1)).capture('weather',limit=100)
    view=inspect_native_weather(tmp_path,[fixture],as_of=NOW+timedelta(seconds=2))
    assert view['summary']['verified_venue_forecast_contexts']==1,view['issues']
    assert view['rows'][0]['venue_location']['basis']=='EXACT_MATCH_AND_REVIEWED_STADIUM_WGS84'
    assert view['rows'][0]['forecast']['temperature_c']==10
    assert not view['live_decision_effect'] and view['production_writes']==view['external_requests']==0


def test_actual_schedule_reader_consumes_match_detail_coordinates_without_clock_laundering(tmp_path,monkeypatch):
    from test_schedule_shadow import setup as calendar_setup

    from goal_analysis.jobs.schedule_shadow import inspect_schedule

    db,_raw,_,fixture,_=calendar_setup(tmp_path)
    sources(tmp_path,monkeypatch,now=SCHEDULE_NOW)
    matches=[match(),calendars()[0]['payload']['matches'][-1],calendars()[1]['payload']['matches'][-1]]
    for m,(name,city) in zip(matches,[('Stade Francis-Le Ble','Brest'),
            ('Stadio Olimpico Grande Torino','Turin'),('MHP Arena','Stuttgart')],strict=True):
        detail={'id':m['fixture_id'],'competition_id':m['competition_id'],'season_id':m['season_id'],
            'home_team':{'id':m['home_team_id']},'away_team':{'id':m['away_team_id']},
            'utc_date':m['kickoff'],'status':m['status'],'is_neutral':False,'venue':{'name':name,'city':city}}
        SnapshotStore(db,tmp_path/'data/raw').save('the_stats_api','detail',json.dumps({'data':detail}).encode(),
            'application/json',fetched_at=SCHEDULE_NOW-timedelta(minutes=2),
            source_url='https://api.thestatsapi.com/api/football/matches/'+m['fixture_id'])
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:SCHEDULE_NOW-timedelta(seconds=20)).capture('raw',limit=100)
    view=inspect_schedule(tmp_path,[fixture],as_of=SCHEDULE_NOW)
    teams=view['features'][0]['teams']
    assert all(t['previous_to_target_venue_great_circle_km']>900 for t in teams),view['location_issues']
    assert all(t['actual_travel_km'] is None for t in teams)
    assert all(t['previous_kickoff_gap_hours'] in {24,48} for t in teams)
    assert view['production_writes']==view['external_requests']==0


def test_schedule_only_passes_each_targets_own_at_most_three_locations(tmp_path,monkeypatch):
    from test_schedule_shadow import setup as calendar_setup

    from goal_analysis.jobs import schedule_shadow as module

    _,_,_,fixture,_=calendar_setup(tmp_path)
    second={**fixture,'fixture_id':'the_stats_api:mt_999','provider_fixture_id':'mt_999',
        'provider_home_team_id':'tm_9','provider_away_team_id':'tm_10'}
    rows=[match(),calendars()[0]['payload']['matches'][-1],calendars()[1]['payload']['matches'][-1],target_fixture(second)]
    locations={m['fixture_id']:envelope({**m,'provider':'wikidata','coordinate_scope':'VERIFIED_STADIUM',
        'latitude':48.,'longitude':6.}) for m in rows}
    monkeypatch.setattr(geo,'match_locations',lambda *a,**kw:(locations,{}))
    original=module.build_schedule_features;seen={}
    def observe(target,values,*,as_of,venues=None):
        if venues is not None:seen[target['fixture_id']]={v['payload']['fixture_id'] for v in venues}
        return original(target,values,as_of=as_of,venues=venues)
    monkeypatch.setattr(module,'build_schedule_features',observe)
    module.inspect_schedule(tmp_path,[fixture,second],as_of=SCHEDULE_NOW)
    assert seen['mt_100']=={'mt_100','mt_1','mt_2'} and seen['mt_999']=={'mt_999'}
    assert max(map(len,seen.values()))<=3
