"""Append-only canonical entities and evidenced provider identities.

Explicit initialization only. Shares the sports database; never migrates the
ticket ledger or changes legacy IDs. Names alone cannot create provider links.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from contextlib import nullcontext
from datetime import UTC, datetime
import hashlib
import json
import re
from urllib.parse import urlsplit

from goal_analysis.normalization.canonical import (
    Entity, KINDS, check_id, entity_from_record, text, utc, validate_graph,
)
from .db import Database

PROVIDERS = frozenset({"api_football", "the_stats_api", "football_data_co_uk", "the_odds_api"})
METHODS = frozenset({"NATIVE_PROVIDER_ID", "VERIFIED_PROVIDER_MAPPING",
                     "EXACT_NAMES_KICKOFF_EXPLICIT_LEAGUE"})


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderBinding:
    provider: str
    kind: str
    provider_id: str
    entity_id: str

    def __post_init__(self):
        if self.provider not in PROVIDERS or self.kind not in KINDS:
            raise ValueError("unknown provider or entity kind")
        text(self.provider_id, "provider_id")
        check_id(self.entity_id, self.kind)


@dataclass(frozen=True, slots=True)
class MappingEvidence:
    method: str
    source_url: str
    content_sha256: str
    observed_at: datetime

    def __post_init__(self):
        if self.method not in METHODS:
            raise ValueError("mapping requires explicit deterministic evidence")
        parts = urlsplit(text(self.source_url, "source_url"))
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("mapping evidence requires a credential-free source URL")
        if not isinstance(self.content_sha256, str) or not re.fullmatch("[0-9a-f]{64}", self.content_sha256):
            raise ValueError("mapping evidence requires a SHA-256")
        object.__setattr__(self, "observed_at", utc(self.observed_at))

    def to_record(self):
        return {"method": self.method, "source_url": self.source_url,
                "content_sha256": self.content_sha256, "observed_at": self.observed_at.isoformat()}


class CanonicalRegistry:
    def __init__(self, database: Database, *, clock=None):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def initialize(self):
        with self.database.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS canonical_entities (
                entity_id TEXT PRIMARY KEY, kind TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS canonical_entity_versions (
                revision_id TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL REFERENCES canonical_entities(entity_id),
                record_json TEXT NOT NULL, content_sha256 TEXT NOT NULL,
                observed_at TEXT NOT NULL, ingested_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS canonical_provider_bindings (
                binding_id TEXT PRIMARY KEY, provider TEXT NOT NULL, kind TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                entity_id TEXT NOT NULL REFERENCES canonical_entities(entity_id),
                evidence_json TEXT NOT NULL, observed_at TEXT NOT NULL, ingested_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS canonical_entity_proofs (
                proof_id TEXT PRIMARY KEY,
                revision_id TEXT NOT NULL REFERENCES canonical_entity_versions(revision_id),
                evidence_json TEXT NOT NULL, ingested_at TEXT NOT NULL)""")
            db.execute("""CREATE INDEX IF NOT EXISTS canonical_binding_lookup
                ON canonical_provider_bindings(provider, kind, provider_id, observed_at, ingested_at)""")
            db.execute("""CREATE INDEX IF NOT EXISTS canonical_entity_asof
                ON canonical_entity_versions(entity_id, observed_at, ingested_at)""")
            for table in ("canonical_entities", "canonical_entity_versions", "canonical_provider_bindings",
                          "canonical_entity_proofs"):
                for action in ("UPDATE", "DELETE"):
                    db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                        BEFORE {action} ON {table}
                        BEGIN SELECT RAISE(ABORT, 'canonical registry is append-only'); END""")

    def register(self, entities, bindings, evidence: MappingEvidence, *, _connection=None):
        """Atomically validate a complete graph and append identities/provenance.

        Reimporting the same evidence is idempotent even with a later clock.
        Changed attributes become a new version, never an update of old facts.
        """
        if not isinstance(evidence, MappingEvidence):
            raise ValueError("typed mapping evidence required")
        graph = validate_graph(entities)
        bindings = tuple(bindings)
        for binding in bindings:
            if not isinstance(binding, ProviderBinding) or binding.entity_id not in graph:
                raise ValueError("binding target must exist in the supplied validated graph")
        now = utc(self.clock())
        if evidence.observed_at > now:
            raise ValueError("future mapping evidence")
        observed = evidence.observed_at.isoformat(timespec="microseconds")
        ingested = now.isoformat(timespec="microseconds")
        proof = evidence.to_record()
        counts = {"entities_created": 0, "versions_created": 0, "bindings_created": 0,
                  "proofs_created": 0}
        boundary = self.database.transaction() if _connection is None else nullcontext(_connection)
        with boundary as db:
            for binding in bindings:
                prior = db.execute("""SELECT DISTINCT entity_id FROM canonical_provider_bindings
                    WHERE provider=? AND kind=? AND provider_id=?""",
                    (binding.provider, binding.kind, binding.provider_id)).fetchall()
                if any(row[0] != binding.entity_id for row in prior):
                    raise ValueError("CONFLICTING_PROVIDER_IDENTITY")
            # Also reject two contradictory links within this same transaction.
            claims = {}
            for binding in bindings:
                key = (binding.provider, binding.kind, binding.provider_id)
                if key in claims and claims[key] != binding.entity_id:
                    raise ValueError("CONFLICTING_PROVIDER_IDENTITY")
                claims[key] = binding.entity_id
            for entity in graph.values():
                counts["entities_created"] += db.execute(
                    "INSERT OR IGNORE INTO canonical_entities VALUES (?,?)",
                    (entity.entity_id, entity.KIND)).rowcount
                record = entity.to_record()
                payload_hash = digest(record)
                conflicting = db.execute("""SELECT 1 FROM canonical_entity_versions
                    WHERE entity_id=? AND observed_at=? AND content_sha256<>? LIMIT 1""",
                    (entity.entity_id, observed, payload_hash)).fetchone()
                if conflicting:
                    raise ValueError("CONFLICTING_ENTITY_AT_SAME_TIME")
                revision = digest([entity.entity_id, payload_hash, observed])
                counts["versions_created"] += db.execute(
                    "INSERT OR IGNORE INTO canonical_entity_versions VALUES (?,?,?,?,?,?)",
                    (revision, entity.entity_id, encoded(record), payload_hash, observed, ingested)).rowcount
                counts["proofs_created"] += db.execute(
                    "INSERT OR IGNORE INTO canonical_entity_proofs VALUES (?,?,?,?)",
                    (digest([revision, proof]), revision, encoded(proof), ingested)).rowcount
            for binding in bindings:
                identity = [binding.provider, binding.kind, binding.provider_id, binding.entity_id]
                binding_id = digest([identity, proof])
                counts["bindings_created"] += db.execute(
                    "INSERT OR IGNORE INTO canonical_provider_bindings VALUES (?,?,?,?,?,?,?,?)",
                    (binding_id, *identity, encoded(proof), observed, ingested)).rowcount
        return counts

    def register_many(self, batches):
        """One atomic data transaction across all pages of a catalogue/import."""
        counts = Counter()
        with self.database.transaction() as db:
            for entities, bindings, evidence in batches:
                counts.update(self.register(entities, bindings, evidence, _connection=db))
        return dict(counts)

    def resolve(self, provider, kind, provider_id, *, as_of):
        if provider not in PROVIDERS or kind not in KINDS:
            raise ValueError("unknown provider or entity kind")
        text(provider_id, "provider_id")
        cutoff = utc(as_of).isoformat(timespec="microseconds")
        with self.database.connect() as db:
            rows = db.execute("""SELECT DISTINCT entity_id FROM canonical_provider_bindings
                WHERE provider=? AND kind=? AND provider_id=?
                AND observed_at<=? AND ingested_at<=?""",
                (provider, kind, provider_id, cutoff, cutoff)).fetchall()
        if len(rows) > 1:
            raise ValueError("CONFLICTING_PROVIDER_IDENTITY")
        return rows[0][0] if rows else None

    def entity(self, entity_id, *, as_of):
        cutoff = utc(as_of).isoformat(timespec="microseconds")
        with self.database.connect() as db:
            row = db.execute("""SELECT record_json,content_sha256 FROM canonical_entity_versions
                WHERE entity_id=? AND observed_at<=? AND ingested_at<=?
                ORDER BY observed_at DESC, ingested_at DESC, revision_id DESC LIMIT 1""",
                (entity_id, cutoff, cutoff)).fetchone()
        if row is None:
            return None
        record = json.loads(row[0])
        if digest(record) != row[1]:
            raise ValueError("CORRUPT_CANONICAL_ENTITY")
        return entity_from_record(record)

    def provenance(self, entity_id, *, as_of):
        cutoff = utc(as_of).isoformat(timespec="microseconds")
        with self.database.connect() as db:
            rows = db.execute("""SELECT p.evidence_json FROM canonical_entity_proofs p
                JOIN canonical_entity_versions v ON v.revision_id=p.revision_id
                WHERE v.entity_id=? AND v.observed_at<=? AND v.ingested_at<=?
                AND p.ingested_at<=? ORDER BY v.observed_at,p.proof_id""",
                (entity_id, cutoff, cutoff, cutoff)).fetchall()
        return [json.loads(row[0]) for row in rows]
