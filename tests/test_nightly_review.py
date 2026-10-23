"""Injected-only crash/retry review regressions; never call external providers."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs import nightly


@pytest.mark.parametrize("failed_checkpoint", ["ledger", "export"])
def test_completed_generation_is_reused_after_local_checkpoint_error(
    tmp_path, monkeypatch, failed_checkpoint,
):
    clock = [datetime(2026, 10, 7, 20, tzinfo=UTC)]
    generated, saved, failed = [], {}, []

    class Ledger:
        def save_run(self, report):
            if failed_checkpoint == "ledger" and not failed:
                failed.append("ledger")
                raise OSError("Injected transient local write failure")
            saved[report["run_id"]] = deepcopy(report)

    def generator(target, settings, job):
        generated.append(target)
        return {"artifacts": {"report": {
            "date": target.isoformat(), "construction_status": "COMPLETE",
            "recommendations": [{"profile_id": "daily223", "legs": []}],
            "astra": {"status": "COMPLETE"},
        }}}

    def export(directory, bundle):
        if failed_checkpoint == "export" and not failed:
            failed.append("export")
            raise OSError("Injected transient local write failure")
        directory.mkdir(parents=True)
        (directory / "report.json").write_text(
            json.dumps(bundle["artifacts"]["report"]), encoding="utf-8",
        )

    monkeypatch.setattr(nightly, "strategy_manifest", lambda settings: {"manifest_sha256": "fixed"})
    monkeypatch.setattr(nightly, "attach_strategy_provenance", lambda report, settings: None)
    monkeypatch.setattr(nightly, "write_portfolio_bundle", export)
    workflow = nightly.NightlyWorkflow(
        tmp_path, clock=lambda: clock[0], generator=generator, ledger=Ledger(),
        settler=lambda job: {}, learner=lambda cutoff: {"llm": {"status": "COMPLETE"}},
    )
    monkeypatch.setattr(workflow, "_saved_report", lambda run_id: deepcopy(saved.get(run_id)))
    plan = nightly.schedule_plan(clock[0])
    job = {
        "job_id": "nightly-" + plan["target_date"], "target_date": plan["target_date"],
        "slot_date": plan["slot_date"], "scheduled_at": plan["scheduled_at"],
        "deadline": plan["deadline"], "status": "PENDING", "manifest_sha256": "fixed",
        "settings": {"enabled_profiles": ["daily223"]}, "config": {}, "usage": {},
        "stages": [{"name": name, "status": "PENDING", "attempts": 0,
                    "next_retry_at": None, "result": {}} for name in nightly.STAGES],
    }
    stage = workflow._stage(job, "generation")
    workflow._execute_stage(job, "generation")
    assert stage["status"] == "RETRY_WAIT"
    first_run_id = stage["run_id"]
    assert len(generated) == 1

    clock[0] += timedelta(minutes=16)
    workflow._execute_stage(job, "generation")

    assert len(generated) == 1, "A durable completed bundle must not trigger another AI generation"
    assert stage["run_id"] == first_run_id
    assert stage["status"] == "PARTIAL"
    assert set(saved) == {first_run_id}


@pytest.mark.parametrize(("utc", "due", "target", "scheduled_offset"), [
    ("2026-03-28T21:00:00+00:00", True, "2026-03-29", "+01:00"),
    ("2026-03-29T03:59:00+00:00", True, "2026-03-29", "+01:00"),
    ("2026-03-29T04:00:00+00:00", False, "2026-03-29", "+01:00"),
    ("2026-10-24T20:00:00+00:00", True, "2026-10-25", "+02:00"),
    ("2026-10-25T04:59:00+00:00", True, "2026-10-25", "+02:00"),
    ("2026-10-25T05:00:00+00:00", False, "2026-10-25", "+02:00"),
])
def test_berlin_catchup_uses_local_six_am_across_dst(utc, due, target, scheduled_offset):
    plan = nightly.schedule_plan(datetime.fromisoformat(utc))
    assert plan["due"] is due
    assert plan["target_date"] == target
    assert plan["scheduled_at"].endswith(scheduled_offset)


@pytest.mark.parametrize(("provider_error", "expected_reason"), [
    ("HTTP 401 authentication failure", "AUTH_REQUIRED"),
    ("HTTP 429 quota exceeded", "PROVIDER_LIMIT"),
])
def test_settlement_retains_nonretryable_provider_block(
    tmp_path, monkeypatch, provider_error, expected_reason,
):
    from goal_analysis.providers import the_stats_primary

    class Football:
        last_usage = None

        def __init__(self, *args, **kwargs):
            pass

        def request(self, endpoint, params):
            raise RuntimeError(provider_error)

    class Ledger:
        def refresh_results(self, client):
            # PortfolioLedger intentionally redacts external errors in this shape.
            try:
                client.request("fixtures", {"id": 1})
            except RuntimeError as error:
                return {"errors": [{"fixture_id": "1", "error": type(error).__name__,
                                    "reason": "RESULT_REFRESH_FAILED"}]}
            raise AssertionError("The injected provider always fails")

    monkeypatch.setattr(the_stats_primary, "TheStatsPrimaryClient", Football)
    monkeypatch.setenv("THE_STATS_API_KEY", "local-regression-placeholder")
    clock = datetime(2026, 10, 7, 20, tzinfo=UTC)
    plan = nightly.schedule_plan(clock)
    workflow = nightly.NightlyWorkflow(tmp_path, clock=lambda: clock, ledger=Ledger())
    job = {
        "target_date": plan["target_date"], "deadline": plan["deadline"],
        "status": "PENDING", "config": {}, "usage": {},
        "stages": [{"name": name, "status": "PENDING", "attempts": 0,
                    "next_retry_at": None, "result": {}} for name in nightly.STAGES],
    }
    workflow._execute_stage(job, "settlement")
    stage = workflow._stage(job, "settlement")
    assert stage["status"] == "BLOCKED"
    assert stage["reason"] == expected_reason
    assert job["usage"]["settlement_football_calls"] == 1
