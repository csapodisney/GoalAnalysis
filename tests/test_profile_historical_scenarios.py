"""TESTONLY supplied values; no DB, provider, model, native process or replay."""
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import pytest

from goal_analysis.agents.profile_decision import request_tool, set_options, start_sessions
from goal_analysis.agents.profile_roster import AGENT_IDS, get_agent_definition
from goal_analysis.agents.profile_tools import ProfileToolService
from goal_analysis.jobs.agent_snapshot import build_normal_snapshot, build_snapshot, digest
from goal_analysis.jobs.profile_historical_scenarios import (
    KIND,
    HistoricalScenarioEngine,
    build_history_view,
    describe_history_view,
    policy_catalog,
)
from goal_analysis.normalization.training_datasets import snapshot as dataset_snapshot

NOW = '2026-11-12T18:00:00+00:00'
WINDOW = {'start': '2026-10-01T00:00:00+00:00', 'end': '2026-11-12T18:00:00+00:00'}
SETTLEMENT = 'REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES'


def rows(count=25):
    result = []
    for index in range(count):
        kickoff = datetime(2026, 10, 1, 12, tzinfo=UTC) + timedelta(days=index)
        result.append({'fixture_id': str(1000 + index), 'league_id': '1',
            'home_team_id': '10', 'away_team_id': '20', 'status': 'FT',
            'kickoff': kickoff.isoformat(), 'observed_at': (kickoff + timedelta(hours=3)).isoformat(),
            'full_time_home': 2, 'full_time_away': 1, 'first_half_home': 1,
            'first_half_away': 0, 'neutral_venue': False, 'source_id': 'TESTONLY-history-' + str(index)})
    return result


def history(values):
    value = {'kind': 'DAILY223_HISTORY', 'schema_version': 1, 'provider': 'api_football',
             'collected_at': NOW, 'records': deepcopy(values), 'sources': ['TESTONLY:source']}
    return {**value, 'snapshot_sha256': digest(value)}


def fixtures():
    return [{'fixture_id': 'api_football:9000', 'source_provider': 'api_football',
             'api_football_fixture_id': '9000', 'api_football_league_id': '1',
             'api_football_home_team_id': '10', 'api_football_away_team_id': '20',
             'kickoff': '2026-11-13T18:00:00+00:00', 'neutral_venue': False},
            {'fixture_id': 'api_football:9001', 'kickoff': '2026-11-13T19:00:00+00:00'}]


def quotes():
    return [{**fixtures()[0], 'candidate_id': 'TESTONLY-' + selection,
             'market_key': 'totals_2_5', 'selection_key': selection, 'period': 'FULL_TIME',
             'settlement': SETTLEMENT, 'decimal_price': 2.0, 'bookmaker': 'TESTONLY',
             'quote_provider': 'TESTONLY', 'quote_source_id': 'TESTONLY:quote',
             'quote_source_sha256': 'a' * 64, 'quote_observed_at': NOW, 'quoted_at': None}
            for selection in ('over', 'under')]


def frame(values=None, *, owner=None, scope='same_competition', window=None):
    owner = owner or AGENT_IDS[0]
    values = rows() if values is None else values
    view = build_history_view({'history': history(values)}, as_of=NOW)
    target_fixtures, candidates = fixtures(), quotes()
    families = {
        'odds_and_price_history': {'status': 'AVAILABLE', 'value': {'candidate_input': {'candidates': candidates}},
                                  'source_refs': ['TESTONLY:source']},
        'historical_strategy_evaluation': {'status': 'AVAILABLE', 'value': describe_history_view(view),
                                          'source_refs': ['TESTONLY:source']}}
    ids = [row['fixture_id'] for row in target_fixtures]
    snapshot = build_snapshot(run_id='TESTONLY-descriptive-history', target_date='2026-11-13', as_of=NOW,
        authoritative_fixture_ids=ids, calendar_receipt={'target_date': '2026-11-13',
            'scope': 'FULL_PROVIDER_DAY', 'status': 'COMPLETE', 'fixture_ids': ids,
            'source_refs': ['TESTONLY:source']}, fixtures=target_fixtures,
        family_values=families, source_manifest={'TESTONLY:source': {'kind': 'TESTONLY'}})
    options = [{'option_id': owner + '-' + str(index), 'agent_id': owner, 'legs': [deepcopy(quote)]}
               for index, quote in enumerate(candidates)]
    state = next(state for state in start_sessions(snapshot,
        {key: get_agent_definition(key).method_version for key in AGENT_IDS}) if state.agent_id == owner)
    state = set_options(state, snapshot, options)
    policy = next(row for row in policy_catalog() if row['scope'] == scope)
    _, request = request_tool(state, snapshot, 'historical_strategy_evaluation',
        parameters={'window': deepcopy(WINDOW if window is None else window), 'policy_hash': policy['policy_hash']})
    return snapshot, options, request, view


def evaluate(values=None, **kwargs):
    snapshot, options, request, view = frame(values, **kwargs)
    result = ProfileToolService(snapshot, historical_view=view).evaluate(request, options)
    return result, options


def sample(result, options, option=0, cohort='competition'):
    return result['value']['by_option'][options[option]['option_id']]['legs'][0]['scope_samples'][cohort]


def test_complete_25_row_cohort_includes_rows_beyond_old_twenty_and_own_market_changes():
    data = rows()
    data[-1].update(full_time_home=0, full_time_away=0, first_half_home=0)
    result, options = evaluate(data)
    over, under = sample(result, options), sample(result, options, 1)
    assert (over['sample_size'], over['market_hits'], over['market_misses']) == (25, 24, 1)
    assert (under['sample_size'], under['market_hits']) == (25, 1)
    assert over['descriptive_hit_rate'] == pytest.approx(24 / 25)
    manifest = result['value']['sample_manifests'][over['sample_manifest_sha256']]
    assert len(manifest) == 25
    assert result['value']['evidence_kind'] == KIND
    assert result['value']['profit_or_PIT_claim'] is False
    assert result['value']['by_option'][options[0]['option_id']]['historical_roi'] is None


def test_requested_window_selects_all_and_only_matching_historical_kickoffs():
    result, options = evaluate(window={'start': '2026-10-22T00:00:00+00:00',
                                      'end': '2026-10-25T00:00:00+00:00'})
    assert sample(result, options)['sample_size'] == 3


@pytest.mark.parametrize('scope', ['team_any_venue', 'team_same_known_venue', 'head_to_head'])
def test_each_declared_team_scope_is_computed_over_all_matching_rows(scope):
    data = rows(3)
    data[1].update(home_team_id='30', away_team_id='10')
    data[2].update(home_team_id='40', away_team_id='50')
    result, options = evaluate(data, scope=scope)
    assert sample(result, options, cohort='home')['sample_size'] == (2 if scope == 'team_any_venue' else 1)
    assert sample(result, options, cohort='away')['sample_size'] == 1


@pytest.mark.parametrize('scope', ['team_any_venue', 'team_same_known_venue', 'head_to_head'])
def test_team_and_h2h_policies_include_other_competitions_explicitly(scope):
    data = rows(2)
    data[1]['league_id'] = '2'
    result, options = evaluate(data, scope=scope)
    own = sample(result, options, cohort='home')
    assert own['sample_size'] == 2
    assert own['competition_counts'] == {'api_football:1': 1, 'api_football:2': 1}
    competition, competition_options = evaluate(data)
    assert sample(competition, competition_options)['sample_size'] == 1


def test_team_h2h_outcome_uses_current_team_role_not_original_home_role():
    _, options, request, view = frame(rows(1), scope='team_any_venue')
    candidates = quotes()
    candidates[0].update(market_key='h2h', selection_key='away')
    options[0]['legs'][0] = deepcopy(candidates[0])
    payload = request.data()
    result = HistoricalScenarioEngine(view).evaluate(payload, options,
        candidates=candidates, fixtures=fixtures())
    samples = result['by_option'][options[0]['option_id']]['legs'][0]['scope_samples']
    assert samples['home']['market_hits'] == 0
    assert samples['away']['market_hits'] == 0


def test_half_time_uses_exact_first_half_scores_and_missing_remains_unknown():
    data = rows(2)
    data[1]['first_half_home'] = None
    _, options, request, view = frame(data)
    candidates = quotes()
    candidates[0].update(period='FIRST_HALF', settlement='FIRST_HALF_PLUS_STOPPAGE')
    options[0]['legs'][0] = deepcopy(candidates[0])
    result = HistoricalScenarioEngine(view).evaluate(request.data(), options,
        candidates=candidates, fixtures=fixtures())
    own = result['by_option'][options[0]['option_id']]['legs'][0]['scope_samples']['competition']
    assert (own['sample_size'], own['market_hits'], own['unknown_period_scores']) == (1, 0, 1)


@pytest.mark.parametrize('change,reason', [
    ({'market_key': 'totals_2_25', 'line': 2.25}, 'unsupported historical market'),
    ({'line': 3.5}, 'EXPLICIT_MARKET_LINE_MISMATCH'),
    ({'handicap': 0}, 'UNSUPPORTED_EXACT_PRODUCT'),
    ({'settlement': 'INCLUDING_EXTRA_TIME'}, 'UNSUPPORTED_EXACT_PERIOD_OR_SETTLEMENT'),
])
def test_unsupported_exact_product_is_local_unknown_without_substitution(change, reason):
    _, options, request, view = frame()
    candidates = quotes()
    candidates[0].update(change)
    options[0]['legs'][0] = deepcopy(candidates[0])
    result = HistoricalScenarioEngine(view).evaluate(request.data(), options,
        candidates=candidates, fixtures=fixtures())
    bad = result['by_option'][options[0]['option_id']]['legs'][0]
    assert bad['status'] == 'UNKNOWN' and reason in bad['reason']
    assert result['by_option'][options[1]['option_id']]['status'] == 'AVAILABLE'


def test_missing_venue_never_defaults_to_home_advantage_and_other_scope_survives():
    data = rows(3)
    for row in data:
        row.pop('neutral_venue')
    result, _ = evaluate(data, scope='team_same_known_venue')
    assert result['status'] == 'UNKNOWN'
    result, options = evaluate(data, scope='team_any_venue')
    assert sample(result, options, cohort='home')['sample_size'] == 3


def test_aet_and_penalty_totals_never_fill_missing_regulation_scores():
    data = rows(2)
    data[0].update(status='AET', full_time_home=None, full_time_away=None,
                   goals={'home': 3, 'away': 2}, extra_time_home=3, extra_time_away=2)
    result, options = evaluate(data)
    own = sample(result, options)
    assert (own['sample_size'], own['unknown_period_scores']) == (1, 1)


def test_future_and_invalid_rows_do_not_erase_the_other_valid_results():
    data = rows(4)
    data[0]['available_at'] = '2026-11-12T18:00:01+00:00'
    data[1]['observed_at'] = '2026-10-02T15:00:00'
    data[2]['full_time_home'] = True
    result, options = evaluate(data)
    assert sample(result, options)['sample_size'] == 1
    assert sum(result['value']['coverage']['excluded_counts'].values()) == 3


def test_late_observed_old_result_is_descriptive_today_never_a_pit_decision():
    data = rows(1)
    data[0]['observed_at'] = '2026-11-12T17:00:00+00:00'
    result, options = evaluate(data)
    assert sample(result, options)['sample_size'] == 1
    assert result['value']['original_strategy_decision_history'] is False
    assert result['value']['profit_or_PIT_claim'] is False
    manifest = next(iter(result['value']['sample_manifests'].values()))
    assert manifest[0]['source_aliases'][0]['knowledge_clocks']['observed_at'] == data[0]['observed_at']


def test_retained_and_online_aliases_count_once_with_both_source_bindings():
    data = rows(2)
    dataset = dataset_snapshot(data, role='normal_standard_venue_history', as_of=NOW,
        recipe={'version': 'TESTONLY'}, lineage={'input_partition': []})
    artifacts = {'history': history(data), 'normal_model_input': {'status': 'COMPLETE',
        'input_view': {'as_of': NOW, 'dataset': dataset, 'history': deepcopy(data)}}}
    view = build_history_view(artifacts, as_of=NOW)
    assert view['input_row_count'] == 4 and len(view['records']) == 2
    assert all(len(row['source_aliases']) == 2 for row in view['records'])
    artifacts['normal_model_input']['input_view']['dataset']['rows'][0]['full_time_home'] = 9
    rejected = build_history_view(artifacts, as_of=NOW)
    assert len(rejected['records']) == 2
    assert rejected['source_issues'][0]['source'] == 'artifacts.normal_model_input'


def test_conflicting_result_aliases_are_quarantined_not_first_row_wins():
    data = rows(2)
    data.append({**data[0], 'full_time_home': 4})
    result, options = evaluate(data)
    assert sample(result, options)['sample_size'] == 1
    assert result['value']['coverage']['excluded_counts']['CONFLICTING_HISTORY_FIXTURE'] == 2


def test_equivalent_historical_kickoff_aliases_deduplicate_and_preserve_original_clocks():
    data = rows(2)
    alternate = {**data[0], 'kickoff': '2026-10-01T14:00:00+02:00',
                 'observed_at': '2026-10-01T15:00:00Z'}
    data.append(alternate)
    view = build_history_view({'history': history(data)}, as_of=NOW)
    assert len(view['records']) == 2 and view['excluded_rows'] == []
    aliases = view['records'][0]['source_aliases']
    assert {row['kickoff'] for row in aliases} == {data[0]['kickoff'], alternate['kickoff']}
    assert {row['knowledge_clocks']['observed_at'] for row in aliases} == {
        data[0]['observed_at'], alternate['observed_at']}


def test_provider_namespaces_are_not_merged_by_numeric_or_team_names():
    data = rows(2)
    data[0]['provider'] = 'the_stats_api'
    result, options = evaluate(data)
    assert sample(result, options)['sample_size'] == 1


def test_all_ten_owners_receive_distinct_receipts_over_one_shared_full_view():
    snapshot, _, _, view = frame()
    service = ProfileToolService(snapshot, historical_view=view)
    sessions = start_sessions(snapshot, {key: get_agent_definition(key).method_version for key in AGENT_IDS})
    results = []
    for state in sessions:
        owner = state.agent_id
        options = [{'option_id': owner + '-' + str(index), 'agent_id': owner, 'legs': [deepcopy(quote)]}
                   for index, quote in enumerate(quotes())]
        state = set_options(state, snapshot, options)
        _, request = request_tool(state, snapshot, 'historical_strategy_evaluation',
            parameters={'window': WINDOW, 'policy_hash': policy_catalog()[0]['policy_hash']})
        result = service.evaluate(request, options)
        assert result['agent_id'] == owner and result['own_option_ids'] == [row['option_id'] for row in options]
        assert len(snapshot.data()['fixture_ids']) == 2
        assert sample(result, options)['sample_size'] == 25
        results.append(result)
    assert len({row['result_id'] for row in results}) == 10
    assert len({row['value']['source_view_sha256'] for row in results}) == 1
    assert len(service._historical_engine._windows) == 1


@pytest.mark.parametrize('window', [
    {'start': '2026-10-01T00:00:00', 'end': WINDOW['end']},
    {'start': WINDOW['start'], 'end': '2026-11-12T18:00:01+00:00'},
    {'start': WINDOW['end'], 'end': WINDOW['start']},
])
def test_invalid_or_future_requested_window_is_unknown_without_widening(window):
    result, _ = evaluate(window=window)
    assert result['status'] == 'UNKNOWN' and result['value'] is None


def test_cached_receipt_is_detached_and_changed_options_need_a_fresh_binding():
    snapshot, options, request, view = frame()
    service = ProfileToolService(snapshot, historical_view=view)
    view['records'][0]['full_time_home'] = 99
    first = service.evaluate(request, options)
    first['value']['by_option'].clear()
    second = service.evaluate(request, options)
    assert second['status'] == 'CACHED' and len(second['value']['by_option']) == 2
    options[0]['legs'][0]['selection_key'] = 'under'
    with pytest.raises(ValueError, match='STALE_TOOL_OPTIONS'):
        service.evaluate(request, options)


def test_engine_sample_manifest_does_not_expose_mutable_internal_source_clocks():
    _, options, request, view = frame(rows(1))
    engine = HistoricalScenarioEngine(view)
    result = engine.evaluate(request.data(), options, candidates=quotes(), fixtures=fixtures())
    manifest = next(iter(result['sample_manifests'].values()))
    manifest[0]['source_aliases'][0]['knowledge_clocks']['observed_at'] = '2099-01-01T00:00:00+00:00'
    second = engine.evaluate(request.data(), options, candidates=quotes(), fixtures=fixtures())
    original = next(iter(second['sample_manifests'].values()))
    assert original[0]['source_aliases'][0]['knowledge_clocks']['observed_at'] == rows(1)[0]['observed_at']


def test_unbound_source_change_cannot_turn_into_valid_descriptive_evidence():
    _, options, request, view = frame()
    options[0]['legs'][0]['selection_key'] = 'under'
    result = HistoricalScenarioEngine(view).evaluate(request.data(), options,
        candidates=quotes(), fixtures=fixtures())
    assert result['by_option'][options[0]['option_id']]['legs'][0]['reason'] == 'SOURCE_PRODUCT_IDENTITY_MISMATCH'


def test_equivalent_aware_quote_and_kickoff_clocks_bind_without_rewriting_originals():
    _, options, request, view = frame()
    candidates = quotes()
    options[0]['legs'][0]['kickoff'] = '2026-11-13T19:00:00+01:00'
    options[0]['legs'][0]['quote_observed_at'] = '2026-11-12T18:00:00Z'
    before = deepcopy(options)
    result = HistoricalScenarioEngine(view).evaluate(request.data(), options,
        candidates=candidates, fixtures=fixtures())
    assert result['by_option'][options[0]['option_id']]['status'] == 'AVAILABLE'
    assert options == before and candidates == quotes()
    options[0]['legs'][0]['quote_observed_at'] = '2026-11-12T18:00:00'
    bad = HistoricalScenarioEngine(view).evaluate(request.data(), options,
        candidates=candidates, fixtures=fixtures())
    assert bad['by_option'][options[0]['option_id']]['legs'][0]['reason'] == 'AWARE_HISTORY_CLOCK_REQUIRED'


def test_empty_data_exposes_catalog_but_no_fabricated_hit_rate():
    result, options = evaluate([])
    assert result['status'] == 'UNKNOWN' and result['value'] is None
    own = result['diagnostics']['by_option'][options[0]['option_id']]['legs'][0]['scope_samples']['competition']
    assert own['sample_size'] == 0 and own['descriptive_hit_rate'] is None
    summary = describe_history_view(build_history_view({}, as_of=NOW))
    assert summary['tool_available'] is True and len(summary['tool_catalog']) == 4
    assert summary['data_status'] == 'UNKNOWN' and summary['available_window'] is None


def test_scenario_builder_receives_only_three_exact_kernel_keys_and_snapshot_copy():
    snapshot, options, request, view = frame()
    artifacts = {'candidate_input': {'candidates': quotes()}, 'numeric_models': {},
                 'fixtures': {'records': fixtures()}, 'huge_other': {'TESTONLY': 'not kernel input'}}
    service = ProfileToolService(snapshot, artifacts=artifacts, historical_view=view)
    assert set(service._artifacts) == {'candidate_input', 'numeric_models', 'fixtures'}
    assert service._artifacts_hash == digest(artifacts)
    artifacts['candidate_input']['candidates'].clear()
    assert len(service._artifacts['candidate_input']['candidates']) == 2
    assert service.evaluate(request, options)['status'] == 'AVAILABLE'


def test_original_chronological_price_decision_branch_remains_separate_and_cost_unknown():
    snapshot, options, _, _ = frame(rows(1))
    state = start_sessions(snapshot, {key: get_agent_definition(key).method_version for key in AGENT_IDS})[0]
    state = set_options(state, snapshot, options)
    policy = 'e' * 64
    _, request = request_tool(state, snapshot, 'historical_strategy_evaluation',
        parameters={'window': {'start': WINDOW['start'], 'end': '2026-11-01T00:00:00+00:00'},
                    'policy_hash': policy})
    payload = request.data()
    decision, kickoff, settled = ('2026-10-02T10:00:00+00:00', '2026-10-02T12:00:00+00:00',
                                  '2026-10-02T14:00:00+00:00')
    model = 'b' * 64

    def envelope(kind, value, available):
        return {'kind': kind, 'payload': value, 'sha256': digest(value), 'available_at': available}

    sources = {
        'feature': envelope('PIT_FEATURES', {'selection_id': 'TESTONLY-original', 'model_sha256': model}, decision),
        'quote': envelope('HISTORICAL_PROVIDER_QUOTE', {'selection_id': 'TESTONLY-original',
            'quoted_at': decision, 'decimal_odds': 2.0, 'currency': 'EUR'}, decision),
        'decision': envelope('HISTORICAL_STRATEGY_DECISION', {'selection_id': 'TESTONLY-original',
            'model_sha256': model, 'agent_id': payload['agent_id'], 'method_version': payload['method_version'],
            'policy_hash': policy, 'decision_at': decision}, decision),
        'settlement': envelope('SETTLEMENT', {'selection_id': 'TESTONLY-original',
            'settled_at': settled, 'stake': 1.0, 'gross_return': 2.0, 'currency': 'EUR'}, settled)}
    record = {key: payload[key] for key in ('agent_id', 'method_version', 'option_hash', 'own_option_ids')}
    record.update(evaluation_id='TESTONLY-original', evaluation_as_of=NOW,
        policy_hash=policy, window=payload['parameters']['window'], sources=sources,
        observations=[{'observation_id': 'TESTONLY-observation', 'selection_id': 'TESTONLY-original',
            'decision_at': decision, 'kickoff': kickoff, 'settled_at': settled, 'model_sha256': model,
            'feature_source': 'feature', 'quote_source': 'quote', 'decision_source': 'decision',
            'settlement_source': 'settlement'}])
    result = ProfileToolService(snapshot, historical_evaluations=[record]).evaluate(request, options)
    assert result['status'] == 'AVAILABLE'
    assert result['value']['roi_before_cost'] == 1.0 and result['value']['roi_after_cost'] is None
    assert result['source_bindings']['evidence_kind'] != KIND
    sources['quote']['payload']['quoted_at'] = None
    sources['quote']['sha256'] = digest(sources['quote']['payload'])
    bad = ProfileToolService(snapshot, historical_evaluations=[record]).evaluate(request, options)
    assert bad['status'] == 'UNKNOWN'


def test_normal_snapshot_keeps_full_calendar_and_publishes_actual_catalog_coverage():
    events = fixtures()
    artifacts = {'history': history(rows(25)), 'candidate_input': {'candidates': quotes()},
        'calendar': {'scope_receipt': {'scope': 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY',
            'date': '2026-11-13', 'status': 'COMPLETE', 'provider_calendar_status': 'COMPLETE',
            'routing_status': 'COMPLETE', 'provider_fixture_ids': [row['fixture_id'] for row in events],
            'events': events}}, 'fixtures': {'records': events}}
    snapshot = build_normal_snapshot(run_id='TESTONLY-normal-history', target_date='2026-11-13',
        snapshot_as_of=NOW, artifacts=artifacts, historical_view=build_history_view(artifacts, as_of=NOW))
    family = snapshot.data()['families']['historical_strategy_evaluation']
    assert family['status'] == 'AVAILABLE'
    assert family['value']['admitted_record_count'] == 25
    assert family['value']['own_result_status'] == 'NOT_REQUESTED'
    assert snapshot.data()['fixture_ids'] == [row['fixture_id'] for row in events]
    legacy = build_normal_snapshot(run_id='TESTONLY-normal-history', target_date='2026-11-13',
        snapshot_as_of=NOW, artifacts=artifacts)
    assert legacy.data()['families']['historical_strategy_evaluation']['status'] == 'UNKNOWN'


def test_executor_installs_descriptive_tool_without_reader_fit_or_paid_host(tmp_path, monkeypatch):
    from goal_analysis.jobs import agent_portfolio, ten_agent_normal

    events, observed = fixtures(), []
    artifacts = {'history': history(rows(2)), 'candidate_input': {'candidates': quotes()},
        'calendar': {'scope_receipt': {'scope': 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY',
            'date': '2026-11-13', 'status': 'COMPLETE', 'provider_calendar_status': 'COMPLETE',
            'routing_status': 'COMPLETE', 'provider_fixture_ids': [row['fixture_id'] for row in events],
            'events': events}}, 'fixtures': {'records': events}}

    class FixtureRunner:
        def __init__(self, **kwargs):
            self.tools = kwargs['tools']

        def run(self, snapshot):
            for owner in AGENT_IDS:
                _, options, _, _ = frame(rows(2), owner=owner)
                state = next(state for state in start_sessions(snapshot,
                    {key: get_agent_definition(key).method_version for key in AGENT_IDS}) if state.agent_id == owner)
                state = set_options(state, snapshot, options)
                _, request = request_tool(state, snapshot, 'historical_strategy_evaluation',
                    parameters={'window': WINDOW, 'policy_hash': policy_catalog()[0]['policy_hash']})
                observed.append(self.tools.evaluate(request, options))
            return {'status': 'COMPLETE', 'host_requests_reserved': 0,
                    'owners': [{'agent_id': owner, 'observed_host_usage': []} for owner in AGENT_IDS]}

    def forbidden_host(*args, **kwargs):
        raise AssertionError('TESTONLY: paid host is forbidden')

    monkeypatch.setattr(ten_agent_normal, 'ProfileDecisionRunner', FixtureRunner)
    monkeypatch.setattr(agent_portfolio, 'build_agent_option_builder', lambda *args: None)
    monkeypatch.setattr(agent_portfolio, 'publish_agent_decisions', lambda *args, **kwargs: None)
    executor = ten_agent_normal.NormalAgentExecutor(host=forbidden_host, journal_root=tmp_path,
        clock=lambda: datetime.fromisoformat(NOW))
    report = executor(run_id='TESTONLY-normal-history', target_date=date(2026, 11, 13),
        as_of=datetime.fromisoformat(NOW), artifacts=artifacts, fixture_records=events,
        enriched={}, club_enriched={}, candidate_context={}, weather_by_fixture={}, historical_runs=[],
        feedback={}, settings={'enabled_profiles': list(AGENT_IDS)}, team_plan={})
    assert len(observed) == 10 and all(row['status'] == 'AVAILABLE' for row in observed)
    assert report['agent_source_readers']['descriptive_recorded_results']['admitted_record_count'] == 2
    assert (tmp_path / 'TESTONLY-normal-history' / 'descriptive-history-input.json').is_file()
