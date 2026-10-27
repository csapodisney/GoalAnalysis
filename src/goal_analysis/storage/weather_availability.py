"""Exact new NORMAL weather completions, never a source scan or backfill API.

Construction has no DB/schema I/O. A receipt proves source presence only;
native fixture, venue, review consumption and betting effect remain separate.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs, urlsplit

from goal_analysis.agents.weather_dataset import _weather_record
from goal_analysis.normalization.temporal import project_times

from .canonical_registry import digest, encoded
from .sports_weather import WeatherStore, _json
from .temporal import _source_hash

MAX_RAW_BYTES = 500_000
MAX_SNAPSHOT_BYTES = 1_000_000
MAX_FETCH_ORIGINS = 1000
SECONDS = 20
_COLUMNS = ("receipt_id", "namespace", "source_key", "source_sha256", "observed_at",
    "published_at", "ingested_at", "available_at", "times_json")
_FIELDS = (("temperature_c", "temperature_2m", "°C"),
    ("precipitation_probability_pct", "precipitation_probability", "%"),
    ("precipitation_mm", "precipitation", "mm"), ("weather_code", "weather_code", "wmo code"),
    ("wind_speed_kmh", "wind_speed_10m", "km/h"), ("wind_gusts_kmh", "wind_gusts_10m", "km/h"),
    ("relative_humidity_pct", "relative_humidity_2m", "%"))


class AvailabilityError(ValueError):
    """Fixed credential-safe reason code, never provider/SQL error text."""


def _check(value, code):
    if not value:
        raise AvailabilityError(code)


def _time(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    _check(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None,
        "AWARE_UTC_SOURCE_CLOCK_REQUIRED")
    return value.astimezone(UTC)


def _receipt_schema(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='temporal_receipts'").fetchone():
        return False
    rows = db.execute("PRAGMA table_info(temporal_receipts)").fetchall()
    _check(tuple(r["name"] for r in rows) == _COLUMNS
        and tuple(r["pk"] for r in rows) == (1, 0, 0, 0, 0, 0, 0, 0, 0)
        and all(r["type"].upper() == "TEXT" for r in rows), "EXISTING_RECEIPT_SCHEMA_INVALID")
    unique = []
    for index in db.execute("PRAGMA index_list(temporal_receipts)"):
        if index["unique"]:
            name = index["name"].replace('"', '""')
            unique.append(tuple(row["name"] for row in db.execute('PRAGMA index_info("' + name + '")')))
    _check(("namespace", "source_key", "source_sha256") in unique, "EXISTING_RECEIPT_UNIQUE_KEY_INVALID")
    triggers = {row["name"]: re.sub(r"\s+", "", row["sql"]).lower().rstrip(";").replace("createtriggerifnotexists", "createtrigger", 1) for row in db.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='temporal_receipts'")}
    for operation in ("update", "delete"):
        name = "temporal_receipts_no_" + operation
        expected = f"createtrigger{name}before{operation}ontemporal_receiptsbeginselectraise(abort,'temporalreceiptsareappend-only');end"
        _check(triggers.get(name) == expected, "EXISTING_RECEIPT_APPEND_ONLY_TRIGGER_INVALID")
    return True


def _prior(db, key, exists):
    if not exists:
        return None
    rows = db.execute("SELECT * FROM temporal_receipts WHERE namespace='weather' AND source_key=? LIMIT 2", (key,)).fetchall()
    _check(len(rows) <= 1, "CONFLICTING_EXISTING_WEATHER_RECEIPTS")
    return dict(rows[0]) if rows else None


class WeatherCompletion:
    """One serial factory instance's actual fetch origin and held RootRunLock."""

    def __init__(self, root, source_run_id, root_run_lock, *, clock):
        self.root = Path(root).resolve()
        self.path = self.root / "data/goal_analysis.sqlite3"
        self.run_id, self.lock, self.clock = source_run_id, root_run_lock, clock
        _check(isinstance(source_run_id, str) and 0 < len(source_run_id) <= 200, "EXACT_SOURCE_RUN_REQUIRED")
        self.check_held()
        self.started = self.last = _time(clock())
        self.origins = {}

    def check_held(self):
        from goal_analysis.jobs.portfolio import RunLock

        _check(isinstance(self.lock, RunLock) and self.lock.stream is not None and not self.lock.stream.closed
            and not self.lock.configuration_recovery
            and self.lock.path.resolve() == self.root / "data/arthur/run.lock", "EXISTING_HELD_SAME_ROOT_LOCK_REQUIRED")

    def _now(self):
        current = _time(self.clock())
        _check(current >= self.last, "WEATHER_WITNESS_CLOCK_MOVED_BACKWARDS")
        self.last = current
        return current

    def transport(self, underlying):
        def fetched(url):
            data = underlying(url)
            # A failure to attest a valid response cannot turn it into a network
            # failure or poison other fixture lookups. No second fetch occurs.
            try:
                parsed = urlsplit(url)
                if parsed.scheme == "https" and parsed.netloc == "api.open-meteo.com" and parsed.path == "/v1/forecast":
                    raw = _json(data).encode("utf-8")
                    _check(len(raw) <= MAX_RAW_BYTES and isinstance(data, dict), "FORECAST_ORIGIN_BYTE_BUDGET")
                    key = (url, sha256(raw).hexdigest())
                    _check(key in self.origins or len(self.origins) < MAX_FETCH_ORIGINS, "FORECAST_ORIGIN_COUNT_BUDGET")
                    self.origins.setdefault(key, {"finished_at": self._now(), "observed_at": None})
            except (ValueError, TypeError, OverflowError):
                pass
            return data
        return fetched

    def _read(self, key, deadline):
        _check(self.path.is_file(), "COMMITTED_WEATHER_STORE_REQUIRED")
        _check(monotonic() < deadline, "WEATHER_RECEIPT_TIME_BUDGET")
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=min(5, deadline-monotonic()))) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            db.set_progress_handler(lambda: int(monotonic() > deadline), 10000)
            size = db.execute("SELECT length(CAST(snapshot_json AS BLOB)) FROM sports_weather_snapshots WHERE snapshot_id=?", (key,)).fetchone()
            _check(size is not None and size[0] <= MAX_SNAPSHOT_BYTES, "EXACT_WEATHER_SNAPSHOT_BYTE_BUDGET")
            row = dict(db.execute("SELECT * FROM sports_weather_snapshots WHERE snapshot_id=?", (key,)).fetchone())
            _check(db.execute("SELECT 1 FROM sports_weather_source_runs WHERE snapshot_id=? AND source_run_id=?", (key, self.run_id)).fetchone(), "EXACT_COMMITTED_SOURCE_RUN_LINK_REQUIRED")
            exists = _receipt_schema(db)
            return row, _prior(db, key, exists), exists

    def _validate(self, row, fixture, now):
        _check(re.fullmatch(r"the_stats_api:mt_\d{1,30}", fixture.get("fixture_id", "")) is not None
            and row["fixture_id"] == fixture["fixture_id"] and fixture.get("source_provider") == "the_stats_api"
            and fixture.get("provider_fixture_id") == fixture["fixture_id"].split(":", 1)[1], "CURRENT_NATIVE_FIXTURE_REQUIRED")
        stored = json.loads(row["snapshot_json"])
        _check(_json(stored) == row["snapshot_json"] and sha256(row["snapshot_json"].encode()).hexdigest() == row["snapshot_id"], "CANONICAL_WEATHER_SNAPSHOT_ID_REQUIRED")
        _check(all(stored.get(key) == row[key] for key in row if key not in {"snapshot_id", "snapshot_json"}), "WEATHER_COLUMN_PAYLOAD_MISMATCH")
        _check(row["provider"] == "open_meteo" and row["weather_kind"] == "FORECAST"
            and row["status"] == "SOURCED" and stored["time_basis"] == "FETCHED_AT", "CURRENT_SOURCED_FORECAST_REQUIRED")
        _weather_record(row, fixture, now, "FORECAST")
        weather, raw = stored["weather"], stored["raw_payload"]
        _check(isinstance(raw, dict) and not raw.get("error"), "FULL_RAW_FORECAST_REQUIRED")
        _check(all(weather.get(name) is not None for name in ("temperature_c", "precipitation_probability_pct", "wind_speed_kmh")), "CURRENT_PROVIDER_REQUIRED_VALUES_MISSING")
        body = _json(raw).encode("utf-8")
        _check(len(body) <= MAX_RAW_BYTES and sha256(body).hexdigest() == row["raw_payload_hash"]
            == weather.get("raw_payload_hash"), "FULL_RAW_FORECAST_HASH_REQUIRED")
        kickoff, target = _time(fixture["kickoff"]), _time(row["target_time"])
        hour = kickoff.replace(minute=0, second=0, microsecond=0)
        if kickoff - hour >= timedelta(minutes=30):
            hour += timedelta(hours=1)
        _check(target == hour and now <= kickoff - timedelta(minutes=15), "EXACT_PREMATCH_UTC_TARGET_REQUIRED")
        _check(row["provider_issue_time"] is None and weather.get("provider_issue_time") is None, "UNKNOWN_PROVIDER_ISSUE_TIME_MUST_STAY_UNKNOWN")
        parsed = urlsplit(row["source_url"])
        query = parse_qs(parsed.query)
        _check(parsed.scheme == "https" and parsed.netloc == "api.open-meteo.com" and parsed.path == "/v1/forecast"
            and query.get("timezone") == ["UTC"] and raw.get("utc_offset_seconds") == 0, "EXACT_OPEN_METEO_UTC_SOURCE_REQUIRED")
        for key, bound in (("latitude", 90), ("longitude", 180)):
            _check(type(raw.get(key)) in (int, float) and math.isfinite(raw[key]) and abs(raw[key]) <= bound,
                "PROVIDER_GRID_COORDINATES_UNPROVEN")
        _check(all(len(query.get(key, [])) == 1 for key in ("latitude", "longitude", "hourly", "start_date", "end_date")), "UNAMBIGUOUS_FORECAST_REQUEST_REQUIRED")
        _check(query["start_date"] == query["end_date"] == [target.date().isoformat()]
            and len(query["hourly"][0].split(",")) == len(_FIELDS)
            and set(query["hourly"][0].split(",")) == {field for _name, field, _unit in _FIELDS}, "EXACT_REQUEST_DAY_AND_FIELDS_REQUIRED")
        _check(float(query["latitude"][0]) == weather["latitude"] and float(query["longitude"][0]) == weather["longitude"], "EXACT_REQUEST_COORDINATES_REQUIRED")
        times = raw["hourly"]["time"]
        text = target.strftime("%Y-%m-%dT%H:%M")
        _check(times.count(text) == 1, "ONE_EXACT_FORECAST_HOUR_REQUIRED")
        index = times.index(text)
        for name, field, unit in _FIELDS:
            if weather.get(name) is not None:
                _check(raw["hourly_units"].get(field) == unit and raw["hourly"][field][index] == weather[name], "FORECAST_VALUE_OR_UNIT_CONFLICT")
        location = (stored.get("metadata") or {}).get("stadium_location")
        if location is not None:
            payload = location["payload"]
            _check((payload["latitude"], payload["longitude"]) == (weather["latitude"], weather["longitude"]), "STORED_STADIUM_COORDINATE_CONFLICT")
        return stored

    def _existing(self, prior, row, now):
        source_sha = _source_hash(row)
        _check(prior["receipt_id"] == digest(["weather", row["snapshot_id"], source_sha])
            and prior["source_sha256"] == source_sha, "EXISTING_WEATHER_RECEIPT_SOURCE_CONFLICT")
        times = project_times("weather", row, available_at=prior["available_at"])
        _check(encoded(times) == prior["times_json"] and all(times[key] == prior[key]
            for key in ("observed_at", "published_at", "ingested_at", "available_at"))
            and _time(prior["available_at"]) <= now, "EXISTING_WEATHER_RECEIPT_TIME_CONFLICT")
        return {"status": "REUSED_EXISTING_SOURCE_RECEIPT", "receipt_id": prior["receipt_id"], "created": False,
            "available_at": prior["available_at"], "new_time_assigned": False}

    def record(self, archive, weather, fixture, context, *, metadata=None):
        self.check_held()
        _check(isinstance(archive, WeatherStore) and archive.database.path.resolve() == self.path, "EXACT_NORMAL_WEATHER_STORE_REQUIRED")
        saved = archive.ingest(weather, fixture, source_run_id=self.run_id,
            raw_payload=context.get("raw_payload"), metadata=metadata)
        if weather.get("status") != "SOURCED":
            return saved, {"status": "NOT_SOURCED_WEATHER", "created": False}
        attempted_commit = False
        db = None
        deadline = monotonic() + SECONDS
        try:
            row, prior, schema_exists = self._read(saved["snapshot_id"], deadline)
            now = self._now()
            self._validate(row, fixture, now)
            if prior is not None:
                return saved, self._existing(prior, row, now)
            _check(saved["inserted"] is True, "RETAINED_UNWITNESSED_ROW_NOT_ADMITTED")
            origin = self.origins.get((row["source_url"], row["raw_payload_hash"]))
            observed = _time(row["observed_at"])
            _check(origin is not None and self.started <= origin["finished_at"] <= observed <= now
                and _time(context["observed_at"]) == observed and context.get("source_url") == row["source_url"]
                and context.get("raw_payload_hash") == row["raw_payload_hash"], "OWN_CURRENT_FETCH_ORIGIN_REQUIRED")
            if origin["observed_at"] is None:
                origin["observed_at"] = observed
            _check(origin["observed_at"] == observed, "CACHED_ORIGINAL_FETCH_CLOCK_MISMATCH")
            source_sha = _source_hash(row)
            self.check_held()
            _check(monotonic() < deadline, "WEATHER_RECEIPT_TIME_BUDGET")
            db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=min(5, deadline-monotonic()))
            db.row_factory = sqlite3.Row
            db.set_progress_handler(lambda: int(monotonic() > deadline), 10000)
            db.execute("BEGIN IMMEDIATE")
            current = dict(db.execute("SELECT * FROM sports_weather_snapshots WHERE snapshot_id=?", (saved["snapshot_id"],)).fetchone())
            _check(_source_hash(current) == source_sha
                and db.execute("SELECT 1 FROM sports_weather_source_runs WHERE snapshot_id=? AND source_run_id=?", (saved["snapshot_id"], self.run_id)).fetchone(), "WEATHER_SOURCE_OR_RUN_CAS_MISMATCH")
            _check(_receipt_schema(db) == schema_exists and _prior(db, saved["snapshot_id"], schema_exists) is None, "WEATHER_PRIOR_RECEIPT_CAS_MISMATCH")
            available = self._now()
            self._validate(current, fixture, available)
            self.check_held()
            if not schema_exists:
                db.execute("""CREATE TABLE temporal_receipts (
                    receipt_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, source_key TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL, observed_at TEXT NOT NULL, published_at TEXT, ingested_at TEXT,
                    available_at TEXT NOT NULL, times_json TEXT NOT NULL, UNIQUE(namespace,source_key,source_sha256))""")
                db.execute("CREATE INDEX temporal_asof ON temporal_receipts(namespace,available_at,observed_at)")
                for operation in ("UPDATE", "DELETE"):
                    db.execute(f"CREATE TRIGGER temporal_receipts_no_{operation.lower()} BEFORE {operation} ON temporal_receipts BEGIN SELECT RAISE(ABORT,'temporal receipts are append-only'); END")
            times = project_times("weather", current, available_at=available)
            receipt_id = digest(["weather", saved["snapshot_id"], source_sha])
            _check(monotonic() < deadline, "WEATHER_RECEIPT_TIME_BUDGET")
            db.execute("INSERT INTO temporal_receipts VALUES (?,?,?,?,?,?,?,?,?)", (receipt_id, "weather", saved["snapshot_id"], source_sha,
                times["observed_at"], times["published_at"], times["ingested_at"], times["available_at"], encoded(times)))
            attempted_commit = True
            db.commit()
            _row, receipt, _exists = self._read(saved["snapshot_id"], deadline)
            result = self._existing(receipt, _row, self._now())
            result.update(status="NEW_COMMITTED_SOURCE_RECEIPT_READ_BACK", created=True,
                source_run_id=self.run_id, availability_basis="SOURCE_PRESENCE_UPPER_BOUND")
            return saved, result
        except (ValueError, TypeError, KeyError, OSError, OverflowError, sqlite3.Error) as error:
            if db is not None and not attempted_commit:
                try:
                    db.rollback()
                except sqlite3.Error:
                    attempted_commit = True
            return saved, {"status": "PARTIAL_RECEIPT_COMMIT_UNPROVEN_NO_RETRY" if attempted_commit else "NO_RECEIPT_ADMISSION_NO_RETRY",
                "created": None if attempted_commit else False, "source_snapshot_remains_committed": True,
                "reason": str(error) if isinstance(error, AvailabilityError) else "WEATHER_AVAILABILITY_UNPROVEN",
                "automatic_retry": False}
        finally:
            if db is not None:
                try:
                    db.close()
                except sqlite3.Error:
                    pass
