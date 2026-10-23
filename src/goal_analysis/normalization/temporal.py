"""Pure, explicit time semantics for independently witnessed source records.

No clock is read here. A caller may supply an availability witness obtained
after reading a committed source row. Source publication, retrieval, original
ingestion and that conservative presence bound remain separate timestamps.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, time

NAMESPACES = ("result", "weather", "raw", "news")
TIME_FIELDS = ("event_time", "published_at", "observed_at", "ingested_at", "available_at")
WEATHER_KINDS = ("FORECAST", "OBSERVATION", "REANALYSIS")
WEATHER_BASES = ("FETCHED_AT", "REPORT_AVAILABLE_AT_UPPER_BOUND", "LOOKUP_ATTEMPT_AT", "UNKNOWN")


def _timestamp(value, field, *, optional=False):
    if value is None and optional:
        return None
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError
        return stamp.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"INVALID_TIMESTAMP:{field}") from None


def _object(value, field):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = item
        return result

    try:
        if isinstance(value, Mapping):
            value = json.dumps(dict(value), allow_nan=False)
        if not isinstance(value, str):
            raise TypeError
        payload = json.loads(value, object_pairs_hook=pairs)
        if not isinstance(payload, dict):
            raise TypeError
        # JSON exponents may overflow to inf even without a NaN/Infinity token.
        json.dumps(payload, allow_nan=False)
        return payload
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError(f"INVALID_JSON_OBJECT:{field}") from None


def _same_time(payload, key, expected):
    if key in payload and _timestamp(payload[key], key, optional=True) != expected:
        raise ValueError(f"CONFLICTING_SOURCE_TIME:{key}")


def _date(value):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise ValueError("INVALID_MATCH_DATE") from None


def _local_time(value):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}:[0-9]{2}(?::[0-9]{2})?", value):
            raise ValueError
        if time.fromisoformat(value).tzinfo is not None:
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise ValueError("INVALID_UNKNOWN_ZONE_LOCAL_TIME") from None


def _result(source, observed):
    record = _object(source.get("record_json"), "record_json")
    _same_time(record, "observed_at", observed)
    event = _timestamp(record.get("kickoff"), "kickoff", optional=True)
    day = _date(record.get("match_date"))
    local = _local_time(record.get("kickoff_local_time"))
    if event is not None:
        if record.get("provider") == "football_data_co_uk":
            raise ValueError("UNPROVEN_FOOTBALL_DATA_UTC_KICKOFF")
        if event >= observed:
            raise ValueError("RESULT_KICKOFF_NOT_BEFORE_RECEIPT")
        if (day is not None and event.date().isoformat() != day) or local is not None:
            raise ValueError("CONFLICTING_RESULT_TIME_PRECISION")
    elif day is not None and date.fromisoformat(day) > observed.date():
        raise ValueError("FUTURE_FINISHED_RESULT_DATE")
    return event, None, "KICKOFF" if event is not None else "DATE_ONLY" if day else "UNKNOWN", {
        "match_date": day, "kickoff_local_time": local,
    }


def _weather(source, observed):
    snapshot = _object(source.get("snapshot_json"), "snapshot_json")
    kind = snapshot.get("weather_kind", source.get("weather_kind"))
    if kind not in WEATHER_KINDS:
        raise ValueError("INVALID_WEATHER_KIND")
    if source.get("weather_kind") is not None and source["weather_kind"] != kind:
        raise ValueError("CONFLICTING_WEATHER_KIND")
    event = _timestamp(source.get("target_time"), "target_time", optional=True)
    published = _timestamp(source.get("provider_issue_time"), "provider_issue_time", optional=True)
    kickoff = _timestamp(source.get("kickoff"), "kickoff")
    for key, expected in (("observed_at", observed), ("target_time", event),
                          ("provider_issue_time", published), ("kickoff", kickoff)):
        _same_time(snapshot, key, expected)
    if kind in ("OBSERVATION", "REANALYSIS") and event is not None and event > observed:
        raise ValueError("WEATHER_TARGET_AFTER_RECEIPT")
    basis = snapshot.get("time_basis")
    basis = "UNKNOWN" if basis is None else basis
    if basis not in WEATHER_BASES:
        raise ValueError("INVALID_OBSERVED_TIME_BASIS")
    return event, published, kind + "_TARGET", basis


def project_times(namespace, source, *, available_at=None):
    """Return detached UTC metadata without changing source or legacy hashes.

    Only an outer source ``ingested_at`` is original ingestion evidence. Its
    basis is RECORDED_LOCAL_INGESTION_WALL, not a measured commit-visible time.
    It is never filled from a receipt, source JSON, publication or a new witness.
    ``available_at`` is accepted only as an explicit caller-verified source
    presence upper bound; omitting it keeps availability unproven.
    """
    if not isinstance(namespace, str) or namespace not in NAMESPACES:
        raise ValueError("INVALID_TEMPORAL_NAMESPACE")
    if not isinstance(source, Mapping):
        raise ValueError("SOURCE_MAPPING_REQUIRED")  # noqa: TRY004 - uniform contract rejection
    observed = _timestamp(source.get("fetched_at" if namespace == "raw" else "observed_at"), "observed_at")
    ingested = _timestamp(source.get("ingested_at"), "ingested_at", optional=True)
    available = _timestamp(available_at, "available_at", optional=True)
    extra = {}
    if namespace == "result":
        event, published, kind, extra = _result(source, observed)
        basis = "SOURCE_RECEIPT"
    elif namespace == "weather":
        event, published, kind, basis = _weather(source, observed)
    elif namespace == "news":
        event = _timestamp(source.get("event_at"), "event_at", optional=True)
        published = _timestamp(source.get("published_at"), "published_at")
        fetched = _timestamp(source.get("source_fetched_at"), "source_fetched_at")
        if published > fetched or fetched > observed:
            raise ValueError("INVALID_NEWS_PUBLICATION_FETCH_RECEIPT_ORDER")
        extra["source_fetched_at"] = fetched.isoformat(timespec="microseconds")
        kind, basis = "SOURCE_EVENT" if event is not None else "UNKNOWN", "RESULT_RECEIPT"
    else:
        event, published, kind, basis = None, None, "UNKNOWN", "FETCHED_AT"
    if published is not None and published > observed:
        raise ValueError("PUBLICATION_AFTER_RECEIPT")
    if ingested is not None and ingested < observed:
        raise ValueError("INGESTION_BEFORE_RECEIPT")
    if available is not None and (available < observed or ingested is not None and available < ingested):
        raise ValueError("AVAILABILITY_BEFORE_KNOWN_SOURCE_TIMES")
    times = dict(zip(TIME_FIELDS, (event, published, observed, ingested, available), strict=True))
    return {
        **{key: value.isoformat(timespec="microseconds") if value is not None else None
           for key, value in times.items()},
        "event_time_kind": kind, "observed_time_basis": basis,
        "ingested_time_basis": "RECORDED_LOCAL_INGESTION_WALL" if ingested is not None else "UNKNOWN",
        "availability_basis": "SOURCE_PRESENCE_UPPER_BOUND" if available is not None else "UNPROVEN",
        "missing_times": [key for key in TIME_FIELDS if times[key] is None], **extra,
    }
