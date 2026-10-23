import hashlib
import json
from datetime import timedelta

import pytest

from test_canonical_registry import NOW
from goal_analysis.jobs.canonical_seed import seed_competition_catalogue
from goal_analysis.storage import Database, SnapshotStore


def prepare(tmp_path):
    db = Database(tmp_path / "data/goal_analysis.sqlite3")
    db.initialize()
    store = SnapshotStore(db, tmp_path / "data/raw")
    raw = json.dumps({"data": [{"id": "comp_1", "name": "League", "country": "England"}]}).encode()
    saved = store.save("the_stats_api", "competition_catalogue", raw, "application/json", NOW,
                       "https://api.thestatsapi.com/api/football/competitions?page=1")
    return db, saved


def test_seed_consumes_real_raw_boundary_and_is_idempotent(tmp_path):
    db, _ = prepare(tmp_path)
    with db.connect() as connection:
        old = [tuple(r) for r in connection.execute("SELECT * FROM raw_snapshots")]
    result = seed_competition_catalogue(tmp_path, clock=lambda: NOW + timedelta(days=1))
    assert result["counts"]["bindings_created"] == 1
    assert result["external_requests"] == 0
    again = seed_competition_catalogue(tmp_path, clock=lambda: NOW + timedelta(days=2))
    assert not any(again["counts"].values())
    with db.connect() as connection:
        assert [tuple(r) for r in connection.execute("SELECT * FROM raw_snapshots")] == old
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_corrupt_catalogue_is_rejected_before_additive_schema_write(tmp_path):
    db, saved = prepare(tmp_path)
    (tmp_path / "data/raw" / saved.relative_path).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="CORRUPT"):
        seed_competition_catalogue(tmp_path, clock=lambda: NOW)
    with db.connect() as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='canonical_entities'").fetchone() is None


def test_no_catalogue_and_future_catalogue_do_not_claim_coverage_or_write(tmp_path):
    db = Database(tmp_path / "data/goal_analysis.sqlite3")
    db.initialize()
    assert seed_competition_catalogue(tmp_path, clock=lambda: NOW)["status"] == "MISSING"
    prepare(tmp_path)
    with pytest.raises(ValueError, match="future"):
        seed_competition_catalogue(tmp_path, clock=lambda: NOW - timedelta(seconds=1))
    with db.connect() as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='canonical_entities'").fetchone() is None
