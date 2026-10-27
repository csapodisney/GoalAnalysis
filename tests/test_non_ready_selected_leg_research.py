"""Public APP non-READY research controls; TESTONLY, no model fit or I/O."""
import socket
import sqlite3
import subprocess
import urllib.request
from copy import deepcopy
from unittest.mock import Mock

import pytest

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import portfolio_distribution as module

NOW = "2026-10-27T03:00:00+00:00"
KICKOFF = "2026-10-28T18:00:00+00:00"


@pytest.fixture(autouse=True)
def forbidden_io(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("NO_DB_MODEL_PROVIDER_NETWORK_OR_PROCESS"))
    for parent, name in ((sqlite3, "connect"), (socket, "create_connection"),
                         (socket.socket, "connect"), (urllib.request, "urlopen"), (subprocess, "Popen")):
        monkeypatch.setattr(parent, name, forbidden)
    yield forbidden
    forbidden.assert_not_called()


def leg(name="a", fixture="f", family="totals_2_5", selection="over"):
    return {"candidate_id": name, "fixture_id": fixture, "kickoff": KICKOFF,
            "market_key": family, "selection_key": selection, "period": "FULL_TIME", "settlement": module.SETTLEMENT,
            "bookmaker": "TESTONLY_BOOK", "decimal_price": 2.0, "quote_provider": "TESTONLY_PROVIDER",
            "quote_source_id": "TESTONLY_SOURCE:" + name, "quote_source_sha256": "a" * 64,
            "quote_observed_at": NOW, "quoted_at": None}


def prediction(fixture="f", **values):
    return {"fixture_id": fixture, "probabilities": {"home": .6, "draw": .25, "away": .15,
        "over25": .6, "under25": .4, "btts_yes": .55, **values},
        "model_details": {"cutoff": NOW, "version": "TESTONLY_MODEL_V1"}}


def card(name="one", legs=None, status="DRAFT", **fields):
    return {"recommendation_id": "TESTONLY:" + name, "profile_id": name, "status": status,
            "legs": [leg()] if legs is None else legs, **fields}


def inputs(cards, predictions=None):
    report = {"run_id": "TESTONLY_NO_PIT_OR_ACTUAL_DAILY", "finished_at": NOW,
              "recommendations": cards, "tickets": []}
    candidates, fixtures = {}, {}
    for row in cards:
        for value in row.get("legs", []) or []:
            candidates[value["candidate_id"]] = deepcopy(value)
            fixtures[value["fixture_id"]] = {"fixture_id": value["fixture_id"], "kickoff": value["kickoff"]}
    models = [prediction()] if predictions is None else predictions
    return report, {"candidate_input": {"candidates": list(candidates.values())}, "fixtures": list(fixtures.values()),
                    "numeric_models": {name: {"status": "READY", "predictions": deepcopy(models)} for name in module.MODELS}}


def evaluate(report, artifacts, name="gauss"):
    before = deepcopy((report, artifacts))
    result = module.build_portfolio_distribution(report, artifacts)
    assert (report, artifacts) == before
    assert result["sha256"] == canonical_sha256({k: v for k, v in result.items() if k != "sha256"})
    sidecar = result["non_ready_selected_leg_research"]
    assert sidecar["mode"] == "SHADOW" and sidecar["decision_effect"] == "NONE"
    assert sidecar["source_stage"] == "recommendations"
    return result, sidecar, sidecar["models"].get(name)


def test_normal_public_caller_additive_only_existing_ready_fields_and_inputs_exact():
    source = card(legs=[leg(), leg("b", "unknown")])
    report, artifacts = inputs([source])
    report["tickets"] = [card("ready", status="READY")]
    baseline = module.build_portfolio_distribution(report, {"numeric_models": artifacts["numeric_models"]})
    result, _, _ = evaluate(report, artifacts)
    stripped = {k: v for k, v in result.items() if k not in {"sha256", "non_ready_selected_leg_research"}}
    assert stripped == {k: v for k, v in baseline.items() if k != "sha256"}
    assert result["packages"] == baseline["packages"]
    assert result["selected_leg_partial_scenarios"] == baseline["selected_leg_partial_scenarios"]


@pytest.mark.parametrize("status", ["DRAFT", "REVIEWED", "MODEL_READY", "ODDS_PENDING", "TARGET_PROPOSAL"])
def test_predeclared_nonready_statuses_have_research_without_promotion(status):
    report, artifacts = inputs([card(status=status)])
    _, sidecar, model = evaluate(report, artifacts)
    assert sidecar["accounting"]["included_card_count"] == 1
    row = model["cards"][0]
    assert row["source_card_identity"]["status"] == status
    assert row["known_leg_event_probability"] == pytest.approx(.6)
    assert row["source_status_promoted"] is row["research_event_is_new_ticket"] is False


def test_partial_two_of_three_preserves_unknown_and_fullcard_identity():
    source = card(legs=[leg(), leg("b", "g"), leg("c", "missing")])
    report, artifacts = inputs([source], [prediction(), prediction("g")])
    _, _, model = evaluate(report, artifacts)
    row = model["cards"][0]
    assert (row["known_leg_count"], row["unknown_leg_count"], row["source_leg_count"]) == (2, 1, 3)
    assert row["source_card_sha256"] == canonical_sha256(source)
    assert row["known_leg_event_probability"] == pytest.approx(.36)
    assert row["scenario_model_full_card_probability"] is None
    assert row["scenario_model_full_card_probability_bounds"] == {"lower": 0.0, "upper": pytest.approx(.36)}
    assert row["bounds_interpretation"] == module.SCENARIO_BOUNDS_LABEL


def test_unknown_price_is_not_a_veto_for_source_bound_model_research():
    report, artifacts = inputs([card()])
    report["recommendations"][0]["legs"][0].pop("decimal_price")
    _, _, model = evaluate(report, artifacts)
    row = model["cards"][0]
    assert row["known_leg_count"] == 1
    assert row["leg_bindings"][0]["source_binding"]["quote_price_known"] is False


@pytest.mark.parametrize("value", [None, "2.50", 2.5])
def test_totals_implicit_or_exact_explicit_line_is_supported(value):
    report, artifacts = inputs([card()])
    report["recommendations"][0]["legs"][0]["line"] = value
    _, _, model = evaluate(report, artifacts)
    assert model["known_leg_count"] == 1


def test_explicit_wrong_line_excludes_only_that_leg_not_valid_other_input():
    report, artifacts = inputs([card(legs=[leg(), leg("b", "g")])], [prediction(), prediction("g")])
    report["recommendations"][0]["legs"][0]["line"] = 3.5
    _, _, model = evaluate(report, artifacts)
    assert (model["known_leg_count"], model["unknown_leg_count"]) == (1, 1)
    assert model["cards"][0]["leg_bindings"][0]["binding"] == "EXPLICIT_MARKET_LINE_MISMATCH"


@pytest.mark.parametrize("key,value", [("market_key", "totals_3_5"), ("selection_key", "under"),
                                        ("period", "FIRST_HALF"), ("settlement", "INCLUDING_EXTRA_TIME")])
def test_product_mismatch_cannot_borrow_same_fixture_probability(key, value):
    report, artifacts = inputs([card()])
    report["recommendations"][0]["legs"][0][key] = value
    _, _, model = evaluate(report, artifacts)
    assert model["known_leg_count"] == 0
    assert model["cards"][0]["leg_bindings"][0]["binding"] == "SOURCE_PRODUCT_IDENTITY_MISMATCH"


def test_source_fixture_kickoff_conflict_is_local_unknown():
    report, artifacts = inputs([card()])
    artifacts["fixtures"][0]["kickoff"] = "2026-10-29T18:00:00+00:00"
    _, _, model = evaluate(report, artifacts)
    assert model["known_leg_count"] == 0


@pytest.mark.parametrize("source", ["candidate", "fixture", "prediction"])
def test_duplicate_source_identity_is_not_arbitrarily_first_selected(source):
    report, artifacts = inputs([card()])
    if source == "candidate":
        artifacts["candidate_input"]["candidates"] *= 2
    elif source == "fixture":
        artifacts["fixtures"] *= 2
    else:
        artifacts["numeric_models"]["gauss"]["predictions"] *= 2
    _, _, model = evaluate(report, artifacts)
    assert model["known_leg_count"] == 0


@pytest.mark.parametrize("cutoff", [None, "2026-10-27T03:00:00", "2026-10-27T04:00:00+00:00"])
def test_missing_naive_or_future_model_cutoff_never_becomes_prematch_evidence(cutoff):
    report, artifacts = inputs([card()])
    artifacts["numeric_models"]["gauss"]["predictions"][0]["model_details"]["cutoff"] = cutoff
    _, _, model = evaluate(report, artifacts)
    assert model["known_leg_count"] == 0


def test_missing_model_version_remains_local_to_that_model():
    report, artifacts = inputs([card()])
    artifacts["numeric_models"]["gauss"]["predictions"][0]["model_details"].pop("version")
    _, sidecar, model = evaluate(report, artifacts)
    assert model["known_leg_count"] == 0
    assert sidecar["models"]["laplace"]["known_leg_count"] == 1


def test_kepler_only_supported_h2h_not_totals_or_btts():
    report, artifacts = inputs([card(legs=[leg("h", "f", "h2h", "home"), leg("t", "g"), leg("b", "h", "btts", "yes")])],
                               [prediction(), prediction("g"), prediction("h")])
    artifacts["numeric_models"]["kepler"]["predictions"] = [{**prediction(f), "probabilities": {"home": .6, "draw": .25, "away": .15}}
                                                            for f in ("f", "g", "h")]
    _, _, model = evaluate(report, artifacts, "kepler")
    assert model["known_leg_count"] == 1 and model["unknown_leg_count"] == 2
    assert model["cards"][0]["scenario_model_full_card_probability"] is None


def test_zero_known_has_no_estimate_or_probability_substitute():
    report, artifacts = inputs([card(legs=[leg(fixture="unknown")])])
    _, _, model = evaluate(report, artifacts)
    row = model["cards"][0]
    assert row["known_leg_count"] == 0
    assert row["known_leg_event_probability"] is row["scenario_model_full_card_probability_bounds"] is None
    assert model["joint_known_leg_events"] is None


def test_exact_record_dedup_retains_distinct_identity_and_stake_as_shared_events():
    first = card("same", stake_units=1)
    second = card("other", stake_units=2)
    report, artifacts = inputs([first, deepcopy(first), second])
    _, sidecar, model = evaluate(report, artifacts)
    assert sidecar["accounting"]["partitions"] == {"INCLUDED": 2, "DUPLICATE": 1}
    assert {row["source_card_identity"]["stake_units"] for row in model["cards"]} == {1, 2}
    assert model["joint_known_leg_events"]["satisfied_known_leg_event_count_distribution"]["probability_mass"] == pytest.approx([.4, 0, .6])


def test_shared_fixture_opposite_selections_cannot_both_win():
    report, artifacts = inputs([card("home", [leg("h", "f", "h2h", "home")]),
                               card("away", [leg("a", "f", "h2h", "away")])])
    _, _, model = evaluate(report, artifacts)
    distribution = model["joint_known_leg_events"]["satisfied_known_leg_event_count_distribution"]
    assert distribution["probability_mass"] == pytest.approx([.25, .75, 0])


def test_every_excluded_empty_ready_or_bad_status_card_is_accounted():
    report, artifacts = inputs([card("valid"), card("ready", status="READY"), card("empty", []),
                               card("abstain", status="ABSTAINED"), card("unknown", status="FUTURE_UNKNOWN_STATUS")])
    report["recommendations"].append({"status": "DRAFT", "legs": None})
    _, sidecar, model = evaluate(report, artifacts)
    assert len(sidecar["card_accounting"]) == 6
    assert sidecar["accounting"]["partitions"] == {"INCLUDED": 1, "EXCLUDED": 5}
    assert model["source_card_count"] == 1


def test_canonical_recommendation_stage_ignores_prior_proposal_as_another_event():
    report, artifacts = inputs([card()])
    report["profile_proposals"] = deepcopy(report["recommendations"])
    _, sidecar, model = evaluate(report, artifacts)
    assert sidecar["profile_proposals_are_not_additional_events"]
    assert model["source_card_count"] == 1


def test_card_budget_failure_is_explicit_no_first_n_and_ready_still_measured():
    report, artifacts = inputs([card(str(i)) for i in range(31)])
    report["tickets"] = [card("ready", status="READY")]
    result = module.build_portfolio_distribution(report, artifacts)
    sidecar = result["non_ready_selected_leg_research"]
    assert sidecar["status"] == "UNAVAILABLE" and "BUDGET" in sidecar["reason"]
    assert sidecar["models"] == {}
    assert result["packages"]["tickets"]["models"]["gauss"]["covered_card_count"] == 1


def test_fixture_budget_failure_is_complete_unavailable_not_partial():
    source = card(legs=[leg(str(i), str(i)) for i in range(181)])
    report, artifacts = inputs([source])
    result = module.build_portfolio_distribution(report, artifacts)["non_ready_selected_leg_research"]
    assert result["status"] == "UNAVAILABLE" and result["models"] == {}
    assert len(result["card_accounting"]) == 1


def test_invalid_one_model_does_not_erase_other_models_known_events():
    report, artifacts = inputs([card()])
    artifacts["numeric_models"]["kepler"]["predictions"] = None
    _, sidecar, model = evaluate(report, artifacts)
    assert model["known_leg_count"] == 1
    assert sidecar["models"]["kepler"]["reason"] == "INVALID_LOCAL_MODEL_SOURCE"


def test_explicit_foreign_quote_price_is_not_claimed_source_bound():
    report, artifacts = inputs([card()])
    report["recommendations"][0]["legs"][0]["decimal_price"] = 9.9
    _, _, model = evaluate(report, artifacts)
    assert model["known_leg_count"] == 0
    assert model["cards"][0]["leg_bindings"][0]["binding"] == "EXPLICIT_QUOTE_CORRESPONDENCE_MISMATCH"


def test_future_quote_receipt_does_not_become_old_source_evidence():
    report, artifacts = inputs([card()])
    for row in (report["recommendations"][0]["legs"][0], artifacts["candidate_input"]["candidates"][0]):
        row["quote_observed_at"] = "2026-10-27T04:00:00+00:00"
    _, _, model = evaluate(report, artifacts)
    assert model["known_leg_count"] == 0


def test_source_budget_is_explicit_not_hidden_candidate_truncation():
    report, artifacts = inputs([card()])
    artifacts["candidate_input"]["candidates"] *= 10001
    result = module.build_portfolio_distribution(report, artifacts)["non_ready_selected_leg_research"]
    assert result["status"] == "UNAVAILABLE" and "BUDGET" in result["reason"]


def test_identical_inputs_are_seeded_and_deterministic():
    report, artifacts = inputs([card("one"), card("two", [leg("b", "g")])], [prediction(), prediction("g")])
    first = module.build_portfolio_distribution(report, artifacts)
    second = module.build_portfolio_distribution(deepcopy(report), deepcopy(artifacts))
    assert first == second


def test_no_source_view_preserves_legacy_output_shape():
    result = module.build_portfolio_distribution({"observed_at": NOW, "recommendations": [card()]}, {})
    assert "non_ready_selected_leg_research" not in result


def test_broken_optional_source_does_not_erase_ready_packages(monkeypatch):
    report, artifacts = inputs([card()])
    report["tickets"] = [card("ready", status="READY")]
    monkeypatch.setattr(module, "non_ready_selected_leg_research", Mock(side_effect=ValueError("PRIVATE_SOURCE_TEXT")))
    result = module.build_portfolio_distribution(report, artifacts)
    assert result["packages"]["tickets"]["models"]["gauss"]["covered_card_count"] == 1
    assert result["non_ready_selected_leg_research"]["reason"] == "NON_READY_RESEARCH_SOURCE_INPUT_INVALID"
    assert "PRIVATE_SOURCE_TEXT" not in str(result)


def test_source_and_quote_candidate_alias_contradiction_rejected():
    report, artifacts = inputs([card()])
    report["recommendations"][0]["legs"][0].update(source_candidate_id="a", quote_candidate_id="foreign")
    _, _, model = evaluate(report, artifacts)
    assert model["known_leg_count"] == 0
    assert model["cards"][0]["leg_bindings"][0]["binding"] == "CONFLICTING_SUPPLIED_CANDIDATE_IDENTIFIERS"


def test_another_real_candidate_id_cannot_be_hidden_by_source_alias():
    report, artifacts = inputs([card("one"), card("other", [leg("b", "g")])], [prediction(), prediction("g")])
    report["recommendations"][0]["legs"][0].update(source_candidate_id="a", quote_candidate_id="a", candidate_id="b")
    _, _, model = evaluate(report, artifacts)
    assert model["cards"][0]["known_leg_count"] == 0
    assert model["cards"][1]["known_leg_count"] == 1


def test_documented_model_virtual_candidate_id_has_exact_original_quote_binding():
    report, artifacts = inputs([card()])
    value = report["recommendations"][0]["legs"][0]
    value.update(source_candidate_id="a", quote_candidate_id="a")
    value["candidate_id"] = "one-" + canonical_sha256(["one", "f", "totals_2_5", "over", "TESTONLY_BOOK"])[:24]
    _, _, model = evaluate(report, artifacts)
    assert model["known_leg_count"] == 1
    assert model["cards"][0]["leg_bindings"][0]["source_binding"]["candidate_id"] == "a"


def test_undeclared_virtual_id_is_not_accepted_as_quote_provenance():
    report, artifacts = inputs([card()])
    report["recommendations"][0]["legs"][0].update(source_candidate_id="a", quote_candidate_id="a", candidate_id="one-forged-virtual")
    _, _, model = evaluate(report, artifacts)
    assert model["known_leg_count"] == 0


def test_same_recommendation_identity_conflicting_versions_not_favorable_selected():
    first, second = card("same"), card("same", [leg("b", "g")])
    report, artifacts = inputs([first, second, card("valid", [leg("c", "h")])],
                               [prediction(), prediction("g", over25=.9, under25=.1), prediction("h")])
    _, sidecar, model = evaluate(report, artifacts)
    assert sidecar["accounting"]["partitions"] == {"EXCLUDED": 2, "INCLUDED": 1}
    assert all(row["reason"] == "CONFLICTING_SOURCE_CARD_IDENTITY" for row in sidecar["card_accounting"][:2])
    assert model["source_card_count"] == 1 and model["known_leg_count"] == 1


def test_same_ticket_identity_conflicting_stake_versions_not_two_research_events():
    first, second = card("one", ticket_id="SAME", stake_units=1), card("two", ticket_id="SAME", stake_units=2)
    report, artifacts = inputs([first, second])
    _, sidecar, model = evaluate(report, artifacts)
    assert sidecar["accounting"]["partitions"] == {"EXCLUDED": 2}
    assert sidecar["accounting"]["included_card_count"] == 0
    assert sidecar["status"] == "NO_SUPPORTED_NON_READY_CARDS"
    assert model is None and sidecar["models"] == {}


def test_actual_normal_observed_records_envelope_reaches_research_kernel():
    report, artifacts = inputs([card()])
    artifacts["fixtures"] = {"observed_at": NOW, "records": artifacts["fixtures"]}
    _, sidecar, model = evaluate(report, artifacts)
    assert sidecar["fixture_source_kind"] == "NORMAL_OBSERVED_RECORDS_ENVELOPE"
    assert sidecar["source_authentication"] == "RETAINED_REPORT_INPUT_CORRESPONDENCE_NOT_PIT_OR_CALIBRATION"
    assert model["known_leg_count"] == 1 and model["cards"][0]["known_leg_event_probability"] == pytest.approx(.6)


@pytest.mark.parametrize("observed", ["2026-10-27T04:00:00+00:00", "2026-10-27T03:00:00", None, "NOT_A_CLOCK"])
def test_future_naive_missing_or_malformed_fixture_envelope_clock_is_local_unknown(observed):
    report, artifacts = inputs([card()])
    report["tickets"] = [card("ready", status="READY")]
    artifacts["fixtures"] = {"observed_at": observed, "records": artifacts["fixtures"]}
    result, sidecar, model = evaluate(report, artifacts)
    assert sidecar["status"] == "UNAVAILABLE" and sidecar["models"] == {} and model is None
    assert sidecar["accounting"]["unexamined_card_count"] == 1
    assert result["packages"]["tickets"]["models"]["gauss"]["covered_card_count"] == 1


def test_extreme_integer_line_is_local_unknown_preserves_ready_and_other_valid_research():
    report, artifacts = inputs([card(legs=[leg(), leg("b", "g")])], [prediction(), prediction("g")])
    report["recommendations"][0]["legs"][0]["line"] = 10 ** 400
    report["tickets"] = [card("ready", status="READY")]
    baseline = module.build_portfolio_distribution(report, {"numeric_models": artifacts["numeric_models"]})
    result, _, model = evaluate(report, artifacts)
    assert {key: value for key, value in result.items() if key not in {"sha256", "non_ready_selected_leg_research"}} == {
        key: value for key, value in baseline.items() if key != "sha256"}
    assert result["packages"]["tickets"]["models"]["gauss"]["covered_card_count"] == 1
    assert (model["known_leg_count"], model["unknown_leg_count"]) == (1, 1)
    assert model["cards"][0]["leg_bindings"][0]["binding"] == "EXPLICIT_MARKET_LINE_MISMATCH"
    assert model["cards"][0]["leg_bindings"][1]["binding"] == "VALID_MODEL_MARKET_MARGINAL"


def test_huge_invalid_model_probability_is_local_to_leg_preserves_same_model_and_others():
    report, artifacts = inputs([card(legs=[leg(), leg("b", "g")])], [prediction(), prediction("g")])
    artifacts["numeric_models"]["gauss"]["predictions"][0]["probabilities"]["over25"] = 10 ** 400
    report["tickets"] = [card("ready", [leg("b", "g")], status="READY")]
    baseline = module.build_portfolio_distribution(report, {"numeric_models": artifacts["numeric_models"]})
    result, sidecar, model = evaluate(report, artifacts)
    assert {key: value for key, value in result.items() if key not in {"sha256", "non_ready_selected_leg_research"}} == {
        key: value for key, value in baseline.items() if key != "sha256"}
    assert (model["known_leg_count"], model["unknown_leg_count"]) == (1, 1)
    assert model["cards"][0]["leg_bindings"][0]["binding"] == "INVALID_LOCAL_MODEL_PROBABILITY"
    assert model["cards"][0]["leg_bindings"][1]["binding"] == "VALID_MODEL_MARKET_MARGINAL"
    assert sidecar["models"]["laplace"]["known_leg_count"] == 2
    assert sidecar["models"]["kepler"]["known_leg_count"] == 2
    assert result["packages"]["tickets"]["models"]["gauss"]["covered_card_count"] == 1
