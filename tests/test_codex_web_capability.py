"""Native web capability and observed-source boundaries; no network calls."""
import json
import subprocess
from pathlib import Path

import pytest
from test_astra_review import SOURCE, candidate
from test_codex_review import Cli

from goal_analysis.agents.codex_review import CodexReviewer


class SearchEvents(Cli):
    def __init__(self, events, *, malformed=False):
        super().__init__(web=False)  # The final review may use supplied evidence alone.
        self.events, self.malformed = events, malformed

    def __call__(self, command, **kwargs):
        result = super().__call__(command, **kwargs)
        if 'web_search="live"' not in command:
            return result
        completion = json.loads(result.stdout.splitlines()[-1])
        result = subprocess.CompletedProcess(command, 0,
                   "\n".join(json.dumps(e) for e in [*self.events, completion]), "")
        if self.malformed:
            output = Path(command[command.index("--output-last-message") + 1])
            output.write_text("invalid JSON", encoding="utf8")
        return result


def event(kind, **fields):
    return {"type": kind, "item": {"type": "web_search", "id": "web-1", **fields}}


def run(cli):
    return CodexReviewer(command=["codex"], runner=cli).review([candidate()], "2026-09-21")


def test_research_enables_required_host_and_assessment_disables_it():
    cli = Cli()
    result = run(cli)
    calls = [cmd for cmd, *_ in cli.calls if "exec" in cmd]
    assert result["research_status"] == "COMPLETE_WITH_SOURCES"
    assert "features.code_mode_host=true" in calls[0]
    assert "features.code_mode_host=false" in calls[1]
    assert all("shell_tool" in cmd and "multi_agent" in cmd and "plugins" in cmd for cmd in calls)
    assert all(cmd[cmd.index("--sandbox") + 1] == "read-only" for cmd in calls)


@pytest.mark.parametrize("events", [
    [event("item.started")],
    [event("item.started"), event("item.completed", status="failed", results=[{"url": SOURCE}])],
])
def test_started_or_failed_search_cannot_verify_or_count_completed_usage(events):
    result = run(SearchEvents(events))
    assert result["research_status"] == "UNAVAILABLE"
    assert result["usage"]["web_search_calls"] == 0
    assert not result["sources"]
    diagnostics = result["research_diagnostics"]
    assert diagnostics["status"] == "NO_COMPLETED_SEARCH"
    assert diagnostics["attempted_searches"] == 1
    assert diagnostics["completed_searches"] == 0


def test_model_citation_must_appear_in_actual_completed_tool_results():
    result = run(SearchEvents([event("item.completed", results=[{"url": "https://example.org/other"}])]))
    assert result["research_status"] == "UNAVAILABLE"
    assert result["research_diagnostics"]["status"] == "NO_ACCEPTED_SOURCES"
    assert result["research_diagnostics"]["returned_source_count"] == 1
    assert result["research_diagnostics"]["accepted_source_count"] == 0
    assert not result["sources"]


def test_requested_action_url_without_returned_page_is_not_source_evidence():
    result = run(SearchEvents([event("item.completed", action={"url": SOURCE}, results=[])]))
    assert result["research_status"] == "UNAVAILABLE"
    assert result["research_diagnostics"]["status"] == "NO_ACCEPTED_SOURCES"


def test_malformed_research_preserves_completed_search_usage_and_reason():
    result = run(SearchEvents([event("item.completed", results=[{"url": SOURCE}])], malformed=True))
    assert result["status"] == "COMPLETE"  # Separate supplied-data assessment succeeds.
    assert result["research_status"] == "UNAVAILABLE"
    assert result["usage"]["web_search_calls"] == 1
    assert result["research_diagnostics"]["status"] == "INVALID_RESEARCH_JSON"


def test_completed_search_uses_nested_public_results_and_deduplicates_events():
    item = event("item.completed", results={"sources": [{"url": SOURCE}, {"url": "http://127.0.0.1/private"}]})
    result = run(SearchEvents([event("item.started"), item, item]))
    assert result["research_status"] == "COMPLETE_WITH_SOURCES"
    assert result["usage"]["web_search_calls"] == 1
    assert result["research_diagnostics"] == {
        "status": "VERIFIED", "attempted_searches": 1, "completed_searches": 1,
        "returned_source_count": 1, "accepted_source_count": 1, "cli_version": "0.155.1",
    }
