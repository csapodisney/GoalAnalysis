"""Offline SHADOW probability ensemble; never imported by the live ticket path.

Rates become a separately identified independent-Poisson challenger. Calibration
accepts witnessed prematch forecasts AND later witnessed outcomes, not today's
reconstruction of an old forecast. Source readers must verify receipts against
their actual stores before passing envelopes to this pure boundary.
"""
import math
import re
from collections import Counter
from copy import deepcopy
from datetime import timedelta

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.leakage import model_inputs
from goal_analysis.normalization.training_datasets import sha, snapshot, validate

VERSION = "xg-ensemble-shadow-v1"
XG_VERSION = "xg-independent-poisson-shadow-v1"
FAMILIES = {"1X2_FT_REGULATION": ("home", "draw", "away"),
            "TOTAL_2.5_FT_REGULATION": ("over25", "under25"),
            "BTTS_FT_REGULATION": ("btts_yes", "btts_no")}
TEMPERATURES = (.5, .67, .8, 1., 1.25, 1.5, 2., 3.)
RECIPE = {"min_calibration": 30, "min_validation": 20, "min_per_class": 2,
          "calibration_fraction": .6, "density_prior_matches": 30,
          "temperatures": list(TEMPERATURES), "lockout_minutes": 15}


def _sealed(value):
    result = deepcopy(value)
    result["result_sha256"] = sha(value)
    return result


def _verify(value):
    if not isinstance(value, dict):
        raise TypeError("INVALID_SHADOW_ARTIFACT")
    unsigned = {k: v for k, v in value.items() if k != "result_sha256"}
    if value.get("result_sha256") != sha(unsigned):
        raise ValueError("SHADOW_ARTIFACT_HASH_MISMATCH")


def _number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("INVALID_ENSEMBLE_NUMBER")
    return value


def _identity(row):
    for key, prefix in (("fixture_id", "mt"), ("competition_id", "comp"), ("season_id", "sn")):
        if not isinstance(row.get(key), str) or not re.fullmatch(prefix + r"_\d{1,30}", row[key]):
            raise ValueError("INVALID_ENSEMBLE_NATIVE_ID")


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("CONTENT_BOUND_SOURCE_REQUIRED")


def _vector(probabilities, keys):
    if not isinstance(probabilities, dict) or set(probabilities) != set(keys):
        raise ValueError("EXACT_PROBABILITY_FAMILY_REQUIRED")
    values = [_number(probabilities[k], 0, 1) for k in keys]
    if not math.isclose(math.fsum(values), 1, rel_tol=0, abs_tol=1e-10):
        raise ValueError("PROBABILITIES_MUST_SUM_TO_ONE")
    return values


def predict_xg(fit, fixture, *, as_of):
    """Separate raw probabilities, never a calibrated forecast or score boost.

    Both actual venue features are required. The product of venue-normalized
    attack/defense strengths is a new SHADOW Poisson assumption to backtest.
    """
    _verify(fit)
    if fit.get("version") != "xg-rates-shadow-v1" or fit.get("mode") != "SHADOW":
        raise ValueError("XG_RATE_VERSION_REQUIRED")
    validate(fit["dataset"])
    _identity(fixture)
    now = utc(as_of)
    if utc(fit["as_of"]) > now:
        raise ValueError("FUTURE_XG_FIT")
    if now > utc(fixture["kickoff"]) - timedelta(minutes=15):
        raise ValueError("PREMATCH_LOCKOUT")
    if fixture["fixture_id"] in {r["fixture_id"] for r in fit["dataset"]["rows"]}:
        raise ValueError("TARGET_ALREADY_IN_XG_FIT")
    result = {"version": XG_VERSION, "mode": "SHADOW", "live_decision_effect": False,
              "calibrated": False, "as_of": now.isoformat(), "fixture_id": fixture["fixture_id"],
              "competition_id": fixture["competition_id"], "season_id": fixture["season_id"],
              "training_as_of": fit["as_of"],
              "input_dataset_id": fit["dataset"]["manifest"]["dataset_id"],
              "source_fit_sha256": fit["result_sha256"], "probabilities": None,
              "league_xg_matches": 0, "status": "INSUFFICIENT_XG_HISTORY"}
    if fixture.get("neutral_venue") is not False:
        return _sealed({**result, "status": "UNPROVEN_NONNEUTRAL_VENUE"})
    for key in ("home_team_id", "away_team_id"):
        if not isinstance(fixture.get(key), str) or not re.fullmatch(r"tm_\d{1,30}", fixture[key]):
            raise ValueError("INVALID_ENSEMBLE_NATIVE_TEAM")
    if fixture["home_team_id"] == fixture["away_team_id"]:
        raise ValueError("IDENTICAL_ENSEMBLE_TEAMS")
    league = next((r for r in fit["leagues"] if
                   (r["competition_id"], r["season_id"]) ==
                   (fixture["competition_id"], fixture["season_id"])), None)
    if league is None:
        return _sealed(result)
    result["league_xg_matches"] = league["matches"]
    if league["matches"] < 3:
        return _sealed(result)
    teams = {t["team_id"]: t for t in league["teams"]}
    home = teams.get(fixture["home_team_id"], {}).get("home", {})
    away = teams.get(fixture["away_team_id"], {}).get("away", {})
    if any(t.get("status") != "AVAILABLE_SHADOW" for t in (home, away)):
        return _sealed(result)
    lh = league["league_home_xg"] * home["attack_strength"] * away["defense_strength"]
    la = league["league_away_xg"] * away["attack_strength"] * home["defense_strength"]
    # Bounded diagnostic scope, no silent clipping or missing-feature imputation.
    _number(lh, 0, 40); _number(la, 0, 40)

    def poisson(rate):
        values = [math.exp(-rate)]
        for n in range(1, 161):
            values.append(values[-1] * rate / n)
        if abs(1-math.fsum(values)) > 1e-12:
            raise ValueError("XG_SCORE_GRID_TAIL_TOO_LARGE")
        return values

    h, a = poisson(lh), poisson(la)
    ph = math.fsum(p*math.fsum(a[:i]) for i, p in enumerate(h))
    pd = math.fsum(p*q for p, q in zip(h, a))
    pa = math.fsum(q*math.fsum(h[:i]) for i, q in enumerate(a))
    total = math.fsum((ph, pd, pa))
    under = math.exp(-lh-la) * (1 + lh+la + (lh+la)**2/2)
    btts = (-math.expm1(-lh)) * (-math.expm1(-la))
    result.update(status="RAW_XG_PROBABILITIES_SHADOW", lambdas={"home": lh, "away": la},
                  probabilities={"home": ph/total, "draw": pd/total, "away": pa/total,
                                 "over25": 1-under, "under25": under,
                                 "btts_yes": btts, "btts_no": 1-btts})
    for keys in FAMILIES.values():
        _vector({k: result["probabilities"][k] for k in keys}, keys)
    return _sealed(result)


def _forecast(envelope, *, as_of, family, competition, season):
    row = model_inputs([envelope], as_of=as_of)[0]
    _digest(envelope.get("source_sha256")); _digest(envelope.get("receipt_id"))
    _identity(row)
    keys = FAMILIES[family]
    if (row.get("family"), row["competition_id"], row["season_id"]) != (family, competition, season):
        raise ValueError("ENSEMBLE_SCOPE_MISMATCH")
    issued = utc(row["issued_at"])
    # The forecast receipt must already have existed when the forecast was issued.
    model_inputs([envelope], as_of=issued)
    if issued > utc(as_of) or issued > utc(row["kickoff"]) - timedelta(minutes=15):
        raise ValueError("INVALID_PREMATCH_FORECAST_TIME")
    count = row.get("league_xg_matches")
    if type(count) is not int or count < 0:
        raise ValueError("INVALID_XG_DENSITY")
    goals = ("GAUSS", "KEPLER", "LAPLACE") if len(keys) == 3 else ("GAUSS", "LAPLACE")
    components = row.get("components", {})
    if not set(goals) <= set(components) or set(components) - {*goals, "XG"}:
        raise ValueError("NUMERIC_COMPONENTS_REQUIRED_NO_HEURISTICS")
    for name, component in components.items():
        _vector(component.get("probabilities"), keys)
        _digest(component.get("input_dataset_id"))
        if not isinstance(component.get("model_version"), str) or not component["model_version"]:
            raise ValueError("MODEL_VERSION_REQUIRED")
        if utc(component["training_as_of"]) > issued:
            raise ValueError("FUTURE_COMPONENT_TRAINING")
        if name == "XG" and component["model_version"] != XG_VERSION:
            raise ValueError("UNSUPPORTED_XG_PROBABILITY_MODEL")
    return row


def _temperature(values, temperature):
    logits = [math.log(max(1e-15, p))/temperature for p in values]
    maximum = max(logits)
    weights = [math.exp(v-maximum) for v in logits]
    total = math.fsum(weights)
    return [v/total for v in weights]


def _metrics(predictions, outcomes):
    n = len(outcomes)
    return {"matches": n,
            "log_loss": math.fsum(-math.log(max(1e-15, p[y])) for p, y in zip(predictions, outcomes))/n,
            "brier": math.fsum(math.fsum((v-int(i == y))**2 for i, v in enumerate(p))
                               for p, y in zip(predictions, outcomes))/n}


def _blend(row, parameters, keys, *, use_xg=True, raw_control=False):
    names = [n for n in parameters if n != "XG"]
    if raw_control:
        weights = {n: 1/len(names) for n in names}
    else:
        weights = {n: 1/max(1e-9, parameters[n]["calibration_log_loss"]) for n in names}
        if use_xg and row["league_xg_matches"] >= 3 and "XG" in row["components"]:
            # Fitted reliability multiplied by observed league density, never a fixed xG share.
            density = row["league_xg_matches"] / (row["league_xg_matches"] + RECIPE["density_prior_matches"])
            weights["XG"] = density/max(1e-9, parameters["XG"]["calibration_log_loss"])
        total = math.fsum(weights.values())
        weights = {n: w/total for n, w in weights.items()}
    probabilities = [0.0]*len(keys)
    for name, weight in weights.items():
        p = _vector(row["components"][name]["probabilities"], keys)
        if not raw_control:
            p = _temperature(p, parameters[name]["temperature"])
        for i, value in enumerate(p):
            probabilities[i] += weight*value
    return probabilities, {**weights, "XG": weights.get("XG", 0.0)}


def fit_ensemble(envelopes, *, as_of, family, competition_id, season_id):
    """Fit temperature/reliability on a purged prefix, evaluate an untouched suffix.

    This is one chronological diagnostic holdout, not a 30-day walk-forward
    promotion certificate. No refit uses validation labels. Exact numerical
    model versions and market/league/season scopes must match throughout.
    """
    if family not in FAMILIES:
        raise ValueError("UNSUPPORTED_ENSEMBLE_FAMILY")
    _identity({"fixture_id": "mt_0", "competition_id": competition_id, "season_id": season_id})
    cutoff, keys = utc(as_of), FAMILIES[family]
    rows = model_inputs(envelopes, as_of=cutoff)
    usable, seen, versions, issues = [], set(), None, Counter()
    for envelope, row in zip(envelopes, rows):
        _digest(envelope.get("source_sha256")); _digest(envelope.get("receipt_id"))
        forecast = _forecast(row["forecast"], as_of=cutoff, family=family,
                             competition=competition_id, season=season_id)
        fixture = forecast["fixture_id"]
        if fixture in seen:
            raise ValueError("DUPLICATE_CALIBRATION_FIXTURE")
        seen.add(fixture)
        if row["outcome"] not in keys or not utc(forecast["kickoff"]) < utc(row["settled_at"]) <= utc(envelope["times"]["observed_at"]):
            raise ValueError("INVALID_SETTLED_OUTCOME")
        if forecast["league_xg_matches"] < 3 or "XG" not in forecast["components"]:
            issues["NO_QUALIFIED_PAIRED_XG_FORECAST"] += 1
            continue
        current = {n: c["model_version"] for n, c in forecast["components"].items()}
        if versions is not None and versions != current:
            raise ValueError("MIXED_CALIBRATION_MODEL_VERSIONS")
        versions = current
        usable.append((forecast, keys.index(row["outcome"]), envelope))
    usable.sort(key=lambda r: (utc(r[0]["issued_at"]), r[0]["fixture_id"]))
    dataset = snapshot([r[2] for r in usable], role="witnessed_probability_calibration",
        as_of=cutoff, recipe={"version": VERSION, **RECIPE, "family": family},
        lineage=sorted(envelopes, key=lambda e: e["record_id"]))
    result = {"version": VERSION, "mode": "SHADOW", "live_decision_effect": False,
              "promotion_allowed": False, "as_of": cutoff.isoformat(), "family": family,
              "competition_id": competition_id, "season_id": season_id,
              "recipe": RECIPE, "dataset": dataset, "input_rows": len(rows),
              "paired_rows": len(usable), "issues": dict(issues), "parameters": None,
              "model_versions": versions, "validation": None,
              "status": "INSUFFICIENT_WITNESSED_CALIBRATION", "validation_improved": False}
    if len(usable) < RECIPE["min_calibration"] + RECIPE["min_validation"]:
        return _sealed(result)
    split = min(int(len(usable)*RECIPE["calibration_fraction"]), len(usable)-RECIPE["min_validation"])
    validation = usable[split:]
    first_validation = utc(validation[0][0]["issued_at"])
    train = [r for r in usable[:split] if utc(r[2]["times"]["available_at"]) < first_validation]
    result.update(calibration_rows=len(train), validation_rows=len(validation),
                  purged_rows=split-len(train), first_validation_issued_at=first_validation.isoformat())
    counts = Counter(r[1] for r in train)
    if len(train) < RECIPE["min_calibration"] or any(counts[i] < RECIPE["min_per_class"] for i in range(len(keys))):
        return _sealed({**result, "status": "INSUFFICIENT_PURGED_CALIBRATION_OR_CLASSES"})
    parameters = {}
    labels = [r[1] for r in train]
    for name in versions:
        predictions = [_vector(r[0]["components"][name]["probabilities"], keys) for r in train]
        candidates = [(_metrics([_temperature(p, t) for p in predictions], labels)["log_loss"], t)
                      for t in TEMPERATURES]
        loss, temperature = min(candidates, key=lambda pair: (pair[0], abs(pair[1]-1), pair[1]))
        parameters[name] = {"temperature": temperature, "calibration_log_loss": loss}
    outcomes = [r[1] for r in validation]
    metrics = {name: _metrics([_blend(r[0], parameters, keys, **options)[0] for r in validation], outcomes)
               for name, options in (("raw_goal_control", {"raw_control": True}),
                                     ("calibrated_goal_control", {"use_xg": False}),
                                     ("ensemble", {}))}
    improved = all(metrics["ensemble"][m] <= metrics[base][m] for base in
                   ("raw_goal_control", "calibrated_goal_control") for m in ("brier", "log_loss"))
    improved = improved and any(metrics["ensemble"][m] < metrics["calibrated_goal_control"][m]
                                for m in ("brier", "log_loss"))
    result.update(status="CALIBRATED_DIAGNOSTIC_SHADOW", parameters=parameters,
                  validation=metrics, validation_improved=improved,
                  evaluation_method="PURGED_CHRONOLOGICAL_HOLDOUT_NOT_PROMOTION_WALK_FORWARD")
    return _sealed(result)


def predict_ensemble(artifact, forecast_envelope, *, as_of):
    _verify(artifact)
    if artifact.get("version") != VERSION or artifact.get("mode") != "SHADOW":
        raise ValueError("SHADOW_ENSEMBLE_VERSION_REQUIRED")
    validate(artifact["dataset"])
    if utc(artifact["as_of"]) > utc(as_of):
        raise ValueError("FUTURE_ENSEMBLE_FIT")
    row = _forecast(forecast_envelope, as_of=as_of, family=artifact["family"],
                    competition=artifact["competition_id"], season=artifact["season_id"])
    if utc(artifact["as_of"]) > utc(row["issued_at"]):
        raise ValueError("ENSEMBLE_FIT_AFTER_FORECAST")
    if utc(as_of) > utc(row["kickoff"]) - timedelta(minutes=15):
        raise ValueError("PREMATCH_LOCKOUT")
    used = {r["payload"]["forecast"]["payload"]["fixture_id"] for r in artifact["dataset"]["rows"]}
    if row["fixture_id"] in used:
        raise ValueError("TARGET_ALREADY_IN_CALIBRATION")
    result = {"version": VERSION, "mode": "SHADOW", "live_decision_effect": False,
              "promotion_allowed": False, "fixture_id": row["fixture_id"],
              "family": artifact["family"], "artifact_sha256": artifact["result_sha256"],
              "validation_improved": artifact["validation_improved"],
              "forecast_sha256": forecast_envelope["payload_sha256"],
              "probabilities": None, "weights": {"XG": 0.0},
              "status": "UNCALIBRATED_NO_ENSEMBLE_DECISION"}
    if artifact["parameters"] is None:
        return _sealed(result)
    if any(c["model_version"] != artifact["model_versions"].get(n) for n, c in row["components"].items()):
        raise ValueError("UNCALIBRATED_COMPONENT_VERSION")
    probabilities, weights = _blend(row, artifact["parameters"], FAMILIES[artifact["family"]])
    result.update(status="CALIBRATED_DIAGNOSTIC_SHADOW", weights=weights,
                  probabilities=dict(zip(FAMILIES[artifact["family"]], probabilities)))
    return _sealed(result)
