"""Descriptive xG rate normalization. SHADOW features, never probabilities.

Fit only a witnessed prefix. Opponent strength excludes the current match and
is shrunk toward the league/venue baseline. A later walk-forward fit must use
its own then-available prefix; these are not historic prematch team ratings.
"""
import math
import re
from collections import Counter, defaultdict

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.leakage import model_inputs
from goal_analysis.normalization.training_datasets import sha, snapshot

VERSION = "xg-rates-shadow-v1"
DEFAULT_RECIPE = {"half_life_days": 90, "lookback_days": 365, "prior_weight": 3,
                  "min_matches": 3, "min_effective_matches": 2, "min_weighted_exposure": 1}


def _recipe(overrides):
    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError("INVALID_XG_RECIPE")
    recipe = {**DEFAULT_RECIPE, **(overrides or {})}
    if set(recipe) != set(DEFAULT_RECIPE):
        raise ValueError("INVALID_XG_RECIPE")
    for key, (lo, hi) in {"half_life_days": (1, 3650), "lookback_days": (1, 3650),
                          "prior_weight": (0.01, 100), "min_matches": (3, 100),
                          "min_effective_matches": (1, 100), "min_weighted_exposure": (.01, 100)}.items():
        value = recipe[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError("INVALID_XG_RECIPE")
    if type(recipe["min_matches"]) is not int:
        raise ValueError("INTEGER_MIN_MATCHES_REQUIRED")
    return recipe


def _row(payload, cutoff, lookback):
    row = dict(payload)
    for key, prefix in (("fixture_id", "mt"), ("competition_id", "comp"),
                        ("season_id", "sn"), ("home_team_id", "tm"), ("away_team_id", "tm")):
        if not isinstance(row.get(key), str) or not re.fullmatch(prefix + r"_\d{1,30}", row[key]):
            raise ValueError("INVALID_NATIVE_XG_ID")
    if row["home_team_id"] == row["away_team_id"]:
        raise ValueError("IDENTICAL_XG_TEAMS")
    if (row.get("provider") != "the_stats_api" or row.get("scope") != "REGULATION_FIRST_PLUS_SECOND_HALF"
            or row.get("status") != "FT" or row.get("neutral_venue") is not False):
        raise ValueError("UNPROVEN_NONNEUTRAL_REGULATION_XG")
    kickoff = utc(row["kickoff"])
    if not kickoff < utc(row["observed_at"]) <= cutoff:
        raise ValueError("NOT_PRIOR_COMPLETED_XG")
    days = (cutoff - kickoff).total_seconds() / 86400
    if days > lookback:
        raise ValueError("OUTSIDE_XG_LOOKBACK")
    for key in ("home_xg", "away_xg"):
        if type(row.get(key)) not in (int, float) or not math.isfinite(row[key]) or not 0 <= row[key] <= 20:
            raise ValueError("MISSING_OR_INVALID_MATCH_XG")
    return row, days


def fit_xg_rates(envelopes, *, as_of, recipe=None):
    """Return opponent-adjusted home/away rate features with explicit gaps.

    Minimum sample requirements are applied per team and venue. Missing team
    history never becomes a measured league-average feature. Shrinkage is an
    explicit estimator prior, not filled-in xG observations.
    """
    cutoff, recipe = utc(as_of), _recipe(recipe)
    payloads = model_inputs(envelopes, as_of=cutoff)  # Hard fail on future/unwitnessed inputs.
    accepted, issues, identities = [], Counter(), set()
    for payload in payloads:
        try:
            row, days = _row(payload, cutoff, recipe["lookback_days"])
        except (ValueError, KeyError) as error:
            issues[str(error)] += 1
            continue
        if row["fixture_id"] in identities:
            raise ValueError("DUPLICATE_XG_MATCH")
        identities.add(row["fixture_id"])
        weight = 2 ** (-days / recipe["half_life_days"])
        if weight == 0:
            issues["NUMERICAL_TIME_WEIGHT_UNDERFLOW"] += 1
            continue
        accepted.append((row, weight))
    accepted.sort(key=lambda pair: (utc(pair[0]["kickoff"]), pair[0]["fixture_id"]))
    rows = [row for row, _ in accepted]
    dataset = snapshot(rows, role="opponent_normalized_xg_input", as_of=cutoff,
        recipe={"version": VERSION, **recipe, "mode": "SHADOW"},
        lineage=[{k: e.get(k) for k in ("record_id", "payload_sha256", "source_sha256", "receipt_id", "times", "source_receipts")}
                 for e in sorted(envelopes, key=lambda e: e["record_id"])])
    groups = defaultdict(list)
    for row, weight in accepted:
        groups[(row["competition_id"], row["season_id"])].append((row, weight))
    leagues = []
    for (competition, season), matches in sorted(groups.items()):
        total = math.fsum(w for _, w in matches)
        base = {side: math.fsum(w * r[side + "_xg"] for r, w in matches) / total
                for side in ("home", "away")}
        item = {"competition_id": competition, "season_id": season, "matches": len(matches),
                "weighted_exposure": total, "league_home_xg": base["home"],
                "league_away_xg": base["away"], "teams": []}
        if len(matches) < recipe["min_matches"] or min(base.values()) <= 0:
            item["status"] = "INSUFFICIENT_LEAGUE_XG_OR_ZERO_BASELINE"
            leagues.append(item)
            continue
        if any(not math.isfinite(r[side + "_xg"] / base[side])
               for r, _ in matches for side in ("home", "away")):
            item["status"] = "NUMERICALLY_UNSTABLE_BASELINE"
            leagues.append(item)
            continue
        # [exposure, weighted attack sum, weighted defense sum, match count].
        # These sufficient statistics remove this match from each opponent fit.
        stats = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
        observations = []
        for row, weight in matches:
            for side, opponent_side in (("home", "away"), ("away", "home")):
                team, opponent = row[side + "_team_id"], row[opponent_side + "_team_id"]
                attack, defense = row[side + "_xg"] / base[side], row[opponent_side + "_xg"] / base[opponent_side]
                values = stats[team]
                values[0] += weight; values[1] += weight * attack; values[2] += weight * defense; values[3] += 1
                observations.append((team, opponent, side, weight, attack, defense))
        buckets = defaultdict(list)
        prior = recipe["prior_weight"]
        for team, opponent, side, weight, attack, defense in observations:
            ow, oa, od, count = stats[opponent]
            # Opponent's attack in this match equals this team's defense and vice versa.
            remaining_weight = max(0.0, ow - weight)
            opponent_attack = (prior + max(0.0, oa - weight * defense)) / (prior + remaining_weight)
            opponent_defense = (prior + max(0.0, od - weight * attack)) / (prior + remaining_weight)
            buckets[(team, side)].append((weight, (weight * attack) / opponent_defense,
                                         (weight * defense) / opponent_attack, count - 1))
        for team in sorted(stats):
            team_row = {"team_id": team, "total_matches": stats[team][3]}
            for side, other in (("home", "away"), ("away", "home")):
                observations = buckets[(team, side)]
                exposure = math.fsum(o[0] for o in observations)
                scale = max((o[0] for o in observations), default=1)
                normalized = [o[0] / scale for o in observations]
                effective = math.fsum(normalized)**2 / math.fsum(w*w for w in normalized) if normalized else 0.0
                no_opponent_history = sum(o[3] == 0 for o in observations)
                ready = (len(observations) >= recipe["min_matches"]
                         and exposure >= recipe["min_weighted_exposure"]
                         and effective >= recipe["min_effective_matches"] and not no_opponent_history)
                team_row[side] = {"status": "AVAILABLE_SHADOW" if ready else "INSUFFICIENT_XG_HISTORY",
                    "matches": len(observations), "effective_matches": effective,
                    "weighted_exposure": exposure, "opponents_with_no_other_history": no_opponent_history,
                    "prior_fraction": prior / (prior + exposure),
                    "attack_strength": None, "defense_strength": None,
                    "attack_xg_per_regulation_match": None, "conceded_xg_per_regulation_match": None}
                if ready:
                    attack = (prior + math.fsum(o[1] for o in observations)) / (prior + exposure)
                    defense = (prior + math.fsum(o[2] for o in observations)) / (prior + exposure)
                    team_row[side].update(attack_strength=attack, defense_strength=defense,
                        attack_xg_per_regulation_match=attack * base[side],
                        conceded_xg_per_regulation_match=defense * base[other])
            item["teams"].append(team_row)
        item["status"] = "SHADOW_FEATURES_AVAILABLE_WITH_EXPLICIT_GAPS"
        leagues.append(item)
    result = {"version": VERSION, "mode": "SHADOW", "as_of": cutoff.isoformat(), "recipe": recipe,
        "units": "XG_PER_REGULATION_MATCH_INCLUDING_STOPPAGE", "is_probability_model": False,
        "live_decision_effect": False, "control_ranking_unchanged": True,
        "opponent_estimate": "LEAVE_THIS_MATCH_OUT_AT_CURRENT_FIT_CUTOFF_WITH_EXPLICIT_PRIOR",
        "input_rows": len(payloads), "accepted_matches": len(rows), "issues": dict(sorted(issues.items())),
        "dataset": dataset, "leagues": leagues}
    result["result_sha256"] = sha(result)
    return result
