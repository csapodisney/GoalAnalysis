"""Provider quote timestamps are descriptive metadata, never price eligibility."""

from datetime import UTC, datetime


def quote_metadata(raw, now: datetime, max_age_seconds: int = 300) -> dict:
    """Keep the source value without manufacturing a fresh timestamp.

    Missing, malformed, old and future dates do not invalidate a real price.
    ``quoted_at`` is nullable; consumers use the status only for information.
    """
    stamp = None
    status = "MISSING" if raw is None or raw == "" else "INVALID"
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                stamp = parsed.astimezone(UTC)
                age = (now - stamp).total_seconds()
                status = "FUTURE" if age < 0 else "OLDER" if age > max_age_seconds else "CURRENT"
        except (ValueError, TypeError, OverflowError):
            pass
    return {
        "quoted_at": stamp.isoformat() if stamp is not None else None,
        "quote_timestamp_raw": raw,
        "quote_timestamp_status": status,
    }
