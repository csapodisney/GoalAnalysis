"""Small local monitor-attempt receipt; neither OS completion nor quote freshness."""

import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

VERSION = "odds-monitor-attempt-v1"
RELATIVE_PATH = "data/arthur/odds-monitor-last-attempt.json"
MAX_BYTES = 16 * 1024
STALE_AFTER = timedelta(minutes=5)  # Presentation only, never a business/quote gate.
SUCCESS = frozenset({"UPDATED", "NO_CHANGE", "NO_UNLOCKED_FIXTURES"})
STATUSES = SUCCESS | {"MONITOR_UNAVAILABLE"}
SOURCE_REASONS = frozenset({
    "PRIMARY_LEDGER_UNREADABLE", "PRIMARY_LEDGER_SCHEMA_INVALID",
    "PRIMARY_RUN_IDENTITY_MISMATCH", "PRIMARY_RUN_JSON_INVALID", "PRIMARY_RUN_HASH_MISMATCH",
    "PRIMARY_RUN_CLOCK_INVALID", "PRIMARY_RUN_SHAPE_INVALID", "PRIMARY_FIXTURE_IDENTITY_INVALID",
    "PRIMARY_FIXTURE_CLOCK_INVALID", "PRIMARY_FIXTURE_CONFLICT",
})
OTHER_REASONS = frozenset({"THE_STATS_CREDENTIAL_UNAVAILABLE", "UNSPECIFIED_MONITOR_FAILURE"})
COUNTS = frozenset({"rows_fetched", "rows_in_scope", "runs_selected",
                    "rows_outside_date_scope", "empty_report_fallbacks"})
QUERY_SCOPES = frozenset({"PARTIAL_FETCH_FAILURE", "FETCH_FAILURE_WITHOUT_UPDATED_OR_CACHED_QUERY",
                         "NO_FETCH_FAILURE_RECORDED"})
FIELDS = frozenset({"version", "attempt_id", "started_at_utc", "finished_at_utc", "status",
                    "classified_exit_code", "source_read_failure", "reason", "source_counts",
                    "adapter_dispatch_count", "query_failure_count", "updated_or_cached_query_count",
                    "query_failure_scope"})


def _utc(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("AWARE_MONITOR_ATTEMPT_CLOCK_REQUIRED")
    return value.astimezone(UTC)


def _count(value):
    return value if type(value) is int and 0 <= value <= 1_000_000 else None


def _path(root):
    root = Path(root).resolve()
    path = root / RELATIVE_PATH
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError("MONITOR_ATTEMPT_PATH_REDIRECTION")
    return path


def _validate(value):
    if (not isinstance(value, dict) or set(value) != FIELDS or value["version"] != VERSION
            or not isinstance(value["attempt_id"], str)
            or re.fullmatch(r"[a-f0-9]{32}", value["attempt_id"]) is None
            or value["status"] not in STATUSES
            or type(value["classified_exit_code"]) is not int
            or value["classified_exit_code"] != int(value["status"] not in SUCCESS)):
        raise ValueError("INVALID_MONITOR_ATTEMPT_SCHEMA")
    start, finish = _utc(value["started_at_utc"]), _utc(value["finished_at_utc"])
    if start > finish or any(_utc(value[key]).isoformat() != value[key]
                             for key in ("started_at_utc", "finished_at_utc")):
        raise ValueError("INVALID_MONITOR_ATTEMPT_CLOCK")
    failure, reason, counts = value["source_read_failure"], value["reason"], value["source_counts"]
    if failure is not None and type(failure) is not bool:
        raise ValueError("INVALID_SOURCE_FAILURE_FLAG")
    if not isinstance(counts, dict) or set(counts) - COUNTS:
        raise ValueError("INVALID_SOURCE_COUNTERS")
    if any(number is not None and _count(number) is None for number in counts.values()):
        raise ValueError("INVALID_SOURCE_COUNTERS")
    if value["status"] in SUCCESS:
        if failure is not False or reason is not None or counts:
            raise ValueError("SUCCESS_CANNOT_RETAIN_PREVIOUS_SOURCE_FAILURE")
    elif failure is True:
        if reason not in SOURCE_REASONS:
            raise ValueError("INVALID_SOURCE_REASON")
    elif reason not in OTHER_REASONS or counts:
        raise ValueError("INVALID_MONITOR_FAILURE_REASON")
    for key in ("adapter_dispatch_count", "query_failure_count", "updated_or_cached_query_count"):
        if value[key] is not None and _count(value[key]) is None:
            raise ValueError("INVALID_MONITOR_COUNTER")
    if value["query_failure_scope"] is not None and value["query_failure_scope"] not in QUERY_SCOPES:
        raise ValueError("INVALID_QUERY_FAILURE_SCOPE")
    return value


def record_monitor_attempt(root, result, *, started_at, finished_at, classified_exit_code,
                           attempt_id=None):
    """Replace one bounded sanitized receipt; errors belong to the optional caller boundary."""
    source = result if isinstance(result, dict) else {}
    status = source.get("status")
    status = status if isinstance(status, str) and status in STATUSES else "MONITOR_UNAVAILABLE"
    source_reason = source.get("reason")
    failure, reason, counts = None, "UNSPECIFIED_MONITOR_FAILURE", {}
    if status in SUCCESS:
        if "source_read_failure" in source and source["source_read_failure"] is not False:
            raise ValueError("INCONSISTENT_MONITOR_SUCCESS")
        failure, reason = False, None
    elif (source.get("source_read_failure") is True and isinstance(source_reason, str)
          and source_reason in SOURCE_REASONS):
        failure, reason = True, source_reason
        diagnostics = source.get("source_read")
        raw_counts = diagnostics.get("counts") if isinstance(diagnostics, dict) else None
        counts = {key: _count(raw_counts.get(key)) for key in COUNTS} if isinstance(raw_counts, dict) else {}
    elif source.get("reason") == "THE_STATS_CREDENTIAL_UNAVAILABLE":
        failure, reason = False, "THE_STATS_CREDENTIAL_UNAVAILABLE"
    scope = source.get("query_failure_scope")
    value = _validate({
        "version": VERSION, "attempt_id": uuid4().hex if attempt_id is None else attempt_id,
        "started_at_utc": _utc(started_at).isoformat(), "finished_at_utc": _utc(finished_at).isoformat(),
        "status": status, "classified_exit_code": classified_exit_code,
        "source_read_failure": failure, "reason": reason, "source_counts": counts,
        "adapter_dispatch_count": _count(source.get("provider_calls")),
        "query_failure_count": _count(source.get("query_failure_count")),
        "updated_or_cached_query_count": _count(source.get("updated_or_cached_query_count")),
        "query_failure_scope": scope if isinstance(scope, str) and scope in QUERY_SCOPES else None,
    })
    raw = (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("MONITOR_ATTEMPT_RECEIPT_TOO_LARGE")
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return value


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("DUPLICATE_MONITOR_ATTEMPT_FIELD")
        value[key] = item
    return value


def _invalid_constant(_value):
    raise ValueError("INVALID_MONITOR_ATTEMPT_NUMBER")


def monitor_attempt_status(root, now):
    """Read one file only; missing/unknown evidence never vetoes other queries or prices."""
    try:
        path = _path(root)
        if not path.exists():
            return {"status": "MISSING", "receipt": None}
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError("INVALID_MONITOR_ATTEMPT_FILE")
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("MONITOR_ATTEMPT_RECEIPT_TOO_LARGE")
        value = _validate(json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                                     parse_constant=_invalid_constant))
        age = _utc(now) - _utc(value["finished_at_utc"])
        if age < timedelta(0):
            raise ValueError("FUTURE_MONITOR_ATTEMPT")
        return {"status": "STALE" if age > STALE_AFTER else "AVAILABLE", "receipt": value}
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RuntimeError):
        return {"status": "UNAVAILABLE", "receipt": None}
