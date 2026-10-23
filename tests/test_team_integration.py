"""Team authority reaches real publication without changing wager history."""

from copy import deepcopy
from datetime import timedelta

import pytest
from test_model_integration import predictor_fixture
from test_numeric_models import NOW as MODEL_NOW
from test_numeric_models import fixtures, sample_history
from test_portfolio_flow import NOW, RecordedReviewer, environment

from goal_analysis.config.portfolio import (
    DEFAULT_PROBATION_PROFILE_IDS,
    PROFILE_IDS,
    validate_settings,
)
from goal_analysis.jobs.portfolio import run_portfolio
from goal_analysis.models import predict_models
from goal_analysis.storage.portfolio_ledger import PortfolioLedger
from goal_analysis.team.routing import generation_plan, validate_plan


def plan(states):
    return generation_plan(PROFILE_IDS, {"members": [
        {"profile_id": profile, "status": status} for profile, status in states.items()
    ]})


def test_suspended_daily_is_really_excluded_not_forced_back(tmp_path):
    cfg, football, odds, cache, _, _, _ = environment(tmp_path)
    report = run_portfolio(cfg, {}, football, odds, cache, NOW.date(), RecordedReviewer(),
                           clock=lambda: NOW, team_plan=plan({"daily223": "SUSPENDED"})) ["artifacts"]["report"]
    assert "daily223" not in {r["profile_id"] for r in report["tickets"]}
    assert "daily223" not in {r["profile_id"] for r in report["preview_tickets"]}
    assert not next(r for r in report["recommendations"] if r["profile_id"] == "daily223")["legs"]
    assert any(r["legs"] for r in report["recommendations"] if r["profile_id"] != "daily223")
    assert not report["real_wager_placed"]
    assert report["settings"]["enabled_profiles"] == PROFILE_IDS


def test_no_members_no_provider_calls_or_generic_fallback(tmp_path):
    cfg, football, odds, cache, calls, _, _ = environment(tmp_path)
    before = deepcopy(calls)
    report = run_portfolio(cfg, {"strictness": 0}, football, odds, cache, NOW.date(),
                           clock=lambda: NOW, team_plan=plan({p: "SUSPENDED" for p in PROFILE_IDS}))["artifacts"]["report"]
    assert calls == before
    assert report["construction_status"] == "TEAM_PAUSED"
    assert not report["tickets"] and not report["preview_tickets"]
    assert all(r["status"] == "SUSPENDED" and not r["legs"] for r in report["recommendations"])


def test_probation_persisted_and_frozen_but_never_live_wager(tmp_path):
    cfg, football, odds, cache, _, _, _ = environment(tmp_path)
    roster = plan({"daily223": "PROBATION", "laplace": "PROBATION"})
    # This fixture tests audition persistence, independently of the default 10x band.
    report = run_portfolio(cfg, {"target_min_odds": 2}, football, odds, cache, NOW.date(), RecordedReviewer(),
                           clock=lambda: NOW, model_predictor=predictor_fixture,
                           team_plan=roster)["artifacts"]["report"]
    papers = [r for r in report["recommendations"] if r["publication_scope"] == "PAPER_AUDITION"]
    assert {r["profile_id"] for r in papers} == {"daily223", *DEFAULT_PROBATION_PROFILE_IDS}
    assert all(not r["playable"] and r["stake_eur"] is None for r in papers)
    assert next(r for r in papers if r["profile_id"] == "daily223")["legs"]
    laplace = next(r for r in papers if r["profile_id"] == "laplace")
    assert laplace["legs"] and laplace["status"] == "MODEL_READY"
    from goal_analysis.engine.diversification import ticket_signature
    signatures = [ticket_signature(r) for r in report["recommendations"] if r["legs"]]
    assert len(signatures) == len(set(signatures))
    assert not {"daily223", "laplace"} & {r["profile_id"] for r in report["tickets"]}
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: NOW)
    ledger.save_run(report)
    frozen = ledger.freeze_run(report["run_id"])
    assert frozen["recommendation_count"] == len([r for r in report["recommendations"] if r["legs"]])
    assert any(r["publication_scope"] == "PAPER_AUDITION" for r in ledger.list_recommendations())
    assert ledger.performance(mode="actual")["summary"]["tickets"] == 0


def test_tampered_roster_fails_before_provider_calls(tmp_path):
    cfg, football, odds, cache, calls, _, _ = environment(tmp_path)
    roster = plan({"daily223": "SUSPENDED"})
    roster["live_profiles"].append("daily223")
    before = deepcopy(calls)
    with pytest.raises(ValueError, match="hash"):
        run_portfolio(cfg, {}, football, odds, cache, NOW.date(), clock=lambda: NOW, team_plan=roster)
    assert calls == before
    assert validate_plan(plan({}), PROFILE_IDS)["paper_profiles"] == list(DEFAULT_PROBATION_PROFILE_IDS)


def test_explicitly_disabled_profile_not_reenabled_by_team():
    settings = validate_settings({"enabled_profiles": ["daily223"]})
    roster = generation_plan(settings["enabled_profiles"], {"members": [
        {"profile_id": "laplace", "status": "ACTIVE"},
    ]})
    assert roster["live_profiles"] == ["daily223"] and not roster["paper_profiles"]


def test_unavailable_team_never_resets_to_active():
    with pytest.raises(ValueError, match="unavailable"):
        generation_plan(PROFILE_IDS, {"status": "UNAVAILABLE", "members": []})


def test_probation_only_numeric_does_not_spend_a_review(tmp_path):
    cfg, football, odds, cache, _, _, _ = environment(tmp_path)
    reviewer = RecordedReviewer()
    roster = plan({**{p: "SUSPENDED" for p in PROFILE_IDS}, "laplace": "PROBATION"})
    report = run_portfolio(cfg, {}, football, odds, cache, NOW.date(), reviewer,
                           clock=lambda: NOW, model_predictor=predictor_fixture,
                           team_plan=roster)["artifacts"]["report"]
    assert not reviewer.calls and not report["tickets"]
    assert any(r["legs"] and r["publication_scope"] == "PAPER_AUDITION" for r in report["recommendations"])


def test_team_snapshot_read_only_and_corruption_disclosed(tmp_path):
    from goal_analysis.portfolio_dashboard import ArthurState

    state = ArthurState(tmp_path)
    initial = state.snapshot(NOW.date().isoformat())
    assert len(initial["team"]["members"]) == len(PROFILE_IDS)
    assert not (tmp_path / "data/team/team.sqlite3").exists()
    assert not (tmp_path / "data/team/scouting.sqlite3").exists()
    state.update_team_status({"profile_id": "daily223", "status": "SUSPENDED", "reason": "Tulajdonosi teszt"})
    updated = state.snapshot(NOW.date().isoformat())
    assert next(m for m in updated["team"]["members"] if m["profile_id"] == "daily223")["status"] == "SUSPENDED"
    assert state.ledger.performance(mode="actual")["summary"]["tickets"] == 0
    (tmp_path / "data/team/team.sqlite3").write_bytes(b"corrupt synthetic team data")
    assert state.snapshot()["team"]["status"] == "UNAVAILABLE"


def test_team_http_auth_and_mutations(tmp_path):
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from goal_analysis.portfolio_dashboard import ArthurState, make_handler

    state = ArthurState(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, "test-team-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/team/status"
    payload = {"profile_id": "daily223", "status": "SUSPENDED", "reason": "HTTP teszt"}

    def request(data, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Arthur-Token"] = token
        return urllib.request.urlopen(urllib.request.Request(
            url, data=json.dumps(data).encode(), headers=headers, method="POST"), timeout=5)

    try:
        with pytest.raises(urllib.error.HTTPError) as denied:
            request(payload)
        assert denied.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as invalid:
            request({**payload, "profile_id": "download-and-run"}, "test-team-token")
        assert invalid.value.code == 400
        with request(payload, "test-team-token") as response:
            result = json.load(response)
        assert next(m for m in result["members"] if m["profile_id"] == "daily223")["status"] == "SUSPENDED"
        with request({**payload, "status": "PROBATION"}, "test-team-token") as response:
            restored = json.load(response)
        assert restored["events"][-1]["to_status"] == "PROBATION"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_laplace_real_library_fit_and_temporal_boundary():
    history = sample_history()
    first = predict_models(fixtures(), history, MODEL_NOW, enabled_profiles=("laplace",))["laplace"]
    assert first["status"] == "READY" and len(first["predictions"]) == 2
    prediction = first["predictions"][0]
    assert sum(prediction["probabilities"][k] for k in ("home", "draw", "away")) == pytest.approx(1)
    assert prediction["model_details"]["ensemble"] == "none_independent_poisson_baseline"
    future = dict(history[0], fixture_id="999999", kickoff=(MODEL_NOW + timedelta(days=1)).isoformat())
    second = predict_models(fixtures(), history + [future], MODEL_NOW, enabled_profiles=("laplace",))["laplace"]
    assert second["predictions"] == first["predictions"]


def test_promoted_laplace_has_live_authority_but_cannot_bypass_diversity(tmp_path):
    cfg, football, odds, cache, _, _, _ = environment(tmp_path)
    report = run_portfolio(cfg, {}, football, odds, cache, NOW.date(), clock=lambda: NOW,
                           model_predictor=predictor_fixture,
                           team_plan=plan({"laplace": "ACTIVE"}))["artifacts"]["report"]
    promoted = next(r for r in report["recommendations"] if r["profile_id"] == "laplace")
    assert promoted["publication_scope"] == "LIVE" and promoted["legs"]
    assert promoted["status"] == "MODEL_READY"
    assert "laplace" in report["diversification"]["assigned"]
    assert not report["diversification"]["assigned"]["laplace"]["main_ticket"]
    assert promoted["stake_eur"] is None and not report["real_wager_placed"]
    assert len(report["tickets"]) <= 5


def test_planned_suspension_is_not_a_nightly_generation_failure(tmp_path):
    from test_nightly import setup_workflow

    from goal_analysis.team.manager import TeamManager

    workflow, clock, _ = setup_workflow(tmp_path)
    TeamManager(tmp_path, clock=lambda: clock[0]).set_status("daily223", "SUSPENDED", "Synthetic test")
    outcome = workflow.run()
    generation = workflow._stage(outcome["job"], "generation")
    assert generation["status"] == "SUCCESS"
    assert generation["result"]["profile_count"] == len(PROFILE_IDS) - 1
    assert generation["result"]["suspended_profiles"] == ["daily223"]
