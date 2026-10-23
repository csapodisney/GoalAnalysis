"""Exact native taxonomy, truthful missingness and post-save SHADOW isolation."""
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs.competition_types import (
    archive_competition_types,
    inspect_competition_types,
)
from goal_analysis.normalization.competition_types import RULES, classify_competition
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.temporal import TemporalStore
from goal_analysis.storage.training_datasets import DatasetStore

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
OBS = NOW-timedelta(hours=2)
WITNESS = NOW-timedelta(hours=1)


def competition(identifier='comp_7739', **extra):
    name, country, confederation, format_, _, _ = RULES[identifier]
    return {'id': identifier, 'name': name, 'country': country,
            'confederation': confederation, 'type': format_, **extra}


def match(**extra):
    return {'id': 'mt_1', 'competition_id': 'comp_7739', 'season_id': 'sn_1',
            'utc_date': (NOW+timedelta(days=1)).isoformat(),
            'home_team': {'id': 'tm_1'}, 'away_team': {'id': 'tm_2'},
            'stage_name': 'qualifying', **extra}


def setup(tmp_path, *, catalogue=None, matches=None):
    db = Database(tmp_path/'data/goal_analysis.sqlite3'); db.initialize()
    raw = SnapshotStore(db, tmp_path/'data/raw')
    temporal = TemporalStore(db, tmp_path/'data/raw', clock=lambda: WITNESS)
    temporal.initialize()
    def put(data, resource, url, when=OBS):
        return raw.save('the_stats_api', resource, json.dumps({'data': data}).encode(),
                        'application/json', fetched_at=when, source_url=url)
    cat = put([competition()] if catalogue is None else catalogue, 'competition_catalogue',
              'https://api.thestatsapi.com/api/football/competitions?page=1&per_page=100')
    meta = put([match()] if matches is None else matches, 'matches',
               'https://api.thestatsapi.com/api/football/matches?competition_id=comp_7739')
    temporal.capture('raw', limit=100)
    fixture = {'fixture_id': 'the_stats_api:mt_1', 'source_provider': 'the_stats_api',
               'provider_fixture_id': 'mt_1', 'provider_league_id': 'comp_7739',
               'provider_season_id': 'sn_1', 'provider_home_team_id': 'tm_1',
               'provider_away_team_id': 'tm_2', 'kickoff': match()['utc_date'],
               'fixture_source': {'content_hash': meta.content_hash}}
    return db, raw, temporal, put, fixture, cat, meta


@pytest.mark.parametrize('identifier', list(RULES))
def test_every_curated_native_identity_is_classified_without_namesearch(identifier):
    result = classify_competition(competition(identifier), None)
    assert result['family']==RULES[identifier][4]
    assert result['participant_type']==RULES[identifier][5]
    assert 'probability' not in result and 'score' not in result


def test_friendly_provider_league_format_is_not_mistaken_for_competitive_league():
    r = classify_competition(competition('comp_29967'), None)
    assert r['provider_format']=='league' and r['family']=='FRIENDLY'
    assert r['participant_type']=='NATIONAL' and r['phase']=='FRIENDLY'
    assert r['is_friendly'] is True and r['is_qualifier'] is False


def test_national_qualifier_and_club_qualifier_are_orthogonal_dimensions():
    national = classify_competition(competition('comp_8973'), None)
    club = classify_competition(competition('comp_3498'), 'qualifying')
    assert national['family']=='NATIONAL_TEAM' and club['family']=='INTERNATIONAL_CLUB'
    assert national['phase']==club['phase']=='QUALIFIER'
    assert national['is_qualifier'] is club['is_qualifier'] is True


@pytest.mark.parametrize('stage,phase', [('round_of_16','KNOCKOUT'),('final','FINAL'),
                                       ('group_stage','GROUP_STAGE'),('league_phase','LEAGUE_PHASE')])
def test_explicit_stage_labels_remain_separate_from_competition_family(stage, phase):
    r = classify_competition(competition(), stage)
    assert r['phase']==phase and r['family']=='INTERNATIONAL_CLUB'
    assert r['is_qualifier'] is False


@pytest.mark.parametrize('stage', [None,'Round of 16','qualifying_guess', '', 3])
def test_unknown_stage_does_not_get_calendar_or_casefold_guesses(stage):
    r = classify_competition(competition(), stage)
    assert r['family']=='INTERNATIONAL_CLUB' and r['phase']=='UNKNOWN'
    assert r['is_qualifier'] is None and r['status']=='FAMILY_KNOWN_PHASE_UNKNOWN'


@pytest.mark.parametrize('changes', [{'name':'UEFA Europa league'},{'type':'league'},
                                   {'country':'World'},{'confederation':None},{'id':'comp_1'}])
def test_conflicting_or_unmapped_catalogue_does_not_infer_from_names(changes):
    r = classify_competition(competition(**changes), 'qualifying')
    assert r['family']=='UNKNOWN' and r['is_friendly'] is None


def test_impossible_qualifier_or_friendly_stage_is_a_conflict():
    assert classify_competition(competition('comp_8973'),'final')['reason']=='QUALIFIER_STAGE_CONFLICT'
    assert classify_competition(competition('comp_29967'),'qualifying')['reason']=='FRIENDLY_STAGE_CONFLICT'


def test_read_only_physical_boundary_and_full_lineage_are_deterministic(tmp_path):
    db, _, _, _, f, _, _ = setup(tmp_path)
    before = deepcopy(f)
    with db.connect() as con: count = con.execute('SELECT count(*) FROM raw_snapshots').fetchone()[0]
    a = inspect_competition_types(tmp_path,[f],as_of=NOW)
    assert a==inspect_competition_types(tmp_path,[f],as_of=NOW)
    assert a['counts']=={'INTERNATIONAL_CLUB':1} and a['status']=='COMPLETE'
    assert a['external_requests']==a['production_writes']==0 and not a['live_decision_effect']
    assert len(a['dataset']['lineage'])==2 and len(a['rows'][0]['source_receipt_ids'])==2
    assert f==before
    with db.connect() as con: assert con.execute('SELECT count(*) FROM raw_snapshots').fetchone()[0]==count


def test_earlier_cutoff_does_not_borrow_later_witness(tmp_path):
    _, _, _, _, f, _, _ = setup(tmp_path)
    r = inspect_competition_types(tmp_path,[f],as_of=OBS+timedelta(minutes=5))
    assert r['counts']=={'UNKNOWN':1} and r['dataset']['lineage']==[]


def test_future_catalogue_version_does_not_replace_old_known_identity(tmp_path):
    _, _, temporal, put, f, _, _ = setup(tmp_path)
    original = inspect_competition_types(tmp_path,[f],as_of=NOW)
    put([competition(type='league')], 'competition_catalogue',
        'https://api.thestatsapi.com/api/football/competitions?page=2', OBS+timedelta(minutes=5))
    temporal.clock=lambda: NOW+timedelta(hours=1); temporal.capture('raw',limit=100)
    r = inspect_competition_types(tmp_path,[f],as_of=NOW)
    assert r['rows']==original['rows'] and r['dataset']==original['dataset']


def test_same_time_conflicting_catalogue_and_match_are_unknown(tmp_path):
    _, _, temporal, put, f, _, _ = setup(tmp_path)
    put([competition(type='league')], 'competition_catalogue',
        'https://api.thestatsapi.com/api/football/competitions?page=2')
    temporal.capture('raw',limit=100)
    assert inspect_competition_types(tmp_path,[f],as_of=NOW)['counts']=={'UNKNOWN':1}
    other = tmp_path/'other'
    _, _, _, _, f, _, _ = setup(other,matches=[match(),match(stage_name='final')])
    assert inspect_competition_types(other,[f],as_of=NOW)['counts']=={'UNKNOWN':1}


@pytest.mark.parametrize('field,value', [('provider_home_team_id','tm_3'),('provider_season_id','sn_2'),
                                       ('provider_league_id','comp_3498'),('source_provider','api_football')])
def test_exact_fixture_identity_required_and_legacy_config_labels_are_ignored(tmp_path,field,value):
    _, _, _, _, f, _, _ = setup(tmp_path)
    f[field]=value; f['competition_type']='UEFA'
    assert inspect_competition_types(tmp_path,[f],as_of=NOW)['counts']=={'UNKNOWN':1}


def test_corrupt_source_cannot_classify(tmp_path):
    _, _, _, _, f, _, meta = setup(tmp_path)
    path=tmp_path/'data/raw'/meta.relative_path
    path.write_bytes(path.read_bytes()+b' ')
    assert inspect_competition_types(tmp_path,[f],as_of=NOW)['counts']=={'UNKNOWN':1}


def test_conflicting_nested_ids_and_partly_invalid_pages_are_not_partly_admitted(tmp_path):
    _, _, _, _, f, _, _ = setup(tmp_path,matches=[match(),match(id='mt_bad')])
    assert inspect_competition_types(tmp_path,[f],as_of=NOW)['counts']=={'UNKNOWN':1}
    other=tmp_path/'other'
    _, _, _, _, f, _, _ = setup(other,matches=[match(competition={'id':'comp_3498'})])
    assert inspect_competition_types(other,[f],as_of=NOW)['counts']=={'UNKNOWN':1}


def test_absent_pointer_does_not_search_the_archive_for_a_convenient_match(tmp_path):
    _, _, _, _, f, _, _ = setup(tmp_path)
    del f['fixture_source']
    assert inspect_competition_types(tmp_path,[f],as_of=NOW)['counts']=={'UNKNOWN':1}


def test_a_forecast_uses_its_own_page_not_another_requested_fixtures_newer_page(tmp_path):
    _, _, temporal, put, f, _, _ = setup(tmp_path)
    meta=put([match(stage_name='final'),match(id='mt_2')], 'matches',
             'https://api.thestatsapi.com/api/football/matches?competition_id=comp_7739&page=2',
             OBS+timedelta(minutes=5))
    temporal.capture('raw',limit=100)
    second={**f,'fixture_id':'the_stats_api:mt_2','provider_fixture_id':'mt_2',
            'fixture_source':{'content_hash':meta.content_hash}}
    r=inspect_competition_types(tmp_path,[f,second],as_of=NOW)
    assert r['counts']=={'INTERNATIONAL_CLUB':2} and r['rows'][0]['phase']=='QUALIFIER'


def test_missing_database_and_duplicate_requested_fixtures_are_explicit(tmp_path):
    r=inspect_competition_types(tmp_path,[{'fixture_id':'anything'}],as_of=NOW)
    assert r['counts']=={'UNKNOWN':1}
    with pytest.raises(ValueError,match='DUPLICATE_REQUESTED_FIXTURE'):
        inspect_competition_types(tmp_path,[{'fixture_id':'a'},{'fixture_id':'a'}],as_of=NOW)


def test_post_save_hook_archives_exact_view_without_mutating_report(tmp_path):
    _, _, _, _, f, _, _ = setup(tmp_path)
    report={'fixtures':[f],'recommendations':[{'legs':['untouched']}], 'report_sha256':'original'}
    original=deepcopy(report)
    a=archive_competition_types(tmp_path,report,as_of=NOW)
    b=archive_competition_types(tmp_path,report,as_of=NOW)
    assert a['dataset_created'] is True and b['dataset_created'] is False
    store=DatasetStore(tmp_path/'data/arthur/competition-types/datasets')
    assert len(store.read(a['dataset_id'])['rows'])==1 and report==original


def test_archive_disk_failure_cannot_veto_existing_canonical_proof_or_tickets(tmp_path,monkeypatch):
    from goal_analysis.jobs import fixture_contexts
    _, _, _, _, f, _, _ = setup(tmp_path)
    config=tmp_path/'config/canonical-contexts.json';config.parent.mkdir()
    config.write_text(json.dumps({'enabled':True,'mode':'SHADOW'}))
    monkeypatch.setattr(fixture_contexts,'link_report',lambda *args,**kwargs:
                        {'gaps':{},'counts':{'fixtures_linked':1},'ticket_decision_changes':0})
    monkeypatch.setattr(DatasetStore,'put',lambda *args: (_ for _ in ()).throw(OSError('private-path')))
    r=fixture_contexts.shadow_archive(tmp_path,{'fixtures':[f]},clock=lambda:NOW)
    assert r['status']=='COMPLETE' and r['counts']['fixtures_linked']==1
    assert r['competition_types']['status']=='UNAVAILABLE'
    assert 'private-path' not in json.dumps(r)
