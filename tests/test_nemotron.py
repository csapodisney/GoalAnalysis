"""Codex advisory boundaries; every inference is an injected local fake."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from goal_analysis.agents import nemotron as module
from goal_analysis.agents.nemotron import MODEL, NemotronAdvisor
from goal_analysis.learning._findings import findings_and_proposals
from goal_analysis.learning._metrics import digest

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


def report():
    leg = {"fixture_id": "api_football:123", "decimal_price": 2.2,
           "quoted_at": NOW.isoformat(), "source_url": "https://example.com/odds?api_key=do-not-send",
           "bookmaker": "test", "model_probability": 0.6}
    return {"run_id": "test-run", "date": "2026-10-12", "observed_at": NOW.isoformat(),
            "actual_wagers": [{"private": "USER_PRIVATE_WAGER", "stake_eur": 12345}],
            "recommendations": [
                {"profile_id": "gauss", "strategy_version": "v1", "legs": [leg]},
                {"profile_id": "persona", "legs": [deepcopy(leg)]},
                {"profile_id": "abstained", "legs": []}]}


def learning():
    values = {"tickets": {"total": 12, "decided": 8, "won": 3, "lost": 5},
              "small_sample": True, "missing_odds_legs": 2, "shared_fixture_occurrences": 4,
              "financial": {"stake_eur": 777, "private": "PRIVATE_FINANCIAL"}}
    research = {"scope": "research", "kind": "SCOPE_SUMMARY", "label": "research",
                "metrics": values, "source_refs": [{"table": "portfolio_runs", "key": "test-run", "hash": "a" * 64}]}
    research["evidence_id"] = "ev-" + digest(research)[:24]
    actual = deepcopy(research)
    actual["scope"] = "actual"
    actual["metrics"]["financial"]["private"] = "ACTUAL_ONLY_PRIVATE"
    actual["evidence_id"] = "ev-" + digest({k: v for k, v in actual.items() if k != "evidence_id"})[:24]
    evidence = [research, actual]
    _, proposals = findings_and_proposals(evidence)
    return {"date": "2026-10-12", "report_id": "learning-2026-10-12-abc",
            "as_of": NOW.isoformat(), "evidence": evidence, "recommendations": proposals}


class Runner:
    def __init__(self, change=None):
        self.calls = []
        self.change = change

    def __call__(self, path):
        assert path.is_absolute() and path.name == "tasks.json"
        tasks = json.loads(path.read_text(encoding="utf-8"))
        self.calls.append(tasks)
        rows = []
        for task in tasks:
            packet = json.loads(task["prompt"].split("INPUT DATA:\n", 1)[1])
            option = packet["proposal_options"][0]
            rows.append({"Name": task["name"], "Success": True, "Model": MODEL, "FinishReason": "stop",
                         "PromptTokens": 100, "CompletionTokens": 25,
                         "Answer": json.dumps({"priorities": [{"recommendation_id": option["recommendation_id"],
                                                               "priority": "HIGH", "evidence_ids": option["evidence_ids"]}]})})
        payload = {"Results": rows, "TaskCount": len(rows), "UsageComplete": True,
                   "KnownPromptTokens": 999999, "KnownCompletionTokens": 999999}
        if self.change:
            self.change(payload)
        return payload


def advisor(tmp_path, runner):
    return NemotronAdvisor(tmp_path, runner=runner, clock=lambda: NOW)


def test_readonly_snapshot_does_not_create_files_or_call_runner(tmp_path):
    runner = Runner()
    assert advisor(tmp_path, runner).snapshot()["report"] is None
    assert not list(tmp_path.iterdir()) and not runner.calls


def test_report_success_two_workers_original_unchanged_and_minimized(tmp_path):
    runner = Runner()
    original = report()
    before = deepcopy(original)
    result = advisor(tmp_path, runner).review_report(original)
    assert result["status"] == "COMPLETE"
    assert result["automatic_changes"] is False and original == before
    assert result["usage"] == {"requests": 2, "known_input_tokens": 200, "known_output_tokens": 50,
                               "usage_complete": True, "unknown_usage_tasks": []}
    assert len(runner.calls) == 1 and len(runner.calls[0]) == 2
    prompts = json.dumps(runner.calls)
    assert "USER_PRIVATE_WAGER" not in prompts and "do-not-send" not in prompts
    assert "stake_eur" not in prompts and "actual_wagers" not in prompts
    assert all(len(t["prompt"]) <= 32000 for t in runner.calls[0])
    packet = json.loads(runner.calls[0][0]["prompt"].split("INPUT DATA:\n", 1)[1])
    assert packet["evidence"][0]["metrics"]["shared_fixture_occurrences"] == 1
    assert packet["source_snapshot_sha256"] == digest(original)
    assert not list((tmp_path / "data/arthur/codex-advisory-tmp").iterdir())


def test_repeat_same_run_uses_immutable_cached_result_after_policy_change(tmp_path, monkeypatch):
    runner = Runner()
    agent = advisor(tmp_path, runner)
    first = agent.review_report(report())
    monkeypatch.setattr(module, "POLICY_VERSION", "future-policy")
    assert agent.review_report(report()) == first == agent.snapshot("test-run")["report"]
    assert len(runner.calls) == 1
    with sqlite3.connect(agent.path) as db:
        for table in ("advisory_claims", "advisory_results"):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f"DELETE FROM {table}")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE advisory_results SET payload='{}'")


def test_conflicting_run_evidence_rejected_without_overwrite(tmp_path):
    runner = Runner()
    agent = advisor(tmp_path, runner)
    first = agent.review_report(report())
    changed = report()
    changed["recommendations"][0]["legs"][0]["decimal_price"] = 2.5
    assert agent.review_report(changed)["status"] == "UNAVAILABLE"
    assert agent.snapshot("test-run")["report"] == first
    assert len(runner.calls) == 1


def test_learning_one_worker_research_only_once_per_day(tmp_path):
    runner = Runner()
    agent = advisor(tmp_path, runner)
    original = learning()
    before = deepcopy(original)
    result = agent.review_learning(original)
    assert result["status"] == "COMPLETE" and result["subject_id"] == "2026-10-12"
    assert original == before and result["usage"]["requests"] == 1
    assert len(runner.calls[0]) == 1
    prompts = json.dumps(runner.calls)
    assert "PRIVATE_FINANCIAL" not in prompts and "ACTUAL_ONLY_PRIVATE" not in prompts
    assert "stake_eur" not in prompts and '"actual"' not in prompts
    assert agent.review_learning(original) == result
    changed = deepcopy(original)
    changed["report_id"] = "different-same-day"
    assert agent.review_learning(changed)["status"] == "UNAVAILABLE"
    assert len(runner.calls) == 1


def test_learning_actual_only_skips_request(tmp_path):
    runner = Runner()
    source = learning()
    source["evidence"] = [e for e in source["evidence"] if e["scope"] == "actual"]
    result = advisor(tmp_path, runner).review_learning(source)
    assert result["status"] == "SKIPPED_NO_DATA" and not runner.calls


@pytest.mark.parametrize("change", [
    lambda p: p.update(priorities=[{"recommendation_id": "invented", "priority": "HIGH", "evidence_ids": []}]),
    lambda p: p["priorities"][0].update(evidence_ids=["other-evidence"]),
    lambda p: p["priorities"].append(deepcopy(p["priorities"][0])),
    lambda p: p["priorities"][0].update(detail="New 99% winning prediction"),
    lambda p: p["priorities"][0].update(priority="CRITICAL"),
    lambda p: p.update(priorities=[]),
    lambda p: p.update(invented_profit=999),
    lambda p: p["priorities"][0].update(recommendation_id=[]),
])
def test_untrusted_model_schema_ids_and_new_facts_rejected(tmp_path, change):
    def mutate(payload):
        row = payload["Results"][0]
        response = json.loads(row["Answer"])
        change(response)
        row["Answer"] = json.dumps(response)
    runner = Runner(mutate)
    result = advisor(tmp_path, runner).review_report(report())
    assert result["status"] == "PARTIAL"
    assert result["tasks"][0]["status"] == "INVALID_RESPONSE"
    assert not result["tasks"][0]["proposals"]
    assert "99%" not in json.dumps(result) and "invented_profit" not in json.dumps(result)


@pytest.mark.parametrize("field,value", [("Success", False), ("FinishReason", "length"), ("Model", "different-model")])
def test_incomplete_or_wrong_model_never_complete(tmp_path, field, value):
    runner = Runner(lambda raw: raw["Results"][0].update({field: value}))
    result = advisor(tmp_path, runner).review_report(report())
    assert result["status"] == "PARTIAL" and not result["tasks"][0]["proposals"]


def test_unknown_usage_preserved_even_when_answer_valid(tmp_path):
    runner = Runner(lambda raw: raw["Results"][0].pop("PromptTokens"))
    result = advisor(tmp_path, runner).review_report(report())
    assert result["status"] == "COMPLETE"
    assert result["usage"]["known_input_tokens"] == 100
    assert result["usage"]["usage_complete"] is False
    assert result["usage"]["unknown_usage_tasks"] == ["risk"]


@pytest.mark.parametrize("mode", ["timeout", "secret", "malformed", "oversized"])
def test_failure_bounded_redacted_and_never_retried(tmp_path, mode):
    calls = []
    def fail(path):
        calls.append(path)
        if mode == "timeout":
            raise TimeoutError("provider raw error")
        if mode == "secret":
            raise RuntimeError("nvapi-SECRET-do-not-log")
        return {"Results": "bad"} if mode == "malformed" else {"junk": "S" * 200000}
    agent = advisor(tmp_path, fail)
    result = agent.review_report(report())
    assert result["status"] == "UNAVAILABLE" and result["usage"]["requests"] == 2
    assert not result["usage"]["usage_complete"]
    assert agent.review_report(report()) == result and len(calls) == 1
    assert "SECRET" not in json.dumps(result) and "provider raw" not in json.dumps(result)


def test_crash_after_claim_does_not_retry(tmp_path):
    def crash(path):
        raise KeyboardInterrupt
    agent = advisor(tmp_path, crash)
    with pytest.raises(KeyboardInterrupt):
        agent.review_report(report())
    runner = Runner()
    recovered = advisor(tmp_path, runner)
    assert recovered.review_report(report())["status"] == "INTERRUPTED"
    assert recovered.snapshot()["report"]["status"] == "INTERRUPTED"
    assert not runner.calls


def test_concurrent_run_claims_once_and_does_not_block_inference_transaction(tmp_path):
    entered, release = threading.Event(), threading.Event()
    runner = Runner()
    def blocking(path):
        entered.set()
        assert release.wait(5)
        return runner(path)
    agent = advisor(tmp_path, blocking)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(agent.review_report, report())
        assert entered.wait(5)
        second = agent.review_report(report())
        assert second["status"] == "INTERRUPTED"
        release.set()
        assert first.result(timeout=5)["status"] == "COMPLETE"
    assert len(runner.calls) == 1


def test_corrupt_database_fails_closed_without_provider(tmp_path):
    runner = Runner()
    agent = advisor(tmp_path, runner)
    agent.path.parent.mkdir(parents=True)
    agent.path.write_bytes(b"not SQLite")
    assert agent.review_report(report())["status"] == "UNAVAILABLE"
    assert agent.snapshot()["report"] is None and not runner.calls


def test_existing_empty_database_is_not_reinitialized_and_billed(tmp_path):
    runner = Runner()
    agent = advisor(tmp_path, runner)
    agent.path.parent.mkdir(parents=True)
    agent.path.touch()
    assert agent.review_report(report())["status"] == "UNAVAILABLE"
    assert not runner.calls and agent.path.stat().st_size == 0


def test_corrupt_payload_hash_fails_closed(tmp_path):
    runner = Runner()
    agent = advisor(tmp_path, runner)
    agent.review_report(report())
    with sqlite3.connect(agent.path) as db:
        db.execute("DROP TRIGGER results_no_update")
        db.execute("UPDATE advisory_results SET payload='{}'")
    assert agent.review_report(report())["status"] == "UNAVAILABLE"
    assert agent.snapshot()["report"] is None and len(runner.calls) == 1


def test_no_data_does_not_call_provider(tmp_path):
    runner = Runner()
    original = report()
    original["recommendations"] = []
    result = advisor(tmp_path, runner).review_report(original)
    assert result["status"] == "SKIPPED_NO_DATA" and result["usage"]["requests"] == 0
    assert not runner.calls


@pytest.mark.parametrize("change", [
    lambda r: r.update(run_id="../../bad"),
    lambda r: r.pop("observed_at"),
    lambda r: r.update(observed_at="2026-10-12T10:00:00"),
    lambda r: r["recommendations"].append(deepcopy(r["recommendations"][0])),
    lambda r: r["recommendations"][0]["legs"][0].update(decimal_price=float("nan")),
])
def test_invalid_report_fails_without_provider_or_changes(tmp_path, change):
    runner = Runner()
    original = report()
    change(original)
    assert advisor(tmp_path, runner).review_report(original)["status"] == "UNAVAILABLE"
    assert not runner.calls


def test_duplicate_unknown_workers_fail_closed(tmp_path):
    runner = Runner(lambda raw: raw["Results"].append(deepcopy(raw["Results"][0])))
    result = advisor(tmp_path, runner).review_report(report())
    assert result["status"] == "UNAVAILABLE" and not result["tasks"]


def test_missing_worker_is_partial_with_unknown_usage(tmp_path):
    runner = Runner(lambda raw: raw["Results"].pop())
    result = advisor(tmp_path, runner).review_report(report())
    assert result["status"] == "PARTIAL"
    assert result["usage"]["unknown_usage_tasks"] == ["data_quality"]


def test_environment_drops_credentials_and_untrusted_paths(monkeypatch):
    from goal_analysis.agents.codex_review import _environment

    for name in ("OPENAI_API_KEY", "API_FOOTBALL_KEY", "NVIDIA_API_KEY"):
        monkeypatch.setenv(name, "never-export")
    monkeypatch.setenv("PATH", "C:/untrusted")
    env = _environment()
    assert "never-export" not in json.dumps(env)
    assert env["PATH"] == "C:/untrusted"


def test_configured_uses_codex_cli_without_provider_credentials(monkeypatch):
    from goal_analysis.agents import codex_review

    monkeypatch.setattr(codex_review, "codex_installed", lambda: True)
    assert NemotronAdvisor.configured()
    monkeypatch.setattr(codex_review, "codex_installed", lambda: False)
    assert not NemotronAdvisor.configured()


def test_not_configured_is_saved_without_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(NemotronAdvisor, "configured", staticmethod(lambda: False))
    agent = NemotronAdvisor(tmp_path, clock=lambda: NOW)
    result = agent.review_report(report())
    assert result["status"] == "UNAVAILABLE" and result["usage"]["requests"] == 0
    assert agent.review_report(report()) == result


def test_learning_modified_evidence_hash_rejected(tmp_path):
    original = learning()
    original["evidence"][0]["metrics"]["tickets"]["won"] = 1000
    runner = Runner()
    assert advisor(tmp_path, runner).review_learning(original)["status"] == "UNAVAILABLE"
    assert not runner.calls


def test_post_inference_persistence_failure_retains_usage_and_never_retries(tmp_path, monkeypatch):
    runner = Runner()
    agent = advisor(tmp_path, runner)
    def fail_save(*args):
        raise sqlite3.OperationalError("SECRET storage failure")
    monkeypatch.setattr(agent, "_save_result", fail_save)
    result = agent.review_report(report())
    assert result["status"] == "INTERRUPTED"
    assert result["usage"]["requests"] == 2 and result["usage"]["known_input_tokens"] == 200
    assert result["usage"]["usage_complete"] is True and not result["tasks"]
    retry = agent.review_report(report())
    assert retry["status"] == "INTERRUPTED" and retry["usage"]["usage_complete"] is False
    assert len(runner.calls) == 1 and "SECRET" not in json.dumps(result)


def test_legacy_quote_provider_source_counts_without_url_or_timestamp(tmp_path):
    original = report()
    for row in original["recommendations"]:
        for leg in row["legs"]:
            leg.pop("source_url")
            leg.pop("quoted_at")
            leg.update(quote_provider="the_odds_api", quote_source_id="event123")
    runner = Runner()
    assert advisor(tmp_path, runner).review_report(original)["status"] == "COMPLETE"
    packet = json.loads(runner.calls[0][1]["prompt"].split("INPUT DATA:\n", 1)[1])
    ev = packet["evidence"][0]
    assert ev["metrics"]["missing_quote_timestamps"] == 2
    assert ev["metrics"]["incomplete_quote_provenance"] == 0
    assert ev["provider_context_urls_not_exact_quote_urls"] == ["https://the-odds-api.com/"]
    assert "quote_coverage" not in {p["recommendation_id"] for p in packet["proposal_options"]}


def test_publication_cutoff_and_incomplete_web_context_are_explicit(tmp_path):
    original = report()
    original["finished_at"] = "2026-10-12T12:05:00+00:00"
    original["astra"] = {"research_status": "UNAVAILABLE", "research_notes": "UNTRUSTED DO NOT SEND"}
    runner = Runner()
    result = advisor(tmp_path, runner).review_report(original)
    assert result["evidence_as_of"] == original["finished_at"]
    packet = json.loads(runner.calls[0][1]["prompt"].split("INPUT DATA:\n", 1)[1])
    ev = packet["evidence"][0]
    assert ev["source_observed_at"] == original["observed_at"]
    assert ev["publication_cutoff"] == original["finished_at"]
    assert "web_context_gap" in {p["recommendation_id"] for p in packet["proposal_options"]}
    assert "UNTRUSTED DO NOT SEND" not in json.dumps(runner.calls)


def test_learning_day_requires_berlin_cutoff_day(tmp_path):
    original = learning()
    original["as_of"] = "2026-10-12T23:15:00+00:00"
    runner = Runner()
    assert advisor(tmp_path, runner).review_learning(original)["status"] == "UNAVAILABLE"
    assert not runner.calls


def test_default_process_uses_codex_structured_output(tmp_path, monkeypatch):
    from goal_analysis.agents import codex_review

    captured = []
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps([{"name": "profile", "prompt": "Return {\"option_id\":\"x\"}. INPUT:\n{}"}]), encoding="utf-8")

    class FakeReviewer:
        def __init__(self, **kwargs):
            captured.append(kwargs)
        def _ensure_login(self):
            captured.append("login")
        def _request(self, body, usage):
            captured.append(body)
            return {"usage": {"input_tokens": 7, "output_tokens": 2}, "output": [
                {"content": [{"text": '{"option_id":"x"}'}]}]}

    monkeypatch.setattr(codex_review, "CodexReviewer", FakeReviewer)
    result = module._run_helper(path)
    assert result["Results"][0]["Model"] == "gpt-6-astra"
    assert result["Results"][0]["Answer"] == '{"option_id":"x"}'
    assert captured[0]["web_search"] is False and "login" in captured


def test_codex_adapter_hash_is_stable_and_contains_no_nvidia_call():
    text = module.ADAPTER.read_text(encoding="utf-8")
    assert "forced_login_method" in text and '"chatgpt"' in text
    assert "Invoke-Nemotron" not in text and "nvidia-api" not in text.lower()
    assert module._bootstrap_hash() == __import__("hashlib").sha256(module.ADAPTER.read_bytes()).hexdigest()
