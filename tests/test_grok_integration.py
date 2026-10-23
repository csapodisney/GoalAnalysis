"""Whole-package review precedes publication; legacy advice remains read-only."""
from copy import deepcopy

from test_nightly import setup_workflow

from goal_analysis.jobs import grok_advisory


def test_nightly_grok_runs_after_archive_and_does_not_rewrite_report(tmp_path, monkeypatch):
    workflow, _clock, calls = setup_workflow(tmp_path)
    seen = []

    def analyze(root, report, **kwargs):
        assert root == tmp_path
        assert workflow.ledger.list_freezes(date=report["date"])
        assert kwargs["completion_clock"] is workflow.clock
        assert kwargs["completion_clock"]() == kwargs["now"]
        seen.append(deepcopy(report))
        return {"status": "PARTIAL", "mode": "SHADOW"}

    monkeypatch.setattr(grok_advisory, "analyze", analyze)
    outcome = workflow.run()
    assert outcome["status"] == "COMPLETED"
    assert len(seen) == 1
    saved = workflow._saved_report(seen[0]["run_id"])
    assert saved == seen[0]
    assert calls.count("generation") == calls.count("learning") == 1
    learning = workflow._stage(outcome["job"], "learning")
    assert learning["result"]["grok"] == {"status": "PARTIAL", "mode": "SHADOW"}


def test_nightly_does_not_send_repaired_package_to_grok_again(tmp_path, monkeypatch):
    workflow, _clock, _calls = setup_workflow(tmp_path)
    generate = workflow.generator

    def generated(*args):
        bundle = generate(*args)
        bundle["artifacts"]["report"]["grok_review"] = {
            "status": "COMPLETE", "mode": "ONE_BATCH_CONDITIONAL_REPAIR",
            "changed_profiles": ["gauss"], "repair_rounds": 1}
        return bundle

    def forbidden(*args, **kwargs):
        raise AssertionError("Completed prepublication review must not be sent again")

    workflow.generator = generated
    monkeypatch.setattr(grok_advisory, "analyze", forbidden)
    outcome = workflow.run()
    assert outcome["status"] == "COMPLETED"
    learning = workflow._stage(outcome["job"], "learning")
    assert learning["result"]["grok"] == {
        "status": "COMPLETE", "mode": "ONE_BATCH_CONDITIONAL_REPAIR"}


def test_portfolio_callback_cannot_change_daily(tmp_path):
    from test_daily_223_live import NOW
    from test_portfolio_flow import RecordedReviewer, environment

    from goal_analysis.jobs.portfolio import run_portfolio

    cfg, football, odds, cache, _calls, _state, _db = environment(tmp_path)
    seen = []

    def malicious(report, options):
        seen.append(deepcopy(report))
        report["tickets"][0]["legs"][0]["decimal_price"] = 999
        return report

    bundle = run_portfolio(cfg, {}, football, odds, cache, NOW.date(), RecordedReviewer(),
                           clock=lambda: NOW, package_reviewer=malicious)
    report = bundle["artifacts"]["report"]
    assert len(seen) == 1
    assert {key: report["tickets"][0][key] for key in seen[0]["tickets"][0]} == seen[0]["tickets"][0]
    assert report["grok_review"]["repair_status"] == "UNAVAILABLE_NO_CHANGE"
