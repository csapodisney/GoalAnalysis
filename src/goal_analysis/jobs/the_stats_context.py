"""Bounded supplementary football facts, never ticket/model mutations.

Current squads and season totals are observed now, not historical prematch data.
Only exact names AND kickoff AND an explicit verified league mapping can join.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from goal_analysis.normalization.identity import match_fixture
from goal_analysis.normalization.live_identity import config_league_id
from goal_analysis.providers.the_stats_api import BASE_URL, TheStatsApiClient, TheStatsApiError
from goal_analysis.providers.the_stats_leagues import LEAGUES
from goal_analysis.storage import CacheStore, Database, SnapshotStore

VERSION = "thestatsapi-context-v1"
PROVIDER = "the_stats_api"
MAX_DAILY_REQUESTS = 110  # Provider allows 120/min; keep ten calls of headroom.
TEAM_SEASON_STATS_TTL = timedelta(hours=6)
# IDs checked against the complete provider catalogue on 2026-10-18.

# Pure, closed canonical fact schema shared with the Grok packet validator.
FACT_FIELDS = {
    "competition": {"competition_id", "name", "country", "season_id"},
    "team_profile": {"team_id", "name", "country", "stadium_city", "stadium_capacity"},
    "team_season_stats": {"team_id", "season_id", "competition_id", "matches_played", "wins", "draws", "losses", "points", "position", "goals_for", "goals_against", "expected_goals_for", "expected_goals_against", "xg_matches"},
    "squad": {"team_id", "players", "total", "truncated"},
    "injuries_suspensions": {"team_id", "injuries", "suspensions"},
    "lineup": {"match_id", "type", "confirmed", "home_team_id", "away_team_id", "home_players", "away_players"},
    "player_season_stats": {"player_id", "team_id", "season_id", "competition_id", "appearances", "starts", "minutes_played", "goals", "assists", "expected_goals", "expected_assists", "xg_matches"},
    "player_goal_market": {"match_id", "home_team_id", "away_team_id", "candidates"},
    "match_odds": {"match_id", "quotes", "timestamp_basis"},
    "recent_result": {"match_id", "utc_date", "status", "competition_id", "home_team_id", "away_team_id", "home_goals", "away_goals", "score_scope"},
    "recent_match_stats": {"match_id", "utc_date", "status", "home_team_id", "away_team_id", "home_xg", "away_xg", "home_shots", "away_shots", "scope"},
}
_ROWS = {
    "players": {"player_id", "name", "position"},
    "home_players": {"player_id", "name", "position"},
    "away_players": {"player_id", "name", "position"},
    "candidates": {"player_id", "name", "decimal_price", "bookmaker"},
    "quotes": {"bookmaker", "market_key", "selection_key", "point", "opening_price", "last_price"},
    "injuries": {"player_id", "status", "reason", "start_date", "expected_return", "active"},
    "suspensions": {"player_id", "reason", "start_date", "end_date", "active", "matches", "competition_id"},
}
_STRINGS = {"name", "country", "stadium_city", "position", "status", "reason", "start_date", "expected_return", "end_date", "utc_date", "scope", "score_scope", "type", "bookmaker", "market_key", "selection_key", "timestamp_basis"}
_BOOLS = {"active", "confirmed", "truncated"}


def _scalar(key, value):
    if value is None:
        return None
    if key.endswith("_id"):
        prefix = {"competition_id": "comp", "season_id": "sn", "team_id": "tm", "home_team_id": "tm", "away_team_id": "tm", "player_id": "pl", "match_id": "mt"}.get(key)
        if not prefix or not isinstance(value, str) or not re.fullmatch(prefix + r"_\d{1,30}", value):
            raise ValueError("INVALID_FACT_ID")
    elif key in _BOOLS:
        if type(value) is not bool:
            raise ValueError("INVALID_FACT_BOOLEAN")
    elif key in _STRINGS and not (key == "position" and type(value) is int):
        if not isinstance(value, str) or len(value) > 180 or any(ord(c) < 32 for c in value):
            raise ValueError("INVALID_FACT_TEXT")
    elif type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1_000_000:
        raise ValueError("INVALID_FACT_NUMBER")
    return value


def normalize_fact_data(category, data):
    """Validate a canonical allowlisted fact without I/O; reject unknown fields."""
    if category not in FACT_FIELDS or not isinstance(data, dict) or set(data) - FACT_FIELDS[category]:
        raise ValueError("UNKNOWN_FACT_FIELD")
    result = {}
    for key, value in data.items():
        if key in _ROWS:
            if not isinstance(value, list) or len(value) > 100:
                raise ValueError("INVALID_FACT_ROWS")
            rows = []
            for row in value:
                if not isinstance(row, dict) or set(row) - _ROWS[key]:
                    raise ValueError("UNKNOWN_FACT_ROW_FIELD")
                rows.append({k: _scalar(k, v) for k, v in row.items()})
            result[key] = rows
        else:
            result[key] = _scalar(key, value)
    return result


def _stamp(value):
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("AWARE_TIMESTAMP_REQUIRED")
    return stamp.astimezone(UTC)


def _name(value):
    # Case, whitespace and punctuation only; no aliases, transliteration or fuzzy match.
    return "".join(c for c in unicodedata.normalize("NFKC", str(value)).casefold() if c.isalnum())


class Resources:
    """One bounded execution, verified raw cache, and stop-on-quota/auth failure."""
    def __init__(self, root, client, clock, max_requests):
        self.root, self.client, self.clock = Path(root), client, clock
        database = Database(self.root / "data" / "goal_analysis.sqlite3")
        database.initialize()
        self.database = database
        self.cache = CacheStore(database)
        self.snapshots = SnapshotStore(database, self.root / "data" / "raw")
        self.memo = {}
        self.unavailable_capabilities = set()
        self.usage = {"requests": 0, "cache_hits": 0, "max_requests": max_requests, "stopped_reason": None, "by_endpoint": {}}

    def get(self, endpoint, params=None, *, ttl=timedelta(minutes=30), cache_only=False,
            retained_team_stats=False):
        if type(cache_only) is not bool:
            raise ValueError('cache_only must be boolean')
        if type(retained_team_stats) is not bool:
            raise ValueError('retained_team_stats must be boolean')
        if not timedelta(0) < ttl <= timedelta(hours=6):
            raise ValueError('Invalid resource cache lifetime')
        capability = 'PLAYER_PROPS' if re.fullmatch(r'/v2/football/matches/mt_\d+/odds/players', endpoint) else None
        if capability in self.unavailable_capabilities:
            return None, None, 'ADDON_REQUIRED'
        params = dict(sorted((params or {}).items()))
        if retained_team_stats and not (
                re.fullmatch(r'/football/teams/tm_\d+/stats', endpoint)
                and set(params) == {'season_id'}
                and re.fullmatch(r'sn_\d+', str(params['season_id']))
                and ttl == TEAM_SEASON_STATS_TTL):
            raise ValueError('Retained reuse is limited to identified team season stats')
        key = json.dumps([endpoint, params], sort_keys=True, separators=(",", ":"))
        memo_key = (key, ttl, retained_team_stats)
        now = _stamp(self.clock())
        if memo_key in self.memo:
            result = self.memo[memo_key]
            if result[1] is None or timedelta(0) <= now - _stamp(result[1]['observed_at']) < ttl:
                return result
        encoded = {k: str(v).lower() if type(v) is bool else v for k, v in params.items()}
        url = BASE_URL + endpoint + ("?" + urlencode(encoded) if encoded else "")
        # Verify metadata too: a forged receipt time must not make an old/future
        # snapshot usable. CacheStore's generic get verifies TTL, not its hash.
        with self.database.connect() as connection:
            cache_row = connection.execute(
                "SELECT value_json,content_hash,created_at,expires_at FROM cache_entries WHERE namespace=? AND cache_key=?",
                (VERSION, key)).fetchone()
        cached = None
        retained = False
        if cache_row:
            try:
                created, expires = _stamp(cache_row['created_at']), _stamp(cache_row['expires_at'])
                # A previous collector stored only a 30-minute cache horizon.
                # Reuse those bytes solely under the existing six-hour team
                # stats freshness contract, with a mandatory original witness.
                retained = (retained_team_stats and created < expires <= now
                            and now < created + ttl)
                if (hashlib.sha256(cache_row["value_json"].encode("utf-8")).hexdigest() == cache_row["content_hash"]
                        and (created <= now < expires or retained)):
                    cached = json.loads(cache_row["value_json"])
            except (ValueError, TypeError):
                pass
        if isinstance(cached, dict):
            try:
                source = cached["source"]
                raw = self.snapshots.read(source["content_hash"])
                if (source["source_url"] != url or source["provider"] != PROVIDER
                        or not timedelta(0) <= now - _stamp(source["observed_at"]) < ttl
                        or (retained and _stamp(source['observed_at']) != created)
                        or hashlib.sha256(raw).hexdigest() != source["content_hash"]):
                    raise ValueError("INVALID_CACHE")
                payload = json.loads(raw)
                if retained and not source.get('http_observation_id'):
                    raise ValueError('RETAINED_STATS_REQUIRE_ORIGINAL_HTTP_WITNESS')
                if source.get('http_observation_id'):
                    try:
                        from goal_analysis.storage.http_observations import read_http_observation

                        with self.database.connect() as connection:
                            read_http_observation(connection, self.root, source['http_observation_id'],
                                                  now, source=source)
                    except Exception:  # noqa: BLE001 - No refetch or fresh witness on cache failure.
                        if retained:
                            raise ValueError('INVALID_RETAINED_STATS_WITNESS') from None
                        source = {**source, 'http_observation_status': 'UNAVAILABLE'}
                self.usage["cache_hits"] += 1
                self.memo[memo_key] = (payload["data"], source, None)
                return self.memo[memo_key]
            except (ValueError, KeyError, OSError, TypeError):
                pass
        if cache_only:
            # Do not memoize a miss: a later budgeted phase may fetch it.
            return None, None, 'CACHE_MISS'
        if self.usage["stopped_reason"] or self.usage["requests"] >= self.usage["max_requests"]:
            return None, None, self.usage["stopped_reason"] or "REQUEST_BUDGET"
        self.usage["requests"] += 1
        self.usage["by_endpoint"][endpoint] = self.usage["by_endpoint"].get(endpoint, 0) + 1
        try:
            response = self.client.request(endpoint, params)
            receipt = self.client.last_response
            raw = self.client.last_raw_response
            if (not raw or not receipt or receipt["payload_sha256"] != hashlib.sha256(raw).hexdigest()
                    or receipt.get("source_url") != url):
                raise ValueError("INVALID_RAW_RECEIPT")
            observed = _stamp(receipt["observed_at"])
            if observed > _stamp(self.clock()):
                raise ValueError("FUTURE_RECEIPT")
            saved = self.snapshots.save(PROVIDER, "daily_context", raw, "application/json", observed, url)
            source = {"provider": PROVIDER, "source_url": url, "observed_at": observed.isoformat(),
                      "content_hash": saved.content_hash, "resource_type": "daily_context",
                      "published_at": None}
            # Additive availability evidence only: failure of a new observer
            # cannot veto an already validated stable provider response.
            try:
                from goal_analysis.storage.http_observations import HttpObservations

                observer = getattr(self, '_http_observations', None)
                if observer is None:
                    observer = self._http_observations = HttpObservations(self.database, self.root, clock=self.clock)
                source.update(observer.admit(receipt))
            except Exception:  # noqa: BLE001 - SHADOW provenance observer is failure isolated.
                source['http_observation_status'] = 'UNAVAILABLE'
            self.cache.set(VERSION, key, {"source": source}, ttl, now=observed)
            result = (response["data"], source, None)
        except TheStatsApiError as error:
            if error.code == 'ADDON_REQUIRED' and capability:
                self.unavailable_capabilities.add(capability)
            elif error.status in {401, 403, 429} or error.code in {"LOCAL_REQUEST_LIMIT", "MISSING_OR_INVALID_API_KEY"}:
                self.usage["stopped_reason"] = error.code
            result = (None, None, error.code)
        except (ValueError, KeyError, TypeError, OSError, RuntimeError):
            result = (None, None, "INVALID_RESPONSE")
        self.memo[memo_key] = result
        return result


def _team(row, side):
    return row.get(side + "_team", {}) if isinstance(row, dict) else {}


def _field_id(row, name):
    value = row.get(name + "_id")
    if value is None and isinstance(row.get(name), dict):
        value = row[name].get("id")
    return value


def _players(rows):
    if not isinstance(rows, list):
        return []
    return [{"player_id": r.get("id", r.get("player_id")), "name": r.get("name"), "position": r.get("position")}
            for r in rows[:100] if isinstance(r, dict)]


def _fact(record, category, subject, data, source, error=None):
    if not error:
        try:
            data = normalize_fact_data(category, data)
            if not source or _stamp(source["observed_at"]) >= _stamp(record["kickoff"]) - timedelta(minutes=15):
                raise ValueError("AFTER_LOCKOUT")
        except (ValueError, TypeError, KeyError):
            error = "INVALID_OR_LATE_FACT"
    item = {"category": category, "subject_id": subject, "status": "UNAVAILABLE" if error else "AVAILABLE",
            "data": None if error else data, "source": source, "reason": error}
    record["facts"].append(item)
    if error:
        record["missing"].append(category + ":" + error)


def collect_context(root, report, *, max_requests=MAX_DAILY_REQUESTS, client=None, clock=None,
                    request_policy='category-first-v1'):
    """Return detached facts. No ticket writes, odds calls, or historical time travel."""
    if type(max_requests) is not int or not 0 <= max_requests <= MAX_DAILY_REQUESTS:
        raise ValueError(f"MAX_REQUESTS_MUST_BE_0_TO_{MAX_DAILY_REQUESTS}")
    from .context_request_policy import FAIR, LEGACY, fixture_order

    if request_policy not in (LEGACY, FAIR):
        raise ValueError('Unknown context request policy')
    clock = clock or (lambda: datetime.now(UTC))
    started = _stamp(clock())
    from goal_analysis.storage.provider_quota import observer_for

    client = client or TheStatsApiClient(max_requests=max(1, max_requests), timeout=10, clock=clock,
                                        quota_observer=observer_for(root, clock=clock))
    resources = Resources(root, client, clock, max_requests)
    records, matched = {}, []
    fixtures = report.get("fixtures", [])
    if request_policy == FAIR:
        fixtures = sorted(fixtures, key=fixture_order)
    for fixture in fixtures:
        fid = str(fixture.get("fixture_id", ""))
        record = {"fixture_id": fid, "kickoff": fixture.get("kickoff"), "status": "UNMATCHED", "mapping": None, "facts": [], "missing": []}
        records[fid] = record
        try:
            kickoff = _stamp(record["kickoff"])
        except ValueError:
            record["missing"].append("INVALID_KICKOFF")
            continue
        if kickoff - timedelta(minutes=15) <= _stamp(clock()):
            record["status"] = "LOCKED"
            record["missing"].append("PREMATCH_LOCKOUT")
            continue
        league = LEAGUES.get(str(config_league_id(fixture)))
        if not league:
            record["missing"].append("NO_VERIFIED_LEAGUE_MAPPING")
            continue
        comp, comp_name, country = league
        detail, comp_source, error = resources.get(f"/football/competitions/{comp}")
        if error or not isinstance(detail, dict) or detail.get("id") != comp or _name(detail.get("name")) != _name(comp_name) or _name(detail.get("country")) != _name(country):
            record["missing"].append(error or "COMPETITION_IDENTITY_MISMATCH")
            continue
        day = kickoff.date().isoformat()
        rows, mapping_source, error = resources.get("/football/matches", {"competition_id": comp, "date_from": day, "date_to": day, "utc_offset": "+00:00", "per_page": 100})
        resolution = match_fixture(fixture, rows, competition_id=comp, as_of=clock())
        if resolution["status"] != "MATCHED":
            record["status"] = ("LOCKED" if resolution["status"] == "LOCKED" else
                                "AMBIGUOUS" if resolution["reason"] == "AMBIGUOUS_EXACT_MATCH"
                                else "UNMATCHED")
            record["missing"].append(error or "MISSING_LINK:" + resolution["reason"])
            continue
        row = resolution["row"]
        mapping = {"method": "EXACT_NAMES_KICKOFF_EXPLICIT_LEAGUE", "match_id": row.get("id"), "competition_id": comp,
                   "season_id": _field_id(row, "season"), "home_team_id": _team(row, "home").get("id"),
                   "away_team_id": _team(row, "away").get("id"), "source": mapping_source}
        try:
            for key in ("match_id", "season_id", "home_team_id", "away_team_id"):
                if mapping[key] is None:
                    raise ValueError("MISSING_ID")
                _scalar(key, mapping[key])
            if mapping["home_team_id"] == mapping["away_team_id"]:
                raise ValueError("IDENTICAL_TEAMS")
        except ValueError:
            record["missing"].append("INVALID_PROVIDER_IDENTITIES")
            continue
        record.update(status="MATCHED", mapping=mapping)
        _fact(record, "competition", comp, {"competition_id": comp, "name": comp_name, "country": country, "season_id": mapping["season_id"]}, comp_source)
        matched.append(record)

    # Core prematch layers first. This order gives every matched fixture team xG,
    # active absences, lineup and goalscorer-market coverage before spending the
    # remaining budget on individual player season records.
    if request_policy == FAIR:
        _collect_fair_core(resources, matched)
    categories = (("player_goal_market", "match_odds") if request_policy == FAIR else
                  ("team_season_stats", "injuries_suspensions", "lineup", "player_goal_market", "match_odds"))
    for category in categories:
        for record in matched:
            if _stamp(clock()) >= _stamp(record["kickoff"]) - timedelta(minutes=15):
                record["status"] = "LOCKED"
                record["missing"].append("LOCKED_DURING_COLLECTION")
                continue
            mapping = record["mapping"]
            subjects = ([mapping["home_team_id"], mapping["away_team_id"]]
                        if category in {"team_season_stats", "injuries_suspensions"}
                        else [mapping["match_id"]])
            for subject in subjects:
                _collect_fact(resources, record, category, subject)

    # Resolve positions only for teams that actually have an active absence.
    # This keeps the daily request budget focused while allowing goalkeeper and
    # defender absences to be treated differently from attacking absences.
    for record in matched:
        _collect_absence_squads(resources, record)

    # The player market supplies a bounded, provider-identified shortlist. Fetch
    # at most two attacking candidates per team (four per fixture). This avoids
    # treating a single scorer as the whole attack while retaining rate-limit
    # headroom on large fixture days.
    for record in matched:
        _collect_key_scorers(resources, record)

    counts = Counter(r["status"] for r in records.values())
    facts = [f for r in records.values() for f in r["facts"]]
    available = [f for f in facts if f["status"] == "AVAILABLE"]
    gaps = Counter(code for r in records.values() for code in r["missing"])
    coverage = {"fixtures": len(records), "fixtures_total": len(records), "matched": counts["MATCHED"], "fixtures_matched": counts["MATCHED"],
                "unmatched": counts["UNMATCHED"], "ambiguous": counts["AMBIGUOUS"], "locked": counts["LOCKED"],
                "available_facts": len(available), "missing_facts": len(facts) - len(available), "gaps": dict(gaps),
                "teams": len({f["subject_id"] for f in available if f["subject_id"].startswith("tm_")}),
                "players": len({p["player_id"] for f in available for p in f["data"].get("players", [])}),
                "players_with_season_stats": len({f["subject_id"] for f in available if f["category"] == "player_season_stats"}),
                "player_stats_scope": "AT_MOST_TWO_ATTACKERS_PER_TEAM_MARKET_OR_ROSTER_WITH_ACTIVE_ABSENCES_PRIORITIZED",
                "squads_for_active_absences": sum(
                    f["category"] == "squad" for f in available),
                "active_absence_positions_mapped": _mapped_absence_count(records),
                "shadow_odds_quotes": sum(len(f["data"].get("quotes", [])) for f in available
                                           if f["category"] == "match_odds"),
                "with_xg": sum(f["category"] in {"team_season_stats", "recent_match_stats", "player_season_stats"} and any(v is not None for k, v in f["data"].items() if "expected_goals" in k or k in {"home_xg", "away_xg"}) for f in available),
                "with_lineups": sum(f["category"] == "lineup" for f in available)}
    result = {"version": VERSION, "provider": PROVIDER, "status": "AVAILABLE" if available and not gaps else "PARTIAL" if available else "UNAVAILABLE",
            "date": report.get("date"), "run_id": report.get("run_id"), "started_at": started.isoformat(), "observed_at": _stamp(clock()).isoformat(),
            "context_by_fixture": records, "coverage": coverage, "usage": resources.usage}
    if request_policy == FAIR:
        result.update(mode='SHADOW', decision_effect='NONE', request_policy=request_policy,
            scheduling={'core_jobs': 5 * len(matched), 'cache_prepass': True,
                        'order': 'KICKOFF_ID_LINEUP_FIRST_THEN_FIXTURE_ROUND_ROBIN',
                        'request_cap_increased': False,
                        'available_core_facts': sum(f['category'] in {
                            'lineup', 'team_season_stats', 'injuries_suspensions'} for f in available),
                        'missing_facts_remain': len(facts) != len(available)})
    from goal_analysis.jobs.league_coverage import inspect_league_coverage

    try:
        result["league_coverage"] = inspect_league_coverage(root,
            {**report, "sports_supplement": {"the_stats_api": result}}, as_of=result["observed_at"])
    except Exception:  # noqa: BLE001 - inventory must not discard valid collected facts.
        result["league_coverage"] = {"mode": "SHADOW", "status": "UNAVAILABLE",
                                    "reason": "LEAGUE_COVERAGE_AUDIT_FAILED", "external_requests": 0}
    return result


def _collect_fair_core(resources, matched):
    from .context_request_policy import core_plan

    plan = core_plan(matched)
    done = set()
    for cache_only in (True, False):
        for index, (record, category, subject) in enumerate(plan):
            if index in done:
                continue
            if _stamp(resources.clock()) >= _stamp(record['kickoff']) - timedelta(minutes=15):
                record['status'] = 'LOCKED'
                if 'LOCKED_DURING_COLLECTION' not in record['missing']:
                    record['missing'].append('LOCKED_DURING_COLLECTION')
                done.add(index)
                continue
            if _collect_fact(resources, record, category, subject, cache_only=cache_only):
                done.add(index)


def _collect_fact(resources, record, category, subject, *, cache_only=False):
    if _stamp(resources.clock()) >= _stamp(record['kickoff']) - timedelta(minutes=15):
        record['status'] = 'LOCKED'
        _fact(record, category, subject, None, None, 'PREMATCH_LOCKOUT')
        return True
    mapping = record["mapping"]
    season, comp = mapping["season_id"], mapping["competition_id"]
    if category == "player_season_stats":
        endpoint, params = f"/football/players/{subject}/stats", {"season_id": season, "competition_id": comp}
    elif category == "recent_result":
        endpoint, params = "/football/matches", {"team_id": subject, "status": "finished", "date_to": (_stamp(record["kickoff"]).date() - timedelta(days=1)).isoformat(), "sort": "-utc_date", "per_page": 1}
    elif category == "lineup":
        endpoint, params = f"/football/matches/{subject}/lineups", {}
    elif category == "player_goal_market":
        endpoint, params = f"/v2/football/matches/{subject}/odds/players", {"bookmaker": "bet365"}
    elif category == "match_odds":
        endpoint, params = f"/football/matches/{subject}/odds", {"bookmaker": "bet365,pinnacle"}
    else:
        suffix = {"team_season_stats": "/stats", "injuries_suspensions": "/injuries-suspensions", "squad": "/players", "team_profile": ""}[category]
        endpoint = f"/football/teams/{subject}" + suffix
        params = {"season_id": season} if category == "team_season_stats" else {"current": True} if category == "injuries_suspensions" else {}
    lifetime = ({'ttl': TEAM_SEASON_STATS_TTL, 'retained_team_stats': True}
                if category == 'team_season_stats' else {})
    raw, source, error = resources.get(endpoint, params, cache_only=cache_only, **lifetime)
    if cache_only and error == 'CACHE_MISS':
        return False
    data = None
    try:
        if not error:
            data = _canonical(category, raw, subject, mapping)
            if category == "recent_result":
                if data["match_id"] == mapping["match_id"] or data["status"] != "finished" or _stamp(data["utc_date"]) >= min(_stamp(record["kickoff"]), _stamp(source["observed_at"])):
                    raise ValueError("NOT_PRIOR_COMPLETED_MATCH")
                if subject not in {data["home_team_id"], data["away_team_id"]}:
                    raise ValueError("WRONG_TEAM")
    except (ValueError, KeyError, TypeError, IndexError):
        error = "INVALID_PROVIDER_DATA"
    _fact(record, category, subject, data, source, error)
    if category == "recent_result" and not error:
        stats, stats_source, stats_error = resources.get(f"/football/matches/{data['match_id']}/stats",
                                                       cache_only=cache_only)
        stats_data = None
        try:
            if not stats_error:
                if not isinstance(stats, dict) or stats.get("match_id") != data["match_id"]:
                    raise ValueError("MATCH_ID_MISMATCH")
                stats_data = {k: data[k] for k in ("match_id", "utc_date", "status", "home_team_id", "away_team_id")}
                stats_data["scope"] = "PROVIDER_ALL_EXTRA_TIME_SCOPE_UNKNOWN"
                overview = stats.get("overview", {})
                for prefix, metric in (("xg", "expected_goals"), ("shots", "total_shots")):
                    values = overview.get(metric, {}).get("all", {})
                    for side in ("home", "away"):
                        stats_data[side + "_" + prefix] = values.get(side)
        except (ValueError, KeyError, TypeError, AttributeError):
            stats_error = "INVALID_PROVIDER_DATA"
        _fact(record, "recent_match_stats", data["match_id"], stats_data, stats_source, stats_error)
    return True


def _collect_key_scorers(resources, record):
    market = next((fact for fact in record["facts"]
                   if fact["category"] == "player_goal_market"
                   and fact["status"] == "AVAILABLE"), None)
    mapping = record["mapping"]
    if not market:
        # Player props are a separate subscription. Their absence must not
        # discard ordinary player season xG or the active absence check.
        absences = {row.get('player_id') for fact in record['facts']
                    if fact['category'] == 'injuries_suspensions' and fact['status'] == 'AVAILABLE'
                    for row in [*fact['data'].get('injuries', []), *fact['data'].get('suspensions', [])]
                    if row.get('active') is True}
        for team_id in (mapping['home_team_id'], mapping['away_team_id']):
            squad = next((fact for fact in record['facts'] if fact['category'] == 'squad'
                          and fact['subject_id'] == team_id and fact['status'] == 'AVAILABLE'), None)
            if squad is None:
                _collect_fact(resources, record, 'squad', team_id)
                squad = record['facts'][-1]
            if squad['status'] != 'AVAILABLE':
                continue
            attackers = [row for row in squad['data'].get('players', [])
                         if row.get('player_id') in absences or str(row.get('position', '')).upper() in
                         {'F', 'FW', 'FORWARD', 'ATTACKER', 'M', 'MF', 'MIDFIELDER'}]
            attackers.sort(key=lambda row: (row['player_id'] not in absences,
                str(row.get('position', '')).upper() not in {'F', 'FW', 'FORWARD', 'ATTACKER'}, row['player_id']))
            for player in attackers[:2]:
                _collect_fact(resources, record, 'player_season_stats', player['player_id'])
        return
    found = Counter()
    attempted = set()
    for candidate in market["data"].get("candidates", [])[:8]:
        if (sum(found.values()) == 4
                or all(found[team_id] >= 2 for team_id in
                       (mapping["home_team_id"], mapping["away_team_id"]))
                or resources.usage["requests"] >= resources.usage["max_requests"]):
            break
        player_id = candidate["player_id"]
        if player_id in attempted:
            continue
        attempted.add(player_id)
        _collect_fact(resources, record, "player_season_stats", player_id)
        fact = record["facts"][-1]
        if fact["category"] == "player_season_stats" and fact["status"] == "AVAILABLE":
            team_id = fact["data"].get("team_id")
            if (team_id in {mapping["home_team_id"], mapping["away_team_id"]}
                    and found[team_id] < 2):
                found[team_id] += 1


def _collect_absence_squads(resources, record):
    """Fetch a team roster only where a sourced active absence needs a position."""
    teams = set()
    for fact in record.get("facts", []):
        if fact.get("category") != "injuries_suspensions" or fact.get("status") != "AVAILABLE":
            continue
        data = fact.get("data") or {}
        rows = list(data.get("injuries", [])) + list(data.get("suspensions", []))
        if any(row.get("active") is True for row in rows):
            teams.add(fact.get("subject_id"))
    for team_id in sorted(teams):
        if resources.usage["requests"] >= resources.usage["max_requests"]:
            break
        _collect_fact(resources, record, "squad", team_id)


def _mapped_absence_count(records):
    total = 0
    for record in records.values():
        positions = {}
        active = set()
        for fact in record.get("facts", []):
            if fact.get("status") != "AVAILABLE":
                continue
            data = fact.get("data") or {}
            if fact.get("category") == "squad":
                positions.update({row.get("player_id"): row.get("position")
                                  for row in data.get("players", [])})
            elif fact.get("category") == "injuries_suspensions":
                active.update(row.get("player_id") for row in
                              list(data.get("injuries", [])) + list(data.get("suspensions", []))
                              if row.get("active") is True)
        total += sum(player_id in positions and positions[player_id] is not None
                     for player_id in active)
    return total


def _odds_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 1 else None


def _market_quotes(bookmaker, internal, values, *, point=None):
    rows = []
    if not isinstance(values, dict):
        return rows
    for selection, raw in values.items():
        if selection not in {"home", "draw", "away", "yes", "no", "over", "under"}:
            continue
        raw = raw if isinstance(raw, dict) else {"last_seen": raw}
        opening, last = _odds_number(raw.get("opening")), _odds_number(raw.get("last_seen"))
        if last is None:
            continue
        rows.append({"bookmaker": bookmaker, "market_key": internal,
                     "selection_key": selection, "point": point,
                     "opening_price": opening, "last_price": last})
    return rows


def _canonical_match_odds(raw, subject):
    if raw.get("match_id") != subject or not isinstance(raw.get("bookmakers"), list):
        raise ValueError("MATCH_ODDS_IDENTITY_MISMATCH")
    quotes = []
    for book in raw["bookmakers"]:
        if not isinstance(book, dict) or not isinstance(book.get("bookmaker"), str):
            continue
        name, markets = book["bookmaker"], book.get("markets")
        if not isinstance(markets, dict):
            continue
        for provider_key, internal in (("match_odds", "h2h"), ("btts", "btts"),
                                       ("first_half_result", "h2h_3_way_h1"),
                                       ("btts_first_half", "btts_h1")):
            quotes.extend(_market_quotes(name, internal, markets.get(provider_key)))
        for provider_key, internal in (("total_goals", "totals"),
                                       ("first_half_total_goals", "totals_h1")):
            totals = markets.get(provider_key)
            if not isinstance(totals, dict):
                continue
            for line, values in totals.items():
                try:
                    point = float(line)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(point) and 0 < point < 20:
                    quotes.extend(_market_quotes(name, internal, values, point=point))
    quotes.sort(key=lambda row: (row["bookmaker"], row["market_key"],
                                 row["point"] or -1, row["selection_key"]))
    return {"match_id": subject, "quotes": quotes[:100],
            "timestamp_basis": "PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME"}


def _canonical(category, raw, subject, mapping):
    if category == "squad":
        if not isinstance(raw, list):
            raise ValueError("SQUAD_SHAPE")
        if any(not isinstance(row, dict) or (row.get("current_team") or {}).get("id") != subject for row in raw):
            raise ValueError("SQUAD_TEAM_MISMATCH")
        return {"team_id": subject, "players": _players(raw), "total": len(raw), "truncated": len(raw) > 100}
    if category == "recent_result":
        row = raw[0]
        score = row.get("score", {}).get("regulation") or {}
        return {"match_id": row["id"], "utc_date": row["utc_date"], "status": row["status"], "competition_id": _field_id(row, "competition"),
                "home_team_id": _team(row, "home").get("id"), "away_team_id": _team(row, "away").get("id"),
                "home_goals": score.get("home"), "away_goals": score.get("away"), "score_scope": "FULL_TIME_90_MINUTES"}
    if not isinstance(raw, dict):
        raise TypeError("OBJECT_REQUIRED")
    if category == "team_profile":
        if raw.get("id") != subject:
            raise ValueError("TEAM_MISMATCH")
        stadium = raw.get("stadium") or {}
        return {"team_id": subject, "name": raw.get("name"), "country": raw.get("country"), "stadium_city": stadium.get("city"), "stadium_capacity": stadium.get("capacity")}
    if category in {"team_season_stats", "player_season_stats"}:
        key = "team_id" if category == "team_season_stats" else "player_id"
        if raw.get(key) != subject or raw.get("season_id") != mapping["season_id"] or raw.get("competition_id") != mapping["competition_id"]:
            raise ValueError("STATS_IDENTITY_MISMATCH")
        data = {k: raw[k] for k in FACT_FIELDS[category] if k in raw}
        if category == "player_season_stats":
            if raw.get("team_id") not in {mapping["home_team_id"], mapping["away_team_id"]}:
                raise ValueError("PLAYER_TEAM_MISMATCH")
            for key in ("goals", "assists", "expected_goals", "expected_assists", "xg_matches"):
                data[key] = (raw.get("scoring") or {}).get(key)
        return data
    if category == "injuries_suspensions":
        result = {"team_id": subject}
        for kind in ("injuries", "suspensions"):
            if not isinstance(raw.get(kind), list):
                raise TypeError("MISSING_ABSENCE_LIST")
            result[kind] = []
            for row in raw[kind]:
                data = {k: row[k] for k in _ROWS[kind] if k in row}
                if kind == "suspensions":
                    data["competition_id"] = _field_id(row, "competition")
                result[kind].append(data)
        return result
    if category == "lineup":
        if raw.get("match_id") != subject or raw.get("type") not in {"predicted", "team_sheet"}:
            raise ValueError("NOT_PREMATCH_LINEUP")
        result = {"match_id": subject, "type": raw["type"], "confirmed": raw.get("confirmed")}
        for side in ("home", "away"):
            team = raw.get(side, {})
            if team.get("id") != mapping[side + "_team_id"]:
                raise ValueError("LINEUP_TEAM_MISMATCH")
            result[side + "_team_id"] = team["id"]
            result[side + "_players"] = _players(team.get("starting_xi"))
        return result
    if category == "player_goal_market":
        if (raw.get("match_id") != subject
                or (raw.get("home_team") or {}).get("id") != mapping["home_team_id"]
                or (raw.get("away_team") or {}).get("id") != mapping["away_team_id"]):
            raise ValueError("PLAYER_MARKET_MATCH_MISMATCH")
        candidates = {}
        bookmakers = raw.get("bookmakers")
        if not isinstance(bookmakers, list):
            raise TypeError("PLAYER_MARKET_SHAPE")
        for book in bookmakers:
            bookmaker = book.get("bookmaker")
            for market in book.get("markets", []) if isinstance(book, dict) else []:
                if market.get("name") != "anytime_goalscorer":
                    continue
                for player in market.get("players", []):
                    player_id, price = player.get("id"), player.get("odd")
                    if (not isinstance(player_id, str) or not re.fullmatch(r"pl_\d{1,30}", player_id)
                            or type(price) not in (int, float) or not math.isfinite(price) or price <= 1):
                        continue
                    row = {"player_id": player_id, "name": player.get("name"),
                           "decimal_price": price, "bookmaker": bookmaker}
                    if player_id not in candidates or price < candidates[player_id]["decimal_price"]:
                        candidates[player_id] = row
        return {"match_id": subject, "home_team_id": mapping["home_team_id"],
                "away_team_id": mapping["away_team_id"],
                "candidates": sorted(candidates.values(),
                                     key=lambda row: (row["decimal_price"], row["player_id"]))[:20]}
    if category == "match_odds":
        return _canonical_match_odds(raw, subject)
    raise ValueError("UNKNOWN_CATEGORY")
