"""Cached xG uses two hash-bound, actually witnessed sources; no guessed scopes."""
import json
from copy import deepcopy
from datetime import timedelta

import pytest
from test_xg_rates import NOW

from goal_analysis.jobs.xg_shadow import inspect_xg_rates
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.temporal import TemporalStore

OBS = NOW-timedelta(hours=2)
WITNESS = NOW-timedelta(hours=1)


def match(number=1, **extra):
    return {"id": f"mt_{number}", "competition_id": "comp_1", "season_id": "sn_1",
        "home_team": {"id": "tm_1", "name": "Home"},
        "away_team": {"id": "tm_2", "name": "Away"},
        "utc_date": (NOW-timedelta(days=1)).isoformat(), "status": "finished",
        "score": {"went_to_extra_time": False, "went_to_penalties": False},
        "is_neutral": False, "xg_available": True, "xg_quality": "full", **extra}


def stats(number=1, **extra):
    return {"match_id": f"mt_{number}", "overview": {"expected_goals": {
        "first_half": {"home": .8, "away": .5},
        "second_half": {"home": .7, "away": .6},
        "all": {"home": 99, "away": 99}}}, **extra}


def setup(tmp_path, matches=None, statistic=None):
    database = Database(tmp_path/'data/goal_analysis.sqlite3'); database.initialize()
    raw = SnapshotStore(database, tmp_path/'data/raw')
    temporal = TemporalStore(database, tmp_path/'data/raw', clock=lambda: WITNESS)
    temporal.initialize()
    def put(data, url, observed=OBS):
        return raw.save('the_stats_api','xg_test',json.dumps({'data':data}).encode(),
                        'application/json',fetched_at=observed,source_url=url)
    put([match()] if matches is None else matches,
        'https://api.thestatsapi.com/api/football/matches?date_from=2026-10-01&date_to=2026-10-21')
    saved = put(stats() if statistic is None else statistic,
                'https://api.thestatsapi.com/api/football/matches/mt_1/stats')
    temporal.capture('raw',limit=100)
    return database, raw, temporal, put, saved


def test_reader_uses_explicit_regulation_halves_not_provider_all(tmp_path):
    setup(tmp_path)
    view=inspect_xg_rates(tmp_path,as_of=NOW)
    assert view['fit']['accepted_matches']==1
    row=view['envelopes'][0]['payload']
    assert row['home_xg']==pytest.approx(1.5) and row['away_xg']==pytest.approx(1.1)
    assert row['available_at']==WITNESS.isoformat() and row['observed_at']==OBS.isoformat()
    assert view['external_requests']==view['production_writes']==0
    assert len(view['source_receipts'])==2 and not view['live_decision_effect']


def test_no_earlier_replay_borrows_a_later_availability_witness(tmp_path):
    setup(tmp_path)
    assert inspect_xg_rates(tmp_path,as_of=OBS+timedelta(minutes=30))['fit']['accepted_matches']==0
    assert inspect_xg_rates(tmp_path,as_of=NOW)['fit']['accepted_matches']==1


def test_new_unavailable_version_cannot_steal_old_replay_input_or_limit(tmp_path):
    _,_,temporal,put,_=setup(tmp_path)
    original=inspect_xg_rates(tmp_path,as_of=NOW,limit=1)['fit']
    modified=stats();modified['overview']['expected_goals']['first_half']['home']=3
    put(modified,'https://api.thestatsapi.com/api/football/matches/mt_1/stats',OBS+timedelta(minutes=10))
    temporal.clock=lambda:NOW+timedelta(hours=1)
    temporal.capture('raw',limit=100)
    assert inspect_xg_rates(tmp_path,as_of=NOW,limit=1)['fit']==original


@pytest.mark.parametrize('change',[{'is_neutral':None},{'is_neutral':True},{'xg_quality':'partial'},
    {'score':{'went_to_extra_time':True,'went_to_penalties':False}}])
def test_unknown_or_wrong_venue_duration_quality_are_not_invented(tmp_path,change):
    setup(tmp_path,matches=[match(**change)])
    r=inspect_xg_rates(tmp_path,as_of=NOW)
    assert r['fit']['accepted_matches']==0
    assert r['issues']['UNPROVEN_REGULATION_VENUE_OR_XG_QUALITY']==1


def test_stats_endpoint_and_metadata_identity_must_match_exactly(tmp_path):
    setup(tmp_path,statistic=stats(2))
    r=inspect_xg_rates(tmp_path,as_of=NOW)
    assert not r['envelopes'] and r['issues']['XG_MATCH_ID_MISMATCH']==1


def test_raw_hash_change_after_temporal_receipt_is_not_accepted(tmp_path):
    _,_,_,_,saved=setup(tmp_path)
    file=tmp_path/'data/raw'/saved.relative_path
    file.write_bytes(file.read_bytes()+b' ')
    r=inspect_xg_rates(tmp_path,as_of=NOW)
    assert r['fit']['accepted_matches']==0 and r['issues']['RAW_XG_HASH_MISMATCH']==1


def test_missing_half_does_not_fall_back_to_all_or_zero(tmp_path):
    value=stats();del value['overview']['expected_goals']['first_half']['home']
    setup(tmp_path,statistic=value)
    assert inspect_xg_rates(tmp_path,as_of=NOW)['fit']['accepted_matches']==0


def test_same_time_conflicting_statistic_versions_are_not_hash_sorted(tmp_path):
    _,_,temporal,put,_=setup(tmp_path)
    other=stats();other['overview']['expected_goals']['first_half']['home']=3
    put(other,'https://api.thestatsapi.com/api/football/matches/mt_1/stats')
    temporal.capture('raw',limit=100)
    r=inspect_xg_rates(tmp_path,as_of=NOW)
    assert not r['envelopes'] and r['issues']['AMBIGUOUS_LATEST_XG_VERSION']==1


def test_limit_counts_unique_applicable_matches_not_repeated_versions(tmp_path):
    _,_,temporal,put,_=setup(tmp_path,matches=[match(),match(2)])
    put(stats(2),'https://api.thestatsapi.com/api/football/matches/mt_2/stats')
    other=stats();other['overview']['expected_goals']['first_half']['home']=3
    put(other,'https://api.thestatsapi.com/api/football/matches/mt_1/stats',OBS+timedelta(minutes=10))
    temporal.capture('raw',limit=100)
    r=inspect_xg_rates(tmp_path,as_of=NOW,limit=2)
    assert r['fit']['accepted_matches']==2 and r['status']!='LIMIT_REACHED'


def test_ambiguous_latest_identity_does_not_borrow_old_metadata(tmp_path):
    _,_,temporal,put,_=setup(tmp_path)
    duplicate=deepcopy(match());duplicate['away_team']['id']='tm_3'
    put([match(),duplicate],
        'https://api.thestatsapi.com/api/football/matches?date_from=2026-10-01&date_to=2026-10-22',
        OBS+timedelta(minutes=10))
    temporal.capture('raw',limit=100)
    assert inspect_xg_rates(tmp_path,as_of=NOW)['fit']['accepted_matches']==0


def test_empty_database_and_absent_proof_tables_are_explicit(tmp_path):
    assert inspect_xg_rates(tmp_path,as_of=NOW)['status']=='MISSING_SOURCE'
    Database(tmp_path/'data/goal_analysis.sqlite3').initialize()
    assert inspect_xg_rates(tmp_path,as_of=NOW)['status']=='MISSING_PROOF_TABLES'
