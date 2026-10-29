"""TESTONLY candidate scenarios through the real existing source-bound producer."""
import socket
import sqlite3
from copy import deepcopy
from datetime import datetime
from unittest.mock import Mock

import pytest

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import portfolio_distribution as existing
from goal_analysis.jobs import profile_scenarios as module

NOW = "2026-10-28T07:00:00+00:00"
KICKOFF = "2026-10-28T18:00:00+00:00"


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("TESTONLY_NO_DB_OR_NETWORK"))
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    yield
    forbidden.assert_not_called()


def leg(identifier="quote-f", fixture="f", market="totals_2_5", selection="over"):
    return {"candidate_id": identifier, "fixture_id": fixture, "kickoff": KICKOFF,
            "market_key": market, "selection_key": selection, "period": "FULL_TIME",
            "settlement": existing.SETTLEMENT, "bookmaker": "TESTONLY_BOOK", "decimal_price": 2.0,
            "quote_observed_at": NOW, "quote_provider": "TESTONLY_PROVIDER"}


def card(profile="one", legs=None, status="DRAFT"):
    return {"profile_id": profile, "recommendation_id": "TESTONLY:" + profile,
            "method_version": "TESTONLY_OWN_METHOD_1", "status": status,
            "legs": [leg()] if legs is None else legs, "publication_scope": "PAPER_AUDITION"}


def prediction(fixture="f", over=.6):
    return {"fixture_id": fixture, "probabilities": {"home": .5, "draw": .25, "away": .25,
            "over25": over, "under25": 1 - over, "btts_yes": .5},
            "model_details": {"cutoff": NOW, "version": "TESTONLY_MODEL_1"}}


def inputs(cards, predictions=None):
    report = {"run_id": "TESTONLY_PREDECISION", "date": "2026-10-28",
              "observed_at": NOW, "recommendations": deepcopy(cards), "tickets": []}
    candidates, fixtures = {}, {}
    for row in cards:
        for value in row["legs"]:
            candidates[value["candidate_id"]] = deepcopy(value)
            fixtures[value["fixture_id"]] = {"fixture_id": value["fixture_id"], "kickoff": KICKOFF}
    models = [prediction()] if predictions is None else predictions
    artifacts = {"candidate_input": {"candidates": list(candidates.values())},
                 "fixtures": {"records": list(fixtures.values()), "observed_at": NOW},
                 "numeric_models": {name: {"status": "READY", "predictions": deepcopy(models),
                     "training_datasets": {"manifests": [{"dataset_id": "TESTONLY_DATASET", "as_of": NOW}]}}
                     for name in existing.MODELS}}
    return report, artifacts


@pytest.mark.parametrize('valid_alias',[True,False])
def test_real_persona_alias_is_projected_only_with_exact_constructor_proof(valid_alias):
    source=leg()
    profile='tipster_bastya_bence'
    alias=profile+'-'+canonical_sha256([profile,source['candidate_id']])[:20]
    selected={**source,'candidate_id':alias if valid_alias else alias+'forged',
              'source_candidate_id':source['candidate_id'],'quote_candidate_id':source['candidate_id']}
    cards=[card(profile,legs=[selected])]
    report,artifacts=inputs(cards)
    artifacts['candidate_input']['candidates']=[source]
    before=deepcopy(cards)
    result=module.build_profile_scenario_context(report,artifacts,cards,as_of=datetime.fromisoformat(NOW))
    assessment=result['profiles'][0]
    assert assessment['models']['gauss']['known_leg_count']==(1 if valid_alias else 0)
    assert bool(assessment['source_alias_projection']) is valid_alias
    assert cards==before


def evaluate(cards, report, artifacts):
    before = deepcopy((cards, report, artifacts))
    result = module.build_profile_scenario_context(report, artifacts, cards, as_of=datetime.fromisoformat(NOW))
    assert (cards, report, artifacts) == before
    assert result["sha256"] == canonical_sha256({key: value for key, value in result.items() if key != "sha256"})
    return result


def test_actual_model_value_changes_assessment_and_actual_review_context():
    cards = [card()]
    report, artifacts = inputs(cards)
    first = evaluate(cards, report, artifacts)
    artifacts["numeric_models"]["gauss"]["predictions"][0]["probabilities"].update(over25=.8, under25=.2)
    second = evaluate(cards, report, artifacts)
    a, b = first["profiles"][0], second["profiles"][0]
    assert a["models"]["gauss"]["scenario_model_full_card_probability"] == pytest.approx(.6)
    assert b["models"]["gauss"]["scenario_model_full_card_probability"] == pytest.approx(.8)
    assert a["risk_facts"] != b["risk_facts"]
    assert first["reviewer_context_by_source_candidate_id"]["quote-f"] != second["reviewer_context_by_source_candidate_id"]["quote-f"]
    assert a["source_card_sha256"] == canonical_sha256(cards[0]) == b["source_card_sha256"]
    assert first["phase"] == "BEFORE_FINAL_PROFILE_DECISION"


def test_missing_local_family_preserves_other_profile_and_unknown_full_card():
    cards = [card("covered"), card("partial", [leg(), leg("quote-g", "g", "totals_3_5")])]
    report, artifacts = inputs(cards, [prediction(), prediction("g")])
    result = evaluate(cards, report, artifacts)
    covered, partial = result["profiles"]
    assert covered["status"] == "AVAILABLE"
    model = partial["models"]["gauss"]
    assert (model["known_leg_count"], model["unknown_leg_count"]) == (1, 1)
    assert model["known_leg_event_probability"] == pytest.approx(.6)
    assert model["scenario_model_full_card_probability"] is None
    assert model["scenario_model_full_card_probability_bounds"] == {"lower": 0.0, "upper": pytest.approx(.6)}
    assert partial["status"] == "PARTIAL" and "UNKNOWN" in partial["risk_facts"][0]


def test_unsupported_leg_never_gets_inverse_odds_or_neutral_probability():
    cards = [card(legs=[leg(market="totals_3_5")])]
    report, artifacts = inputs(cards)
    measured = evaluate(cards, report, artifacts)["profiles"][0]
    assert measured["status"] == "UNKNOWN"
    assert all(model["known_leg_event_probability"] is None
               and model["scenario_model_full_card_probability"] is None for model in measured["models"].values())


def test_ready_is_only_a_labelled_hypothesis_original_and_shadow_phase_unchanged():
    cards = [card(status="READY")]
    report, artifacts = inputs(cards)
    old = existing.build_portfolio_distribution(report, artifacts)
    result = evaluate(cards, report, artifacts)
    assessment = result["profiles"][0]
    assert cards[0]["status"] == assessment["original_status"] == "READY"
    assert assessment["source_card_sha256"] == canonical_sha256(cards[0])
    assert assessment["hypothesis_card_sha256"] != assessment["source_card_sha256"]
    assert assessment["hypothesis_role"] == "PREDECISION_CANDIDATE_OPTION"
    assert assessment["hypothesis_status"] == "TARGET_PROPOSAL"
    identity = result["existing_kernel_result"]["models"]["gauss"]["cards"][0]["source_card_identity"]
    assert identity["status"] == "TARGET_PROPOSAL"
    assert result["existing_kernel_result"]["mode"] == "SHADOW"
    assert result["existing_kernel_result"]["decision_effect"] == "NONE"
    assert existing.build_portfolio_distribution(report, artifacts) == old
    assert "INDEPENDENCE_ACROSS_DISTINCT_FIXTURES_CONDITIONAL_ON_A_FIXED_MODEL_SCENARIO" in result["assumptions"]


def test_source_cutoffs_refs_and_future_model_stay_honest_and_local():
    cards = [card()]
    report, artifacts = inputs(cards)
    future = "2026-10-28T08:00:00+00:00"
    artifacts["numeric_models"]["gauss"]["predictions"][0]["model_details"]["cutoff"] = future
    result = evaluate(cards, report, artifacts)
    models = result["profiles"][0]["models"]
    assert models["gauss"]["scenario_model_full_card_probability"] is None
    assert models["laplace"]["scenario_model_full_card_probability"] == pytest.approx(.6)
    assert models["gauss"]["source_refs"]["predictions"][0]["model_cutoff"] == future
    assert models["laplace"]["source_refs"]["dataset_manifest_refs"] == [{"dataset_id": "TESTONLY_DATASET", "as_of": NOW}]
    assert result["profiles"][0]["status"] == "PARTIAL"


def test_shared_opposite_profiles_use_real_existing_joint_outcomes():
    cards = [card("over"), card("under", [leg("quote-under", selection="under")])]
    report, artifacts = inputs(cards)
    result = evaluate(cards, report, artifacts)
    joint = result["existing_kernel_result"]["models"]["gauss"]["joint_known_leg_events"]
    assert joint["satisfied_known_leg_event_count_distribution"]["probability_mass"] == pytest.approx([0, 1, 0])
    assert len(result["profiles"]) == 2


def test_full_calendar_remains_referenced_without_hitting_subset_research_cap():
    cards = [card()]
    report, artifacts = inputs(cards)
    artifacts["fixtures"]["records"].extend({"fixture_id": f"extra-{i}", "kickoff": KICKOFF} for i in range(200))
    result = evaluate(cards, report, artifacts)
    assert result["source_refs"]["calendar_fixture_count"] == 201
    assert result["source_refs"]["calendar_sha256"] == canonical_sha256(artifacts["fixtures"])
    assert result["profiles"][0]["status"] == "AVAILABLE"


def test_ambiguous_source_quote_stays_unknown_not_silently_deduplicated():
    cards = [card()]
    report, artifacts = inputs(cards)
    artifacts["candidate_input"]["candidates"] *= 2
    result = evaluate(cards, report, artifacts)
    assert result["profiles"][0]["status"] == "UNKNOWN"
    assert result["reviewer_context_by_source_candidate_id"] == {}


def test_scoping_does_not_hide_a_conflicting_virtual_candidate_alias():
    cards = [card()]
    report, artifacts = inputs(cards)
    source = deepcopy(cards[0]["legs"][0])
    virtual = "one-" + canonical_sha256(["one", "f", "totals_2_5", "over", "TESTONLY_BOOK"])[:24]
    cards[0]["legs"][0].update(candidate_id=virtual, source_candidate_id="quote-f", quote_candidate_id="quote-f")
    artifacts["candidate_input"]["candidates"].append({**source, "candidate_id": virtual})
    result = evaluate(cards, report, artifacts)
    assert result["profiles"][0]["status"] == "UNKNOWN"
    assert any("CONFLICTING_SUPPLIED_CANDIDATE_IDENTIFIERS" in reason
               for reason in result["profiles"][0]["uncertainties"])


def test_disabled_v1_does_no_computation_or_change(monkeypatch):
    producer = Mock(side_effect=AssertionError("DISABLED_MUST_NOT_COMPUTE"))
    monkeypatch.setattr(existing, "build_portfolio_distribution", producer)
    report, artifacts = inputs([card()])
    before = deepcopy((report, artifacts))
    result = module.build_profile_scenario_context(report, artifacts, None, as_of=None, enabled=False)
    assert result["status"] == "DISABLED" and result["decision_effect"] == "NONE"
    assert result["profiles"] == [] and result["reviewer_context_by_source_candidate_id"] == {}
    assert (report, artifacts) == before
    producer.assert_not_called()
