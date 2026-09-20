"""Market-specific historical evidence; descriptive rates, never win probabilities."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timedelta

from goal_analysis.agents import canonical_sha256
from goal_analysis.providers.api_football_history import aware_time, positive_id


def validate_history(snapshot: dict) -> list[dict]:
    if (
        snapshot.get("kind") != "DAILY223_HISTORY"
        or type(snapshot.get("schema_version")) is not int
        or snapshot["schema_version"] != 1
        or snapshot.get("provider") != "api_football"
    ):
        raise ValueError("DAILY223_HISTORY schema 1 snapshot required")
    content = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    if snapshot.get("snapshot_sha256") != canonical_sha256(content):
        raise ValueError("history snapshot hash mismatch")
    collected = aware_time(snapshot["collected_at"])
    records = snapshot["records"]
    if not isinstance(records, list):
        raise TypeError("history records must be a list")
    seen = set()
    for item in records:
        for key in ("fixture_id", "league_id", "home_team_id", "away_team_id"):
            if positive_id(item[key]) != item[key]:
                raise ValueError("history IDs must be canonical strings")
        if item["fixture_id"] in seen:
            raise ValueError("duplicate fixture in history snapshot")
        seen.add(item["fixture_id"])
        if item["home_team_id"] == item["away_team_id"]:
            raise ValueError("history teams must differ")
        if item["status"] not in {"FT", "AET", "PEN"}:
            raise ValueError("history must contain completed matches only")
        if not aware_time(item["kickoff"]) < aware_time(item["observed_at"]) <= collected:
            raise ValueError("invalid history observation chronology")
        for side in ("home", "away"):
            full, half = item[f"full_time_{side}"], item[f"first_half_{side}"]
            if any(
                value is not None and (type(value) is not int or value < 0)
                for value in (full, half)
            ):
                raise ValueError("invalid score in history snapshot")
            if full is not None and half is not None and half > full:
                raise ValueError("half-time score exceeds full-time score")
        if not isinstance(item.get("source_id"), str) or not item["source_id"].strip():
            raise ValueError("history source required")
    return records


def enrich_daily223_input(payload: dict, snapshot: dict) -> dict:
    """Replace historical/venue assertions with calculations on this snapshot.

    Candidate prices and evaluation time are preserved. Collect history BEFORE
    refreshing prices; late-acquired history must not be backdated for a replay.
    """
    records = validate_history(snapshot)
    now = aware_time(payload["observed_at"])
    if aware_time(snapshot["collected_at"]) > now:
        raise ValueError(
            "history was collected after the decision time; refresh the input snapshot"
        )
    if not isinstance(payload.get("candidates"), list):
        raise TypeError("candidates must be a list")
    result = deepcopy(payload)
    analyses = []
    for candidate in result["candidates"]:
        evidence = candidate.get("evidence", [])
        if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
            raise ValueError("candidate evidence must be a list of objects")
        # A rerun is idempotent; supplied history claims cannot override measured data.
        candidate["evidence"] = [
            item for item in evidence if item.get("category") not in {"historical", "venue_form"}
        ]
        try:
            analysis, generated = _analyse(candidate, records, now, snapshot["snapshot_sha256"])
            candidate["evidence"].extend(generated)
        except (KeyError, ValueError, TypeError) as error:
            analysis = {
                "candidate_id": candidate.get("candidate_id"),
                "status": "DATA_REQUIRED",
                "reason": str(error),
            }
        analyses.append(analysis)
    result["history_analysis"] = {
        "feature_version": "daily223-history-v1",
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "sources": deepcopy(snapshot["sources"]),
        "candidates": analyses,
        "interpretation": "Descriptive sample frequencies; not calibrated future probabilities.",
    }
    return result


def _analyse(
    candidate: dict, records: list[dict], now: datetime, digest: str
) -> tuple[dict, list[dict]]:
    home = positive_id(candidate["api_football_home_team_id"])
    away = positive_id(candidate["api_football_away_team_id"])
    fixture = positive_id(candidate["api_football_fixture_id"])
    league = positive_id(candidate["api_football_league_id"])
    if home == away:
        raise ValueError("candidate teams must differ")
    kickoff = aware_time(candidate["kickoff"])
    if kickoff <= now:
        raise ValueError("candidate match has already started")
    period = candidate["period"]
    if period not in {"FULL_TIME", "FIRST_HALF"}:
        raise ValueError("history supports FULL_TIME or FIRST_HALF only")
    predicate = _market_predicate(candidate["market_key"], candidate["selection_key"])
    eligible = [
        item
        for item in records
        if item["fixture_id"] != fixture and aware_time(item["kickoff"]) < now
    ]
    profiles = {}
    for role, team in (("home", home), ("away", away)):
        earliest = now - timedelta(days=730)
        since = candidate.get(f"{role}_history_since")
        if since is not None:
            start = aware_time(since)
            if start > now:
                raise ValueError("history/coach start must not be in the future")
            earliest = max(earliest, start)
        all_team = sorted(
            [item for item in eligible if team in {item["home_team_id"], item["away_team_id"]}],
            key=lambda item: (aware_time(item["kickoff"]), item["fixture_id"]),
            reverse=True,
        )
        same_league = [
            item
            for item in all_team
            if item["league_id"] == league and aware_time(item["kickoff"]) >= earliest
        ]
        venue = [item for item in same_league if item[f"{role}_team_id"] == team]
        profiles[role] = {
            "team_id": team,
            "history_from": earliest.isoformat(),
            "historical_20": _sample(same_league[:20], team, role, period, predicate),
            "recent_5": _sample(same_league[:5], team, role, period, predicate),
            "venue_10": _sample(venue[:10], team, role, period, predicate),
            "observed_workload": _workload(all_team, kickoff),
        }
    generated = []
    for category, window, target in (
        ("historical", "historical_20", 20),
        ("venue_form", "venue_10", 10),
    ):
        h, a = profiles["home"][window], profiles["away"][window]
        if min(h["sample_size"], a["sample_size"]) < 3:
            continue
        strength = min(h["market_hits"] / h["sample_size"], a["market_hits"] / a["sample_size"])
        reliability = min(h["sample_size"], a["sample_size"], target) / target
        used_ids = set(h["fixture_ids"] + a["fixture_ids"])
        recent_note = ""
        if category == "venue_form":
            rh, ra = profiles["home"]["recent_5"], profiles["away"]["recent_5"]
            if min(rh["sample_size"], ra["sample_size"]) < 3:
                continue
            strength = min(
                0.6 * h["descriptive_hit_rate"] + 0.4 * rh["descriptive_hit_rate"],
                0.6 * a["descriptive_hit_rate"] + 0.4 * ra["descriptive_hit_rate"],
            )
            reliability = min(reliability, rh["sample_size"] / 5, ra["sample_size"] / 5)
            used_ids.update(rh["fixture_ids"] + ra["fixture_ids"])
            recent_note = (
                f"Utolsó 5: hazai oldal {rh['market_hits']}/{rh['sample_size']}, "
                f"vendég oldal {ra['market_hits']}/{ra['sample_size']}; "
                "helyszíni/aktuális arány: 60/40. "
            )
        observed = max(
            aware_time(item["observed_at"]) for item in records if item["fixture_id"] in used_ids
        )
        generated.append(
            {
                "evidence_id": f"history-v1:{candidate['candidate_id']}:{category}",
                "category": category,
                "claim_type": "INFERENCE",
                "statement": (
                    f"{period} {candidate['market_key']} / {candidate['selection_key']}; "
                    f"{window}: hazai oldal {h['market_hits']}/{h['sample_size']}, "
                    f"vendég oldal {a['market_hits']}/{a['sample_size']}. "
                    f"{recent_note}"
                    "Megfigyelt piaci mintázat, nem becsült nyerési esély."
                ),
                "source_id": f"history-sha256:{digest}",
                "observed_at": observed.isoformat(),
                "strength": strength,
                "reliability": reliability,
            }
        )
    return {
        "candidate_id": candidate["candidate_id"],
        "status": "CALCULATED",
        "history_league_id": league,
        "period": period,
        "profiles": profiles,
        "missing_support": [
            name
            for name in ("historical", "venue_form")
            if not any(item["category"] == name and item["strength"] > 0 for item in generated)
        ],
        "limitations": [
            "Historical and venue samples can overlap; their evidence is not independent.",
            "Opponent strength and coach/lineup continuity are not automatically adjusted.",
            "Workload is limited to the explicitly collected leagues and seasons.",
            "No player minutes, actual rest duration, travel, mood or motivation is inferred.",
        ],
    }, generated


def _market_predicate(market: str, selection: str):
    if market == "h2h" and selection in {"home", "away", "draw"}:

        def result(goals_for, goals_against, role):
            if selection == "draw":
                return goals_for == goals_against
            return goals_for > goals_against if selection == role else goals_for < goals_against

        return result
    if market == "btts" and selection in {"yes", "no"}:
        return lambda gf, ga, _: (gf > 0 and ga > 0) == (selection == "yes")
    match = re.fullmatch(r"totals_(\d+)_5", market)
    if match and selection in {"over", "under"}:
        line = int(match.group(1)) + 0.5
        return lambda gf, ga, _: gf + ga > line if selection == "over" else gf + ga < line
    raise ValueError("unsupported historical market/selection; no substituted market")


def _sample(rows: list[dict], team: str, role: str, period: str, predicate) -> dict:
    prefix = "full_time" if period == "FULL_TIME" else "first_half"
    played, unknown = [], []
    for item in rows:
        home, away = item[f"{prefix}_home"], item[f"{prefix}_away"]
        if home is None or away is None:
            unknown.append(item["fixture_id"])
            continue
        gf, ga = (home, away) if item["home_team_id"] == team else (away, home)
        played.append((item, gf, ga, bool(predicate(gf, ga, role))))
    count = len(played)
    hits = sum(hit for _, _, _, hit in played)
    return {
        "sample_size": count,
        "market_hits": hits,
        "descriptive_hit_rate": hits / count if count else None,
        "goals_for_mean": sum(gf for _, gf, _, _ in played) / count if count else None,
        "goals_against_mean": sum(ga for _, _, ga, _ in played) / count if count else None,
        "results_newest_first": [
            "W" if gf > ga else "L" if gf < ga else "D" for _, gf, ga, _ in played
        ],
        "fixture_ids": [item["fixture_id"] for item, _, _, _ in played],
        "source_ids": [item["source_id"] for item, _, _, _ in played],
        "missing_period_scores": unknown,
    }


def _workload(rows: list[dict], kickoff: datetime) -> dict:
    counts = {
        f"observed_matches_{days}d": sum(
            kickoff - timedelta(days=days) <= aware_time(item["kickoff"]) < kickoff for item in rows
        )
        for days in (7, 14)
    }
    return {
        **counts,
        "hours_between_kickoffs": (kickoff - aware_time(rows[0]["kickoff"])).total_seconds() / 3600
        if rows
        else None,
        "scope": "OBSERVED_COMPLETED_MATCHES_IN_COLLECTED_COMPETITIONS",
        "actual_rest_hours": None,
        "player_minutes": None,
        "fatigue_assessment": None,
        "motivation_assessment": None,
    }
