"""Complete-ledger measurements and injected CLI only; never a real model request."""

import json
import sqlite3
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from test_portfolio_ledger import NOW, Football, fixture, report, ticket

from goal_analysis.learning import CodexLearningReviewer, LearningAgent
from goal_analysis.learning._findings import review_packet
from goal_analysis.learning._metrics import digest, measure
from goal_analysis.learning.reviewer import llm_result
from goal_analysis.storage.portfolio_ledger import PortfolioLedger


class Review:
    def __init__(self, status="COMPLETE"):
        self.calls = []
        self.status = status

    def review(self, packet):
        self.calls.append(deepcopy(packet))
        selected = dict(packet["proposal_options"][0], source="codex", priority="HIGH")
        return llm_result(self.status, {"requests": 1, "input_tokens": 100,
                         "output_tokens": 20, "total_tokens": 120}, proposals=[selected])


def populated(root):
    ledger = PortfolioLedger(root / "data/arthur/ledger.sqlite3", now=lambda: NOW)
    actual = ticket("actual", "kronikas")
    paper = ticket("paper", "ritmusor")
    model = ticket("unpriced", "gauss")
    model.pop("ticket_id")
    model.update(combined_price=None, stake_eur=None, strategy_version="gauss-selection-v1")
    for leg in model["legs"]:
        leg.update(decimal_price=None, model_probability=0.7, model_details={"version": "gauss-1.0"})
    original = report(actual, paper)
    original["recommendations"] = [deepcopy(actual), deepcopy(paper), model]
    ledger.save_run(original)
    ledger.record_wager("actual", actual_combined_price=11.9)
    ledger.now = lambda: NOW + timedelta(minutes=10)
    ledger.freeze_run("run-1")
    ledger.now = lambda: NOW + timedelta(hours=6)
    ledger.refresh_results(Football({i: fixture(i) for i in (1, 2, 3)}))
    return ledger


def test_actual_research_frozen_are_separate_and_all_sources_resolve(tmp_path):
    ledger = populated(tmp_path)
    now = NOW + timedelta(hours=8)
    reviewer = Review()
    agent = LearningAgent(tmp_path, ledger, lambda: now)
    measured = agent.run(now, reviewer)
    assert measured["status"] == "COMPLETE"
    assert measured["automatic_changes"] is False
    actual, research, frozen = (measured["metrics"][k] for k in ("actual", "research", "frozen"))
    assert actual["tickets"]["total"] == 1
    assert actual["financial"]["net_eur"] == 10.9
    assert actual["financial"]["roi_pct"] == 1090
    assert research["tickets"]["total"] == 2
    assert frozen["tickets"]["total"] == 3
    assert not research["financial"]["available"] and research["financial"]["net_eur"] is None
    assert not frozen["financial"]["available"]
    assert research["missing_odds_legs"] == 3
    assert research["unique_fixtures"] == 3 and research["legs"]["total"] == 6
    assert research["calibration"]["brier_binary"] == pytest.approx(0.09)
    assert research["calibration"]["eligible_selections"] == 3
    assert measured["llm"]["usage"]["total_tokens"] == 120
    evidence = {item["evidence_id"]: item for item in measured["evidence"]}
    for item in measured["evidence"]:
        assert item["evidence_id"] == "ev-" + digest({k: v for k, v in item.items() if k != "evidence_id"})[:24]
        assert all(ref["table"].startswith("portfolio_") and len(ref["hash"]) == 64 for ref in item["source_refs"])
    for item in measured["findings"] + measured["recommendations"]:
        assert item["evidence_ids"] and set(item["evidence_ids"]) <= evidence.keys()
    assert all(r["status"] == "PROPOSAL_ONLY" for r in measured["recommendations"])


def test_hash_immutable_first_daily_snapshot_and_no_second_call(tmp_path):
    ledger = populated(tmp_path)
    now = NOW + timedelta(hours=8)
    reviewer = Review()
    agent = LearningAgent(tmp_path, ledger, lambda: now)
    first = agent.run(now, reviewer)
    agent.clock = lambda: now + timedelta(hours=1)
    second = agent.run(now + timedelta(hours=1), reviewer)
    assert first == second == agent.latest() == agent.list_reports(10)[0]
    assert len(reviewer.calls) == 1
    assert "first_immutable" in first["snapshot_policy"].lower()
    assert len(list((tmp_path / "reports/learning").glob("*.json"))) == 1
    with sqlite3.connect(agent.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE learning_reports SET report_json='{}'")


def test_reads_have_no_side_effects_and_no_data_does_not_call_ai(tmp_path):
    agent = LearningAgent(tmp_path, clock=lambda: NOW)
    assert agent.latest() is None and agent.list_reports() == []
    assert list(tmp_path.iterdir()) == []
    reviewer = Review()
    result = agent.run(NOW, reviewer)
    assert result["status"] == "NO_DATA"
    assert result["llm"]["status"] == "SKIPPED_NO_DATA"
    assert not reviewer.calls
    assert not (tmp_path / "data/arthur/ledger.sqlite3").exists()


def test_complete_history_is_not_truncated_at_one_hundred_runs(tmp_path):
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: NOW)
    for index in range(131):
        stamp = NOW - timedelta(days=132 - index)
        ledger.now = lambda stamp=stamp: stamp
        item = ticket(str(index), "legacy", fixture_ids=(index * 3 + 1, index * 3 + 2, index * 3 + 3))
        item.update(created_at=stamp.isoformat(), date=stamp.date().isoformat())
        for leg in item["legs"]:
            leg["kickoff"] = (stamp + timedelta(hours=3)).isoformat()
        original = report(item, identifier=f"run-{index}", observed_at=stamp)
        original["date"] = stamp.date().isoformat()
        ledger.save_run(original)
    result = LearningAgent(tmp_path, ledger, lambda: NOW).run(NOW, Review())
    assert result["coverage"]["runs_as_of"] == result["coverage"]["runs_total"] == 131
    assert result["metrics"]["research"]["tickets"]["total"] == 131
    assert result["coverage"]["unknown_version_recommendations"] == 131
    assert result["metrics"]["research"]["by_version"][0]["version"] == "UNKNOWN_LEGACY"


def test_as_of_excludes_future_corrections_wagers_and_runs(tmp_path):
    ledger = populated(tmp_path)
    before = NOW + timedelta(hours=6, minutes=1)
    initial = measure(ledger.path, before)
    assert initial["metrics"]["actual"]["tickets"]["won"] == 1
    ledger.now = lambda: NOW + timedelta(hours=12)
    ledger.refresh_results(Football({i: fixture(i, fulltime=(0, 0)) for i in (1, 2, 3)}))
    old = measure(ledger.path, before)
    later = measure(ledger.path, NOW + timedelta(hours=13))
    assert old["metrics"] == initial["metrics"]
    assert later["metrics"]["actual"]["tickets"]["lost"] == 1
    assert old["coverage"]["cutoff_excluded"] > 0
    early = measure(ledger.path, NOW - timedelta(seconds=1))
    assert early["coverage"]["runs_as_of"] == 0
    assert early["metrics"]["actual"]["tickets"]["total"] == 0


def test_learning_never_changes_original_ledger_or_configuration(tmp_path):
    ledger = populated(tmp_path)
    with sqlite3.connect(ledger.path) as db:
        before = list(db.iterdump())
    LearningAgent(tmp_path, ledger, lambda: NOW + timedelta(hours=8)).run(NOW + timedelta(hours=8), Review())
    with sqlite3.connect(ledger.path) as db:
        assert list(db.iterdump()) == before
    assert not (tmp_path / "config").exists()
    assert not (tmp_path / "src").exists()


def test_invalid_evidence_reference_rejected_but_statistics_saved(tmp_path):
    ledger = populated(tmp_path)

    class Invalid(Review):
        def review(self, packet):
            result = super().review(packet)
            result["proposals"][0]["evidence_ids"] = ["made-up-evidence"]
            result["proposals"][0]["detail"] = "Invented 99% success"
            return result

    now = NOW + timedelta(hours=8)
    result = LearningAgent(tmp_path, ledger, lambda: now).run(now, Invalid())
    assert result["status"] == "PARTIAL"
    assert result["llm"]["status"] == "INVALID_RESPONSE"
    assert result["llm"]["usage"]["total_tokens"] == 120
    assert result["metrics"]["actual"]["financial"]["net_eur"] == 10.9
    assert "Invented" not in json.dumps(result) and "made-up" not in json.dumps(result)


def test_uncertain_interruption_never_repeats_charged_request(tmp_path):
    ledger = populated(tmp_path)
    now = NOW + timedelta(hours=8)
    agent = LearningAgent(tmp_path, ledger, lambda: now)

    class Crash:
        def review(self, packet):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        agent.run(now, Crash())
    reviewer = Review()
    agent.clock = lambda: now + timedelta(minutes=11)
    recovered = agent.run(now + timedelta(minutes=11), reviewer)
    assert recovered["llm"]["status"] == "INTERRUPTED"
    assert recovered["llm"]["usage"]["requests"] is None
    assert not reviewer.calls
    assert recovered["as_of"] == now.isoformat()


def test_concurrent_invocations_reserve_exactly_one_request(tmp_path):
    ledger = populated(tmp_path)
    now = NOW + timedelta(hours=8)
    agent = LearningAgent(tmp_path, ledger, lambda: now)
    entered, release = threading.Event(), threading.Event()

    class Blocking(Review):
        def review(self, packet):
            entered.set()
            assert release.wait(5)
            return super().review(packet)

    reviewer = Blocking()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(agent.run, now, reviewer)
        assert entered.wait(5)
        concurrent = agent.run(now, reviewer)
        assert concurrent["in_progress"] is True
        release.set()
        assert first.result(timeout=5)["status"] == "COMPLETE"
    assert len(reviewer.calls) == 1


class Cli:
    def __init__(self, *, failure=None, auth="Logged in using ChatGPT", invalid=False):
        self.calls, self.failure, self.auth, self.invalid = [], failure, auth, invalid

    def __call__(self, command, *, prompt, cwd, timeout):
        self.calls.append((command, prompt, cwd, timeout))
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.155.1", "")
        if command[-2:] == ["login", "status"]:
            return subprocess.CompletedProcess(command, 0, self.auth, "")
        if self.failure:
            return subprocess.CompletedProcess(command, 1, "", self.failure)
        packet = json.loads(prompt.split("INPUT DATA:\n", 1)[1])
        proposal = packet["proposal_options"][0]
        result = {"priorities": [{"recommendation_id": proposal["recommendation_id"],
                                  "priority": "HIGH", "evidence_ids": proposal["evidence_ids"]}]}
        if self.invalid:
            result["invented_profit"] = 999999
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps(result), encoding="utf-8")
        event = {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}
        return subprocess.CompletedProcess(command, 0, json.dumps(event), "")


def test_injected_cli_only_subscription_readonly_disabled_tools_and_usage(tmp_path):
    ledger = populated(tmp_path)
    now = NOW + timedelta(hours=8)
    cli = Cli()
    agent = LearningAgent(tmp_path, ledger, lambda: now)
    result = agent.run(now, CodexLearningReviewer(tmp_path, runner=cli, command=["codex"]))
    assert result["llm"]["status"] == "COMPLETE"
    commands = [row for row in cli.calls if "exec" in row[0]]
    assert len(commands) == 1
    command, prompt, cwd, timeout = commands[0]
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--model") + 1] == "gpt-6-astra"
    assert 'forced_login_method="chatgpt"' in command and 'web_search="disabled"' in command
    assert 'approval_policy="never"' in command
    for disabled in ("shell_tool", "unified_exec", "apps", "plugins", "multi_agent"):
        assert disabled in command
    assert cwd.is_relative_to(tmp_path / "data/learning/tmp") and timeout == 300
    assert result["llm"]["usage"]["total_tokens"] == 120
    assert result["llm"]["api_fallback"] is False
    assert "source_refs" not in json.loads(prompt.split("INPUT DATA:\n", 1)[1])["evidence"][0]


@pytest.mark.parametrize(("failure", "category", "transient"), [
    ("usage limit reached private-token", "QUOTA_EXHAUSTED", False),
    ("rate limit retry 30 private-token", "TRANSIENT_RATE_LIMIT", True),
    ("connection timeout private-token", "TRANSIENT_CONNECTION", True),
])
def test_failures_are_sanitized_single_attempt_and_keep_report(tmp_path, failure, category, transient):
    ledger = populated(tmp_path)
    now = NOW + timedelta(hours=8)
    cli = Cli(failure=failure)
    agent = LearningAgent(tmp_path, ledger, lambda: now)
    reviewer = CodexLearningReviewer(tmp_path, runner=cli, command=["codex"])
    result = agent.run(now, reviewer)
    assert agent.run(now, reviewer) == result
    assert result["status"] == "PARTIAL"
    assert result["llm"]["error_category"] == category
    assert result["llm"]["transient"] is transient
    assert result["llm"]["usage"]["input_tokens"] is None
    assert "private-token" not in json.dumps(result)
    assert len([row for row in cli.calls if "exec" in row[0]]) == 1


def test_auth_failure_does_not_probe_model_and_invalid_response_accounts_usage(tmp_path):
    ledger = populated(tmp_path)
    now = NOW + timedelta(hours=8)
    agent = LearningAgent(tmp_path, ledger, lambda: now)
    packet = review_packet(agent._build(now, now))
    auth = Cli(auth="Logged in using an API key - private-token")
    result = CodexLearningReviewer(tmp_path, runner=auth, command=["codex"]).review(packet)
    assert result["status"] == "UNAVAILABLE" and result["error_category"] == "AUTH_REQUIRED"
    assert not any("exec" in row[0] for row in auth.calls)
    invalid = Cli(invalid=True)
    result = CodexLearningReviewer(tmp_path, runner=invalid, command=["codex"]).review(packet)
    assert result["status"] == "INVALID_RESPONSE"
    assert result["usage"]["total_tokens"] == 120
    assert "invented_profit" not in json.dumps(result)


def test_legacy_estimated_wager_price_does_not_become_actual_roi(tmp_path):
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: NOW)
    ledger.save_run(report(ticket()))
    ledger.record_wager("ticket-1")  # Legacy path: research estimate, not accepted bookmaker price.
    ledger.now = lambda: NOW + timedelta(hours=6)
    ledger.refresh_results(Football({i: fixture(i) for i in (1, 2, 3)}))
    measured = measure(ledger.path, NOW + timedelta(hours=8))
    actual = measured["metrics"]["actual"]
    assert actual["tickets"]["won"] == 1
    assert actual["financial"]["available"] is False
    assert actual["financial"]["net_eur"] is None and actual["financial"]["roi_pct"] is None
    assert measured["coverage"]["unverified_actual_prices"] == 1


def test_historical_replay_never_spends_another_current_day_ai_call(tmp_path):
    ledger = populated(tmp_path)
    now = NOW + timedelta(days=2)
    reviewer = Review()
    result = LearningAgent(tmp_path, ledger, lambda: now).run(NOW + timedelta(hours=8), reviewer)
    assert result["llm"]["status"] == "SKIPPED_NO_DATA"
    assert not reviewer.calls


def test_missing_export_is_recreated_without_repeating_review(tmp_path):
    ledger = populated(tmp_path)
    now = NOW + timedelta(hours=8)
    agent, reviewer = LearningAgent(tmp_path, ledger, lambda: now), Review()
    first = agent.run(now, reviewer)
    exported = next((tmp_path / "reports/learning").glob("*.json"))
    exported.unlink()  # Only this test's disposable export.
    assert agent.run(now, reviewer) == first
    assert exported.is_file() and len(reviewer.calls) == 1


def test_legacy_fallback_never_revives_a_post_kickoff_published_draft(tmp_path):
    published = NOW + timedelta(hours=4)
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: published)
    original = report(ticket())  # Draft at NOW, kickoff NOW+3h.
    original["finished_at"] = published.isoformat()
    ledger.save_run(original)
    assert ledger.list_recommendations() == []
    measured = measure(ledger.path, NOW + timedelta(hours=5))
    assert measured["metrics"]["research"]["tickets"]["total"] == 0
    assert measured["coverage"]["legacy_original_snapshot_selections"] == 0
