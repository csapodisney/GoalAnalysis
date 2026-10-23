"""Immutable sourced model summaries and explicitly observational measurement links.

This module never retrieves posts, verifies their content, or estimates effects.
Grok claim IDs are request-local: changed summaries remain separate assertions,
without inferring that one summary supersedes another claim about the same post.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from goal_analysis.normalization.news_kinds import NEWS_KINDS

from .db import Database

_CONTENT_STATUS = "UNVERIFIED"
_PROVENANCE_STATUS = "CITATION_AND_TIMESTAMP_CHECKED"
_REPRESENTATION = "MODEL_GENERATED_SUMMARY"
_INTERPRETATION = "OBSERVATIONAL_NOT_CAUSAL"
_KINDS = frozenset(NEWS_KINDS)
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS sports_news_imports (
        import_id INTEGER PRIMARY KEY, import_key TEXT NOT NULL UNIQUE,
        provider TEXT NOT NULL, request_id TEXT NOT NULL, model TEXT NOT NULL,
        observed_at TEXT NOT NULL, result_status TEXT NOT NULL, result_hash TEXT NOT NULL,
        claims_accepted INTEGER NOT NULL, claims_skipped INTEGER NOT NULL,
        skipped_reasons_json TEXT NOT NULL,
        license_status TEXT NOT NULL DEFAULT 'UNREVIEWED_COMMERCIAL_USE'
    )""",
    """CREATE TABLE IF NOT EXISTS sports_news_assertions (
        assertion_id INTEGER PRIMARY KEY, content_hash TEXT NOT NULL UNIQUE,
        source_platform TEXT NOT NULL, source_key TEXT NOT NULL, source_url TEXT NOT NULL,
        author TEXT NOT NULL, published_at TEXT NOT NULL, event_at TEXT,
        fixture_namespace TEXT NOT NULL, fixture_id TEXT NOT NULL,
        kind TEXT NOT NULL, claim TEXT NOT NULL, content_status TEXT NOT NULL,
        provenance_status TEXT NOT NULL, representation TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sports_news_import_claims (
        import_id INTEGER NOT NULL REFERENCES sports_news_imports(import_id),
        origin_claim_id TEXT NOT NULL,
        assertion_id INTEGER NOT NULL REFERENCES sports_news_assertions(assertion_id),
        source_fetched_at TEXT NOT NULL, PRIMARY KEY(import_id, origin_claim_id)
    )""",
    """CREATE TABLE IF NOT EXISTS sports_news_associations (
        association_id INTEGER PRIMARY KEY, content_hash TEXT NOT NULL UNIQUE,
        assertion_id INTEGER NOT NULL REFERENCES sports_news_assertions(assertion_id),
        fixture_id TEXT NOT NULL, observed_at TEXT NOT NULL,
        interpretation TEXT NOT NULL CHECK(interpretation = 'OBSERVATIONAL_NOT_CAUSAL'),
        before_measured_at TEXT NOT NULL, before_source_ref TEXT NOT NULL,
        before_metrics_json TEXT NOT NULL, after_measured_at TEXT NOT NULL,
        after_source_ref TEXT NOT NULL, after_metrics_json TEXT NOT NULL,
        metadata_json TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS sports_news_fixture
        ON sports_news_assertions(fixture_namespace, fixture_id, published_at)""",
    """CREATE INDEX IF NOT EXISTS sports_news_source
        ON sports_news_assertions(source_platform, source_key, published_at)""",
    """CREATE INDEX IF NOT EXISTS sports_news_kind
        ON sports_news_assertions(kind, published_at)""",
    """CREATE INDEX IF NOT EXISTS sports_news_receipt
        ON sports_news_imports(observed_at, request_id)""",
    """CREATE INDEX IF NOT EXISTS sports_news_assertion_provenance
        ON sports_news_import_claims(assertion_id, import_id)""",
    """CREATE INDEX IF NOT EXISTS sports_news_association_fixture
        ON sports_news_associations(fixture_id, observed_at)""",
)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ValueError("news values must be finite JSON-compatible data") from error


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, name: str, maximum: int = 600) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be a nonempty bounded string")
    if any(ord(character) < 32 and character not in "\t\n" for character in value):
        raise ValueError(f"{name} contains control characters")
    return value


def _stamp(value: Any, name: str) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"invalid {name}") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _assertion(row: dict, observed: str) -> tuple[dict, str, str]:
    claim_id = _text(row.get("claim_id"), "claim_id", 160)
    source = _text(row.get("source_url"), "source_url", 256)
    parsed = urlsplit(source)
    match = re.fullmatch(r"/([A-Za-z0-9_]{1,15})/status/([0-9]{15,20})/?", parsed.path)
    if (parsed.scheme != "https" or parsed.netloc.lower() not in
            {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
            or parsed.query or parsed.fragment or match is None):
        raise ValueError("accepted Grok claim must reference an X post")
    author = _text(row.get("author"), "author", 16)
    if author.lstrip("@").lower() != match[1].lower():
        raise ValueError("claim author does not match source URL")
    published = _stamp(row.get("published_at"), "published_at")
    fetched = _stamp(row.get("fetched_at"), "fetched_at")
    if published > fetched or fetched > observed:
        raise ValueError("claim publication/fetch cannot follow result observation")
    # Recheck the accepted source identity/time without requesting or copying a post.
    post_time = datetime.fromtimestamp(((int(match[2]) >> 22) + 1288834974657) / 1000, UTC)
    if abs((datetime.fromisoformat(published) - post_time).total_seconds()) > 1:
        raise ValueError("claim publication does not match X source timestamp")
    if row.get("kind") not in _KINDS:
        raise ValueError("unsupported accepted claim kind")
    assertion = {
        "source_platform": "x", "source_key": f"x:{match[2]}", "source_url": source,
        "author": author, "published_at": published, "event_at": None,
        # This is the Arthur fixture reference; no cross-provider identity is inferred.
        "fixture_namespace": "arthur", "fixture_id": _text(row.get("fixture_id"), "fixture_id", 160),
        "kind": row["kind"], "claim": _text(row.get("claim"), "claim"),
        "content_status": _CONTENT_STATUS, "provenance_status": _PROVENANCE_STATUS,
        "representation": _REPRESENTATION,
    }
    return assertion, claim_id, fetched


class NewsStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.transaction() as connection:
            for statement in _SCHEMA:
                connection.execute(statement)
            for table in ("imports", "assertions", "import_claims", "associations"):
                for operation in ("UPDATE", "DELETE"):
                    connection.execute(f"""CREATE TRIGGER IF NOT EXISTS
                        sports_news_{table}_no_{operation.lower()}
                        BEFORE {operation} ON sports_news_{table}
                        BEGIN SELECT RAISE(ABORT, 'sports news is append-only'); END""")

    def ingest_grok_result(
        self, result: dict, *, request_id: str, observed_at: datetime, model: str = "grok-4.7",
    ) -> dict[str, int]:
        """Import only validator-accepted, cited claims, with original receipt times.

        observed_at is the original result completion time when backfilling.
        Missing validation markers/citations are counted and skipped. Malformed
        accepted claims reject the entire import atomically. Request-local claim
        IDs and model names are provenance, excluded from assertion content hashes.
        """
        if not isinstance(result, dict):
            raise TypeError("result must be a mapping")
        request_id, model = _text(request_id, "request_id", 160), _text(model, "model", 160)
        if "model" in result and result["model"] != model:
            raise ValueError("result model does not match supplied origin model")
        observed = _stamp(observed_at, "observed_at")
        status = _text(result.get("status"), "status", 80)
        rows, citations = result.get("claims", []), result.get("citations", [])
        if not isinstance(rows, list) or not isinstance(citations, list):
            raise TypeError("claims and citations must be lists")
        if any(not isinstance(citation, str) for citation in citations):
            raise TypeError("citations must contain strings")
        prepared, seen, skipped = [], {}, {}
        for row in rows:
            reason = None
            if (not isinstance(row, dict) or row.get("content_status") != _CONTENT_STATUS
                    or row.get("provenance_status") != _PROVENANCE_STATUS):
                reason = "NOT_VALIDATOR_ACCEPTED"
            elif row.get("source_url") not in citations:
                reason = "MISSING_CITATION"
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            assertion, claim_id, fetched = _assertion(row, observed)
            digest = _digest(assertion)
            if claim_id in seen and seen[claim_id] != (digest, fetched):
                raise ValueError("conflicting duplicate origin claim ID")
            if claim_id not in seen:
                prepared.append((assertion, claim_id, fetched, digest))
            seen[claim_id] = (digest, fetched)
        result_hash = _digest(result)
        import_key = _digest({"request_id": request_id, "model": model,
                              "observed_at": observed, "result_hash": result_hash})
        counts = {"import_id": 0, "imports_created": 0, "assertions_created": 0,
                  "assertions_reused": 0, "claims_linked": 0, "claims_accepted": len(prepared),
                  "claims_skipped": sum(skipped.values())}
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT import_id FROM sports_news_imports WHERE import_key = ?", (import_key,)
            ).fetchone()
            if existing is not None:
                counts.update(import_id=existing["import_id"], assertions_reused=len(prepared))
                return counts
            cursor = connection.execute("""INSERT INTO sports_news_imports (
                import_key, provider, request_id, model, observed_at, result_status,
                result_hash, claims_accepted, claims_skipped, skipped_reasons_json
            ) VALUES (?, 'xai_grok', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (import_key, request_id, model, observed, status, result_hash, len(prepared),
                 sum(skipped.values()), _json(skipped)))
            import_id = cursor.lastrowid
            counts.update(import_id=import_id, imports_created=1)
            for assertion, claim_id, fetched, digest in prepared:
                existing = connection.execute(
                    "SELECT assertion_id FROM sports_news_assertions WHERE content_hash = ?",
                    (digest,),
                ).fetchone()
                if existing is None:
                    names = ("content_hash", *assertion)
                    cursor = connection.execute(
                        f"INSERT INTO sports_news_assertions ({', '.join(names)}) "
                        f"VALUES ({', '.join('?' for _ in names)})", (digest, *assertion.values()),
                    )
                    assertion_id = cursor.lastrowid
                    counts["assertions_created"] += 1
                else:
                    assertion_id = existing["assertion_id"]
                    counts["assertions_reused"] += 1
                connection.execute("""INSERT INTO sports_news_import_claims
                    (import_id, origin_claim_id, assertion_id, source_fetched_at)
                    VALUES (?, ?, ?, ?)""", (import_id, claim_id, assertion_id, fetched))
                counts["claims_linked"] += 1
        return counts

    def claims(
        self, *, fixture_id: str | None = None, as_of: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """All distinct accepted assertion versions with latest eligible provenance.

        As-of requires result receipt, claim fetch and source publication at/before
        cutoff. No claim becomes historically available merely by its publication date.
        """
        conditions, parameters = [], []
        if fixture_id is not None:
            conditions.append("a.fixture_id = ?")
            parameters.append(_text(fixture_id, "fixture_id", 160))
        if as_of is not None:
            cutoff = _stamp(as_of, "as_of")
            conditions += ["i.observed_at <= ?", "r.source_fetched_at <= ?", "a.published_at <= ?"]
            parameters += [cutoff, cutoff, cutoff]
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        with self.database.connect() as connection:
            rows = connection.execute(f"""WITH ranked AS (
                SELECT a.*, i.request_id, i.model, i.observed_at, i.import_id,
                    i.license_status, r.origin_claim_id, r.source_fetched_at,
                    ROW_NUMBER() OVER(PARTITION BY a.assertion_id
                        ORDER BY i.observed_at DESC, i.import_id DESC, r.origin_claim_id) AS rank
                FROM sports_news_assertions a
                JOIN sports_news_import_claims r USING(assertion_id)
                JOIN sports_news_imports i USING(import_id) {where}
            ) SELECT * FROM ranked WHERE rank = 1 ORDER BY published_at, assertion_id""",
                parameters).fetchall()
        # sqlite3.Row iteration yields values; keys() is required here.
        return [{key: row[key] for key in row.keys() if key != "rank"}  # noqa: SIM118
                for row in rows]

    def record_association(
        self, assertion_id: int, *, fixture_id: str, observed_at: datetime,
        before: dict, after: dict, metadata: dict | None = None,
    ) -> dict[str, int]:
        """Record supplied measurements only; this never computes an effect.

        before/after each require measured_at (aware datetime or ISO timestamp),
        source_ref (explicit source/snapshot reference) and metrics (finite numeric
        mapping). Publication is not assumed to be the underlying event time.
        """
        if type(assertion_id) is not int or assertion_id < 1:
            raise ValueError("assertion_id must be a positive integer")
        fixture = _text(fixture_id, "fixture_id", 160)
        observed = _stamp(observed_at, "observed_at")
        measurements = []
        for name, measurement in (("before", before), ("after", after)):
            if not isinstance(measurement, dict):
                raise TypeError(f"{name} must be a mapping")
            stamp = _stamp(measurement.get("measured_at"), f"{name}.measured_at")
            reference = _text(measurement.get("source_ref"), f"{name}.source_ref", 2048)
            metrics = measurement.get("metrics")
            if (not isinstance(metrics, dict) or not metrics or any(
                not isinstance(key, str) or not key.strip() or type(value) not in (int, float)
                or not math.isfinite(value) for key, value in metrics.items()
            )):
                raise ValueError("metrics must be nonempty finite numeric mappings")
            measurements.append((stamp, reference, _json(metrics)))
        if measurements[0][0] > measurements[1][0] or measurements[1][0] > observed:
            raise ValueError("measurement times must be before <= after <= observed_at")
        if metadata is not None and not isinstance(metadata, dict):
            raise TypeError("metadata must be a mapping")
        values = (assertion_id, fixture, observed, _INTERPRETATION,
                  *measurements[0], *measurements[1], _json(metadata or {}))
        digest = _digest(values)
        with self.database.transaction() as connection:
            available = connection.execute("""SELECT a.fixture_id FROM sports_news_assertions a
                JOIN sports_news_import_claims r USING(assertion_id)
                JOIN sports_news_imports i USING(import_id)
                WHERE a.assertion_id = ? AND i.observed_at <= ?
                  AND r.source_fetched_at <= ? AND a.published_at <= ? LIMIT 1""",
                (assertion_id, observed, observed, observed)).fetchone()
            if available is None or available["fixture_id"] != fixture:
                raise ValueError("association must reference a matching assertion already observed")
            existing = connection.execute("""SELECT association_id FROM sports_news_associations
                WHERE content_hash = ?""", (digest,)).fetchone()
            if existing is not None:
                return {"association_id": existing["association_id"], "associations_created": 0}
            cursor = connection.execute("""INSERT INTO sports_news_associations (
                content_hash, assertion_id, fixture_id, observed_at, interpretation,
                before_measured_at, before_source_ref, before_metrics_json,
                after_measured_at, after_source_ref, after_metrics_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (digest, *values))
            return {"association_id": cursor.lastrowid, "associations_created": 1}

    def associations(self, *, as_of: datetime | None = None) -> list[dict[str, Any]]:
        conditions, parameters = "", ()
        if as_of is not None:
            cutoff = _stamp(as_of, "as_of")
            conditions = """WHERE observed_at <= ? AND before_measured_at <= ?
                AND after_measured_at <= ?"""
            parameters = (cutoff, cutoff, cutoff)
        with self.database.connect() as connection:
            rows = connection.execute(f"""SELECT * FROM sports_news_associations
                {conditions} ORDER BY observed_at, association_id""", parameters).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for name in ("before_metrics", "after_metrics", "metadata"):
                item[name] = json.loads(item.pop(f"{name}_json"))
            result.append(item)
        return result

    def summary(self) -> dict[str, Any]:
        """Compact SQL aggregation without loading claim bodies or provenance rows."""
        with self.database.connect() as connection:
            totals = connection.execute("""SELECT COUNT(*) AS total_assertions,
                COUNT(DISTINCT source_key) AS total_sources,
                COUNT(DISTINCT fixture_id) AS total_fixtures FROM sports_news_assertions""").fetchone()
            imports = connection.execute("""SELECT COUNT(*) AS total_imports,
                COALESCE(SUM(claims_skipped), 0) AS claims_skipped
                FROM sports_news_imports""").fetchone()
            associations = connection.execute(
                "SELECT COUNT(*) FROM sports_news_associations").fetchone()[0]
            groups = {}
            for field in ("kind", "content_status", "provenance_status"):
                rows = connection.execute(f"""SELECT {field}, COUNT(*) AS count
                    FROM sports_news_assertions GROUP BY {field} ORDER BY {field}""").fetchall()
                groups[f"by_{field}"] = {row[field]: row["count"] for row in rows}
        return {**dict(totals), **dict(imports), "total_associations": associations, **groups}
