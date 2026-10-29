"""TESTONLY current-contract scenario inputs and optional review integration."""
import socket
import sqlite3
from copy import deepcopy
from unittest.mock import Mock

import pytest
from test_profile_context_review import NOW, card, prepared, quote

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import portfolio_distribution as distribution
from goal_analysis.jobs import profile_context_review as job
from goal_analysis.jobs.profile_scenarios import build_profile_scenario_context


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("TESTONLY_NO_DB_OR_NETWORK"))
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    yield
    forbidden.assert_not_called()


def source(identifier="quote-1", fixture="the_stats_api:mt_123"):
    return {**quote(identifier, fixture), "settlement": distribution.SETTLEMENT}


def own_card(sources, profile="gauss"):
    value = card(sources, profile)
    value.update(status="MODEL_READY", method_version="TESTONLY_METHOD_1", support_score=.81)
    for leg, original in zip(value["legs"], sources, strict=True):
        leg["candidate_id"] = original["candidate_id"]
    return value


def scenarios(report, choices, sources):
    predictions = [{"fixture_id": value["fixture_id"], "probabilities": {
        "home": .5, "draw": .25, "away": .25, "over25": .6, "under25": .4, "btts_yes": .5},
        "model_details": {"cutoff": NOW.isoformat(), "version": "TESTONLY_MODEL_1"}}
        for value in sources]
    artifacts = {"candidate_input": {"candidates": deepcopy(sources)},
        "fixtures": {"observed_at": NOW.isoformat(), "records": [{key: value[key] for key in (
            "fixture_id", "kickoff", "provider_fixture_id", "provider_league_id", "provider_season_id",
            "provider_home_team_id", "provider_away_team_id")} for value in sources]},
        "numeric_models": {name: {"status": "READY", "predictions": deepcopy(predictions)}
                           for name in distribution.MODELS}}
    return build_profile_scenario_context(report, artifacts, choices, as_of=NOW)


def test_current_captured_choice_is_reviewed_with_actual_exact_scenario_packet():
    sources = [source()]
    choice = own_card(sources)
    report = {"settings": {"operating_contract_version": 2},
              "profile_decision_sources": [{"card": choice}]}
    scenario = scenarios(report, [choice], sources)
    report["profile_predecision_scenarios"] = scenario
    before = deepcopy(report)
    planned = job.plan(report, {}, sources, prepared(sources), {}, now=NOW, maximum=30, remaining_rounds=1)
    packet = planned["batches"][0][0]
    assert packet["profile_scenario_context"] == scenario["reviewer_context_by_source_candidate_id"][sources[0]["candidate_id"]]
    assert "probability=0.6" in packet["profile_scenario_context"][0]["risk_facts"][0]
    assert report == before
    assert list(job.cards({**report, "settings": {"operating_contract_version": 1}}, {})) == []


def test_real_scenario_facts_reach_captured_decision_reasoning_without_math_or_source_edits():
    sources = [source()]
    choice = own_card(sources)
    choice["reasoning"] = ["TESTONLY saját, változatlan stratégiai indok."]
    report = {"settings": {"operating_contract_version": 2},
              "profile_decision_sources": [{"card": choice}]}
    scenario = scenarios(report, [choice], sources)
    before = deepcopy(choice)
    job.attach_decision_inputs(report, {}, prepared(sources), scenario)
    reasoning = choice["decision_reasoning"]
    assert reasoning["scenario_risk_facts"] == scenario["profiles"][0]["risk_facts"]
    assert reasoning["scenario_context_sha256"] == scenario["profiles"][0]["context_sha256"]
    assert reasoning["phase"] == "BEFORE_FINAL_PROFILE_DECISION"
    assert reasoning["numeric_probability_or_rank_changed"] is False
    assert {key: value for key, value in choice.items() if key not in {"decision_reasoning", "reasoning"}} == {
        key: value for key, value in before.items() if key != "reasoning"}
    assert choice["reasoning"][:len(before["reasoning"])] == before["reasoning"]
    assert any("feltételes, nem kalibrált" in line and "60.00%" in line for line in choice["reasoning"])
    # A display-only wrapper changes the full card hash, not its economic legs.
    choice["display_label"] = "TESTONLY same-economic wrapper"
    job.attach_decision_inputs(report, {}, prepared(sources), scenario)
    assert choice["decision_reasoning"]["scenario_risk_facts"] == scenario["profiles"][0]["risk_facts"]
    assert choice["legs"] == before["legs"]


def test_optional_context_unknown_is_local_to_its_profile():
    sources = [source(), source("quote-2", "the_stats_api:mt_124")]
    choices = [own_card(sources[:1]), own_card(sources[1:], "kepler")]
    report = {"settings": {"operating_contract_version": 2}, "recommendations": choices}
    scenario = scenarios(report, choices, sources)
    job.attach_decision_inputs(report, {}, prepared(sources[:1]), scenario)
    first, second = [value["decision_reasoning"] for value in choices]
    assert not any("OPTIONAL_CONTEXT_UNAVAILABLE" in reason for reason in first["uncertainties"])
    assert any("mt_124:OPTIONAL_CONTEXT_UNAVAILABLE" in reason for reason in second["uncertainties"])
    assert first["scenario_risk_facts"] and second["scenario_risk_facts"]
    assert first["missing_context_is_neutral"] is second["missing_context_is_neutral"] is False


def test_active_missing_optional_review_keeps_current_draft_options_but_legacy_drops():
    sources = [source()]
    choice = own_card(sources)
    before = deepcopy(choice)
    for version in (1, 2):
        report = {"settings": {"operating_contract_version": version}, "recommendations": [deepcopy(choice)]}
        options = {"gauss": [{"quality": .79, "recommendation": deepcopy(choice)}]}
        coverage = job.audit(report, options, sources, prepared(sources), {}, now=NOW)
        gated, pools = job.apply(report, options, coverage, {}, mode="ACTIVE")
        assert gated["recommendations"][0]["status"] == "DRAFT"
        assert gated["recommendations"][0]["legs"] == choice["legs"]
        if version == 2:
            assert pools["gauss"][0]["recommendation"]["status"] == "DRAFT"
            assert pools["gauss"][0]["quality"] == .79
            assert pools["gauss"][0]["recommendation"]["legs"] == choice["legs"]
        else:
            assert pools["gauss"] == []
        assert options["gauss"][0]["recommendation"]["status"] == "MODEL_READY"
    assert choice == before


def test_different_source_scenario_is_not_attached_to_another_choice():
    first, second = source(), source("quote-2", "the_stats_api:mt_124")
    original = own_card([first])
    changed = own_card([second])
    report = {"settings": {"operating_contract_version": 2}, "recommendations": [changed]}
    scenario = scenarios(report, [original], [first])
    job.attach_decision_inputs(report, {}, prepared([second]), scenario)
    assert changed["decision_reasoning"]["scenario_risk_facts"] == []
    assert changed["decision_reasoning"]["scenario_context_sha256"] is None


def test_shorter_option_cannot_borrow_longer_cards_full_scenario_fact():
    sources = [source(), source("quote-2", "the_stats_api:mt_124")]
    original = own_card(sources)
    shorter = own_card(sources[:1])
    changed_price = deepcopy(original)
    changed_price["legs"][0]["decimal_price"] += .1
    report = {"settings": {"operating_contract_version": 2}}
    scenario = scenarios(report, [original], sources)
    assert scenario["profiles"][0]["models"]["gauss"]["scenario_model_full_card_probability"] == pytest.approx(.36)
    for different in (shorter, changed_price):
        assert canonical_sha256(different) != scenario["profiles"][0]["source_card_sha256"]
        report["recommendations"] = [different]
        job.attach_decision_inputs(report, {}, prepared(sources), scenario)
        assert different["decision_reasoning"]["scenario_risk_facts"] == []
        assert different["decision_reasoning"]["scenario_context_sha256"] is None
