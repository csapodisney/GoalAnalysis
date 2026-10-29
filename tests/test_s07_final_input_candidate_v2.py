"""PREPARED TEST RECIPES ONLY: not executed or promoted with candidate v1.

Copy this new file into an isolated candidate-v2 test tree only after preserving
the frozen candidate-v1 outcome and promoting the two proposed source files.
"""
import inspect
import socket
import sqlite3
import subprocess
import urllib.request
from copy import deepcopy
from unittest.mock import Mock

import pytest
from test_generated_ticket_capacity import NOW as COHORT_NOW
from test_generated_ticket_capacity import card as complete_card
from test_generated_ticket_capacity import report as empty_report
from test_predecision_scenario_wire import context as compact_scenario
from test_profile_context_review import NOW, card, quote, witnessed

from goal_analysis.agents import astra_review
from goal_analysis.generated_tickets import capture_profile_decisions, finalize_generated
from goal_analysis.jobs import portfolio, profile_context_stage
from goal_analysis.jobs import profile_context_review as contract
from goal_analysis.jobs.portfolio_distribution import MODELS, SETTLEMENT
from goal_analysis.jobs.profile_scenarios import (
    build_profile_scenario_context,
    source_leg_signature,
)


@pytest.fixture(autouse=True)
def no_external_boundaries(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("TESTONLY_NO_DB_NETWORK_PROCESS_OR_MODEL_FIT"))
    for owner, name in ((sqlite3, "connect"), (socket.socket, "connect"),
                        (socket, "create_connection"), (urllib.request, "urlopen"), (subprocess, "Popen")):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr(astra_review, "_utcnow", lambda: NOW)
    yield
    forbidden.assert_not_called()


def test_late_own_alternative_is_the_exact_canonical_scenario_input_before_review():
    # Both original personas chose the same economic ticket. The late own alias
    # is the existing deduplication fallback, not a relabelled other persona.
    value = empty_report(0)
    value["settings"]["enabled_profiles"] = ["gauss", "laplace"]
    original = complete_card("gauss", 1)
    duplicate = complete_card("laplace", 1)
    # The capacity-only fixture omits settlement; the real scenario producer
    # requires the explicit regulation-time product on both cards and sources.
    for template in (original, duplicate):
        for leg in template["legs"]:
            leg["settlement"] = SETTLEMENT
    value["recommendations"] = [original, duplicate]
    capture_profile_decisions(value)
    late = complete_card("laplace", 1001)
    for leg in late["legs"]:
        leg["settlement"] = SETTLEMENT
    late["reasoning"] = ["TESTONLY late materialized own alternative"]
    options = {"laplace": [{"quality": .79, "recommendation": late}]}
    finalize_generated(value, value["settings"], options=options)
    chosen = next(row for row in value["generated_tickets"] if row["profile_id"] == "laplace")
    assert source_leg_signature(chosen) == source_leg_signature(late)
    assert source_leg_signature(chosen) != source_leg_signature(duplicate)

    source_legs = {leg["candidate_id"]: deepcopy(leg)
                   for item in value["generated_tickets"] for leg in item["legs"]}
    fixtures = {leg["fixture_id"]: deepcopy(leg) for leg in source_legs.values()}
    predictions = [{"fixture_id": identifier, "probabilities": {
        "home": .65, "draw": .20, "away": .15, "over25": .6, "under25": .4, "btts_yes": .5},
        "model_details": {"cutoff": COHORT_NOW.isoformat(), "version": "TESTONLY_EXISTING_MODEL_V1"}}
        for identifier in fixtures]
    artifacts = {"candidate_input": {"candidates": list(source_legs.values())},
        "fixtures": {"records": list(fixtures.values()), "observed_at": COHORT_NOW.isoformat()},
        "numeric_models": {name: {"status": "READY", "predictions": deepcopy(predictions)} for name in MODELS}}
    scenario = build_profile_scenario_context(value, artifacts, value["generated_tickets"], as_of=COHORT_NOW)
    assessment = next(row for row in scenario["profiles"] if row["profile_id"] == "laplace")
    assert assessment["source_leg_signature"] == source_leg_signature(chosen)
    assert assessment["models"]["gauss"]["scenario_model_full_card_probability"] == pytest.approx(.65 ** 2)
    contract.attach_decision_inputs(value, options, {}, scenario)
    assert chosen["decision_reasoning"]["scenario_risk_facts"] == assessment["risk_facts"]
    assert late["decision_reasoning"]["scenario_context_sha256"] == assessment["context_sha256"]
    assert late["reasoning"][0] == "TESTONLY late materialized own alternative"
    assert any("feltételes, nem kalibrált" in line for line in late["reasoning"])
    assert any("42.25%" in line for line in late["reasoning"])
    assert len([line for line in late["reasoning"] if line.startswith("[S07] GAUSS:")]) == 1
    visible_before=deepcopy(late["reasoning"])
    math_before=deepcopy(late["legs"])
    contract.attach_decision_inputs(value, options, {}, scenario)
    assert late["reasoning"] == visible_before  # No repeated or stale rendered lines.
    assert late["legs"] == math_before

    # A known-leg event is never rendered as the complete ticket's probability.
    partial=deepcopy(scenario)
    partial_assessment=next(row for row in partial["profiles"] if row["profile_id"] == "laplace")
    for model in partial_assessment["models"].values():
        model.update(known_leg_count=1,unknown_leg_count=1,
                     known_leg_event_probability=.65,scenario_model_full_card_probability=None)
    contract.attach_decision_inputs(value, options, {}, partial)
    assert all("teljes szelvény esélye UNKNOWN" in line for line in late["reasoning"]
               if line.startswith(("[S07] GAUSS:","[S07] KEPLER:","[S07] LAPLACE:")))
    assert not any("42.25%" in line or "65.00%" in line for line in late["reasoning"])
    assert late["reasoning"][0] == "TESTONLY late materialized own alternative"
    assert late["legs"] == math_before
    assert next(iter(contract.cards(value, options)))[0] == ("generated_tickets", 0)
    legacy = {**value, "settings": {"operating_contract_version": 1}}
    assert all(location[0] != "generated_tickets" for location, _ in contract.cards(legacy, options))

    # Guard the specific public-caller ordering that caused the missed alias;
    # the numerical/source-bound assertions above exercise the real kernels.
    source = inspect.getsource(portfolio.run_portfolio)
    base = source.index("    _base_diversity_recommendations(")
    cohort = source.index("        finalize_generated(", base)
    scenarios = source.index("        scenarios=build_profile_scenario_context(", cohort)
    final_review = source.index("profile_context_stage.execute(", scenarios)
    assert base < cohort < scenarios < final_review


def test_old_exact_context_review_cannot_complete_new_scenario_after_supplement_failure():
    source = quote()
    artifacts, prepared = witnessed([source])  # Existing mocked exact wire helper.
    old_covered, issues = contract.bindings(artifacts, prepared, [source])
    assert not issues and source["candidate_id"] in old_covered
    assert "profile_scenario_context_sha256" not in old_covered[source["candidate_id"]]
    choice = card([source])
    choice.update(status="READY", playable=True)
    value = {"settings": {"operating_contract_version": 2}, "date": NOW.date().isoformat(),
        "generated_tickets": [deepcopy(choice)], "recommendations": [deepcopy(choice)],
        "profile_predecision_scenarios": {"reviewer_context_by_source_candidate_id": {
            source["candidate_id"]: [compact_scenario()]}}}
    options = {"gauss": [{"quality": .79, "recommendation": deepcopy(choice)}]}
    old_review = deepcopy(artifacts["candidate_review_response_batches"][0]["response"])
    requests = []

    class FailedOptionalReviewer:
        def review(self, packets, target_date, feedback=None):
            requests.append(deepcopy(packets))
            return {"status": "UNAVAILABLE", "reviews": [], "usage": {"requests": 0}}

    result, pools, _ = profile_context_stage.execute(value, options, artifacts, prepared, [source],
        old_review, mode="ACTIVE", reviewer=FailedOptionalReviewer(), target_date=value["date"],
        feedback=None, maximum=30, remaining_rounds=1, clock=lambda: NOW,
        merge=portfolio._merge_astra_reviews)
    assert len(requests) == 1
    assert requests[0][0]["profile_scenario_context"] == [compact_scenario()]
    for item in (result["generated_tickets"][0], result["recommendations"][0], pools["gauss"][0]["recommendation"]):
        assert item["status"] == "DRAFT" and item["playable"] is False
        assert item["profile_context_review"]["status"] == "PENDING"
        assert item["profile_context_review"]["legs"][0]["reason"] == "NO_EXACT_CURRENT_PROFILE_SCENARIO_REVIEW"
        assert item["legs"] == choice["legs"]
    assert pools["gauss"][0]["quality"] == .79
    assert value["generated_tickets"][0]["status"] == "READY"  # Input unchanged.
