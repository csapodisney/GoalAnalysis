"""Actual CLI routing and persistence with no acquisition, host or production I/O."""

import importlib.util
import json
import sys
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest
import test_ten_agent_output_integration as output_fixture

from goal_analysis.agents.profile_decision_runner import CodexDecisionHost, ProfileDecisionRunner
from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.jobs.ten_agent_normal import NormalAgentExecutor
from goal_analysis.storage.portfolio_ledger import PortfolioLedger

# Reuse the already staged TESTONLY fixture's physical-path/network/process
# boundary. Importing this module does not collect or run its seven test bodies.
owned_output_boundary = output_fixture.owned_output_boundary


@pytest.fixture
def cli_case(tmp_path, monkeypatch):
    from goal_analysis import models
    from goal_analysis.agents import codex_review
    from goal_analysis.jobs import agent_evidence, nemotron
    from goal_analysis.team import manager

    monkeypatch.setattr(sys, 'path', list(sys.path))
    source = output_fixture.SOURCE_ROOT / 'scripts/run-arthur.py'
    spec = importlib.util.spec_from_file_location('TESTONLY_ten_agent_cli', source)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    monkeypatch.chdir(tmp_path)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (output_fixture.NOW.astimezone(tz) if tz else
                    output_fixture.NOW.replace(tzinfo=None))

    monkeypatch.setattr(cli, 'datetime', Clock)
    folder = tmp_path / 'config'
    folder.mkdir()
    live_config = {'schema_version': 1, 'odds_region': 'eu',
        'preferred_bookmakers': ['test_api_football_8'], 'account_region': 'DE', 'currency': 'EUR',
        'max_football_calls': 8, 'max_odds_credits': 20, 'extra_markets': [], 'team_aliases': {},
        'leagues': [{'competition_id': 'TESTONLY-DE1', 'api_football_id': 78,
            'season': 2026, 'history_seasons': [2026], 'odds_sport_key': 'soccer_germany_bundesliga',
            'competition_type': 'LEAGUE'}]}
    config_path, settings_path = folder / 'daily223-live.json', folder / 'arthur-settings.json'
    config_path.write_text(json.dumps(live_config), encoding='utf-8')
    settings_path.write_text(json.dumps({'operating_contract_version': 2}), encoding='utf-8')
    calls = []

    def forbidden(*args, **kwargs):
        raise AssertionError('TESTONLY CLI must not acquire data, invoke a host or fit a model')

    def ledger_factory(path):
        calls.append('ledger')
        assert path == tmp_path / 'data/arthur/ledger.sqlite3'
        return PortfolioLedger(path, now=lambda: output_fixture.NOW)

    class PrimaryDatabase:
        def __init__(self, path):
            calls.append('primary-database-boundary')
            assert path == tmp_path / 'data/goal_analysis.sqlite3'

        def initialize(self):
            calls.append('primary-initialize-boundary')

    class Team:
        def __init__(self, root):
            assert root == tmp_path

        def enroll_missing(self):
            calls.append('team-boundary')
            return {'snapshot': {}}

    football, odds, cache, reviewer, weather = (object() for _ in range(5))

    def primary_boundary(*args, **kwargs):
        calls.append('football-boundary')
        return football

    def odds_boundary(*args, **kwargs):
        calls.append('odds-boundary')
        return odds

    def weather_boundary(root, run_id, *, root_run_lock):
        calls.append('weather-boundary')
        assert root == tmp_path and root_run_lock.stream is not None
        return weather

    monkeypatch.setattr(cli, 'PortfolioLedger', ledger_factory)
    monkeypatch.setattr(cli, 'Database', PrimaryDatabase)
    monkeypatch.setattr(cli, 'CacheStore', lambda database: cache)
    monkeypatch.setattr(cli, 'TheStatsPrimaryClient', primary_boundary)
    monkeypatch.setattr(cli, 'TheStatsOddsFeed', odds_boundary)
    monkeypatch.setattr(cli, 'make_weather_provider', weather_boundary)
    monkeypatch.setattr(cli, 'make_reviewer', lambda settings: reviewer)
    monkeypatch.setattr(manager, 'TeamManager', Team)
    monkeypatch.setattr(codex_review, 'CodexReviewer', forbidden)
    monkeypatch.setattr(ProfileDecisionRunner, 'run', forbidden)
    monkeypatch.setattr(models, 'predict_models', forbidden)
    monkeypatch.setattr(agent_evidence, 'read_current_news', forbidden)
    monkeypatch.setattr(nemotron, 'enabled', lambda root: False)
    monkeypatch.setattr(nemotron, 'analyze', forbidden)
    args = ['--live', '--date', output_fixture.DAY,
            '--config', str(config_path), '--settings', str(settings_path)]
    return SimpleNamespace(cli=cli, root=tmp_path, calls=calls, args=args,
        football=football, odds=odds, cache=cache, reviewer=reviewer, weather=weather,
        forbidden=forbidden)


def test_actual_cli_new_mode_routes_full_day_and_saves_twenty_generated_cards(cli_case, monkeypatch, capsys):
    case = cli_case
    issued, artifacts = output_fixture.completed_output(run_id='TESTONLY-cli-twenty')
    full_ids = [leg['fixture_id'] for card in issued['generated_tickets'] for leg in card['legs']]
    receipt = {'status': 'COMPLETE', 'scope': 'ACCESSIBLE_PROVIDER_FOOTBALL_DAY',
        'target_date': output_fixture.DAY, 'provider_fixture_ids': full_ids,
        'selection': 'ALL_PROVIDER_DAY_FIXTURES', 'TESTONLY': True}
    issued['full_day_calendar'] = deepcopy(receipt)
    artifacts['calendar'] = {'scope_receipt': deepcopy(receipt)}
    received = []

    def collection_boundary(config, settings, football, odds, cache, day, reviewer, ledger, **kwargs):
        case.calls.append('collection-boundary')
        assert football is case.football and odds is case.odds and cache is case.cache
        assert reviewer is case.reviewer and isinstance(ledger, PortfolioLedger)
        assert day.isoformat() == output_fixture.DAY
        assert kwargs['weather_provider'] is case.weather
        assert 'selected_competition_ids' not in kwargs
        assert kwargs['normal_history_root'] == case.root
        executor = kwargs['agent_executor']
        assert isinstance(executor, NormalAgentExecutor) and isinstance(executor.host, CodexDecisionHost)
        assert executor.journal_root == case.root / 'data/arthur/agent-decisions'
        assert executor.clock() == output_fixture.NOW
        assert executor.max_requests == 20 and executor.timeout_seconds == 3600
        assert set(AGENT_IDS) <= set(settings['enabled_profiles'])
        assert executor.historical_reader is None  # Explicit remaining integration gap.
        assert config['leagues'][0]['api_football_id'] == 78
        assert len(full_ids) == 80  # Fixture receipt is not restricted to that configured league.
        received.append(executor)
        issued['settings'] = deepcopy(settings)
        artifacts['report'] = deepcopy(issued)
        return {'schema_version': 3, 'artifacts': deepcopy(artifacts)}

    monkeypatch.setattr(case.cli, 'run_portfolio', collection_boundary)
    assert case.cli.main(case.args) == 0
    assert len(received) == 1 and case.calls.count('collection-boundary') == 1
    assert not received[0].journal_root.exists()  # No agent invocation happened at this boundary.
    ledger = PortfolioLedger(case.root / 'data/arthur/ledger.sqlite3', now=lambda: output_fixture.NOW)
    saved = ledger.list_runs(date=output_fixture.DAY)
    assert len(saved) == 1 and len(saved[0]['generated_tickets']) == 20
    assert saved[0]['tickets'] == [] and saved[0]['real_wager_placed'] is False
    assert all(card['status'] == 'DRAFT' and card['publication_scope'] == 'RESEARCH'
               and card['playable'] is False for card in saved[0]['generated_tickets'])
    report_file = case.root / 'reports/arthur' / issued['run_id'] / 'report.json'
    assert json.loads(report_file.read_text('utf-8')) == saved[0]
    assert saved[0]['full_day_calendar'] == receipt
    assert len(ledger.list_tickets(date=output_fixture.DAY)) == 20
    assert len(ledger.list_recommendations(date=output_fixture.DAY)) == 20
    assert ledger.list_tickets(date=output_fixture.DAY, mode='actual') == []
    console = capsys.readouterr().out
    assert 'generated=20; tickets=0;' in console and 'ARTHUR: COMPLETE;' in console
    assert 'No wager was placed.' in console


def test_actual_cli_rejects_manual_league_scope_before_lock_or_any_constructor(cli_case, monkeypatch, capsys):
    case = cli_case
    monkeypatch.setattr(case.cli, 'RunLock', case.forbidden)
    monkeypatch.setattr(case.cli, 'run_portfolio', case.forbidden)
    assert case.cli.main([*case.args, '--competition-ids', '78']) == 1
    assert case.calls == []
    assert not (case.root / 'data').exists() and not (case.root / 'reports').exists()
    assert 'manual league scope' in capsys.readouterr().out


@pytest.mark.parametrize('with_prior_cards', [False, True])
def test_actual_cli_preserves_one_blocked_collection_as_empty_current_run_without_retry(
    cli_case, monkeypatch, capsys, with_prior_cards,
):
    case = cli_case
    ledger = PortfolioLedger(case.root / 'data/arthur/ledger.sqlite3', now=lambda: output_fixture.NOW)
    if with_prior_cards:
        previous, _ = output_fixture.completed_output(run_id='TESTONLY-cli-prior-twenty')
        ledger.save_run(previous)
    prior_tickets = ledger.list_tickets(date=output_fixture.DAY)
    prior_recommendations = ledger.list_recommendations(date=output_fixture.DAY, archive=True)

    def failed_collection(*args, **kwargs):
        case.calls.append('failed-collection-boundary')
        assert isinstance(kwargs['agent_executor'], NormalAgentExecutor)
        raise ValueError('TESTONLY_REQUIRED_SOURCE_UNAVAILABLE')

    monkeypatch.setattr(case.cli, 'run_portfolio', failed_collection)
    assert case.cli.main(case.args) == 2
    assert case.calls.count('failed-collection-boundary') == 1
    ledger = PortfolioLedger(case.root / 'data/arthur/ledger.sqlite3', now=lambda: output_fixture.NOW)
    all_runs = ledger.list_runs(date=output_fixture.DAY)
    assert len(all_runs) == (2 if with_prior_cards else 1)
    if with_prior_cards:
        assert previous in all_runs and len(prior_tickets) == len(prior_recommendations) == 20
    saved = [report for report in all_runs if report['construction_status'] == 'DATA_BLOCKED']
    assert len(saved) == 1 and saved[0]['construction_status'] == 'DATA_BLOCKED'
    assert saved[0]['generated_tickets'] == [] and saved[0]['tickets'] == []
    assert saved[0]['generation_capacity']['generated_count'] == 0
    assert saved[0]['generation_capacity']['shortfall'] == 15
    assert saved[0]['astra']['status'] == 'UNAVAILABLE' and saved[0]['real_wager_placed'] is False
    assert saved[0]['diagnostics'] == [{'code': 'RUN_FAILED', 'message': 'TESTONLY_REQUIRED_SOURCE_UNAVAILABLE'}]
    files = list((case.root / 'reports/arthur').glob('*/report.json'))
    assert len(files) == 1 and json.loads(files[0].read_text('utf-8')) == saved[0]
    assert ledger.list_tickets(date=output_fixture.DAY) == prior_tickets
    assert ledger.list_recommendations(date=output_fixture.DAY, archive=True) == prior_recommendations
    assert not (case.root / 'data/arthur/agent-decisions').exists()
    console = capsys.readouterr().out
    assert 'ARTHUR: DATA_BLOCKED; generated=0; tickets=0;' in console
