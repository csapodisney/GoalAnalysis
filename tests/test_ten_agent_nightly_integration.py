"""TESTONLY real nightly stages with already-completed fixture output and owned DB.

No scheduler registration, actual collector, host, model, fit or replay occurs.
The shared public fixture provides cards, not evidence of real agent reasoning.
"""

from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
import test_ten_agent_output_integration as output_fixture

from goal_analysis.config.portfolio import validate_settings
from goal_analysis.jobs import nightly
from goal_analysis.jobs.portfolio import run_portfolio
from goal_analysis.storage.portfolio_ledger import PortfolioLedger
from goal_analysis.team.routing import generation_plan

owned_output_boundary = output_fixture.owned_output_boundary
NOW, DAY = output_fixture.NOW, output_fixture.DAY


@pytest.fixture
def stage_case(tmp_path, monkeypatch):
    from goal_analysis.jobs import fixture_contexts, ticket_evidence
    from goal_analysis.team import routing

    settings = validate_settings({'operating_contract_version': 2})
    manifest = {'application_version': 'TESTONLY', 'manifest_sha256': 'a' * 64,
                'source_files': {}, 'profiles': {}, 'TESTONLY': True}
    monkeypatch.setattr(nightly, 'strategy_manifest', lambda supplied: deepcopy(manifest))

    def forbidden(*args, **kwargs):
        raise AssertionError('TESTONLY independent output must not use legacy publication mutation/audit')

    monkeypatch.setattr(nightly, 'attach_strategy_provenance', forbidden)
    monkeypatch.setattr(routing, 'apply_publication_policy', forbidden)
    actual_build = ticket_evidence.build_ticket_evidence

    def current_audit(report, artifacts):
        owner_run = report.get('ten_agent_decisions') or {}
        if owner_run.get('version') != 'ten-agent-decision-run-v1':
            return forbidden()
        return actual_build(report, artifacts)

    monkeypatch.setattr(ticket_evidence, 'build_ticket_evidence', current_audit)
    # The current audit rebinds its own pure saved-evidence contract.
    # Legacy publication mutation and the legacy audit entry remain forbidden.
    monkeypatch.setattr(fixture_contexts, 'shadow_archive', lambda *args, **kwargs: {'status': 'DISABLED'})
    clock = [NOW]
    ledger = PortfolioLedger(tmp_path / 'data/arthur/ledger.sqlite3', now=lambda: clock[0])
    calls, issued = [], []

    def producer(target, supplied_settings, job):
        calls.append('TESTONLY-already-completed-output')
        run_id = next(stage for stage in job['stages'] if stage['name'] == 'generation')['run_id']
        report, artifacts = output_fixture.completed_output(run_id=run_id)
        assert target.isoformat() == DAY
        report['settings'] = deepcopy(supplied_settings)
        report['team'] = deepcopy(job['team_plan'])
        issued.append(deepcopy(report))
        return {'artifacts': {**artifacts, 'report': report}}

    workflow = nightly.NightlyWorkflow(tmp_path, clock=lambda: clock[0], ledger=ledger, generator=producer)
    job = {'job_id': f'nightly-{DAY}', 'target_date': DAY, 'slot_date': DAY,
        'scheduled_at': NOW.isoformat(), 'deadline': (NOW + timedelta(hours=5)).isoformat(),
        'status': 'PENDING', 'settings': settings, 'config': {}, 'manifest_sha256': manifest['manifest_sha256'],
        'team_plan': generation_plan(settings['enabled_profiles']), 'usage': {'generation_football_calls': 0},
        'stages': [{'name': name, 'status': 'PENDING', 'attempts': 0, 'max_attempts': 3,
                    'last_started_at': None, 'last_finished_at': None, 'next_retry_at': None,
                    'reason': None, 'result': {}} for name in nightly.STAGES]}
    return SimpleNamespace(workflow=workflow, job=job, clock=clock, calls=calls, issued=issued,
                           ledger=ledger, root=tmp_path)


def test_actual_stage_preserves_bound_id_twenty_variants_and_freezes_complete_set(stage_case):
    case = stage_case
    case.workflow._execute_stage(case.job, 'generation')
    stage = case.workflow._stage(case.job, 'generation')
    assert stage['status'] == 'SUCCESS' and stage['attempts'] == 1
    assert stage['result']['generated_count'] == stage['result']['recommendations'] == 20
    assert stage['result']['profile_count'] == stage['result']['completed_owner_count'] == 10
    expected = f'nightly-{DAY}-a1'
    report = case.ledger.list_runs(date=DAY)[0]
    assert report['run_id'] == expected and report['recommendations'] == []
    assert report['generated_tickets'] == case.issued[0]['generated_tickets']
    assert report['ten_agent_decisions'] == case.issued[0]['ten_agent_decisions']
    assert all(card['agent_product_binding']['run_id'] == expected for card in report['generated_tickets'])
    assert report['team'] == case.job['team_plan']
    assert report['strategy_manifest']['TESTONLY'] is True
    assert report['ticket_evidence']['status'] == 'PARTIAL'
    assert report['ticket_evidence']['snapshot_binding']['status'] == 'UNKNOWN'
    assert report['ticket_evidence']['accounting']['canonical_card_count'] == 20
    assert report['ticket_evidence']['accounting']['audited_card_count'] == 20
    assert all(row['own_selection']['status'] == 'UNKNOWN'
               for row in report['ticket_evidence']['cards'])
    assert len(case.ledger.list_tickets(date=DAY)) == len(case.ledger.list_recommendations(date=DAY)) == 20
    assert (case.root / 'reports/arthur' / expected / 'report.json').is_file()
    case.workflow._execute_stage(case.job, 'generation')
    assert len(case.calls) == 1  # Terminal stage never resubmits its producer.
    result, status = case.workflow._archive(case.job)
    assert status == 'SUCCESS' and result['recommendation_count'] == 20
    assert len(case.ledger.list_freezes()[0]['recommendations']) == 20


def test_wrong_bound_run_id_blocks_without_postdecision_rewrite_or_ledger_write(stage_case):
    case = stage_case

    def wrong(target, settings, job):
        case.calls.append('wrong-bound-fixture')
        report, artifacts = output_fixture.completed_output(run_id='TESTONLY-wrong-bound-source')
        return {'artifacts': {**artifacts, 'report': report}}

    case.workflow.generator = wrong
    case.workflow._execute_stage(case.job, 'generation')
    stage = case.workflow._stage(case.job, 'generation')
    assert stage['status'] == 'BLOCKED' and stage['reason'] == 'TEN_AGENT_BOUND_GENERATION_IDENTITY_REQUIRED'
    assert case.ledger.list_runs() == [] and stage['next_retry_at'] is None
    case.workflow._execute_stage(case.job, 'generation')
    assert case.calls == ['wrong-bound-fixture']


def test_partial_owner_execution_stays_partial_even_with_eighteen_cards(stage_case):
    case = stage_case
    original = case.workflow.generator

    def partial(*args):
        bundle = original(*args)
        report = bundle['artifacts']['report']
        owner = report['ten_agent_decisions']['owners'][-1]
        owner.update(status='INCOMPLETE_NO_RETRY', reason='TESTONLY incomplete owner')
        owner.pop('decision')
        report['generated_tickets'] = [card for card in report['generated_tickets']
                                       if card['profile_id'] != owner['agent_id']]
        report['ten_agent_decisions'].update(status='PARTIAL', canonical_ticket_count=18)
        report['astra']['status'] = 'PARTIAL'
        report['construction_status'] = 'PARTIAL'
        output_fixture.refresh_generation_capacity(report)
        return bundle

    case.workflow.generator = partial
    case.workflow._execute_stage(case.job, 'generation')
    stage = case.workflow._stage(case.job, 'generation')
    assert stage['status'] == 'PARTIAL' and stage['result']['generated_count'] == 18
    assert stage['result']['completed_owner_count'] == 9
    assert len(case.ledger.list_recommendations(date=DAY)) == 18
    case.workflow._execute_stage(case.job, 'generation')
    assert len(case.calls) == 1


def test_current_team_pause_saves_empty_partial_without_withdrawing_prior_cards(stage_case):
    case = stage_case
    previous, _ = output_fixture.completed_output(run_id='TESTONLY-before-team-pause')
    case.ledger.save_run(previous)
    prior_tickets = case.ledger.list_tickets(date=DAY)
    prior_recommendations = case.ledger.list_recommendations(date=DAY, archive=True)
    case.job['team_plan']['blocked_profiles'] = list(case.job['settings']['enabled_profiles'])

    def paused(target, settings, job):
        case.calls.append('TESTONLY-team-paused')
        run_id = case.workflow._stage(job, 'generation')['run_id']
        report = {'schema_version': 3, 'module': 'ARTHUR_PORTFOLIO', 'run_id': run_id,
            'date': target.isoformat(), 'observed_at': NOW.isoformat(), 'finished_at': NOW.isoformat(),
            'construction_status': 'TEAM_PAUSED', 'settings': deepcopy(settings),
            'tickets': [], 'generated_tickets': [], 'recommendations': [], 'profiles': [],
            'astra': {'status': 'NO_DATA'}, 'usage': {}, 'real_wager_placed': False}
        output_fixture.refresh_generation_capacity(report)
        return {'schema_version': 3, 'artifacts': {'report': report}}

    case.workflow.generator = paused
    case.workflow._execute_stage(case.job, 'generation')
    stage = case.workflow._stage(case.job, 'generation')
    assert stage['status'] == 'PARTIAL' and stage['result']['reason'] == 'TEAM_PAUSED'
    assert stage['result']['generated_count'] == stage['result']['completed_owner_count'] == 0
    assert stage['result']['astra_status'] == 'NO_DATA' and stage['next_retry_at'] is None
    saved = case.ledger.list_runs(date=DAY)
    current = next(report for report in saved if report['run_id'] == stage['run_id'])
    assert current['construction_status'] == 'TEAM_PAUSED' and current['generated_tickets'] == []
    assert 'ten_agent_decisions' not in current and previous in saved
    assert len(prior_tickets) == len(prior_recommendations) == 20
    assert case.ledger.list_tickets(date=DAY) == prior_tickets
    assert case.ledger.list_recommendations(date=DAY, archive=True) == prior_recommendations
    assert (case.root / 'reports/arthur' / stage['run_id'] / 'report.json').is_file()
    case.workflow._execute_stage(case.job, 'generation')
    assert case.calls == ['TESTONLY-team-paused']


def test_current_generation_temporary_failure_is_terminal_without_automatic_retry(stage_case):
    case = stage_case

    def failed(*args):
        case.calls.append('TESTONLY-failure')
        raise RuntimeError('TESTONLY temporary error after unknown host outcome')

    case.workflow.generator = failed
    case.workflow._execute_stage(case.job, 'generation')
    stage = case.workflow._stage(case.job, 'generation')
    assert stage['status'] == 'BLOCKED' and stage['reason'] == 'TEMPORARY_FAILURE'
    assert stage['attempts'] == 1 and stage['next_retry_at'] is None
    case.clock[0] += timedelta(minutes=20)
    case.workflow._execute_stage(case.job, 'generation')
    assert case.calls == ['TESTONLY-failure'] and stage['run_id'] == f'nightly-{DAY}-a1'


def test_existing_current_retry_state_does_not_mint_another_paid_attempt(stage_case):
    case = stage_case
    stage = case.workflow._stage(case.job, 'generation')
    stage.update(status='RETRY_WAIT', attempts=1, run_id=f'nightly-{DAY}-a1')
    case.workflow._execute_stage(case.job, 'generation')
    assert stage['status'] == 'BLOCKED'
    assert stage['reason'] == 'TEN_AGENT_GENERATION_NO_AUTOMATIC_RETRY'
    assert stage['attempts'] == 1 and stage['run_id'] == f'nightly-{DAY}-a1'
    assert case.calls == []


def test_legacy_generation_keeps_existing_temporary_retry_policy(stage_case):
    case = stage_case
    case.job['settings'] = validate_settings({'operating_contract_version': 1})

    def failed(*args):
        case.calls.append('TESTONLY-legacy-failure')
        raise RuntimeError('TESTONLY legacy temporary error')

    case.workflow.generator = failed
    case.workflow._execute_stage(case.job, 'generation')
    stage = case.workflow._stage(case.job, 'generation')
    assert stage['status'] == 'RETRY_WAIT' and stage['next_retry_at'] is not None
    case.clock[0] += timedelta(minutes=20)
    case.workflow._execute_stage(case.job, 'generation')
    assert len(case.calls) == 2 and stage['attempts'] == 2


@pytest.mark.parametrize('current', [True, False])
def test_default_generate_passes_stage_identity_before_independent_execution(stage_case, monkeypatch, current):
    from goal_analysis import storage
    from goal_analysis.agents import codex_review
    from goal_analysis.jobs import sports_context_archive, ten_agent_normal
    from goal_analysis.providers import the_stats_odds

    case = stage_case
    settings = validate_settings({'operating_contract_version': 2 if current else 1})
    case.job['settings'] = settings
    case.job['config'] = {'max_football_calls': 8, 'max_odds_credits': 20, 'odds_region': 'eu'}
    run_id = f'nightly-{DAY}-a1'
    case.workflow._stage(case.job, 'generation')['run_id'] = run_id
    executor = object()
    calls = []

    class Database:
        def __init__(self, path):
            assert path == case.root / 'data/goal_analysis.sqlite3'
            calls.append('database-boundary')

        def initialize(self):
            pass

    def factory(root, supplied, *, clock):
        assert root == case.root and supplied == settings and clock() == NOW
        calls.append('executor-boundary')
        return executor

    def coordinator(*args, **kwargs):
        assert kwargs['agent_executor'] is (executor if current else None)
        assert kwargs.get('publication_run_id') == (run_id if current else None)
        assert ('publication_run_id' in kwargs) is current
        assert 'selected_competition_ids' not in kwargs
        calls.append('coordinator-boundary')
        return {'TESTONLY': 'no acquisition or execution'}

    monkeypatch.setattr(storage, 'Database', Database)
    monkeypatch.setattr(storage, 'CacheStore', lambda db: object())
    monkeypatch.setattr(ten_agent_normal, 'make_normal_agent_executor', factory)
    monkeypatch.setattr(codex_review, 'CodexReviewer', lambda **kwargs: object())
    monkeypatch.setattr(the_stats_odds, 'TheStatsOddsFeed', lambda *args, **kwargs: object())
    monkeypatch.setattr(sports_context_archive, 'make_weather_provider', lambda *args, **kwargs: object())
    monkeypatch.setattr(case.workflow, '_football', lambda *args: object())
    monkeypatch.setattr(nightly, 'run_portfolio', coordinator)
    assert case.workflow._generate(date.fromisoformat(DAY), settings, case.job)['TESTONLY']
    assert calls == (['executor-boundary'] if current else []) + ['database-boundary', 'coordinator-boundary']


@pytest.mark.parametrize('identifier', ['../TESTONLY-escape', 'nightly-2026-11-11-a1', f'nightly-{DAY}-a4'])
def test_publication_identity_rejects_before_configuration_collection_or_model_import(identifier):
    with pytest.raises(ValueError, match='SAFE_TARGET_DAY_PUBLICATION_RUN_ID_REQUIRED'):
        run_portfolio(None, None, None, None, None, date.fromisoformat(DAY),
                      publication_run_id=identifier)

