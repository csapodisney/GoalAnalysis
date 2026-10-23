"""Thirty profiles share collection, publication, and the immutable daily ledger."""

from copy import deepcopy

from test_model_integration import predictor_fixture
from test_portfolio_flow import NOW, RecordedReviewer, environment

from goal_analysis.config.portfolio import MODEL_PROFILE_IDS, PROFILE_IDS, validate_settings
from goal_analysis.jobs.portfolio import run_portfolio
from goal_analysis.portfolio_dashboard import ArthurState
from goal_analysis.storage.portfolio_ledger import PortfolioLedger
from goal_analysis.team.routing import generation_plan
from goal_analysis.tipsters.catalog import TIPSTER_PROFILE_IDS


def test_thirty_profiles_one_shared_review_and_one_model_batch(tmp_path):
    config, football, odds, cache, _, _, _ = environment(tmp_path)
    reviewer, model_calls = RecordedReviewer(), []

    def predict(*args, **kwargs):
        model_calls.append(kwargs["enabled_profiles"])
        return predictor_fixture(*args, **kwargs)

    report = run_portfolio(config, {}, football, odds, cache, NOW.date(), reviewer,
                           clock=lambda: NOW, model_predictor=predict)["artifacts"]["report"]
    assert len(PROFILE_IDS) == 30
    assert len(report["recommendations"]) == 30
    assert {r["profile_id"] for r in report["recommendations"]} == set(PROFILE_IDS)
    assert len(reviewer.calls) == 1 and model_calls == [MODEL_PROFILE_IDS]
    assert report["tipster_club"]["additional_llm_calls"] == 0
    personas = [r for r in report["recommendations"] if r["profile_id"] in TIPSTER_PROFILE_IDS]
    assert len(personas) == 20
    assert all(not r["legs"] or all(leg["estimated_edge"] >= 0 for leg in r["legs"])
               for r in personas)
    contrarians = [r for r in personas if r["persona"]["selection_policy"].get("contrarian_value")]
    assert len(contrarians) == 3 and all(r["status"] == "ABSTAINED" for r in contrarians)
    assert report["tipster_club"]["published_count"] == sum(bool(r["legs"]) for r in personas)
    assert all(r["publication_scope"] == "PAPER_AUDITION" for r in personas)
    assert all(r["stake_eur"] is None and not r["playable"] for r in personas)
    assert all(r["persona"]["target_odds_band"]["min"] <= r["combined_price"] <= r["persona"]["target_odds_band"]["max"]
               and r["persona"]["min_legs"] <= len(r["legs"]) <= r["persona"]["max_legs"]
               for r in personas if r["legs"])
    assert len(report["tickets"]) <= 5 and not report["real_wager_placed"]
    assert "tipsters/catalog.py" in report["strategy_manifest"]["source_files"]

    ledger = PortfolioLedger(tmp_path / "data/arthur/ledger.sqlite3", now=lambda: NOW)
    ledger.save_run(report)
    ledger.save_run(report)
    before = deepcopy(ledger.list_runs()[0])
    frozen = ledger.freeze_run(report["run_id"])
    assert frozen["recommendation_count"] == sum(bool(r["legs"]) for r in report["recommendations"])
    assert len(ledger.list_runs()) == 1
    assert ledger.performance(mode="actual")["summary"]["tickets"] == 0
    view = ArthurState(tmp_path).snapshot(NOW.date().isoformat())
    assert view["application_version"] == "3.11"
    assert len(view["tipster_club"]["catalogue"]) == 20
    assert len(view["tipster_club"]["recommendations"]) == 20  # Includes honest abstentions.
    assert len(view["team"]["members"]) == 30
    assert ledger.list_runs()[0] == before
    old_day = ArthurState(tmp_path).snapshot("2020-01-01")
    assert not old_day["tipster_club"]["recommendations"]


def test_persona_suspension_and_owner_activation_reach_publication(tmp_path):
    config, football, odds, cache, _, _, _ = environment(tmp_path)
    active, suspended = TIPSTER_PROFILE_IDS[:2]
    requested = validate_settings({})["enabled_profiles"]
    plan = generation_plan(requested, {"members": [
        {"profile_id": active, "status": "ACTIVE"},
        {"profile_id": suspended, "status": "SUSPENDED"},
    ]})
    report = run_portfolio(config, {}, football, odds, cache, NOW.date(), RecordedReviewer(),
                           clock=lambda: NOW, model_predictor=predictor_fixture,
                           team_plan=plan)["artifacts"]["report"]
    cards = {r["profile_id"]: r for r in report["recommendations"]}
    assert cards[active]["publication_scope"] == "LIVE"
    assert cards[suspended]["publication_scope"] == "SUSPENDED"
    assert not cards[suspended]["legs"]
    assert all(not r.get("stake_eur") for r in cards.values() if r["profile_id"] in TIPSTER_PROFILE_IDS)


def test_persona_disabled_choices_do_not_return_and_models_remain_disabled(tmp_path):
    config, football, odds, cache, _, _, _ = environment(tmp_path)
    requested = ["daily223", TIPSTER_PROFILE_IDS[0]]
    calls = []

    def no_models(*args, **kwargs):
        calls.append(1)
        raise AssertionError("Disabled model must not be invoked")

    report = run_portfolio(config, {"enabled_profiles": requested}, football, odds, cache,
                           NOW.date(), RecordedReviewer(), clock=lambda: NOW,
                           model_predictor=no_models)["artifacts"]["report"]
    assert not calls
    assert {r["profile_id"] for r in report["recommendations"]} == set(requested)
    assert not report["numeric_models"]


def test_club_abstention_does_not_erase_original_published_ticket():
    identifier = TIPSTER_PROFILE_IDS[0]
    published = {"profile_id": identifier, "date": "2026-01-01", "legs": [{"fixture_id": "1"}],
                 "recommendation_id": "original", "outcome": "PENDING"}
    abstention = {"profile_id": identifier, "legs": [], "status": "ABSTAINED"}
    club = ArthurState.tipster_snapshot(validate_settings({}), [{"recommendations": [abstention]}],
                                       [published], {"members": []})
    assert club["recommendations"] == [published]
