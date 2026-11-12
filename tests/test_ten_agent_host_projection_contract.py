"""Host wire-size boundary, with the external request replaced by an inert fixture."""
import json

import pytest

from goal_analysis.agents.astra_review import _ReviewError
from goal_analysis.agents.codex_review import CodexReviewer
from goal_analysis.jobs.agent_snapshot import json_text


def _request(rows):
    return {'phase': 'PLAN', 'agent_id': 'TESTONLY-owner', 'session_id': 'TESTONLY-session',
            'snapshot_id': 'TESTONLY-snapshot', 'data': {'table': ['x'] * rows}}


def test_host_uses_same_compact_input_budget_as_runner_without_inflation():
    request = _request(80_000)
    assert len(json_text(request)) < 360_000 < len(json.dumps(request))
    reviewer = CodexReviewer.__new__(CodexReviewer)
    calls = []

    def inert(body, usage):
        calls.append(body)
        answer = {key: request[key] for key in ('phase', 'agent_id', 'session_id', 'snapshot_id')}
        return {'output': [{'content': [{'type': 'output_text', 'text': json.dumps(answer)}]}]}

    reviewer._request = inert
    result = reviewer.request_profile_decision(request, timeout=1)
    assert len(calls) == 1 and calls[0]['input'] == json_text(request)
    assert json.loads(calls[0]['input']) == request
    assert result['agent_id'] == request['agent_id']
    assert result['host_usage']['cost_amount'] is None


def test_actual_compact_overflow_stops_before_external_request():
    request = _request(90_001)
    assert len(json_text(request)) > 360_000
    reviewer = CodexReviewer.__new__(CodexReviewer)
    calls = []
    reviewer._request = lambda *args: calls.append(args)
    with pytest.raises(_ReviewError, match='Profile input budget exceeded'):
        reviewer.request_profile_decision(request, timeout=1)
    assert calls == []
