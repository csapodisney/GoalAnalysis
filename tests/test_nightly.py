"""Daily orchestration contracts; no sports or model service is called."""

import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from test_daily_223_live import config
from test_portfolio_ledger import Football, fixture

from goal_analysis.config.portfolio import DEFAULT_SETTINGS, validate_settings
from goal_analysis.jobs.nightly import (
    JobStore,
    NightlyWorkflow,
    WorkflowError,
    automation_snapshot,
    schedule_plan,
)
from goal_analysis.jobs.portfolio import RunLock
from goal_analysis.storage.portfolio_ledger import PortfolioLedger
from goal_analysis.strategy_provenance import strategy_manifest

NOW = datetime(2026, 10, 7, 20, tzinfo=UTC)


def setup_workflow(tmp_path, **overrides):
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "daily223-live.json").write_text(json.dumps(config()), "utf-8")
    clock = [NOW]
    calls = []
    ledger = PortfolioLedger(tmp_path / "data/arthur/ledger.sqlite3", now=lambda: clock[0])

    def generate(target, settings, job):
        calls.append("generation")
        records = []
        for index, profile in enumerate(settings["enabled_profiles"]):
            records.append({
                "profile_id": profile, "profile_name": profile, "date": target.isoformat(),
                "created_at": clock[0].isoformat(), "status": "ODDS_PENDING", "stake_eur": None,
                "combined_price": None, "playable": False, "reasoning": ["Synthetic test only"],
                "legs": [{"fixture_id": f"api_football:{index + 1}",
                          "kickoff": datetime(target.year, target.month, target.day, 12, tzinfo=UTC).isoformat(),
                          "decimal_price": None, "quoted_at": None,
                          "market_key": "totals_2_5", "selection_key": "over", "period": "FULL_TIME",
                          "home_team": "Synthetic Home", "away_team": "Synthetic Away"}],
            })
        report = {
            "schema_version": 3, "run_id": "synthetic", "date": target.isoformat(),
            "observed_at": clock[0].isoformat(), "finished_at": clock[0].isoformat(),
            "tickets": [], "recommendations": records, "construction_status": "ODDS_PENDING",
            "astra": {"status": "COMPLETE", "usage": {"requests": 1}},
            "usage": {"football_calls": 0}, "real_wager_placed": False,
        }
        return {"schema_version": 3, "artifacts": {"report": report}}

    def settle(job):
        calls.append("settlement")
        return {"errors": [], "remaining_fixtures": 0}

    def learn(as_of):
        calls.append("learning")
        return {"report_id": "test-learning", "as_of": as_of.isoformat(),
                "status": "COMPLETE", "llm": {"status": "COMPLETE"}}

    kwargs = {"clock": lambda: clock[0], "generator": generate, "settler": settle,
              "learner": learn, "ledger": ledger, **overrides}
    return NightlyWorkflow(tmp_path, **kwargs), clock, calls


@pytest.mark.parametrize(("stamp", "due", "target", "offset"), [
    ("2026-10-07T19:59:59+00:00", False, "2026-10-07", "+02:00"),
    ("2026-10-07T20:00:00+00:00", True, "2026-10-08", "+02:00"),
    ("2026-10-08T03:59:59+00:00", True, "2026-10-08", "+02:00"),
    ("2026-10-08T04:00:00+00:00", False, "2026-10-08", "+02:00"),
    ("2026-10-24T20:00:00+00:00", True, "2026-10-25", "+01:00"),
    ("2026-10-25T21:00:00+00:00", True, "2026-10-26", "+01:00"),
    ("2026-03-28T21:00:00+00:00", True, "2026-03-29", "+02:00"),
    ("2026-12-31T21:00:00+00:00", True, "2027-01-01", "+01:00"),
])
def test_berlin_gate_and_dst(stamp, due, target, offset):
    plan = schedule_plan(datetime.fromisoformat(stamp))
    assert plan["due"] is due and plan["target_date"] == target
    assert plan["next_run_at"].endswith(offset)
    assert "T22:00:00" in plan["next_run_at"]


def test_outside_window_and_check_are_read_only(tmp_path):
    workflow = NightlyWorkflow(tmp_path, clock=lambda: NOW - timedelta(hours=2))
    assert workflow.run()["status"] == "NOT_DUE"
    view = automation_snapshot(tmp_path, NOW)
    assert view["current_job"] is None and view["schedule"]["enabled"] is False
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError):
        schedule_plan(datetime(2026, 10, 7, tzinfo=UTC).replace(tzinfo=None))


def test_completed_generation_without_durable_checkpoint_never_replays(tmp_path, monkeypatch):
    workflow, clock, calls = setup_workflow(tmp_path)
    original = Path.write_text

    def fail_staging(path, *args, **kwargs):
        if "nightly-staging" in path.parts:
            raise OSError("Simulated full disk")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_staging)
    outcome = workflow.run()
    stage = workflow._stage(outcome["job"], "generation")
    assert stage["status"] == "BLOCKED"
    assert stage["reason"] == "UNKNOWN_EXECUTION_REQUIRES_REVIEW"
    clock[0] += timedelta(minutes=16)
    assert workflow.run()["status"] == "ALREADY_FINISHED"
    assert calls.count("generation") == 1


def test_corrupt_generation_checkpoint_is_not_regenerated(tmp_path):
    workflow, clock, calls = setup_workflow(tmp_path)
    job = workflow._new_job(schedule_plan(clock[0]))
    stage = workflow._stage(job, "generation")
    stage.update(status="RUNNING", attempts=1, run_id="partial")
    checkpoint = tmp_path / "data/arthur/nightly-staging/partial.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("{broken", encoding="utf-8")
    workflow.store.save(job)
    outcome = workflow.run()
    assert workflow._stage(outcome["job"], "generation")["reason"] == "CHECKPOINT_REQUIRES_REVIEW"
    assert "generation" not in calls


def test_provenance_includes_inline_prompts_and_input_policy():
    source = strategy_manifest(DEFAULT_SETTINGS)["source_files"]
    assert {"agents/codex_review.py", "agents/astra_review.py", "models/__init__.py",
            "jobs/daily_223_candidates.py", "config/portfolio.py"} <= source.keys()


def test_corrupt_automation_store_does_not_break_status(tmp_path):
    path = tmp_path / "data/arthur/automation.sqlite3"
    path.parent.mkdir(parents=True)
    path.write_text("not sqlite", encoding="utf-8")
    assert automation_snapshot(tmp_path, NOW)["store_status"] == "AUTOMATION_STORE_UNAVAILABLE"


def test_dashboard_exposes_automation_and_missing_learning_without_network(tmp_path):
    from goal_analysis.portfolio_dashboard import ArthurState

    setup_workflow(tmp_path)
    view = ArthurState(tmp_path).snapshot("2026-10-08")
    assert view["application_version"] == "3.11"
    assert view["automation"]["scheduled_time"] == "22:00"
    assert view["learning"] is None and view["learning_history"] == []
    learning_db = tmp_path / "data/learning/learning.sqlite3"
    learning_db.parent.mkdir(parents=True)
    learning_db.write_text("not sqlite", encoding="utf-8")
    assert ArthurState(tmp_path).snapshot()["learning_error"] == "LEARNING_REPORT_UNAVAILABLE"


def test_nightly_real_ledger_learning_and_dashboard_contract(tmp_path):
    from goal_analysis.learning import LearningAgent
    from goal_analysis.portfolio_dashboard import ArthurState

    workflow, clock, _ = setup_workflow(tmp_path)

    class NoModelExpected:
        def review(self, packet):
            raise AssertionError("Only future fixtures: no model request is justified")

    agent = LearningAgent(tmp_path, workflow.ledger, clock=lambda: clock[0])
    workflow.learner = lambda cutoff: agent.run(cutoff, NoModelExpected())
    assert workflow.run()["status"] == "COMPLETED"
    view = ArthurState(tmp_path).snapshot("2026-10-08")
    report = view["learning"]
    assert view["automation"]["current_job"]["status"] == "COMPLETED"
    assert report["llm"]["status"] == "SKIPPED_NO_DATA"
    assert report["metrics"]["research"]["tickets"]["pending"] == len(DEFAULT_SETTINGS["enabled_profiles"])
    assert report["metrics"]["frozen"]["tickets"]["pending"] == len(DEFAULT_SETTINGS["enabled_profiles"])
    assert report["metrics"]["actual"]["tickets"]["total"] == 0
    assert report["automatic_changes"] is False
    assert view["learning_history"][0]["report_hash"] == report["report_hash"]


def test_all_profiles_frozen_once_and_provenance_immutable(tmp_path):
    workflow, clock, calls = setup_workflow(tmp_path)
    outcome = workflow.run()
    assert outcome["status"] == "COMPLETED"
    assert calls == ["settlement", "generation", "learning"]
    assert workflow.run()["status"] == "ALREADY_FINISHED"
    assert calls == ["settlement", "generation", "learning"]
    packages = workflow.ledger.list_freezes()
    size = len(DEFAULT_SETTINGS["enabled_profiles"])
    assert len(packages) == 1 and len(packages[0]["recommendations"]) == size
    assert workflow.ledger.performance(mode="actual")["summary"]["stake_eur"] == 0
    report = workflow.ledger.list_runs()[0]
    assert report["run_id"] == "nightly-2026-10-08-a1"
    assert report["strategy_manifest"]["manifest_sha256"]
    assert all(r["strategy_version"].startswith(r["profile_id"] + "-v3.11-") for r in report["recommendations"])
    assert all(r["legs"][0]["decimal_price"] is None for r in report["recommendations"])
    exported = tmp_path / "reports/arthur" / report["run_id"] / "report.json"
    assert json.loads(exported.read_text("utf-8")) == report
    before = packages[0]["snapshot_hash"]
    clock[0] += timedelta(days=1)
    workflow.ledger.refresh_call_limit = size
    workflow.ledger.refresh_results(Football({i: fixture(i) for i in range(1, size + 1)}))
    after = workflow.ledger.list_freezes()[0]
    assert after["snapshot_hash"] == before and after["won"] == size
    assert JobStore(tmp_path).jobs()[0]["status"] == "COMPLETED"


def test_only_failed_generation_retries_and_wait_is_respected(tmp_path):
    workflow, clock, calls = setup_workflow(tmp_path)
    original = workflow.generator
    count = [0]

    def unstable(*args):
        count[0] += 1
        if count[0] == 1:
            raise OSError("private-provider-credential")
        return original(*args)

    workflow.generator = unstable
    first = workflow.run()
    assert first["status"] == "RETRY_WAIT" and calls == ["settlement"]
    assert "private-provider-credential" not in json.dumps(first)
    workflow.run()
    assert count == [1]
    clock[0] += timedelta(minutes=15)
    assert workflow.run()["status"] == "COMPLETED"
    assert count == [2] and calls == ["settlement", "generation", "learning"]
    assert len(workflow.ledger.list_runs()) == 1


def test_settlement_retry_does_not_repeat_generation_or_archive(tmp_path):
    workflow, clock, calls = setup_workflow(tmp_path)
    attempts = [0]

    def settle(job):
        attempts[0] += 1
        return {"errors": [{"error": "timeout"}] if attempts[0] == 1 else []}

    workflow.settler = settle
    assert workflow.run()["status"] == "RETRY_WAIT"
    assert calls == ["generation"] and len(workflow.ledger.list_freezes()) == 1
    clock[0] += timedelta(minutes=15)
    assert workflow.run()["status"] == "COMPLETED"
    assert calls == ["generation", "learning"] and attempts == [2]


def test_saved_generation_recovers_after_checkpoint_crash_without_recall(tmp_path):
    workflow, _, calls = setup_workflow(tmp_path)
    workflow.run()
    job = workflow.store.jobs()[0]
    job["status"] = "RUNNING"
    generation = workflow._stage(job, "generation")
    generation.update(status="RUNNING", next_retry_at=None)
    workflow.store.save(job)
    assert workflow.run()["status"] == "COMPLETED"
    assert calls == ["settlement", "generation", "learning"]
    assert len(workflow.ledger.list_runs()) == len(workflow.ledger.list_freezes()) == 1


def test_unknown_generation_execution_never_repeats_charge(tmp_path):
    workflow, _, calls = setup_workflow(tmp_path)
    job = workflow._new_job(schedule_plan(NOW))
    workflow._stage(job, "generation").update(status="RUNNING", attempts=1, run_id="nightly-2026-10-08-a1")
    workflow.store.save(job)
    outcome = workflow.run()
    assert outcome["status"] == "COMPLETED_WITH_WARNINGS"
    assert calls == ["settlement", "learning"]
    assert workflow._stage(outcome["job"], "generation")["reason"] == "UNKNOWN_EXECUTION_REQUIRES_REVIEW"


def test_permanent_auth_failure_still_learns_and_does_not_retry(tmp_path):
    workflow, _, calls = setup_workflow(tmp_path)

    def blocked(*args):
        raise WorkflowError("AUTH_REQUIRED", retryable=False)

    workflow.generator = blocked
    outcome = workflow.run()
    assert outcome["status"] == "COMPLETED_WITH_WARNINGS"
    assert workflow._stage(outcome["job"], "generation")["status"] == "BLOCKED"
    assert calls == ["settlement", "learning"]
    assert workflow.run()["status"] == "ALREADY_FINISHED"


def test_retry_is_bounded_and_expired_slot_cannot_backfill(tmp_path):
    workflow, clock, calls = setup_workflow(tmp_path)

    def unavailable(*args):
        raise OSError("temporary")

    workflow.generator = unavailable
    assert workflow.run()["status"] == "RETRY_WAIT"
    clock[0] += timedelta(minutes=15)
    assert workflow.run()["status"] == "RETRY_WAIT"
    clock[0] += timedelta(minutes=30)
    third = workflow.run()
    assert third["status"] == "COMPLETED_WITH_WARNINGS"
    assert workflow._stage(third["job"], "generation")["attempts"] == 3
    assert calls == ["settlement", "learning"]
    job = workflow.store.jobs()[0]
    job["status"] = "RETRY_WAIT"
    workflow._stage(job, "generation")["status"] = "RETRY_WAIT"
    workflow.store.save(job)
    clock[0] = datetime(2026, 10, 8, 4, tzinfo=UTC)
    assert workflow.run()["status"] == "NOT_DUE"
    assert workflow.store.jobs()[0]["status"] == "EXPIRED"


def test_shared_manual_run_lock_defers_daily_without_new_job(tmp_path):
    workflow, _, calls = setup_workflow(tmp_path)
    with RunLock(tmp_path / "data/arthur/run.lock"):
        assert workflow.run()["status"] == "BUSY"
    assert calls == [] and not workflow.store.jobs()


def test_existing_manual_freeze_preserved_and_disabled_profiles_respected(tmp_path):
    workflow, _, _ = setup_workflow(tmp_path)
    settings = validate_settings({"operating_contract_version": 1})
    settings["enabled_profiles"] = ["daily223", "gauss"]
    (tmp_path / "config/arthur-settings.json").write_text(json.dumps(settings), "utf-8")
    old = workflow.generator(date(2026, 10, 8), settings, {})["artifacts"]["report"]
    workflow.ledger.save_run(old)
    workflow.ledger.freeze_run(old["run_id"])
    old_hash = workflow.ledger.list_freezes()[0]["snapshot_hash"]
    outcome = workflow.run()
    assert outcome["status"] == "COMPLETED_WITH_WARNINGS"
    assert workflow.ledger.list_freezes()[0]["snapshot_hash"] == old_hash
    assert len(workflow.ledger.list_runs()) == 2
    assert len(workflow.ledger.list_runs()[0]["recommendations"]) == 2
    assert json.loads((tmp_path / "config/arthur-settings.json").read_text("utf-8")) == settings


def test_budget_reserves_each_call_before_network_and_survives_reload(tmp_path, monkeypatch):
    workflow, _, _ = setup_workflow(tmp_path)
    job = workflow._new_job(schedule_plan(NOW))
    job["config"]["max_football_calls"] = 2
    monkeypatch.setenv("THE_STATS_API_KEY", "synthetic-secret")
    requests = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, endpoint, params):
            requests.append(1)
            assert workflow.store.get(job["target_date"])["usage"]["generation_football_calls"] == len(requests)
            raise OSError("temporary")

    monkeypatch.setattr("goal_analysis.providers.the_stats_primary.TheStatsPrimaryClient", Client)
    client = workflow._football(job, "generation")
    for _ in range(2):
        with pytest.raises(OSError):
            client.request("fixtures", {})
    with pytest.raises(WorkflowError, match="DAILY_REQUEST_BUDGET"):
        client.request("fixtures", {})
    assert len(requests) == 2
    assert "synthetic-secret" not in json.dumps(workflow.store.get(job["target_date"]))


def test_policy_versions_are_reproducible_and_sensitive_to_settings():
    first = strategy_manifest(DEFAULT_SETTINGS)
    assert first == strategy_manifest(deepcopy(DEFAULT_SETTINGS))
    changed = {**DEFAULT_SETTINGS, "strictness": 0}
    assert first["manifest_sha256"] != strategy_manifest(changed)["manifest_sha256"]


def test_seven_day_correction_window_preserves_manual_default(tmp_path):
    workflow, clock, _ = setup_workflow(tmp_path)
    workflow.run()
    clock[0] = datetime(2026, 10, 8, 18, tzinfo=UTC)
    size = len(DEFAULT_SETTINGS["enabled_profiles"])
    football = Football({i: fixture(i) for i in range(1, size + 1)})
    workflow.ledger.refresh_call_limit = size
    workflow.ledger.refresh_results(football)
    clock[0] += timedelta(days=3)
    assert workflow.ledger.refresh_results(football)["football_calls"] == 0
    weekly = PortfolioLedger(workflow.ledger.path, now=lambda: clock[0], correction_window_hours=168,
                             refresh_call_limit=size)
    assert weekly.refresh_results(football)["football_calls"] == size
