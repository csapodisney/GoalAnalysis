"""Actual availability, strict model admission, chronology and known legacy gap."""
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock

import pytest
from test_temporal_store import NOW, RECEIPT, result, setup_sources

from goal_analysis.jobs.point_in_time import (
    replay_models,
    require_chronological_availability,
    result_model_view,
)
from goal_analysis.models._data import prepare_history
from goal_analysis.normalization.leakage import LookAheadError, model_inputs, require_known_before
from goal_analysis.normalization.training_datasets import sha


def envelope(**times):
    payload = {'home_goals': 2, 'fixture_id': 'synthetic:1'}
    return {'record_id': 'synthetic:1', 'payload': payload, 'payload_sha256': sha(payload),
        'times': {'observed_at': RECEIPT, 'published_at': None, 'ingested_at': None,
                  'available_at': NOW, 'availability_basis': 'SOURCE_PRESENCE_UPPER_BOUND', **times}}


@pytest.mark.parametrize('field', ['observed_at', 'published_at', 'ingested_at', 'available_at'])
def test_one_microsecond_future_knowledge_is_a_hard_failure_before_any_model_call(field):
    value = envelope(**{field: NOW + timedelta(microseconds=1)})
    value['record_id'] = 'synthetic:2'
    model = Mock()
    with pytest.raises(LookAheadError, match='FUTURE_'):
        model(model_inputs([envelope(), value], as_of=NOW))
    model.assert_not_called()


@pytest.mark.parametrize('field', ['observed_at', 'available_at'])
def test_missing_receipt_or_local_witness_is_not_replaced_with_a_guess(field):
    with pytest.raises(LookAheadError, match='UNPROVEN_'):
        model_inputs([envelope(**{field: None})], as_of=NOW)


@pytest.mark.parametrize('field', ['observed_at', 'published_at', 'ingested_at', 'available_at'])
def test_naive_knowledge_timestamp_is_rejected(field):
    with pytest.raises(LookAheadError, match='INVALID_'):
        model_inputs([envelope(**{field: NOW.replace(tzinfo=None)})], as_of=NOW)


def test_equal_cutoff_is_visible_and_caller_mutation_cannot_change_source():
    value = envelope()
    before = deepcopy(value)
    rows = model_inputs([value], as_of=NOW)
    rows[0]['home_goals'] = 99
    assert value == before
    assert model_inputs([value], as_of=NOW)[0]['home_goals'] == 2
    assert model_inputs([], as_of=NOW) == []


def test_future_forecast_target_and_announced_future_event_are_known_information():
    for kind in ('FORECAST_TARGET', 'SOURCE_EVENT'):
        value = envelope(event_time=NOW + timedelta(days=2), event_time_kind=kind)
        assert model_inputs([value], as_of=NOW)
    for kind in ('KICKOFF', 'OBSERVATION_TARGET', 'REANALYSIS_TARGET'):
        with pytest.raises(LookAheadError, match='EVENT_AFTER_RECEIPT'):
            model_inputs([envelope(event_time=NOW + timedelta(days=2), event_time_kind=kind)], as_of=NOW)


def test_earlier_publication_does_not_override_late_download_or_ingestion():
    with pytest.raises(LookAheadError, match='FUTURE_OBSERVED_AT'):
        model_inputs([envelope(published_at=RECEIPT, observed_at=NOW + timedelta(days=1))], as_of=NOW)
    with pytest.raises(LookAheadError, match='INVALID_INGESTION_ORDER'):
        require_known_before(envelope(ingested_at=RECEIPT - timedelta(seconds=1))['times'], as_of=NOW)
    with pytest.raises(LookAheadError, match='PUBLICATION_AFTER_RECEIPT'):
        require_known_before(envelope(published_at=NOW)['times'], as_of=NOW)


def test_payload_hash_and_duplicate_identity_cannot_borrow_good_timestamp_evidence():
    value = envelope()
    value['payload']['home_goals'] = 3
    with pytest.raises(LookAheadError, match='HASH_MISMATCH'):
        model_inputs([value], as_of=NOW)
    with pytest.raises(LookAheadError, match='DUPLICATE_'):
        model_inputs([envelope(), envelope()], as_of=NOW)
    with pytest.raises(LookAheadError, match='AVAILABILITY_BASIS'):
        model_inputs([envelope(availability_basis='KICKOFF_PLUS_3H_ASSUMPTION')], as_of=NOW)


def test_payload_timestamp_cannot_disagree_with_the_good_envelope_even_after_rehash():
    value = envelope()
    value['payload']['observed_at'] = (NOW + timedelta(days=1)).isoformat()
    value['payload_sha256'] = sha(value['payload'])
    with pytest.raises(LookAheadError, match='PAYLOAD_TIME_DIFFERS'):
        model_inputs([value], as_of=NOW)


def prepared_source(tmp_path):
    root = tmp_path
    database, results, _, _, _, temporal = setup_sources(root / 'data')
    # setup_sources uses sports.sqlite3; the production path must be explicit.
    import sqlite3
    from contextlib import closing

    results.ingest([result()])
    temporal.capture('result')
    target = root / 'data/goal_analysis.sqlite3'
    with closing(database.connect()) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    return root


def test_proven_result_view_is_read_only_deterministic_and_visible_at_actual_witness(tmp_path):
    root = prepared_source(tmp_path)
    database = root / 'data/goal_analysis.sqlite3'
    before = database.read_bytes()
    old = result_model_view(root, as_of=NOW - timedelta(microseconds=1))
    assert old['records'] == [] and old['gaps']['MISSING_AVAILABLE_AT_WITNESS'] == 1
    ready = result_model_view(root, as_of=NOW)
    assert ready == result_model_view(root, as_of=NOW)
    assert ready['mode'] == 'SHADOW' and len(ready['records']) == 1
    assert ready['records'][0]['fixture_id'] == 'mt_1'
    assert ready['dataset']['manifest']['row_count'] == 1
    assert database.read_bytes() == before and ready['external_requests'] == ready['ledger_writes'] == 0


def test_older_replay_does_not_see_later_correction_or_new_receipt(tmp_path):
    root = prepared_source(tmp_path)
    from goal_analysis.storage import Database
    from goal_analysis.storage.historical_results import HistoricalResults
    from goal_analysis.storage.temporal import TemporalStore

    original = result_model_view(root, as_of=NOW)
    database = Database(root / 'data/goal_analysis.sqlite3')
    later = NOW + timedelta(days=1)
    corrected = result()
    corrected.update(observed_at=later.isoformat(), home_goals=5)
    HistoricalResults(database, clock=lambda: later).ingest([corrected])
    TemporalStore(database, root / 'data/raw', clock=lambda: later).capture('result')
    assert result_model_view(root, as_of=NOW) == original
    assert result_model_view(root, as_of=later)['records'][0]['full_time_home'] == 5


def test_missing_archive_and_limit_are_explicit(tmp_path):
    assert result_model_view(tmp_path, as_of=NOW)['status'] == 'MISSING_SOURCE'
    root = prepared_source(tmp_path)
    assert result_model_view(root, as_of=NOW, limit=1)['status'] == 'AVAILABLE'


def test_unrelated_csv_receipts_cannot_starve_native_model_history_and_limit_is_recent(tmp_path):
    root = prepared_source(tmp_path)
    from goal_analysis.storage import Database
    from goal_analysis.storage.historical_results import HistoricalResults
    from goal_analysis.storage.temporal import TemporalStore

    db = Database(root / 'data/goal_analysis.sqlite3')
    results = HistoricalResults(db, clock=lambda: RECEIPT)
    csv = {**result(8), 'provider':'football_data_co_uk', 'provider_id':'csv-8', 'kickoff':None,
           'home_team':'CSV Home', 'away_team':'CSV Away', 'score_scope':'SOURCE_FULL_TIME_UNVERIFIED'}
    results.ingest([csv])
    temporal = TemporalStore(db, root / 'data/raw', clock=lambda: NOW)
    temporal.capture('result')
    view = result_model_view(root, as_of=NOW, limit=1)
    assert len(view['records']) == 1 and view['records'][0]['fixture_id'] == 'mt_1'
    later = result(2)
    time = RECEIPT - timedelta(hours=4)
    later.update(kickoff=time.isoformat(), match_date=time.date().isoformat())
    results.ingest([later])
    temporal.capture('result')
    view = result_model_view(root, as_of=NOW, limit=1)
    assert view['status'] == 'LIMIT_REACHED' and view['gaps']['CANDIDATE_LIMIT'] == 1
    assert view['records'][0]['fixture_id'] == 'mt_2'


@pytest.mark.parametrize('ids', [['not-a-hash'], ['a'*64, 'a'*64], [True], 'a'*64])
def test_result_receipt_filter_rejects_malformed_or_duplicate_identities(tmp_path, ids):
    from goal_analysis.storage import Database
    from goal_analysis.storage.temporal import TemporalStore

    root = prepared_source(tmp_path)
    store = TemporalStore(Database(root / 'data/goal_analysis.sqlite3'), root / 'data/raw')
    with pytest.raises(ValueError, match='INVALID_TEMPORAL_RESULT_SELECTION'):
        store.records('result', NOW, result_version_ids=ids)
    assert store.records('result', NOW, result_version_ids=[]) == []


def test_legacy_boundary_gap_is_exposed_not_misreported_as_point_in_time_safe():
    # Characterize the unpromoted live path; do not bless it as a strict replay.
    row = {'fixture_id':'1', 'league_id':'1', 'home_team_id':'1', 'away_team_id':'2',
        'kickoff':(NOW - timedelta(days=4)).isoformat(), 'observed_at':RECEIPT.isoformat(),
        'full_time_home':2, 'full_time_away':1, 'status':'FT', 'source_id':'synthetic:gap',
        'available_at':(NOW + timedelta(days=1)).isoformat()}
    admitted, _ = prepare_history([row], NOW, set())
    assert len(admitted) == 1  # Concrete remaining defect: the live path is not promoted.
    with pytest.raises(LookAheadError, match='FUTURE_AVAILABLE_AT'):
        model_inputs([envelope(available_at=row['available_at'])], as_of=NOW)


def test_chronological_features_need_actual_prior_availability_not_a_delayed_backfill():
    first = {'league_id':'comp_1', 'kickoff':NOW - timedelta(days=4),
             'available_at':NOW - timedelta(days=4) + timedelta(hours=4),
             'observed_at':NOW - timedelta(days=4) + timedelta(hours=3)}
    second = {**first, 'kickoff':NOW - timedelta(days=2),
              'available_at':NOW - timedelta(days=2) + timedelta(hours=4),
              'observed_at':NOW - timedelta(days=2) + timedelta(hours=3)}
    require_chronological_availability([second, first])
    with pytest.raises(LookAheadError, match='CHRONOLOGICAL_FEATURE'):
        require_chronological_availability([{**first, 'available_at': NOW}, second])
    # Different leagues and simultaneous/overlapping matches are not earlier clocks.
    require_chronological_availability([{**first, 'available_at': NOW},
                                       {**second, 'league_id':'comp_2'}])
    require_chronological_availability([{**first, 'available_at': NOW},
                                       {**second, 'kickoff':first['kickoff'] + timedelta(hours=3)}])


def test_strict_shadow_entry_point_passes_only_verified_rows_to_actual_numerical_models(tmp_path):
    root = prepared_source(tmp_path)
    value = envelope()
    value['payload'] = {'fixture_id':'the_stats_api:mt_900', 'source_provider':'the_stats_api',
        'provider_fixture_id':'mt_900', 'provider_league_id':'comp_1', 'provider_season_id':'sn_1',
        'provider_home_team_id':'tm_1', 'provider_away_team_id':'tm_2',
        'kickoff':(NOW + timedelta(hours=10)).isoformat(), 'provider_status':'NS'}
    value['payload_sha256'] = sha(value['payload'])
    output = replay_models(root, [value], as_of=NOW)
    assert output['mode'] == 'SHADOW' and output['source_status'] == 'AVAILABLE'
    assert all(r['status'] == 'INSUFFICIENT_HISTORY' and not r['predictions']
               for r in output['numeric_models'].values())
    model = Mock()
    value['times']['available_at'] = NOW + timedelta(microseconds=1)
    with pytest.raises(LookAheadError, match='FUTURE_AVAILABLE_AT'):
        replay_models(root, [value], as_of=NOW, predictor=model)
    model.assert_not_called()


def test_unproven_pi_history_is_withheld_before_the_strict_shadow_model_is_called(tmp_path):
    root = prepared_source(tmp_path)
    from goal_analysis.storage import Database
    from goal_analysis.storage.historical_results import HistoricalResults
    from goal_analysis.storage.temporal import TemporalStore

    db = Database(root / 'data/goal_analysis.sqlite3')
    other = result(2)
    second_time = RECEIPT - timedelta(hours=1)
    other.update(kickoff=second_time.isoformat(), match_date=second_time.date().isoformat())
    HistoricalResults(db, clock=lambda: RECEIPT).ingest([other])
    TemporalStore(db, root / 'data/raw', clock=lambda: NOW).capture('result')
    model = Mock()
    output = replay_models(root, [], as_of=NOW, profiles=('KEPLER',), predictor=model)
    model.assert_not_called()
    assert output['numeric_models']['kepler']['status'] == 'UNPROVEN_POINT_IN_TIME_HISTORY'
