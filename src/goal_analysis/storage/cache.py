from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import Database


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


class CacheStore:
    """Namespaced JSON cache with explicit TTL and content hashes."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        ttl: timedelta,
        now: datetime | None = None,
    ) -> str:
        if ttl.total_seconds() <= 0:
            raise ValueError("ttl must be positive")
        created_at = _utc(now or datetime.now(timezone.utc))
        expires_at = created_at + ttl
        value_json = json.dumps(value, sort_keys=True, separators=(",", ":"))
        content_hash = hashlib.sha256(value_json.encode("utf-8")).hexdigest()

        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO cache_entries(
                    namespace, cache_key, value_json, created_at, expires_at, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, cache_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at,
                    content_hash = excluded.content_hash
                """,
                (
                    namespace,
                    key,
                    value_json,
                    created_at.isoformat(),
                    expires_at.isoformat(),
                    content_hash,
                ),
            )
        return content_hash

    def get(
        self,
        namespace: str,
        key: str,
        now: datetime | None = None,
    ) -> Any | None:
        observed_at = _utc(now or datetime.now(timezone.utc))
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT value_json, expires_at
                FROM cache_entries
                WHERE namespace = ? AND cache_key = ?
                """,
                (namespace, key),
            ).fetchone()

        if row is None:
            return None
        if datetime.fromisoformat(row["expires_at"]) <= observed_at:
            self.delete(namespace, key)
            return None
        return json.loads(row["value_json"])

    def delete(self, namespace: str, key: str) -> bool:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM cache_entries WHERE namespace = ? AND cache_key = ?",
                (namespace, key),
            )
        return cursor.rowcount > 0

    def purge_expired(self, now: datetime | None = None) -> int:
        observed_at = _utc(now or datetime.now(timezone.utc))
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM cache_entries WHERE expires_at <= ?",
                (observed_at.isoformat(),),
            )
        return cursor.rowcount
