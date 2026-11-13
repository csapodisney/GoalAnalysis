"""TESTONLY command construction reaches an inert runner; no CLI/model/network."""
import json
from datetime import UTC, datetime

import pytest
from test_profile_source_host import make_source_phase

from goal_analysis.agents.codex_review import CodexReviewer


class RunnerReached(Exception):
    pass


@pytest.mark.parametrize('role', ['ordinary', 'source', 'research'])
def test_only_explicit_source_and_research_roles_enable_router_with_original_restrictions(tmp_path, monkeypatch, role):
    calls = []
    def runner(command, **kwargs):
        calls.append((list(command), kwargs))
        raise RunnerReached('TESTONLY stopped before real native execution')
    reviewer = CodexReviewer(command=['TESTONLY-no-process'], runner=runner, web_search=True)
    monkeypatch.setattr(reviewer, '_ensure_login', lambda: None)
    if role == 'research':
        now = datetime(2026, 11, 13, 12, tzinfo=UTC)
        result = reviewer.research_fixtures([{'fixture_id': 'TESTONLY:f', 'home_team': 'TESTONLY Home',
            'away_team': 'TESTONLY Away', 'kickoff': '2026-11-13T18:00:00Z'}],
            '2026-11-13', clock=lambda: now)
        assert result['status'] == 'UNKNOWN' and result['error_type'] == 'RunnerReached'
    else:
        scope, request, _ = make_source_phase(tmp_path)
        with pytest.raises(RunnerReached):
            reviewer.request_profile_decision(request, timeout=5,
                **({'source_access': scope} if role == 'source' else {}))
    assert len(calls) == 1
    command, kwargs = calls[0]
    overrides = dict(command[index + 1].split('=', 1) for index, value in enumerate(command) if value == '-c')
    assert json.loads(overrides['features.code_mode_host']) is (role != 'ordinary')
    assert json.loads(overrides['web_search']) == ('live' if role == 'research' else 'disabled')
    assert command[command.index('--sandbox') + 1] == 'read-only'
    assert json.loads(overrides['approval_policy']) == 'never'
    disabled = {command[index + 1] for index, value in enumerate(command) if value == '--disable'}
    assert {'shell_tool', 'unified_exec', 'shell_snapshot', 'multi_agent', 'apps', 'plugins'} <= disabled
    assert 0 < kwargs['timeout'] <= 300
    mcp = {key: value for key, value in overrides.items() if key.startswith('mcp_servers.')}
    if role == 'source':
        assert json.loads(mcp['mcp_servers.arthur_data.enabled_tools']) == ['read_data']
        assert 'env' in kwargs
    else:
        assert mcp == {} and 'env' not in kwargs
