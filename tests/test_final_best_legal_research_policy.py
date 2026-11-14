import json
from copy import deepcopy

import pytest

from goal_analysis.agents.codex_review import CodexReviewer
from goal_analysis.agents.profile_decision_runner import profile_response_schema
from goal_analysis.agents.profile_roster import get_agent_definition


def _capture():
    calls = []
    reviewer = CodexReviewer.__new__(CodexReviewer)

    def inert(body, usage):
        calls.append(deepcopy(body))
        return {'output': [{'content': [{'type': 'output_text', 'text': '{}'}]}]}

    reviewer._request = inert
    return reviewer, calls


def _request(phase, options):
    return {'agent_id': 'ritmusor', 'phase': phase, 'session_id': 'TESTONLY-session',
            'snapshot_id': 'TESTONLY-snapshot',
            'persona': get_agent_definition('ritmusor').as_dict(),
            'data': {'validated_own_options': options,
                     'tool_results': [{'role': 'candidate_scenarios', 'status': 'UNKNOWN'},
                                      {'role': 'historical_strategy_evaluation', 'status': 'UNKNOWN'}]}}


def test_final_ranking_policy_preserves_actual_wire_schema_and_unknown_tool_results():
    reviewer, calls = _capture()
    request = _request('FINAL', [{'option_id': 'TESTONLY-own-1'}, {'option_id': 'TESTONLY-own-2'}])
    original = deepcopy(request)
    assert 'abstain when either scoring path lacks support' in request['persona']['selection_path'][-1]
    reviewer.request_profile_decision(request, timeout=7)
    body = calls.pop()
    text = body['instructions']
    assert 'FINAL_BEST_LEGAL_RESEARCH_OPTION_V1' in text
    assert 'precedence over qualitative abstention suggestions in persona.selection_path' in text
    assert 'select the best such own option as the main research card' in text
    assert 'ABSTAIN only when no admitted own option remains valid' in text
    assert 'mandatory price, identity, kickoff, source-read, own model and product rule' in text
    assert 'UNKNOWN must remain UNKNOWN' in text
    assert 'never READY or wager authorization' in text
    assert 'no new tool or model call is requested' in text
    assert json.loads(body['input']) == request == original
    assert body['text']['format']['schema'] == profile_response_schema('FINAL')
    assert body['_arthur_profile_timeout'] == 7
    assert calls == []


def test_empty_final_can_abstain_while_plan_and_request_deadline_are_unchanged():
    reviewer, calls = _capture()
    request = _request('FINAL', [])
    reviewer.request_profile_decision(request, timeout=7)
    final = calls[-1]
    assert 'If data.validated_own_options is empty, return top-level option_ids=[]' in final['instructions']
    assert 'empty admitted set stated' in final['instructions']
    assert json.loads(final['input'])['data']['validated_own_options'] == []
    plan_request = _request('PLAN', [])
    reviewer.request_profile_decision(plan_request, timeout=7)
    plan = calls[-1]
    assert 'FINAL_BEST_LEGAL_RESEARCH_OPTION_V1' not in plan['instructions']
    assert json.loads(plan['input']) == plan_request
    assert plan['text']['format']['schema'] == profile_response_schema('PLAN')
    with pytest.raises(Exception, match='Invalid bounded profile request'):
        reviewer.request_profile_decision(request, timeout=301)
    assert len(calls) == 2
