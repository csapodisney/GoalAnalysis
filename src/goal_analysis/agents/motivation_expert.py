"""Read-only match stakes and sourced human-factor evidence; never mental certainty.

Standings rows identify fixture/team/competition/season, points, games_remaining,
observed_at, source_url and rules. Rules require verified=True, source_url,
observed_at, points_per_win and boundaries. Each boundary has objective,
target_points (current qualifying boundary), max_challenger_points (maximum final
points of ALL relevant challengers), complete_contender_coverage=True. Relegation
boundaries describe survival. Equality is UNKNOWN: tie-breaks are not guessed.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from urllib.parse import urlsplit

from goal_analysis.storage.sports_news import _assertion

VERSION = "motivation-evidence-v1"
_OBJECTIVES = {"TITLE_RACE", "EUROPE_RACE", "RELEGATION_RACE"}


def _stamp(value):
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("motivation timestamps must be timezone-aware")
    return parsed.astimezone(UTC)


def _source(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("missing source URL")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid source URL")
    return value


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 1_000_000:
        raise ValueError("invalid standings integer")
    return value


def _identity(value):
    if type(value) not in (str, int) or not str(value).strip():
        raise ValueError("missing identity")
    return str(value)


def _standing(row, fixture, cutoff):
    competition = fixture.get("competition_id", fixture.get("api_football_league_id",
                                                           fixture.get("league_id")))
    if (_identity(row["competition_id"]) != _identity(competition)
            or _identity(row["season"]) != _identity(fixture.get("season"))):
        raise ValueError("standings scope mismatch")
    team = _identity(row["team_id"])
    teams = {str(fixture[key]) for key in ("home_team_id", "away_team_id",
             "api_football_home_team_id", "api_football_away_team_id") if fixture.get(key)}
    if team not in teams:
        raise ValueError("standings team mismatch")
    observed, rules = _stamp(row["observed_at"]), row["rules"]
    rules_observed = _stamp(rules["observed_at"])
    if observed > cutoff or rules_observed > cutoff or rules.get("verified") is not True:
        raise ValueError("unverified or future rules/standings")
    points = _integer(row["points"], -1000)
    games = _integer(row["games_remaining"])
    per_win = _integer(rules["points_per_win"], 1)
    maximum = points + games * per_win
    result = []
    for boundary in rules["boundaries"]:
        objective = boundary["objective"]
        if objective not in _OBJECTIVES or boundary.get("complete_contender_coverage") is not True:
            raise ValueError("unsupported or incomplete boundary")
        target = _integer(boundary["target_points"], -1000)
        challenger_max = _integer(boundary["max_challenger_points"], -1000)
        if challenger_max < target:
            raise ValueError("inconsistent boundary")
        status = ("ELIMINATED" if maximum < target else "QUALIFIED"
                  if points > challenger_max else "UNKNOWN"
                  if maximum == target or points == challenger_max else objective)
        result.append({"team_id": team, "competition_id": str(competition),
                       "season": str(row["season"]), "objective": objective, "stakes": status,
                       "points": points, "games_remaining": games,
                       "max_points_available": games * per_win, "maximum_final_points": maximum,
                       "point_gap": target - points, "target_points": target,
                       "max_challenger_points": challenger_max, "tiebreak_status": "UNKNOWN",
                       "source_url": _source(row["source_url"]),
                       "observed_at": observed.isoformat(),
                       "rules_source_url": _source(rules["source_url"]),
                       "rules_observed_at": rules_observed.isoformat(),
                       "interpretation": "MATHEMATICAL_STAKES_NOT_MENTAL_STATE"})
    return result


def _claim(row, cutoff):
    if (row.get("content_status") != "UNVERIFIED"
            or row.get("provenance_status") != "CITATION_AND_TIMESTAMP_CHECKED"):
        raise ValueError("claim not validator-accepted")
    candidate = {**row, "claim_id": row.get("claim_id", row.get("origin_claim_id")),
                 "fetched_at": row.get("fetched_at", row.get("source_fetched_at"))}
    observed = _stamp(row.get("observed_at", candidate["fetched_at"]))
    if observed > cutoff:
        raise ValueError("future claim receipt")
    assertion, claim_id, fetched = _assertion(candidate, observed.isoformat(timespec="microseconds"))
    return {**assertion, "claim_id": claim_id, "fetched_at": fetched,
            "observed_at": observed.isoformat(),
            "status": "SOURCED_CLAIM_UNVERIFIED"}


def analyze_motivation(fixtures, *, as_of, standings=None, claims=None, standings_context=None):
    """Build deterministic advice from supplied data only, without mutating inputs."""
    cutoff = _stamp(as_of)
    fixture_rows = {str(row["fixture_id"]): row for row in fixtures}
    output = {fid: {"fixture_id": fid, "stakes": ["UNKNOWN"], "standing_evidence": [],
                   "human_factors": [], "review_required": False, "review_reason": None,
                   "advisory_only": True, "probability_adjustment": 0.0}
              for fid in fixture_rows}
    skipped = Counter()
    for row in standings or []:
        try:
            fid = _identity(row["fixture_id"])
            if fid not in fixture_rows:
                continue
            output[fid]["standing_evidence"].extend(_standing(row, fixture_rows[fid], cutoff))
        except (KeyError, TypeError, ValueError, AttributeError):
            skipped["INVALID_OR_UNAVAILABLE_STANDINGS"] += 1
    for row in claims or []:
        try:
            fid = _identity(row["fixture_id"])
            if fid not in output:
                continue
            evidence = _claim(row, cutoff)
            if evidence not in output[fid]["human_factors"]:
                output[fid]["human_factors"].append(evidence)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError, OSError):
            skipped["INVALID_OR_UNAVAILABLE_CLAIM"] += 1
    for item in output.values():
        context = (standings_context or {}).get(item["fixture_id"])
        if context is not None:
            try:
                if (_stamp(context["observed_at"]) > cutoff
                        or context.get("rules_status") != "UNVERIFIED"):
                    raise ValueError("future or mislabelled descriptive context")
                fixture = fixture_rows[item["fixture_id"]]
                league = fixture.get('provider_league_id', fixture.get("api_football_league_id", fixture.get("league_id")))
                if (str(context["league_id"]) != str(league)
                        or str(context["season"]) != str(fixture.get("season"))):
                    raise ValueError("descriptive context scope mismatch")
                for team in context["teams"]:
                    _identity(team["team_name"])
                    _integer(team["rank"], 1)
                    _integer(team["points"], -1000)
                    _integer(team["played"])
                _source(context["source_url"])
                item["standings_context"] = context
            except (KeyError, TypeError, ValueError, AttributeError):
                skipped["INVALID_OR_UNAVAILABLE_CONTEXT"] += 1
        item["stakes"] = sorted({row["stakes"] for row in item["standing_evidence"]}) or ["UNKNOWN"]
        topics = {}
        for row in item["human_factors"]:
            topics.setdefault(row["kind"], set()).add(row["claim"])
        # Different statements are retained; semantic contradictions require review.
        item["review_required"] = any(len(texts) > 1 for texts in topics.values())
        if item["review_required"]:
            item["review_reason"] = "MULTIPLE_ASSERTIONS_SAME_TOPIC_CHECK_FOR_CONTRADICTION"
        item["summary_hu"] = (
            f"Tabellatét: {', '.join(item['stakes'])}; "
            f"forrásolt, tartalmilag nem igazolt állítás: {len(item['human_factors'])}."
        )
        if item.get("standings_context"):
            for team in item["standings_context"].get("teams", []):
                item["summary_hu"] += (f" {team['team_name']}: {team['rank']}. hely, "
                                       f"{team['points']} pont, {team['played']} lejátszott meccs.")
    counts = {"fixtures": len(output),
              "with_standings": sum(bool(row["standing_evidence"]) for row in output.values()),
              "with_claims": sum(bool(row["human_factors"]) for row in output.values()),
              "review_required": sum(row["review_required"] for row in output.values())}
    return {"version": VERSION, "observed_at": cutoff.isoformat(), "advisory_only": True,
            "fixtures": list(output.values()), "counts": counts, "skipped": dict(skipped)}


def compact_motivation_context(report, *, max_claims_per_fixture=6):
    """Bounded common-batch context; omitted-claim counts remain explicit."""
    if type(max_claims_per_fixture) is not int or not 0 <= max_claims_per_fixture <= 20:
        raise ValueError("max_claims_per_fixture must be between 0 and 20")
    rows = []
    for item in report["fixtures"]:
        claims = item["human_factors"]
        rows.append({"fixture_id": item["fixture_id"], "stakes": item["stakes"],
                     "standing_evidence": item["standing_evidence"],
                     "standings_context": item.get("standings_context"),
                     "claims": [{key: claim[key] for key in ("claim_id", "kind", "claim",
                                 "source_url", "published_at", "fetched_at", "status")}
                                for claim in claims[:max_claims_per_fixture]],
                     "claims_omitted": max(0, len(claims) - max_claims_per_fixture),
                     "review_required": item["review_required"]})
    return {"version": report["version"], "as_of": report["observed_at"], "fixtures": rows,
            "policy": "ADVISORY_ONLY; sourced claims remain unverified; preserve conflicting "
                      "evidence; no invented mental state or numerical uplift; DAILY_223 unchanged."}
