"""Source-bound candidate scenario facts before a profile's final decision.

This adapter reuses the existing research producer, including its explicit
cross-fixture independence/IPF assumptions. It never authenticates fresh quotes,
fits a model, promotes a card, selects a ticket, or claims calibrated probability.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import portfolio_distribution as distribution

VERSION = "profile-predecision-scenarios-v1"
PHASE = "BEFORE_FINAL_PROFILE_DECISION"
MAX_LEGS_PER_CARD = 6


def source_leg_signature(card):
    """Exact economic and source identity; order/status/prose are not exposures."""
    fields=('fixture_id','market_key','selection_key','period','line','handicap','decimal_price',
            'bookmaker','region','currency','quote_provider','quote_source_id','quote_source_sha256',
            'quote_observed_at','quoted_at','kickoff')
    rows=[{**{key:leg.get(key) for key in fields},'source_candidate_id':_source_id(leg)}
          for leg in card.get('legs',[])]
    return canonical_sha256(sorted(rows,key=canonical_sha256))


def _source_id(leg):
    return leg.get("source_candidate_id") or leg.get("quote_candidate_id") or leg.get("candidate_id")


def _model_refs(model, fixtures):
    predictions = model.get("predictions", [])
    predictions = predictions if isinstance(predictions, list) else []
    datasets = model.get("training_datasets") or {}
    manifests = datasets.get("manifests", []) if isinstance(datasets, dict) else []
    return {
        "numeric_predictions_sha256": canonical_sha256(predictions),
        "predictions": [{"fixture_id": row.get("fixture_id"),
                         "prediction_sha256": canonical_sha256(row),
                         "model_cutoff": row.get("model_details", {}).get("cutoff"),
                         "model_version": row.get("model_details", {}).get("version")}
                        for row in predictions if isinstance(row, dict)
                        and row.get("fixture_id") in fixtures],
        "dataset_manifest_refs": [{key: row[key] for key in ("dataset_id", "sha256", "as_of", "path")
                                   if key in row} for row in manifests[:30] if isinstance(row, dict)]
        if isinstance(manifests, list) else [],
        "dataset_manifest_count": len(manifests) if isinstance(manifests, list) else None,
    }


def build_profile_scenario_context(report, artifacts, profile_cards, *, as_of, enabled=True):
    """Return bounded per-card facts and source-candidate review context, without I/O.

    ``profile_cards`` are the profiles' own provisional full-day choices, at most
    one six-leg card per profile. They are hypotheses, not final issued tickets.
    The caller must attach these facts to the actual subsequent decision/review;
    merely saving this result is not proof that a decision consumed them.
    """
    if type(enabled) is not bool:
        raise ValueError("SCENARIO_ENABLED_BOOLEAN_REQUIRED")
    result = {"version": VERSION, "phase": PHASE,
              "status": "DISABLED" if not enabled else "UNKNOWN",
              "decision_effect": "NONE" if not enabled else "DECISION_INPUT_ONLY",
              "profiles": [], "reviewer_context_by_source_candidate_id": {},
              "external_requests": 0, "original_statuses_or_model_outputs_changed": False}
    if not enabled:
        return result
    stamp = as_of.isoformat() if isinstance(as_of, datetime) else as_of
    if distribution._time(stamp) is None:
        raise ValueError("AWARE_PREDECISION_CUTOFF_REQUIRED")
    if not isinstance(profile_cards, (list, tuple)):
        raise TypeError("PROFILE_CARDS_LIST_REQUIRED")
    result.update(as_of=stamp, input_card_count=len(profile_cards))
    if len(profile_cards) > distribution.MAX_CARDS:
        result["reason"] = "PROFILE_SCENARIO_CARD_BUDGET_EXCEEDED"
        return result

    hypotheses, originals = [], {}
    candidate_rows=artifacts.get('candidate_input',{}).get('candidates',[])
    source_ids={row.get('candidate_id') for row in candidate_rows if isinstance(row,dict)}
    for index, card in enumerate(profile_cards):
        identity = canonical_sha256(card)
        legs = card.get("legs", []) if isinstance(card, dict) else []
        profile = card.get("profile_id") if isinstance(card, dict) else None
        valid = (isinstance(profile, str) and bool(profile) and isinstance(legs, list)
                 and 0 < len(legs) <= MAX_LEGS_PER_CARD and all(
                     isinstance(leg, dict) and isinstance(leg.get("fixture_id"), str)
                     and isinstance(_source_id(leg), str) and all(
                         leg.get(key) is None or isinstance(leg[key], str)
                         for key in ("candidate_id", "source_candidate_id", "quote_candidate_id"))
                     for leg in legs))
        assessment = {"profile_id": profile, "source_card_sha256": identity,
                      "input_card_index": index, "original_status": card.get("status") if isinstance(card, dict) else None,
                      "status": "UNKNOWN", "source_candidate_ids": [], "models": {},
                      "risk_facts": [], "uncertainties": [],
                      "method": {key: deepcopy(card[key]) for key in
                                 ("method_kind", "method_version", "strategy_version", "catalog_version")
                                 if isinstance(card, dict) and key in card}}
        if valid:
            assessment['source_leg_signature']=source_leg_signature(card)
        result["profiles"].append(assessment)
        if not valid:
            assessment["uncertainties"].append("EMPTY_INVALID_OR_OVER_BUDGET_PROFILE_HYPOTHESIS")
            continue
        hypothesis = deepcopy(card)
        hypothesis.update(status="TARGET_PROPOSAL", original_status=card.get("status"),
                          hypothesis_role="PREDECISION_CANDIDATE_OPTION",
                          original_input_card_sha256=identity)
        projections=[]
        for leg in hypothesis['legs']:
            source=leg.get('source_candidate_id')
            alias=leg.get('candidate_id')
            expected=str(card['profile_id'])+'-'+canonical_sha256([card['profile_id'],source])[:20]
            if (isinstance(source,str) and leg.get('quote_candidate_id')==source
                    and alias==expected and alias not in source_ids):
                # Only the exact existing persona constructor alias is projected.
                # The unchanged producer still checks every source/product/clock.
                projections.append({'original_candidate_id':alias,'source_candidate_id':source})
                leg['candidate_id']=source
        assessment['source_alias_projection']=projections
        hypothetical_hash = canonical_sha256(hypothesis)
        assessment.update(hypothesis_card_sha256=hypothetical_hash,
                          hypothesis_role="PREDECISION_CANDIDATE_OPTION", hypothesis_status="TARGET_PROPOSAL")
        originals[hypothetical_hash] = (assessment, card)
        hypotheses.append(hypothesis)

    # Scope the research source lookup to referenced products, not the profiles'
    # starting calendar. Preserve duplicate source rows so ambiguity stays UNKNOWN.
    fixture_source = artifacts.get("fixtures", [])
    fixture_rows = fixture_source.get("records", []) if isinstance(fixture_source, dict) else fixture_source
    candidate_rows = artifacts.get("candidate_input", {}).get("candidates", [])
    fixture_ids = {leg.get("fixture_id") for card in hypotheses for leg in card["legs"]}
    source_ids = {leg.get(key) for card in hypotheses for leg in card["legs"]
                  for key in ("candidate_id", "source_candidate_id", "quote_candidate_id")
                  if isinstance(leg.get(key), str)}
    scoped = deepcopy(artifacts)
    if isinstance(fixture_rows, list):
        records = [row for row in fixture_rows if isinstance(row, dict) and row.get("fixture_id") in fixture_ids]
        scoped["fixtures"] = {**fixture_source, "records": records} if isinstance(fixture_source, dict) else records
    if isinstance(candidate_rows, list):
        scoped["candidate_input"] = {**artifacts.get("candidate_input", {}), "candidates":
            [row for row in candidate_rows if isinstance(row, dict) and row.get("candidate_id") in source_ids]}
    result["source_refs"] = {"calendar_sha256": canonical_sha256(fixture_source),
                             "calendar_fixture_count": len(fixture_rows) if isinstance(fixture_rows, list) else None,
                             "candidate_input_sha256": canonical_sha256(artifacts.get("candidate_input", {})),
                             "source_scope": "REFERENCED_PRODUCTS_ONLY_NOT_A_PROFILE_CALENDAR_FILTER"}
    snapshot = {"run_id": report.get("run_id"), "date": report.get("date"),
                "observed_at": stamp, "finished_at": stamp, "recommendations": hypotheses, "tickets": []}
    produced = distribution.build_portfolio_distribution(snapshot, scoped)
    kernel = produced["non_ready_selected_leg_research"]
    result.update(existing_producer_version=produced["version"],
                  existing_kernel_result=kernel, assumptions=produced["assumptions"])
    for name, model_result in kernel.get("models", {}).items():
        for measured in model_result.get("cards", []):
            assessment, original = originals[measured["source_card_sha256"]]
            local_fixtures = {leg.get("fixture_id") for leg in original["legs"]}
            model = artifacts.get("numeric_models", {}).get(name) or {}
            facts = {key: deepcopy(measured.get(key)) for key in
                     ("status", "known_leg_count", "unknown_leg_count", "known_leg_event_probability",
                      "scenario_model_full_card_probability", "scenario_model_full_card_probability_bounds",
                      "bounds_interpretation")}
            facts["source_refs"] = _model_refs(model, local_fixtures)
            facts["leg_bindings"] = deepcopy(measured["leg_bindings"])
            assessment["models"][name] = facts
            known, unknown = measured["known_leg_count"], measured["unknown_leg_count"]
            probability = measured.get("known_leg_event_probability")
            text = f"{name}: {known}/{measured['source_leg_count']} source-bound modeled legs; {unknown} UNKNOWN."
            if probability is not None:
                text += f" Existing uncalibrated scenario known-leg event probability={probability:.8g}."
                if unknown:
                    text += " Full-card probability UNKNOWN; known-leg event is not a replacement ticket."
            assessment["risk_facts"].append(text)
            for binding in measured["leg_bindings"]:
                source = binding["source_binding"]
                if source.get("status") == "BOUND":
                    assessment["source_candidate_ids"].append(source["candidate_id"])
                if binding["binding"] != "VALID_MODEL_MARKET_MARGINAL":
                    assessment["uncertainties"].append(f"{name}:{binding['binding']}")
    for assessment in result["profiles"]:
        assessment["source_candidate_ids"] = sorted(set(assessment["source_candidate_ids"]))
        if not assessment["models"]:
            assessment["uncertainties"].append(kernel.get("reason", "SCENARIO_NOT_ESTIMABLE"))
        assessment["uncertainties"] = sorted(set(assessment["uncertainties"]))
        modeled = [model for model in assessment["models"].values() if model["known_leg_count"]]
        assessment["status"] = ("AVAILABLE" if modeled and not assessment["uncertainties"]
                                else "PARTIAL" if modeled else "UNKNOWN")
        assessment["context_sha256"] = canonical_sha256(assessment)
        compact = {key: deepcopy(assessment[key]) for key in
                   ("profile_id", "source_card_sha256", "original_status", "method", "status",
                    "risk_facts", "uncertainties", "context_sha256")}
        compact.update(version=VERSION, phase=PHASE)
        for identifier in assessment["source_candidate_ids"]:
            result["reviewer_context_by_source_candidate_id"].setdefault(identifier, []).append(compact)
    result["status"] = ("AVAILABLE" if result["profiles"] and all(row["status"] == "AVAILABLE" for row in result["profiles"])
                        else "PARTIAL" if any(row["status"] != "UNKNOWN" for row in result["profiles"]) else "UNKNOWN")
    result["sha256"] = canonical_sha256(result)
    return result
