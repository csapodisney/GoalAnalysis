"""Populate native competition identities from already downloaded evidence.

No network, name guessing, legacy ID rewrites or ticket changes. Cross-provider
links are a separate, explicit operation with MappingEvidence.
"""
from datetime import UTC, datetime
from pathlib import Path
import hashlib
import json
import sqlite3

from goal_analysis.normalization.canonical import CanonicalCompetition, canonical_id, utc, validate_graph
from goal_analysis.storage.canonical_registry import CanonicalRegistry, MappingEvidence, ProviderBinding
from goal_analysis.storage.db import Database


def seed_competition_catalogue(root, *, clock=None):
    root = Path(root).resolve()
    path = root / "data/goal_analysis.sqlite3"
    if not path.is_file():
        raise ValueError("existing sports database required")
    # Read-only inventory before any additive initialization.
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = list(db.execute("""SELECT * FROM raw_snapshots WHERE
            provider='the_stats_api' AND resource_type='competition_catalogue'
            ORDER BY fetched_at,content_hash"""))
    prepared = []
    started = utc(clock() if clock else datetime.now(UTC))
    if not rows:
        return {"status": "MISSING", "reason": "NO_DOWNLOADED_CATALOGUE",
                "catalogue_snapshots": 0, "counts": {}, "external_requests": 0}
    raw_root = (root / "data/raw").resolve()
    for row in rows:
        raw_path = (raw_root / row["relative_path"]).resolve()
        if not raw_path.is_relative_to(raw_root):
            raise ValueError("raw snapshot escapes archive")
        raw = raw_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["content_hash"]:
            raise ValueError("CORRUPT_CATALOGUE_SNAPSHOT")
        payload = json.loads(raw)
        if not isinstance(payload.get("data"), list):
            raise ValueError("invalid competition catalogue")
        evidence = MappingEvidence("NATIVE_PROVIDER_ID", row["source_url"],
                                   row["content_hash"], utc(row["fetched_at"]))
        if evidence.observed_at > started:
            raise ValueError("future catalogue snapshot")
        entities, bindings = [], []
        for item in payload["data"]:
            identifier = item["id"]
            entity = CanonicalCompetition(entity_id=canonical_id("competition", "the_stats_api:" + identifier),
                                          name=item["name"], country=item.get("country"))
            entities.append(entity)
            bindings.append(ProviderBinding("the_stats_api", "competition", identifier, entity.entity_id))
        validate_graph(entities)
        prepared.append((entities, bindings, evidence))
    registry = CanonicalRegistry(Database(path), clock=clock)
    # Expand is idempotent; every catalogue page is then one atomic data write.
    registry.initialize()
    counts = registry.register_many(prepared)
    return {"status": "COMPLETE", "catalogue_snapshots": len(prepared),
            "counts": counts, "external_requests": 0,
            "scope": "NATIVE_IDS_ONLY_NO_NAME_BASED_CROSS_PROVIDER_MERGES"}
