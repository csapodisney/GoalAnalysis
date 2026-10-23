"""Descriptive exact-product leg outcomes; no causal or predictive attribution."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy

from goal_analysis.agents import canonical_sha256

VERSION = "arthur-leg-error-analysis-v1"
STATUSES = ("WON", "LOST", "VOID", "PENDING", "REVIEW")
IDENTITY = ("fixture_id", "period", "market_key", "selection_key")


def _identity(leg):
    if not isinstance(leg, dict) or not all(isinstance(leg.get(k), str) and leg[k] for k in IDENTITY):
        return None
    return tuple(leg[k] for k in IDENTITY)


def _record(record, identifier, ambiguous, *, paper):
    originals = record.get("legs")
    originals = originals if isinstance(originals, list) else []
    identities = [_identity(leg) for leg in originals]
    issues = []
    if ambiguous:
        issues.append("AMBIGUOUS_RECORD_ID")
    if not originals or any(key is None for key in identities):
        issues.append("INVALID_OR_EMPTY_ORIGINAL_LEGS")
    if len(set(identities)) != len(identities) or len({key[0] for key in identities if key}) != len(originals):
        issues.append("DUPLICATE_OR_INVALID_FIXTURE_PRODUCT")
    mapped, missing_results = {}, False
    if paper:
        # _outcome in the existing paper policy already verified the exact
        # original/result identity. Identity failure there is not pending proof.
        if record.get("settlement_issue") in {"SETTLEMENT_IDENTITY_MISMATCH", "INVALID_SETTLEMENT_LEGS", "NO_LEGS"}:
            issues.append("PAPER_SETTLEMENT_IDENTITY_REVIEW")
        if set(record.get("exclusion_reasons", [])) & {
            "INVALID_PUBLICATION", "INVALID_RECORD", "DUPLICATE_FIXTURE", "AMBIGUOUS_PROFILE_DAY",
        }:
            issues.append("PAPER_RECORD_ADMISSION_REVIEW")
    else:
        raw = (record.get("settlement") or {}).get("legs", [])
        keys = [_identity(row) for row in raw] if isinstance(raw, list) else []
        if not raw:
            if record.get("outcome", "PENDING") == "PENDING":
                missing_results = True
            else:
                issues.append("NO_SETTLEMENT_LEG_ROWS")
        elif any(key is None for key in keys) or len(set(keys)) != len(keys) or set(keys) != set(identities):
            issues.append("SETTLEMENT_IDENTITY_MISMATCH")
        else:
            mapped = dict(zip(keys, raw, strict=True))
    admitted = not issues
    linked = admitted and not missing_results
    legs = []
    for index, original in enumerate(originals):
        original = original if isinstance(original, dict) else {}
        key = identities[index]
        result = original if paper else mapped.get(key, {})
        outcome = result.get("measured_outcome" if paper else "outcome", "PENDING")
        outcome = outcome if outcome in STATUSES else "REVIEW"
        if not admitted:
            outcome = "REVIEW"
        if not paper and outcome == "VOID" and result.get("reason") != "BOOKMAKER_VOID_CONFIRMED":
            outcome = "REVIEW"
        legs.append({"leg_index": index, **{k: original.get(k) for k in IDENTITY},
            "home_team": original.get("home_team"), "away_team": original.get("away_team"),
            "decimal_price": original.get("decimal_price"), "observed_outcome": outcome,
            "result_context": {k: deepcopy(result[k]) for k in (
                "reason", "home_goals", "away_goals", "provider_status", "note") if k in result},
            "model_probability_recorded": original.get("model_probability"),
            "forecast_score_not_assessed_here": True})
    counts = Counter(leg["observed_outcome"] for leg in legs)
    claimed = record.get("outcome", "PENDING")
    derived = ("LOST" if counts["LOST"] else "REVIEW" if counts["REVIEW"] else
        "PENDING" if counts["PENDING"] else "VOID" if counts["VOID"] == len(legs) and legs else "WON")
    # Legacy recommendation summaries label all-confirmed-void as WON. Preserve
    # that known presentation policy; do not change its actual financial logic.
    bookmaker_review = (claimed == "REVIEW" and derived in {"WON", "VOID"}
        and (record.get("settlement") or {}).get("reason") == "ACTUAL_PARTIAL_VOID_RETURN_REQUIRES_BOOKMAKER_CONFIRMATION")
    compatible = claimed == derived or (derived == "VOID" and claimed == "WON") or bookmaker_review
    if admitted and not compatible:
        issues.append("TICKET_OUTCOME_CONFLICT")
    consistent = admitted and compatible
    near = consistent and claimed == "LOST" and len(legs) >= 2 and counts["LOST"] == 1 and counts["WON"] == len(legs) - 1
    if not consistent:
        classification = "RESULT_REVIEW"
    elif bookmaker_review:
        classification = "BOOKMAKER_RETURN_REVIEW"
    elif near:
        classification = "ONE_LOSS_ALL_OTHER_LEGS_WON"
    elif counts["LOST"] >= 2:
        classification = "MULTIPLE_CONFIRMED_LOSSES"
    elif counts["LOST"] == 1:
        classification = ("SINGLE_LOST_SELECTION" if len(legs) == 1 else
            "ONE_LOSS_OTHER_LEGS_UNRESOLVED" if counts["PENDING"] or counts["REVIEW"] else "ONE_LOSS_WITH_VOID_LEGS")
    elif counts["VOID"]:
        classification = "VOID_AFFECTED"
    else:
        classification = "ALL_LEGS_WON" if derived == "WON" else "NOT_RESOLVED"
    return {"record_id": identifier, "profile_id": record.get("profile_id"), "date": record.get("date"),
        "source_record_sha256": canonical_sha256(record), "ticket_outcome_recorded": claimed,
        "result_identity_bound": linked, "ticket_leg_outcomes_consistent": consistent,
        "cross_record_result_conflict": False,
        "issues": issues, "leg_count": len(legs), "leg_outcome_counts": {s: counts[s] for s in STATUSES},
        "exactly_one_loss_all_other_legs_won": near, "classification": classification,
        "financially_eligible": record.get("paper_eligible") if paper else None, "legs": legs}


def build_leg_error_analysis(records, *, mode):
    """Retain every distinct original ticket and expose repeated-event dependence.

    A winning/losing leg describes its market result, never the cause of a
    model error, a near-win probability, or profit recoverable by replacing it.
    """
    if mode not in {"actual", "research", "recommendations", "paper"}:
        raise ValueError("Unknown leg analysis mode")
    unique, seen, identifiers = [], set(), defaultdict(set)
    for record in records:
        digest = canonical_sha256(record)
        if digest in seen:
            continue
        seen.add(digest)
        identifier = record.get("ticket_id") or record.get("recommendation_id") or "snapshot:" + digest
        identifiers[identifier].add(digest)
        unique.append((record, identifier))
    rows = [_record(record, identifier, len(identifiers[identifier]) > 1, paper=mode == "paper")
            for record, identifier in unique]
    products = defaultdict(list)
    for row in rows:
        if row["ticket_leg_outcomes_consistent"]:
            for leg in row["legs"]:
                key = (row["date"], *(_identity(leg) or ()))
                products[key].append((row["record_id"], row["profile_id"], leg["observed_outcome"]))
    shared = []
    conflicts = set()
    for key, observations in sorted(products.items(), key=lambda pair: str(pair[0])):
        counts = Counter(status for _, _, status in observations)
        decided = {status for _, _, status in observations if status in {"WON", "LOST"}}
        losing_ids = sorted({identifier for identifier, _, status in observations if status == "LOST"})
        if len(decided) > 1:
            conflicts.add(key)
        shared.append({"date": key[0], **dict(zip(IDENTITY, key[1:], strict=True)),
            "selection_occurrences": len(observations), "distinct_record_ids": sorted({r for r, _, _ in observations}),
            "profile_ids": sorted({p for _, p, _ in observations}, key=str),
            "outcome_counts": {s: counts[s] for s in STATUSES}, "conflicting_decided_outcomes": len(decided) > 1,
            "confirmed_losing_record_ids": losing_ids if len(decided) <= 1 else [],
            "same_product_shared_loss": len(losing_ids) > 1 and len(decided) == 1})
    for row in rows:
        if any((row["date"], *(_identity(leg) or ())) in conflicts for leg in row["legs"]):
            row["cross_record_result_conflict"] = True
            row["exactly_one_loss_all_other_legs_won"] = False
            row["classification"] = "CROSS_RECORD_RESULT_REVIEW"
            row["issues"].append("CONFLICTING_OBSERVED_RESULTS_ACROSS_RECORDS")
    total = Counter(leg["observed_outcome"] for row in rows for leg in row["legs"])
    return {"version": VERSION, "mode": mode, "decision_effect": "NONE", "records": rows,
        "summary": {"input_records": len(records), "distinct_records": len(rows),
            "duplicate_exact_copies_ignored": len(records) - len(rows),
            "ambiguous_record_ids": sum(len(digests) > 1 for digests in identifiers.values()),
            "record_result_review": sum(not row["ticket_leg_outcomes_consistent"] or row["cross_record_result_conflict"] for row in rows),
            "exactly_one_loss_all_other_legs_won": sum(row["exactly_one_loss_all_other_legs_won"] for row in rows),
            "classification_counts": dict(Counter(row["classification"] for row in rows)),
            "leg_outcome_counts": {s: total[s] for s in STATUSES},
            "unique_exact_products": len(products),
            "products_with_conflicting_decided_results": sum(p["conflicting_decided_outcomes"] for p in shared),
            "same_product_shared_losses": sum(p["same_product_shared_loss"] for p in shared)},
        "exact_product_results": shared,
        "semantics": "OBSERVED_MARKET_OUTCOMES_NOT_CAUSAL_ERRORS_OR_INDEPENDENT_SAMPLES",
        "notes": ["Exact product identity is fixture/period/market/selection, linked to the original ticket.",
            "One loss with pending/review/void legs is not counted as all other legs won.",
            "Shared selections across tickets remain repeated exposure, not independent observations.",
            "No financial attribution, model calibration, causal explanation or automatic strategy change."]}


def safe_leg_error_analysis(records, *, mode):
    try:
        return build_leg_error_analysis(records, mode=mode)
    except Exception:  # noqa: BLE001 - Optional analytics cannot hide stable settlement views.
        return {"version": VERSION, "mode": mode, "status": "UNAVAILABLE",
                "reason": "LEG_ERROR_ANALYSIS_FAILED", "decision_effect": "NONE"}
