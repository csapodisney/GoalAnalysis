from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .db import Database


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    content_hash: str
    provider: str
    resource_type: str
    fetched_at: datetime
    media_type: str
    relative_path: str
    size_bytes: int
    source_url: str | None = None


class SnapshotStore:
    """Content-addressed immutable storage for raw provider responses."""

    def __init__(self, database: Database, root: Path) -> None:
        self.database = database
        self.root = Path(root)

    def save(
        self,
        provider: str,
        resource_type: str,
        content: bytes,
        media_type: str,
        fetched_at: datetime | None = None,
        source_url: str | None = None,
    ) -> SnapshotRecord:
        observed_at = fetched_at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        observed_at = observed_at.astimezone(timezone.utc)

        content_hash = hashlib.sha256(content).hexdigest()
        relative_path = Path(provider) / resource_type / f"{content_hash}.bin"
        destination = self.root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            if destination.read_bytes() != content:
                raise RuntimeError("snapshot hash collision or modified snapshot")
        else:
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(content)
            temporary.replace(destination)

        record = SnapshotRecord(
            content_hash=content_hash,
            provider=provider,
            resource_type=resource_type,
            fetched_at=observed_at,
            media_type=media_type,
            relative_path=relative_path.as_posix(),
            size_bytes=len(content),
            source_url=source_url,
        )

        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO raw_snapshots(
                    content_hash, provider, resource_type, fetched_at,
                    media_type, relative_path, size_bytes, source_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.content_hash,
                    record.provider,
                    record.resource_type,
                    record.fetched_at.isoformat(),
                    record.media_type,
                    record.relative_path,
                    record.size_bytes,
                    record.source_url,
                ),
            )
        return record

    def read(self, content_hash: str) -> bytes:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT relative_path FROM raw_snapshots WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
        if row is None:
            raise KeyError(content_hash)
        return (self.root / row["relative_path"]).read_bytes()
