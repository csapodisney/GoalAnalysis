"""Physical TESTONLY window controls and recorded caller handoff; no profit claim.

Caller predictor/source doubles test handoff only. Independent retained raw and
unchanged numerical-model experiment provide the separate numerical evidence.
Scripts are inspected as AST; none is imported or executed.
"""
import ast
import json
import socket
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_model_integration import predictor_fixture
from test_native_history_shadow import NOW as WINDOW_NOW
from test_native_history_shadow import OBSERVED as WINDOW_OBSERVED
from test_native_history_shadow import raw_match as window_raw_match
from test_native_history_shadow import setup as window_setup
from test_portfolio_flow import NOW, RecordedReviewer, environment

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.portfolio import LEGACY_PROFILE_IDS
from goal_analysis.jobs import native_history_shadow as native_module
from goal_analysis.jobs import native_history_shadow as window_native
from goal_analysis.jobs import normal_history_input as normal_module
from goal_analysis.jobs import online_history_venue as venue_module
from goal_analysis.jobs import portfolio as portfolio_module
from goal_analysis.normalization.training_datasets import sha, snapshot
from goal_analysis.storage import SnapshotStore

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = {'enabled_profiles': ['daily223', 'gauss', 'kepler', 'laplace']}


def window_old_page(database, root, number):
    body = {'data': [window_raw_match('mt_' + str(number), utc_date='2019-01-01T12:00:00+00:00')], 'meta': {'page': number, 'per_page': 100, 'total_pages': number}}
    url = 'https://api.thestatsapi.com/api/football/matches?competition_id=comp_1&status=finished&date_from=2019-01-01&date_to=2019-01-31&utc_offset=%2B00%3A00&per_page=100&page=' + str(number)
    return SnapshotStore(database, root / 'data/raw').save('the_stats_api', 'football_matches', json.dumps(body).encode(), 'application/json', WINDOW_OBSERVED, url)

def test_default_reader_has_no_window_flag_and_matches_whole_window_data_and_dataset(tmp_path):
    _, _, target = window_setup(tmp_path)
    default = window_native.native_history_view(tmp_path, [target], as_of=WINDOW_NOW)
    whole = window_native.native_history_view(tmp_path, [target], as_of=WINDOW_NOW, history_start=WINDOW_NOW - timedelta(days=730))
    assert 'normal_model_window_partition' not in default
    assert whole['normal_model_window_partition']['older_source_count'] == 0
    assert default['fixtures'] == whole['fixtures']
    assert default['history'] == whole['history']
    assert default['dataset'] == whole['dataset']

def test_old_complete_windows_partition_before_same_source_bound_no_first_n(tmp_path, monkeypatch):
    database, temporal, target = window_setup(tmp_path)
    old = [window_old_page(database, tmp_path, n) for n in (2, 3)]
    temporal.capture('raw')
    monkeypatch.setattr(window_native, 'MAX_SOURCES', 1)
    control = window_native.native_history_view(tmp_path, [target], as_of=WINDOW_NOW)
    assert control['status'] == 'LIMIT_REACHED' and (not control['history'])
    treatment = window_native.native_history_view(tmp_path, [target], as_of=WINDOW_NOW, history_start=WINDOW_NOW - timedelta(days=730))
    assert treatment['status'] in {'AVAILABLE', 'GAPS'}
    assert {r['fixture_id'] for r in treatment['history']} == {'mt_1'}
    partition = treatment['normal_model_window_partition']
    assert partition['older_source_count'] == 2 and partition['source_budget_unchanged'] == 1
    assert {s['content_hash'] for s in partition['older_source_aliases']} == {s.content_hash for s in old}
    assert not partition['global_five_year_provider_coverage_claim']

def test_metadata_sentinel_still_failclosed_even_when_old_sources_would_be_partitioned(tmp_path, monkeypatch):
    database, temporal, target = window_setup(tmp_path)
    for n in (2, 3):
        window_old_page(database, tmp_path, n)
    temporal.capture('raw')
    monkeypatch.setattr(window_native, 'MAX_METADATA', 2)
    actual = window_native.native_history_view(tmp_path, [target], as_of=WINDOW_NOW, history_start=WINDOW_NOW - timedelta(days=730))
    assert actual['status'] == 'LIMIT_REACHED' and (not actual['history'])
    assert actual['gaps']['METADATA_LIMIT'] == 1

@pytest.mark.parametrize('delta', [timedelta(days=-731), timedelta(seconds=1)])
def test_invalid_model_window_rejected_before_any_database_access(tmp_path, delta):
    with pytest.raises(ValueError, match='INVALID_NORMAL_HISTORY_MODEL_WINDOW'):
        window_native.native_history_view(tmp_path, [], as_of=WINDOW_NOW, history_start=WINDOW_NOW + delta)
    assert not (tmp_path / 'data').exists()

def test_window_never_replaces_source_availability_or_original_clock(tmp_path):
    _, _, target = window_setup(tmp_path)
    actual = window_native.native_history_view(tmp_path, [target], as_of=WINDOW_NOW, history_start=WINDOW_NOW - timedelta(days=730))
    before = window_native.native_history_view(tmp_path, [target], as_of=WINDOW_NOW)
    assert actual['history'] == before['history']
    assert actual['history'][0]['observed_at'] == WINDOW_OBSERVED.isoformat(timespec='microseconds')
    assert actual['history'][0]['available_at'] == WINDOW_NOW.isoformat(timespec='microseconds')
    late = window_native.native_history_view(tmp_path, [target], as_of=WINDOW_NOW - timedelta(microseconds=1), history_start=WINDOW_NOW - timedelta(days=729))
    assert not late['fixtures'] and (not late['history'])

@pytest.fixture
def modules(monkeypatch):
    monkeypatch.setattr(socket, 'socket', Mock(side_effect=AssertionError('NO_REAL_NETWORK')))
    return SimpleNamespace(native=native_module, normal=normal_module, venue=venue_module, portfolio=portfolio_module)

def run(path, function, *, predictor=predictor_fixture, settings=None, ledger=None, **kwargs):
    cfg, football, odds, cache, _calls, _state, _db = environment(path)
    return function(cfg, deepcopy(SETTINGS if settings is None else settings), football, odds, cache, NOW.date(), RecordedReviewer(), ledger, clock=lambda: NOW, model_predictor=predictor, **kwargs)

def handoff(monkeypatch, modules, *, unknown_first=False, reader_failure=False):
    events, captured = ([], {'binds': [], 'readers': [], 'predictors': []})

    def bind(root, fixtures, history, *, as_of):
        events.append('bind')
        captured['binds'].append(deepcopy((root, fixtures, history, as_of)))
        targets = [{**deepcopy(value), 'neutral_venue': False} for value in fixtures]
        if unknown_first:
            targets[0]['neutral_venue'] = None
        extra = {'fixture_id': '777777', 'league_id': '78', 'season': 2026, 'home_team_id': '1', 'away_team_id': '2', 'status': 'FT', 'kickoff': (NOW - timedelta(days=20)).isoformat(), 'observed_at': (NOW - timedelta(hours=1)).isoformat(), 'source_id': 'TESTONLY_CALLER_HISTORY', 'neutral_venue': False, 'full_time_home': 2, 'full_time_away': 1}
        return {'fixtures': targets, 'history': [*deepcopy(history['records']), extra], 'diagnostics': {'TESTONLY_SYNTHETIC_BINDING': len(targets)}, 'read_budget_failure': None, 'source_proofs': [], 'original_inputs_unchanged': True, 'new_receipts_or_availability_clocks': 0}

    def reader(root, fixtures, *, as_of, history_start):
        events.append('reader')
        captured['readers'].append(deepcopy((root, fixtures, as_of, history_start)))
        if reader_failure:
            raise OSError('TESTONLY_PRIVATE_RETAINED_READER_ERROR')
        empty = snapshot([], role='TESTONLY_EMPTY_RETAINED_CALLER_SCOPE', as_of=as_of, recipe={'testonly': True}, lineage={'lossless_history_aliases': []})
        return {'status': 'GAPS', 'as_of': as_of.isoformat(), 'fixtures': [], 'history': [], 'dataset': empty, 'gaps': {'TESTONLY_NO_NATIVE_TARGET_IN_LEGACY_FLOW': len(fixtures)}, 'source_count': 0}

    def predictor(fixtures, history, now, **kwargs):
        events.append('predictor')
        captured['predictors'].append(deepcopy((fixtures, history, now, kwargs)))
        return predictor_fixture(fixtures, history, now, **kwargs)
    monkeypatch.setattr(modules.venue, 'bind_online_venue', bind)
    monkeypatch.setattr(modules.native, 'native_history_view', reader)
    return (events, captured, predictor)

def test_default_none_preserves_original_predictor_handoff_without_new_readers_or_keys(tmp_path, monkeypatch, modules):
    captured = []

    def forbidden(*_a, **_k):
        pytest.fail('DEFAULT_NONE_MUST_NOT_ENTER_NEW_INPUT_PATH')

    def predictor(fixtures, history, now, **kwargs):
        captured.append(deepcopy((fixtures, history, now, kwargs)))
        return predictor_fixture(fixtures, history, now, **kwargs)
    monkeypatch.setattr(modules.venue, 'bind_online_venue', forbidden)
    monkeypatch.setattr(modules.native, 'native_history_view', forbidden)
    actual = run(tmp_path, modules.portfolio.run_portfolio, predictor=predictor)['artifacts']
    assert len(captured) == 1
    fixtures, history, cutoff, kwargs = captured[0]
    assert fixtures == actual['fixtures']['records'] and history == actual['history']['records']
    assert cutoff == NOW and kwargs == {'enabled_profiles': ('gauss', 'kepler', 'laplace'), 'artifact_dir': None}
    assert actual['numeric_models'] == predictor_fixture(fixtures, history, cutoff, **kwargs)
    assert len(actual['report']['tickets']) == 1
    assert actual['report']['tickets'][0]['profile_id'] == 'daily223'
    assert 'normal_model_input' not in actual and 'normal_model_input' not in actual['report']

def test_explicit_root_reads_whole_universe_once_before_predictor_and_freezes_actual_consumed_hash(tmp_path, monkeypatch, modules):
    events, captured, predictor = handoff(monkeypatch, modules)
    artifacts = run(tmp_path, modules.portfolio.run_portfolio, normal_history_root=tmp_path, predictor=predictor)['artifacts']
    normal, summary = (artifacts['normal_model_input'], artifacts['report']['normal_model_input'])
    assert normal['status'] == 'COMPLETE'
    assert len(captured['binds']) == len(captured['readers']) == 1
    root, targets, cutoff, history_start = captured['readers'][0]
    assert root == tmp_path.resolve() and len(targets) == len(artifacts['fixtures']['records']) == 3
    assert cutoff == NOW and history_start == NOW - timedelta(days=730)
    assert events.index('reader') < events.index('predictor') and events[0] == 'bind'
    selected = normal['input_view']['history']
    assert len(selected) == 1 and selected[0]['source_id'] == 'TESTONLY_CALLER_HISTORY'
    assert summary['input_dataset']['rows_sha256'] == sha(selected)
    assert summary['input_artifact_sha256'] == canonical_sha256(normal)
    assert all(call[1] == selected for call in captured['predictors'])
    assert summary['model_input_uses'] == normal['model_input_uses']
    assert all(use['history_sha256'] == sha(selected) for use in summary['model_input_uses'])
    assert 'TESTONLY_CALLER_HISTORY' not in json.dumps(artifacts['history'])
    assert not summary['historical_feature_availability_certified']

def test_corrected_unknown_reaches_context_numeric_recommendations_and_new_report_without_changing_originals(tmp_path, monkeypatch, modules):
    from goal_analysis.engine import model_recommendations
    from goal_analysis.jobs import candidate_context_review, profile_context_stage
    _events, captured, predictor = handoff(monkeypatch, modules, unknown_first=True)
    received = {'context': [], 'recommendations': []}
    original_builder = model_recommendations.build_model_recommendations

    def build(*args, **kwargs):
        received['recommendations'].append(deepcopy(args[1]))
        return original_builder(*args, **kwargs)

    def prepare(_root, fixtures, _packets, **_kwargs):
        received['context'].append(deepcopy(fixtures))
        return {'contexts': {}, 'as_of': NOW.isoformat(), 'collection': {'usage': {'requests': 0}}}
    monkeypatch.setattr(model_recommendations, 'build_model_recommendations', build)
    monkeypatch.setattr(candidate_context_review, 'policy', lambda _root: 'ACTIVE')
    monkeypatch.setattr(candidate_context_review, 'prepare', prepare)
    monkeypatch.setattr(candidate_context_review, 'attach', lambda packets, _prepared: deepcopy(packets))
    monkeypatch.setattr(profile_context_stage, 'policy', lambda _root: 'OFF')
    ledger = SimpleNamespace(path=tmp_path / 'data/arthur/ledger.sqlite3', feedback_summary=dict, list_runs=lambda **_kwargs: [])
    artifacts = run(tmp_path, modules.portfolio.run_portfolio, normal_history_root=tmp_path, predictor=predictor, ledger=ledger)['artifacts']
    original = captured['binds'][0][1]
    assert artifacts['fixtures']['records'] == original
    assert all('neutral_venue' not in fixture for fixture in original)
    assert captured['predictors'] and received['context'] and received['recommendations']
    for values in (captured['predictors'][0][0], received['context'][0], received['recommendations'][0], artifacts['report']['fixtures']):
        assert values[0]['neutral_venue'] is None
        assert values[1]['neutral_venue'] is False
    assert artifacts['history'] == captured['binds'][0][2]
    flags = artifacts['report']['normal_model_input']['source_flags']
    assert flags['heuristic_history'] == 'ORIGINAL_ONLINE_ONLY'
    assert flags['original_collected_inputs'] == 'PRESERVED_UNMODIFIED'

def test_unavailable_retained_reader_keeps_online_predictor_calls_and_baseline(tmp_path, monkeypatch, modules):
    _events, captured, predictor = handoff(monkeypatch, modules, reader_failure=True)
    artifacts = run(tmp_path, modules.portfolio.run_portfolio, normal_history_root=tmp_path, predictor=predictor)['artifacts']
    assert len(captured['readers']) == 1 and captured['predictors']
    assert all(row[1] and row[1][0]['source_id'] == 'TESTONLY_CALLER_HISTORY' for row in captured['predictors'])
    assert artifacts['report']['tickets']
    summary = artifacts['report']['normal_model_input']
    assert summary['reader_diagnostics'][0]['code'] == 'RETAINED_READER_UNAVAILABLE'
    assert 'TESTONLY_PRIVATE_RETAINED_READER_ERROR' not in json.dumps(artifacts['report'])

def test_late_shadow_paths_cannot_replace_new_normal_numeric_models(tmp_path, monkeypatch, modules):
    from goal_analysis.models import history_sample_shadow
    _events, captured, predictor = handoff(monkeypatch, modules)
    shadow_calls = []

    def sample(*_args, **_kwargs):
        shadow_calls.append('sample')
        return {'mode': 'SHADOW', 'decision_effect': 'NONE', 'treatment': {'gauss': 'TESTONLY_FOREIGN_OUTPUT'}}

    def native(*_args, **_kwargs):
        shadow_calls.append('native')
        return {'mode': 'SHADOW', 'decision_effect': 'NONE', 'numeric_models': {'gauss': 'TESTONLY_FOREIGN_OUTPUT'}}
    monkeypatch.setattr(history_sample_shadow, 'evaluate', sample)
    monkeypatch.setattr(modules.native, 'replay_native_history', native)
    artifacts = run(tmp_path, modules.portfolio.run_portfolio, normal_history_root=tmp_path, predictor=predictor, history_sample_shadow=True, native_history_shadow_root=tmp_path)['artifacts']
    assert shadow_calls == ['sample', 'native'] and len(captured['readers']) == 1
    assert artifacts['report']['numeric_models'] == artifacts['numeric_models']
    assert all(isinstance(value, dict) for value in artifacts['numeric_models'].values())
    assert artifacts['report']['native_history_shadow']['numeric_models']['gauss'] == 'TESTONLY_FOREIGN_OUTPUT'
    assert artifacts['normal_model_input']['status'] == 'COMPLETE'

def test_unexpected_binding_failure_is_visible_local_model_failure_not_daily_veto(tmp_path, monkeypatch, modules):

    def fail(*_args, **_kwargs):
        raise ValueError('TESTONLY_PRIVATE_SOURCE_BINDING_FAILURE')

    def forbidden(*_args, **_kwargs):
        pytest.fail('FAILED_BINDING_MUST_NOT_CALL_MODEL_OR_RETAINED_READER')
    monkeypatch.setattr(modules.venue, 'bind_online_venue', fail)
    monkeypatch.setattr(modules.native, 'native_history_view', forbidden)
    artifacts = run(tmp_path, modules.portfolio.run_portfolio, normal_history_root=tmp_path, predictor=forbidden)['artifacts']
    assert artifacts['report']['tickets']
    assert artifacts['normal_model_input']['status'] == 'UNAVAILABLE'
    assert all(value['status'] == 'MODEL_ERROR' for value in artifacts['numeric_models'].values())
    assert artifacts['report']['normal_model_input']['reason'] == 'NORMAL_MODEL_INPUT_PREPARATION_FAILED'
    assert 'TESTONLY_PRIVATE_SOURCE_BINDING_FAILURE' not in json.dumps(artifacts['report'])

def test_disabled_models_do_not_enter_new_input_path_even_with_explicit_root(tmp_path, monkeypatch, modules):

    def forbidden(*_args, **_kwargs):
        pytest.fail('DISABLED_MODELS_MUST_NOT_READ_OR_FIT')
    monkeypatch.setattr(modules.venue, 'bind_online_venue', forbidden)
    monkeypatch.setattr(modules.native, 'native_history_view', forbidden)
    artifacts = run(tmp_path, modules.portfolio.run_portfolio, normal_history_root=tmp_path, settings={'enabled_profiles': list(LEGACY_PROFILE_IDS)}, predictor=forbidden)['artifacts']
    assert not artifacts['numeric_models'] and 'normal_model_input' not in artifacts

@pytest.mark.parametrize('filename', ['run-arthur.py', 'regenerate-arthur-protected.py'])
def test_actual_script_candidate_callsite_passes_its_concrete_root_without_executing_script(filename):
    tree = ast.parse((ROOT / 'scripts' / filename).read_text(encoding='utf-8'))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    calls = [node for node in ast.walk(main) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and (node.func.id == 'run_portfolio')]
    assert len(calls) == 1
    root = next(keyword.value for keyword in calls[0].keywords if keyword.arg == 'normal_history_root')
    assert isinstance(root, ast.Name) and root.id == 'ROOT'

def test_nightly_candidate_passes_its_concrete_root_without_instantiating_workflow():
    tree = ast.parse((ROOT / 'src/goal_analysis/jobs/nightly.py').read_text(encoding='utf-8'))
    workflow = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'NightlyWorkflow')
    generate = next(node for node in workflow.body if isinstance(node, ast.FunctionDef) and node.name == '_generate')
    calls = [node for node in ast.walk(generate) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and (node.func.id == 'run_portfolio')]
    assert len(calls) == 1
    root = next(keyword.value for keyword in calls[0].keywords if keyword.arg == 'normal_history_root')
    assert isinstance(root, ast.Attribute) and root.attr == 'root'
    assert isinstance(root.value, ast.Name) and root.value.id == 'self'
