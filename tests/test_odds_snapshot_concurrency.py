"""Two-connection WAL regression: publish only a committed, complete quote set."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from test_odds_snapshot_store import NOW, counts, parent, quote, setup_store, stamp


@pytest.mark.parametrize("operation", ["ingest", "seal"])
def test_competing_write_after_first_archive_read_cannot_invalidate_publication(tmp_path, monkeypatch, operation):
    store = setup_store(tmp_path)
    staged = store.ingest([quote()], **parent(), _publish=False) if operation == "seal" else None
    original_now = store._now
    contention = []
    other = sqlite3.connect(store.database.path, timeout=0)

    def concurrent_writer(db):
        instant = original_now(db)  # Establish a read snapshot before the competing commit.
        try:
            other.execute("INSERT INTO legacy_ticket VALUES ('concurrent unrelated write')")
            other.commit()
            contention.append("COMMITTED_BEFORE_ARCHIVE_WRITE")
        except sqlite3.OperationalError as error:
            other.rollback()
            contention.append(error.sqlite_errorname)
        return instant

    try:
        monkeypatch.setattr(store, "_now", concurrent_writer)
        if operation == "ingest":
            staged = store.ingest([quote()], **parent(), _publish=False)
        else:
            store.seal(staged["source_id"])
        assert contention == ["SQLITE_BUSY"]
        monkeypatch.setattr(store, "_now", original_now)
        # Releasing archive admission lets the same unrelated writer complete.
        other.execute("INSERT INTO legacy_ticket VALUES ('concurrent unrelated write')")
        other.commit()
        assert other.execute("SELECT count(*) FROM legacy_ticket").fetchone()[0] == 2
    finally:
        other.close()
    assert counts(store)["sports_odds_quotes"] == 1
    if operation == "ingest":
        # Data is committed but still invisible until a distinct later seal.
        assert store.quotes(as_of=NOW) == []
        store.clock = lambda: NOW + timedelta(seconds=1)
        store.seal(staged["source_id"])
    rows = store.quotes(as_of=NOW + timedelta(seconds=2))
    assert len(rows) == 1 and rows[0]["raw"] == quote()["raw"]
    assert rows[0]["sealed_at"] == stamp(NOW + timedelta(seconds=1) if operation == "ingest" else NOW)
    before = counts(store)
    assert store.ingest([quote()], **parent())["quotes_created"] == 0
    assert counts(store) == before


@pytest.mark.parametrize("operation", ["ingest", "seal"])
def test_busy_writer_admission_is_bounded_and_preserves_committed_staging(tmp_path, monkeypatch, operation):
    store = setup_store(tmp_path)
    staged = store.ingest([quote()], **parent(), _publish=False)
    original_connect = store.database.connect

    def short_timeout():
        connection = original_connect()
        connection.execute("PRAGMA busy_timeout=25")
        return connection

    monkeypatch.setattr(store.database, "connect", short_timeout)
    other = original_connect()
    before = counts(store)
    other.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError) as failure:
            if operation == "seal":
                store.seal(staged["source_id"])
            else:
                store.ingest([quote()], **parent(), _publish=False)
        assert failure.value.sqlite_errorname == "SQLITE_BUSY"
        assert counts(store) == before
        assert store.quotes(as_of=NOW) == []
    finally:
        other.rollback()
        other.close()
    store.clock = lambda: NOW + timedelta(seconds=1)
    store.seal(staged["source_id"])
    assert store.quotes(as_of=NOW) == []
    assert len(store.quotes(as_of=NOW + timedelta(seconds=1))) == 1


def test_other_database_read_transactions_keep_wal_concurrency(tmp_path):
    store = setup_store(tmp_path)
    other = sqlite3.connect(store.database.path, timeout=0)
    try:
        with store.database.transaction() as reader:
            assert reader.execute("SELECT count(*) FROM legacy_ticket").fetchone()[0] == 1
            other.execute("INSERT INTO legacy_ticket VALUES ('parallel writer')")
            other.commit()
            assert reader.execute("SELECT count(*) FROM legacy_ticket").fetchone()[0] == 1
        with store.database.transaction() as reader:
            assert reader.execute("SELECT count(*) FROM legacy_ticket").fetchone()[0] == 2
    finally:
        other.close()


def test_seal_clock_is_sampled_after_waiting_for_writer_admission(tmp_path, monkeypatch):
    store = setup_store(tmp_path)
    staged = store.ingest([quote()], **parent(), _publish=False)
    entering, released = Event(), Event()
    original_connect = store.database.connect
    other = original_connect()
    other.execute("BEGIN IMMEDIATE")

    def connection():
        result = original_connect()
        entering.set()
        return result

    def clock():
        assert released.is_set(), "Publication clock sampled before writer admission"
        return NOW + timedelta(seconds=7)

    store.clock = clock
    monkeypatch.setattr(store.database, "connect", connection)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            sealing = pool.submit(store.seal, staged["source_id"])
            try:
                assert entering.wait(2)
            finally:
                released.set()
                other.rollback()
            assert sealing.result(timeout=2)["sealed_at"] == stamp(NOW + timedelta(seconds=7))
    finally:
        other.close()
    assert store.quotes(as_of=NOW + timedelta(seconds=6)) == []
    assert len(store.quotes(as_of=NOW + timedelta(seconds=7))) == 1


def test_simultaneous_sealers_publish_once_without_retiming_the_source(tmp_path):
    store = setup_store(tmp_path)
    staged = store.ingest([quote()], **parent(), _publish=False)
    barrier = Barrier(2)

    def seal():
        from goal_analysis.storage.odds_snapshots import OddsSnapshots
        independent = OddsSnapshots(store.database, clock=lambda: NOW + timedelta(seconds=1))
        barrier.wait(timeout=2)
        return independent.seal(staged["source_id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(seal) for _ in range(2)]
        results = [future.result(timeout=3) for future in futures]
    assert results[0] == results[1]
    assert results[0]["quote_count"] == 1 and counts(store)["sports_odds_publications"] == 1
    assert results[0]["sealed_at"] == stamp(NOW + timedelta(seconds=1))


@pytest.mark.parametrize("invalid", [None, 0, 1, "yes"])
def test_writer_admission_mode_requires_explicit_boolean(tmp_path, invalid):
    store = setup_store(tmp_path)
    with pytest.raises(TypeError), store.database.transaction(immediate=invalid):
        pass
