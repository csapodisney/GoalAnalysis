"""New regression completion after preserved FIRST142 result; no host or DB calls."""
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from goal_analysis.agents.codex_review import CodexReviewer, _profile_usage
from goal_analysis.agents.profile_decision import start_sessions
from goal_analysis.agents.profile_decision_runner import (
    DecisionBudget,
    DecisionJournal,
    ProfileDecisionRunner,
)
from goal_analysis.agents.profile_input import decision_view
from goal_analysis.agents.profile_roster import ACTIVE_AGENTS, get_agent_definition
from goal_analysis.jobs.agent_snapshot import build_snapshot, digest

NOW = datetime(2026, 11, 12, 15, tzinfo=UTC)


def _snapshot_and_states():
    ids = ['TESTONLY:release-completion:unpriced']
    snapshot = build_snapshot(
        run_id='TESTONLY:semantic-once-completion', target_date='2026-11-13', as_of=NOW.isoformat(),
        authoritative_fixture_ids=ids,
        calendar_receipt={'target_date': '2026-11-13', 'scope': 'FULL_PROVIDER_DAY',
                          'status': 'COMPLETE', 'fixture_ids': ids, 'source_refs': ['TESTONLY:source']},
        fixtures=[{'fixture_id': ids[0]}], family_values={}, source_manifest={'TESTONLY:source': 'a' * 64},
    )
    return snapshot, start_sessions(snapshot, {row.agent_id: row.method_version for row in ACTIVE_AGENTS})


def _runner(tmp_path, snapshot, host):
    journal = DecisionJournal(tmp_path / 'TESTONLY-once-journal', snapshot)
    runner = ProfileDecisionRunner(
        host=host, tools=object(), option_builder=lambda *args: None, journal=journal,
        budget=DecisionBudget(20, NOW + timedelta(seconds=30), clock=lambda: NOW),
    )
    return journal, runner


def test_same_owner_session_snapshot_phase_cannot_resubmit_when_view_body_changes(tmp_path):
    snapshot, states = _snapshot_and_states()
    state = states[0]
    host = Mock(side_effect=AssertionError('TESTONLY_EXISTING_PURPOSE_MUST_NOT_CALL_HOST'))
    journal, runner = _runner(tmp_path, snapshot, host)
    original_request = {
        'agent_id': state.agent_id, 'session_id': state.session_id, 'snapshot_id': state.snapshot_id,
        'phase': 'PLAN', 'persona': get_agent_definition(state.agent_id).as_dict(),
        'data': {'snapshot': snapshot.data()},
    }
    key = journal.claim(original_request)
    original_claim = (journal.directory / f'{key}.claim.json').read_bytes()
    projected = decision_view(snapshot)
    assert digest(projected) != digest(snapshot.data())
    with pytest.raises(FileExistsError):
        runner._call(state, 'PLAN', {'snapshot': projected})
    host.assert_not_called()
    assert (journal.directory / f'{key}.claim.json').read_bytes() == original_claim
    assert len(list(journal.directory.glob('*.claim.json'))) == 1
    claim = json.loads(original_claim)
    assert claim['request_sha256'] == digest(original_request)
    assert claim['request'] == original_request
    assert not list(journal.directory.glob('*.outcome.json'))


def test_different_owner_or_phase_has_its_own_once_claim_and_body_hash(tmp_path):
    snapshot, states = _snapshot_and_states()
    calls = []

    def host(request, *, timeout):
        assert 0 < timeout <= 30
        calls.append(request)
        return {key: request[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}

    journal, runner = _runner(tmp_path, snapshot, host)
    for state, phase in ((states[0], 'PLAN'), (states[1], 'PLAN'), (states[0], 'FINAL')):
        reply = runner._call(state, phase, {'TESTONLY': 'same supplied payload for distinct purposes'})
        assert reply['agent_id'] == state.agent_id and reply['phase'] == phase
    assert [(row['agent_id'], row['phase']) for row in calls] == [
        (states[0].agent_id, 'PLAN'), (states[1].agent_id, 'PLAN'), (states[0].agent_id, 'FINAL')]
    paths = list(journal.directory.glob('*.claim.json'))
    assert len(paths) == 3
    claims = [json.loads(path.read_text('utf-8')) for path in paths]
    assert all(row['request_sha256'] == digest(row['request']) for row in claims)
    assert len({row['request_sha256'] for row in claims}) == 3
    assert len(list(journal.directory.glob('*.outcome.json'))) == 3


def test_incomplete_host_attempt_keeps_tokens_and_cost_unknown(monkeypatch):
    def failed_request(self, body, usage):
        usage['requests'] += 1
        raise RuntimeError('TESTONLY incomplete host, no completed usage observation')

    monkeypatch.setattr(CodexReviewer, '_request', failed_request)
    reviewer = CodexReviewer(command=['TESTONLY:no-cli'])
    with pytest.raises(RuntimeError) as failure:
        reviewer.request_profile_decision({'phase': 'PLAN'}, timeout=10)
    usage = failure.value.host_usage
    assert usage['requests'] == 1
    assert usage['usage_complete'] is False
    assert all(usage[field] is None for field in (
        'input_tokens', 'output_tokens', 'total_tokens', 'cached_tokens', 'reasoning_tokens', 'cost_amount'))


def test_partially_observed_usage_preserves_measured_values_without_zero_filling():
    usage = _profile_usage({'requests': 1, 'input_tokens': 120, 'output_tokens': 30,
        'total_tokens': 150, 'cached_tokens': 0, 'reasoning_tokens': 0,
        '_profile_token_fields_observed': ['input_tokens', 'output_tokens', 'total_tokens']})
    assert (usage['input_tokens'], usage['output_tokens'], usage['total_tokens']) == (120, 30, 150)
    assert usage['cached_tokens'] is None and usage['reasoning_tokens'] is None
    assert usage['usage_complete'] is False and usage['cost_amount'] is None


@pytest.mark.parametrize('recorded', [True, False])
def test_rejected_host_reply_preserves_recorded_or_unknown_attempt_usage(tmp_path, recorded):
    snapshot, states = _snapshot_and_states()
    observed = {'requests': 1, 'input_tokens': 120, 'output_tokens': 30, 'cost_amount': None}

    def host(request, *, timeout):
        return {'phase': 'WRONG_PHASE', **({'host_usage': observed} if recorded else {})}

    journal, runner = _runner(tmp_path, snapshot, host)
    with pytest.raises(ValueError) as failure:
        runner._call(states[0], 'PLAN', {'TESTONLY': 'foreign reply'})
    outcome = json.loads(next(journal.directory.glob('*.outcome.json')).read_text('utf-8'))
    assert outcome['status'] == 'FAILED_NO_RETRY'
    assert outcome['host_usage'] == failure.value.host_usage
    if recorded:
        assert failure.value.host_usage == observed
    else:
        assert failure.value.host_usage['requests'] is None
        assert failure.value.host_usage['usage_complete'] is False
