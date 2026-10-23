"""Independent numerical recommendations through the real coordinator and ledger."""

import json
from copy import deepcopy
from datetime import timedelta

from test_portfolio_flow import NOW, RecordedReviewer, environment
from test_portfolio_ledger import Football, fixture

from goal_analysis.config.portfolio import (
    LEGACY_PROFILE_IDS,
    PROFILE_IDS,
    TIPSTER_PROFILE_IDS,
    load_settings,
    save_settings,
)
from goal_analysis.jobs.portfolio import run_portfolio, write_portfolio_bundle
from goal_analysis.storage.portfolio_ledger import PortfolioLedger
from goal_analysis.team.routing import generation_plan


def predictor_fixture(fixtures, history_records, now, **kwargs):
    return {
        profile: {
            "profile_id": profile,
            "status": "READY",
            "message": "Synthetic injected predictions for integration tests only.",
            "diagnostics": [],
            "predictions": [
                {
                    "fixture_id": item["fixture_id"],
                    "probabilities": {
                        "home": 0.75, "draw": 0.15, "away": 0.10,
                        **({"over25": 0.85, "under25": 0.15, "btts_yes": 0.7}
                           if profile == "gauss" else {}),
                    },
                    "stability": {
                        "home": 0.02, "draw": 0.02, "away": 0.02,
                        "over25": 0.02, "under25": 0.02, "btts_yes": 0.02,
                    },
                    "model_details": {"version": "synthetic-test-v1", "training_matches": 200},
                }
                for item in fixtures
            ],
        }
        for profile in kwargs["enabled_profiles"]
    }


def test_models_receive_whole_calendar_and_do_not_add_wagers_or_ai_calls(tmp_path):
    cfg, football, odds, cache, _, _, _ = environment(tmp_path)
    captured = {}

    def predictor(fixtures, history, now, **kwargs):
        captured.update(fixtures=deepcopy(fixtures), history=deepcopy(history), now=now)
        return predictor_fixture(fixtures, history, now, **kwargs)

    reviewer = RecordedReviewer()
    bundle = run_portfolio(
        cfg, {}, football, odds, cache, NOW.date(), reviewer,
        clock=lambda: NOW, model_predictor=predictor,
    )
    report = bundle["artifacts"]["report"]
    assert captured["fixtures"] == bundle["artifacts"]["fixtures"]["records"]
    assert captured["history"] == bundle["artifacts"]["history"]["records"]
    assert len(captured["fixtures"]) == 3
    numerical = {r["profile_id"]: r for r in report["recommendations"] if r["profile_id"] in {"gauss", "kepler"}}
    assert set(numerical) == {"gauss", "kepler"}
    # Shared fixtures no longer suppress independently supported model research.
    assert any(r["legs"] for r in numerical.values())
    assert all(r["status"] in {"MODEL_READY", "ABSTAINED"} for r in numerical.values())
    assert all(r["stake_eur"] is None and not r["playable"] for r in numerical.values())
    assert all(r["probability"] is None for r in numerical.values())
    assert report["tickets"] and report["diversification"]["ready_exposure"]["compliant"]
    assert not {"gauss", "kepler"} & {r["profile_id"] for r in report["tickets"]}
    assert len(reviewer.calls) == 1
    assert report["recommendation_overlap"]
    assignments = report["diversification"]["assigned"]
    assert {r["profile_id"] for r in numerical.values() if r["legs"]} <= set(assignments)
    assert all(not assignments[p]["main_ticket"] for p in numerical if p in assignments)

    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: NOW)
    ledger.save_run(report)
    frozen = ledger.freeze_run(report["run_id"])
    assert frozen["recommendation_count"] == sum(bool(r["legs"]) for r in report["recommendations"])
    records = ledger.list_recommendations()
    assert {"gauss", "kepler"} & {r["profile_id"] for r in records}
    assert ledger.performance(mode="actual")["summary"]["stake_eur"] == 0
    export_path = tmp_path / "reports" / "numeric-test"
    write_portfolio_bundle(export_path, bundle)
    assert "GAUSS" in (export_path / "report.md").read_text("utf-8")


def test_distinct_numeric_recommendation_remains_immutable_in_ledger_and_settles(tmp_path):
    cfg, football, odds, cache, _, _, _ = environment(tmp_path)
    enabled = ["daily223", "gauss"]
    roster = generation_plan(enabled, {"members": [{"profile_id": "daily223", "status": "SUSPENDED"}]})
    reviewer = RecordedReviewer()
    report = run_portfolio(
        cfg, {"enabled_profiles": enabled}, football, odds, cache, NOW.date(), reviewer,
        clock=lambda: NOW, model_predictor=predictor_fixture, team_plan=roster,
    )["artifacts"]["report"]
    gauss = next(r for r in report["recommendations"] if r["profile_id"] == "gauss")
    assert len(gauss["legs"]) == 3 and gauss["status"] == "MODEL_READY"
    assert all(l["model_probability"] in {0.75, 0.85, 0.7} for l in gauss["legs"])
    assert not reviewer.calls and not report["tickets"]
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: NOW)
    ledger.save_run(report)
    assert ledger.freeze_run(report["run_id"])["recommendation_count"] == 1
    records = ledger.list_recommendations()
    stored = next(r for r in records if r["profile_id"] == "gauss")
    original = stored["legs"][0]["model_probability"]
    gauss["legs"][0]["model_probability"] = 0.001
    assert next(r for r in ledger.list_recommendations() if r["profile_id"] == "gauss")["legs"][0]["model_probability"] == original
    assert ledger.performance(mode="actual")["summary"]["stake_eur"] == 0

    result_clock = NOW + timedelta(hours=8)
    settled = PortfolioLedger(ledger.path, now=lambda: result_clock)
    ids = {int(l["fixture_id"].split(":")[-1]) for r in records for l in r["legs"]}
    settled.refresh_results(Football({i: fixture(i, fulltime=(2, 1), halftime=(0, 0)) for i in ids}))
    performance = settled.performance(mode="recommendations")
    gauss_summary = next(r for r in performance["by_profile"] if r["profile_id"] == "gauss")
    assert gauss_summary["leg_won"] + gauss_summary["leg_lost"] == len(stored["legs"])
    assert gauss_summary["leg_hit_rate_pct"] is not None
    assert not performance["shared_failures"]  # Each fixture appears in only one recommendation.
    assert settled.performance(mode="actual")["summary"]["stake_eur"] == 0


def test_model_failure_preserves_existing_profiles_and_has_honest_status(tmp_path):
    cfg, football, odds, cache, _, _, _ = environment(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("private-key-must-not-escape")

    report = run_portfolio(
        cfg, {}, football, odds, cache, NOW.date(), RecordedReviewer(),
        clock=lambda: NOW, model_predictor=fail,
    )["artifacts"]["report"]
    assert report["tickets"] and report["diversification"]["ready_exposure"]["compliant"]
    assert len(report["recommendations"]) == len(PROFILE_IDS)
    assert "private-key-must-not-escape" not in json.dumps(report)
    for profile in ("gauss", "kepler"):
        card = next(r for r in report["recommendations"] if r["profile_id"] == profile)
        assert card["status"] == "MODEL_ERROR" and not card["legs"]


def test_disabled_models_are_not_invoked(tmp_path):
    cfg, football, odds, cache, _, _, _ = environment(tmp_path)

    def fail(*args, **kwargs):
        raise AssertionError("disabled model was invoked")

    report = run_portfolio(
        cfg, {"enabled_profiles": LEGACY_PROFILE_IDS}, football, odds, cache, NOW.date(),
        RecordedReviewer(), clock=lambda: NOW, model_predictor=fail,
    )["artifacts"]["report"]
    assert not report["numeric_models"]
    assert {r["profile_id"] for r in report["recommendations"]} == set(LEGACY_PROFILE_IDS)


def test_old_settings_gain_paper_profiles_without_reenabling_legacy_profiles(tmp_path):
    path = tmp_path / "settings.json"
    original = json.dumps({"schema_version": 3, "enabled_profiles": ["daily223", "merlin"]})
    path.write_text(original, encoding="utf-8")
    settings = load_settings(path)
    assert settings["enabled_profiles"] == ["daily223", "merlin", "gauss", "kepler", "laplace", *TIPSTER_PROFILE_IDS]
    assert path.read_text("utf-8") == original
    settings["enabled_profiles"].remove("kepler")
    save_settings(path, settings)
    assert load_settings(path)["enabled_profiles"] == ["daily223", "merlin", "gauss", "laplace", *TIPSTER_PROFILE_IDS]
