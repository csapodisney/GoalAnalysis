"""Self-contained pilot fixtures: real runner, inert host/tools, no external I/O."""
from collections import Counter
from datetime import UTC, datetime, timedelta
from threading import Lock

import pytest

from goal_analysis.agents.profile_decision import SIMULATOR_ROLES
from goal_analysis.agents.profile_decision_runner import (
    DecisionBudget,
    DecisionJournal,
    ProfileDecisionRunner,
)
from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.jobs.agent_snapshot import build_snapshot, digest
from goal_analysis.jobs.ten_agent_normal import NormalAgentExecutor

NOW = datetime(2026, 11, 13, 8, tzinfo=UTC)


def snapshot():
    fixtures = [{'fixture_id': f'TESTONLY:f{i}', 'source_candidate_id': f'TESTONLY:c{i}'} for i in range(2)]
    ids = [row['fixture_id'] for row in fixtures]
    return build_snapshot(run_id='TESTONLY-pilot', target_date='2026-11-13', as_of=NOW.isoformat(),
        authoritative_fixture_ids=ids,
        calendar_receipt={'target_date': '2026-11-13', 'scope': 'FULL_PROVIDER_DAY', 'status': 'COMPLETE',
                          'fixture_ids': ids, 'source_refs': ['TESTONLY:calendar']},
        fixtures=fixtures, family_values={'odds_and_price_history': {
            'status': 'AVAILABLE', 'source_refs': ['TESTONLY:quotes'], 'value': {'candidate_input': {'candidates': [
                {'candidate_id': row['source_candidate_id'], 'fixture_id': row['fixture_id'],
                 'market': 'totals_2_5', 'outcome': 'over', 'period': 'FULL_TIME',
                 'decimal_price': 2.0, 'quoted_at': None} for row in fixtures]}}}},
        source_manifest={'TESTONLY:calendar': 'a' * 64, 'TESTONLY:quotes': 'b' * 64})


def builder(owner, proposal, shared):
    ids = {f'TESTONLY:c{i}': f'TESTONLY:f{i}' for i in range(2)}
    return {'agent_id': owner, 'option_id': proposal['option_id'], 'reason': proposal['reason'],
        'legs': [{'candidate_id': cid, 'fixture_id': ids[cid], 'market': 'totals_2_5',
                  'outcome': 'over', 'period': 'FULL_TIME', 'decimal_price': 2.0}
                 for cid in proposal['source_candidate_ids']]}


class Tools:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def evaluate(self, request, options):
        value = request.data()
        self.calls.append(value)
        assert value['option_hash'] == digest(options)
        return {key: value[key] for key in ('request_id', 'agent_id', 'snapshot_id', 'option_hash', 'own_option_ids')} | {
            'status': 'UNKNOWN', 'value': None, 'reason': 'TESTONLY_NO_ACTUAL_HISTORY_OR_SCENARIO'}


class Host:
    def __init__(self, *, fail=False, abstain=False):
        self.fail, self.abstain, self.calls, self.lock = fail, abstain, [], Lock()

    def __call__(self, request, *, timeout):
        assert 0 < timeout <= 300
        identity = {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
        with self.lock:
            self.calls.append((request['agent_id'], request['phase']))
        if self.fail:
            raise RuntimeError('TESTONLY_HOST_FAILED')
        shared = request['data']['snapshot']
        if request['phase'] == 'PLAN':
            return {**identity, 'options': [{'option_id': request['agent_id'] + ':own',
                'reason': 'TESTONLY own proposal', 'source_candidate_ids': ['TESTONLY:c0', 'TESTONLY:c1']}],
                'fixture_reviews': [{'fixture_id': key, 'reason': 'TESTONLY full calendar', 'rejected': False}
                                    for key in shared['fixture_ids']],
                'tool_requests': [{'role': role, 'parameters': {'window': None, 'policy_hash': None}}
                                  for role in SIMULATOR_ROLES]}
        options = request['data']['validated_own_options']
        return {**identity, 'option_ids': [] if self.abstain else [row['option_id'] for row in options],
            'reason': 'TESTONLY abstention' if self.abstain else 'TESTONLY own decision',
            'tool_interpretations': [{'request_id': row['request']['request_id'],
                                     'interpretation': 'UNKNOWN is not a neutral estimate'}
                                    for row in request['data']['tool_results']],
            'family_uses': [{'family': name,
                'status': 'AVAILABLE_AND_CONSIDERED' if family['status'] == 'AVAILABLE' else 'MISSING',
                'reason': 'TESTONLY bound family state', 'value_sha256': family['value_sha256'],
                'option_ids': [row['option_id'] for row in options] if family['status'] == 'AVAILABLE' else [],
                'risk': 'TESTONLY no fabricated evidence'} for name, family in shared['families'].items()]}


def runner(tmp_path, host):
    shared = snapshot()
    journal = DecisionJournal(tmp_path / 'journal', shared)
    budget = DecisionBudget(20, NOW + timedelta(seconds=60), clock=lambda: NOW)
    return shared, journal, budget, ProfileDecisionRunner(host=host, tools=Tools(), option_builder=builder,
                                                         journal=journal, budget=budget)


def test_default_none_and_successful_nonfirst_pilot_use_each_owner_once_with_shared_budget(tmp_path):
    for mode in (None, AGENT_IDS[-1]):
        host = Host()
        shared, journal, budget, actual = runner(tmp_path / str(mode), host)
        original = shared.payload_json
        result = actual.run(shared, pilot_agent_id=mode)
        assert result['status'] == 'COMPLETE' and result['agent_count'] == 10
        assert [row['agent_id'] for row in result['owners']] == list(AGENT_IDS)
        assert Counter(host.calls) == Counter((owner, phase) for owner in AGENT_IDS for phase in ('PLAN', 'FINAL'))
        assert budget.used_requests == 20 and shared.payload_json == original
        assert (journal.directory / 'run.claim.json').is_file()
        if mode is None:
            assert 'pilot_gate' not in result and 'unattempted_agent_ids' not in result
            assert not (journal.directory / 'pilot-gate.json').exists()
        else:
            assert host.calls[:2] == [(mode, 'PLAN'), (mode, 'FINAL')]
            assert result['pilot_gate']['status'] == 'PASSED' and result['unattempted_agent_ids'] == []


def test_failed_pilot_stops_after_one_real_owner_claim_and_cannot_reclaim_run(tmp_path):
    host = Host(fail=True)
    shared, journal, budget, actual = runner(tmp_path, host)
    result = actual.run(shared, pilot_agent_id=AGENT_IDS[0])
    assert result['status'] == 'PARTIAL' and result['agent_count'] == 1
    assert result['owners'][0]['status'] == 'INCOMPLETE_NO_RETRY'
    assert result['unattempted_agent_ids'] == list(AGENT_IDS[1:])
    assert host.calls == [(AGENT_IDS[0], 'PLAN')] and budget.used_requests == 1
    assert len(list(journal.directory.glob('*.owner.json'))) == 1
    with pytest.raises(FileExistsError):
        actual.run(shared, pilot_agent_id=AGENT_IDS[0])
    assert len(host.calls) == 1


def test_abstaining_pilot_normal_reports_one_actual_owner_and_never_calls_ten_owner_publisher(tmp_path, monkeypatch):
    shared, host = snapshot(), Host(abstain=True)
    monkeypatch.setattr('goal_analysis.jobs.ten_agent_normal.build_normal_snapshot', lambda **kwargs: shared)
    monkeypatch.setattr('goal_analysis.jobs.ten_agent_normal.build_history_view',
                        lambda *args, **kwargs: {'records': [], 'source_view_sha256': '0' * 64})
    monkeypatch.setattr('goal_analysis.jobs.ten_agent_normal.ProfileToolService', Tools)
    monkeypatch.setattr('goal_analysis.jobs.agent_portfolio.build_agent_option_builder', lambda *args: builder)

    def forbidden_publisher(*args, **kwargs):
        raise AssertionError('A one-owner pilot stop must not enter the ten-owner publisher')

    monkeypatch.setattr('goal_analysis.jobs.agent_portfolio.publish_agent_decisions', forbidden_publisher)
    execute = NormalAgentExecutor(host=host, journal_root=tmp_path, clock=lambda: NOW,
                                  pilot_agent_id=AGENT_IDS[0])
    artifacts = {'calendar': {'scope_receipt': {'provider_fixture_ids': list(shared.fixture_ids)}}}
    report = execute(run_id='TESTONLY-pilot', target_date=NOW.date(), as_of=NOW, artifacts=artifacts,
        fixture_records=[], enriched={}, club_enriched={}, candidate_context={}, weather_by_fixture={},
        historical_runs=[], feedback={}, settings={'enabled_profiles': list(AGENT_IDS)}, team_plan={})
    assert report['construction_status'] == 'PARTIAL' and report['generated_tickets'] == []
    assert report['ten_agent_decisions']['agent_count'] == 1
    assert report['ten_agent_decisions']['owners'][0]['decision']['status'] == 'ABSTAIN'
    assert report['unattempted_agent_ids'] == list(AGENT_IDS[1:])
    assert host.calls == [(AGENT_IDS[0], 'PLAN'), (AGENT_IDS[0], 'FINAL')]


def test_invalid_pilot_is_rejected_before_run_claim_or_host_budget(tmp_path):
    host = Host()
    shared, journal, budget, actual = runner(tmp_path, host)
    with pytest.raises(ValueError, match='VALID_PILOT_AGENT_REQUIRED'):
        actual.run(shared, pilot_agent_id='TESTONLY-foreign-owner')
    with pytest.raises(ValueError, match='VALID_PILOT_AGENT_REQUIRED'):
        NormalAgentExecutor(host=host, journal_root=tmp_path, clock=lambda: NOW, pilot_agent_id='TESTONLY-foreign-owner')
    assert host.calls == [] and budget.used_requests == 0 and not (journal.directory / 'run.claim.json').exists()
