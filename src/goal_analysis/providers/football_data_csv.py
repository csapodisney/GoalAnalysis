"""Lossless field parsing for Football-Data.co.uk's European season CSV files.

Schema reference: https://football-data.co.uk/notes.txt. This module does no I/O.
Provider IDs remain separate from API-Football and other fixture identifiers.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import Counter
from datetime import date

PROVIDER = "football_data_co_uk"
_REQUIRED = {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}
_BOOKMAKERS = (
    "1XB", "B365", "BF", "BFD", "BMGM", "BV", "BS", "BW", "CL", "GB", "IW",
    "LB", "PP", "PS", "P", "SK", "SO", "SB", "SJ", "SY", "VC", "WH", "BFE",
    "Max", "Avg", "BbMx", "BbAv",
)
_ODDS_COLUMN = re.compile(
    rf"(?:{'|'.join(_BOOKMAKERS)})(?P<closing>C)?(?:[HDA]|[<>]2\.5|AH[HA]?)\Z"
)


def _snapshot_type(column: str) -> str | None:
    match = _ODDS_COLUMN.fullmatch(column)
    if match:
        return "closing" if match["closing"] else "pre_closing"
    if column in {"AHh", "BbAHh", "Bb1X2", "BbOU", "BbAH"}:
        return "pre_closing"
    if column == "AHCh":
        return "closing"
    return None


def _score(value: str, code: str) -> int:
    if not re.fullmatch(r"[0-9]{1,3}", value):
        raise ValueError(code)
    return int(value)


def _result(home: int, away: int) -> str:
    return "H" if home > away else "A" if away > home else "D"


def _match_date(value: str) -> date:
    match = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{2}|[0-9]{4})", value)
    if not match:
        raise ValueError("invalid_date")
    day, month, year = (int(part) for part in match.groups())
    if len(match[3]) == 2:
        # The archive starts in 1993; do not depend on strptime's sliding pivot.
        year += 1900 if year >= 93 else 2000
    try:
        return date(year, month, day)
    except ValueError:
        raise ValueError("invalid_date") from None


def _record(raw: dict[str, str], league: str, season: str, as_of: date) -> dict:
    value = {key.strip(): item.strip() for key, item in raw.items()}
    if value["Div"] != league:
        raise ValueError("league_mismatch")
    match_day = _match_date(value["Date"])
    if match_day > as_of:
        raise ValueError("future_result_date")
    home, away = value["HomeTeam"], value["AwayTeam"]
    if not home or not away or home == away or any(c in home + away for c in "\r\n\x00"):
        raise ValueError("invalid_teams")
    full_home = _score(value["FTHG"], "invalid_full_time_score")
    full_away = _score(value["FTAG"], "invalid_full_time_score")
    full_result = value["FTR"]
    if full_result not in {"H", "D", "A"}:
        raise ValueError("invalid_full_time_result")
    if full_result != _result(full_home, full_away):
        raise ValueError("inconsistent_full_time_result")

    half_home = half_away = half_result = None
    for column, code in (("HTHG", "invalid_half_time_score"), ("HTAG", "invalid_half_time_score")):
        if value.get(column):
            score = _score(value[column], code)
            if column == "HTHG":
                half_home = score
            else:
                half_away = score
    if value.get("HTR"):
        half_result = value["HTR"]
        if half_result not in {"H", "D", "A"}:
            raise ValueError("invalid_half_time_result")
    if half_home is not None and half_home > full_home:
        raise ValueError("half_time_exceeds_full_time")
    if half_away is not None and half_away > full_away:
        raise ValueError("half_time_exceeds_full_time")
    if (half_home is not None and half_away is not None and half_result is not None
            and half_result != _result(half_home, half_away)):
        raise ValueError("inconsistent_half_time_result")

    local_time = value.get("Time") or None
    if local_time is not None:
        match = re.fullmatch(r"([0-9]{1,2}):([0-9]{2})", local_time)
        if not match or int(match[1]) > 23 or int(match[2]) > 59:
            raise ValueError("invalid_local_time")
        local_time = f"{int(match[1]):02d}:{match[2]}"
    identity = [PROVIDER, league, season, match_day.isoformat(), home, away]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()
    return {
        "source_record_key": f"{PROVIDER}:{digest}",
        "provider": PROVIDER,
        "league_code": league,
        "season_code": season,
        "match_date": match_day.isoformat(),
        "home_team": home,
        "away_team": away,
        "full_time_home": full_home,
        "full_time_away": full_away,
        "full_time_result": full_result,
        "half_time_home": half_home,
        "half_time_away": half_away,
        "half_time_result": half_result,
        "kickoff_local_time": local_time,
        "raw": raw,
        "odds_snapshot_types": {
            column: kind for column in raw if (kind := _snapshot_type(column.strip()))
        },
    }


def parse_csv(content: bytes, league_code: str, season_code: str, as_of: date) -> dict:
    """Return accepted normalized records and row rejection metadata.

    ``records`` contain a provider-namespaced stable ``source_record_key`` based
    on league/season/date/teams, ISO ``match_date``, team names, integer full/half
    time scores, H/D/A results, and optional ``kickoff_local_time`` (HH:MM with
    unknown timezone). Missing optional values are None, never inferred.
    ``raw`` preserves every named original column and decoded string value,
    including blank fields and unknown odds/statistics. ``odds_snapshot_types`` classifies
    recognized columns as pre_closing/closing; these are NOT observation times
    or a promise that pre_closing means market opening.

    Top-level ``columns`` preserves named header order and spelling. Trailing
    unnamed columns are omitted only when their cells are exactly empty, with their
    count in ``ignored_empty_trailing_columns`` and the unchanged header in
    ``original_columns``. Internal blank headers fail the entire file. Rows with
    nonempty unnamed cells are quarantined with reason nonempty_unnamed_column,
    including otherwise rejected or blank rows; no meaning is assigned to them.
    ``rejections``
    contains source physical row numbers and fixed reason codes; malformed file
    syntax, HTML, ambiguous headers and missing required headers raise ValueError.
    Original bytes should be archived by the caller for provenance and rejected
    row recovery. Duplicate identities are retained for caller conflict handling.
    """
    if not isinstance(content, bytes):
        raise TypeError("content_must_be_bytes")
    if type(as_of) is not date:
        raise TypeError("as_of_must_be_date")
    if not isinstance(league_code, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{0,7}", league_code):
        raise ValueError("invalid_league_code")
    if not isinstance(season_code, str) or not re.fullmatch(r"[0-9]{4}", season_code):
        raise ValueError("invalid_season_code")
    if (int(season_code[:2]) + 1) % 100 != int(season_code[2:]):
        raise ValueError("invalid_season_code")
    if b"\x00" in content:
        raise ValueError("invalid_csv_content")
    encoding = "utf-8-sig" if content.startswith(b"\xef\xbb\xbf") else "utf-8"
    try:
        text = content.decode(encoding)
    except UnicodeDecodeError:
        encoding = "cp1252"
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            raise ValueError("invalid_csv_encoding") from None
    if re.match(r"\s*(?:<!doctype\b|<html\b|<head\b|<body\b)", text, re.IGNORECASE):
        raise ValueError("html_instead_of_csv")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    columns = None
    original_columns = None
    trailing_empty_columns = 0
    records, rejections = [], []
    blank_rows = 0
    try:
        for row in reader:
            if (columns is not None and trailing_empty_columns
                    and any(cell != "" for cell in row[len(columns):])):
                rejections.append({"row_number": reader.line_num,
                                   "reason_code": "nonempty_unnamed_column"})
                continue
            if not row or all(not item.strip() for item in row):
                blank_rows += 1
                continue
            if columns is None:
                original_columns = row
                normalized = [column.strip() for column in row]
                while normalized and not normalized[-1]:
                    normalized.pop()
                    trailing_empty_columns += 1
                columns = row[:len(normalized)]
                if any(not column for column in normalized):
                    raise ValueError("blank_csv_header")
                if len(set(normalized)) != len(normalized):
                    raise ValueError("duplicate_csv_headers")
                if not _REQUIRED.issubset(normalized):
                    raise ValueError("missing_required_headers")
                continue
            if not len(columns) <= len(row) <= len(original_columns):
                rejections.append({"row_number": reader.line_num, "reason_code": "column_count_mismatch"})
                continue
            row = row[:len(columns)]
            try:
                record = _record(dict(zip(columns, row, strict=True)), league_code, season_code, as_of)
            except ValueError as exc:
                rejections.append({"row_number": reader.line_num, "reason_code": str(exc)})
            else:
                records.append(record)
    except csv.Error:
        raise ValueError("malformed_csv") from None
    if columns is None:
        raise ValueError("missing_csv_header")
    return {
        "provider": PROVIDER,
        "league_code": league_code,
        "season_code": season_code,
        "columns": columns,
        "original_columns": original_columns,
        "ignored_empty_trailing_columns": trailing_empty_columns,
        "records": records,
        "rejections": rejections,
        "rejection_counts": dict(Counter(item["reason_code"] for item in rejections)),
        "blank_rows_skipped": blank_rows,
        "encoding": encoding,
    }
