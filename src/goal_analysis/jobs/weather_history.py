"""Small explicit historical-weather batches from already verified forecast locations."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from goal_analysis.providers.open_meteo_history import MODEL, OpenMeteoHistory
from goal_analysis.storage.db import Database
from goal_analysis.storage.sports_weather import WeatherStore

MAX_RECORDS = 10_000


def _candidates(database, now):
    """Latest known forecast per fixture, then eligible dates lacking matching ERA5.

    Select latest before eligibility filtering: an older schedule/location must
    not replace a newer forecast merely because that newer kickoff is ineligible.
    SQL existence checks avoid loading an unbounded reanalysis history into RAM.
    """
    cutoff = datetime.combine(now.date() - timedelta(days=6), time.max, UTC)
    query = """
        WITH latest AS (
            SELECT *,ROW_NUMBER() OVER (
                PARTITION BY fixture_id ORDER BY observed_at DESC,snapshot_id DESC
            ) AS rank
            FROM sports_weather_snapshots
            WHERE weather_kind='FORECAST' AND status='SOURCED' AND observed_at<=?
        )
        SELECT f.snapshot_id,f.snapshot_json FROM latest f
        WHERE f.rank=1 AND f.target_time<=? AND NOT EXISTS (
            SELECT 1 FROM sports_weather_snapshots r
            WHERE r.fixture_id=f.fixture_id AND r.weather_kind='REANALYSIS'
              AND r.status='SOURCED' AND r.observed_at<=?
              AND r.target_time=f.target_time AND r.kickoff=f.kickoff
              AND json_extract(r.snapshot_json,'$.weather.model')=?
              AND json_extract(r.snapshot_json,'$.weather.latitude')=
                  json_extract(f.snapshot_json,'$.weather.latitude')
              AND json_extract(r.snapshot_json,'$.weather.longitude')=
                  json_extract(f.snapshot_json,'$.weather.longitude')
        ) ORDER BY f.kickoff DESC,f.observed_at DESC,f.snapshot_id LIMIT ?
    """
    stamp = now.isoformat(timespec="microseconds")
    with database.connect() as connection:
        rows = connection.execute(query, (stamp, cutoff.isoformat(timespec="microseconds"),
                                           stamp, MODEL, MAX_RECORDS)).fetchall()
    return [json.loads(row["snapshot_json"]) | {"snapshot_id": row["snapshot_id"]}
            for row in rows]


def _coordinates(weather):
    values = weather.get("latitude"), weather.get("longitude")
    if any(type(value) not in (int, float) or not math.isfinite(value) or abs(value) > bound
           for value, bound in zip(values, (90, 180))):
        return None
    return values


def backfill_weather(root, max_requests=5, clock=None):
    """Run one bounded batch; do not schedule work or fetch missing locations.

    Already downloaded forecast coordinates provide provenance. City coordinates
    remain labelled as city precision, never upgraded to exact stadium position.
    A cache hit consumes no request budget. HTTP failures stop the batch, while
    budget-deferred fixtures are available for the next explicit run.
    """
    if type(max_requests) is not int or not 0 <= max_requests <= 100:
        raise ValueError("max_requests must be an integer between 0 and 100")
    clock = clock or (lambda: datetime.now(UTC))
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    now = now.astimezone(UTC)
    root = Path(root)
    database = Database(root / "data" / "goal_analysis.sqlite3")
    database.initialize()
    store = WeatherStore(database)
    store.initialize()
    candidates = _candidates(database, now)
    run_id = f"weather-history:{now.isoformat()}"
    current_source = None

    def record(fixture, weather, context):
        # Budget exhaustion describes local scheduling, not a weather observation.
        if weather.get("reason_code") == "REQUEST_BUDGET_EXHAUSTED":
            return
        store.ingest(weather, fixture, source_run_id=run_id,
                     raw_payload=context["raw_payload"], metadata={
                         "coordinate_source_snapshot_id": current_source["snapshot_id"],
                         "coordinate_source_url": current_source.get("source_url"),
                         "coordinate_source_observed_at": current_source["observed_at"],
                         "historical_use": "RETROSPECTIVE_REANALYSIS_NOT_PREMATCH_KNOWLEDGE",
                     })

    provider = OpenMeteoHistory.from_root(root, recorder=record, clock=clock,
                                         max_requests=max_requests)
    result = {"observed_at": now.isoformat(), "source_run_id": run_id,
              "model": MODEL, "max_requests": max_requests, "record_window": MAX_RECORDS,
              "records_examined": len(candidates), "requests": 0, "sourced": 0,
              "unknown": 0, "deferred": 0, "items": []}
    for index, source in enumerate(candidates):
        current_source = source
        fixture, weather = source["fixture"], source["weather"]
        item = {"fixture_id": source["fixture_id"],
                "coordinate_source_snapshot_id": source["snapshot_id"]}
        coordinates = _coordinates(weather)
        if coordinates is None:
            result["deferred"] += 1
            result["items"].append(item | {"status": "DEFERRED", "reason_code": "MISSING_COORDINATES"})
            continue
        precision = weather.get("coordinate_precision") or (
            "VENUE_CITY" if source["provider"] == "open_meteo" else "PROVIDER_LOCATION"
        )
        reading = provider.historical(
            fixture, latitude=coordinates[0], longitude=coordinates[1],
            coordinate_source=f"stored_forecast:{source['snapshot_id']}",
            coordinate_precision=precision,
        )
        reason = reading.get("reason_code")
        if reason == "REQUEST_BUDGET_EXHAUSTED":
            result["deferred"] += 1
            status = "DEFERRED"
        else:
            status = reading["status"]
            result["sourced" if status == "SOURCED" else "unknown"] += 1
        result["items"].append(item | {"status": status, "reason_code": reason,
                                      "raw_payload_hash": reading.get("raw_payload_hash")})
        if provider.stopped_reason:
            remaining = candidates[index + 1:]
            result["deferred"] += len(remaining)
            result["items"].extend({"fixture_id": pending["fixture_id"], "status": "DEFERRED",
                                    "reason_code": "PROVIDER_STOPPED_AFTER_ERROR"}
                                   for pending in remaining)
            break
    result["requests"] = provider.requests_used
    result["stopped_reason"] = provider.stopped_reason
    result["status"] = "PARTIAL" if result["unknown"] or provider.stopped_reason else (
        "DEFERRED" if result["deferred"] else "COMPLETE")
    destination = root / "data" / "arthur" / "weather-history-import.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
