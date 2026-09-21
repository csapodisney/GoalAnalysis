from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Iterator


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
                (self.SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
