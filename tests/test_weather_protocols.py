"""Isolated actual N4 training bytes; synthetic schedule/HTTP/clock, no betting claim."""
import json
import socket
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from reanalysis_sources import install, original_venue_bytes

from goal_analysis.agents import weather_market_validation as market
from goal_analysis.jobs.the_stats_context import Resources
from goal_analysis.normalization.training_datasets import sha
from goal_analysis.providers.the_stats_api import TheStatsApiClient
from goal_analysis.storage import weather_protocols as storage

NOW = datetime(2026, 10, 23, 11, 20, tzinfo=UTC)


@pytest.fixture
def env(tmp_path, monkeypatch):
    result_source = json.loads((Path(__file__).parent/'fixtures/weather-protocols-20261023/original-result-source.json').read_bytes())

    def add_original_result(body):
        body['tables']['raw_snapshots'].append(result_source['row'])
        body['tables']['temporal_receipts'].append(result_source['receipt'])
        body['raw_files'][result_source['row']['relative_path']] = result_source['content']

    body, database = install(tmp_path, bundle_change=add_original_result)
    original_venue_bytes(monkeypatch, tmp_path)
    original = json.loads(body['tables']['historical_result_versions'][0]['record_json'])
    kickoff = NOW+timedelta(days=1, hours=1)
    target = {'id': 'mt_999000001', 'competition': {'id': original['competition_id']},
        'season': {'id': original['season_id']}, 'utc_date': kickoff.isoformat(),
        'home_team': {'id': original['home_id'], 'name': 'Synthetic scheduled home'},
        'away_team': {'id': original['away_id'], 'name': 'Synthetic scheduled away'},
        'status': 'scheduled', 'is_neutral': False}
    transport = Mock(return_value=(json.dumps({'data': target}).encode(), {}, 200))
    client = TheStatsApiClient('safe-test-key', transport, clock=lambda: NOW, max_requests=1)
    resources = Resources(tmp_path, client, lambda: NOW, 1)
    _, source, error = resources.get('/football/matches/'+target['id'])
    assert error is None
    fixture = {'fixture_id': 'the_stats_api:'+target['id'], 'kickoff': kickoff.isoformat(),
        'home_team_id': 'the_stats_api:'+original['home_id'], 'away_team_id': 'the_stats_api:'+original['away_id']}
    protocol = {'version': market.VERSION, 'family': 'BTTS_FT',
        'competition_id': 'the_stats_api:'+original['competition_id'], 'season_id': original['season_id'],
        'training_end': (NOW+timedelta(minutes=10)).isoformat(),
        'validation_start': (NOW+timedelta(days=1)).isoformat(),
        'validation_end': (NOW+timedelta(days=2)).isoformat(),
        'baseline_model_version': 'synthetic-baseline-implementation-not-certified',
        'weather_model_version': 'synthetic-weather-implementation-not-certified',
        'holdout_fixtures': [fixture]}
    ids = ['the_stats_api:'+original['provider_id']]
    training = storage.training_sources(tmp_path, protocol, ids, as_of=NOW)
    protocol['training_envelopes_sha256'] = sha(training)
    clock = [NOW]
    return {'root': tmp_path, 'db': database, 'protocol': protocol, 'ids': ids, 'training': training,
        'sources': {fixture['fixture_id']: {'source': source, 'is_neutral': False}},
        'clock': clock, 'store': storage.WeatherProtocols(tmp_path, clock=lambda: clock[0]),
        'transport': transport}


def register(e):
    return e['store'].register(e['protocol'], training_ids=e['ids'], holdout_sources=e['sources'])


def row_count(e):
    with closing(sqlite3.connect(e['db'].path)) as db:
        return [db.execute('SELECT count(*) FROM '+t).fetchone()[0]
                for t in ('weather_market_protocols', 'weather_market_protocol_receipts')]


def sql(e, *commands):
    with closing(sqlite3.connect(e['db'].path)) as db:
        for query, args in commands:
            db.execute(query, args)
        db.commit()


def test_prospective_registration_preserves_first_clock_and_real_source_bindings(env):
    value = register(env)
    assert value['protocol_store_verified'] is True and value['model_implementation_verified'] is False
    assert value['forecast_store_verified'] is value['heldout_validation_complete'] is False
    assert value['training'] == env['training'] and row_count(env) == [1, 1]
    assert value['envelope']['times']['available_at'] == NOW.isoformat(timespec='microseconds')
    # Idempotence must keep the first receipt even after the training deadline.
    env['clock'][0] += timedelta(days=3)
    assert register(env) == value and row_count(env) == [1, 1]
    assert env['transport'].call_count == 1


def test_readonly_reconstruction_has_no_network_database_write_or_receipt_creation(env, monkeypatch):
    value = register(env)
    original = sqlite3.connect

    def connect(path, *args, **kwargs):
        assert '?mode=ro' in str(path) and kwargs.get('uri')
        return original(path, *args, **kwargs)

    def blocked(*args, **kwargs):
        pytest.fail('Protocol reader attempted network I/O')

    monkeypatch.setattr(sqlite3, 'connect', connect)
    monkeypatch.setattr(socket, 'socket', blocked)
    assert env['store'].read(value['protocol_id'], as_of=NOW) == value
    assert env['transport'].call_count == 1


@pytest.mark.parametrize('delta,passes', [(-1, False), (0, True), (1, True)])
def test_registration_own_availability_boundary(env, delta, passes):
    value = register(env)
    at = NOW+timedelta(microseconds=delta)
    if passes:
        assert env['store'].read(value['protocol_id'], as_of=at) == value
    else:
        with pytest.raises(ValueError):
            env['store'].read(value['protocol_id'], as_of=at)


def test_receipt_is_after_committed_source_and_not_borrowed_from_registration_start(env):
    clocks = iter([NOW, NOW, NOW+timedelta(seconds=1)])
    store = storage.WeatherProtocols(env['root'], clock=lambda: next(clocks))
    value = store.register(env['protocol'], training_ids=env['ids'], holdout_sources=env['sources'])
    assert value['envelope']['times']['observed_at'] == NOW.isoformat(timespec='microseconds')
    assert value['envelope']['times']['available_at'] == (NOW+timedelta(seconds=1)).isoformat(timespec='microseconds')
    with pytest.raises(ValueError, match='FUTURE_AVAILABLE'):
        store.read(value['protocol_id'], as_of=NOW)


def test_unwitnessed_committed_source_is_unusable_and_explicit_retry_uses_real_later_clock(env):
    clocks = iter([NOW, NOW, NOW+timedelta(hours=1)])
    store = storage.WeatherProtocols(env['root'], clock=lambda: next(clocks))
    with pytest.raises(ValueError, match='TIMELY_RECEIPT_MISSING'):
        store.register(env['protocol'], training_ids=env['ids'], holdout_sources=env['sources'])
    assert row_count(env) == [1, 0]
    identifier = storage._intent(env['protocol'], env['ids'], env['sources'])[0]
    with pytest.raises(ValueError, match='FIRST_RECEIPT_MISSING'):
        env['store'].read(identifier, as_of=NOW)
    assert row_count(env) == [1, 0]
    env['clock'][0] += timedelta(seconds=2)
    value = register(env)
    assert value['envelope']['times']['available_at'] == env['clock'][0].isoformat(timespec='microseconds')
    assert row_count(env) == [1, 1]


def test_registration_after_training_deadline_cannot_retroactively_register(env):
    env['clock'][0] += timedelta(hours=1)
    with pytest.raises(ValueError, match='AFTER_TRAINING'):
        register(env)
    assert env['store'].inspect(as_of=env['clock'][0])['protocols'] == []


@pytest.mark.parametrize('field,value', [
    ('version', 'other'), ('family', 'OU2.5'), ('competition_id', '39'), ('season_id', '2026'),
    ('training_end', (NOW+timedelta(days=2)).isoformat()),
    ('validation_end', (NOW+timedelta(hours=1)).isoformat()),
    ('training_envelopes_sha256', '0'*64), ('baseline_model_version', ''),
    ('weather_model_version', 'synthetic-baseline-implementation-not-certified'),
])
def test_invalid_or_changed_declaration_is_not_accepted(env, field, value):
    env['protocol'][field] = value
    with pytest.raises((ValueError, TypeError, KeyError)):
        register(env)


@pytest.mark.parametrize('field,value', [
    ('fixture_id', 'the_stats_api:mt_999000002'), ('home_team_id', 'the_stats_api:tm_999'),
    ('away_team_id', 'the_stats_api:tm_999'), ('kickoff', (NOW+timedelta(days=1, hours=2)).isoformat()),
])
def test_fixed_cohort_identity_must_match_archived_schedule(env, field, value):
    env['protocol']['holdout_fixtures'][0][field] = value
    with pytest.raises(ValueError):
        register(env)


@pytest.mark.parametrize('field,value', [('is_neutral', None), ('is_neutral', True)])
def test_neutral_venue_cannot_be_guessed(env, field, value):
    next(iter(env['sources'].values()))[field] = value
    with pytest.raises(ValueError):
        register(env)


def test_duplicate_training_or_overlap_with_cohort_is_not_allowed(env):
    with pytest.raises(ValueError, match='EXACT_SOURCE_SELECTION'):
        env['store'].register(env['protocol'], training_ids=env['ids']*2, holdout_sources=env['sources'])
    with pytest.raises(ValueError, match='EXACT_SOURCE_SELECTION'):
        env['store'].register(env['protocol'], training_ids=list(env['sources']), holdout_sources=env['sources'])


def test_missing_or_post_cutoff_training_source_is_not_invented(env):
    with pytest.raises(ValueError, match='REAL_TRAINING_SOURCE_MISSING'):
        storage.training_sources(env['root'], env['protocol'], ['the_stats_api:mt_999'], as_of=NOW)
    with pytest.raises(ValueError, match='REAL_TRAINING_SOURCE_MISSING'):
        storage.training_sources(env['root'], env['protocol'], env['ids'], as_of=NOW-timedelta(hours=1))


def test_half_time_scope_is_not_inferred_from_full_time_weather_training(env):
    env['protocol']['family'] = 'BTTS_H1'
    with pytest.raises(ValueError, match='EXPLICIT_HALF_SCORE_SCOPE'):
        register(env)


@pytest.mark.parametrize('table', ['weather_market_protocols', 'weather_market_protocol_receipts'])
@pytest.mark.parametrize('operation', ['UPDATE', 'DELETE'])
def test_protocol_and_first_receipt_tables_are_append_only(env, table, operation):
    register(env)
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        sql(env, ((f'UPDATE {table} SET protocol_id=protocol_id' if operation == 'UPDATE' else f'DELETE FROM {table}'), ()))
    assert row_count(env) == [1, 1]


@pytest.mark.parametrize('field,value', [('source_sha256', '0'*64), ('receipt_id', '0'*64),
    ('available_at', (NOW-timedelta(days=1)).isoformat())])
def test_tampered_receipt_cannot_certify_a_protocol(env, field, value):
    saved = register(env)
    sql(env, ('DROP TRIGGER weather_market_protocol_receipts_no_update', ()),
        (f'UPDATE weather_market_protocol_receipts SET {field}=?', (value,)))
    with pytest.raises(ValueError, match='SOURCE_RECEIPT_MISMATCH'):
        env['store'].read(saved['protocol_id'], as_of=NOW)


def test_rehashed_caller_envelope_cannot_impersonate_genuine_store_record(env):
    saved = register(env)
    forged = deepcopy(saved['envelope']);forged['payload']['family'] = '1X2_FT'
    forged['payload_sha256'] = sha(forged['payload'])
    with pytest.raises(ValueError):
        market.validate_registered_weather_market(env['root'], sha(forged), forecasts=[], outcomes=[], as_of=NOW)
    assert row_count(env) == [1, 1]


def test_changed_stored_protocol_bytes_cannot_borrow_original_receipt(env):
    saved = register(env)
    sql(env, ('DROP TRIGGER weather_market_protocols_no_update', ()),
        ("UPDATE weather_market_protocols SET protocol_json=replace(protocol_json,'BTTS_FT','1X2_FT')", ()))
    with pytest.raises(ValueError, match='SOURCE_RECEIPT_MISMATCH'):
        env['store'].read(saved['protocol_id'], as_of=NOW)


def test_missing_protocol_receipt_is_not_implicitly_repaired_on_read(env):
    saved = register(env)
    sql(env, ('DROP TRIGGER weather_market_protocol_receipts_no_delete', ()),
        ('DELETE FROM weather_market_protocol_receipts', ()))
    with pytest.raises(ValueError, match='FIRST_RECEIPT_MISSING'):
        env['store'].read(saved['protocol_id'], as_of=NOW)
    assert row_count(env) == [1, 0]


def test_changed_original_schedule_witness_invalidates_reader(env):
    saved = register(env)
    source = next(iter(env['sources'].values()))['source']
    sql(env, ('DROP TRIGGER the_stats_http_witnesses_no_delete', ()),
        ('DELETE FROM the_stats_http_witnesses WHERE response_id=?', (source['http_observation_id'],)))
    with pytest.raises(ValueError):
        env['store'].read(saved['protocol_id'], as_of=NOW)


@pytest.mark.parametrize('which', ['declaration', 'binding', 'training', 'discovery'])
def test_resource_bounds_fail_explicitly(env, monkeypatch, which):
    saved = register(env)
    if which == 'declaration': monkeypatch.setattr(storage, 'MAX_PROTOCOL_BYTES', 8)
    elif which == 'binding': monkeypatch.setattr(storage, 'MAX_BINDING_BYTES', 8)
    elif which == 'training': monkeypatch.setattr(storage, 'MAX_TRAINING', 0)
    else: monkeypatch.setattr(storage, 'MAX_PROTOCOLS', 0)
    with pytest.raises(ValueError):
        if which == 'discovery': env['store'].inspect(as_of=NOW)
        else: env['store'].read(saved['protocol_id'], as_of=NOW)


def test_corrupt_registration_is_reported_not_counted_or_repaired(env):
    register(env)
    sql(env, ('DROP TRIGGER weather_market_protocol_receipts_no_update', ()),
        ("UPDATE weather_market_protocol_receipts SET receipt_id='bad'", ()))
    result = env['store'].inspect(as_of=NOW)
    assert result['protocols'] == [] and 'MISMATCH' in result['gaps'][0]['reason']
    assert row_count(env) == [1, 1]


def test_empty_reader_and_readiness_do_not_create_a_database(tmp_path):
    store = storage.WeatherProtocols(tmp_path)
    assert store.inspect(as_of=NOW)['protocols'] == []
    with pytest.raises(ValueError, match='STORE_MISSING'):
        store.read('0'*64, as_of=NOW)
    assert list(tmp_path.iterdir()) == []


def test_registered_protocol_does_not_authenticate_arbitrary_forecast_or_outcome_stores(env, monkeypatch):
    saved = register(env)
    pure = Mock(return_value={'status': 'SHADOW_LOSS_IMPROVED', 'live_weight_eligible': False})
    monkeypatch.setattr(market, 'validate_weather_market', pure)
    result = market.validate_registered_weather_market(env['root'], saved['protocol_id'],
        forecasts=[], outcomes=[], as_of=NOW+timedelta(days=3))
    assert pure.call_args.args[0] == saved['envelope']
    assert pure.call_args.kwargs['training'] == env['training']
    assert result['status'] == 'FORECAST_AND_OUTCOME_STORE_PROVENANCE_PENDING'
    assert result['heldout_validation_complete'] is result['live_promotion_allowed'] is False
    assert result['rank_multiplier'] == 1 and result['probability_adjustment'] == 0


def test_registration_can_precede_future_holdout_but_evaluation_cannot(env):
    value = register(env)
    assert value['protocol_store_verified']
    with pytest.raises(ValueError, match='CHRONOLOGICAL_HOLDOUT'):
        market.validate_registered_weather_market(env['root'], value['protocol_id'], forecasts=[], outcomes=[], as_of=NOW)


def test_changed_original_weather_bytes_invalidates_registered_training(env):
    saved = register(env)
    rawhash = env['training'][0]['payload']['weather']['raw_payload_hash']
    with closing(sqlite3.connect(env['db'].path)) as db:
        name = db.execute('SELECT relative_path FROM raw_snapshots WHERE content_hash=?', (rawhash,)).fetchone()[0]
    path = env['root']/'data/raw'/name
    path.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='REAL_TRAINING_SOURCE_MISSING'):
        env['store'].read(saved['protocol_id'], as_of=NOW)


def test_inspection_deadline_is_checked_after_the_last_source_read(env, monkeypatch):
    register(env)
    base = storage.monotonic()
    completed = [False]
    original = env['store'].read

    def read(*args, **kwargs):
        value = original(*args, **kwargs)
        completed[0] = True
        return value

    monkeypatch.setattr(env['store'], 'read', read)
    monkeypatch.setattr(storage, 'monotonic', lambda: base+31 if completed[0] else base)
    with pytest.raises(ValueError, match='DISCOVERY_TIME_BUDGET'):
        env['store'].inspect(as_of=NOW)


def test_readiness_counts_only_verified_protocols_and_never_claims_model_validation(env):
    from goal_analysis.jobs.weather_market_readiness import inspect_weather_market_readiness

    register(env)
    result = inspect_weather_market_readiness(env['root'], as_of=NOW)
    rows = {r['family']: r for r in result['markets']}
    assert rows['BTTS_FT']['verified_registered_protocol_count'] == 1
    assert rows['1X2_FT']['verified_registered_protocol_count'] == 0
    assert all(r['heldout_market_validation_complete'] is False for r in rows.values())
    assert result['external_requests'] == result['production_writes'] == 0


def test_protocol_inventory_budget_does_not_hide_result_readiness_or_claim_complete_inventory(env, monkeypatch):
    from goal_analysis.jobs.weather_market_readiness import inspect_weather_market_readiness

    register(env)
    monkeypatch.setattr(storage, 'MAX_PROTOCOLS', 0)
    result = inspect_weather_market_readiness(env['root'], as_of=NOW)
    assert result['protocol_source_gaps'] == ['WEATHER_PROTOCOL_DISCOVERY_BUDGET']
    assert result['source_coverage']['matches_with_reanalysis'] == 1
    assert all(r['protocol_inventory_complete'] is False for r in result['markets'])


def test_caller_mutation_during_source_reads_cannot_change_published_declaration(env, monkeypatch):
    original = deepcopy(env['protocol'])
    build = storage._bindings

    def change_caller(*args, **kwargs):
        env['protocol']['family'] = '1X2_FT'
        return build(*args, **kwargs)

    monkeypatch.setattr(storage, '_bindings', change_caller)
    saved = register(env)
    assert saved['envelope']['payload'] == original
    assert env['protocol']['family'] == '1X2_FT'


@pytest.mark.parametrize('field', ['home_goals', 'away_goals', 'half_home_goals', 'half_away_goals'])
def test_result_label_must_match_original_provider_score_even_if_upstream_normalized_row_differs(env, monkeypatch, field):
    original = storage.load_native_weather_dataset

    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        value['matches'][0][field] = (value['matches'][0][field] or 0) + 1
        return value

    monkeypatch.setattr(storage, 'load_native_weather_dataset', changed)
    with pytest.raises(ValueError, match='RESULT_NORMALIZED_SCORE_MISMATCH'):
        register(env)


def test_original_result_raw_receipt_is_required_not_just_canonical_result_and_weather(env):
    saved = register(env)
    actual = json.loads((Path(__file__).parent/'fixtures/weather-protocols-20261023/original-result-source.json').read_bytes())
    sql(env, ('DROP TRIGGER temporal_receipts_no_delete', ()),
        ("DELETE FROM temporal_receipts WHERE namespace='raw' AND source_key=?", (actual['row']['content_hash'],)))
    with pytest.raises(ValueError, match='UNWITNESSED_SOURCE'):
        env['store'].read(saved['protocol_id'], as_of=NOW)


@pytest.mark.parametrize('where', ['declaration', 'cohort'])
def test_extra_outcome_or_policy_fields_cannot_enter_a_registered_declaration(env, where):
    target = env['protocol'] if where == 'declaration' else env['protocol']['holdout_fixtures'][0]
    target['future_result_or_policy_override'] = 'not a scheduled-source fact'
    with pytest.raises(ValueError, match='EXACT_DECLARATION_SCHEMA'):
        register(env)
