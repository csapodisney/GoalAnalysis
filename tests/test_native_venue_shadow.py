"""Actual local raw/HTTP witnesses, controlled data, no invented coordinates."""
import json
import socket
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock

import pytest
from test_xg_specialist_shadow import NOW
from test_xg_specialist_shadow import environment as base

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import native_options_shadow as pipeline
from goal_analysis.jobs import native_venue_shadow as venue
from goal_analysis.normalization.training_datasets import validate
from goal_analysis.storage import SnapshotStore
from goal_analysis.storage.http_observations import HttpObservations
from goal_analysis.storage.temporal import TemporalStore


def environment(root, *, change=None, when=None, witness=True, available=None, pointer=False, http=False):
    data, models, db, _ = base(root, history_teams=[])
    store = SnapshotStore(db, root/'data/raw')
    observed = when or NOW-timedelta(minutes=3)
    sources = []
    for index, fixture in enumerate(data['fixtures']):
        match = {'id':fixture['provider_fixture_id'], 'competition_id':fixture['provider_league_id'],
            'season_id':fixture['provider_season_id'], 'home_team':{'id':fixture['provider_home_team_id']},
            'away_team':{'id':fixture['provider_away_team_id']}, 'utc_date':fixture['kickoff'],
            'status':'scheduled', 'is_neutral':False, 'venue':{'name':'Actual match stadium', 'city':'Berlin'}}
        if change: change(match)
        url = 'https://api.thestatsapi.com/api/football/matches/'+fixture['provider_fixture_id']
        meta = store.save('the_stats_api', 'venue_detail', json.dumps({'data':match}).encode(),
            'application/json', fetched_at=observed, source_url=url)
        source = {'provider':'the_stats_api', 'content_hash':meta.content_hash, 'source_url':url, 'observed_at':observed.isoformat()}
        if http:
            proof = HttpObservations(db, root, clock=lambda:available or NOW-timedelta(seconds=4)).admit({
                'provider':'the_stats_api', 'authenticated':True, 'http_status':200,
                'response_id':f'{index+1:032x}', 'payload_sha256':meta.content_hash,
                'source_url':url, 'observed_at':observed.isoformat()})
            source.update(proof)
        sources.append(source)
        if pointer: fixture['_venue_source'] = source
    if witness:
        TemporalStore(db, root/'data/raw', clock=lambda:available or NOW-timedelta(seconds=3)).capture('raw', limit=1000)
    return data, models, db, sources


@pytest.mark.parametrize('pointer,http', [(False,False),(True,False),(False,True),(True,True)])
def test_exact_match_metadata_readonly_receipt_bound_without_coordinate_claim(tmp_path,monkeypatch,pointer,http):
    data,_,db,_=environment(tmp_path,pointer=pointer,http=http)
    original=deepcopy(data)
    def logical():
        with closing(sqlite3.connect(db.path.as_uri()+'?mode=ro',uri=True)) as connection:
            return tuple(connection.iterdump())
    before=logical(); connect=sqlite3.connect
    def readonly(path,*args,**kwargs):
        assert kwargs.get('uri') and str(path).endswith('?mode=ro')
        return connect(path,*args,**kwargs)
    monkeypatch.setattr(socket,'socket',Mock(side_effect=AssertionError('NO_NETWORK')))
    monkeypatch.setattr(sqlite3,'connect',readonly)
    result=venue.inspect_native_venues(tmp_path,data['fixtures'],as_of=NOW)
    assert result['summary']=={'fixtures':3,'verified_match_venue_metadata':3,
        'prematch_eligible_metadata':3,'verified_venue_coordinate_pairs':0}, result['issues']
    assert validate(result['dataset'])==result['dataset'] and original==data
    assert logical()==before and result['production_writes']==result['external_requests']==0
    for row in result['rows']:
        assert row['status']=='VENUE_METADATA_VERIFIED_SHADOW'
        assert row['source_receipts'][0]['receipt_id'] and row['target_receipt']['receipt_id']
        assert row['venue_metadata']['name']=='Actual match stadium'
        assert row['coordinate_status']=='UNKNOWN' and not row['location_ready']
        assert all(row['venue_metadata'][k] is None for k in ('provider_venue_id','latitude','longitude'))
        assert row['ranking_multiplier']==1 and row['probability_adjustment']==0 and not row['live_decision_effect']


@pytest.mark.parametrize('broken',['match','competition','season','home','away','kickoff','status','neutral',
                                 'unknown_neutral','missing_venue','name','city','control_character'])
def test_wrong_match_context_cannot_be_joined_by_team_or_city(tmp_path,broken):
    def change(match):
        if broken in ('match','competition','season'):match[{'match':'id','competition':'competition_id','season':'season_id'}[broken]]='wrong'
        elif broken in ('home','away'):match[broken+'_team']['id']='tm_987654'
        elif broken=='kickoff':match['utc_date']=(NOW+timedelta(days=2)).isoformat()
        elif broken=='status':match['status']='cancelled'
        elif broken in ('neutral','unknown_neutral'):match['is_neutral']=True if broken=='neutral' else None
        elif broken=='missing_venue':match.pop('venue')
        elif broken=='control_character':match['venue']['name']='unsafe\ntext'
        else:match['venue'][broken]=None
    data,_,_,_=environment(tmp_path,change=change)
    result=venue.inspect_native_venues(tmp_path,data['fixtures'],as_of=NOW)
    assert result['summary']['verified_match_venue_metadata']==0
    assert all(r['status']=='UNKNOWN' and r['venue_metadata'] is None for r in result['rows'])


@pytest.mark.parametrize('fields',[
    {'id':'venue_1','latitude':52.5,'longitude':13.4},
    {'id':1,'lat':52.5,'lon':13.4},
    {'coordinates':{'latitude':52.5,'longitude':13.4}},
])
def test_undocumented_fields_are_not_silently_promoted_to_a_coordinate_contract(tmp_path,fields):
    data,*_=environment(tmp_path,change=lambda m:m['venue'].update(fields))
    row=venue.inspect_native_venues(tmp_path,data['fixtures'],as_of=NOW)['rows'][0]
    assert row['venue_metadata']['undocumented_fields_present']==sorted(fields)
    assert row['coordinate_status']=='UNKNOWN' and row['venue_metadata']['latitude'] is None


@pytest.mark.parametrize('broken',['unwitnessed','future_receipt','wrong_pointer','bad_hash','body_tampered','alias_missing'])
def test_no_fallback_from_unproven_explicit_or_latest_detail(tmp_path,broken):
    args={'pointer':True}
    if broken=='unwitnessed':args['witness']=False
    if broken=='future_receipt':args['available']=NOW+timedelta(seconds=1)
    if broken=='alias_missing':args['http']=True
    data,_,db,sources=environment(tmp_path,**args)
    if broken=='wrong_pointer':data['fixtures'][0]['_venue_source']['source_url']=sources[1]['source_url']
    if broken=='bad_hash':data['fixtures'][0]['_venue_source']['content_hash']='a'*64
    if broken=='alias_missing':data['fixtures'][0]['_venue_source']['http_observation_id']='a'*32
    if broken=='body_tampered':
        with db.connect() as connection:
            path=connection.execute('SELECT relative_path FROM raw_snapshots WHERE content_hash=?',
                (sources[0]['content_hash'],)).fetchone()[0]
        (tmp_path/'data/raw'/path).write_bytes(b'{}')
    row=venue.inspect_native_venues(tmp_path,data['fixtures'],as_of=NOW)['rows'][0]
    assert row['status']=='UNKNOWN' and not row['source_receipts'] and row['venue_metadata'] is None


@pytest.mark.parametrize('case',['stale_detail','stale_target','lockout'])
def test_old_known_text_can_remain_visible_without_becoming_current_prematch_location(tmp_path,case):
    data,*_=environment(tmp_path,when=NOW-timedelta(minutes=31) if case=='stale_detail' else None)
    cutoff=NOW+timedelta(minutes=31) if case=='stale_target' else NOW
    if case=='lockout':cutoff=venue.utc(data['fixtures'][0]['kickoff'])-timedelta(minutes=15)
    rows=venue.inspect_native_venues(tmp_path,data['fixtures'],as_of=cutoff)['rows']
    assert rows[0]['venue_metadata'] is not None and not rows[0]['metadata_prematch_eligible']
    assert not rows[0]['location_ready']


def test_same_time_conflict_and_later_missing_venue_do_not_restore_old_favourable_text(tmp_path):
    data,_,db,sources=environment(tmp_path)
    fixture=data['fixtures'][0]
    store=SnapshotStore(db,tmp_path/'data/raw')
    source=sources[0]
    with db.connect() as connection:
        meta=connection.execute('SELECT relative_path FROM raw_snapshots WHERE content_hash=?',(source['content_hash'],)).fetchone()
    body=json.loads((tmp_path/'data/raw'/meta[0]).read_text('utf-8'))
    body['data']['venue']['name']='Conflicting stadium'
    store.save('the_stats_api','venue_detail',json.dumps(body).encode(),'application/json',
        fetched_at=NOW-timedelta(minutes=3),source_url=source['source_url'])
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW-timedelta(seconds=2)).capture('raw',limit=1000)
    row=venue.inspect_native_venues(tmp_path,[fixture],as_of=NOW)['rows'][0]
    assert row['reason']=='AMBIGUOUS_LATEST_MATCH_VENUE_DETAIL'
    body['data']['venue']=None
    store.save('the_stats_api','venue_detail',json.dumps(body).encode(),'application/json',
        fetched_at=NOW-timedelta(minutes=1),source_url=source['source_url'])
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW-timedelta(seconds=1)).capture('raw',limit=1000)
    row=venue.inspect_native_venues(tmp_path,[fixture],as_of=NOW)['rows'][0]
    assert row['reason']=='MISSING_MATCH_VENUE_METADATA'


def test_missing_database_and_bounds_do_not_create_a_database_or_guessed_stadium(tmp_path):
    result=venue.inspect_native_venues(tmp_path,[],as_of=NOW)
    assert result['rows']==[] and not (tmp_path/'data').exists()
    with pytest.raises(ValueError,match='BOUNDED'):
        venue.inspect_native_venues(tmp_path,[{}]*1001,as_of=NOW)
    data,*_=environment(tmp_path)
    with pytest.raises(ValueError,match='UNIQUE'):
        venue.inspect_native_venues(tmp_path,data['fixtures']*2,as_of=NOW)
    data['fixtures'][0]['provider_season_id']='api_football:1'
    with pytest.raises(ValueError,match='Invalid native'):
        venue.inspect_native_venues(tmp_path,data['fixtures'],as_of=NOW)


def test_reader_is_attached_to_options_and_specialists_without_changing_any_decision(tmp_path,monkeypatch):
    data,models,*_=environment(tmp_path)
    original=deepcopy((data,models))
    fit=Mock(side_effect=lambda *_a,**_k:{'numeric_models':deepcopy(models),
        'source_view':{'fixtures':deepcopy(data['fixtures']),'dataset':{'manifest':{'dataset_id':'1'*64}}}})
    monkeypatch.setattr(pipeline,'replay_native_history',fit)
    result=pipeline.replay_native_options(tmp_path,{},data,{},as_of=NOW)['options_shadow']
    assert fit.call_count==1 and (data,models)==original
    review=result['venue_review'];assert review['status']=='OBSERVED' and review['leg_bindings']>=3
    assert review['source_audit']['summary']['verified_match_venue_metadata']==3
    for item in review['option_reviews']:
        option=result['options'][item['profile_id']][item['option_index']]
        assert item['option_sha256']==canonical_sha256(option)
        for leg,binding in zip(option['recommendation']['legs'],item['legs'],strict=True):
            assert binding['leg_sha256']==canonical_sha256(leg) and not binding['location_ready']
            row=review['source_audit']['rows'][binding['context_row_index']]
            assert binding['context_sha256']==canonical_sha256(row)
    monkeypatch.setattr(venue,'inspect_native_venues',Mock(side_effect=ValueError('sensitive')))
    failed=pipeline.replay_native_options(tmp_path,{},data,{},as_of=NOW)['options_shadow']
    assert failed['venue_review']['status']=='UNAVAILABLE' and 'sensitive' not in str(failed)
    for key in ('options','cards','option_source_bindings','player_review','cup_review','xg_review',
                'motivation_review','weather_review','rotation_review'):
        assert result[key]==failed[key]


def test_oversized_raw_source_is_not_loaded(tmp_path):
    data,_,db,sources=environment(tmp_path,pointer=True)
    with sqlite3.connect(db.path) as connection:
        connection.execute('UPDATE raw_snapshots SET size_bytes=? WHERE content_hash=?',(2_000_001,sources[0]['content_hash']))
    assert venue.inspect_native_venues(tmp_path,[data['fixtures'][0]],as_of=NOW)['rows'][0]['reason']=='MISSING_OR_OVERSIZED_VENUE_SOURCE'


def test_same_old_bytes_new_actual_http_response_uses_its_own_receipt(tmp_path):
    data,_,db,sources=environment(tmp_path,when=NOW-timedelta(hours=2))
    source=sources[0]
    observed=NOW-timedelta(minutes=1)
    proof=HttpObservations(db,tmp_path,clock=lambda:NOW-timedelta(seconds=2)).admit({
        'provider':'the_stats_api','authenticated':True,'http_status':200,'response_id':'f'*32,
        'payload_sha256':source['content_hash'],'source_url':source['source_url'],'observed_at':observed.isoformat()})
    row=venue.inspect_native_venues(tmp_path,[data['fixtures'][0]],as_of=NOW)['rows'][0]
    assert row['metadata_prematch_eligible'] and row['source_receipts'][0]['receipt_id']==proof['http_receipt_id']
    assert venue.utc(row['source_receipts'][0]['times']['observed_at'])==observed


def test_same_city_home_stadium_and_fixture_labels_never_substitute_for_match_detail(tmp_path):
    data,_,db,_=base(tmp_path,history_teams=[])
    fixture=data['fixtures'][0];fixture.update(venue_name='Anfield',venue_city='Liverpool')
    SnapshotStore(db,tmp_path/'data/raw').save('the_stats_api','team_detail',json.dumps({'data':{
        'id':fixture['provider_home_team_id'],'stadium':{'id':'venue_1','name':'Anfield','city':'Liverpool',
        'latitude':53.43,'longitude':-2.96}}}).encode(),'application/json',fetched_at=NOW-timedelta(minutes=3),
        source_url='https://api.thestatsapi.com/api/football/teams/'+fixture['provider_home_team_id'])
    TemporalStore(db,tmp_path/'data/raw',clock=lambda:NOW-timedelta(seconds=2)).capture('raw',limit=1000)
    row=venue.inspect_native_venues(tmp_path,[fixture],as_of=NOW)['rows'][0]
    assert row['reason']=='MISSING_ARCHIVED_MATCH_VENUE_DETAIL' and row['venue_metadata'] is None


@pytest.mark.parametrize('suffix',['?venue=1','/odds'])
def test_correct_hash_from_wrong_endpoint_is_not_a_venue_pointer(tmp_path,suffix):
    data,*_=environment(tmp_path,pointer=True)
    data['fixtures'][0]['_venue_source']['source_url']+=suffix
    row=venue.inspect_native_venues(tmp_path,[data['fixtures'][0]],as_of=NOW)['rows'][0]
    assert row['reason']=='INVALID_EXPLICIT_MATCH_VENUE_SOURCE'


def test_reader_receipt_time_never_comes_from_file_mtime(tmp_path):
    data,_,db,sources=environment(tmp_path,witness=False)
    import os
    with closing(db.connect()) as connection:
        path=connection.execute('SELECT relative_path FROM raw_snapshots WHERE content_hash=?',
            (sources[0]['content_hash'],)).fetchone()[0]
    os.utime(tmp_path/'data/raw'/path,(0,0))
    assert venue.inspect_native_venues(tmp_path,[data['fixtures'][0]],as_of=NOW)['rows'][0]['status']=='UNKNOWN'
