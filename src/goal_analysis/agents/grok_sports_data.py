"""Bounded numerical views of already collected sports data; no I/O or refetching."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

_SIDES = r"(?:hazai oldal|home) (\d{1,3})/(\d{1,3}), (?:vendég oldal|away) (\d{1,3})/(\d{1,3})"
_COUNTS = {
    "historical_20": re.compile(r"(?:^|; )historical_20: " + _SIDES + r"\."),
    "venue_10": re.compile(r"(?:^|; )venue_10: " + _SIDES + r"\."),
    "last5": re.compile(r"(?:Utolsó 5|last5): " + _SIDES + r";"),
}
_WEIGHTS = re.compile(r"(?:helyszíni/aktuális arány|venue/current): (\d{1,3})/(\d{1,3})\.")
_PREFIX = (r"(?:(?:FULL_TIME|FIRST_HALF|SECOND_HALF) [a-z][a-z0-9_]{0,79} / "
           r"[a-z0-9][a-z0-9_.+-]{0,79}; )?")
_BOILERPLATE = r"(?: Megfigyelt piaci mintázat, nem becsült nyerési esély\.)?"
_WHOLE = {
    "historical_20": re.compile(_PREFIX + r"historical_20: " + _SIDES + r"\." + _BOILERPLATE),
    "venue_10": re.compile(_PREFIX + r"venue_10: " + _SIDES + r"\."
        + r"(?: (?:Utolsó 5|last5): " + _SIDES + r"; " + _WEIGHTS.pattern + r")?"
        + _BOILERPLATE),
}
_MARKETS = {"home", "draw", "away", "over25", "under25", "btts_yes"}
_MODELS = {"gauss", "kepler", "laplace"}
_COUNTERS = {"training_count", "history_count"}
_VALIDATION_COUNTS = {"sample_size", "training_sample_size"}
_VALIDATION_METRICS = {"brier_multiclass_sum": 2, "rps": 1, "log_loss": 100}
_VALIDATION_TIMES = {"training_end", "validation_start", "validation_end"}
_MODEL_KEYS = {
    "probabilities", "stability", "cutoff", "training_count", "history_count",
    "history_hash", "windows", "window_training_counts", "closure_delay_hours", "validation",
    "stability_definition",
}
# These definitions mirror models/gauss.py, kepler.py and laplace.py. They are
# descriptive model metadata, never statistical independence or calibration claims.
_STABILITY_DEFINITIONS = {
    "gauss": "max_minus_min_probability_across_four_fits",
    "kepler": "not_measured",
    "laplace": "not_measured_single_fixed_model",
}


def _number(value, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError("Invalid sports metric")
    return value


def _count(value, maximum=1_000_000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("Invalid sports count")
    return value


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Invalid sports timestamp")
        return stamp.astimezone(UTC)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid sports timestamp") from exc


def validate_history_counts(value):
    """Validate observed hits/sample sizes, never treat them as model probabilities."""
    allowed = set(_COUNTS) | {"venue_current_weights_pct"}
    if not isinstance(value, dict) or not value or not set(value) <= allowed:
        raise ValueError("Invalid history counts")
    if ("historical_20" in value and len(value) != 1) or (
            "historical_20" not in value and "venue_10" not in value):
        raise ValueError("Invalid history count group")
    clean = {}
    for key, maximum in (("historical_20", 20), ("venue_10", 10), ("last5", 5)):
        if key not in value:
            continue
        sides = value[key]
        if not isinstance(sides, dict) or set(sides) != {"home", "away"}:
            raise ValueError("Invalid history sides")
        clean[key] = {}
        for side in ("home", "away"):
            pair = sides[side]
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("Invalid history pair")
            hits, sample = (_count(item, maximum) for item in pair)
            if hits > sample:
                raise ValueError("History hits exceed sample")
            clean[key][side] = [hits, sample]
    if "venue_current_weights_pct" in value:
        weights = value["venue_current_weights_pct"]
        if not isinstance(weights, list) or len(weights) != 2:
            raise ValueError("Invalid history weights")
        clean["venue_current_weights_pct"] = [_count(item, 100) for item in weights]
        if sum(weights) != 100:
            raise ValueError("Invalid history weights")
    return clean


def history_counts(statement, category):
    """Parse only the generated historical/venue grammar, before prose truncation."""
    key = {"historical": "historical_20", "venue_form": "venue_10"}.get(category)
    if key is None or not isinstance(statement, str) or len(statement) > 4000:
        return None
    statement = statement.strip()
    # A matching clause is insufficient: omitted suffixes could contain injuries,
    # additional sample counts or other numerical evidence that prose still needs.
    if not _WHOLE[key].fullmatch(statement):
        return None
    matches = list(_COUNTS[key].finditer(statement))
    if len(matches) != 1:
        return None
    values = [int(item) for item in matches[0].groups()]
    result = {key: {"home": values[:2], "away": values[2:]}}
    if key == "venue_10":
        for name, pattern in (("last5", _COUNTS["last5"]),
                              ("venue_current_weights_pct", _WEIGHTS)):
            matches = list(pattern.finditer(statement))
            if len(matches) > 1:
                return None
            if matches:
                values = [int(item) for item in matches[0].groups()]
                result[name] = ({"home": values[:2], "away": values[2:]}
                                if name == "last5" else values)
    try:
        return validate_history_counts(result)
    except ValueError:
        return None


def _market_numbers(value):
    if not isinstance(value, dict) or not set(value) <= _MARKETS:
        raise ValueError("Invalid model markets")
    return {key: _number(number, 1) for key, number in value.items()}


def validate_model_context(value, cutoff):
    """Strict provider-boundary whitelist for fixture-level saved model summaries."""
    limit = _stamp(cutoff)
    if (not isinstance(value, dict)
            or set(value) != {"period", "calibration", "independent_evidence", "models"}
            or value["period"] != "FULL_TIME"
            or value["calibration"] != "unvalidated" or value["independent_evidence"] is not False
            or not isinstance(value["models"], dict) or not value["models"]
            or not set(value["models"]) <= _MODELS):
        raise ValueError("Invalid model context")
    models = {}
    for name, row in value["models"].items():
        if (not isinstance(row, dict) or not set(row) <= _MODEL_KEYS
                or not {"probabilities", "cutoff"} <= set(row)):
            raise ValueError("Invalid model summary")
        stamp = _stamp(row["cutoff"])
        if stamp > limit:
            raise ValueError("Future model summary")
        clean = {"probabilities": _market_numbers(row["probabilities"]),
                 "cutoff": stamp.isoformat()}
        if not clean["probabilities"]:
            raise ValueError("Empty model probabilities")
        if "stability" in row:
            clean["stability"] = _market_numbers(row["stability"])
        if "stability_definition" in row:
            if row["stability_definition"] != _STABILITY_DEFINITIONS[name]:
                raise ValueError("Invalid model stability definition")
            clean["stability_definition"] = row["stability_definition"]
        for key in _COUNTERS & set(row):
            clean[key] = _count(row[key])
        if "history_hash" in row:
            if not isinstance(row["history_hash"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["history_hash"]):
                raise ValueError("Invalid model history hash")
            clean["history_hash"] = row["history_hash"]
        if "windows" in row:
            windows = row["windows"]
            if (not isinstance(windows, list) or not 1 <= len(windows) <= 8
                    or any(type(item) is not int or not 1 <= item <= 3650 for item in windows)
                    or len(set(windows)) != len(windows)):
                raise ValueError("Invalid model windows")
            clean["windows"] = list(windows)
        if "window_training_counts" in row:
            counts = row["window_training_counts"]
            allowed = {str(item) for item in clean.get("windows", [])}
            if not isinstance(counts, dict) or not set(counts) <= allowed:
                raise ValueError("Invalid window counts")
            clean["window_training_counts"] = {key: _count(number) for key, number in counts.items()}
        if "closure_delay_hours" in row:
            clean["closure_delay_hours"] = _number(row["closure_delay_hours"], 168)
        if "validation" in row:
            raw = row["validation"]
            allowed = _VALIDATION_COUNTS | set(_VALIDATION_METRICS) | _VALIDATION_TIMES
            if not isinstance(raw, dict) or not set(raw) <= allowed:
                raise ValueError("Invalid model validation")
            checked = {key: _count(raw[key]) for key in _VALIDATION_COUNTS & set(raw)}
            for key in set(_VALIDATION_METRICS) & set(raw):
                checked[key] = _number(raw[key], _VALIDATION_METRICS[key])
            times = {}
            for key in _VALIDATION_TIMES & set(raw):
                times[key] = _stamp(raw[key])
                if times[key] > stamp:
                    raise ValueError("Future validation sample")
                checked[key] = times[key].isoformat()
            ordered = [times[key] for key in ("training_end", "validation_start", "validation_end")
                       if key in times]
            if ordered != sorted(ordered):
                raise ValueError("Invalid validation chronology")
            clean["validation"] = checked
        models[name] = clean
    return {"period": "FULL_TIME", "calibration": "unvalidated",
            "independent_evidence": False, "models": models}


def model_context(report, fixture_id, cutoff):
    """Read already-saved predictions once per fixture; skip invalid or future rows."""
    source = report.get("numeric_models", {}) if isinstance(report, dict) else {}
    if not isinstance(source, dict):
        return {}
    models = {}
    for name in sorted(_MODELS):
        model = source.get(name, {})
        rows = model.get("predictions", []) if isinstance(model, dict) else []
        if not isinstance(rows, list) or len(rows) > 2000:
            continue
        matched = [row for row in rows if isinstance(row, dict)
                   and row.get("fixture_id") == fixture_id]
        if len(matched) != 1:
            continue
        prediction = matched[0]
        detail = prediction.get("model_details", {})
        if not isinstance(detail, dict):
            continue
        row = {key: detail[key] for key in _MODEL_KEYS - {"probabilities", "stability", "validation"}
               if key in detail}
        if "cutoff" not in row and "model_cutoff" in report:
            row["cutoff"] = report["model_cutoff"]
        row.setdefault("stability_definition", _STABILITY_DEFINITIONS[name])
        for key in ("probabilities", "stability"):
            if key in prediction:
                row[key] = prediction[key]
        validation = detail.get("validation")
        if isinstance(validation, dict):
            row["validation"] = {key: validation[key] for key in
                                 _VALIDATION_COUNTS | set(_VALIDATION_METRICS) | _VALIDATION_TIMES
                                 if key in validation}
        candidate = {"period": "FULL_TIME", "calibration": "unvalidated", "independent_evidence": False,
                     "models": {name: row}}
        try:
            models.update(validate_model_context(candidate, cutoff)["models"])
        except ValueError:
            continue
    return ({"period": "FULL_TIME", "calibration": "unvalidated",
             "independent_evidence": False, "models": models}
            if models else {})


_FORM_WINDOWS = {"historical_20": 20, "recent_5": 5, "venue_10": 10}
_FORM_FIELDS = {"sample_size", "goals_for_mean", "goals_against_mean", "results_newest_first"}
_WORKLOAD_FIELDS = {"observed_matches_7d", "observed_matches_14d", "hours_between_kickoffs", "scope"}
_WORKLOAD_SCOPE = "OBSERVED_COMPLETED_MATCHES_IN_COLLECTED_COMPETITIONS"
_PERIODS = {"FULL_TIME", "FIRST_HALF", "SECOND_HALF"}


def validate_form_context(value, cutoff):
    """Bound saved team form; kickoff spacing is not inferred actual player rest."""
    limit = _stamp(cutoff)
    if (not isinstance(value, dict)
            or not {"period", "independent_evidence", "profiles"} <= set(value)
            or not set(value) <= {"period", "independent_evidence", "profiles", "sources"}
            or not isinstance(value["period"], str) or value["period"] not in _PERIODS
            or value["independent_evidence"] is not False
            or not isinstance(value["profiles"], dict) or not value["profiles"]
            or not set(value["profiles"]) <= {"home", "away"}):
        raise ValueError("Invalid form context")
    clean = {"period": value["period"], "independent_evidence": False, "profiles": {}}
    for side, raw in value["profiles"].items():
        if (not isinstance(raw, dict) or not raw
                or not set(raw) <= set(_FORM_WINDOWS) | {"observed_workload"}):
            raise ValueError("Invalid form profile")
        profile = {}
        for key, maximum in _FORM_WINDOWS.items():
            if key not in raw:
                continue
            window = raw[key]
            if (not isinstance(window, dict) or not set(window) <= _FORM_FIELDS
                    or "sample_size" not in window):
                raise ValueError("Invalid form sample")
            sample = _count(window["sample_size"], maximum)
            checked = {"sample_size": sample}
            for field in ("goals_for_mean", "goals_against_mean"):
                if field in window:
                    if sample == 0:
                        raise ValueError("Goal mean without sample")
                    checked[field] = _number(window[field], 100)
            if "results_newest_first" in window:
                results = window["results_newest_first"]
                if (not isinstance(results, list) or len(results) != sample
                        or any(not isinstance(item, str) or item not in {"W", "D", "L"}
                               for item in results)):
                    raise ValueError("Invalid form results")
                checked["results_newest_first"] = list(results)
            profile[key] = checked
        if "observed_workload" in raw:
            workload = raw["observed_workload"]
            if (not isinstance(workload, dict) or not set(workload) <= _WORKLOAD_FIELDS
                    or workload.get("scope") != _WORKLOAD_SCOPE):
                raise ValueError("Invalid observed workload")
            checked = {"scope": _WORKLOAD_SCOPE}
            for key in ("observed_matches_7d", "observed_matches_14d"):
                if key in workload:
                    checked[key] = _count(workload[key], 1000)
            if ("observed_matches_7d" in checked and "observed_matches_14d" in checked
                    and checked["observed_matches_7d"] > checked["observed_matches_14d"]):
                raise ValueError("Invalid nested workload counts")
            if "hours_between_kickoffs" in workload:
                checked["hours_between_kickoffs"] = _number(workload["hours_between_kickoffs"], 100000)
            profile["observed_workload"] = checked
        clean["profiles"][side] = profile
    if "sources" in value:
        sources = value["sources"]
        if not isinstance(sources, list) or not 1 <= len(sources) <= 12:
            raise ValueError("Invalid form sources")
        clean["sources"] = []
        seen = set()
        for source in sources:
            if not isinstance(source, dict) or set(source) != {"history_sha256", "observed_at"}:
                raise ValueError("Invalid form source")
            digest = source["history_sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("Invalid form history hash")
            observed = _stamp(source["observed_at"])
            if observed > limit:
                raise ValueError("Future form source")
            identity = (digest, observed.isoformat())
            if identity in seen:
                raise ValueError("Duplicate form source")
            seen.add(identity)
            clean["sources"].append({"history_sha256": digest, "observed_at": observed.isoformat()})
    return clean


def form_context(leg, cutoff):
    """Extract compact form already present on a leg without copying text or raw records."""
    if not isinstance(leg, dict):
        return {}
    context = leg.get("historical_context")
    if not isinstance(context, dict) or context.get("status") != "CALCULATED":
        return {}
    period = context.get("period", leg.get("period"))
    if period != leg.get("period", period):
        return {}
    profiles = context.get("profiles")
    if not isinstance(profiles, dict):
        return {}
    value = {"period": period, "independent_evidence": False, "profiles": {}}
    for side in ("home", "away"):
        raw = profiles.get(side)
        if not isinstance(raw, dict):
            continue
        profile = {}
        for key in _FORM_WINDOWS:
            window = raw.get(key)
            if isinstance(window, dict):
                profile[key] = {field: window[field] for field in _FORM_FIELDS
                                if field in window and window[field] is not None}
        workload = raw.get("observed_workload")
        if isinstance(workload, dict):
            profile["observed_workload"] = {field: workload[field] for field in _WORKLOAD_FIELDS
                                           if field in workload and workload[field] is not None}
        if profile:
            value["profiles"][side] = profile
    sources = {}
    evidence = leg.get("evidence", [])
    if isinstance(evidence, list):
        for row in evidence[:12]:
            if not isinstance(row, dict):
                continue
            source = row.get("source_id")
            if (row.get("category") not in {"historical", "venue_form"}
                    or not isinstance(source, str)
                    or not re.fullmatch(r"history-sha256:[a-f0-9]{64}", source)):
                continue
            try:
                observed = _stamp(row.get("observed_at")).isoformat()
            except ValueError:
                return {}
            sources[(source[15:], observed)] = {"history_sha256": source[15:], "observed_at": observed}
    if sources:
        value["sources"] = [sources[key] for key in sorted(sources)]
    try:
        return validate_form_context(value, cutoff)
    except (ValueError, TypeError):
        return {}
