"""Subscription routing, provenance and process boundaries; no model calls."""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_astra_review import SOURCE, candidate, review
from test_daily_223_live import config

from goal_analysis.agents.codex_review import (
    CodexReviewer,
    _environment,
    _run_process,
    codex_command,
)
from goal_analysis.config.portfolio import validate_settings


class Cli:
    def __init__(self, auth="Logged in using ChatGPT", fail=None, web=True):
        self.auth, self.fail, self.web = auth, fail, web
        self.calls = []

    def __call__(self, command, *, prompt, cwd, timeout):
        self.calls.append((command, prompt, cwd, timeout))
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.155.1", "")
        if command[-2:] == ["login", "status"]:
            return subprocess.CompletedProcess(command, 0, "", self.auth)
        if self.fail:
            return subprocess.CompletedProcess(command, 1, "", self.fail)
        output = Path(command[command.index("--output-last-message") + 1])
        events = []
        if 'web_search="live"' in command:
            answer = {
                "notes": "Forrásolt csapathír.",
                "sources": [{"url": SOURCE, "title": "Team news"}],
            }
            if self.web:
                events.append(
                    {"type": "item.completed", "item": {"type": "web_search", "id": "search1"}}
                )
        elif "--output-schema" in command:
            answer = {"reviews": [review(urls=[SOURCE] if self.web else [])]}
        else:
            answer = "ARTHUR_OK"
        output.write_text(
            answer if isinstance(answer, str) else json.dumps(answer), encoding="utf-8"
        )
        events.append(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cached_input_tokens": 5,
                    "reasoning_output_tokens": 3,
                },
            }
        )
        return subprocess.CompletedProcess(command, 0, "\n".join(map(json.dumps, events)), "")


def test_chatgpt_review_preserves_two_stages_usage_sources_and_read_only(tmp_path):
    cli = Cli()
    result = CodexReviewer(command=["codex"], runner=cli, cache_dir=tmp_path).review(
        [candidate()], "2026-09-21"
    )
    assert result["status"] == "COMPLETE", result
    assert result["backend"] == "codex_chatgpt"
    assert result["api_fallback"] is False
    assert result["usage"]["requests"] == 2
    assert result["usage"]["input_tokens"] == 200
    assert result["usage"]["web_search_calls"] == 1
    assert result["sources"][0]["url"] == SOURCE
    assert result["source_provenance"] == "codex_citations_after_observed_web_search"
    jobs = [c for c in cli.calls if "exec" in c[0]]
    assert len(jobs) == 2
    for command, prompt, cwd, timeout in jobs:
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert command[command.index("--model") + 1] == "gpt-6-astra"
        assert 'forced_login_method="chatgpt"' in command
        assert 'approval_policy="never"' in command
        assert "--ignore-rules" not in command
        assert "--dangerously-bypass-approvals-and-sandbox" not in command
        assert "shell_tool" in command and "multi_agent" in command
        assert command[-1] == "-" and len(prompt) > 100
        assert cwd != Path.cwd() and timeout == 300


def test_subscription_cache_reuses_unchanged_packets(tmp_path):
    cli = Cli()
    reviewer = CodexReviewer(command=["codex"], runner=cli, cache_dir=tmp_path)
    first = reviewer.review([candidate()], "2026-09-21")
    second = reviewer.review([candidate()], "2026-09-21")
    assert first["status"] == second["status"] == "COMPLETE"
    assert second["usage"]["cache_hit"] is True
    assert len([c for c in cli.calls if "exec" in c[0]]) == 2


@pytest.mark.parametrize(
    "auth",
    [
        "Not logged in",
        "Logged in using an API key - SECRET",
        "Logged in using ChatGPT plus trailing secret",
    ],
)
def test_api_key_or_missing_login_never_runs_a_model(auth):
    cli = Cli(auth=auth)
    result = CodexReviewer(command=["codex"], runner=cli).check_connection()
    assert result["verified"] is False
    assert result["status"] == "UNAVAILABLE"
    assert not any("exec" in c[0] for c in cli.calls)
    assert "SECRET" not in json.dumps(result)


def test_model_check_uses_chatgpt_even_if_platform_key_is_present(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-platform-key")
    cli = Cli()
    result = CodexReviewer(command=["codex"], runner=cli).check_connection()
    assert result["verified"] is True
    assert result["billing_mode"] == "chatgpt_subscription"
    assert result["requested_model"] == "gpt-6-astra"
    assert "resolved_model" not in result
    assert result["limits"]["output_token_cap_enforced"] is False
    assert "private-platform-key" not in repr(cli.calls)


def test_cli_quota_does_not_retry_or_fallback_or_echo_raw_logs():
    cli = Cli(fail="usage limit reached; private-session-token")
    result = CodexReviewer(command=["codex"], runner=cli).check_connection()
    assert result["verified"] is False
    assert "kerete" in result["message"]
    assert "private-session-token" not in json.dumps(result)
    assert len([c for c in cli.calls if "exec" in c[0]]) == 1


def test_citations_without_web_tool_event_are_not_researched():
    cli = Cli(web=False)
    result = CodexReviewer(command=["codex"], runner=cli).review([candidate()], "2026-09-21")
    assert result["status"] == "COMPLETE"
    assert result["research_status"] == "UNAVAILABLE"
    assert result["sources"] == []
    assert result["usage"]["input_tokens"] == 200
    assert len([c for c in cli.calls if "exec" in c[0]]) == 2


def test_child_process_receives_no_service_keys_and_no_shell_interpolation(monkeypatch, tmp_path):
    for name in (
        "API_FOOTBALL_KEY",
        "THE_ODDS_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "CODEX_ACCESS_TOKEN",
    ):
        monkeypatch.setenv(name, "do-not-forward")
    assert "do-not-forward" not in _environment().values()
    literal = '$(never-execute); `do-not-run` & "quoted"'
    result = _run_process(
        [
            sys.executable,
            "-c",
            "import sys,os; print(sys.stdin.read()); print(os.getenv('OPENAI_API_KEY', 'ABSENT'))",
        ],
        prompt=literal,
        cwd=tmp_path,
        timeout=5,
    )
    assert literal in result.stdout and "ABSENT" in result.stdout
    assert result.returncode == 0


def test_windows_npm_shim_uses_node_without_cmd_shell(monkeypatch, tmp_path):
    shim = tmp_path / "Path With Spaces" / "codex.cmd"
    script = shim.parent / "node_modules/@openai/codex/bin/codex.js"
    script.parent.mkdir(parents=True)
    script.write_text("// test wrapper")
    monkeypatch.setattr("shutil.which", lambda name: str(shim) if name == "codex" else "node.exe")
    assert codex_command() == ["node.exe", str(script)]


def test_existing_settings_migrate_without_openai_key_and_api_backend_is_rejected():
    old = json.loads(
        (Path(__file__).parents[1] / "config/arthur-settings.example.json").read_text()
    )
    old["openai"].pop("backend")
    assert validate_settings(old)["openai"]["backend"] == "codex_chatgpt"
    blocked = copy.deepcopy(old)
    blocked["openai"]["backend"] = "openai_api"
    with pytest.raises(ValueError):
        validate_settings(blocked)


def test_cli_config_check_does_not_require_platform_key(tmp_path, monkeypatch, capsys):
    source = Path(__file__).parents[1] / "scripts/run-arthur.py"
    spec = importlib.util.spec_from_file_location("arthur_subscription_cli", source)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-football")
    monkeypatch.setenv("THE_ODDS_API_KEY", "test-odds")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config_file = tmp_path / "sports.json"
    config_file.write_text(json.dumps(config()))
    assert cli.main(["--check-config", "--config", str(config_file)]) == 0
    assert "backend=codex_chatgpt" in capsys.readouterr().out


def test_malformed_web_research_still_runs_evidence_review_and_counts_usage():
    class BrokenResearch(Cli):
        def __call__(self, command, **kwargs):
            result = super().__call__(command, **kwargs)
            if 'web_search="live"' in command:
                output = Path(command[command.index("--output-last-message") + 1])
                output.write_text("not valid research JSON", encoding="utf-8")
            return result

    cli = BrokenResearch(web=False)
    result = CodexReviewer(command=["codex"], runner=cli).review([candidate()], "2026-09-21")
    assert result["status"] == "COMPLETE"
    assert result["research_status"] == "UNAVAILABLE"
    assert result["sources"] == []
    assert result["usage"]["requests"] == 2
    assert result["usage"]["total_tokens"] == 240


def test_review_quota_failure_stops_without_second_model_request():
    cli = Cli(fail="usage limit; private-token")
    result = CodexReviewer(command=["codex"], runner=cli).review([candidate()], "2026-09-21")
    assert result["status"] == "FAILED"
    assert result["usage"]["requests"] == 1
    assert "private-token" not in json.dumps(result)
