import copy
import json
import subprocess
from pathlib import Path

import pytest

from goal_analysis.agents.grok_concurrence import ConcurrenceReviewer, _safe_environment

SOURCE = "https://club.example/news/team-update"


def packet():
    return {"date": "2026-10-18", "cutoff": "2026-10-17T12:00:00+00:00", "concerns": [
        {"concern_id": "c1", "profile_id": "tipster_archiv_nora", "reason": "Kapushiány gyanúja.",
         "leg": {"fixture_id": "123", "home_team": "Home", "away_team": "Away",
                 "kickoff": "2026-10-18T15:00:00+00:00", "market_key": "total_goals",
                 "selection_key": "under25", "period": "FT", "decimal_price": 2.0,
                 "model_probability": .6}, "claims": []}]}


def answer():
    return {"decisions": [{"concern_id": "c1", "fixture_id": "123", "agree": True,
                           "basis": "VERIFIED_CONTEXT", "reason": "A klub a kapus hiányát közölte.",
                           "source_urls": [SOURCE]}]}


class Cli:
    def __init__(self, *, result=None, sources=None, search_status="completed", error=False):
        self.result = answer() if result is None else result
        self.sources = [SOURCE] if sources is None else sources
        self.search_status, self.error = search_status, error
        self.calls = []

    def __call__(self, command, *, prompt, cwd, timeout):
        self.calls.append((command, prompt, timeout))
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.155.1", "")
        if command[-2:] == ["login", "status"]:
            return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT", "")
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps(self.result), encoding="utf-8")
        events = [{"type": "item.completed", "item": {"id": "s1", "type": "web_search",
                   "status": self.search_status, "results": [{"url": s} for s in self.sources]}},
                  {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}}]
        return subprocess.CompletedProcess(command, int(self.error),
                                           "\n".join(json.dumps(e) for e in events), "")


def run(cli, data=None):
    return ConcurrenceReviewer(command=["codex"], runner=cli).review(packet() if data is None else data)


def test_single_bounded_ephemeral_read_only_call_and_observed_evidence():
    data, cli = packet(), Cli()
    original = copy.deepcopy(data)
    result = run(cli, data)
    assert result["status"] == "COMPLETE"
    assert result["decisions"][0]["agree"] is True
    assert result["usage"]["request_count"] == 1
    assert result["usage"]["input_tokens"] == 100
    assert result["usage"]["web_search_calls"] == 1
    analysis = [call for call in cli.calls if "exec" in call[0]]
    assert len(analysis) == 1
    command, prompt, timeout = analysis[0]
    assert timeout == 180
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert 'forced_login_method="chatgpt"' in command
    assert 'web_search="live"' in command
    assert "shell_tool" in command and "apps" in command
    assert "high" in prompt and "ALONE" in prompt
    assert result["decisions"][0]["advisory_only"] is True
    assert data == original


@pytest.mark.parametrize("mode", ["forged", "failed_search", "x_only", "unsupported_basis"])
def test_agreement_fails_closed_without_independent_observed_support(mode):
    body = answer()
    kwargs = {}
    if mode == "forged":
        kwargs["sources"] = ["https://other.example/news"]
    elif mode == "failed_search":
        kwargs["search_status"] = "failed"
    elif mode == "x_only":
        url = "https://x.com/club/status/1234567890123456789"
        body["decisions"][0]["source_urls"] = [url]
        kwargs["sources"] = [url]
    else:
        body["decisions"][0]["basis"] = "NOT_CONFIRMED"
    result = run(Cli(result=body, **kwargs))
    assert result["status"] == "PARTIAL"
    assert result["decisions"][0]["agree"] is False


def test_wrong_fixture_and_unknown_concern_are_rejected():
    for field, value in (("fixture_id", "999"), ("concern_id", "other")):
        body = answer()
        body["decisions"][0][field] = value
        result = run(Cli(result=body))
        assert result["status"] == "PARTIAL"
        assert result["decisions"] == []


def test_missing_coverage_is_partial_and_no_retry():
    data = packet()
    data["concerns"].append({**copy.deepcopy(data["concerns"][0]), "concern_id": "c2"})
    cli = Cli()
    result = run(cli, data)
    assert result["status"] == "PARTIAL"
    assert result["diagnostics"]["concerns_without_decision"] == 1
    assert len([call for call in cli.calls if "exec" in call[0]]) == 1


def test_daily_is_advisory_only_and_input_unchanged():
    data = packet()
    data["concerns"][0]["profile_id"] = "daily223"
    original = copy.deepcopy(data)
    decision = run(Cli(), data)["decisions"][0]
    assert decision["protected_profile"] is True
    assert decision["advisory_only"] is True
    assert data == original


def test_xai_credentials_excluded_from_child_and_rejected_in_input_and_output(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "secret-without-recognizable-prefix")
    monkeypatch.setenv("GROK_API_KEY", "second-secret")
    monkeypatch.setenv("THE_STATS_API_KEY", "private-stats-secret")
    monkeypatch.setenv("THESTATS_API_KEY", "legacy-private-stats-secret")
    assert "XAI_API_KEY" not in _safe_environment()
    assert "GROK_API_KEY" not in _safe_environment()
    assert "THE_STATS_API_KEY" not in _safe_environment()
    assert "THESTATS_API_KEY" not in _safe_environment()
    data = packet()
    data["concerns"][0]["reason"] = "secret-without-recognizable-prefix"
    cli = Cli()
    result = run(cli, data)
    assert result["error_code"] == "INVALID_PACKET"
    assert cli.calls == []
    body = answer()
    body["decisions"][0]["reason"] = "secret-without-recognizable-prefix"
    result = run(Cli(result=body))
    assert result["error_code"] == "INVALID_OUTPUT"
    assert "secret-without" not in json.dumps(result)
    assert result["usage"]["input_tokens"] == 100


@pytest.mark.parametrize("change", ["oversized", "locked", "unvalidated_claim"])
def test_invalid_packet_makes_no_cli_calls(change):
    data = packet()
    if change == "oversized":
        data["concerns"] = [{**copy.deepcopy(data["concerns"][0]), "concern_id": f"c{i}"}
                            for i in range(41)]
    elif change == "locked":
        data["concerns"][0]["leg"]["kickoff"] = "2026-10-17T12:15:00+00:00"
    else:
        data["concerns"][0]["claims"] = [{"fixture_id": "123"}]
    cli = Cli()
    result = run(cli, data)
    assert result["error_code"] == "INVALID_PACKET"
    assert cli.calls == []


def test_cli_failure_and_malformed_output_preserve_known_usage():
    for cli in (Cli(error=True), Cli(result={"raw": "malformed"})):
        result = run(cli)
        assert result["status"] == "UNAVAILABLE"
        assert result["usage"]["input_tokens"] == 100
        assert result["decisions"] == []


def test_existing_evidence_is_forwarded_without_prior_model_conclusion():
    data = packet()
    data["concerns"][0]["leg"]["evidence"] = [
        {"evidence_id": "e1", "category": "history", "claim_type": "INFERENCE",
         "statement": "Nyolc korábbi mérkőzés mintája.", "source_id": "history-123",
         "observed_at": "2026-10-17T11:00:00+00:00"}]
    data["concerns"][0]["codex_prior_opinion"] = "Do not transmit"
    cli = Cli()
    result = run(cli, data)
    assert result["status"] == "COMPLETE"
    assert result["decisions"][0]["provenance_status"] == "OBSERVED_INDEPENDENT_SOURCE"
    prompt = next(call[1] for call in cli.calls if "exec" in call[0])
    assert "Nyolc korábbi mérkőzés mintája." in prompt
    assert "Do not transmit" not in prompt


def test_existing_long_evidence_without_id_is_bounded_and_incomplete_sources_counted():
    data = packet()
    source = {"category": "history", "claim_type": "INFERENCE", "statement": "A" * 450,
              "source_id": "historical-source", "observed_at": "2026-10-17T11:00:00+00:00"}
    data["concerns"][0]["leg"]["evidence"] = [source,
        {"category": "history", "statement": "Missing timestamp"}]
    cli = Cli()
    result = run(cli, data)
    assert result["status"] == "COMPLETE"
    prompt = next(call[1] for call in cli.calls if "exec" in call[0])
    clean = json.loads(prompt.split("\nINPUT DATA:\n", 1)[1])["concerns"][0]["leg"]
    assert clean["evidence_omitted_count"] == 1
    assert clean["evidence_status"] == "SUPPLIED_UNVERIFIED"
    assert clean["evidence"][0]["statement"] == "A" * 320
    assert clean["evidence"][0]["statement_truncated"] is True
    assert len(clean["evidence"][0]["evidence_id"]) == 24
    assert clean["evidence"][0]["source_id"] == source["source_id"]
    assert clean["evidence"][0]["observed_at"] == source["observed_at"]
    assert "evidence_id" not in source
