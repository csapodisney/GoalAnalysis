"""SHADOW weather-conditioned baseline scores, never a new forecast.

The public root/as_of reader uses actual physically validating source readers.
The pure content helper cannot authenticate caller-supplied envelopes and labels
that boundary explicitly. No writer, capture, model fit or provider is called.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic

from goal_analysis.agents.weather_expert import POLICY as WEATHER_POLICY
from goal_analysis.learning.forecast_quality import _scores
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.training_datasets import sha, snapshot, validate

VERSION = "weather-conditioned-baseline-quality-shadow-v1"
STRATUM_BASIS = "PREMATCH_FORECAST_FEATURE_PROXY_NOT_REALIZED_WEATHER"
MAX_ROWS = 10000
SETTLEMENT = "REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES"
WEATHER_FIELDS = {"temperature_c": (-80, 60), "wind_speed_kmh": (0, 300),
                  "precipitation_mm": (0, 500), "wind_gusts_kmh": (0, 500), "relative_humidity_pct": (0, 100)}
PAIR_ROLE = "native_baseline_weather_feature_pairs_shadow"
FORECAST_ROLE = "native_captured_calibration_forecasts"
PURE_AUTH = "PURE_CONTENT_CHECK_ONLY_NOT_PHYSICAL_STORE_AUTHENTICATION"
LOCAL_REASONS = {"EXACT_WEATHER_TARGET_IDENTITY_REQUIRED", "WEATHER_CONTEXT_SOURCE_HASH_OR_STATUS_MISMATCH",
    "PREMATCH_FORECAST_NOT_REANALYSIS_REQUIRED", "PREMATCH_SOURCE_AVAILABILITY_OR_TARGET_CUTOFF_MISMATCH",
    "ORIGINAL_FEATURE_PAIR_SOURCE_REFERENCE_REQUIRED", "FEATURE_PAIR_CANNOT_BECOME_WEATHER_PROBABILITY",
    "INVALID_WEATHER_VALUE", "EXACT_SUPPORTED_FORECAST_PRODUCT_REQUIRED", "ORIGINAL_NUMERIC_PROBABILITY_REQUIRED",
    "ORIGINAL_MODEL_VERSION_REQUIRED", "ORIGINAL_FORECAST_SOURCE_REFERENCES_REQUIRED", "OUTCOME_RECEIPT_BUDGET_OR_TYPE",
    "INVALID_RESULT_SOURCE_RECEIPT", "CONFLICTING_RESULT_RECEIPT_ID", "OUTCOME_MUST_BE_OBSERVED_AFTER_TARGET",
    "CONFLICTING_AVAILABLE_OUTCOME_REVISIONS", "CONFLICTING_FIRST_WEATHER_PAIRS", "ORIGINAL_NATIVE_FORECAST_MISSING",
    "CONFLICTING_ORIGINAL_FORECAST_ID", "EXACT_ORIGINAL_BASELINE_CONTENT_MISMATCH"}


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _probability(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def _seal(value):
    value["sha256"] = sha(value)
    return value


def _baseline(row):
    return {key: value for key, value in row.items() if key != "result_receipts"}


def _source_rows(value, role, cutoff):
    data = validate(value["dataset"])
    if data["manifest"]["role"] != role or utc(data["manifest"]["as_of"]) != cutoff:
        raise ValueError("SOURCE_DATASET_ROLE_OR_CUTOFF_MISMATCH")
    if len(data["rows"]) > MAX_ROWS or any(not isinstance(row, dict) for row in data["rows"]):
        raise ValueError("WEATHER_LEARNING_ROW_BUDGET")
    return data


def _weather(pair, cutoff):
    baseline, context, target = pair["baseline"], pair["weather_context"], pair["target"]
    fixture, kickoff = baseline["fixture_id"], utc(baseline["kickoff"])
    if (pair["fixture_id"] != fixture or target["fixture_id"] != fixture or context["fixture_id"] != fixture
            or fixture != "the_stats_api:" + target["provider_fixture_id"]
            or any(utc(value) != kickoff for value in (pair["kickoff"], target["kickoff"], context["kickoff"]))
            or any(not isinstance(target.get(key), str) or not target[key] for key in (
                "provider_league_id", "provider_season_id", "provider_home_team_id", "provider_away_team_id"))
            or target["provider_home_team_id"] == target["provider_away_team_id"]):
        raise ValueError("EXACT_WEATHER_TARGET_IDENTITY_REQUIRED")
    if context["status"] != "FORECAST_CONTEXT_VERIFIED_SHADOW" or pair["weather_context_sha256"] != sha(context):
        raise ValueError("WEATHER_CONTEXT_SOURCE_HASH_OR_STATUS_MISMATCH")
    weather = context["forecast"]
    if weather.get("weather_kind") != "FORECAST" or weather.get("status", "SOURCED") != "SOURCED":
        raise ValueError("PREMATCH_FORECAST_NOT_REANALYSIS_REQUIRED")
    model_at, created, baseline_saved = (utc(baseline[key]) for key in ("model_as_of", "created_at", "saved_at"))
    pair_saved = utc(pair["saved_at"])
    observed, weather_available = utc(weather["observed_at"]), utc(context["forecast_receipt"]["times"]["available_at"])
    target_available = utc(context["target_receipt"]["times"]["available_at"])
    if (not model_at <= created <= baseline_saved <= pair_saved <= cutoff
            or pair_saved > kickoff - timedelta(minutes=15)
            or not observed <= weather_available <= pair_saved
            or target_available > pair_saved
            or not 0 <= (pair_saved - observed).total_seconds() <= WEATHER_POLICY["forecast_freshness_hours"] * 3600
            or abs(utc(weather["target_time"]) - kickoff) > timedelta(minutes=WEATHER_POLICY["kickoff_tolerance_minutes"])):
        raise ValueError("PREMATCH_SOURCE_AVAILABILITY_OR_TARGET_CUTOFF_MISMATCH")
    if not _hash(pair.get("pair_id")) or not _hash(pair.get("pair_receipt_id")):
        raise ValueError("ORIGINAL_FEATURE_PAIR_SOURCE_REFERENCE_REQUIRED")
    if (pair.get("weather_model_probability") is not None or pair.get("probability_adjustment") != 0
            or pair.get("ranking_multiplier") != 1 or pair.get("live_decision_effect") is not False):
        raise ValueError("FEATURE_PAIR_CANNOT_BECOME_WEATHER_PROBABILITY")
    values = {}
    for key, (low, high) in WEATHER_FIELDS.items():
        value = weather.get(key)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high):
            raise ValueError("INVALID_WEATHER_VALUE")
        values[key] = value
    rain = values["precipitation_mm"]
    return values, "UNKNOWN" if rain is None else "WET" if rain >= WEATHER_POLICY["wet_threshold_mm"] else "DRY"


def _forecast_valid(row):
    family, selection = row.get("market_key"), row.get("selection_key")
    selections = {"h2h": {"home", "draw", "away"}, "totals_2_5": {"over", "under"}, "btts": {"yes", "no"}}
    if (family not in selections or selection not in selections[family] or row.get("period") != "FULL_TIME"
            or row.get("settlement") != SETTLEMENT
            or row.get("line") != ("2.5" if family == "totals_2_5" else None)):
        raise ValueError("EXACT_SUPPORTED_FORECAST_PRODUCT_REQUIRED")
    if not _probability(row.get("probability")) or row.get("probability_kind") != "ORIGINAL_NUMERIC_MODEL_ESTIMATE":
        raise ValueError("ORIGINAL_NUMERIC_PROBABILITY_REQUIRED")
    if any(not isinstance(row.get(key), str) or not row[key] for key in ("profile_id", "strategy_version", "model_version")):
        raise ValueError("ORIGINAL_MODEL_VERSION_REQUIRED")
    if (not _hash(row.get("run_sha256")) or not isinstance(row.get("source_receipt_ids"), list)
            or not row["source_receipt_ids"] or not all(_hash(value) for value in row["source_receipt_ids"])):
        raise ValueError("ORIGINAL_FORECAST_SOURCE_REFERENCES_REQUIRED")


def _outcome(receipts, kickoff, cutoff):
    if not isinstance(receipts, list) or len(receipts) > 100:
        raise ValueError("OUTCOME_RECEIPT_BUDGET_OR_TYPE")
    seen, labels, references = {}, set(), []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise TypeError("INVALID_RESULT_SOURCE_RECEIPT")
        identifier = receipt.get("receipt_id")
        if (not _hash(identifier) or not _hash(receipt.get("source_sha256"))
                or type(receipt.get("outcome")) is not int or receipt["outcome"] not in (0, 1)):
            raise ValueError("INVALID_RESULT_SOURCE_RECEIPT")
        if identifier in seen and seen[identifier] != sha(receipt):
            raise ValueError("CONFLICTING_RESULT_RECEIPT_ID")
        seen[identifier] = sha(receipt)
        observed, available = utc(receipt["observed_at"]), utc(receipt["available_at"])
        if not kickoff < observed <= available:
            raise ValueError("OUTCOME_MUST_BE_OBSERVED_AFTER_TARGET")
        if available > cutoff:
            continue
        labels.add(receipt["outcome"])
        references.append(identifier)
    if len(labels) > 1:
        raise ValueError("CONFLICTING_AVAILABLE_OUTCOME_REVISIONS")
    return (next(iter(labels)) if labels else None), sorted(set(references))


def _distinct_outcome_receipts(matches):
    """The 100-version bound counts distinct content, never alias repetition."""
    unique = {}
    for _, row in matches:
        receipts = row.get("result_receipts", [])
        if not isinstance(receipts, list) or len(receipts) > 100:
            raise ValueError("OUTCOME_RECEIPT_BUDGET_OR_TYPE")
        for receipt in receipts:
            if not isinstance(receipt, dict):
                raise TypeError("INVALID_RESULT_SOURCE_RECEIPT")
            unique.setdefault(sha(receipt), receipt)
            if len(unique) > 100:
                raise ValueError("OUTCOME_RECEIPT_BUDGET_OR_TYPE")
    return list(unique.values())


def _weather_summary(rows):
    result = {}
    for key in WEATHER_FIELDS:
        values = [row["weather"][key] for row in rows if row["weather"][key] is not None]
        result[key] = {"known": len(values), "unknown": len(rows) - len(values),
                       "mean": math.fsum(values) / len(values) if values else None,
                       "minimum": min(values) if values else None, "maximum": max(values) if values else None}
    return result


def summarize_source_content(feature_source, forecast_source, *, as_of):
    """Pure content contract; this function never authenticates caller sources."""
    cutoff = utc(as_of)
    pairs = _source_rows(feature_source, PAIR_ROLE, cutoff)
    forecasts = _source_rows(forecast_source, FORECAST_ROLE, cutoff)
    index = defaultdict(list)
    for position, row in enumerate(forecasts["rows"]):
        identifier = row.get("forecast_id")
        index[identifier if _hash(identifier) else ("INVALID_NATIVE_FORECAST_ID", position)].append((position, row))
    occurrences, candidates, seen_pairs = [], defaultdict(list), {}
    for position, pair in enumerate(pairs["rows"]):
        baseline = pair.get("baseline")
        reference = {"input_pair_index": position, "input_pair_sha256": sha(pair),
                     "pair_id": pair.get("pair_id"), "forecast_id": baseline.get("forecast_id") if isinstance(baseline, dict) else None}
        occurrences.append(reference)
        identifier = pair.get("pair_id")
        if not _hash(identifier):
            reference.update(status="EXCLUDED", reason="INVALID_PAIR_ID")
        elif identifier in seen_pairs:
            previous = seen_pairs[identifier]
            if previous["input_pair_sha256"] == reference["input_pair_sha256"]:
                reference.update(status="DUPLICATE", reason="EXACT_PAIR_RECORD")
            else:
                reference.update(status="EXCLUDED", reason="CONFLICTING_PAIR_ID")
                previous.update(status="EXCLUDED", reason="CONFLICTING_PAIR_ID")
        else:
            seen_pairs[identifier] = reference
            if not _hash(reference["forecast_id"]):
                reference.update(status="EXCLUDED", reason="INVALID_FORECAST_ID")
            else:
                try:
                    utc(pair["saved_at"])
                    candidates[reference["forecast_id"]].append((position, pair))
                except (KeyError, TypeError, ValueError):
                    reference.update(status="EXCLUDED", reason="INVALID_PAIR_AVAILABILITY")
    consumed, joined_ids = [], set()
    for identifier, entries in sorted(candidates.items()):
        # Select by original prematch own-presence, before examining outcomes.
        entries.sort(key=lambda item: (utc(item[1]["saved_at"]), item[1]["pair_id"]))
        earliest = utc(entries[0][1]["saved_at"])
        equal_first = [entry for entry in entries if utc(entry[1]["saved_at"]) == earliest]
        content = {sha({key: value for key, value in pair.items() if key not in ("pair_id", "pair_receipt_id", "saved_at")})
                   for _, pair in equal_first}
        position, pair = entries[0]
        reference = occurrences[position]
        for other_position, _ in entries[1:]:
            if occurrences[other_position].get("status") != "EXCLUDED":
                occurrences[other_position].update(status="SUPERSEDED", reason="LATER_OR_IDENTICAL_FIRST_PAIR_NOT_NEW_FORECAST")
        if len(content) != 1:
            for first_position, _ in equal_first:
                occurrences[first_position].update(status="EXCLUDED", reason="CONFLICTING_FIRST_WEATHER_PAIRS")
            continue
        if reference.get("status") == "EXCLUDED":
            continue
        try:
            matches = index.get(identifier, [])
            if not matches:
                raise ValueError("ORIGINAL_NATIVE_FORECAST_MISSING")
            if len({sha(_baseline(row)) for _, row in matches}) != 1:
                raise ValueError("CONFLICTING_ORIGINAL_FORECAST_ID")
            baseline = matches[0][1]
            if sha(_baseline(baseline)) != sha(pair["baseline"]):
                raise ValueError("EXACT_ORIGINAL_BASELINE_CONTENT_MISMATCH")
            _forecast_valid(baseline)
            weather, stratum = _weather(pair, cutoff)
            outcome, receipts = _outcome(_distinct_outcome_receipts(matches), utc(baseline["kickoff"]), cutoff)
            joined_ids.add(identifier)
            status = "PENDING" if outcome is None else "SCORED"
            reference.update(status=status, reason="OUTCOME_NOT_AVAILABLE_AS_OF" if outcome is None else "EXACT_VERIFIED_SOURCE_JOIN")
            consumed.append({"forecast_id": identifier, "pair_id": pair["pair_id"], "pair_receipt_id": pair["pair_receipt_id"],
                "original_baseline_sha256": sha(pair["baseline"]), "weather_context_sha256": pair["weather_context_sha256"],
                "fixture_id": baseline["fixture_id"], "kickoff": baseline["kickoff"], "model_as_of": baseline["model_as_of"],
                "target": pair["target"], "competition_id": "the_stats_api:" + pair["target"]["provider_league_id"],
                "season_id": pair["target"]["provider_season_id"],
                **{key: baseline[key] for key in ("profile_id", "strategy_version", "model_version", "market_key", "selection_key", "period", "settlement", "line")},
                "probability": baseline["probability"], "outcome": None if outcome is None else "WON" if outcome else "LOST",
                "status": status, "weather": weather, "weather_stratum": stratum, "weather_stratum_basis": STRATUM_BASIS, "result_receipt_ids": receipts,
                "baseline_source_receipt_ids": baseline["source_receipt_ids"], "weather_model_probability": None})
        except (KeyError, TypeError, ValueError) as error:
            reason = str(error) if isinstance(error, ValueError) and str(error) in LOCAL_REASONS else "MALFORMED_SOURCE_CONTENT"
            reference.update(status="EXCLUDED", reason=reason)
    assert all(row.get("status") in {"SCORED", "PENDING", "EXCLUDED", "DUPLICATE", "SUPERSEDED"} for row in occurrences)
    counts = dict(Counter(row["status"] for row in occurrences))
    assert sum(counts.values()) == len(pairs["rows"])
    scored = [row for row in consumed if row["status"] == "SCORED"]
    groups = defaultdict(list)
    fields = ("competition_id", "season_id", "profile_id", "strategy_version", "model_version", "market_key",
              "selection_key", "period", "settlement", "line", "weather_stratum")
    for row in scored:
        groups[tuple(row[key] for key in fields)].append(row)
    grouped = [{**dict(zip(fields, key, strict=True)), **_scores(rows), "unique_fixtures": len({row["fixture_id"] for row in rows}),
                "weather_summary": _weather_summary(rows), "weather_stratum_basis": STRATUM_BASIS} for key, rows in sorted(groups.items(), key=lambda item: sha(item[0]))]
    dataset = snapshot(consumed, role="weather_conditioned_baseline_quality_cohort", as_of=cutoff,
        recipe={"version": VERSION, "selection": "FIRST_COMMITTED_PREMATCH_PAIR_PER_ORIGINAL_FORECAST_BEFORE_OUTCOMES",
                "wet_threshold_mm": WEATHER_POLICY["wet_threshold_mm"], "moisture_is_proxy_not_pitch_measurement": True,
                "weather_stratum_basis": STRATUM_BASIS,
                "weather_probability_model": None},
        lineage={"feature_dataset_id": pairs["manifest"]["dataset_id"], "forecast_dataset_id": forecasts["manifest"]["dataset_id"],
                 "feature_source_gaps": feature_source.get("gaps", {}), "forecast_source_gaps": forecast_source.get("gaps", {}),
                 "pair_occurrences": occurrences})
    return _seal({"version": VERSION, "mode": "SHADOW", "decision_effect": "NONE", "as_of": cutoff.isoformat(),
        "status": "MEASURED" if scored else "PENDING_OUTCOMES" if consumed else "NO_ELIGIBLE_WEATHER_PAIRS",
        "source_authentication": PURE_AUTH, "weather_stratum_basis": STRATUM_BASIS, "dataset": dataset, "pair_occurrences": occurrences,
        "accounting": {"input_pair_occurrences": len(pairs["rows"]), "partitions": counts,
            "input_pair_keyset_sha256": sha([[row["input_pair_index"], row["input_pair_sha256"]] for row in occurrences]),
            "native_forecast_occurrences": len(forecasts["rows"]), "native_forecast_keys_sha256": sha([
                [position, row.get("forecast_id"), sha(row)] for position, row in enumerate(forecasts["rows"])]),
            "native_unique_forecasts_without_joined_weather_pair": len(set(index) - joined_ids)},
        "baseline_scores": _scores(scored), "weather_conditioned_groups": grouped,
        "weather_feature_summary": _weather_summary(consumed),
        "unique_scored_fixtures": len({row["fixture_id"] for row in scored}),
        "source_inventory_complete": not feature_source.get("gaps") and not forecast_source.get("gaps"),
        "assumptions": ["DESCRIPTIVE_CORRELATED_FORECAST_POPULATION_NOT_INDEPENDENT_HOLDOUT",
                        "WEATHER_STRATA_NOT_CAUSAL_OR_PROFIT_EFFECT", "NO_NEW_WEATHER_PROBABILITY_OR_RANKING_WEIGHT"],
        "heldout_weather_comparison_complete": False, "live_promotion_allowed": False,
        "additional_model_fits": 0, "external_requests": 0, "production_writes": 0})


def inspect_weather_forecast_quality(root, *, as_of, max_seconds=30):
    """Optional actual-source reader; failures are local diagnostics, no retry."""
    if type(max_seconds) is not int or not 1 <= max_seconds <= 30:
        raise ValueError("INVALID_WEATHER_LEARNING_TIME_BUDGET")
    cutoff, deadline = utc(as_of), monotonic() + max_seconds
    if cutoff > datetime.now(UTC):
        return _seal({"version": VERSION, "mode": "SHADOW", "decision_effect": "NONE", "status": "UNAVAILABLE",
                      "reason": "FUTURE_AS_OF", "heldout_weather_comparison_complete": False, "external_requests": 0, "production_writes": 0})
    try:
        from goal_analysis.jobs import native_forecast_capture, weather_feature_capture

        root = Path(root).resolve()
        pairs = weather_feature_capture.read_pairs(root, as_of=cutoff, max_seconds=max_seconds)
        remaining = int(deadline - monotonic())
        if remaining < 1:
            raise ValueError("WEATHER_LEARNING_SOURCE_BUDGET")
        forecasts = native_forecast_capture.read_forecasts(root, as_of=cutoff, max_seconds=min(30, remaining))
        if monotonic() >= deadline:
            raise ValueError("WEATHER_LEARNING_SOURCE_BUDGET")
        result = summarize_source_content(pairs, forecasts, as_of=cutoff)
        if monotonic() >= deadline:
            raise ValueError("WEATHER_LEARNING_SOURCE_BUDGET")
        result.pop("sha256")
        result["source_authentication"] = "ACTUAL_PHYSICAL_SOURCE_READERS" if pairs["dataset"]["rows"] else "ACTUAL_READERS_EMPTY_NO_WEATHER_STORE_EVIDENCE"
        result["source_readers"] = ["weather_feature_capture.read_pairs", "native_forecast_capture.read_forecasts"]
        result["automatic_retry"] = False
        return _seal(result)
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error, TimeoutError) as error:
        return _seal({"version": VERSION, "mode": "SHADOW", "decision_effect": "NONE", "status": "UNAVAILABLE",
            "reason": "SOURCE_READER_BUDGET" if isinstance(error, ValueError) and "BUDGET" in str(error) else "WEATHER_LEARNING_SOURCE_UNAVAILABLE",
            "source_authentication": "NOT_ESTABLISHED", "heldout_weather_comparison_complete": False,
            "additional_model_fits": 0, "external_requests": 0, "production_writes": 0, "automatic_retry": False})
