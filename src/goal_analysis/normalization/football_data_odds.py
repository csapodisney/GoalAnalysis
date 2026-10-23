"""Pure native-column Football-Data odds extraction; archival use only.

Historical CSVs supply decimal prices and a pre-closing/closing distinction,
but do not supply quote timestamps. Local match times are never converted to
UTC. Native bookmaker prefixes remain separate, including BF/BFD/BFE and P/PS.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit

from goal_analysis.normalization.canonical import utc
from goal_analysis.providers.football_data_csv import _BOOKMAKERS

PROVIDER = "football_data_co_uk"
AGGREGATES = {"Max": "MAXIMUM", "Avg": "AVERAGE", "BbMx": "MAXIMUM", "BbAv": "AVERAGE"}
_COLUMN = re.compile("(?P<book>" + "|".join(sorted(_BOOKMAKERS, key=len, reverse=True))
                     + r")(?P<closing>C)?(?P<selection>AHH|AHA|[HDA]|[<>]2\.5)\Z")


def describe_column(column):
    """Describe known price columns; handicap lines/count metadata return None."""
    if not isinstance(column, str) or (match := _COLUMN.fullmatch(column.strip())) is None:
        return None
    book, closing, suffix = match["book"], bool(match["closing"]), match["selection"]
    if suffix in {"H", "D", "A"}:
        market, selection, line = "h2h", {"H": "home", "D": "draw", "A": "away"}[suffix], None
    elif suffix in {">2.5", "<2.5"}:
        market, selection, line = "totals", "over" if suffix[0] == ">" else "under", "2.5"
    else:
        market, selection, line = "asian_handicap", "home" if suffix == "AHH" else "away", None
    line_field = ("BbAHh" if book.startswith("Bb") else "AHCh" if closing else "AHh") if market == "asian_handicap" else None
    return {"bookmaker": book, "market_key": market, "selection_key": selection,
        "line": line, "line_field": line_field,
        "price_kind": "CLOSING" if closing else "PRE_CLOSING",
        "quote_type": "AGGREGATE" if book in AGGREGATES else "BOOKMAKER",
        "aggregate_kind": AGGREGATES.get(book)}


def _number(value, *, decimal_price=False):
    # Bound exponent expansion before formatting, without float conversion or
    # Decimal.normalize(), both of which can lose source precision.
    if (not isinstance(value, str) or not value.strip() or len(value) > 100
            or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?",
                            value.strip()) is None):
        return None
    try:
        number = Decimal(value.strip())
        if (not number.is_finite() or abs(number.as_tuple().exponent) > 100
                or decimal_price and number <= 1):
            return None
        result = format(number, "f")
        return result if len(result) <= 100 else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _fields(row):
    raw = row.get("raw")
    if raw is None:
        raw = json.loads(row["raw_json"])
    if not isinstance(raw, Mapping):
        raise ValueError("CSV_RAW_MAPPING_REQUIRED")  # noqa: TRY004 - uniform archive validation
    result = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip() or key.strip() in result:
            raise ValueError("AMBIGUOUS_CSV_COLUMN")
        result[key.strip()] = (key, value)
    return result


def iter_fd_quotes(row, *, observed_at, source_url, source_sha256):
    """Yield proven native prices from one archived row, without I/O.

    Unusable price cells and non-price metadata are skipped; the parent CSV/raw
    row remains the lossless record. Missing handicap lines stay explicit and
    never become zero. A handicap line is the source HOME-team handicap for
    both selections; no away-side sign or settlement convention is invented.
    The caller verifies the complete parent CSV evidence before ingestion.
    """
    if not isinstance(row, Mapping) or row.get("provider") != PROVIDER:
        raise ValueError("FOOTBALL_DATA_ARCHIVE_ROW_REQUIRED")
    fixture = row.get("source_record_key")
    if not isinstance(fixture, str) or re.fullmatch(PROVIDER + r":[0-9a-f]{64}", fixture) is None:
        raise ValueError("SOURCE_NATIVE_FIXTURE_KEY_REQUIRED")
    if not isinstance(source_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None:
        raise ValueError("SOURCE_SHA256_REQUIRED")
    if not isinstance(source_url, str):
        raise ValueError("PUBLIC_FOOTBALL_DATA_SOURCE_REQUIRED")  # noqa: TRY004 - uniform provenance rejection
    parsed = urlsplit(source_url)
    if (parsed.scheme != "https" or parsed.hostname not in {"football-data.co.uk", "www.football-data.co.uk"}
            or parsed.username or parsed.password or parsed.fragment
            or any(re.search(r"key|token|secret|password|authorization", key, re.IGNORECASE)
                   for key in parse_qs(parsed.query))):
        raise ValueError("PUBLIC_FOOTBALL_DATA_SOURCE_REQUIRED")
    observed = utc(observed_at).isoformat(timespec="microseconds")
    fields = _fields(row)
    for column, (original_column, value) in fields.items():
        description = describe_column(column)
        if description is None:
            continue
        decimal = _number(value, decimal_price=True)
        if decimal is None:
            continue
        line_field = description.pop("line_field")
        fragment = {original_column: value}
        line = description["line"]
        if line_field is not None and line_field in fields:
            original_line, raw_line = fields[line_field]
            fragment[original_line] = raw_line
            line = _number(raw_line)
        pointer_column = original_column.replace("~", "~0").replace("/", "~1")
        yield {"provider": PROVIDER, "fixture_id": fixture, **description,
            "competition_id": row.get("league_code"), "season_id": row.get("season_code"),
            "region": None, "currency": None,
            "period": "FULL_TIME", "settlement": "SOURCE_FULL_TIME_UNVERIFIED",
            "line": line, "line_reference": "HOME_TEAM_HANDICAP" if line_field else None,
            "line_status": "MISSING" if line_field and line is None else "KNOWN" if line is not None else "NOT_APPLICABLE",
            "decimal_price": decimal, "quoted_at": None, "quote_time_status": "MISSING",
            "observed_at": observed, "source_url": source_url, "source_sha256": source_sha256,
            "raw_path": f"{fixture}/raw/{pointer_column}", "raw": fragment}
