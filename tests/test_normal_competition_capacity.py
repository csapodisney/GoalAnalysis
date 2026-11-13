"""TESTONLY explicit capacity handoff; no actual model, provider, DB or fit."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs import normal_history_input as inputs
from goal_analysis.jobs.native_history_compaction import HistoryCompactor
from goal_analysis.jobs.portfolio import _model_failure_diagnostic
from goal_analysis.normalization.training_datasets import sha, snapshot, validate

NOW = datetime(2026, 11, 13, 21, 44, tzinfo=UTC)


def fixture(number=900001, competition='comp_1'):
    return {'fixture_id': f'the_stats_api:mt_{number}', 'source_provider': 'the_stats_api',
        'provider_fixture_id': f'mt_{number}', 'provider_league_id': competition,
        'provider_season_id': 'sn_1', 'provider_home_team_id': 'tm_1', 'provider_away_team_id': 'tm_2',
        'provider_status': 'NS', 'kickoff': (NOW + timedelta(days=1)).isoformat(),
        'neutral_venue': False, 'fixture_source': {'content_hash': 'f' * 64, 'source_url': 'TESTONLY_SOURCE'}}


def row(number, competition='comp_1', **changes):
    return {'provider': 'the_stats_api', 'fixture_id': f'mt_{number}', 'league_id': competition,
        'season': 'sn_1', 'home_team_id': 'tm_1', 'away_team_id': 'tm_2', 'status': 'FT',
        'kickoff': (NOW - timedelta(days=10 + number % 100)).isoformat(),
        'observed_at': (NOW - timedelta(hours=2)).isoformat(), 'source_id': f'TESTONLY:{number}',
        'neutral_venue': False, 'full_time_home': 2, 'full_time_away': 1, **changes}


def predictor(calls):
    def fake(targets, history, now, *, enabled_profiles, artifact_dir):
        profile = enabled_profiles[0]
        calls.append({'profile': profile, 'competition': inputs._comp(targets[0]),
                      'targets': deepcopy(targets), 'history': deepcopy(history), 'cutoff': now})
        return {profile: {'profile_id': profile, 'status': 'INSUFFICIENT_HISTORY', 'predictions': [],
                          'diagnostics': [{'code': 'TESTONLY_PREDICTOR_HANDOFF_NO_FIT'}]}}
    return fake


def retained_view(targets, rows):
    compactor = HistoryCompactor()
    for index, value in enumerate(rows):
        times = {key: value[key] for key in ('observed_at', 'available_at', 'ingested_at', 'published_at')}
        times.update(availability_basis='SOURCE_PRESENCE_UPPER_BOUND', event_time=None, event_time_kind='UNKNOWN')
        compactor.add({'record_id': f'TESTONLY:{index}', 'payload': deepcopy(value),
            'payload_sha256': sha(value), 'times': times, 'receipt_id': f'TESTONLY:r:{index}',
            'source_key': f'TESTONLY:raw:{index}', 'source_sha256': sha(value)})
    dataset = snapshot([], role='TESTONLY_RETAINED', as_of=NOW, recipe={'testonly': True},
                       lineage={'lossless_history_aliases': compactor.aliases()})
    return {'status': 'AVAILABLE', 'as_of': NOW.isoformat(), 'fixtures': deepcopy(targets),
            'history': [], 'dataset': dataset, 'gaps': {}, 'source_count': len(rows)}


def test_actual_10000_limit_accepts_all_10008_distinct_truths_across_two_competitions():
    targets = [fixture(), fixture(900002, 'comp_2')]
    history = [row(index, 'comp_1' if index <= 5004 else 'comp_2') for index in range(1, 10009)]
    original_hash, calls = sha(history), []
    result = inputs.predict_normal(targets, history, NOW, predictor=predictor(calls), competition_capacity=True)
    view = result['input_view']
    assert inputs.MAX_TRUTHS == 10000
    assert view['competition_capacity']['global_truth_count'] == 10008
    assert view['competition_capacity']['blocked_competition_ids'] == []
    assert {value['truth_count'] for value in view['competition_capacity']['competitions'].values()} == {5004}
    assert len(view['history']) == validate(view['dataset'])['manifest']['row_count'] == 10008
    assert len(calls) == 6 and all(len(call['history']) == 5004 for call in calls)
    expected = {item['fixture_id']: item for item in history}
    assert all(item == expected[item['fixture_id']] for call in calls for item in call['history'])
    assert all(call['cutoff'] == NOW for call in calls)
    assert len(view['dataset']['lineage']['input_partition']) == 10008 and sha(history) == original_hash


def test_oversized_competition_is_wholly_unavailable_and_other_three_profiles_continue(monkeypatch):
    monkeypatch.setattr(inputs, 'MAX_TRUTHS', 2)
    history = [row(1), row(2), row(3), row(4, 'comp_2'), row(5, 'comp_2')]
    original = deepcopy(history)
    calls = []
    result = inputs.predict_normal([fixture(), fixture(900002, 'comp_2')], history, NOW,
                                  predictor=predictor(calls), competition_capacity=True)
    assert len(calls) == 3 and {call['competition'] for call in calls} == {'comp_2'}
    for profile in inputs.PROFILES:
        blocked = result['numeric_models'][profile]['competition_results']['comp_1']
        assert blocked['status'] == 'INPUT_CAPACITY_UNAVAILABLE' and blocked['predictions'] == []
        assert blocked['diagnostics'][0]['truth_count'] == 3
        assert blocked['diagnostics'][0]['predictor_invoked'] is False
        assert result['numeric_models'][profile]['competition_results']['comp_2']['status'] == 'INSUFFICIENT_HISTORY'
    uninvoked = [use for use in result['model_input_uses'] if use['competition_id'] == 'comp_1']
    assert len(uninvoked) == 3
    assert all(use['history_rows'] is None and use['history_sha256'] is None
               and use['predictor_invoked'] is False for use in uninvoked)
    view = result['input_view']
    assert {item['fixture_id'] for item in view['history']} == {'mt_4', 'mt_5'}
    assert view['competition_capacity']['global_row_count'] == 5
    assert len(view['competition_capacity']['withheld_input_records']) == 3
    assert view['partition_counts']['COMPETITION_TRUTH_BOUND_EXCEEDED_NO_FIRST_N'] == 3
    assert view['selection']['selected_history_rows'] == 5  # Original sample, before explicit capacity stage.
    assert view['competition_capacity']['consumed_rows_after_capacity'] == 2 and history == original


def test_global_cross_competition_conflicts_and_unknown_versions_survive_capacity_isolation(monkeypatch):
    monkeypatch.setattr(inputs, 'MAX_TRUTHS', 3)
    history = [row(1), row(1, 'comp_2'), row(5, neutral_venue=None), row(5, 'comp_2'),
               row(2), row(3), row(9, 'comp_2')]
    result = inputs.prepare_inputs([fixture(), fixture(900002, 'comp_2')], history, NOW,
                                   competition_capacity=True)
    assert result['competition_capacity']['blocked_competition_ids'] == ['comp_1']
    assert [item['fixture_id'] for item in result['history']] == ['mt_9']
    assert any(item.get('code') == 'CONFLICTING_HISTORY' and item.get('fixture_id') == 'mt_1'
               for item in result['selection']['history_diagnostics'])
    assert 'mt_5' in result['selection']['unknown_eligible_history_ids_quarantined']
    assert result['partition_counts']['CONFLICTING_IDENTITY'] == 2
    assert result['partition_counts']['UNKNOWN_VENUE_IDENTITY'] == 2


def test_all_target_ids_and_original_clock_unknown_venue_guards_remain_active():
    history = [row(1), row(900002), row(2, available_at=(NOW + timedelta(seconds=1)).isoformat()),
               row(3, observed_at='2026-11-13T20:00:00'), row(4, neutral_venue=None)]
    original = deepcopy(history)
    result = inputs.prepare_inputs([fixture(), fixture(900002, 'comp_2')], history, NOW,
                                   competition_capacity=True)
    assert [item['fixture_id'] for item in result['history']] == ['mt_1']
    assert result['partition_counts']['FUTURE_KNOWLEDGE'] == 1
    assert result['partition_counts']['INVALID_KNOWLEDGE_CLOCK'] == 1
    assert result['partition_counts']['UNKNOWN_VENUE_IDENTITY'] == 1
    assert any(item['code'] == 'TARGET_HISTORY_EXCLUDED' for item in result['selection']['history_diagnostics'])
    assert history == original


def test_retained_reader_called_once_for_full_calendar_and_all_aliases_remain_exact(monkeypatch):
    monkeypatch.setattr(inputs, 'MAX_TRUTHS', 2)
    targets = [fixture(), fixture(900002, 'comp_2')]
    clocks = {'available_at': (NOW - timedelta(hours=1)).isoformat(),
              'ingested_at': (NOW - timedelta(minutes=90)).isoformat(), 'published_at': None}
    first = row(2, **clocks)
    alias = {**first, 'source_id': 'TESTONLY:alias', 'observed_at': (NOW - timedelta(minutes=100)).isoformat()}
    retained = [first, alias, row(3, 'comp_2', **clocks), row(4, 'comp_2', **clocks)]
    supplied = retained_view(targets, retained)
    reader_calls = []

    def reader(all_targets, *, as_of):
        reader_calls.append((deepcopy(all_targets), as_of))
        return supplied

    result = inputs.prepare_inputs(targets, [row(1)], NOW, retained_reader=reader, competition_capacity=True)
    assert reader_calls == [(targets, NOW)]
    assert result['competition_capacity']['global_truth_count'] == 4
    assert result['competition_capacity']['blocked_competition_ids'] == []
    assert len(result['history']) == 4 and len(result['dataset']['lineage']['input_partition']) == 5
    assert result['partition_counts']['SELECTED_STANDARD_TRUTH_ALIAS'] == 5
    selected = next(value for value in result['history'] if value['fixture_id'] == 'mt_2')
    assert selected == first and selected['available_at'] == first['available_at']
    assert supplied == retained_view(targets, retained)


def test_legacy_global_contract_and_total_raw_alias_guard_are_not_relaxed(monkeypatch):
    monkeypatch.setattr(inputs, 'MAX_TRUTHS', 2)
    targets = [fixture(), fixture(900002, 'comp_2')]
    history = [row(1), row(2), row(3, 'comp_2')]
    with pytest.raises(ValueError, match='ONLINE_TRUTH_BOUND_EXCEEDED_NO_FIRST_N'):
        inputs.prepare_inputs(targets, history, NOW)
    assert len(inputs.prepare_inputs(targets, history, NOW, competition_capacity=True)['history']) == 3
    with pytest.raises(TypeError, match='EXPLICIT_COMPETITION_CAPACITY_BOOLEAN_REQUIRED'):
        inputs.prepare_inputs(targets, history, NOW, competition_capacity='true')
    monkeypatch.setattr(inputs, 'MAX_RAW_ALIASES', 2)
    with pytest.raises(ValueError, match='NORMAL_HISTORY_INPUT_BOUND'):
        inputs.prepare_inputs(targets, history, NOW, competition_capacity=True)


def test_unknown_competition_is_a_local_record_gap_without_guessing_or_global_veto():
    invalid = row(2, 'NOT_A_NATIVE_COMPETITION')
    history = [row(1), invalid]
    result = inputs.prepare_inputs([fixture()], history, NOW, competition_capacity=True)
    assert result['history'] == [row(1)]
    capacity = result['competition_capacity']
    assert capacity['global_row_count'] == 2 and capacity['unknown_competition_row_count'] == 1
    assert capacity['unknown_competition_rows'][0]['row_sha256'] == sha(invalid)
    assert result['partition_counts']['UNKNOWN_CANONICAL_COMPETITION'] == 1
    assert len(result['dataset']['lineage']['input_partition']) == 2
    assert history[1] == invalid


def test_outer_failure_keeps_bounded_stage_class_but_never_secret_exception_text():
    secret = 'TESTONLY password=DO_NOT_RETAIN C:/private/provider/secret'
    error = RuntimeError(secret)
    diagnostic = _model_failure_diagnostic('VENUE_BINDING', error)
    assert diagnostic['stage'] == 'VENUE_BINDING' and diagnostic['failure_class'] == 'RuntimeError'
    assert diagnostic['code'] == 'MODEL_RUN_FAILED' and diagnostic['exception_message_retained'] is False
    assert secret not in str(diagnostic) and 'DO_NOT_RETAIN' not in str(diagnostic)
    unknown = _model_failure_diagnostic(secret, error)
    assert unknown['stage'] == 'UNKNOWN_MODEL_STAGE' and 'DO_NOT_RETAIN' not in str(unknown)
