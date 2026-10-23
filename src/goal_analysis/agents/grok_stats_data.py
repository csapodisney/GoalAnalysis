"""Validate already collected TheStatsAPI evidence; no I/O or additional requests."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlsplit

VERSION = "thestatsapi-context-v1"
CATEGORIES = {"competition", "team_profile", "team_season_stats", "squad",
              "injuries_suspensions", "lineup", "player_season_stats", "player_goal_market",
              "match_odds", "recent_result", "recent_match_stats"}
_SECRET = re.compile(r"(?:xai-|nvapi-|sk-)[A-Za-z0-9_-]{12,}|Bearer\s+\S+", re.IGNORECASE)


def _stamp(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Invalid supplement timestamp")
    return result.astimezone(UTC)


def _text(value, maximum=200):
    if (not isinstance(value, str) or not value or len(value) > maximum
            or _SECRET.search(value) or any(ord(c) < 32 for c in value)):
        raise ValueError("Invalid supplement text")
    return value


def _source(value, cutoff):
    from goal_analysis.providers.the_stats_api import _route

    required = {"provider", "source_url", "observed_at", "content_hash", "resource_type", "published_at"}
    optional = {'http_observation_id', 'http_receipt_id', 'http_observation_status'}
    if (not isinstance(value, dict) or not required <= set(value) or not set(value) <= required | optional
            or value["provider"] != "the_stats_api" or value["published_at"] is not None):
        raise ValueError("Invalid supplement source")
    # Optional acquisition references are syntax/provenance metadata here,
    # never a substitute for the physical receipt reader's SHA/time checks.
    if set(value) & optional:
        status = value.get('http_observation_status')
        ids = {key for key in ('http_observation_id', 'http_receipt_id') if key in value}
        if (status not in {'VERIFIED', 'UNAVAILABLE'}
                or ids and ids != {'http_observation_id', 'http_receipt_id'}
                or status == 'VERIFIED' and not ids
                or ids and (not re.fullmatch('[0-9a-f]{32}', str(value['http_observation_id']))
                            or not re.fullmatch('[0-9a-f]{64}', str(value['http_receipt_id'])))):
            raise ValueError('Invalid HTTP acquisition reference')
    url = _text(value["source_url"], 1200)
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "api.thestatsapi.com"
            or not parsed.path.startswith("/api/") or parsed.fragment):
        raise ValueError("Invalid supplement provider")
    _, allowed, _, required = _route(parsed.path.removeprefix("/api"))
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if (len({key for key, _ in query}) != len(query) or not {key for key, _ in query} <= allowed
            or not required <= {key for key, _ in query}
            or any(not value or len(value) > 200 or _SECRET.search(value) for _, value in query)):
        raise ValueError("Invalid supplement source parameters")
    if _stamp(value["observed_at"]) > cutoff or not re.fullmatch(r"[a-f0-9]{64}", value["content_hash"]):
        raise ValueError("Future or unbound supplement source")
    return {**value, "resource_type": _text(value["resource_type"], 80),
            "observed_at": _stamp(value["observed_at"]).isoformat()}


def _data(value, depth=0):
    """Accept finite bounded provider values, never credentials or executable fields."""
    if depth > 7:
        raise ValueError("Supplement data too deeply nested")
    if value is None or type(value) is bool:
        return value
    if type(value) in (int, float):
        if not math.isfinite(value) or abs(value) > 1e12:
            raise ValueError("Invalid supplement number")
        return value
    if isinstance(value, str):
        return _text(value, 600) if value else value
    if isinstance(value, list) and len(value) <= 100:
        return [_data(item, depth + 1) for item in value]
    if isinstance(value, dict) and len(value) <= 100:
        if any(not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key)
               or key in {"api_key", "token", "password", "secret", "instructions", "prompt", "raw"}
               for key in value):
            raise ValueError("Invalid supplement field")
        return {key: _data(item, depth + 1) for key, item in value.items()}
    raise ValueError("Invalid supplement value")


def _fact_scope(fact, mapping, kickoff, cutoff):
    category, subject, data = fact["category"], fact["subject_id"], fact["data"]
    teams = {mapping["home_team_id"], mapping["away_team_id"]}
    source = urlsplit(fact["source"]["source_url"])
    path = source.path.removeprefix("/api")
    expected = {"competition": f"/football/competitions/{subject}",
        "team_profile": f"/football/teams/{subject}", "team_season_stats": f"/football/teams/{subject}/stats",
        "squad": f"/football/teams/{subject}/players", "injuries_suspensions": f"/football/teams/{subject}/injuries-suspensions",
        "lineup": f"/football/matches/{mapping['match_id']}/lineups",
        "player_season_stats": f"/football/players/{subject}/stats",
        "player_goal_market": f"/v2/football/matches/{mapping['match_id']}/odds/players",
        "match_odds": f"/football/matches/{mapping['match_id']}/odds",
        "recent_match_stats": f"/football/matches/{subject}/stats"}
    if category in expected and path != expected[category]:
        raise ValueError("Supplement resource does not match its fact")
    query = dict(parse_qsl(source.query))
    if category in {"team_season_stats", "player_season_stats"} and query.get("season_id") != mapping["season_id"]:
        raise ValueError("Supplement statistics season mismatch")
    if category == "competition" and subject != mapping["competition_id"]:
        raise ValueError("Supplement competition mismatch")
    if category in {"team_profile", "team_season_stats", "squad", "injuries_suspensions"} and subject not in teams:
        raise ValueError("Supplement team mismatch")
    if category == "lineup" and subject not in teams | {mapping["match_id"]}:
        raise ValueError("Supplement lineup mismatch")
    if category in {"recent_result", "recent_match_stats"}:
        match_id = data.get("match_id", data.get("id"))
        event = _stamp(data.get("kickoff", data.get("utc_date")))
        participants = {data.get("home_team_id"), data.get("away_team_id")}
        if ((category == "recent_match_stats" and match_id != subject)
                or (category == "recent_result" and (subject not in teams & participants
                    or path != "/football/matches" or query.get("team_id") != subject))
                or not teams & participants or match_id == mapping["match_id"] or not re.fullmatch(r"mt_\d+", str(match_id))
                or event >= cutoff or event >= kickoff
                or str(data.get("status", "")).upper() not in {"FT", "FINISHED", "COMPLETED", "AET", "PEN"}):
            raise ValueError("Current or unfinished match cannot be prematch history")
    if category == "player_season_stats" and not re.fullmatch(r"pl_\d+", subject):
        raise ValueError("Supplement player mismatch")
    if category == "player_season_stats" and (data.get("player_id") != subject or data.get("team_id") not in teams):
        raise ValueError("Supplement player team mismatch")
    if category == "player_goal_market" and (subject != mapping["match_id"]
            or data.get("match_id") != mapping["match_id"]
            or {data.get("home_team_id"), data.get("away_team_id")} != teams):
        raise ValueError("Supplement player market mismatch")
    if category == "match_odds" and (subject != mapping["match_id"]
            or data.get("match_id") != mapping["match_id"]
            or data.get("timestamp_basis") != "PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME"):
        raise ValueError("Supplement match odds mismatch")
    if category in {"team_profile", "team_season_stats", "squad", "injuries_suspensions"} and data.get("team_id") != subject:
        raise ValueError("Supplement data identity mismatch")
    if category in {"team_season_stats", "player_season_stats"} and data.get("season_id") != mapping["season_id"]:
        raise ValueError("Supplement data season mismatch")
    if category == "lineup" and (data.get("match_id") != mapping["match_id"]
            or data.get("type") not in {"predicted", "team_sheet"}
            or any(data.get(side + "_team_id") != mapping[side + "_team_id"] for side in ("home", "away"))):
        raise ValueError("Supplement lineup identity mismatch")


def validate_stats_context(value, fixture, cutoff):
    from goal_analysis.jobs.the_stats_context import LEAGUES, normalize_fact_data

    cutoff = _stamp(cutoff)
    if (not isinstance(value, dict) or value.get("version") != VERSION
            or value.get("provider") != "the_stats_api"
            or value.get("fixture_id", fixture["fixture_id"]) != fixture["fixture_id"]
            or _stamp(value.get("kickoff", fixture["kickoff"])) != _stamp(fixture["kickoff"])):
        raise ValueError("Invalid supplement fixture identity")
    observed = _stamp(value["observed_at"])
    if observed > cutoff or observed >= _stamp(fixture["kickoff"]):
        raise ValueError("Future supplement context")
    if value.get("status") not in {"MATCHED", "UNMATCHED", "AMBIGUOUS", "LOCKED", "UNAVAILABLE"}:
        raise ValueError("Invalid supplement status")
    # The enclosing fixture already carries this identity and kickoff; avoid
    # repeating them in the nested transport representation.
    clean = {key: value[key] for key in ("version", "provider", "status")}
    clean["observed_at"] = observed.isoformat()
    mapping = value.get("mapping")
    if value["status"] == "MATCHED":
        if not isinstance(mapping, dict) or mapping.get("method") != "EXACT_NAMES_KICKOFF_EXPLICIT_LEAGUE":
            raise ValueError("Unverified supplement mapping")
        for key, prefix in (("match_id", "mt"), ("competition_id", "comp"), ("season_id", "sn"),
                            ("home_team_id", "tm"), ("away_team_id", "tm")):
            if not re.fullmatch(prefix + r"_\d+", str(mapping.get(key, ""))):
                raise ValueError("Invalid mapped provider identity")
        from goal_analysis.normalization.live_identity import (
            config_league_id,
            field_id,
            fixture_identity,
        )

        league = LEAGUES.get(str(config_league_id(fixture)))
        if not league or league[0] != mapping["competition_id"]:
            raise ValueError("Supplement league mapping mismatch")
        if (fixture.get('source_provider') == 'the_stats_api'
                and (mapping['match_id'] != fixture_identity(fixture)
                    or mapping['season_id'] != fixture.get('provider_season_id')
                    or mapping['home_team_id'] != field_id(fixture, 'home_team')
                    or mapping['away_team_id'] != field_id(fixture, 'away_team'))):
            raise ValueError('Native supplement identity mismatch')
        clean["mapping"] = {key: mapping[key] for key in ("method", "match_id", "competition_id",
                                                        "season_id", "home_team_id", "away_team_id")}
        clean["mapping"]["source"] = _source(mapping["source"], observed)
        mapping_path = urlsplit(clean["mapping"]["source"]["source_url"]).path
        if mapping_path not in {"/api/football/matches", f"/api/football/matches/{mapping['match_id']}"}:
            raise ValueError("Mapping must originate in official fixture data")
    elif mapping:
        raise ValueError("Unexpected unverified mapping")
    facts = value.get("facts", [])
    if not isinstance(facts, list) or len(facts) > 100:
        raise ValueError("Too many supplement facts")
    clean["facts"] = []
    for fact in facts:
        if fact.get("category") not in CATEGORIES or fact.get("status") not in {"AVAILABLE", "UNAVAILABLE", "INVALID", "BUDGET"}:
            raise ValueError("Invalid supplement fact category")
        item = {key: fact[key] for key in ("category", "status")}
        item["subject_id"] = _text(fact["subject_id"], 160)
        if fact["status"] == "AVAILABLE":
            if value["status"] != "MATCHED":
                raise ValueError("Supplement facts lack verified fixture mapping")
            item["source"] = _source(fact["source"], observed)
            item["data"] = _data(normalize_fact_data(fact["category"], fact["data"]))
            _fact_scope(item, mapping, _stamp(fixture["kickoff"]), observed)
        elif fact.get("data"):
            raise ValueError("Unavailable supplement fact contains data")
        clean["facts"].append(item)
    missing = value.get("missing", [])
    if not isinstance(missing, list) or len(missing) > 100:
        raise ValueError("Invalid supplement omissions")
    clean["missing"] = sorted({_text(code, 160) for code in missing})
    return clean


def stats_contexts(report, cutoff):
    supplemental = report.get("sports_supplement", {}).get("the_stats_api")
    if not supplemental:
        return {}
    if supplemental.get("status") == "UNAVAILABLE" and not supplemental.get("context_by_fixture"):
        observed = _stamp(supplemental["observed_at"])
        if observed > _stamp(cutoff):
            raise ValueError("Future unavailable sports supplement")
        return {fixture["fixture_id"]: {"version": VERSION, "provider": "the_stats_api",
            "fixture_id": fixture["fixture_id"], "kickoff": fixture["kickoff"],
            "observed_at": observed.isoformat(), "status": "UNAVAILABLE", "facts": [],
            "missing": ["THE_STATS_CONTEXT_UNAVAILABLE"]} for fixture in report.get("fixtures", [])
            if _stamp(fixture["kickoff"]) > _stamp(cutoff)}
    if supplemental.get("version") != VERSION or supplemental.get("provider") != "the_stats_api":
        raise ValueError("Invalid sports supplement provider")
    observed = _stamp(supplemental["observed_at"])
    if observed > _stamp(cutoff) or supplemental.get("date") != report["date"]:
        raise ValueError("Invalid sports supplement scope")
    contexts = supplemental.get("context_by_fixture", {})
    return {fixture["fixture_id"]: validate_stats_context({**contexts[fixture["fixture_id"]],
        "version": VERSION, "provider": "the_stats_api", "observed_at": observed.isoformat()}, fixture, cutoff)
        for fixture in report.get("fixtures", []) if fixture["fixture_id"] in contexts
        and _stamp(fixture["kickoff"]) > _stamp(cutoff)}
