"""Local, auditable weather associations; no calls, training service or ticket mutation.

Research motivates *testing* weather effects, never supplies invented betting coefficients.
Actual historical weather can support a retrospective association. Ranking remains neutral
until an untouched chronological holdout also improves using archived pre-match forecasts.
All effects are observational and the multipliers are heuristics, not win probabilities.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from itertools import groupby
from statistics import NormalDist

VERSION = "weather-expert-v1"
SCIENCE_BASIS = ("PMC11474995", "APPLSCI_10_19_6750", "STATPAIR_WEATHER")
# Predeclared engineering choices, not fitted coefficients from the cited papers.
POLICY = {
    "lookback_days": 3 * 365,
    "elo_initial": 1500.0,
    "elo_k": 20.0,
    "elo_scale": 400.0,
    "elo_min_prior_matches": 20,
    "opponent_elo_tolerance": 100.0,
    "relative_elo_tolerance": 100.0,
    "result_delay_hours": 2,
    "temperature_tolerance_c": 5.0,
    "wind_tolerance_kmh": 10.0,
    "humidity_tolerance_pct": 20.0,
    "wet_threshold_mm": 0.1,
    "forecast_freshness_hours": 6,
    "kickoff_tolerance_minutes": 60,
    "prematch_lockout_minutes": 15,
    "minimum_analogs": 20,
    "minimum_controls": 30,
    "minimum_ess": 15.0,
    "shrinkage_prior_matches": 20.0,
    "recency_half_life_days": 365.0,
    "variance_floor": 0.25,
    "familywise_alpha": 0.05,
    "holdout_fraction": 0.30,
    "minimum_holdout": 20,
    "minimum_holdout_each_group": 5,
    "minimum_forecast_holdout_coverage": 0.8,
    "minimum_mse_improvement_fraction": 0.02,
    "multiplier_limit": 0.1,
    "effect_to_rank_divisor": 10.0,
}


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.tzinfo is None:
            return None
        if parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _number(value, low, high):
    if type(value) in (int, float) and math.isfinite(value) and low <= value <= high:
        return float(value)
    return None


def _identifier(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _competition(value):
    return value.strip().upper() if isinstance(value, str) and value.strip() else "UNKNOWN"


def _same_competition(left, right):
    # Exact league id equality is checked separately; unknown type cannot join leagues.
    return "UNKNOWN" in (left, right) or left == right


def _weather(value, *, kickoff, cutoff, forecast=False):
    if not isinstance(value, dict):
        return None, "MISSING_WEATHER"
    observed = _stamp(value.get("observed_at"))
    target = _stamp(value.get("target_time") or value.get("forecast_time"))
    if observed is None or observed > cutoff:
        return None, "UNKNOWN_OR_FUTURE_WEATHER_RECEIPT"
    if target is None or abs((target - kickoff).total_seconds()) > 60 * POLICY[
        "kickoff_tolerance_minutes"
    ]:
        return None, "WEATHER_TIME_MISMATCH"
    kind = str(value.get("weather_kind") or value.get("kind") or "").upper()
    if value.get("status", "SOURCED") != "SOURCED":
        return None, "UNSOURCED_WEATHER"
    if forecast:
        if kind != "FORECAST":
            return None, "NOT_A_FORECAST"
        if cutoff - observed > timedelta(hours=POLICY["forecast_freshness_hours"]):
            return None, "STALE_FORECAST"
        if observed >= kickoff:
            return None, "FORECAST_RECEIVED_AFTER_KICKOFF"
    elif kind not in {"REANALYSIS", "OBSERVATION"}:
        return None, "NOT_HISTORICAL_ACTUAL_WEATHER"
    clean = {}
    for key, low, high in (
        ("temperature_c", -80, 60),
        ("wind_speed_kmh", 0, 300),
        ("precipitation_mm", 0, 500),
    ):
        clean[key] = _number(value.get(key), low, high)
        if clean[key] is None:
            return None, "MISSING_OR_INVALID_WEATHER_VALUES"
    humidity = value.get("relative_humidity_pct")
    clean["relative_humidity_pct"] = _number(humidity, 0, 100)
    if humidity is not None and clean["relative_humidity_pct"] is None:
        return None, "INVALID_HUMIDITY"
    gusts = value.get("wind_gusts_kmh")
    clean["wind_gusts_kmh"] = _number(gusts, 0, 500)
    if gusts is not None and clean["wind_gusts_kmh"] is None:
        return None, "INVALID_WIND_GUSTS"
    # Open-Meteo does not observe the pitch itself. Keep a clearly labelled
    # moisture proxy instead of inventing a sourced surface-condition claim.
    clean["pitch_moisture_proxy"] = (
        "WET" if clean["precipitation_mm"] >= POLICY["wet_threshold_mm"] else "DRY")
    clean.update(weather_kind=kind, observed_at=observed.isoformat(),
                 target_time=target.isoformat())
    return clean, None


def _analog(weather, target):
    """None means incomparable, False a measured non-analog, True an analog."""
    humidity = target["relative_humidity_pct"]
    if humidity is not None and weather["relative_humidity_pct"] is None:
        return None
    gusts = target.get("wind_gusts_kmh")
    if gusts is not None and weather.get("wind_gusts_kmh") is None:
        return None
    wet = POLICY["wet_threshold_mm"]
    return (
        abs(weather["temperature_c"] - target["temperature_c"])
        <= POLICY["temperature_tolerance_c"]
        and abs(weather["wind_speed_kmh"] - target["wind_speed_kmh"])
        <= POLICY["wind_tolerance_kmh"]
        and (gusts is None or abs(weather["wind_gusts_kmh"] - gusts)
             <= POLICY["wind_tolerance_kmh"])
        and (weather["precipitation_mm"] >= wet) == (target["precipitation_mm"] >= wet)
        and (humidity is None or abs(weather["relative_humidity_pct"] - humidity)
             <= POLICY["humidity_tolerance_pct"])
    )


def _history(dataset, cutoff):
    excluded = Counter()
    by_id = defaultdict(list)
    for raw in dataset.get("matches", []):
        if not isinstance(raw, dict):
            excluded["INVALID_RECORD"] += 1
            continue
        identity = [_identifier(raw.get(key)) for key in (
            "fixture_id", "league_id", "home_team_id", "away_team_id"
        )]
        kickoff, observed = _stamp(raw.get("kickoff")), _stamp(raw.get("observed_at"))
        goals = [raw.get("home_goals"), raw.get("away_goals")]
        if not all(identity) or identity[2] == identity[3] or kickoff is None:
            excluded["UNKNOWN_MATCH_IDENTITY"] += 1
        elif observed is None or observed > cutoff:
            excluded["UNKNOWN_OR_FUTURE_RESULT_RECEIPT"] += 1
        elif kickoff + timedelta(hours=POLICY["result_delay_hours"]) >= cutoff:
            excluded["RESULT_NOT_YET_USABLE"] += 1
        elif any(type(goal) is not int or not 0 <= goal <= 30 for goal in goals):
            excluded["INVALID_FULL_TIME_SCORE"] += 1
        else:
            clean = dict(raw, kickoff=kickoff, observed_at=observed,
                         competition_type=_competition(raw.get("competition_type")))
            by_id[identity[0]].append(clean)
    rows = []
    for versions in by_id.values():
        signatures = {(row["league_id"], row["home_team_id"], row["away_team_id"],
                       row["kickoff"]) for row in versions}
        latest = max(row["observed_at"] for row in versions)
        current = [row for row in versions if row["observed_at"] == latest]
        if len(signatures) != 1 or len({(row["home_goals"], row["away_goals"])
                                       for row in current}) != 1:
            excluded["CONFLICTING_MATCH_VERSIONS"] += len(versions)
            continue
        # Stable tie breaker; input order never chooses a different weather version.
        selected = min(current, key=lambda row: json.dumps(row, sort_keys=True, default=str))
        rows.append(selected)
        excluded["DUPLICATE_MATCH_VERSION"] += len(versions) - 1
    rows.sort(key=lambda row: (row["kickoff"], row["fixture_id"]))
    return rows, dict(excluded)


def _elo_history(rows):
    """Pre-game ratings; same-time/overlapping matches cannot update each other."""
    ratings, counts = {}, Counter()
    timeline = defaultdict(list)
    pending, sequence = [], 0

    def apply_until(time):
        while pending and pending[0][0] <= time:
            completed_at, _, home_key, away_key, delta = heapq.heappop(pending)
            ratings[home_key] = ratings.get(home_key, POLICY["elo_initial"]) + delta
            ratings[away_key] = ratings.get(away_key, POLICY["elo_initial"]) - delta
            counts[home_key] += 1
            counts[away_key] += 1
            for key in (home_key, away_key):
                timeline[key].append((completed_at, ratings[key], counts[key]))

    enriched = []
    for kickoff, batch in groupby(rows, key=lambda row: row["kickoff"]):
        apply_until(kickoff)
        for row in batch:
            home_key = row["league_id"], row["home_team_id"]
            away_key = row["league_id"], row["away_team_id"]
            home_elo = ratings.get(home_key, POLICY["elo_initial"])
            away_elo = ratings.get(away_key, POLICY["elo_initial"])
            enriched.append(dict(row, home_elo=home_elo, away_elo=away_elo,
                                 home_prior_count=counts[home_key],
                                 away_prior_count=counts[away_key]))
            exponent = max(-10, min(10, (away_elo - home_elo) / POLICY["elo_scale"]))
            expected = 1 / (1 + 10 ** exponent)
            actual = 1 if row["home_goals"] > row["away_goals"] else (
                0 if row["home_goals"] < row["away_goals"] else 0.5)
            delta = POLICY["elo_k"] * (actual - expected)
            heapq.heappush(pending, (kickoff + timedelta(hours=POLICY["result_delay_hours"]),
                                    sequence, home_key, away_key, delta))
            sequence += 1
    apply_until(datetime.max.replace(tzinfo=UTC))
    return enriched, ratings, counts, timeline


def _prior_rating(timeline, key, cutoff):
    for completed_at, rating, count in reversed(timeline.get(key, [])):
        if completed_at < cutoff:
            return rating, count
    return POLICY["elo_initial"], 0


def _weighted(rows, metric, reference):
    weights = [2 ** (-(reference - row["kickoff"]).total_seconds()
                     / (86400 * POLICY["recency_half_life_days"])) for row in rows]
    total = sum(weights)
    if not total:
        return {"count": 0, "ess": 0.0, "mean": None, "variance": None}
    mean = sum(weight * row[metric] for weight, row in zip(weights, rows)) / total
    squared = sum(weight * weight for weight in weights)
    ess = total * total / squared
    variance = sum(weight * (row[metric] - mean) ** 2
                   for weight, row in zip(weights, rows)) / total
    variance *= ess / max(1.0, ess - 1)
    return {"count": len(rows), "ess": ess, "mean": mean,
            "variance": max(POLICY["variance_floor"], variance)}


def _estimate(rows, metric, target, reference, z_value):
    analogs = [row for row in rows if _analog(row["weather_values"], target) is True]
    controls = [row for row in rows if _analog(row["weather_values"], target) is False]
    left, right = _weighted(analogs, metric, reference), _weighted(controls, metric, reference)
    result = {"analog": left, "control": right, "metric": metric,
              "raw_effect": None, "shrunk_effect": 0.0, "interval": None,
              "interval_z": z_value, "sufficient": False, "signal": False}
    if (left["count"] < POLICY["minimum_analogs"]
            or right["count"] < POLICY["minimum_controls"]
            or min(left["ess"], right["ess"]) < POLICY["minimum_ess"]):
        return result
    raw_effect = left["mean"] - right["mean"]
    ess = min(left["ess"], right["ess"])
    shrink = ess / (ess + POLICY["shrinkage_prior_matches"])
    effect = raw_effect * shrink
    se = math.sqrt(left["variance"] / left["ess"] + right["variance"] / right["ess"])
    # Keeping the unshrunk standard error is conservative compared with shrinking it too.
    interval = [effect - z_value * se, effect + z_value * se]
    result.update(raw_effect=raw_effect, shrunk_effect=effect, interval=interval,
                  shrinkage=shrink, sufficient=True,
                  signal=interval[0] > 0 or interval[1] < 0)
    return result


def _deviance(actual, predicted):
    predicted = max(0.05, predicted)
    return 2 * (actual * math.log(actual / predicted) - actual + predicted) if actual else (
        2 * predicted)


def _binary_loss(actual, probability):
    probability = max(0.001, min(0.999, probability))
    return -(actual * math.log(probability) + (1 - actual) * math.log(1 - probability))


def _evaluate(training, holdout, estimate, target, metric, *, use_forecasts=False):
    baseline = sum(row[metric] for row in training) / len(training)
    prevalence = sum(_analog(row["weather_values"], target) is True
                     for row in training) / len(training)
    pairs, groups = [], Counter()
    for row in holdout:
        weather = row.get("forecast_values") if use_forecasts else row["weather_values"]
        if weather is None:
            continue
        is_analog = _analog(weather, target)
        if is_analog is None:
            continue
        groups["analog" if is_analog else "control"] += 1
        prediction = baseline + estimate["shrunk_effect"] * (float(is_analog) - prevalence)
        if metric == "over25":
            prediction = min(0.999, max(0.001, prediction))
        else:
            prediction = max(0.05, prediction)
        pairs.append((row[metric], prediction))
    size = len(pairs)
    coverage = size / len(holdout) if holdout else 0.0
    report = {"count": size, "coverage": coverage, "groups": dict(groups),
              "baseline_mean": baseline, "training_analog_prevalence": prevalence,
              "passed": False, "basis": "ARCHIVED_PREMATCH_FORECAST" if use_forecasts
              else "RETROSPECTIVE_ACTUAL_WEATHER", "reason": "INSUFFICIENT_HOLDOUT"}
    if (size < POLICY["minimum_holdout"]
            or min(groups.get("analog", 0), groups.get("control", 0))
            < POLICY["minimum_holdout_each_group"]
            or (use_forecasts and coverage < POLICY["minimum_forecast_holdout_coverage"])):
        return report
    mse_base = sum((actual - baseline) ** 2 for actual, _ in pairs) / size
    mse_weather = sum((actual - prediction) ** 2 for actual, prediction in pairs) / size
    loss = _binary_loss if metric == "over25" else _deviance
    dev_base = sum(loss(actual, baseline) for actual, _ in pairs) / size
    dev_weather = sum(loss(actual, prediction) for actual, prediction in pairs) / size
    improvement = (mse_base - mse_weather) / mse_base if mse_base > 0 else 0.0
    # Both proper loss functions must improve; no threshold is tuned on this holdout.
    passed = (estimate["signal"]
              and improvement >= POLICY["minimum_mse_improvement_fraction"]
              and dev_weather < dev_base)
    report.update(baseline_mse=mse_base, weather_mse=mse_weather,
                  mse_improvement_fraction=improvement, passed=passed,
                  reason="IMPROVED" if passed else "NO_HELDOUT_IMPROVEMENT")
    if metric == "over25":
        report.update(baseline_brier=mse_base, weather_brier=mse_weather,
                      baseline_log_loss=dev_base, weather_log_loss=dev_weather)
    else:
        report.update(baseline_poisson_deviance=dev_base, weather_poisson_deviance=dev_weather)
    return report


def _team_analysis(team_id, side, fixture, target, candidates, ratings, counts,
                   timeline, cutoff, z_value):
    league = fixture["league_id"]
    opposite = "away" if side == "home" else "home"
    opponent = fixture[f"{opposite}_team_id"]
    key, other = (league, team_id), (league, opponent)
    report = {"team_id": team_id, "side": side, "status": "INSUFFICIENT_DATA",
              "reason": "INSUFFICIENT_ELO_HISTORY", "matched_history_count": 0,
              "own_prior_matches": counts[key], "opponent_prior_matches": counts[other],
              "signals": {}, "validation": {}, "excluded": {}}
    if min(counts[key], counts[other]) < POLICY["elo_min_prior_matches"]:
        return report
    own_elo, opponent_elo = ratings[key], ratings[other]
    report.update(own_elo=own_elo, opponent_elo=opponent_elo)
    kickoff = _stamp(fixture["kickoff"])
    start = kickoff - timedelta(days=POLICY["lookback_days"])
    rejected, pool = Counter(), []
    for row in candidates:
        if row["kickoff"] < start or row["kickoff"] >= kickoff:
            rejected["OUTSIDE_HISTORY_WINDOW"] += 1
            continue
        if not _same_competition(row["competition_type"],
                                 _competition(fixture.get("competition_type"))):
            rejected["DIFFERENT_COMPETITION_TYPE"] += 1
            continue
        if (min(row["home_prior_count"], row["away_prior_count"])
                < POLICY["elo_min_prior_matches"]):
            rejected["INSUFFICIENT_HISTORICAL_ELO"] += 1
            continue
        actual, reason = _weather(row.get("weather"), kickoff=row["kickoff"], cutoff=cutoff)
        if actual is None:
            rejected[reason] += 1
            continue
        if _analog(actual, target) is None:
            rejected["MISSING_COMPARABLE_HUMIDITY"] += 1
            continue
        forecast_cutoff = row["kickoff"] - timedelta(minutes=POLICY["prematch_lockout_minutes"])
        forecast, _ = _weather(row.get("prematch_weather"), kickoff=row["kickoff"],
                               cutoff=forecast_cutoff, forecast=True)
        pool.append(dict(row, weather_values=actual, forecast_values=forecast,
                         total_goals=row["home_goals"] + row["away_goals"],
                         over25=int(row["home_goals"] + row["away_goals"] > 2.5),
                         goals_for=row[f"{side}_goals"]))
    report.update(matched_history_count=len(pool), excluded=dict(rejected),
                  reason="INSUFFICIENT_COMPARABLE_WEATHER")
    if not pool:
        return report
    # Keep all matches sharing the split timestamp on the same side of the split.
    holdout_count = max(POLICY["minimum_holdout"],
                        math.ceil(len(pool) * POLICY["holdout_fraction"]))
    split = max(0, len(pool) - holdout_count)
    if not split:
        return report
    split_time = pool[split]["kickoff"]
    # Matching on today's Elo would indirectly let holdout scores select training rows.
    # Freeze the strength anchor before the holdout, then require its relevance today.
    anchor_own, anchor_own_count = _prior_rating(timeline, key, split_time)
    anchor_opponent, anchor_opponent_count = _prior_rating(timeline, other, split_time)
    report["strength_anchor"] = {
        "as_of": split_time.isoformat(), "own_elo": anchor_own,
        "opponent_elo": anchor_opponent, "own_prior_matches": anchor_own_count,
        "opponent_prior_matches": anchor_opponent_count,
    }
    if min(anchor_own_count, anchor_opponent_count) < POLICY["elo_min_prior_matches"]:
        report["reason"] = "INSUFFICIENT_PRE_HOLDOUT_STRENGTH_HISTORY"
        return report

    def matched_strength(row):
        return (abs(row[f"{opposite}_elo"] - anchor_opponent)
                <= POLICY["opponent_elo_tolerance"]
                and abs((row[f"{side}_elo"] - row[f"{opposite}_elo"])
                        - (anchor_own - anchor_opponent)) <= POLICY["relative_elo_tolerance"])

    strength_pool = [row for row in pool if matched_strength(row)]
    rejected["OPPONENT_OR_RELATIVE_STRENGTH_MISMATCH"] += len(pool) - len(strength_pool)
    report.update(matched_history_count=len(strength_pool), excluded=dict(rejected))
    training = [row for row in strength_pool if row["kickoff"]
                + timedelta(hours=POLICY["result_delay_hours"]) < split_time]
    holdout = [row for row in strength_pool if row["kickoff"] >= split_time]
    if not training or not holdout:
        report["reason"] = "INSUFFICIENT_STRENGTH_MATCHED_HOLDOUT"
        return report
    from pathlib import Path

    from goal_analysis.normalization.training_datasets import snapshot

    dataset_recipe = {'version': VERSION, 'policy': POLICY,
        'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'team_id': team_id, 'side': side, 'target_weather': target, 'strength_anchor': report['strength_anchor']}
    report['training_datasets'] = [snapshot(rows, role=role, as_of=cutoff, recipe=dataset_recipe,
        lineage={'fixture_id': fixture['fixture_id'], 'comparison_z_value': z_value})['manifest']
        for role, rows in (('weather_analog_train', training), ('weather_analog_holdout', holdout))]
    validation = {"training_count": len(training), "holdout_count": len(holdout),
                  "training_last_kickoff": training[-1]["kickoff"].isoformat(),
                  "holdout_start": split_time.isoformat(),
                  "holdout_end": holdout[-1]["kickoff"].isoformat(),
                  "fit_uses_holdout_outcomes": False, "metrics": {}}
    for metric in ("total_goals", "goals_for", "over25"):
        estimate = _estimate(training, metric, target, split_time, z_value)
        report["signals"][metric] = estimate
        validation["metrics"][metric] = {
            "retrospective": _evaluate(training, holdout, estimate, target, metric),
            "forecast": _evaluate(training, holdout, estimate, target, metric,
                                  use_forecasts=True),
        }
    report["validation"] = validation
    still_relevant = (abs(opponent_elo - anchor_opponent) <= POLICY["opponent_elo_tolerance"]
                      and abs((own_elo - opponent_elo) - (anchor_own - anchor_opponent))
                      <= POLICY["relative_elo_tolerance"])
    report["current_strength_matches_validation_anchor"] = still_relevant
    total = report["signals"]["total_goals"]
    over = report["signals"]["over25"]
    checks = validation["metrics"]["total_goals"]
    over_checks = validation["metrics"]["over25"]
    if not total["sufficient"]:
        return report
    if not total["signal"]:
        report.update(status="NO_SIGNAL", reason="INTERVAL_INCLUDES_ZERO")
    elif not over["signal"]:
        report.update(status="NO_SIGNAL", reason="OVER25_INTERVAL_INCLUDES_ZERO")
    elif total["shrunk_effect"] * over["shrunk_effect"] <= 0:
        report.update(status="NO_SIGNAL", reason="TOTAL_MEAN_AND_OVER25_RATE_DISAGREE")
    elif not still_relevant:
        report.update(status="UNVALIDATED", reason="CURRENT_STRENGTH_DIFFERS_FROM_VALIDATION_ANCHOR")
    elif not checks["retrospective"]["passed"] or not over_checks["retrospective"]["passed"]:
        report.update(status="UNVALIDATED", reason="RETROSPECTIVE_HOLDOUT_FAILED")
    elif not checks["forecast"]["passed"] or not over_checks["forecast"]["passed"]:
        report.update(status="UNVALIDATED", reason="PREMATCH_FORECAST_VALIDATION_MISSING_OR_FAILED")
    else:
        report.update(status="VALIDATED", reason="BOTH_HOLDOUT_CHECKS_PASSED")
    return report


def _multiplier(teams, *, require_forecast):
    candidates = []
    for team in teams.values():
        signal = team.get("signals", {}).get("total_goals", {})
        over = team.get("signals", {}).get("over25", {})
        checks = team.get("validation", {}).get("metrics", {}).get("total_goals", {})
        over_checks = team.get("validation", {}).get("metrics", {}).get("over25", {})
        if (signal.get("signal") and checks.get("retrospective", {}).get("passed")
                and team.get("current_strength_matches_validation_anchor") and over.get("signal")
                and over_checks.get("retrospective", {}).get("passed")
                and signal["shrunk_effect"] * over["shrunk_effect"] > 0):
            candidates.append((signal, checks, over_checks))
    directions = {math.copysign(1, signal["shrunk_effect"]) for signal, _, _ in candidates}
    if len(directions) > 1:
        return 1.0, "CONFLICTING_TEAM_SIGNALS"
    if require_forecast:
        candidates = [(signal, checks, over_checks) for signal, checks, over_checks in candidates
                      if checks["forecast"]["passed"] and over_checks["forecast"]["passed"]]
    if not candidates:
        return 1.0, "NO_VALIDATED_SIGNAL"
    # Home/away analyses can share matches. Average effects; never add independent evidence.
    relative = []
    for signal, checks, _ in candidates:
        baseline = max(1.0, checks["retrospective"]["baseline_mean"])
        relative.append(signal["shrunk_effect"] / baseline / POLICY["effect_to_rank_divisor"])
    change = sum(relative) / len(relative)
    change = max(-POLICY["multiplier_limit"], min(POLICY["multiplier_limit"], change))
    return round(1 + change, 10), "TOTAL_GOALS_HIGHER" if change > 0 else "TOTAL_GOALS_LOWER"


def _forecast_map(forecasts, cutoff):
    if isinstance(forecasts, dict):
        return forecasts
    grouped = defaultdict(list)
    for value in forecasts or []:
        if isinstance(value, dict) and _identifier(value.get("fixture_id")):
            grouped[value["fixture_id"]].append(value)

    def latest_known(values):
        known = [row for row in values if _stamp(row.get("observed_at")) is not None
                 and _stamp(row["observed_at"]) <= cutoff]
        # Retain an invalid sole entry so the result can explain why it was rejected.
        return max(known or values, key=lambda row: (_stamp(row.get("observed_at"))
                   or datetime.min.replace(tzinfo=UTC), json.dumps(row, sort_keys=True, default=str)))

    return {key: latest_known(values) for key, values in grouped.items()}


def analyze_weather(fixtures, forecasts, dataset, *, as_of):
    """Return evidence and bounded directional ranks; never modify inputs or tickets.

    ``score_multiplier`` is a total-goals/OVER direction heuristic. Use market_effects
    for market-specific ranking; it MUST NOT be applied to every market on a fixture.
    Historical result receipt and weather receipt must both be at/before ``as_of``.
    A retrospective holdout is not a claim of period-correct betting backtest or causality.
    """
    cutoff = _stamp(as_of)
    if cutoff is None:
        raise ValueError("as_of must be a timezone-aware datetime")
    fixtures = list(fixtures)
    rows, exclusions = _history(dataset, cutoff)
    history, ratings, counts, timeline = _elo_history(rows)
    by_team = defaultdict(list)
    for row in history:
        for side in ("home", "away"):
            by_team[row["league_id"], row[f"{side}_team_id"], side].append(row)
    lookup = _forecast_map(forecasts, cutoff)
    # Bonferroni protects this batch's two teams x three outcomes, not future batches.
    comparisons = max(1, len(fixtures) * 6)
    z_value = NormalDist().inv_cdf(1 - POLICY["familywise_alpha"] / (2 * comparisons))
    results = []
    for fixture in fixtures:
        fixture_id = fixture.get("fixture_id")
        result = {"fixture_id": fixture_id, "status": "INSUFFICIENT_DATA",
                  "reason": "UNKNOWN_MATCH_IDENTITY", "score_multiplier": 1.0,
                  "shadow_score_multiplier": 1.0, "teams": {},
                  "live_weight_eligible": False,
                  "market_effects": {"over25": 1.0, "under25": 1.0},
                  "shadow_market_effects": {"over25": 1.0, "under25": 1.0},
                  "score_semantics": "HEURISTIC_TOTAL_GOALS_DIRECTION_NOT_WIN_PROBABILITY",
                  "association_is_causal": False}
        results.append(result)
        kickoff = _stamp(fixture.get("kickoff"))
        if (kickoff is None or not all(_identifier(fixture.get(key)) for key in (
                "fixture_id", "league_id", "home_team_id", "away_team_id"))
                or fixture["home_team_id"] == fixture["away_team_id"]):
            continue
        if kickoff <= cutoff + timedelta(minutes=POLICY["prematch_lockout_minutes"]):
            result["reason"] = "PREMATCH_LOCKOUT"
            continue
        raw_forecast = lookup.get(fixture_id)
        if (isinstance(raw_forecast, dict) and raw_forecast.get("fixture_id", fixture_id)
                != fixture_id):
            result["reason"] = "FORECAST_FIXTURE_MISMATCH"
            continue
        target, reason = _weather(raw_forecast, kickoff=kickoff, cutoff=cutoff, forecast=True)
        if target is None:
            result["reason"] = reason
            continue
        result["weather"] = target
        for side in ("home", "away"):
            team = fixture[f"{side}_team_id"]
            result["teams"][side] = _team_analysis(
                team, side, fixture, target, by_team[fixture["league_id"], team, side],
                ratings, counts, timeline, cutoff, z_value,
            )
        actual, direction = _multiplier(result["teams"], require_forecast=True)
        shadow, shadow_direction = _multiplier(result["teams"], require_forecast=False)
        result.update(score_multiplier=actual, shadow_score_multiplier=shadow,
                      effect_direction=direction, shadow_effect_direction=shadow_direction,
                      market_effects={"over25": actual, "under25": round(2 - actual, 10)},
                      shadow_market_effects={"over25": shadow,
                                             "under25": round(2 - shadow, 10)})
        states = {team["status"] for team in result["teams"].values()}
        if direction == "CONFLICTING_TEAM_SIGNALS":
            result.update(status="NO_SIGNAL", reason=direction)
        elif actual != 1:
            result.update(status="VALIDATED", reason="ARCHIVED_FORECAST_HOLDOUT_IMPROVED",
                          live_weight_eligible=True)
        elif "UNVALIDATED" in states or shadow != 1:
            result.update(status="UNVALIDATED", reason="FORECAST_WEIGHTING_NOT_VALIDATED")
        elif "NO_SIGNAL" in states:
            result.update(status="NO_SIGNAL", reason="INTERVAL_INCLUDES_ZERO")
        else:
            result["reason"] = "INSUFFICIENT_COMPARABLE_HISTORY"
    policy_json = json.dumps(POLICY, sort_keys=True, separators=(",", ":"))
    return {"version": VERSION, "observed_at": cutoff.isoformat(),
            "data_hash": dataset.get("data_hash"), "policy": dict(POLICY),
            "policy_hash": hashlib.sha256(policy_json.encode()).hexdigest(),
            "science_basis": list(SCIENCE_BASIS),
            "coverage": dict(dataset.get("coverage", {})),
            "accepted_history_count": len(history), "excluded_history": exclusions,
            "inference": "OBSERVATIONAL_NOT_CAUSAL",
            "validation_limit": "RETROSPECTIVE_RESULT_TIMELINE_NOT_A_PERIOD_CORRECT_BACKTEST",
            "interval_family_comparisons": comparisons, "fixtures": results}
