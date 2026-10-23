"""Read witnessed native match xG into an isolated SHADOW rate fit.

No collection, receipt creation, database writes, result mutation or live hook.
Season aggregates cannot substitute for missing match/venue observations.
"""
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from goal_analysis.models.xg_rates import fit_xg_rates
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.leakage import require_known_before
from goal_analysis.normalization.live_source import stats_source_url
from goal_analysis.normalization.temporal import project_times
from goal_analysis.normalization.training_datasets import sha
from goal_analysis.storage.canonical_registry import digest, encoded
from goal_analysis.storage.temporal import _source_hash


def _evidence(db, row, root, cutoff):
    receipt = db.execute("""SELECT * FROM temporal_receipts WHERE namespace='raw'
        AND source_key=?""", (row["content_hash"],)).fetchone()
    if receipt is None:
        raise ValueError("UNWITNESSED_RAW_XG_SOURCE")
    receipt = dict(receipt)
    if _source_hash(row) != receipt["source_sha256"]:
        raise ValueError("RAW_XG_SOURCE_CHANGED_AFTER_WITNESS")
    times = project_times("raw", row, available_at=receipt["available_at"])
    require_known_before(times, as_of=cutoff)
    if (encoded(times) != receipt["times_json"] or receipt["receipt_id"] != digest(
            ["raw", row["content_hash"], receipt["source_sha256"]])):
        raise ValueError("INVALID_RAW_XG_WITNESS")
    if not stats_source_url(row["source_url"]):
        raise ValueError("INVALID_XG_SOURCE_URL")
    path = (root / "data/raw" / row["relative_path"]).resolve()
    if not path.is_relative_to((root / "data/raw").resolve()):
        raise ValueError("RAW_XG_PATH_OUTSIDE_ARCHIVE")
    blob = path.read_bytes()
    if len(blob) != row["size_bytes"] or hashlib.sha256(blob).hexdigest() != row["content_hash"]:
        raise ValueError("RAW_XG_HASH_MISMATCH")
    return json.loads(blob)["data"], {"source_key": row["content_hash"],
        "source_sha256": receipt["source_sha256"], "receipt_id": receipt["receipt_id"],
        "source_url": row["source_url"], "times": times}


def _normalize(match, stats, meta_proof, stats_proof):
    native = match["id"]
    if stats.get("match_id") != native:
        raise ValueError("XG_MATCH_ID_MISMATCH")
    if not stats_proof["source_url"].endswith("/football/matches/" + native + "/stats"):
        raise ValueError("XG_ENDPOINT_ID_MISMATCH")
    score = match.get("score", {})
    if (match.get("status") != "finished" or match.get("is_neutral") is not False
            or score.get("went_to_extra_time") is not False or score.get("went_to_penalties") is not False
            or match.get("xg_available") is not True or match.get("xg_quality") != "full"):
        raise ValueError("UNPROVEN_REGULATION_VENUE_OR_XG_QUALITY")
    metric = stats["overview"]["expected_goals"]
    values = {side: [metric[period][side] for period in ("first_half", "second_half")]
              for side in ("home", "away")}
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 20
           for halves in values.values() for v in halves):
        raise ValueError("INVALID_PERIOD_XG")
    proofs = (meta_proof, stats_proof)
    observed = max(utc(p["times"]["observed_at"]) for p in proofs)
    available = max(utc(p["times"]["available_at"]) for p in proofs)
    payload = {"provider": "the_stats_api", "fixture_id": native,
        "competition_id": match["competition_id"], "season_id": match["season_id"],
        "home_team_id": match["home_team"]["id"], "away_team_id": match["away_team"]["id"],
        "kickoff": utc(match["utc_date"]).isoformat(), "status": "FT", "neutral_venue": False,
        "home_xg": math.fsum(values["home"]), "away_xg": math.fsum(values["away"]),
        "scope": "REGULATION_FIRST_PLUS_SECOND_HALF",
        "observed_at": observed.isoformat(), "available_at": available.isoformat()}
    times = {"observed_at": observed.isoformat(), "published_at": None, "ingested_at": None,
        "available_at": available.isoformat(), "availability_basis": "SOURCE_PRESENCE_UPPER_BOUND",
        "event_time": payload["kickoff"], "event_time_kind": "KICKOFF"}
    return {"record_id": sha([payload, list(proofs)]), "payload": payload,
        "payload_sha256": sha(payload), "source_sha256": sha([p["source_sha256"] for p in proofs]),
        "receipt_id": sha([p["receipt_id"] for p in proofs]), "times": times,
        "source_receipts": list(proofs)}


def inspect_xg_rates(root, *, as_of, limit=1000, recipe=None):
    """Fit the currently witnessed cached sample; never claim league completeness."""
    root, cutoff = Path(root).resolve(), utc(as_of)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("INVALID_XG_SOURCE_LIMIT")
    path = root / "data/goal_analysis.sqlite3"
    issues, envelopes, sources = Counter(), [], {}
    if not path.is_file():
        fit = fit_xg_rates([], as_of=cutoff, recipe=recipe)
        return {"status": "MISSING_SOURCE", "fit": fit, "envelopes": [], "external_requests": 0}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"raw_snapshots", "temporal_receipts"} <= tables:
            fit = fit_xg_rates([], as_of=cutoff, recipe=recipe)
            return {"status": "MISSING_PROOF_TABLES", "fit": fit, "envelopes": [], "external_requests": 0}
        rows = [dict(r) for r in db.execute("""SELECT rowid AS _sequence,* FROM raw_snapshots
            WHERE provider='the_stats_api' AND (source_url LIKE '%/football/matches?%'
                OR source_url LIKE '%/football/matches/mt_%/stats')""")]
        rows = [r for r in rows if utc(r["fetched_at"]) <= cutoff]
        # Admission precedes the limit and latest-version choice. Adding a
        # future/unwitnessed row cannot crowd out an older valid replay input.
        witnessed = []
        for row in rows:
            receipt = db.execute("SELECT times_json FROM temporal_receipts WHERE namespace='raw' AND source_key=?",
                                 (row["content_hash"],)).fetchone()
            if receipt is None:
                issues["UNWITNESSED_SOURCE_NOT_USED"] += 1
                continue
            try:
                require_known_before(json.loads(receipt[0]), as_of=cutoff)
            except (ValueError, TypeError):
                issues["OUTSIDE_KNOWLEDGE_PREFIX_NOT_USED"] += 1
                continue
            witnessed.append(row)
        rows = witnessed
        rows.sort(key=lambda r: (utc(r["fetched_at"]), r["content_hash"]), reverse=True)
        stats_rows = [r for r in rows if re.fullmatch(r"/api/football/matches/mt_\d+/stats", urlsplit(r["source_url"]).path)]
        latest_times = {}
        for row in stats_rows:
            latest_times.setdefault(row["source_url"].split("/")[-2], utc(row["fetched_at"]))
        truncated = len(latest_times) > limit
        wanted = set(list(latest_times)[:limit])
        stats_rows = [r for r in stats_rows if r["source_url"].split("/")[-2] in wanted
                      and utc(r["fetched_at"]) == latest_times[r["source_url"].split("/")[-2]]]
        ambiguous_stats = set()
        seen_latest = {}
        for row in stats_rows:
            native = row["source_url"].split("/")[-2]
            stamp = utc(row["fetched_at"])
            if native in seen_latest and seen_latest[native] == stamp:
                ambiguous_stats.add(native)
            seen_latest.setdefault(native, stamp)
        metadata, metadata_times = {}, {}
        for row in rows:
            if urlsplit(row["source_url"]).path != "/api/football/matches":
                continue
            try:
                matches, proof = _evidence(db, row, root, cutoff)
                if not isinstance(matches, list):
                    raise TypeError("INVALID_XG_MATCH_LIST")
                counts = Counter(m.get("id") for m in matches if isinstance(m, dict))
                for match in matches:
                    if not isinstance(match, dict) or not isinstance(match.get("id"), str):
                        continue
                    native, stamp = match["id"], utc(proof["times"]["observed_at"])
                    if native not in metadata_times:
                        metadata_times[native] = stamp
                        if counts[native] == 1:
                            metadata[native] = match, proof
                    elif stamp == metadata_times[native] and (
                            counts[native] != 1 or native in metadata and metadata[native][0] != match):
                        metadata.pop(native, None)
            except (ValueError, KeyError, TypeError, OSError):
                issues["UNUSABLE_METADATA_SOURCE"] += 1
        seen = set()
        for row in stats_rows:
            native = row["source_url"].split("/")[-2]
            if native in seen:
                continue
            # Latest observed version is never replaced by an older statistic
            # after its applicable witness/hash/schema has failed validation.
            seen.add(native)
            if native in ambiguous_stats:
                issues["AMBIGUOUS_LATEST_XG_VERSION"] += 1
                continue
            try:
                stats, proof = _evidence(db, row, root, cutoff)
                if native not in metadata:
                    raise ValueError("MISSING_WITNESSED_XG_MATCH_IDENTITY")
                match, meta_proof = metadata[native]
                envelope = _normalize(match, stats, meta_proof, proof)
                envelopes.append(envelope)
                for source in envelope["source_receipts"]:
                    sources[source["source_key"]] = source
            except (ValueError, KeyError, TypeError, OSError) as error:
                issues[str(error)] += 1
    fit = fit_xg_rates(envelopes, as_of=cutoff, recipe=recipe)
    return {"version": "cached-xg-shadow-v1", "mode": "SHADOW", "as_of": cutoff.isoformat(),
        "status": "LIMIT_REACHED" if truncated else "GAPS" if issues or fit["issues"] else "AVAILABLE_CACHED_SAMPLE",
        "scope": "WITNESSED_CACHED_MATCH_XG_ONLY_NOT_COMPLETE_LEAGUE_HISTORY",
        "issues": dict(sorted(issues.items())), "fit": fit, "envelopes": envelopes,
        "source_receipts": sorted(sources.values(), key=lambda r: r["source_key"]),
        "external_requests": 0, "production_writes": 0, "live_decision_effect": False}
