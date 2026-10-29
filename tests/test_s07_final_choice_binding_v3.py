"""TESTONLY final-cohort binding with existing source-bound scenario kernels."""
import inspect
import socket
import sqlite3
import subprocess
import urllib.request
from copy import deepcopy
from unittest.mock import Mock

import pytest
from test_current_profile_decision_inputs import NOW, own_card, source
from test_profile_context_review import prepared, witnessed

from goal_analysis.agents import astra_review
from goal_analysis.generated_tickets import refresh_generation_capacity
from goal_analysis.jobs import portfolio, profile_scenarios
from goal_analysis.jobs import profile_context_review as job
from goal_analysis.jobs.portfolio_distribution import MODELS


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("TESTONLY_NO_DB_NETWORK_PROCESS_OR_FIT"))
    for owner, name in ((sqlite3, "connect"), (socket.socket, "connect"),
                        (socket, "create_connection"), (urllib.request, "urlopen"), (subprocess, "Popen")):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr(astra_review, "_utcnow", lambda: NOW)
    yield
    forbidden.assert_not_called()


def choice(sources, profile="gauss"):
    value = own_card(sources, profile)
    value.update(ticket_id="TESTONLY-FINAL-" + profile, status="READY", publication_scope="LIVE", playable=True,
                 reasoning=["TESTONLY saját stratégiai indok."])
    return value


def inputs(*, late=False, unchanged_second=False):
    sources = [source("quote-old", "the_stats_api:mt_123"), source("quote-new", "the_stats_api:mt_124")]
    original = choice(sources[:1])
    selected = choice(sources[1:] if late else sources[:1])
    report = {"run_id": "TESTONLY-FINAL-S07", "date": NOW.date().isoformat(),
        "observed_at": NOW.isoformat(), "finished_at": NOW.isoformat(),
        "settings": {"operating_contract_version": 2}, "generated_tickets": [selected],
        "recommendations": [deepcopy(selected)],
        "profile_decision_sources": [{"source_group": "recommendations", "card": deepcopy(original)}]}
    prior_cards = [original]
    if unchanged_second:
        same = choice(sources[:1], "kepler")
        prior_cards.append(deepcopy(same))
        report["generated_tickets"].append(same)
    predictions = [{"fixture_id": row["fixture_id"], "probabilities": {
        "home": .5, "draw": .25, "away": .25, "over25": over, "under25": 1 - over, "btts_yes": .5},
        "model_details": {"cutoff": NOW.isoformat(), "version": "TESTONLY_EXISTING_V3"}}
        for row, over in zip(sources, (.6, .8), strict=True)]
    artifacts = {"candidate_input": {"candidates": deepcopy(sources)},
        "fixtures": {"observed_at": NOW.isoformat(), "records": [{key: row[key] for key in (
            "fixture_id", "kickoff", "provider_fixture_id", "provider_league_id", "provider_season_id",
            "provider_home_team_id", "provider_away_team_id")} for row in sources]},
        "numeric_models": {name: {"status": "READY", "predictions": deepcopy(predictions)} for name in MODELS}}
    prior = profile_scenarios.build_profile_scenario_context(report, artifacts, prior_cards, as_of=NOW)
    report["profile_predecision_scenarios"] = prior
    return report, artifacts, sources


def test_late_choice_computes_actual_new_source_forecast_only_and_old_review_cannot_make_ready(monkeypatch):
    report, artifacts, sources = inputs(late=True, unchanged_second=True)
    old_wire, contexts = witnessed(sources[1:])
    covered, issues = job.bindings(old_wire, contexts, sources)
    assert not issues and "quote-new" in covered
    assert "profile_scenario_context_sha256" not in covered["quote-new"]
    artifacts.update(old_wire)
    report["astra"] = deepcopy(old_wire["candidate_review_response_batches"][0]["response"])
    unchanged_hash = next(row["context_sha256"] for row in report["profile_predecision_scenarios"]["profiles"]
                          if row["profile_id"] == "kepler")
    facts_before = deepcopy({key: artifacts[key] for key in ("candidate_input", "fixtures", "numeric_models")})
    actual_builder = profile_scenarios.build_profile_scenario_context
    calls = []

    def recorded(*args, **kwargs):
        calls.append([row["profile_id"] for row in args[2]])
        return actual_builder(*args, **kwargs)

    monkeypatch.setattr(profile_scenarios, "build_profile_scenario_context", recorded)
    result, _ = job.ensure_final_decision_inputs(report, {}, artifacts, contexts, now=NOW)
    assessment = next(row for row in result["profile_predecision_scenarios"]["profiles"] if row["profile_id"] == "gauss")
    assert calls == [["gauss"]]
    assert assessment["models"]["gauss"]["scenario_model_full_card_probability"] == pytest.approx(.8)
    assert assessment["models"]["gauss"]["source_refs"]["predictions"][0]["fixture_id"] == sources[1]["fixture_id"]
    assert assessment["models"]["gauss"]["source_refs"]["predictions"][0]["model_cutoff"] == NOW.isoformat()
    assert next(row["context_sha256"] for row in result["profile_predecision_scenarios"]["profiles"]
                if row["profile_id"] == "kepler") == unchanged_hash
    final = result["generated_tickets"][0]
    assert final["status"] == "DRAFT" and final["playable"] is False
    assert final["profile_context_review"]["legs"][0]["reason"] == "NO_EXACT_CURRENT_PROFILE_SCENARIO_REVIEW"
    assert any("80.00%" in line for line in final["reasoning"])
    assert result["final_scenario_rebinding"]["llm_requests"] == result["final_scenario_rebinding"]["model_fits"] == 0
    assert {key: artifacts[key] for key in facts_before} == facts_before


def test_final_canonical_identity_is_not_replaced_by_different_captured_original(monkeypatch):
    report, artifacts, sources = inputs(late=True)
    original_signature = profile_scenarios.source_leg_signature(report["profile_decision_sources"][0]["card"])
    final_before = deepcopy(report["generated_tickets"][0])
    forbidden_selector = Mock(side_effect=AssertionError("FINAL_BINDING_MUST_NOT_RESELECT"))
    monkeypatch.setattr("goal_analysis.generated_tickets.finalize_generated", forbidden_selector)
    result, _ = job.ensure_final_decision_inputs(report, {}, artifacts, prepared(sources), now=NOW)
    final = result["generated_tickets"][0]
    assert final["ticket_id"] == final_before["ticket_id"]
    assert final["legs"] == final_before["legs"]
    assert profile_scenarios.source_leg_signature(final) != original_signature
    assert final["decision_reasoning"]["source_leg_signature"] == profile_scenarios.source_leg_signature(final)
    forbidden_selector.assert_not_called()


def test_unchanged_economic_alias_restores_visible_current_facts_idempotently_without_recompute(monkeypatch):
    report, artifacts, sources = inputs()
    stale = "[S07] stale unrelated full-ticket 99.00%"
    for card in (report["generated_tickets"][0], report["recommendations"][0]):
        card["reasoning"].append(stale)
        card["decision_reasoning"] = {"visible_scenario_lines": [stale], "source_leg_signature": None}
    forbidden_builder = Mock(side_effect=AssertionError("UNCHANGED_CHOICE_MUST_NOT_RECOMPUTE"))
    monkeypatch.setattr(profile_scenarios, "build_profile_scenario_context", forbidden_builder)
    result, options = job.ensure_final_decision_inputs(report, {}, artifacts, prepared(sources), now=NOW)
    after = deepcopy(result)
    second, _ = job.ensure_final_decision_inputs(result, options, artifacts, prepared(sources), now=NOW)
    assert second == after
    for card in (second["generated_tickets"][0], second["recommendations"][0]):
        assert stale not in card["reasoning"]
        assert card["reasoning"][0] == "TESTONLY saját stratégiai indok."
        assert any("60.00%" in line and "feltételes, nem kalibrált" in line for line in card["reasoning"])
        assert card["decision_reasoning"]["source_leg_signature"] == profile_scenarios.source_leg_signature(card)
        assert sum(line.startswith("[S07] GAUSS:") for line in card["reasoning"]) == 1
    forbidden_builder.assert_not_called()


def test_capacity_refresh_changes_only_counters_and_final_public_path_has_no_later_selection(monkeypatch):
    report, _, sources = inputs()
    other = choice(sources[1:], "kepler")
    other.update(status="DRAFT", publication_scope="PAPER_AUDITION", playable=False)
    report["generated_tickets"].append(other)
    report["generation_capacity"] = {"excluded_profiles": [{"profile_id": "laplace", "reason": "TESTONLY"}]}
    before = deepcopy(report["generated_tickets"])
    forbidden_selector = Mock(side_effect=AssertionError("COUNTERS_MUST_NOT_RESELECT"))
    monkeypatch.setattr("goal_analysis.generated_tickets.finalize_generated", forbidden_selector)
    refresh_generation_capacity(report)
    assert report["generated_tickets"] == before
    assert report["generation_capacity"]["status_counts"] == {"READY": 1, "DRAFT": 1}
    assert report["generation_capacity"]["scope_counts"] == {"LIVE": 1, "PAPER_AUDITION": 1}
    assert report["generation_capacity"]["ready_live_count"] == report["generation_capacity"]["draft_count"] == 1
    report["generated_tickets"][0]["status"] = "DRAFT"
    identities = [(row["ticket_id"], deepcopy(row["legs"])) for row in report["generated_tickets"]]
    refresh_generation_capacity(report)
    assert [(row["ticket_id"], row["legs"]) for row in report["generated_tickets"]] == identities
    assert report["generation_capacity"]["ready_live_count"] == 0
    assert report["generation_capacity"]["draft_count"] == 2
    assert report["generation_capacity"]["financial_wager_cap_changed"] is False
    forbidden_selector.assert_not_called()
    tail = inspect.getsource(portfolio.run_portfolio).split("report,diversity_options=ensure_final_decision_inputs(", 1)[1]
    assert "refresh_generation_capacity(report)" in tail
    assert all(token not in tail for token in ("finalize_generated(", "allocate(", "package_reviewer("))
