"""Bounded offline odds archival from verified local sources; never requests odds.

Sources are imported atomically one at a time. Existing receipts form a resume
boundary tied to the parser source hash and exact immutable parent evidence.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from itertools import islice
from pathlib import Path
from time import perf_counter, sleep

from goal_analysis.agents import canonical_sha256
from goal_analysis.normalization import football_data_odds, odds_snapshots
from goal_analysis.normalization.canonical import utc
from goal_analysis.storage.canonical_registry import digest
from goal_analysis.storage.db import Database
from goal_analysis.storage.odds_snapshots import OddsSnapshots, stamp
from goal_analysis.storage.sports_archive import _FIELDS, _hash


def _parser_hash(module):
    return hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()


def _raw(root, source):
    directory = (root / "data/raw").resolve()
    path = (directory / source["relative_path"]).resolve()
    if not path.is_relative_to(directory) or path == directory:
        raise ValueError("RAW_PATH_OUTSIDE_ARCHIVE")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != source["content_hash"] or len(content) != source["size_bytes"]:
        raise ValueError("RAW_PAYLOAD_HASH_OR_SIZE_MISMATCH")
    return content


def _sources(db):
    existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    raw = []
    if "raw_snapshots" in existing:
        raw = [{"kind": "RAW_API", **dict(row)} for row in db.execute("""SELECT * FROM raw_snapshots
            WHERE (provider IN ('api_football','the_odds_api') AND resource_type LIKE '%odds%')
               OR (provider='the_stats_api' AND source_url LIKE '%/odds%')
            ORDER BY fetched_at,content_hash""")]
    fd = []
    if {"sports_archive_imports", "raw_snapshots"} <= existing:
        fd = [{"kind": "FOOTBALL_DATA", **dict(row)} for row in db.execute("""SELECT i.*,
            r.relative_path,r.size_bytes,r.provider AS raw_provider,r.fetched_at AS raw_fetched_at,
            r.content_hash AS proven_raw_hash FROM sports_archive_imports i
            LEFT JOIN raw_snapshots r ON r.content_hash=i.raw_snapshot_hash
            WHERE i.provider='football_data_co_uk' ORDER BY i.fetched_at,i.import_id""")]
    return raw + fd


def _fd_rows(database, source):
    # One source transaction consumes this generator. Never construct all
    # historical bookmaker prices, or even a whole CSV's quotes, in RAM.
    with closing(database.connect()) as db:
        rows = db.execute("""SELECT o.* FROM sports_archive_observations o
            JOIN sports_archive_import_records r USING(observation_id)
            WHERE r.import_id=? ORDER BY o.observation_id""", (source["import_id"],))
        count = 0
        for row in rows:
            count += 1
            row = dict(row)
            if any(row[key] != source[key] for key in ("provider", "league_code", "season_code")):
                raise ValueError("FOOTBALL_DATA_SCOPE_CONFLICT")
            archived = {key: row[key] for key in _FIELDS}
            archived.update(raw=json.loads(row["raw_json"]),
                            odds_snapshot_types=json.loads(row["odds_snapshot_types_json"]))
            if _hash(archived) != row["normalized_content_hash"]:
                raise ValueError("FOOTBALL_DATA_ARCHIVE_ROW_HASH_MISMATCH")
            yield row
        if count != source["record_count"]:
            raise ValueError("FOOTBALL_DATA_RECORD_COUNT_MISMATCH")


def _fd_records(database, source):
    for row in _fd_rows(database, source):
        yield from football_data_odds.iter_fd_quotes(row,
            observed_at=source["fetched_at"], source_url=source["source_url"],
            source_sha256=source["content_hash"])


def backfill_odds(root, *, max_sources=10000, start_index=0, clock=None, include_monitor=True,
                  expected_manifest_sha256=None, batch_size=1000, on_progress=None):
    """Archive a bounded frozen source list, idempotently, with no API calls.

    New rows or parser revisions outside the initial manifest belong to a later run.
    Invalid source evidence is explicitly reported and cannot advance a source
    receipt. Cached sources are reused only after their raw proof revalidates.
    """
    if (type(max_sources) is not int or not 1 <= max_sources <= 10000
            or type(start_index) is not int or start_index < 0 or type(include_monitor) is not bool
            or type(batch_size) is not int or not 1 <= batch_size <= 1000
            or on_progress is not None and not callable(on_progress)):
        raise ValueError("INVALID_ODDS_ARCHIVE_BUDGET")
    root = Path(root).resolve()
    path = root / "data/goal_analysis.sqlite3"
    if not path.is_file():
        return {"status": "MISSING_SOURCE", "external_requests": 0, "created": {}, "sources": 0}
    database = Database(path)
    store = OddsSnapshots(database, clock=clock)
    store.initialize()
    with closing(database.connect()) as db:
        sources = _sources(db)
    parser_hashes = {"RAW_API": _parser_hash(odds_snapshots), "FOOTBALL_DATA": _parser_hash(football_data_odds)}
    monitor_path = root / "data/arthur/odds-monitor.sqlite3"
    if include_monitor and monitor_path.is_file():
        with closing(sqlite3.connect(monitor_path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='monitor_snapshots'").fetchone():
                sources.extend({"kind": "MONITOR", **dict(row)} for row in db.execute(
                    "SELECT * FROM monitor_snapshots ORDER BY observed_at,snapshot_id"))
        parser_hashes["MONITOR"] = parser_hashes["RAW_API"]
    manifest = digest({"sources": sources, "parser_sha256": parser_hashes})
    if ((start_index and expected_manifest_sha256 is None)
            or expected_manifest_sha256 is not None and expected_manifest_sha256 != manifest):
        raise ValueError("SOURCE_MANIFEST_MISMATCH")
    if start_index > len(sources):
        raise ValueError("START_INDEX_OUTSIDE_SOURCE_MANIFEST")
    created, reused, totals = Counter(), Counter(), Counter()
    max_batch_seconds, batches = 0.0, 0
    issues, per_provider = [], {}
    first_failed_index, processed_sources = None, 0
    for source_index, source in enumerate(sources[start_index:start_index + max_sources], start_index):
        processed_sources += 1
        provider, kind = source["provider"], source["kind"]
        try:
            if kind == "MONITOR":
                if canonical_sha256([provider, source["query_key"], source["observed_at"],
                                     json.loads(source["quotes_json"])]) != source["snapshot_id"]:
                    raise ValueError("MONITOR_SNAPSHOT_HASH_MISMATCH")
                source_sha = digest({key: value for key, value in source.items() if key != "kind"})
                observed, url = source["observed_at"], None
                ref = "monitor:" + source["snapshot_id"] + ":parser:" + parser_hashes[kind]
                content = None
            else:
                if kind == "FOOTBALL_DATA" and (
                        source["raw_provider"] != provider or source["proven_raw_hash"] != source["content_hash"]
                        or source["raw_snapshot_hash"] != source["content_hash"]
                        or utc(source["raw_fetched_at"]) > utc(source["fetched_at"])):
                    raise ValueError("FOOTBALL_DATA_RAW_PROVENANCE_MISMATCH")
                content = _raw(root, source)
                source_sha, observed, url = source["content_hash"], source["fetched_at"], source["source_url"]
                ref = ("fd-import:" + source["import_key"] if kind == "FOOTBALL_DATA" else "raw:" + source_sha)
                ref += ":parser:" + parser_hashes[kind]
                if kind == "FOOTBALL_DATA":
                    # Revalidate even a cached import, without re-generating its
                    # millions of prices. No corrupt parent gets a new seal.
                    for _ in _fd_rows(database, source):
                        pass
            source_id = digest({"provider": provider, "observed_at": stamp(observed),
                "source_url": url, "source_sha256": source_sha, "source_ref": ref})
            with closing(database.connect()) as db:
                saved = db.execute("SELECT * FROM sports_odds_publications WHERE source_id=?", (source_id,)).fetchone()
                cached = saved["quote_count"] if saved is not None else None
            if saved is not None:
                reused[provider] += 1
                totals[provider] += cached
                if on_progress is not None:
                    on_progress({"source_index": source_index, "provider": provider, "reused": True,
                                 "quotes": cached, "planned_sources": len(sources)})
                continue
            counts = {}
            if kind == "FOOTBALL_DATA":
                records = _fd_records(database, source)
            else:
                if kind == "MONITOR":
                    parsed = odds_snapshots.normalize_saved_quotes(provider,
                        json.loads(source["quotes_json"]), observed_at=observed,
                        source_url=None, source_sha256=source_sha)
                else:
                    parsed = odds_snapshots.normalize_odds_snapshot(provider, json.loads(content),
                        observed_at=observed, source_url=url, source_sha256=source_sha)
                records = parsed["records"]
                counts = dict(Counter("UNSUPPORTED:" + item["reason"] for item in parsed["unsupported"]))
                counts.update(Counter("REJECTED:" + item["reason"] for item in parsed["rejected"]))
            iterator, result = iter(records), Counter()
            while True:
                chunk = list(islice(iterator, batch_size))
                if not chunk and result:
                    break
                batch_started = perf_counter()
                batch = store.ingest(chunk, provider=provider, observed_at=observed,
                    source_url=url, source_sha256=source_sha, source_ref=ref,
                    issue_counts=counts, _publish=False)
                max_batch_seconds = max(max_batch_seconds, perf_counter() - batch_started)
                batches += 1
                result.update({key: batch[key] for key in ("sources_created", "products_created", "quotes_created")})
                if not chunk:
                    break
                # Give other local writers an explicit gap between short locks.
                sleep(0.005)
            published = store.seal(source_id)
            created.update(result)
            totals[provider] += published["quote_count"]
            item = per_provider.setdefault(provider, Counter())
            item.update({key: result[key] for key in ("sources_created", "products_created", "quotes_created")})
            if on_progress is not None:
                on_progress({"source_index": source_index, "provider": provider, "reused": False,
                             "quotes": published["quote_count"], "planned_sources": len(sources)})
        except (ValueError, TypeError, KeyError, OSError) as error:
            issues.append({"kind": kind, "provider": provider,
                "source_ref": source.get("content_hash", source.get("snapshot_id")),
                "source_index": source_index, "reason": str(error)})
            if first_failed_index is None:
                first_failed_index = source_index
    next_index = min(start_index + max_sources, len(sources))
    if first_failed_index is not None:
        # Later valid siblings may have sealed successfully; retry from the
        # earliest failure and reuse those receipts instead of skipping it.
        next_index = first_failed_index
    return {"mode": "SHADOW", "effective_data_path": "LEGACY_UNCHANGED", "external_requests": 0,
        "source_manifest_sha256": manifest, "parser_sha256": parser_hashes,
        "planned_sources": len(sources), "processed_sources": processed_sources,
        "start_index": start_index, "next_index": next_index, "pending": next_index < len(sources),
        "status": "PARTIAL" if issues else "SOURCE_LIMIT" if next_index < len(sources) else "COMPLETE",
        "created": dict(created), "reused_sources": dict(reused), "quote_totals": dict(totals),
        "batch_size": batch_size, "batches": batches, "max_batch_seconds": max_batch_seconds,
        "by_provider": {key: dict(value) for key, value in per_provider.items()},
        "rejected_sources": issues, "summary": store.summary()}
