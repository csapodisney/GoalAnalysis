"""Offline, additive result backfill from existing sports archives only.

No provider clients, quota consumption, scheduler change or betting switch.
Only pre-existing, verified competition mappings authorize exact event links.
Unknown links remain separate; the output never claims a global unique count.
"""
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.historical_results import (
    normalize_api_result,
    normalize_archive_result,
)
from goal_analysis.normalization.identity import exact_name
from goal_analysis.storage.db import Database
from goal_analysis.storage.historical_results import (
    HistoricalResults,
    proof_record,
    stamp,
    validate,
)
from goal_analysis.storage.sports_archive import _FIELDS, _hash

from .league_coverage import inspect_league_coverage

VERSION = "unified-historical-results-v1"


def _raw(root, row, *, now):
    proof_record({"source_url": row["source_url"], "source_sha256": row["content_hash"],
                  "observed_at": row["fetched_at"]}, now=now)
    hosts = {"api_football": {"v3.football.api-sports.io"},
             "the_stats_api": {"api.thestatsapi.com"},
             "football_data_co_uk": {"football-data.co.uk", "www.football-data.co.uk"}}
    if urlsplit(row["source_url"]).hostname not in hosts[row["provider"]]:
        raise ValueError("WRONG_PROVIDER_SOURCE")
    raw_root = (root / "data/raw").resolve()
    path = (raw_root / row["relative_path"]).resolve()
    if not path.is_relative_to(raw_root):
        raise ValueError("RAW_PATH_ESCAPES_ARCHIVE")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != row["content_hash"]:
        raise ValueError("CORRUPT_RAW_SNAPSHOT")
    return content


def prepare_existing_results(root, *, as_of):
    """Validate downloaded bytes and normalized archive hashes before any write."""
    root, now = Path(root).resolve(), utc(as_of)
    path = root / "data/goal_analysis.sqlite3"
    if not path.is_file():
        raise ValueError("EXISTING_SPORTS_DATABASE_REQUIRED")
    prepared, issues, snapshots = [], Counter(), Counter()
    rejected_records, rejected_examples = Counter(), []
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        imports = list(db.execute("""SELECT i.*,s.relative_path,s.provider AS raw_provider,
            s.fetched_at AS raw_fetched_at
            FROM sports_archive_imports i LEFT JOIN raw_snapshots s ON s.content_hash=i.content_hash
            WHERE i.provider IN ('the_stats_api','football_data_co_uk') ORDER BY i.import_id"""))
        for source in imports:
            source = dict(source)
            try:
                if source["raw_provider"] != source["provider"] or not source["relative_path"]:
                    raise ValueError("MISSING_RAW_SOURCE_LINK")
                if utc(source["raw_fetched_at"]) > utc(source["fetched_at"]):
                    raise ValueError("ARCHIVE_RECEIPT_PRECEDES_RAW_RECEIPT")
                _raw(root, source, now=now)
                batch = []
                rows = db.execute("""SELECT o.* FROM sports_archive_observations o
                    JOIN sports_archive_import_records r USING(observation_id)
                    WHERE r.import_id=? ORDER BY o.observation_id""", (source["import_id"],))
                for row in rows:
                    row = dict(row)
                    raw = json.loads(row["raw_json"])
                    archived = {key: row[key] for key in _FIELDS}
                    archived.update(raw=raw, odds_snapshot_types=json.loads(row["odds_snapshot_types_json"]))
                    if _hash(archived) != row["normalized_content_hash"]:
                        raise ValueError("CORRUPT_ARCHIVE_OBSERVATION")
                    if (row["provider"] != source["provider"] or row["league_code"] != source["league_code"]
                            or row["season_code"] != source["season_code"]):
                        raise ValueError("ARCHIVE_SCOPE_CONFLICT")
                    batch.append(validate(normalize_archive_result(row, raw,
                        observed_at=source["fetched_at"], source_url=source["source_url"],
                        source_sha256=source["content_hash"]), now=now))
                prepared.extend(batch)
                snapshots[source["provider"]] += 1
            except (ValueError, TypeError, KeyError, OSError) as error:
                issues["ARCHIVE:" + str(error)] += 1
        rows = list(db.execute("""SELECT * FROM raw_snapshots
            WHERE provider='api_football' AND resource_type='fixtures'
            ORDER BY fetched_at,content_hash"""))
        for source in rows:
            source = dict(source)
            try:
                payload = json.loads(_raw(root, source, now=now))
                if (not isinstance(payload, dict) or payload.get("errors")
                        or not isinstance(payload.get("response"), list)):
                    raise ValueError("INVALID_FIXTURES_RESPONSE")
                batch = []
                for raw in payload["response"]:
                    try:
                        batch.append(validate(normalize_api_result(raw, observed_at=source["fetched_at"],
                            source_url=source["source_url"], source_sha256=source["content_hash"]), now=now))
                    except (ValueError, TypeError, KeyError, AttributeError) as error:
                        if str(error) == "NOT_FINISHED":
                            continue
                        reason = "api_football:" + str(error)
                        rejected_records[reason] += 1
                        if len(rejected_examples) < 1000:
                            fixture = raw.get("fixture") if isinstance(raw, dict) else None
                            rejected_examples.append({"provider_id": fixture.get("id") if isinstance(fixture, dict) else None,
                                "source_sha256": source["content_hash"], "source_url": source["source_url"],
                                "observed_at": stamp(source["fetched_at"]), "reason": str(error)})
                prepared.extend(batch)
                snapshots["api_football"] += 1
            except (ValueError, TypeError, KeyError, OSError, AttributeError) as error:
                issues["API_SNAPSHOT:" + str(error)] += 1
    return prepared, {"accepted_snapshots": dict(snapshots), "quarantined_sources": dict(issues),
                      "quarantined_records": dict(rejected_records), "quarantined_examples": rejected_examples,
                      "prepared_sightings": len(prepared), "external_requests": 0}


def exact_link_requests(view, coverage):
    """Unique oriented teams + exact UTC + an already verified league ID map.

    The seven new catalogue proposals in 1.6 stay proposals. No name-only or
    Football-Data date/time inference is introduced by this backfill.
    """
    requests, counts = [], Counter()
    rows = [row for match in view["matches"] for row in match["source_records"]]
    for league in coverage.get("leagues", []):
        if (league["mapping_status"] != "MAPPED"
                or league["mapping_method"] != "EXISTING_VERIFIED_MAPPING"
                or not league.get("api_identity", {}).get("source")):
            continue
        pair = sorted([["api_football", str(league["api_football_id"])],
                       ["the_stats_api", league["the_stats"]["id"]]])
        buckets = defaultdict(lambda: defaultdict(list))
        conflicted = defaultdict(lambda: defaultdict(set))
        for versions in view.get("same_time_conflicts", {}).values():
            for row in versions:
                item = row["record"]
                if item["kickoff"] and [item["provider"], item["competition_id"]] in pair:
                    key = (item["kickoff"], exact_name(item["home_team"]), exact_name(item["away_team"]))
                    conflicted[key][item["provider"]].add(row["match_id"])
        for row in rows:
            item = row["record"]
            if not item["kickoff"] or [item["provider"], item["competition_id"]] not in pair:
                continue
            if exact_name(item["home_team"]) == exact_name(item["away_team"]):
                counts["ambiguous_team_orientation"] += 1
                continue
            key = (item["kickoff"], exact_name(item["home_team"]), exact_name(item["away_team"]))
            buckets[key][item["provider"]].append(row)
        for key, candidates in buckets.items():
            if set(candidates) != {"api_football", "the_stats_api"}:
                continue
            if any(len({row["match_id"] for row in group} | conflicted[key][provider]) != 1
                   for provider, group in candidates.items()):
                counts["ambiguous_candidates"] += 1
                continue
            a, b = candidates["api_football"][0], candidates["the_stats_api"][0]
            evidence = []
            for source in (league["api_identity"]["source"], league["the_stats"]["source"]):
                evidence.append({"source_url": source["source_url"],
                    "source_sha256": source["evidence_sha256"], "observed_at": source["observed_at"]})
            for row in (a, b):
                evidence.append({key: row["record"][key] for key in ("source_url", "source_sha256", "observed_at")})
            requests.append({"left_id": a["match_id"], "right_id": b["match_id"],
                "method": "EXACT_NAMES_KICKOFF_EXPLICIT_LEAGUE", "competition_pair": pair,
                "evidence": evidence})
    counts["exact_link_candidates"] = len(requests)
    return requests, dict(counts)


def backfill_existing_results(root, *, clock=None):
    root = Path(root).resolve()
    clock = clock or (lambda: datetime.now(UTC))
    started = utc(clock())
    records, preparation = prepare_existing_results(root, as_of=started)
    if not records:
        return {"version": VERSION, "status": "MISSING", "preparation": preparation,
                "external_requests": 0, "effective_data_path": "LEGACY_UNCHANGED"}
    path = root / "data/goal_analysis.sqlite3"
    backup = root / "data/backups" / ("pre-results-" + started.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8] + ".sqlite3")
    backup.parent.mkdir(parents=True, exist_ok=True)
    # SQLite's backup API includes committed WAL contents and never moves files.
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    store = HistoricalResults(Database(path), clock=clock)
    store.initialize()
    created = Counter()
    # Bounded transactions let daily ingestion continue between batch commits.
    # All source batches were validated above; partial progress never replaces
    # a legacy result or a production dataset, and reruns resume idempotently.
    for start in range(0, len(records), 1000):
        created.update(store.ingest(records[start:start + 1000]))
    del records
    view = store.view(as_of=clock())
    coverage = inspect_league_coverage(root, {}, as_of=started)
    requests, reconciliation = exact_link_requests(view, coverage)
    links = store.link_many(requests)
    final = store.view(as_of=clock())
    providers = Counter(row["record"]["provider"] for match in final["matches"] for row in match["source_records"])
    return {"version": VERSION, "mode": "SHADOW", "status": "PARTIAL" if preparation["quarantined_sources"] or preparation["quarantined_records"] else "COMPLETE",
        "effective_data_path": "LEGACY_UNCHANGED", "started_at": stamp(started),
        "finished_at": stamp(clock()), "backup_path": str(backup), "preparation": preparation,
        "created": dict(created), "reconciliation": {**reconciliation,
            "links_created": sum(item["links_created"] for item in links)},
        "counts": final["counts"], "native_matches_by_provider": dict(providers),
        "date_only_matches": sum(row["record"]["kickoff"] is None for match in final["matches"] for row in match["source_records"]),
        "unlinked_interpretation": "NO_GLOBAL_UNIQUENESS_CLAIM_WITHOUT_VERIFIED_EVENT_LINK",
        "external_requests": 0}
