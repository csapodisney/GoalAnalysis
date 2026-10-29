"""Real local player and forecast readers through the native coordinator.

Only the shared numeric fit is controlled; all source receipts and specialist
selection are real local fixtures. No external requests or production writes.
"""
import socket
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock

import pytest
import test_native_weather_shadow as weather_fixtures
from test_player_specialist_shadow import NOW, environment

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.portfolio import validate_settings
from goal_analysis.jobs import native_options_shadow as pipeline
from goal_analysis.jobs import weather_option_shadow


def stored_rows(db):
    # Closing a reader can checkpoint pre-existing WAL bytes. Compare the whole
    # logical database, including schemas and rows, rather than its main file.
    with closing(sqlite3.connect(db.path.as_uri()+'?mode=ro', uri=True)) as connection:
        return list(connection.iterdump())


def setup(root, monkeypatch, **weather_kwargs):
    data, models, db, meta = environment(root)
    # Add independently witnessed forecast/venue inputs to these same player
    # fixtures, rather than replacing the specialist or its selected legs.
    with pytest.MonkeyPatch.context() as fixture_patch:
        fixture_patch.setattr(weather_fixtures, 'base', lambda *_a, **_k: (data, models, db, meta))
        weather_fixtures.environment(root, **weather_kwargs)
    run = {'numeric_models': models, 'source_view': {'fixtures': data['fixtures'],
           'dataset': {'manifest': {'dataset_id': '1'*64}}}}
    fit = Mock(side_effect=lambda *_a, **_k: deepcopy(run))
    monkeypatch.setattr(pipeline, 'replay_native_history', fit)
    monkeypatch.setattr(socket, 'socket', Mock(side_effect=AssertionError('NO_NETWORK')))
    return data, models, db, fit


@pytest.mark.parametrize('weather_kwargs,expected_status', [
    ({}, 'FORECAST_CONTEXT_VERIFIED_SHADOW'),
    ({'venue': False}, 'UNKNOWN'),
    ({'observed': NOW-timedelta(hours=7)}, 'UNKNOWN'),
])
def test_each_nonempty_player_leg_receives_exact_weather_context_without_effect(
        tmp_path, monkeypatch, weather_kwargs, expected_status):
    data, models, db, fit = setup(tmp_path, monkeypatch, **weather_kwargs)
    original = deepcopy((data, models))
    db_before = stored_rows(db)
    result = pipeline.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)['options_shadow']
    specialist = result['player_review']['specialist']
    assert specialist['status'] == 'SHADOW_CANDIDATE' and len(specialist['candidate_legs']) == 3
    review = result['weather_review']
    assert review['status'] == 'OBSERVED'
    bindings = review['specialist_reviews']['player']
    assert len(bindings) == len(specialist['candidate_legs'])
    assert review['leg_bindings'] == (
        sum(len(option['legs']) for option in review['option_reviews'])
        + sum(len(rows) for rows in review['specialist_reviews'].values()))
    for leg, binding in zip(specialist['candidate_legs'], bindings, strict=True):
        row = review['source_audit']['rows'][binding['context_row_index']]
        assert binding['leg_sha256'] == canonical_sha256(leg)
        assert row['fixture_id'] == leg['fixture_id'] == binding['fixture_id']
        assert binding['context_sha256'] == canonical_sha256(row)
        assert binding['dataset_id'] == review['source_audit']['dataset']['manifest']['dataset_id']
        assert binding['context_status'] == expected_status
        assert binding['data_use'] == ('CONTEXT_ONLY_NO_VALIDATED_EFFECT' if expected_status != 'UNKNOWN'
                                       else 'WITHHELD_UNVERIFIED_CONTEXT')
        assert binding['probability_adjustment'] == 0 and binding['ranking_multiplier'] == 1
        assert binding['live_decision_effect'] is False
    assert (data, models) == original and stored_rows(db) == db_before
    assert fit.call_count == 1 and result['additional_model_fit_runs'] == 0
    assert result['external_requests'] == result['production_writes'] == 0
    assert result['live_promotion'] is False and result['decision_effect'] == 'NONE'


def test_abstained_player_keeps_empty_binding_without_filler(tmp_path, monkeypatch):
    data, models, _, fit = setup(tmp_path, monkeypatch)
    models.pop('gauss')
    result = pipeline.replay_native_options(tmp_path, {}, data,
        validate_settings({'operating_contract_version': 1}), as_of=NOW)['options_shadow']
    assert fit.call_count == 1
    assert result['player_review']['specialist']['candidate_legs'] == []
    assert result['weather_review']['specialist_reviews']['player'] == []
    assert result['weather_review']['option_reviews']


def test_weather_failure_cannot_change_player_cards_or_other_specialists(tmp_path, monkeypatch):
    data, _, db, fit = setup(tmp_path, monkeypatch)
    control = pipeline.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)['options_shadow']
    db_before = stored_rows(db)
    monkeypatch.setattr(weather_option_shadow, 'inspect_native_weather',
                        Mock(side_effect=ValueError('sensitive detail')))
    actual = pipeline.replay_native_options(tmp_path, {}, data, {}, as_of=NOW)['options_shadow']
    assert fit.call_count == 2 and stored_rows(db) == db_before
    assert actual['weather_review']['status'] == 'UNAVAILABLE'
    assert 'sensitive detail' not in str(actual)
    for key in ('cards', 'options', 'option_source_bindings', 'player_review', 'cup_review',
                'xg_review', 'motivation_review', 'rotation_review', 'venue_review'):
        assert actual[key] == control[key]
