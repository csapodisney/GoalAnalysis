from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path


class Database:
    """Small SQLite boundary with explicit initialization and transactions."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        schema = files("goal_analysis.storage").joinpath("schema.sql").read_text("utf-8")
        with self.connect() as connection:
            connection.executescript(schema)
            connection.execute(
                "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
                (self.SCHEMA_VERSION, datetime.now(UTC).isoformat()),
            )

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """Optionally reserve a writer before reads; ordinary readers stay deferred."""
        if type(immediate) is not bool:
            raise TypeError("immediate must be a boolean")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
