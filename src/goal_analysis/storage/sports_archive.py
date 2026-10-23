"""Append-only, source-namespaced historical match archive.

CSV kickoff times are local wall-clock times of unknown timezone. They are never
converted to UTC. An as-of view conservatively excludes every same-day match.
Raw snapshot hashes are logical links: the raw snapshot store may be populated
separately, so archive initialization does not alter or depend on its tables.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, time
from typing import Any

from .db import Database

_FIELDS = (
    "provider", "source_record_key", "league_code", "season_code", "match_date",
    "home_team", "away_team", "full_time_home", "full_time_away", "full_time_result",
    "half_time_home", "half_time_away", "half_time_result", "kickoff_local_time",
)
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS sports_archive_imports (
        import_id INTEGER PRIMARY KEY,
        import_key TEXT NOT NULL UNIQUE,
        provider TEXT NOT NULL,
        league_code TEXT NOT NULL,
        season_code TEXT NOT NULL,
        source_url TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        raw_snapshot_hash TEXT NOT NULL,
        dataset_hash TEXT NOT NULL,
        columns_json TEXT NOT NULL,
        encoding TEXT,
        record_count INTEGER NOT NULL,
        rejection_count INTEGER NOT NULL,
        rejections_json TEXT NOT NULL,
        rejection_counts_json TEXT NOT NULL,
        blank_rows_skipped INTEGER NOT NULL,
        metadata_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sports_archive_observations (
        observation_id INTEGER PRIMARY KEY,
        provider TEXT NOT NULL,
        source_record_key TEXT NOT NULL,
        normalized_content_hash TEXT NOT NULL,
        league_code TEXT NOT NULL,
        season_code TEXT NOT NULL,
        match_date TEXT NOT NULL,
        home_team TEXT NOT NULL,
        away_team TEXT NOT NULL,
        full_time_home INTEGER,
        full_time_away INTEGER,
        full_time_result TEXT,
        half_time_home INTEGER,
        half_time_away INTEGER,
        half_time_result TEXT,
        kickoff_local_time TEXT,
        raw_json TEXT NOT NULL,
        odds_snapshot_types_json TEXT NOT NULL,
        UNIQUE(provider, source_record_key, normalized_content_hash)
    )""",
    """CREATE TABLE IF NOT EXISTS sports_archive_import_records (
        import_id INTEGER NOT NULL REFERENCES sports_archive_imports(import_id),
        observation_id INTEGER NOT NULL
            REFERENCES sports_archive_observations(observation_id),
        PRIMARY KEY (import_id, observation_id)
    )""",
    """CREATE INDEX IF NOT EXISTS sports_archive_import_time
        ON sports_archive_imports(provider, fetched_at)""",
    """CREATE INDEX IF NOT EXISTS sports_archive_match_scope
        ON sports_archive_observations(provider, league_code, season_code, match_date)""",
    """CREATE INDEX IF NOT EXISTS sports_archive_match_date
        ON sports_archive_observations(match_date)""",
    """CREATE INDEX IF NOT EXISTS sports_archive_home_team
        ON sports_archive_observations(provider, home_team, match_date)""",
    """CREATE INDEX IF NOT EXISTS sports_archive_away_team
        ON sports_archive_observations(provider, away_team, match_date)""",
    """CREATE INDEX IF NOT EXISTS sports_archive_record_import
        ON sports_archive_import_records(observation_id, import_id)""",
)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("archive values must be finite JSON-compatible data") from error


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _utc(value: datetime, name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _nonnegative(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _record(record: Any, scope: dict[str, str]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("each record must be a mapping")  # noqa: TRY004 - uniform input validation
    result: dict[str, Any] = {}
    for name in ("provider", "source_record_key", "league_code", "season_code",
                 "match_date", "home_team", "away_team"):
        result[name] = _text(record.get(name), name)
    for name, value in scope.items():
        if result[name] != value:
            raise ValueError(f"record {name} does not match dataset {name}")
    try:
        if date.fromisoformat(result["match_date"]).isoformat() != result["match_date"]:
            raise ValueError("date is not canonical")
    except ValueError as error:
        raise ValueError("match_date must be an ISO YYYY-MM-DD date") from error
    for name in ("full_time_home", "full_time_away", "half_time_home", "half_time_away"):
        value = record.get(name)
        result[name] = None if value is None else _nonnegative(value, name)
    for name in ("full_time_result", "half_time_result"):
        value = record.get(name)
        if value is not None and value not in ("H", "D", "A"):
            raise ValueError(f"{name} must be H, D, A, or None")
        result[name] = value
    for prefix in ("full_time", "half_time"):
        home, away = result[f"{prefix}_home"], result[f"{prefix}_away"]
        outcome = result[f"{prefix}_result"]
        if home is not None and away is not None and outcome is not None:
            expected = "H" if home > away else "A" if away > home else "D"
            if outcome != expected:
                raise ValueError(f"{prefix}_result does not agree with scores")
    local_time = record.get("kickoff_local_time")
    if local_time is not None:
        if not isinstance(local_time, str) or not re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", local_time):
            raise ValueError("kickoff_local_time must be HH:MM, HH:MM:SS, or None")
        try:
            time.fromisoformat(local_time)
        except ValueError as error:
            raise ValueError("invalid kickoff_local_time") from error
    result["kickoff_local_time"] = local_time
    raw = record.get("raw")
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        raise ValueError("raw must be a mapping with string column names")
    odds_types = record.get("odds_snapshot_types", {})
    if not isinstance(odds_types, dict) or any(
        key not in raw or value not in ("pre_closing", "closing")
        for key, value in odds_types.items()
    ):
        raise ValueError("odds_snapshot_types must classify existing raw columns")
    # Round-trip freezes caller-owned nested values before entering the transaction.
    result["raw"] = json.loads(_json(raw))
    result["odds_snapshot_types"] = json.loads(_json(odds_types))
    return result


class SportsArchive:
    """Normalized match versions with immutable import provenance and raw fields."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def initialize(self) -> None:
        """Create only this module's tables, indexes, and immutability triggers."""
        with self.database.transaction() as connection:
            for statement in _SCHEMA:
                connection.execute(statement)
            for table in ("imports", "observations", "import_records"):
                for action in ("UPDATE", "DELETE"):
                    connection.execute(f"""CREATE TRIGGER IF NOT EXISTS
                        sports_archive_{table}_no_{action.lower()}
                        BEFORE {action} ON sports_archive_{table}
                        BEGIN SELECT RAISE(ABORT, 'sports archive is append-only'); END""")

    def ingest(
        self, dataset: dict, *, source_url: str, fetched_at: datetime,
        content_hash: str, metadata: dict | None = None,
    ) -> dict[str, int]:
        """Atomically import validated data; exact reruns do not add provenance.

        A new fetch timestamp adds provenance and sightings, while identical
        match content reuses its immutable observation. content_hash links to
        SnapshotStore's raw response; it must be the original bytes' SHA-256.
        """
        if not isinstance(dataset, dict):
            raise ValueError("dataset must be a mapping")  # noqa: TRY004 - uniform input validation
        scope = {name: _text(dataset.get(name), name)
                 for name in ("provider", "league_code", "season_code")}
        source_url = _text(source_url, "source_url")
        fetched = _utc(fetched_at, "fetched_at")
        if not isinstance(content_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", content_hash):
            raise ValueError("content_hash must be a hexadecimal SHA-256 digest")
        content_hash = content_hash.lower()
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a mapping or None")
        metadata_json = _json(metadata or {})
        columns = dataset.get("columns", [])
        if not isinstance(columns, list) or any(not isinstance(item, str) for item in columns):
            raise ValueError("columns must be a list of strings")
        records = dataset.get("records")
        if not isinstance(records, list):
            raise ValueError("records must be a list")  # noqa: TRY004 - uniform input validation
        prepared = [_record(record, scope) for record in records]
        # Multiple contradictory rows in one source cannot have a reliable order.
        seen: dict[str, str] = {}
        unique_records = []
        for record in prepared:
            digest = _hash(record)
            key = record["source_record_key"]
            if key in seen and seen[key] != digest:
                raise ValueError("conflicting records share one source_record_key in an import")
            if key not in seen:
                unique_records.append((record, digest))
            seen[key] = digest
        rejections = dataset.get("rejections", [])
        rejection_counts = dataset.get("rejection_counts", {})
        if not isinstance(rejections, list) or not isinstance(rejection_counts, dict):
            raise ValueError(  # noqa: TRY004 - uniform input validation
                "rejections and rejection_counts must be a list and mapping"
            )
        for reason, count in rejection_counts.items():
            _text(reason, "rejection reason")
            _nonnegative(count, "rejection count")
        rejections_json, rejection_counts_json = _json(rejections), _json(rejection_counts)
        blanks = _nonnegative(dataset.get("blank_rows_skipped", 0), "blank_rows_skipped")
        encoding = dataset.get("encoding")
        if encoding is not None:
            _text(encoding, "encoding")
        dataset_hash = _hash({"records": sorted(seen.items()), "columns": columns,
                              "rejections": rejections, "rejection_counts": rejection_counts,
                              "blank_rows_skipped": blanks, "encoding": encoding})
        import_key = _hash({**scope, "source_url": source_url, "fetched_at": fetched,
                            "content_hash": content_hash, "dataset_hash": dataset_hash,
                            "metadata": json.loads(metadata_json)})
        counts = {"import_id": 0, "imports_created": 0, "observations_created": 0,
                  "observations_reused": 0, "matches_created": 0, "records_linked": 0,
                  "rejections": len(rejections), "blank_rows_skipped": blanks}
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT import_id FROM sports_archive_imports WHERE import_key = ?",
                (import_key,),
            ).fetchone()
            if existing is not None:
                counts["import_id"] = existing["import_id"]
                counts["observations_reused"] = len(unique_records)
                return counts
            cursor = connection.execute("""INSERT INTO sports_archive_imports (
                import_key, provider, league_code, season_code, source_url, fetched_at,
                content_hash, raw_snapshot_hash, dataset_hash, columns_json, encoding,
                record_count, rejection_count, rejections_json, rejection_counts_json,
                blank_rows_skipped, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                import_key, scope["provider"], scope["league_code"], scope["season_code"],
                source_url, fetched, content_hash, content_hash, dataset_hash, _json(columns),
                encoding, len(prepared), len(rejections), rejections_json, rejection_counts_json,
                blanks, metadata_json,
            ))
            import_id = cursor.lastrowid
            counts.update(import_id=import_id, imports_created=1)
            for record, digest in unique_records:
                previous = connection.execute("""SELECT observation_id, normalized_content_hash
                    FROM sports_archive_observations
                    WHERE provider = ? AND source_record_key = ?""",
                    (record["provider"], record["source_record_key"]),
                ).fetchall()
                counts["matches_created"] += int(not previous)
                existing_id = next((row["observation_id"] for row in previous
                                    if row["normalized_content_hash"] == digest), None)
                if existing_id is None:
                    names = (*_FIELDS, "normalized_content_hash", "raw_json",
                             "odds_snapshot_types_json")
                    cursor = connection.execute(
                        f"INSERT INTO sports_archive_observations ({', '.join(names)}) "
                        f"VALUES ({', '.join('?' for _ in names)})",
                        (*(record[name] for name in _FIELDS), digest, _json(record["raw"]),
                         _json(record["odds_snapshot_types"])),
                    )
                    existing_id = cursor.lastrowid
                    counts["observations_created"] += 1
                else:
                    counts["observations_reused"] += 1
                connection.execute("""INSERT INTO sports_archive_import_records
                    (import_id, observation_id) VALUES (?, ?)""", (import_id, existing_id))
                counts["records_linked"] += 1
        return counts

    def matches(
        self, *, provider: str | None = None, league_code: str | None = None,
        season_code: str | None = None, as_of: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return the latest sighting per provider/key, optionally as known at cutoff.

        as_of must be aware. Unknown kickoff timezone requires match_date to be
        strictly before cutoff's UTC date. Ties use immutable import/observation IDs.
        Local wall-clock kickoff fields never imply a UTC timestamp.
        """
        conditions, parameters = [], []
        filters, filter_values = [], []
        if as_of is not None:
            cutoff = _utc(as_of, "as_of")
            conditions.append("i.fetched_at <= ?")
            parameters.append(cutoff)
            filters.append("match_date < ?")
            filter_values.append(as_of.astimezone(UTC).date().isoformat())
        # Apply classification filters after ranking so moved/corrected records
        # cannot reappear under an obsolete league or season.
        for name, value in (("provider", provider), ("league_code", league_code),
                            ("season_code", season_code)):
            if value is not None:
                filters.append(f"{name} = ?")
                filter_values.append(_text(value, name))
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        extra = " AND " + " AND ".join(filters) if filters else ""
        with self.database.connect() as connection:
            rows = connection.execute(f"""WITH ranked AS (
                SELECT o.*, i.import_id, i.source_url, i.fetched_at AS observed_at,
                    i.content_hash AS source_content_hash, i.raw_snapshot_hash,
                    ROW_NUMBER() OVER (
                        PARTITION BY o.provider, o.source_record_key
                        ORDER BY i.fetched_at DESC, i.import_id DESC, o.observation_id DESC
                    ) AS version_rank
                FROM sports_archive_observations o
                JOIN sports_archive_import_records r ON r.observation_id = o.observation_id
                JOIN sports_archive_imports i ON i.import_id = r.import_id
                {where}
            ) SELECT * FROM ranked WHERE version_rank = 1{extra}
              ORDER BY match_date, provider, league_code, source_record_key""",
                (*parameters, *filter_values),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.pop("version_rank")
            item["raw"] = json.loads(item.pop("raw_json"))
            item["odds_snapshot_types"] = json.loads(item.pop("odds_snapshot_types_json"))
            result.append(item)
        return result

    def summary(self) -> dict[str, Any]:
        """Count immutable versions/imports and unique matches by latest classification."""
        by_provider: dict[str, int] = {}
        with self.database.connect() as connection:
            scopes = connection.execute("""WITH ranked AS (
                SELECT o.provider, o.league_code, o.season_code,
                    ROW_NUMBER() OVER (
                        PARTITION BY o.provider, o.source_record_key
                        ORDER BY i.fetched_at DESC, i.import_id DESC, o.observation_id DESC
                    ) AS version_rank
                FROM sports_archive_observations o
                JOIN sports_archive_import_records r ON r.observation_id = o.observation_id
                JOIN sports_archive_imports i ON i.import_id = r.import_id
            ) SELECT provider, league_code, season_code, COUNT(*) AS unique_matches
              FROM ranked WHERE version_rank = 1
              GROUP BY provider, league_code, season_code
              ORDER BY provider, league_code, season_code""").fetchall()
            observations = connection.execute(
                "SELECT COUNT(*) FROM sports_archive_observations"
            ).fetchone()[0]
            imports = connection.execute("""SELECT COUNT(*) AS total,
                COALESCE(SUM(rejection_count), 0) AS rejections,
                COALESCE(SUM(blank_rows_skipped), 0) AS blanks
                FROM sports_archive_imports""").fetchone()
        for scope in scopes:
            provider = scope["provider"]
            by_provider[provider] = by_provider.get(provider, 0) + scope["unique_matches"]
        return {"total_unique_matches": sum(by_provider.values()), "total_observations": observations,
                "total_imports": imports["total"], "total_rejections": imports["rejections"],
                "blank_rows_skipped": imports["blanks"], "by_provider": dict(sorted(by_provider.items())),
                "by_league_season": [dict(scope) for scope in scopes]}
