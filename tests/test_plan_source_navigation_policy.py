import json
from copy import deepcopy

import pytest

from goal_analysis.agents.codex_review import CodexReviewer, _ReviewError
from goal_analysis.agents.profile_decision_runner import profile_response_schema
from goal_analysis.agents.profile_source_host import SourceAccessScope


class _CapturedRequest(Exception):
    pass


def test_source_plan_navigation_preserves_wire_caps_and_full_read_contract(monkeypatch):
    reviewer = CodexReviewer.__new__(CodexReviewer)
    calls = []
    prepared = {'document': {'binding': {'phase': 'PLAN'}}}
    scope = SourceAccessScope.__new__(SourceAccessScope)
    # Only the typed boundary is exercised; no source file, RPC or native host is used.
    monkeypatch.setattr(SourceAccessScope, 'prepare', lambda self, request: prepared)
    monkeypatch.setattr('goal_analysis.agents.codex_review.time.monotonic', lambda: 10.0)

    def capture(body, usage):
        calls.append(deepcopy(body))
        raise _CapturedRequest

    reviewer._request = capture
    for phase, capability, cap in (('PLAN', scope, 600), ('FINAL', scope, 300),
                                   ('PLAN', None, 300)):
        request = {'phase': phase, 'agent_id': 'TESTONLY-owner',
                   'session_id': 'TESTONLY-session', 'snapshot_id': 'TESTONLY-snapshot',
                   'data': {'snapshot': {'fixture_ids': ['TESTONLY-a', 'TESTONLY-b']},
                            'optional_state': 'UNKNOWN'}}
        original = deepcopy(request)
        with pytest.raises(_CapturedRequest):
            reviewer.request_profile_decision(request, timeout=cap, source_access=capability)
        body = calls[-1]
        text = body['instructions']
        assert json.loads(body['input']) == request == original
        assert body['text']['format']['schema'] == profile_response_schema(phase)
        assert body['_arthur_profile_timeout'] == cap
        assert ('_arthur_profile_source' in body) is (capability is not None)
        assert ('PLAN_INDEPENDENT_READS_V1' in text) is (phase == 'PLAN' and capability is scope)
        if phase == 'PLAN' and capability is scope:
            assert 'when the current host supports concurrent tool calls' in text
            assert 'existing independent read_data or query_data RPCs in parallel' in text
            assert 'There is no new batch API' in text
            assert 'Keep dependent cursor and next_offset pages sequential' in text
            assert 'only the fields required for the current comparison' in text
            assert 'preserving candidate and fixture identities and explicit UNKNOWN values' in text
            assert 'read its entire candidates record and every continuation page' in text
            assert 'All daily fixtures and all twelve families remain accessible' in text
            assert 'same 64-call, 512KiB phase response and 24000-character per-response limits' in text
            assert 'parallelism does not increase any budget or waive any source-proof requirement' in text
    with pytest.raises(_ReviewError, match='Private typed source capability required'):
        reviewer.request_profile_decision(request, timeout=300, source_access=object())
    assert len(calls) == 3
