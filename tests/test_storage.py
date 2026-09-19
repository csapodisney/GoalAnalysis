from datetime import datetime, timedelta, timezone

from goal_analysis.storage import CacheStore, Database, SnapshotStore


def test_database_initialization_is_idempotent(tmp_path) -> None:
    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    database.initialize()

    with database.connect() as connection:
        rows = connection.execute("SELECT version FROM schema_version").fetchall()
    assert [row["version"] for row in rows] == [1]


def test_cache_expires_deterministically(tmp_path) -> None:
    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    cache = CacheStore(database)
    start = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    cache.set("fixtures", "2026-09-20", {"count": 12}, timedelta(minutes=5), start)
    assert cache.get("fixtures", "2026-09-20", start + timedelta(minutes=4)) == {
        "count": 12
    }
    assert cache.get("fixtures", "2026-09-20", start + timedelta(minutes=5)) is None


def test_snapshot_is_content_addressed_and_deduplicated(tmp_path) -> None:
    database = Database(tmp_path / "test.sqlite3")
    database.initialize()
    store = SnapshotStore(database, tmp_path / "raw")
    content = b'{"fixture": 123}'
    fetched_at = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    first = store.save("demo", "fixtures", content, "application/json", fetched_at)
    second = store.save("demo", "fixtures", content, "application/json", fetched_at)

    assert first.content_hash == second.content_hash
    assert store.read(first.content_hash) == content
    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM raw_snapshots").fetchone()[0]
    assert count == 1
