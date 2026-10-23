"""Bounded, zero-API catch-up for legacy TheStats historical result writers.

Only the additive unified result tables and this job's own metadata are written.
An original receipt remains original; a checkpoint follows successful sink
ingestion, so a crash between them safely replays the same immutable evidence.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.historical_results import normalize_archive_result
from goal_analysis.storage.canonical_registry import digest
from goal_analysis.storage.db import Database
from goal_analysis.storage.historical_results import HistoricalResults, stamp, validate
from goal_analysis.storage.raw_admission import admit_raw_sources
from goal_analysis.storage.raw_evidence import read_witnessed_raw
from goal_analysis.storage.sports_archive import _FIELDS, _hash

from .historical_source_binding import bind_source
from .the_stats_history import _atomic, _lock, _read

VERSION = "historical-results-sync-v1"
PROVIDER = "the_stats_api"
_STATE_FIELDS = {"version", "after_import_id", "after_import_key", "updated_at", "last_version_ids"}


class _Wall:
    def __init__(self, clock):
        self.clock, self.last = clock, None

    def __call__(self):
        now = utc(self.clock())
        if self.last is not None and now < self.last:
            raise ValueError("CLOCK_MOVED_BACKWARDS")
        self.last = now
        return now


def _state(path, db):
    if not path.exists():
        return {"version": VERSION, "after_import_id": 0, "after_import_key": None,
                "updated_at": None, "last_version_ids": []}
    value = _read(path)
    if (not isinstance(value, dict) or set(value) != _STATE_FIELDS or value["version"] != VERSION
            or type(value["after_import_id"]) is not int or value["after_import_id"] <= 0
            or not isinstance(value["after_import_key"], str)
            or not isinstance(value["last_version_ids"], list)
            or any(not isinstance(key, str) or len(key) != 64 for key in value["last_version_ids"])):
        raise ValueError("INVALID_SYNC_WATERMARK")
    utc(value["updated_at"])
    source = db.execute("SELECT import_key FROM sports_archive_imports WHERE provider=? AND import_id=?",
                        (PROVIDER, value["after_import_id"])).fetchone()
    if source is None or source["import_key"] != value["after_import_key"]:
        raise ValueError("SYNC_WATERMARK_SOURCE_MISMATCH")
    # A restored pre-sink DB must not silently inherit a newer external cursor.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='historical_result_versions'").fetchone():
        raise ValueError("SYNC_TARGET_MISSING")
    if any(not db.execute("SELECT 1 FROM historical_result_versions WHERE version_id=?", (key,)).fetchone()
           for key in value["last_version_ids"]):
        raise ValueError("SYNC_WATERMARK_TARGET_MISMATCH")
    return value


def _prepare(root, db, source, now):
    remaining,binding=bind_source(root,db,source,now)
    rows = db.execute("""SELECT o.* FROM sports_archive_observations o
        JOIN sports_archive_import_records r USING(observation_id)
        WHERE r.import_id=? ORDER BY o.observation_id""", (source["import_id"],)).fetchall()
    if type(source["record_count"]) is not int or source["record_count"] != len(rows):
        raise ValueError("ARCHIVE_RECORD_COUNT_MISMATCH")
    records, rejected, hashes = [], [], []
    for row in rows:
        row = dict(row)
        raw = json.loads(row["raw_json"])
        archived = {key: row[key] for key in _FIELDS}
        archived.update(raw=raw, odds_snapshot_types=json.loads(row["odds_snapshot_types_json"]))
        if _hash(archived) != row["normalized_content_hash"]:
            raise ValueError("CORRUPT_ARCHIVE_OBSERVATION")
        if any(row[key] != source[key] for key in ("provider", "league_code", "season_code")):
            raise ValueError("ARCHIVE_SCOPE_CONFLICT")
        raw_hash=_hash(raw)
        if remaining[raw_hash]<=0:
            raise ValueError('ARCHIVE_RECORD_NOT_IN_ORIGINAL_RAW')
        remaining[raw_hash]-=1
        hashes.append([row['observation_id'],row['normalized_content_hash'],raw_hash])
        try:
            records.append(validate(normalize_archive_result(row, raw,
                observed_at=source["fetched_at"], source_url=source["source_url"],
                source_sha256=source["content_hash"]), now=now))
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            rejected.append({"observation_id": row["observation_id"],
                "source_record_key": row["source_record_key"], "reason": str(error)})
    binding['archive_record_hashes']=hashes
    dataset_hash=_hash({'records':sorted((r['source_record_key'],r['normalized_content_hash']) for r in rows),
        'columns':json.loads(source['columns_json']),'rejections':json.loads(source['rejections_json']),
        'rejection_counts':json.loads(source['rejection_counts_json']),
        'blank_rows_skipped':source['blank_rows_skipped'],'encoding':source['encoding']})
    if dataset_hash!=source['dataset_hash']:
        raise ValueError('ARCHIVE_DATASET_HASH_MISMATCH')
    identity={k:source[k] for k in ('provider','league_code','season_code','source_url','fetched_at','content_hash')}
    if _hash({**identity,'dataset_hash':dataset_hash,'metadata':json.loads(source['metadata_json'])})!=source['import_key']:
        raise ValueError('ARCHIVE_IMPORT_KEY_MISMATCH')
    binding.update(import_key=source['import_key'],dataset_hash=dataset_hash)
    return records, rejected, binding


def _pending(db, after):
    return db.execute("SELECT count(*) FROM sports_archive_imports WHERE provider=? AND import_id>?",
                      (PROVIDER, after)).fetchone()[0]


def _report(status, state, processed, counts, rejections, pending, *, error=None, import_id=None):
    result = {"status": status, "mode": "SHADOW", "effective_data_path": "LEGACY_UNCHANGED",
              "processed_imports": processed, "pending_imports": pending,
              "after_import_id": state["after_import_id"], "counts": dict(counts),
              "rejections": dict(rejections), "external_requests": 0}
    if error is not None:
        result.update(error_code=error, invalid_import_id=import_id)
    return result


def sync_stats_results(root, *, max_imports=20, clock=lambda: datetime.now(UTC)):
    """Sync at most max_imports TheStats imports in durable import-ID order.

    Raw/source corruption stops before the affected import. A row with intact
    archive integrity but invalid normalized meaning is durably quarantined;
    valid siblings can be ingested and that import can advance. No full unified
    view or Football-Data scan is needed. Metadata write/DB failures propagate,
    preserving the previous watermark for an idempotent retry.
    """
    if type(max_imports) is not int or not 1 <= max_imports <= 1000:
        raise ValueError("MAX_IMPORTS_MUST_BE_BETWEEN_1_AND_1000")
    root = Path(root).resolve()
    path = root / "data/goal_analysis.sqlite3"
    state = {"after_import_id": 0}
    counts = Counter(matches_created=0, versions_created=0, accepted_rows=0, rejected_rows=0)
    rejections, processed, bindings = Counter(), 0, []
    if not path.is_file():
        return _report("INVALID_SOURCE", state, processed, counts, rejections, None,
                       error="EXISTING_SPORTS_DATABASE_REQUIRED")
    wall = _Wall(clock)
    try:
        wall()
    except (ValueError, TypeError, OverflowError):
        return _report("INVALID_SOURCE", state, processed, counts, rejections, None,
                       error="INVALID_CLOCK")
    work = root / "data/arthur/historical-results-sync"
    work.mkdir(parents=True, exist_ok=True)
    checkpoint = work / "watermark.json"
    with _lock(work / "sync.lock"), closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        try:
            state = _state(checkpoint, db)
            now = wall()
            if state["updated_at"] is not None and utc(state["updated_at"]) > now:
                raise ValueError("CLOCK_MOVED_BACKWARDS")
        except (ValueError, TypeError, KeyError, OSError, sqlite3.DatabaseError) as error:
            return _report("INVALID_SOURCE", state, processed, counts, rejections, None, error=str(error))
        try:
            imports = db.execute("""SELECT i.*,s.relative_path,s.provider AS raw_provider,
                s.fetched_at AS raw_fetched_at,s.resource_type AS raw_resource_type
                FROM sports_archive_imports i LEFT JOIN raw_snapshots s ON s.content_hash=i.content_hash
                WHERE i.provider=? AND i.import_id>? ORDER BY i.import_id LIMIT ?""",
                (PROVIDER, state["after_import_id"], max_imports)).fetchall()
        except sqlite3.DatabaseError:
            return _report("INVALID_SOURCE", state, processed, counts, rejections, None,
                           error="ARCHIVE_TABLES_UNAVAILABLE")
        store = HistoricalResults(Database(path), clock=wall)
        initialized = False
        for source in imports:
            source = dict(source)
            try:
                records, rejected, binding = _prepare(root, db, source, wall())
                wall()
                if binding['physical_presence_admission_required']:
                    admission=admit_raw_sources(Database(path),root,[source['content_hash']],clock=wall)
                    if admission['rejected']:
                        raise ValueError('HISTORICAL_RAW_PRESENCE_ADMISSION_FAILED')
                    raw_row=dict(db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?',
                                            (source['content_hash'],)).fetchone())
                    _,binding['raw_presence_proof']=read_witnessed_raw(db,raw_row,root,wall())
            except (ValueError, TypeError, KeyError, OSError, UnicodeError,
                    RecursionError, sqlite3.DatabaseError) as error:
                return _report("INVALID_SOURCE", state, processed, counts, rejections,
                    _pending(db, state["after_import_id"]), error=str(error), import_id=source["import_id"])
            if rejected:
                audit = {"version": VERSION, "import_id": source["import_id"],
                    "import_key": source["import_key"], "source_sha256": source["content_hash"],
                    "source_url": source["source_url"], "observed_at": stamp(source["fetched_at"]),
                    "rejections": rejected}
                audit_path = work / "rejections" / f"import-{source['import_id']}.json"
                if audit_path.exists():
                    if _read(audit_path) != audit:
                        raise ValueError("SYNC_QUARANTINE_CHANGED")
                else:
                    _atomic(audit_path, audit)
            if not initialized:
                store.initialize()
                initialized = True
            try:
                counts.update(store.ingest(records))
                updated = stamp(wall())
            except ValueError as error:
                if str(error) != "CLOCK_MOVED_BACKWARDS":
                    raise
                return _report("INVALID_SOURCE", state, processed, counts, rejections,
                    _pending(db, state["after_import_id"]), error=str(error), import_id=source["import_id"])
            proof_path=work/'source-proofs'/f"import-{source['import_id']}.json"
            if proof_path.exists():
                if _read(proof_path)!=binding:
                    raise ValueError('HISTORICAL_SOURCE_BINDING_CHANGED')
            else:
                _atomic(proof_path,binding)
            bindings.append({'import_id':source['import_id'],'kind':binding['kind'],
                'proof_path':str(proof_path.relative_to(root)),'proof_sha256':digest(binding)})
            next_state = {"version": VERSION, "after_import_id": source["import_id"],
                "after_import_key": source["import_key"], "updated_at": updated,
                "last_version_ids": sorted({digest(record) for record in records})}
            _atomic(checkpoint, next_state)
            state = next_state
            processed += 1
            counts.update(accepted_rows=len(records), rejected_rows=len(rejected))
            rejections.update(row["reason"] for row in rejected)
        pending = _pending(db, state["after_import_id"])
        return {**_report("BATCH_LIMIT" if pending else "COMPLETE", state, processed, counts, rejections, pending),
                'source_bindings':bindings}
