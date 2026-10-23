"""Canonical links, source bytes and committed availability; no live services."""
import json
import sqlite3
from copy import deepcopy
from datetime import timedelta

import pytest
from test_the_stats_context import NOW, Transport, report, run

from goal_analysis.jobs.fixture_contexts import link_report
from goal_analysis.normalization.canonical import CanonicalCompetition, canonical_id, utc
from goal_analysis.storage import Database
from goal_analysis.storage.canonical_registry import (
    CanonicalRegistry,
    MappingEvidence,
    ProviderBinding,
)
from goal_analysis.storage.fixture_contexts import FixtureContexts


def setup(root):
    data = report()
    data['sports_supplement'] = {'the_stats_api': run(root, Transport(), data)}
    db = Database(root / 'data/goal_analysis.sqlite3')
    registry = CanonicalRegistry(db, clock=lambda: NOW)
    registry.initialize()
    entity = CanonicalCompetition(entity_id=canonical_id('competition', 'the_stats_api:comp_3039'),
                                  name='Premier League', country='England')
    source = data['sports_supplement']['the_stats_api']['context_by_fixture']['api_football:1']['facts'][0]['source']
    registry.register([entity], [ProviderBinding('the_stats_api', 'competition', 'comp_3039', entity.entity_id)],
        MappingEvidence('NATIVE_PROVIDER_ID', source['source_url'], source['content_hash'], NOW))
    return data, db, registry


def without_receipt(record):
    return {k:v for k,v in record.items() if k not in {'version', 'event_id', 'ingested_at', 'available_at', 'availability_basis'}}


def test_original_bytes_link_to_same_canonical_match_and_reimport_is_idempotent(tmp_path):
    data, db, registry = setup(tmp_path)
    before = deepcopy(data)
    now = NOW + timedelta(minutes=1)
    proof = link_report(tmp_path, data, clock=lambda: now)
    assert data == before and proof['mode'] == 'SHADOW' and proof['external_requests'] == 0
    assert proof['counts']['fixtures_linked'] == 1 and proof['counts']['events_created'] >= 3
    canonical = proof['fixture_links']['api_football:1']
    assert registry.resolve('api_football', 'fixture', '1', as_of=now) == canonical
    assert registry.resolve('the_stats_api', 'fixture', 'mt_1', as_of=now) == canonical
    store = FixtureContexts(db, clock=lambda: now)
    rows = store.records(canonical, as_of=now)
    assert {'lineup', 'injuries_suspensions'} <= {r['category'] for r in rows}
    assert all(utc(r['available_at']) == now and r['availability_basis'] == 'SOURCE_PRESENCE_UPPER_BOUND' for r in rows)
    assert store.records(canonical, as_of=NOW) == []
    second = link_report(tmp_path, data, clock=lambda: now + timedelta(hours=1))
    assert second['counts']['events_created'] == 0
    assert store.records(canonical, as_of=now + timedelta(hours=1)) == rows
    other_run = {**data, 'run_id': 'another-report-with-the-same-cached-facts'}
    third = link_report(tmp_path, other_run, clock=lambda: now + timedelta(hours=1))
    assert third['counts']['events_created'] == 0
    assert store.records(canonical, as_of=now + timedelta(hours=1)) == rows


@pytest.mark.parametrize('change', ['names', 'kickoff', 'mapping_id', 'missing_bytes', 'future_source', 'normalized_value'])
def test_wrong_or_future_identity_and_unbound_data_cannot_become_positive_context(tmp_path, change):
    data, db, _ = setup(tmp_path)
    now = NOW + timedelta(minutes=1)
    context = data['sports_supplement']['the_stats_api']['context_by_fixture']['api_football:1']
    if change == 'names':
        data['fixtures'][0]['home_team'] = 'Other Team'
    elif change == 'kickoff':
        data['fixtures'][0]['kickoff'] = '2026-10-18T16:30:00+00:00'
    elif change == 'mapping_id':
        context['mapping']['match_id'] = 'mt_999'
    elif change == 'missing_bytes':
        source = context['mapping']['source']['content_hash']
        with db.connect() as connection:
            p = connection.execute('SELECT relative_path FROM raw_snapshots WHERE content_hash=?', (source,)).fetchone()[0]
        (tmp_path / 'data/raw' / p).write_text('{}')
    elif change == 'future_source':
        context['mapping']['source']['observed_at'] = (now + timedelta(hours=1)).isoformat()
    else:
        for fact in context['facts']:
            if fact['category'] == 'injuries_suspensions':
                fact['data']['injuries'] = [{'player_id':'pl_99','active':True}]
    proof = link_report(tmp_path, data, clock=lambda: now)
    if change == 'normalized_value':
        assert proof['gaps'].get('INVALID_FACT_RAW_PROOF', 0) >= 1
        store = FixtureContexts(db, clock=lambda: now)
        rows = store.records(proof['fixture_links']['api_football:1'], as_of=now)
        assert all(r['category'] != 'injuries_suspensions' for r in rows)
    else:
        assert not proof['fixture_links'] and not proof['counts'].get('events_created')


def test_future_forecast_target_is_allowed_and_reanalysis_stays_separate(tmp_path):
    from test_sports_weather import weather

    from goal_analysis.storage.sports_weather import WeatherStore

    data, db, _ = setup(tmp_path)
    archive = WeatherStore(db)
    archive.initialize()
    fixture = data['fixtures'][0]
    for kind in ('FORECAST', 'OBSERVATION', 'REANALYSIS'):
        archive.ingest(weather(fixture_id=fixture['fixture_id'], weather_kind=kind, observed_at=NOW.isoformat(),
                              forecast_time=(NOW - timedelta(hours=1) if kind != 'FORECAST' else NOW + timedelta(hours=4)).isoformat()),
                       fixture, source_run_id='weather-test')
    now = NOW + timedelta(minutes=1)
    proof = link_report(tmp_path, data, clock=lambda: now)
    assert proof['counts']['weather_created'] == 3
    rows = FixtureContexts(db).records(proof['fixture_links']['api_football:1'], as_of=now)
    assert {'weather_forecast','weather_observation','weather_reanalysis'} <= {r['category'] for r in rows}
    assert FixtureContexts(db).records(proof['fixture_links']['api_football:1'], as_of=NOW) == []


def test_news_keeps_actual_source_publication_and_unverified_summary_label(tmp_path):
    from test_sports_news import OBSERVED, result

    from goal_analysis.storage.sports_news import NewsStore

    data, db, _ = setup(tmp_path)
    news = NewsStore(db)
    news.initialize()
    payload = result()
    payload['claims'][0]['fixture_id'] = 'api_football:1'
    news.ingest_grok_result(payload, request_id='real-format-test', observed_at=OBSERVED)
    now = NOW + timedelta(minutes=1)
    proof = link_report(tmp_path, data, clock=lambda: now)
    assert proof['counts']['news_created'] == 1
    rows = FixtureContexts(db).records(proof['fixture_links']['api_football:1'], as_of=now)
    claim = next(r for r in rows if r['category'] == 'news')
    assert claim['payload']['representation'] == 'MODEL_GENERATED_SUMMARY'
    assert claim['payload']['content_status'] == 'UNVERIFIED'
    assert claim['published_at'] == claim['payload']['published_at']
    assert claim['source_url'] == payload['claims'][0]['source_url']


def test_events_and_witnesses_are_immutable_and_partial_batches_are_not_written(tmp_path):
    data, db, _ = setup(tmp_path)
    now = NOW + timedelta(minutes=1)
    proof = link_report(tmp_path, data, clock=lambda: now)
    store = FixtureContexts(db, clock=lambda: now)
    canonical = proof['fixture_links']['api_football:1']
    event = without_receipt(store.records(canonical, as_of=now)[0])
    bad = {**event, 'observed_at': (now + timedelta(minutes=1)).isoformat()}
    with pytest.raises(ValueError, match='FUTURE'):
        store.append([event, bad])
    with db.connect() as connection:
        before = connection.execute('SELECT count(*) FROM fixture_context_events').fetchone()[0]
        for table in ('fixture_context_events', 'fixture_context_receipts'):
            with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                connection.execute(f'DELETE FROM {table}')
        assert connection.execute('SELECT count(*) FROM fixture_context_events').fetchone()[0] == before


def test_corrupt_record_index_is_detected_instead_of_silently_used(tmp_path):
    data, db, _ = setup(tmp_path)
    now = NOW + timedelta(minutes=1)
    proof = link_report(tmp_path, data, clock=lambda: now)
    with db.connect() as connection:
        connection.execute('DROP TRIGGER fixture_context_events_no_update')
        connection.execute("UPDATE fixture_context_events SET category='news'")
    with pytest.raises(ValueError, match='CORRUPT_CONTEXT_INDEX'):
        FixtureContexts(db).records(proof['fixture_links']['api_football:1'], as_of=now)


def test_crash_between_source_commit_and_witness_is_invisible_until_recovery(tmp_path, monkeypatch):
    from contextlib import contextmanager

    data, db, _ = setup(tmp_path)
    clock = [NOW + timedelta(minutes=1)]
    proof = link_report(tmp_path, data, clock=lambda: clock[0])
    store = FixtureContexts(db, clock=lambda: clock[0])
    canonical = proof['fixture_links']['api_football:1']
    original = store.records(canonical, as_of=clock[0])
    event = {**without_receipt(original[0]), 'origin_reference': 'new-committed-observation',
             'observed_at': (NOW + timedelta(seconds=1)).isoformat()}
    transaction = db.transaction
    calls = []

    @contextmanager
    def fail_witness():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError('simulated crash before witness')
        with transaction() as connection:
            yield connection

    monkeypatch.setattr(db, 'transaction', fail_witness)
    with pytest.raises(RuntimeError, match='simulated crash'):
        store.append([event])
    assert store.records(canonical, as_of=clock[0]) == original
    monkeypatch.setattr(db, 'transaction', transaction)
    clock[0] += timedelta(seconds=5)
    assert store.append([event]) == {'created': 0, 'reused': 1}
    assert store.records(canonical, as_of=clock[0] - timedelta(seconds=1)) == original
    recovered = store.records(canonical, as_of=clock[0])
    assert len(recovered) == len(original) + 1
    assert utc(next(r for r in recovered if r['origin_reference'] == event['origin_reference'])['available_at']) == clock[0]


def test_category_filter_is_applied_before_limit_and_invalid_subject_is_atomic(tmp_path):
    data, db, _ = setup(tmp_path)
    now = NOW + timedelta(minutes=1)
    proof = link_report(tmp_path, data, clock=lambda: now)
    store = FixtureContexts(db, clock=lambda: now)
    canonical = proof['fixture_links']['api_football:1']
    rows = store.records(canonical, as_of=now, categories=['lineup'], limit=1)
    assert len(rows) == 1 and rows[0]['category'] == 'lineup'
    bad = without_receipt(rows[0])
    bad['payload']['home_team_id'], bad['payload']['away_team_id'] = bad['payload']['away_team_id'], bad['payload']['home_team_id']
    with pytest.raises(ValueError, match='LINEUP_TEAM_MISMATCH'):
        store.append([bad])


def test_shadow_flag_is_explicit_and_failure_does_not_block_daily_archive(tmp_path, monkeypatch):
    from goal_analysis.jobs import fixture_contexts

    assert fixture_contexts.shadow_archive(tmp_path, {})['status'] == 'DISABLED'
    path = tmp_path / 'config/canonical-contexts.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'enabled': True, 'mode': 'LIVE'}))
    assert fixture_contexts.shadow_archive(tmp_path, {})['status'] == 'DISABLED'
    path.write_text(json.dumps({'enabled': True, 'mode': 'SHADOW'}))
    monkeypatch.setattr(fixture_contexts, 'link_report', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('private-body')))
    outcome = fixture_contexts.shadow_archive(tmp_path, {})
    assert outcome['status'] == 'UNAVAILABLE' and 'private-body' not in json.dumps(outcome)
