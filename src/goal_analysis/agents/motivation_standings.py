"""Bounded official standings collection, without inferred qualification rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from goal_analysis.agents import canonical_sha256
from goal_analysis.providers.api_football_history import positive_id
from goal_analysis.providers.base import ProviderError

from .motivation_expert import _stamp

NAMESPACE = "motivation_standings_v1"
TTL = timedelta(hours=6)
BASE = "https://v3.football.api-sports.io/standings"


def _scope(fixture):
    league = positive_id(fixture.get("api_football_league_id", fixture.get("league_id")))
    season = fixture.get("season")
    if type(season) is not int or not 1900 <= season <= 2200:
        raise ValueError("explicit season required")
    return league, season


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 1_000_000:
        raise ValueError("invalid standings number")
    return value


def _normalize(payload, league_id, season):
    if payload.get("errors") or not isinstance(payload.get("response"), list):
        raise ValueError("invalid standings response")
    if payload.get("paging", {}).get("total", 1) not in (0, 1):
        raise ValueError("incomplete paginated standings")
    records, groups_seen = [], 0
    for entry in payload["response"]:
        league = entry["league"]
        if positive_id(league["id"]) != league_id or league["season"] != season:
            raise ValueError("standings league/season mismatch")
        groups = league["standings"]
        if not isinstance(groups, list):
            raise TypeError("invalid standings groups")
        for group in groups:
            if not isinstance(group, list):
                raise TypeError("invalid standings group")
            seen = set()
            for row in group:
                team_id = positive_id(row["team"]["id"])
                if team_id in seen:
                    raise ValueError("duplicate team in standings group")
                seen.add(team_id)
                records.append({"team_id": team_id, "team_name": row["team"]["name"],
                                "group_index": groups_seen, "group": row.get("group"),
                                "rank": _integer(row["rank"], 1),
                                "points": _integer(row["points"], -1000),
                                "played": _integer(row["all"]["played"]),
                                "goals_difference": _integer(row["goalsDiff"], -10000),
                                "form": row.get("form"), "provider_zone": row.get("description"),
                                "provider_updated_at": row.get("update"), "raw": row})
            groups_seen += 1
    return {"rows": records, "group_count": groups_seen}


def _context(fixture, snapshot):
    teams, missing, ambiguous = [], [], []
    for side in ("home", "away"):
        try:
            if fixture.get('source_provider') == 'the_stats_api':
                from goal_analysis.normalization.live_identity import field_id

                team_id = field_id(fixture, side + '_team')
            else:
                team_id = positive_id(fixture.get(f"api_football_{side}_team_id",
                                                 fixture.get(f"{side}_team_id")))
        except (TypeError, ValueError):
            missing.append(side)
            continue
        matches = [row for row in snapshot["rows"] if row["team_id"] == team_id]
        if len(matches) != 1:
            (ambiguous if matches else missing).append(team_id)
            continue
        row = matches[0]
        group = [other for other in snapshot["rows"] if other["group_index"] == row["group_index"]]
        leaders = [other for other in group if other["rank"] == 1]
        nearby = sorted((other for other in group if other["provider_zone"]
                         and other["provider_zone"] != row["provider_zone"]),
                        key=lambda other: (abs(other["rank"] - row["rank"]), other["rank"]))[:2]
        teams.append({key: row[key] for key in ("team_id", "team_name", "rank", "points",
                      "played", "goals_difference", "form", "provider_zone", "group")}
                     | {"side": side, "games_remaining": None,
                        "points_gap_to_group_leader": leaders[0]["points"] - row["points"]
                        if len(leaders) == 1 else None,
                        "nearby_provider_zones": [{"rank": other["rank"], "points": other["points"],
                            "provider_zone": other["provider_zone"],
                            "points_gap_to_team": other["points"] - row["points"]} for other in nearby]})
    return {"status": "SOURCED" if len(teams) == 2 else "PARTIAL" if teams else "UNKNOWN",
            "provider": "api_football", "league_id": snapshot["league_id"],
            "season": snapshot["season"], "observed_at": snapshot["fetched_at"],
            "source_url": snapshot["source_url"], "group_count": snapshot["group_count"],
            "complete_provider_response": True, "rules_status": "UNVERIFIED",
            "interpretation": "DESCRIPTIVE_TABLE_ONLY_NOT_QUALIFICATION_OR_MENTAL_STATE",
            "teams": teams, "missing_teams": missing, "ambiguous_teams": ambiguous}


def collect_standings(fixtures, client, cache, *, as_of, max_requests=8,
                      clock=lambda: datetime.now(UTC)):
    """Reuse six-hour cache, fetch each explicit league/season once, stop on error.

    Client owns snapshots/provider budget; caller owns remaining request allocation.
    observed_at is the actual final receipt, potentially later than start as_of.
    No retries, inferred seasons, invented remaining games, or paid model calls.
    """
    if getattr(client, 'provider', None) == 'the_stats_api':
        from .the_stats_standings import collect

        return collect(fixtures, client, as_of=as_of, max_requests=max_requests, clock=clock)
    started = _stamp(as_of)
    if type(max_requests) is not int or not 0 <= max_requests <= 40:
        raise ValueError("max_requests must be an integer between 0 and 40")
    fixtures = list(fixtures)
    scopes, errors, deferred = {}, [], []
    for fixture in fixtures:
        try:
            scopes.setdefault(_scope(fixture), []).append(fixture)
        except (TypeError, ValueError):
            errors.append({"fixture_id": str(fixture.get("fixture_id", "")), "code": "MISSING_SCOPE"})
    contexts, all_rows, requests, stopped, last = {}, [], 0, False, started
    for (league, season), members in sorted(scopes.items()):
        key, snapshot = f"{league}:{season}", None
        source = f"{BASE}?league={league}&season={season}"
        cached = cache.get(NAMESPACE, key, now=started) if cache is not None else None
        if cached is not None:
            try:
                fetched = _stamp(cached["fetched_at"])
                if (timedelta(0) <= started - fetched < TTL and cached["league_id"] == league
                        and cached["season"] == season and cached["source_url"] == source
                        and canonical_sha256(cached["payload"]) == cached["payload_sha256"]):
                    snapshot = {**cached, **_normalize(cached["payload"], league, season)}
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
        if snapshot is None:
            remaining = getattr(getattr(client, "last_usage", None), "remaining_day", None)
            if client is None or stopped or requests >= max_requests or remaining == 0:
                deferred.append({"league_id": league, "season": season,
                                 "reason": "NO_CLIENT" if client is None else "STOPPED_ON_ERROR"
                                 if stopped else "QUOTA_EXHAUSTED" if remaining == 0 else "REQUEST_CAP"})
                continue
            requests += 1
            try:
                payload = client.request("standings", {"league": int(league), "season": season})
                fetched = _stamp(clock())
                if fetched < last:
                    raise ValueError("clock moved backwards")
                normalized = _normalize(payload, league, season)
                snapshot = {**normalized, "league_id": league, "season": season,
                            "fetched_at": fetched.isoformat(), "source_url": source,
                            "payload": payload, "payload_sha256": canonical_sha256(payload)}
                if cache is not None:
                    cache.set(NAMESPACE, key, snapshot, TTL, now=fetched)
                last = fetched
            except (ProviderError, KeyError, ValueError, TypeError, AttributeError, OSError):
                errors.append({"league_id": league, "season": season, "code": "STANDINGS_UNAVAILABLE"})
                stopped = True
                continue
        all_rows.extend({**row, "league_id": league, "season": season,
                         "observed_at": snapshot["fetched_at"], "source_url": source}
                        for row in snapshot["rows"])
        for fixture in members:
            contexts[str(fixture["fixture_id"])] = _context(fixture, snapshot)
    return {"rows": all_rows, "context_by_fixture": contexts, "requests": requests,
            "errors": errors, "deferred": deferred, "observed_at": last.isoformat()}
