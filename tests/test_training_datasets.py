"""Versioned actual fit inputs, atomic storage and unchanged numeric outputs."""
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta, timezone
from pathlib import Path

import pytest
from test_numeric_models import NOW, fixtures, sample_history

from goal_analysis.models import datasets, predict_models
from goal_analysis.normalization.training_datasets import snapshot, validate
from goal_analysis.storage.training_datasets import DatasetStore


def value(**changes):
    arguments = {'rows': [{'x': [1.0, 2.0], 'label': 1}, {'x': [3.0, 4.0], 'label': 2}],
                 'role': 'evaluation_train', 'as_of': NOW, 'recipe': {'version': 'fixed-test-model-1'},
                 'lineage': [{'source_id': 'synthetic:123', 'observed_at': NOW - timedelta(minutes=1)}]}
    return snapshot(**{**arguments, **changes})


def test_same_consumed_rows_recipe_and_utc_cutoff_produce_same_version_without_mutation():
    rows = [{'id': 'mt_1', 'time': NOW, 'missing': None, 'bool': False, 'number': 1.2}]
    before = deepcopy(rows)
    a = value(rows=rows)
    b = value(rows=rows, as_of=NOW.astimezone(timezone(timedelta(hours=2))))
    assert a == b and rows == before and validate(a) == a
    assert len(a['manifest']['dataset_id']) == 64


@pytest.mark.parametrize('change', ['row_value', 'row_order', 'cutoff', 'recipe', 'lineage', 'role'])
def test_any_material_dataset_or_recipe_change_has_a_new_identity(change):
    old = value()
    arguments = {}
    if change == 'row_value':
        arguments['rows'] = [{**old['rows'][0], 'label': 2}, old['rows'][1]]
    elif change == 'row_order':
        arguments['rows'] = list(reversed(old['rows']))
    elif change == 'cutoff':
        arguments['as_of'] = NOW + timedelta(seconds=1)
    elif change == 'recipe':
        arguments['recipe'] = {'version':'fixed-test-model-2'}
    elif change == 'lineage':
        arguments['lineage'] = {'source_id':'synthetic:999'}
    else:
        arguments['role'] = 'validation'
    assert value(**arguments)['manifest']['dataset_id'] != old['manifest']['dataset_id']


@pytest.mark.parametrize('field', ['rows', 'lineage', 'manifest'])
def test_payload_and_provenance_tampering_are_rejected(field):
    current = value()
    if field == 'rows':
        current[field][0]['label'] = 99
    elif field == 'lineage':
        current[field][0]['source_id'] = 'different'
    else:
        current[field]['row_count'] += 1
    with pytest.raises(ValueError, match='MISMATCH'):
        validate(current)


def test_no_nonfinite_naive_or_sensitive_values_can_be_published():
    for rows in ([{'x': float('nan')}], [{'x': float('inf')}], [{'time': NOW.replace(tzinfo=None)}], [{'api_key':'private'}]):
        with pytest.raises(ValueError):
            value(rows=rows)


@pytest.mark.parametrize('wrong_count', [True, 1.0])
def test_manifest_hash_validation_preserves_numeric_types(wrong_count):
    current = value(rows=[{'x': 1}])
    current['manifest']['row_count'] = wrong_count
    with pytest.raises(ValueError, match='MISMATCH'):
        validate(current)


def test_atomic_publication_is_idempotent_concurrent_and_corruption_is_not_overwritten(tmp_path):
    store, current = DatasetStore(tmp_path), value()
    with ThreadPoolExecutor(max_workers=4) as pool:
        published = list(pool.map(lambda _: store.put(current), range(8)))
    assert sum(published) == 1
    identifier = current['manifest']['dataset_id']
    assert store.read(identifier) == current and not list(tmp_path.glob('*.tmp'))
    path = store.path(identifier)
    path.write_text('{}')
    with pytest.raises(ValueError, match='CORRUPT_EXISTING'):
        store.put(current)
    assert path.read_text() == '{}'
    with pytest.raises(ValueError):
        store.path('../other')


def test_failed_fit_is_reported_without_claiming_a_success_or_exposing_error_text():
    with datasets.Capture() as captured, pytest.raises(RuntimeError), datasets.fitting(
            'gauss', 'test-fit', [[1,2]], NOW, recipe={'version':'test'}, lineage=[]):
        raise RuntimeError('private-provider-body')
    assert captured.uses[0]['status'] == 'FIT_CALL_FAILED'
    assert captured.uses[0]['error_type'] == 'RuntimeError'
    assert 'private-provider-body' not in json.dumps(captured.uses)
    assert datasets.record('test', 'outside-session', [], NOW, recipe={'version':'test'}, lineage=[]) is None


def test_real_model_fits_capture_exact_separate_inputs_and_do_not_change_predictions(monkeypatch):
    created = []
    original = datasets.Capture

    class Spy(original):
        def __init__(self):
            super().__init__()
            created.append(self)

    monkeypatch.setattr(datasets, 'Capture', Spy)
    result = predict_models(fixtures(), sample_history(), NOW, ('gauss','kepler','laplace'))
    capture = created[0]
    assert all(result[p]['status'] == 'READY' for p in result)
    assert len(capture.datasets) == 9
    assert sum(u['status'] == 'FIT_CALL_RETURNED' for u in capture.uses) == 7
    assert {u['slot'] for u in capture.uses if u['profile'] == 'kepler'} == {
        'chronological_holdout_train', 'chronological_validation', 'final_refit'}
    for current in capture.datasets.values():
        assert validate(current) == current
    gauss = next(s for s in capture.datasets.values() if s['manifest']['role'] == 'dixon_coles_730')
    assert gauss['rows'] == [[r['full_time_home'],r['full_time_away'],r['home_team_id'],r['away_team_id']] for r in gauss['lineage']]
    kepler = next(s for s in capture.datasets.values() if s['manifest']['role'] == 'final_refit')
    assert kepler['rows'] == [[r['features'],r['label']] for r in kepler['lineage']['chronological_features']]
    train = next(s for s in capture.datasets.values() if s['manifest']['role'] == 'chronological_holdout_train')
    validation = next(s for s in capture.datasets.values() if s['manifest']['role'] == 'chronological_validation')
    assert len(train['rows']) < len(kepler['rows']) and len(validation['rows']) < len(kepler['rows'])
    assert all(r['training_datasets']['archive']['status'] == 'NOT_REQUESTED' for r in result.values())


def test_archive_failure_is_explicit_without_discarding_a_valid_numeric_prediction(monkeypatch):
    def fail(self, value):
        raise OSError('private-disk-path')

    monkeypatch.setattr(DatasetStore, 'put', fail)
    root = Path(__file__).resolve().parents[1]
    result = predict_models(fixtures(), sample_history(), NOW, ('laplace',), artifact_dir=root/'artifacts/not-created-test')
    assert result['laplace']['status'] == 'READY'
    assert result['laplace']['training_datasets']['archive']['status'] == 'UNAVAILABLE'
    assert not (root/'artifacts/not-created-test').exists()


def test_weather_train_and_holdout_manifests_describe_the_actual_selected_samples():
    from test_weather_expert import analyze, scenario

    from goal_analysis.agents.weather_expert import POLICY

    arguments = scenario()
    result = analyze(arguments)['fixtures'][0]
    observed = []
    for team in result['teams'].values():
        if 'training_datasets' not in team:
            continue
        manifests = {m['role']:m for m in team['training_datasets']}
        assert manifests['weather_analog_train']['row_count'] == team['validation']['training_count']
        assert manifests['weather_analog_holdout']['row_count'] == team['validation']['holdout_count']
        assert manifests['weather_analog_train']['dataset_id'] != manifests['weather_analog_holdout']['dataset_id']
        assert all(m['recipe']['policy'] == POLICY for m in manifests.values())
        observed.extend(manifests)
    assert observed
