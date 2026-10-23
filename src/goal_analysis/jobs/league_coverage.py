"""Read-only coverage inventory; catalogue claims never prove downloaded facts.

New catalogue matches are diagnostic proposals, not collector activation. The
existing explicit league map remains the only live collection routing map.
"""
import hashlib
import json
import re
import sqlite3
from collections import Counter
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.identity import exact_name
from goal_analysis.normalization.live_identity import config_league_id

VERSION = "league-coverage-v1"
CAPABILITIES = ("has_team_stats", "has_player_stats", "xg_available",
                "odds_available", "live_odds_available")


def _same_name(first, second):
    return (isinstance(first, str) and bool(first.strip())
            and isinstance(second, str) and bool(second.strip())
            and exact_name(first) == exact_name(second))


def _snapshot(root, row, as_of):
    observed = utc(row["fetched_at"])
    if observed > as_of:
        raise ValueError("FUTURE_SNAPSHOT")
    url = urlsplit(row["source_url"])
    host = {"the_stats_api": "api.thestatsapi.com",
            "api_football": "v3.football.api-sports.io"}[row["provider"]]
    if (url.scheme != "https" or url.hostname != host or url.username or url.password
            or url.fragment or any(re.search(r"key|token|secret|password", k, re.IGNORECASE)
                                   for k in parse_qs(url.query))
            or url.path != ("/api/football/competitions" if row["provider"] == "the_stats_api" else "/fixtures")):
        raise ValueError("INVALID_SOURCE_URL")
    raw_root = (root / "data/raw").resolve()
    path = (raw_root / row["relative_path"]).resolve()
    if not path.is_relative_to(raw_root):
        raise ValueError("SNAPSHOT_OUTSIDE_ARCHIVE")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row["content_hash"]:
        raise ValueError("CORRUPT_SNAPSHOT")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise TypeError("INVALID_SNAPSHOT_BODY")
    return payload, {"provider": row["provider"], "source_url": row["source_url"],
        "observed_at": observed.isoformat(), "evidence_sha256": row["content_hash"]}


def _catalogue(root, rows, as_of):
    pages, errors = {}, Counter()
    for row in rows:
        try:
            payload, source = _snapshot(root, row, as_of)
            meta = payload["meta"]
            page, total, count = meta["page"], meta["total_pages"], meta["total"]
            if (any(type(n) is not int for n in (page, total, count))
                    or not 1 <= page <= total <= 100 or count < 0
                    or not isinstance(payload["data"], list)):
                raise ValueError("INVALID_CATALOGUE_PAGE")
            pages.setdefault(page, {"total_pages": total, "total": count,
                "rows": payload["data"], "source": source})
        except (ValueError, TypeError, KeyError, OSError):
            errors["INVALID_OR_FUTURE_CATALOGUE_SNAPSHOT"] += 1
    if not pages:
        return {"status": "MISSING", "reason": "NO_VALID_CATALOGUE", "entries": [],
                "errors": dict(errors)}
    first = next(iter(pages.values()))
    if (set(pages) != set(range(1, first["total_pages"] + 1)) or any(
            p["total_pages"] != first["total_pages"] or p["total"] != first["total"]
            for p in pages.values())):
        return {"status": "MISSING", "reason": "INCOMPLETE_CATALOGUE", "entries": [],
                "errors": dict(errors)}
    entries, ids = [], set()
    for page in sorted(pages):
        for item in pages[page]["rows"]:
            if (not isinstance(item, dict) or not re.fullmatch(r"comp_\d+", str(item.get("id")))
                    or not isinstance(item.get("name"), str) or not item["name"].strip()
                    or (item.get("country") is not None and not isinstance(item["country"], str))
                    or item["id"] in ids):
                return {"status": "MISSING", "reason": "AMBIGUOUS_CATALOGUE_IDENTITY",
                        "entries": [], "errors": dict(errors)}
            ids.add(item["id"])
            entries.append({"id": item["id"], "name": item["name"], "country": item.get("country"),
                "advertised": {key: item.get(key) if type(item.get(key)) is bool else None
                               for key in CAPABILITIES}, "source": pages[page]["source"]})
    if len(entries) != first["total"]:
        return {"status": "MISSING", "reason": "INCOMPLETE_CATALOGUE_COUNT",
                "entries": [], "errors": dict(errors)}
    return {"status": "AVAILABLE", "entries": entries, "errors": dict(errors),
            "sources": [pages[page]["source"] for page in sorted(pages)],
            "snapshot_scope": "INDEPENDENT_AS_OF_PAGES", "pages": len(pages)}


def _api_identities(root, rows, wanted, as_of):
    found, ambiguous = {}, set()
    for row in rows:
        try:
            query = parse_qs(urlsplit(row["source_url"]).query)
            if "league" in query and not set(query["league"]) & {str(n) for n in wanted}:
                continue
            payload, source = _snapshot(root, row, as_of)
            for fixture in payload.get("response", []):
                league = fixture.get("league") if isinstance(fixture, dict) else None
                if (not isinstance(league, dict) or type(league.get("id")) is not int
                        or league["id"] not in wanted or not isinstance(league.get("name"), str)
                        or not league["name"].strip() or not isinstance(league.get("country"), str)
                        or not league["country"].strip()):
                    continue
                identifier = league["id"]
                value = {"name": league["name"], "country": league["country"], "source": source}
                previous = found.get(identifier)
                if previous is None:
                    found[identifier] = value
                elif previous["source"]["observed_at"] == source["observed_at"] and (
                        exact_name(previous["name"]) != exact_name(value["name"])
                        or exact_name(previous["country"]) != exact_name(value["country"])):
                    ambiguous.add(identifier)
        except (ValueError, TypeError, KeyError, OSError):
            continue
    for identifier in ambiguous:
        found[identifier] = {"status": "AMBIGUOUS"}
    return found


def build_league_coverage(leagues, *, catalogue, api_identities, report, as_of):
    """Pure map of the complete configured/conditional/current manual scope."""
    from goal_analysis.agents.grok_stats_data import validate_stats_context
    from goal_analysis.jobs.league_scope import SERIE_C_GROUPS
    from goal_analysis.jobs.the_stats_context import LEAGUES

    cutoff = utc(as_of)
    active = {}
    for league in leagues:
        identifier = league["api_football_id"]
        if type(identifier) is not int or identifier <= 0 or identifier in active:
            raise ValueError("UNIQUE_ACTIVE_LEAGUE_IDS_REQUIRED")
        active[identifier] = {"competition_id": league["competition_id"], "scope": "DEFAULT"}
    for identifier, name in SERIE_C_GROUPS.items():
        active.setdefault(identifier, {"competition_id": name, "scope": "CONDITIONAL"})
    for identifier in report.get("selected_competition_ids", []):
        if type(identifier) is not int or identifier <= 0:
            raise ValueError("INVALID_SELECTED_LEAGUE")
        active.setdefault(identifier, {"competition_id": f"API_{identifier}", "scope": "SELECTED_EXTRA"})
    for fixture in report.get("fixtures", []):
        identifier = str(config_league_id(fixture) or '')
        if identifier.isdigit() and int(identifier) > 0:
            active.setdefault(int(identifier), {"competition_id": f"API_{identifier}", "scope": "SELECTED_EXTRA"})
    entries = catalogue.get("entries", []) if catalogue.get("status") == "AVAILABLE" else []
    result, counts = [], Counter()
    supplement = report.get("sports_supplement", {}).get("the_stats_api") or {}
    for identifier, config in sorted(active.items()):
        api, verified = api_identities.get(identifier), LEAGUES.get(str(identifier))
        matches, method = [], None
        api_conflict = bool(api and (api.get("status") == "AMBIGUOUS" or (verified
                            and verified[2] is not None and not _same_name(api.get("country"), verified[2]))))
        if verified and not api_conflict:
            matches = [item for item in entries if item["id"] == verified[0]
                and _same_name(item["name"], verified[1])
                and (item['country'] is None and verified[2] is None or _same_name(item["country"], verified[2]))]
            method = "EXISTING_VERIFIED_MAPPING"
        elif api and not api_conflict:
            matches = [item for item in entries if item["country"] is not None
                and _same_name(item["name"], api["name"])
                and _same_name(item["country"], api["country"])]
            method = "EXACT_NAME_COUNTRY_PROPOSAL"
        chosen = matches[0] if len(matches) == 1 else None
        status = "MAPPED" if chosen else "AMBIGUOUS" if len(matches) > 1 or api_conflict else "MISSING_LINK"
        observed, facts = Counter(), Counter()
        for fixture in report.get("fixtures", []):
            if str(config_league_id(fixture)) != str(identifier):
                continue
            observed["fixtures"] += 1
            context = supplement.get("context_by_fixture", {}).get(fixture["fixture_id"])
            if not context:
                continue
            try:
                clean = validate_stats_context({**context, "version": supplement["version"],
                    "provider": supplement["provider"], "observed_at": supplement["observed_at"]}, fixture, cutoff)
                if clean["status"] != "MATCHED" or not chosen or clean["mapping"]["competition_id"] != chosen["id"]:
                    continue
                observed["matched"] += 1
                facts.update(fact["category"] for fact in clean["facts"] if fact["status"] == "AVAILABLE")
            except (ValueError, TypeError, KeyError):
                observed["invalid_context"] += 1
        counts[status] += 1
        result.append({"api_football_id": identifier, **config, "api_identity": api,
            "mapping_status": status, "mapping_method": method if chosen else None,
            "resolution_reason": "UNIQUE_CATALOGUE_IDENTITY" if chosen else
                "AMBIGUOUS_IDENTITY" if status == "AMBIGUOUS" else
                "CATALOGUE_UNAVAILABLE" if catalogue.get("status") != "AVAILABLE" else "NO_EXACT_VERIFIED_LINK",
            "the_stats": chosen, "collector_enabled": bool(verified),
            "observed_in_run": dict(observed), "available_fact_categories": dict(facts),
            "capability_interpretation": "ADVERTISED_NOT_ACCESS_OR_DATA_VERIFIED"})
    return deepcopy({"version": VERSION, "mode": "SHADOW", "as_of": cutoff.isoformat(),
        "status": "AVAILABLE" if not counts["MISSING_LINK"] and not counts["AMBIGUOUS"] else "PARTIAL",
        "catalogue": {k: v for k, v in catalogue.items() if k != "entries"},
        "catalogue_competitions": len(entries), "active_competitions": len(result),
        "counts": dict(counts), "leagues": result, "external_requests": 0,
        "new_collector_mappings_activated": 0})


def inspect_league_coverage(root, report, *, as_of, leagues=None):
    """Existing archive/config only. Never initializes a DB or requests a provider."""
    root, cutoff = Path(root).resolve(), utc(as_of)
    if leagues is None:
        path = root / "config/daily223-live.json"
        leagues = json.loads(path.read_text(encoding="utf-8"))["leagues"] if path.exists() else []
    wanted = {item["api_football_id"] for item in leagues}
    from goal_analysis.jobs.league_scope import SERIE_C_GROUPS
    wanted.update(SERIE_C_GROUPS)
    wanted.update(report.get("selected_competition_ids", []))
    wanted.update(int(config_league_id(f)) for f in report.get("fixtures", [])
                  if str(config_league_id(f) or '').isdigit())
    path = root / "data/goal_analysis.sqlite3"
    rows = []
    if path.is_file():
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute("""SELECT * FROM raw_snapshots WHERE
                (provider='the_stats_api' AND resource_type='competition_catalogue') OR
                (provider='api_football' AND resource_type='fixtures')""")]
    # Sort parsed UTC instants; never rely on mixed-offset SQLite text ordering.
    valid = []
    for row in rows:
        try:
            if utc(row["fetched_at"]) <= cutoff:
                valid.append(row)
        except (ValueError, TypeError):
            continue
    valid.sort(key=lambda row: (utc(row["fetched_at"]), row["content_hash"]), reverse=True)
    catalogue = _catalogue(root, [r for r in valid if r["provider"] == "the_stats_api"], cutoff)
    identities = _api_identities(root, [r for r in valid if r["provider"] == "api_football"], wanted, cutoff)
    return build_league_coverage(leagues, catalogue=catalogue, api_identities=identities,
                                 report=report, as_of=cutoff)
