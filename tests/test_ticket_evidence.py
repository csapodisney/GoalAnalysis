from copy import deepcopy

import pytest

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs.ticket_evidence import (
    CATEGORIES,
    GROUPS,
    build_ticket_evidence,
    validate_ticket_evidence,
)


def sample():
    quote = {"candidate_id": "q", "fixture_id": "f", "market_key": "totals_2_5",
        "selection_key": "over", "period": "FULL_TIME", "bookmaker": "book", "decimal_price": 2.5,
        "quote_provider": "the_stats_api", "quote_source_id": "https://source.test/odds",
        "quoted_at": None, "quote_observed_at": "2026-10-22T07:00:00+00:00"}
    evidence = {"category": "historical", "source_id": "history-sha256:abc", "strength": .7}
    leg = {**quote, "candidate_id": "renamed", "source_candidate_id": "q", "evidence": [evidence],
        "model_probability": .6, "model_details": {"calibration": "unvalidated"},
        "confidence_kind": "UNCALIBRATED_MODEL_ESTIMATE"}
    card = {"profile_id": "gauss", "ticket_id": "t", "status": "READY", "legs": [leg],
        "combined_price": 2.5, "strategy_version": "s", "strategy_manifest_sha256": "hash"}
    report = {"run_id": "run", "date": "2026-10-22", "tickets": [card]}
    artifacts = {"candidate_input": {"candidates": [quote]},
        "candidate_analysis": {"candidates": [{**quote, "evidence": [evidence]}]},
        "numeric_models": {"gauss": {"predictions": [{"fixture_id": "f", "probabilities": {"over25": .6},
            "model_details": {"calibration": "unvalidated"}}], "training_datasets": {"mode": "SHADOW"}}}}
    return report, artifacts


def sections(bundle):
    return bundle["cards"][0]["legs"][0]["sections"]


def test_exact_final_card_and_every_domain_bound_without_mutation():
    report, artifacts = sample()
    before = deepcopy((report, artifacts))
    bundle = build_ticket_evidence(report, artifacts)
    assert (report, artifacts) == before
    assert bundle["mode"] == "AUDIT_ONLY" and bundle["decision_effect"] == "NONE"
    assert set(sections(bundle)) == set(CATEGORIES)
    assert sections(bundle)["odds"]["status"] == "SELECTED_QUOTE_BOUND"
    assert sections(bundle)["model"]["all_components_bound"] is True
    assert sections(bundle)["form"]["status"] == "RETAINED_SELECTION_EVIDENCE"
    assert all(sections(bundle)[d]["status"] == "MISSING" for d in ("xg", "players", "context"))
    assert bundle["cards"][0]["card_sha256"] == canonical_sha256(report["tickets"][0])
    validate_ticket_evidence(bundle, report, artifacts)


@pytest.mark.parametrize("group", GROUPS)
def test_all_final_groups_and_empty_abstentions_included(group):
    report, artifacts = sample()
    report[group] = report.pop("tickets")
    report[group].append({"profile_id": "empty", "status": "ABSTAIN", "reason": "NO_VALID_LEGS"})
    bundle = build_ticket_evidence(report, artifacts)
    assert len(bundle["cards"]) == 2
    assert bundle["cards"][1]["empty_card_reason"] == "NO_VALID_LEGS"
    assert bundle["cards"][1]["location"] == [group, 1]


@pytest.mark.parametrize("field,value", [("decimal_price", 9), ("market_key", "h2h"),
    ("selection_key", "under"), ("fixture_id", "other"), ("bookmaker", "other"),
    ("period", "FIRST_HALF"), ("quote_source_id", "https://other.test/"),
    ("quote_observed_at", "2026-10-22T08:00:00+00:00"), ("quoted_at", "2026-10-22T07:00:00+00:00")])
def test_selected_product_source_or_clock_conflict_never_claims_bound(field, value):
    report, artifacts = sample()
    report["tickets"][0]["legs"][0][field] = value
    odds = sections(build_ticket_evidence(report, artifacts))["odds"]
    assert odds["status"] == "CONFLICT" and field in odds["mismatched_fields"]


def test_missing_quote_clock_stays_missing_and_actual_receipt_remains_separate():
    report, artifacts = sample()
    odds = sections(build_ticket_evidence(report, artifacts))["odds"]
    assert odds["selected"]["quoted_at"] is None
    assert odds["provider_quote_clock_known"] is False
    assert odds["selected"]["quote_observed_at"] == "2026-10-22T07:00:00+00:00"


def test_normalized_provider_clock_same_instant_is_allowed():
    report, artifacts = sample()
    artifacts["candidate_input"]["candidates"][0]["quoted_at"] = "2026-10-22T09:00:00+02:00"
    report["tickets"][0]["legs"][0]["quoted_at"] = "2026-10-22T07:00:00+00:00"
    assert sections(build_ticket_evidence(report, artifacts))["odds"]["status"] == "SELECTED_QUOTE_BOUND"


@pytest.mark.parametrize("duplicate", [False, True])
def test_missing_or_duplicate_source_id_is_explicit(duplicate):
    report, artifacts = sample()
    quotes = artifacts["candidate_input"]["candidates"]
    quotes.append(deepcopy(quotes[0])) if duplicate else quotes.clear()
    assert sections(build_ticket_evidence(report, artifacts))["odds"]["status"] == ("AMBIGUOUS" if duplicate else "MISSING")


def test_package_coverage_never_becomes_per_leg_usage():
    report, artifacts = sample()
    report["expert_ranking"] = {"weather_weighted_options": 99, "the_stats_api": {"evidence_attached_options": 99}}
    report["sports_supplement"] = {"the_stats_api": {"context_by_fixture": {"f": {"facts": [
        {"status": "AVAILABLE", "category": kind, "source": {"observed_at": "future"}}
        for kind in ("team_season_stats", "player_season_stats", "lineup")]}}}}
    result = sections(build_ticket_evidence(report, artifacts))
    assert all(result[d]["status"] == "AVAILABLE_NOT_CONFIRMED_USED" for d in ("xg", "players"))
    assert result["context"]["status"] == "MISSING"


@pytest.mark.parametrize("mismatch", [False, True])
def test_only_exact_selected_option_ranking_trace_attached(mismatch):
    report, artifacts = sample()
    card = report["tickets"][0]
    card["expert_ranking"] = {"the_stats_api": {"legs": [{"leg_index": 0, "fixture_id": "other" if mismatch else "f",
        "market_key": "totals_2_5", "selection_key": "over", "quoted_price": 2.5,
        "player_context": [{"id": "p"}], "player_availability_applied": False}]}}
    result = sections(build_ticket_evidence(report, artifacts))
    assert result["xg"]["status"] == ("MISSING" if mismatch else "RANKING_TRACE_RECORDED")
    assert result["players"]["status"] == ("MISSING" if mismatch else "RANKING_TRACE_RECORDED")
    if not mismatch:
        assert result["players"]["availability_applied"] is False


def test_heuristic_is_never_a_numeric_model_probability():
    report, artifacts = sample()
    leg = report["tickets"][0]["legs"][0]
    leg["confidence_kind"] = "EVIDENCE_HEURISTIC_NOT_PROBABILITY"
    assert sections(build_ticket_evidence(report, artifacts))["model"]["selected_probability"] is None


@pytest.mark.parametrize("tamper", ["probability", "fixture", "history", "weight"])
def test_missing_or_changed_prediction_and_history_not_silently_confirmed(tamper):
    report, artifacts = sample()
    leg = report["tickets"][0]["legs"][0]
    prediction = artifacts["numeric_models"]["gauss"]["predictions"][0]
    if tamper == "probability":
        prediction["probabilities"]["over25"] = .9
    elif tamper == "fixture":
        prediction["fixture_id"] = "other"
    elif tamper == "history":
        artifacts["candidate_analysis"]["candidates"][0]["evidence"] = []
    else:
        leg["model_details"] = {"shared_model_probabilities": {"gauss": .6}, "shared_model_weights": {"gauss": .3}}
    result = sections(build_ticket_evidence(report, artifacts))
    assert result["form"]["status"] == "MISSING" if tamper == "history" else result["model"]["all_components_bound"] is False


@pytest.mark.parametrize("root", ["card", "bundle", "source", "artifact"])
def test_validator_rejects_tampering_even_with_rehashed_bundle(root):
    report, artifacts = sample()
    bundle = build_ticket_evidence(report, artifacts)
    if root == "card":
        report["tickets"][0]["status"] = "DRAFT"
    elif root == "source":
        next(iter(bundle["sources"].values()))["payload"] = {"fake": True}
    elif root == "artifact":
        artifacts["candidate_input"]["candidates"][0]["decimal_price"] = 3
    else:
        sections(bundle)["players"]["status"] = "RANKING_TRACE_RECORDED"
    bundle.pop("bundle_sha256")
    bundle["bundle_sha256"] = canonical_sha256(bundle)
    with pytest.raises(ValueError, match="frozen source"):
        validate_ticket_evidence(bundle, report, artifacts)


def test_sidecar_excluded_from_its_own_identity_and_no_daily_card_changes():
    report, artifacts = sample()
    report["tickets"][0]["profile_id"] = "daily223"
    before = deepcopy(report["tickets"])
    bundle = build_ticket_evidence(report, artifacts)
    report["ticket_evidence"] = bundle
    report["report_sha256"] = "external-report-digest"
    assert build_ticket_evidence(report, artifacts) == bundle
    assert report["tickets"] == before


def test_registry_paths_payload_and_digests_bind_independently_to_inputs():
    report, artifacts = sample()
    bundle = build_ticket_evidence(report, artifacts)
    for identifier, record in bundle["sources"].items():
        source = {"report": report, "artifacts": artifacts}[record["root"]]
        for part in record["path"]:
            source = source[part]
        assert record["payload"] == source
        assert record["sha256"] == canonical_sha256(source)
        assert identifier == canonical_sha256({k: record[k] for k in ("root", "path", "sha256")})
