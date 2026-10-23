"""Additive SHADOW availability receipts; legacy timestamps are never rewritten.

A committed source read proves that its exact contents existed locally by the
subsequent wall-clock witness. This conservative upper bound is neither the
provider's publication time nor the unknown original database commit time.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.temporal import project_times

from .canonical_registry import digest, encoded

NAMESPACES = ("result", "weather", "raw", "news")
_TABLES = {"result": "historical_result_versions", "weather": "sports_weather_snapshots",
           "raw": "raw_snapshots", "news": "sports_news_import_claims"}
_KEYS = {"result": "version_id", "weather": "snapshot_id", "raw": "content_hash"}


def _stamp(value):
    return utc(value).isoformat(timespec="microseconds")


def _namespace(value):
    if value not in NAMESPACES:
        raise ValueError("INVALID_TEMPORAL_NAMESPACE")
    return value


def _select(namespace):
    if namespace == "news":
        return """SELECT c.rowid AS _sequence, c.import_id, c.origin_claim_id,
            c.assertion_id, c.source_fetched_at, a.*, i.observed_at,
            i.import_key, i.provider, i.request_id, i.model, i.result_hash
            FROM sports_news_import_claims c
            JOIN sports_news_assertions a ON a.assertion_id=c.assertion_id
            JOIN sports_news_imports i ON i.import_id=c.import_id"""
    return f"SELECT rowid AS _sequence,* FROM {_TABLES[namespace]}"


def _key(namespace, row):
    if namespace == "news":
        return encoded([row["import_id"], row["origin_claim_id"], row["assertion_id"]])
    return row[_KEYS[namespace]]


def _source_hash(row):
    return digest({key: value for key, value in row.items() if key != "_sequence"})


def _exists(connection, table):
    return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                   (table,)).fetchone())


class TemporalStore:
    def __init__(self, database, raw_root, *, clock=None):
        self.database, self.raw_root = database, Path(raw_root).resolve()
        self.clock = clock or (lambda: datetime.now(UTC))
        self._last_clock = None

    def _now(self):
        now = utc(self.clock())
        if self._last_clock is not None and now < self._last_clock:
            raise ValueError("CLOCK_MOVED_BACKWARDS")
        self._last_clock = now
        return now

    def initialize(self):
        with self.database.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS temporal_receipts (
                receipt_id TEXT PRIMARY KEY, namespace TEXT NOT NULL,
                source_key TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                observed_at TEXT NOT NULL, published_at TEXT, ingested_at TEXT,
                available_at TEXT NOT NULL, times_json TEXT NOT NULL,
                UNIQUE(namespace,source_key,source_sha256))""")
            db.execute("""CREATE INDEX IF NOT EXISTS temporal_asof
                ON temporal_receipts(namespace,available_at,observed_at)""")
            for operation in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS temporal_receipts_no_{operation.lower()}
                    BEFORE {operation} ON temporal_receipts BEGIN
                    SELECT RAISE(ABORT,'temporal receipts are append-only'); END""")

    def _raw_proof(self, row):
        path = (self.raw_root / row["relative_path"]).resolve()
        if not path.is_relative_to(self.raw_root) or path == self.raw_root:
            raise ValueError("RAW_PATH_OUTSIDE_ARCHIVE")
        if not path.is_file():
            raise ValueError("RAW_PAYLOAD_MISSING")
        content = path.read_bytes()
        if (hashlib.sha256(content).hexdigest() != row["content_hash"]
                or len(content) != row["size_bytes"]):
            raise ValueError("RAW_PAYLOAD_HASH_OR_SIZE_MISMATCH")

    def capture(self, namespace, *, after=0, limit=1000):
        """Read bounded committed source rows, then witness their availability.

        Cursor is the source rowid, not a claimed event/ingestion time. Invalid
        legacy evidence is reported explicitly and never gets a receipt.
        """
        _namespace(namespace)
        if (type(after) is not int or not 0 <= after <= 2**63 - 1
                or type(limit) is not int or not 1 <= limit <= 10000):
            raise ValueError("INVALID_TEMPORAL_PAGE")
        with closing(self.database.connect()) as db:
            if _exists(db, "temporal_receipts"):
                bound = db.execute("SELECT max(available_at) FROM temporal_receipts").fetchone()[0]
                if bound is not None:
                    bound = utc(bound)
                    self._last_clock = max(self._last_clock or bound, bound)
            if not _exists(db, _TABLES[namespace]):
                return {"scanned": 0, "created": 0, "rejected": [], "next_after": after,
                        "exhausted": True}
            rows = [dict(row) for row in db.execute(_select(namespace) +
                " WHERE " + ("c.rowid" if namespace == "news" else "rowid") +
                ">? ORDER BY _sequence LIMIT ?", (after, limit + 1)).fetchall()]
        exhausted, rows = len(rows) <= limit, rows[:limit]
        prepared, rejected = [], []
        for row in rows:
            source_key = _key(namespace, row)
            try:
                if namespace == "raw":
                    self._raw_proof(row)
                # Only after the committed source read and raw-file verification.
                available = self._now()
                times = project_times(namespace, row, available_at=available)
                source_sha = _source_hash(row)
                receipt_id = digest([namespace, source_key, source_sha])
                prepared.append((receipt_id, namespace, source_key, source_sha,
                    times["observed_at"], times["published_at"], times["ingested_at"],
                    times["available_at"], encoded(times)))
            except (ValueError, TypeError, KeyError, OSError) as error:
                if str(error) == "CLOCK_MOVED_BACKWARDS":
                    raise
                rejected.append({"source_key": source_key, "reason": str(error)})
        created = 0
        with self.database.transaction() as db:
            for values in prepared:
                created += db.execute("INSERT OR IGNORE INTO temporal_receipts VALUES (?,?,?,?,?,?,?,?,?)",
                                      values).rowcount
        return {"scanned": len(rows), "created": created, "rejected": rejected,
                "next_after": rows[-1]["_sequence"] if rows else after, "exhausted": exhausted}

    def _source(self, db, namespace, key):
        if not _exists(db, _TABLES[namespace]):
            return None
        if namespace == "news":
            values = json.loads(key)
            query = _select(namespace) + " WHERE c.import_id=? AND c.origin_claim_id=? AND c.assertion_id=?"
        else:
            values, query = [key], _select(namespace) + f" WHERE {_KEYS[namespace]}=?"
        row = db.execute(query, values).fetchone()
        return dict(row) if row is not None else None

    def records(self, namespace, as_of, *, limit=1000, result_version_ids=None):
        """Strict local availability view; absent proof never means historically known.

        Revalidate the source digest and payload on reads. A mutated/deleted raw
        source cannot borrow an earlier receipt. This view does not apply to any
        betting decision until separately promoted from SHADOW.
        """
        _namespace(namespace)
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("INVALID_TEMPORAL_LIMIT")
        cutoff = _stamp(as_of)
        if result_version_ids is not None and (namespace != 'result'
                or not isinstance(result_version_ids, list) or len(result_version_ids) > 10000
                or any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in result_version_ids)
                or len(set(result_version_ids)) != len(result_version_ids)):
            raise ValueError('INVALID_TEMPORAL_RESULT_SELECTION')
        if result_version_ids == []:
            return []
        selected_clause = '' if result_version_ids is None else (
            ' AND source_key IN (' + ','.join('?' for _ in result_version_ids) + ')')
        result = []
        with closing(self.database.connect()) as db:
            db.execute("BEGIN")  # Receipt and source content share one read snapshot.
            rows = db.execute("""SELECT * FROM temporal_receipts WHERE namespace=?
                AND available_at<=? AND observed_at<=?
                AND (published_at IS NULL OR published_at<=?)
                AND (ingested_at IS NULL OR ingested_at<=?)
                """ + selected_clause + " ORDER BY available_at,source_key,source_sha256",
                (namespace, cutoff, cutoff, cutoff, cutoff, *(result_version_ids or [])))
            for receipt in rows:
                source = self._source(db, namespace, receipt["source_key"])
                if source is None or _source_hash(source) != receipt["source_sha256"]:
                    continue
                try:
                    if namespace == "raw":
                        self._raw_proof(source)
                    times = project_times(namespace, source, available_at=receipt["available_at"])
                except (ValueError, TypeError, KeyError, OSError):
                    continue
                if encoded(times) != receipt["times_json"]:
                    continue
                result.append({"receipt_id": receipt["receipt_id"], "source_key": receipt["source_key"],
                               "source_sha256": receipt["source_sha256"], "times": times})
                if len(result) == limit:
                    break
        return result

    def summary(self):
        with closing(self.database.connect()) as db:
            counts = [dict(row) for row in db.execute("""SELECT namespace,count(*) AS receipts,
                sum(ingested_at IS NULL) AS original_ingestion_unknown,
                sum(published_at IS NULL) AS publication_unknown,
                min(available_at) AS first_proven_available_at,
                max(available_at) AS last_proven_available_at
                FROM temporal_receipts GROUP BY namespace ORDER BY namespace""")]
        return {"mode": "SHADOW", "availability_basis": "SOURCE_PRESENCE_UPPER_BOUND", "sources": counts}
