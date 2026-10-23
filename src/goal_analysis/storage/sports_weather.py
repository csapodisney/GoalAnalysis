"""Immutable, time-aware fixture weather history, separate from betting ledgers.

Forecast target time, provider issue time and actual retrieval time have different
meanings. Missing issue times stay unknown. Importing old reports never assigns
the import time to their weather. Raw JSON hashes use canonical JSON encoding.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime

from .db import Database

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS sports_weather_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        fixture_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        weather_kind TEXT NOT NULL CHECK(weather_kind IN ('FORECAST','OBSERVATION','REANALYSIS')),
        status TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        target_time TEXT,
        provider_issue_time TEXT,
        kickoff TEXT NOT NULL,
        source_url TEXT,
        raw_payload_hash TEXT,
        snapshot_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sports_weather_source_runs (
        snapshot_id TEXT NOT NULL REFERENCES sports_weather_snapshots(snapshot_id),
        source_run_id TEXT NOT NULL,
        PRIMARY KEY(snapshot_id, source_run_id)
    )""",
    """CREATE INDEX IF NOT EXISTS sports_weather_fixture_time
        ON sports_weather_snapshots(fixture_id, weather_kind, observed_at)""",
    """CREATE INDEX IF NOT EXISTS sports_weather_observed_time
        ON sports_weather_snapshots(weather_kind, observed_at)""",
)
_FIXTURE_FIELDS = (
    "fixture_id", "kickoff", "venue_name", "venue_city", "venue_country", "venue_id",
    "home_team", "away_team", "home_team_id", "away_team_id", "league_id", "league_name",
)
_KINDS = {"FORECAST", "OBSERVATION", "REANALYSIS"}
_TIME_BASES = {"FETCHED_AT", "REPORT_AVAILABLE_AT_UPPER_BOUND", "LOOKUP_ATTEMPT_AT"}


def _json(value) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ValueError("weather data must be finite JSON-compatible values") from error


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _stamp(value, name):
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("not aware")
        return stamp.astimezone(UTC).isoformat(timespec="microseconds")
    except (ValueError, TypeError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


class WeatherStore:
    def __init__(self, database: Database):
        self.database = database

    def initialize(self):
        """Create only weather tables, indexes and append-only protections."""
        with self.database.transaction() as connection:
            for statement in _SCHEMA:
                connection.execute(statement)
            for table in ("sports_weather_snapshots", "sports_weather_source_runs"):
                for action in ("UPDATE", "DELETE"):
                    connection.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} "
                        f"BEFORE {action} ON {table} BEGIN "
                        "SELECT RAISE(ABORT, 'weather history is immutable'); END"
                    )

    def ingest(self, weather: dict, fixture: dict, *, observed_at=None,
               source_run_id: str, raw_payload=None, time_basis=None, metadata=None) -> dict:
        """Append one weather version and its run link; exact repeats are idempotent.

        ``observed_at`` may supply a verified original retrieval timestamp for
        legacy reports; it must agree with weather.observed_at when both exist.
        ``raw_payload`` is the complete decoded provider response, if available.
        Forecasts, observations and model-based REANALYSIS require an explicit
        weather_kind and remain distinct even if their values are identical.
        Use time_basis='REPORT_AVAILABLE_AT_UPPER_BOUND' for a conservative
        report availability bound when the actual download timestamp is unknown.
        Optional metadata records the evidence for such backfills.
        """
        if not isinstance(weather, dict) or not isinstance(fixture, dict):
            raise ValueError("weather and fixture must be mappings")  # noqa: TRY004
        weather = json.loads(_json(weather))
        fixture = json.loads(_json({key: fixture[key] for key in _FIXTURE_FIELDS if key in fixture}))
        fixture_id = _text(fixture.get("fixture_id"), "fixture_id")
        if weather.get("fixture_id") != fixture_id:
            raise ValueError("weather fixture_id does not match fixture")
        provider = _text(weather.get("provider"), "provider")
        kind = weather.get("weather_kind")
        if kind not in _KINDS:
            raise ValueError("weather_kind must explicitly be FORECAST, OBSERVATION or REANALYSIS")
        status = weather.get("status")
        if status not in {"SOURCED", "UNKNOWN"}:
            raise ValueError("weather status must be SOURCED or UNKNOWN")
        original_observed = weather.get("observed_at")
        observed = _stamp(original_observed if observed_at is None else observed_at, "observed_at")
        if original_observed is not None and _stamp(original_observed, "observed_at") != observed:
            raise ValueError("observed_at disagrees with original weather retrieval time")
        original_basis = weather.get("time_basis")
        basis = time_basis if time_basis is not None else original_basis or "FETCHED_AT"
        if basis not in _TIME_BASES:
            raise ValueError("unknown weather time_basis")
        if original_basis is not None and original_basis != basis:
            raise ValueError("time_basis disagrees with original weather provenance")
        if basis == "LOOKUP_ATTEMPT_AT" and status != "UNKNOWN":
            raise ValueError("LOOKUP_ATTEMPT_AT is only valid for UNKNOWN weather")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a mapping")
        metadata = json.loads(_json(metadata or {}))
        kickoff = _stamp(fixture.get("kickoff"), "kickoff")
        if weather.get("kickoff") is not None and _stamp(weather["kickoff"], "kickoff") != kickoff:
            raise ValueError("weather kickoff does not match fixture")
        target_value = weather.get("forecast_time")
        target = _stamp(target_value, "forecast_time") if target_value is not None else None
        if status == "SOURCED" and target is None:
            raise ValueError("sourced weather requires forecast_time")
        if kind in {"OBSERVATION", "REANALYSIS"} and target is not None and target > observed:
            raise ValueError("observation target or reanalysis target cannot follow retrieval time")
        issue_value = weather.get("provider_issue_time")
        issued = _stamp(issue_value, "provider_issue_time") if issue_value is not None else None
        if issued is not None and issued > observed:
            raise ValueError("provider issue time cannot follow retrieval time")
        for name, bound in (("latitude", 90), ("longitude", 180)):
            value = weather.get(name)
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(value) or abs(value) > bound
            ):
                raise ValueError(f"invalid {name}")
        raw_hash = weather.get("raw_payload_hash")
        if raw_hash is not None and (
            not isinstance(raw_hash, str) or len(raw_hash) != 64
            or any(c not in "0123456789abcdef" for c in raw_hash)
        ):
            raise ValueError("invalid raw_payload_hash")
        if raw_payload is not None:
            encoded = _json(raw_payload)
            calculated = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            if raw_hash is not None and raw_hash != calculated:
                raise ValueError("raw payload hash mismatch")
            raw_hash = calculated
            raw_payload = json.loads(encoded)
        source_url = weather.get("source_url")
        if source_url is not None:
            _text(source_url, "source_url")
        _text(source_run_id, "source_run_id")
        snapshot = {
            "fixture_id": fixture_id, "provider": provider, "weather_kind": kind,
            "status": status, "observed_at": observed, "target_time": target,
            "provider_issue_time": issued, "kickoff": kickoff, "source_url": source_url,
            "raw_payload_hash": raw_hash, "raw_payload": raw_payload,
            "weather": weather, "fixture": fixture, "time_basis": basis, "metadata": metadata,
        }
        encoded = _json(snapshot)
        identity = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self.database.transaction() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO sports_weather_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (identity, fixture_id, provider, kind, status, observed, target, issued, kickoff,
                 source_url, raw_hash, encoded),
            ).rowcount
            connection.execute(
                "INSERT OR IGNORE INTO sports_weather_source_runs VALUES (?,?)",
                (identity, source_run_id),
            )
        return {"snapshot_id": identity, "inserted": bool(inserted), "observed_at": observed,
                "weather_kind": kind, "raw_payload_hash": raw_hash, "time_basis": basis}

    def as_of(self, cutoff, *, fixture_id=None, weather_kind="FORECAST", limit=1000) -> list[dict]:
        """Return versions known by cutoff, newest retrieval first, one kind only.

        A forecast target may follow cutoff; its retrieval and known issue time
        may not. Observations never substitute for forecasts. No future rows or
        later source-run associations are used to relabel historical values.
        """
        cutoff = _stamp(cutoff, "cutoff")
        if weather_kind not in _KINDS:
            raise ValueError("weather_kind must be FORECAST, OBSERVATION or REANALYSIS")
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000")
        query = (
            "SELECT snapshot_id,snapshot_json FROM sports_weather_snapshots "
            "WHERE observed_at<=? AND weather_kind=? "
            "AND (provider_issue_time IS NULL OR provider_issue_time<=?)"
        )
        params = [cutoff, weather_kind, cutoff]
        if fixture_id is not None:
            query += " AND fixture_id=?"
            params.append(_text(fixture_id, "fixture_id"))
        query += " ORDER BY observed_at DESC,snapshot_id LIMIT ?"
        params.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [json.loads(row["snapshot_json"]) | {"snapshot_id": row["snapshot_id"]} for row in rows]

    def summary(self) -> dict:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS snapshot_count,COUNT(DISTINCT fixture_id) AS fixture_count,"
                "MIN(observed_at) AS first_observed_at,MAX(observed_at) AS last_observed_at,"
                "SUM(CASE WHEN raw_payload_hash IS NOT NULL THEN 1 ELSE 0 END) AS payload_hash_count "
                "FROM sports_weather_snapshots"
            ).fetchone()
            grouped = connection.execute(
                "SELECT weather_kind,status,COUNT(*) AS count FROM sports_weather_snapshots "
                "GROUP BY weather_kind,status ORDER BY weather_kind,status"
            ).fetchall()
            links = connection.execute("SELECT COUNT(*) FROM sports_weather_source_runs").fetchone()[0]
        return dict(row) | {"groups": [dict(item) for item in grouped], "source_run_links": links}
