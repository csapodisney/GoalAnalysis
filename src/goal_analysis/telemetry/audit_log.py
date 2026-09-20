from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from goal_analysis.agents import canonical_sha256


class AuditLogError(ValueError):
    """Raised when the append-only hash chain is invalid."""


class HashChainAuditLog:
    GENESIS = "0" * 64

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        entries = list(self.read())
        previous_hash = entries[-1]["entry_sha256"] if entries else self.GENESIS
        body = {"previous_sha256": previous_hash, "record": dict(record)}
        entry = {**body, "entry_sha256": canonical_sha256(body)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
            handle.write("\n")
        return entry

    def read(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        previous_hash = self.GENESIS
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as error:
                    raise AuditLogError(f"invalid JSON on line {line_number}") from error
                if entry.get("previous_sha256") != previous_hash:
                    raise AuditLogError(f"broken chain on line {line_number}")
                body = {"previous_sha256": entry["previous_sha256"], "record": entry["record"]}
                expected = canonical_sha256(body)
                if entry.get("entry_sha256") != expected:
                    raise AuditLogError(f"hash mismatch on line {line_number}")
                previous_hash = expected
                yield entry

    def verify(self) -> int:
        return sum(1 for _ in self.read())
