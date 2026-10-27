"""ARTIFACT behavior matrix. All sources/receipts/predictions below are TESTONLY.

No fits, database, HTTP or provider operations; actual numerical treatment is a
separate mandatory acceptance step. This file has not been executed by its author.
"""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs import normal_history_input as candidate
from goal_analysis.jobs.native_history_compaction import HistoryCompactor
from goal_analysis.normalization.training_datasets import sha, snapshot

NOW = datetime(2026, 10, 27, 1, tzinfo=UTC)

def fixture(number=900, comp='comp_1', neutral=False):
    return {'fixture_id': 'the_stats_api:mt_' + str(number), 'source_provider': 'the_stats_api', 'provider_fixture_id': 'mt_' + str(number), 'provider_league_id': comp, 'provider_season_id': 'sn_1', 'provider_home_team_id': 'tm_1', 'provider_away_team_id': 'tm_2', 'provider_status': 'NS', 'kickoff': (NOW + timedelta(hours=3)).isoformat(), 'neutral_venue': neutral, 'fixture_source': {'content_hash': 'f' * 64, 'source_url': 'TESTONLY_SOURCE'}}

def row(number=1, comp='comp_1', neutral=False, *, witnessed=False):
    value = {'provider': 'the_stats_api', 'fixture_id': 'mt_' + str(number), 'league_id': comp, 'season': 'sn_1', 'home_team_id': 'tm_1', 'away_team_id': 'tm_2', 'status': 'FT', 'kickoff': (NOW - timedelta(days=10 + number)).isoformat(), 'observed_at': (NOW - timedelta(hours=2)).isoformat(), 'source_id': 'TESTONLY:' + str(number), 'neutral_venue': neutral, 'full_time_home': 2, 'full_time_away': 1}
    if witnessed:
        value.update(available_at=(NOW - timedelta(hours=1)).isoformat(), ingested_at=(NOW - timedelta(minutes=90)).isoformat(), published_at=None)
    return value

def view(targets, rows):
    compactor = HistoryCompactor()
    for number, value in enumerate(rows):
        assert all(k in value for k in ('observed_at', 'available_at', 'ingested_at', 'published_at', 'source_id'))
        times = {k: value[k] for k in ('observed_at', 'available_at', 'ingested_at', 'published_at')}
        times.update(availability_basis='SOURCE_PRESENCE_UPPER_BOUND', event_time=None, event_time_kind='UNKNOWN')
        envelope = {'record_id': 'TESTONLY:' + str(number), 'payload': deepcopy(value), 'payload_sha256': sha(value), 'times': times, 'receipt_id': 'TESTONLY_RECEIPT:' + str(number), 'source_key': 'TESTONLY_RAW:' + str(number), 'source_sha256': sha(value)}
        compactor.add(envelope)
    dataset = snapshot([], role='TESTONLY_READER_DATASET', as_of=NOW, recipe={'testonly': True}, lineage={'lossless_history_aliases': compactor.aliases()})
    return {'status': 'AVAILABLE', 'as_of': NOW.isoformat(), 'fixtures': deepcopy(targets), 'history': [], 'dataset': dataset, 'gaps': {}, 'source_count': len(rows)}

def test_online_standard_partition_keeps_unknown_and_neutral_diagnostics():
    original = [row(1), row(2, neutral=True), row(3, neutral=None)]
    result = candidate.prepare_inputs([fixture()], original, NOW)
    assert [r['fixture_id'] for r in result['history']] == ['mt_1']
    assert result['selection']['isolated_neutral_ids'] == ['mt_2']
    assert result['selection']['unknown_eligible_history_ids_quarantined'] == ['mt_3']
    assert original[2]['neutral_venue'] is None and result['retained_reader_calls'] == 0

def test_single_whole_universe_reader_union_preserves_exact_original_clocks_and_is_detached():
    targets = [fixture(), fixture(901, 'comp_2')]
    retained = [row(2, witnessed=True), row(3, 'comp_2', witnessed=True)]
    supplied = view(targets, retained)
    calls = []

    def reader(values, *, as_of):
        calls.append((deepcopy(values), as_of))
        return supplied
    result = candidate.prepare_inputs(targets, [row(1)], NOW, retained_reader=reader)
    assert len(calls) == 1 and calls[0][0] == targets
    actual = {r['fixture_id']: r for r in result['history']}
    assert actual['mt_2'] == retained[0] and actual['mt_3'] == retained[1]
    assert result['dataset']['manifest']['row_count'] == 3
    actual['mt_2']['available_at'] = 'MUTATED_TESTONLY'
    assert supplied['dataset'] == view(targets, retained)['dataset']

@pytest.mark.parametrize('change', [{'neutral_venue': True}, {'neutral_venue': None}, {'full_time_home': 3}])
def test_full_reconstructed_truth_conflicts_before_filtered_history_or_venue_partition(change):
    target = fixture()
    retained = {**row(1, witnessed=True), **change}
    result = candidate.prepare_inputs([target], [row(1)], NOW, retained_reader=lambda *_a, **_k: view([target], [retained]))
    assert not result['history'] and (not result['online_history'])
    assert result['dataset']['manifest']['row_count'] == 0
    assert any(e['partition'] in {'CONFLICTING_IDENTITY', 'UNKNOWN_VENUE_IDENTITY'} for e in result['dataset']['lineage']['input_partition'])

def test_identical_retained_aliases_never_multiply_the_sample_or_graft_provenance():
    target = fixture()
    first = row(1, witnessed=True)
    second = {**first, 'source_id': 'TESTONLY:LATER', 'observed_at': (NOW - timedelta(minutes=100)).isoformat()}
    result = candidate.prepare_inputs([target], [], NOW, retained_reader=lambda *_a, **_k: view([target], [first, second]))
    assert result['history'] == [first]
    assert result['history'][0]['available_at'] == first['available_at']
    assert result['partition_counts']['SELECTED_STANDARD_TRUTH_ALIAS'] == 2

@pytest.mark.parametrize('fault', ['LIMIT_REACHED', 'cutoff', 'dataset_hash', 'unproven_target', 'exception'])
def test_bad_or_incomplete_retained_reader_keeps_complete_valid_online_baseline(fault):
    target = fixture()

    def reader(*_a, **_k):
        if fault == 'exception':
            raise OSError('TESTONLY_PRIVATE_PROVIDER_MESSAGE')
        supplied = view([target], [row(2, witnessed=True)])
        if fault == 'LIMIT_REACHED':
            supplied['status'] = 'LIMIT_REACHED'
            supplied['gaps'] = {'SOURCE_LIMIT': 1}
        elif fault == 'cutoff':
            supplied['as_of'] = (NOW - timedelta(seconds=1)).isoformat()
        elif fault == 'dataset_hash':
            supplied['dataset']['manifest']['rows_sha256'] = '0' * 64
        else:
            supplied['fixtures'] = []
        return supplied
    result = candidate.prepare_inputs([target], [row(1)], NOW, retained_reader=reader)
    assert result['history'] == [row(1)] and result['online_history'] == [row(1)]
    assert result['reader_diagnostics'][0]['code'] in {'RETAINED_NOT_ADMITTED', 'RETAINED_READER_UNAVAILABLE'}
    assert 'TESTONLY_PRIVATE_PROVIDER_MESSAGE' not in str(result)

def test_future_online_available_clock_does_not_quarantine_older_valid_truth():
    late = {**row(1, neutral=None, witnessed=True), 'available_at': (NOW + timedelta(seconds=1)).isoformat()}
    result = candidate.prepare_inputs([fixture()], [row(1), late], NOW)
    assert result['history'] == [row(1)]
    assert result['partition_counts']['FUTURE_KNOWLEDGE'] == 1

def test_unknown_target_remains_unknown_and_not_a_false_home_advantage_claim():
    target = fixture(neutral=None)
    result = candidate.prepare_inputs([target], [row(1)], NOW)
    assert result['fixtures'][0]['neutral_venue'] is None
    assert result['selection']['unknown_target_ids'] == [target['fixture_id']]

def spy_predictor(calls, fail=None):

    def predict(targets, rows, now, *, enabled_profiles, artifact_dir):
        profile = enabled_profiles[0]
        comp = candidate._comp(targets[0])
        calls.append({'profile': profile, 'comp': comp, 'history': deepcopy(rows), 'now': now, 'path': artifact_dir})
        if fail == (profile, comp):
            raise RuntimeError('TESTONLY_PRIVATE_LOCAL_ERROR')
        return {profile: {'profile_id': profile, 'status': 'INSUFFICIENT_HISTORY', 'predictions': [], 'diagnostics': [{'code': 'TESTONLY_NO_NUMERIC_FIT'}]}}
    return predict

def test_unproven_retained_kepler_chronology_preserves_online_approximation_and_other_models():
    target = fixture()
    online = [row(1), row(2)]
    retained = [row(3, witnessed=True), row(4, witnessed=True)]
    calls = []
    result = candidate.predict_normal([target], online, NOW, predictor=spy_predictor(calls), retained_reader=lambda *_a, **_k: view([target], retained))
    data = {c['profile']: c['history'] for c in calls}
    assert len(data['gauss']) == len(data['laplace']) == 4
    assert data['kepler'] == result['input_view']['online_history'] and len(data['kepler']) == 2
    use = next(u for u in result['model_input_uses'] if u['profile_id'] == 'kepler')
    assert use['basis'] == 'RETAINED_CHRONOLOGY_UNPROVEN_ORIGINAL_ONLINE_APPROXIMATION_FALLBACK'
    assert not result['input_view']['historical_feature_availability_certified']

def test_chronologically_proven_retained_new_rows_can_enter_kepler_without_forging_online_times():
    online = [row(1)]
    retained = [row(3, witnessed=True), row(4, witnessed=True)]
    for value in retained:
        observed = candidate.aware_time(value['kickoff']) + timedelta(hours=4)
        value.update(observed_at=observed.isoformat(), ingested_at=(observed + timedelta(minutes=1)).isoformat(), available_at=(observed + timedelta(minutes=2)).isoformat())
    selected, basis = candidate._kepler_inputs(retained + online, online)
    assert selected == retained + online
    assert basis == 'RETAINED_CHRONOLOGY_VERIFIED_ONLINE_APPROXIMATION_NOT_CERTIFIED_PIT'
    assert 'available_at' not in online[0]

def test_competition_and_profile_failures_do_not_stop_other_unchanged_model_calls():
    targets = [fixture(), fixture(901, 'comp_2')]
    calls = []
    result = candidate.predict_normal(targets, [row(1), row(2, 'comp_2')], NOW, predictor=spy_predictor(calls, ('gauss', 'comp_1')))
    assert len(calls) == 6
    assert result['numeric_models']['gauss']['competition_results']['comp_1']['status'] == 'MODEL_ERROR'
    assert result['numeric_models']['gauss']['competition_results']['comp_2']['status'] == 'INSUFFICIENT_HISTORY'
    assert result['numeric_models']['laplace']['competition_results']['comp_1']['status'] == 'INSUFFICIENT_HISTORY'
    assert 'TESTONLY_PRIVATE_LOCAL_ERROR' not in str(result)

def test_union_budget_failure_discards_whole_retained_scope_not_a_favorable_first_n(monkeypatch):
    monkeypatch.setattr(candidate, 'MAX_TRUTHS', 2)
    target = fixture()
    result = candidate.prepare_inputs([target], [row(1), row(2)], NOW, retained_reader=lambda *_a, **_k: view([target], [row(3, witnessed=True)]))
    assert {_['fixture_id'] for _ in result['history']} == {'mt_1', 'mt_2'}
    assert result['reader_diagnostics'][0]['reason'] == 'UNION_TRUTH_BOUND_EXCEEDED_NO_FIRST_N'

@pytest.mark.parametrize('malformed', [None, [], 'not-a-row', 12])
def test_one_malformed_online_row_is_local_and_valid_other_history_remains(malformed):
    result = candidate.prepare_inputs([fixture()], [malformed, row(1)], NOW)
    assert result['history'] == [row(1)]
    assert result['partition_counts']['MALFORMED_ONLINE_ROW'] == 1
    assert len(result['dataset']['lineage']['input_partition']) == 2

def test_noncanonical_extra_data_cannot_poison_other_online_history():
    bad = {**row(2), 'TESTONLY_invalid_extra': object()}
    result = candidate.prepare_inputs([fixture()], [bad, row(1)], NOW)
    assert result['history'] == [row(1)]
    assert result['partition_counts']['NONCANONICAL_ONLINE_ROW'] == 1

@pytest.mark.parametrize('bad_id', [[], {}, object()])
def test_malformed_history_identity_remains_local_diagnostic(bad_id):
    result = candidate.prepare_inputs([fixture()], [{**row(2), 'fixture_id': bad_id}, row(1)], NOW)
    assert result['history'] == [row(1)]
    assert len(result['dataset']['lineage']['input_partition']) == 2

def test_rehashed_retained_dataset_with_another_cutoff_is_not_current_input():
    target = fixture()
    supplied = view([target], [row(2, witnessed=True)])
    old = supplied['dataset']
    supplied['dataset'] = snapshot(old['rows'], role=old['manifest']['role'], as_of=NOW - timedelta(days=1), recipe=old['manifest']['recipe'], lineage=old['lineage'])
    result = candidate.prepare_inputs([target], [row(1)], NOW, retained_reader=lambda *_a, **_k: supplied)
    assert result['history'] == [row(1)]
    assert result['reader_diagnostics'][0]['reason'] == 'RETAINED_DATASET_CUTOFF_MISMATCH'

def test_empty_fixture_groups_report_no_fixtures_without_a_model_call():

    def forbidden(*_a, **_k):
        pytest.fail('NO_FIXTURES_MUST_NOT_FIT')
    result = candidate.predict_normal([], [row(1)], NOW, predictor=forbidden)
    assert all(value['status'] == 'NO_FIXTURES' for value in result['numeric_models'].values())
    assert not result['model_input_uses']

@pytest.mark.parametrize('league', ['../escape', 'C:/escape', '0', True, None, 'comp_1'])
def test_legacy_competition_id_cannot_escape_the_artifact_directory(league):
    with pytest.raises(ValueError):
        candidate._comp({'league_id': league})

def test_legacy_numeric_competition_uses_existing_canonical_id_validation():
    assert candidate._comp({'league_id': '00078'}) == '78'

def test_legacy_integer_history_competition_reaches_predictor_without_rewriting_provenance():
    target = {'fixture_id': '900', 'league_id': '00078', 'home_team_id': '1', 'away_team_id': '2', 'kickoff': (NOW + timedelta(hours=3)).isoformat(), 'neutral_venue': False}
    record = {**row(1), 'fixture_id': 1, 'league_id': 78, 'home_team_id': 1, 'away_team_id': 2}
    record.pop('provider')
    original = deepcopy(record)
    calls = []
    result = candidate.predict_normal([target], [record], NOW, predictor=spy_predictor(calls), enabled_profiles=('laplace',))
    assert len(calls) == 1 and calls[0]['comp'] == '78'
    assert calls[0]['history'] == [original] and type(calls[0]['history'][0]['league_id']) is int
    assert record == original and result['input_view']['history'] == [original]

@pytest.mark.parametrize('record,expected', [({'league_id': 78}, '78'), ({'league_id': '00078', 'provider': 'api_football'}, '78'), ({'league_id': 'comp_1', 'provider': 'the_stats_api'}, 'comp_1')])
def test_history_competition_partition_uses_the_existing_provider_id_contract(record, expected):
    assert candidate.canonical_history_comp(record) == expected

def test_native_history_competition_does_not_accept_a_foreign_identity_shape():
    with pytest.raises(ValueError):
        candidate.canonical_history_comp({'league_id': 'the_stats_api:comp_1', 'provider': 'the_stats_api'})

@pytest.mark.parametrize('target', [{'league_id': 78}, {'api_football_league_id': '00078'}, {'league': {'id': 78}}])
def test_legacy_fixture_competition_matches_all_existing_normalizer_field_shapes(target):
    assert candidate._comp(target) == '78'

def test_actual_chosen_retained_record_origin_preserves_its_own_complete_clock_binding():
    target, retained = (fixture(), row(1, witnessed=True))
    result = candidate.prepare_inputs([target], [], NOW, retained_reader=lambda *_a, **_k: view([target], [retained]))
    assert result['selected_record_origins'] == [{'record_sha256': sha(retained), 'fixture_id': retained['fixture_id'], 'origin': 'RETAINED', 'source_id': retained['source_id'], 'observed_at': retained['observed_at'], 'available_at': retained['available_at']}]
    assert result['selected_record_origin_counts'] == {'ONLINE': 0, 'RETAINED': 1, 'BOTH': 0}
    assert result['retained_selected_records'] == result['retained_only_selected_records'] == 1

def test_shared_online_retained_representative_and_extra_alias_do_not_inflate_training_record_count():
    target, retained = (fixture(), row(1, witnessed=True))
    alias = {**retained, 'source_id': 'TESTONLY:LATER_ALIAS', 'observed_at': (NOW - timedelta(minutes=100)).isoformat()}
    result = candidate.prepare_inputs([target], [retained], NOW, retained_reader=lambda *_a, **_k: view([target], [retained, alias]))
    assert len(result['history']) == len(result['selected_record_origins']) == 1
    assert result['selected_record_origins'][0]['record_sha256'] == sha(retained)
    assert result['selected_record_origins'][0]['origin'] == 'BOTH'
    assert result['selected_record_origin_counts'] == {'ONLINE': 0, 'RETAINED': 0, 'BOTH': 1}
    assert result['retained_selected_records'] == 1 and result['retained_only_selected_records'] == 0
    assert result['partition_counts']['SELECTED_STANDARD_TRUTH_ALIAS'] == 3
