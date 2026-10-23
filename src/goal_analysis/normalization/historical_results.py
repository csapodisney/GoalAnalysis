"""Pure, source-native historical results for an additive shadow archive.

This module does not import collectors or infer cross-provider identities. CSV
wall-clock times have unknown timezones; result availability is actual receipt,
never the historical match date. Raw JSON is retained as a detached full copy.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from urllib.parse import urlsplit


def _mapping(value, name):
    if not isinstance(value, Mapping):
        raise ValueError(f"INVALID_{name}")  # noqa: TRY004 - uniform record rejection contract
    return value


def _text(value, name):
    if (not isinstance(value, str) or not value.strip()
            or any(char in value for char in "\x00\r\n")):
        raise ValueError(f"INVALID_{name}")
    return value


def _key(value, name):
    text = _text(value, name)
    if text != text.strip():
        raise ValueError(f"INVALID_{name}")
    return text


def _stamp(value, name):
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError
        return stamp.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"INVALID_{name}") from None


def _iso(stamp):
    return stamp.isoformat(timespec="microseconds")


def _date(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("INVALID_MATCH_DATE") from None


def _positive_id(value, name):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"INVALID_{name}")  # noqa: TRY004 - uniform record rejection contract
    text = str(value)
    if not re.fullmatch(r"[0-9]+", text) or int(text) <= 0:
        raise ValueError(f"INVALID_{name}")
    return str(int(text))


def _stats_id(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(rf"{prefix}_[0-9]{{1,30}}", value):
        raise ValueError("INVALID_PROVIDER_ID")
    return value


def _score(value):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError("INVALID_SCORE")
    return value


def _pair(value, *, complete=True):
    if value is None:
        return None, None
    scope = _mapping(value, "SCORE_SCOPE")
    home, away = _score(scope.get("home")), _score(scope.get("away"))
    return (None, None) if complete and (home is None or away is None) else (home, away)


def _halves(full, half):
    if any(first is not None and final is not None and first > final
           for first, final in zip(half, full, strict=True)):
        raise ValueError("HALF_TIME_EXCEEDS_REGULATION")


def _json_copy(raw):
    def check(value):
        if value is None or type(value) in (str, bool, int):
            return
        if type(value) is float and math.isfinite(value):
            return
        if type(value) is list:
            for child in value:
                check(child)
            return
        if type(value) is dict and all(type(key) is str for key in value):
            for child in value.values():
                check(child)
            return
        raise ValueError("INVALID_RAW_JSON")

    try:
        if not isinstance(raw, dict):
            raise ValueError("INVALID_RAW_JSON")  # noqa: TRY004 - uniform record rejection contract
        check(raw)
        return json.loads(json.dumps(raw, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError("INVALID_RAW_JSON") from None


def _evidence(observed_at, source_url, source_sha256):
    observed = _stamp(observed_at, "OBSERVED_AT")
    source_url = _key(source_url, "SOURCE_URL")
    try:
        parsed = urlsplit(source_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None or parsed.fragment
                or any(char.isspace() for char in source_url)):
            raise ValueError
        _ = parsed.port  # Validate a supplied port without changing the evidence URL.
    except ValueError:
        raise ValueError("INVALID_SOURCE_URL") from None
    if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256):
        raise ValueError("INVALID_SOURCE_SHA256")
    return observed, source_url, source_sha256.lower()


def normalize_api_result(raw, *, observed_at, source_url, source_sha256):
    """Normalize one finished API-Football raw fixture, never goals/AET totals.

    Non-completed statuses raise ``ValueError('NOT_FINISHED')`` so an offline
    import may count them separately. Missing regulation scores remain unknown.
    """
    raw = _json_copy(raw)
    observed, source_url, source_sha256 = _evidence(observed_at, source_url, source_sha256)
    fixture = _mapping(raw.get("fixture"), "FIXTURE")
    status = _mapping(fixture.get("status"), "STATUS").get("short")
    _key(status, "STATUS")
    if status not in ("FT", "AET", "PEN"):
        raise ValueError("NOT_FINISHED")
    league = _mapping(raw.get("league"), "COMPETITION")
    teams = _mapping(raw.get("teams"), "TEAMS")
    home, away = (_mapping(teams.get(side), "TEAM") for side in ("home", "away"))
    home_id, away_id = (_positive_id(team.get("id"), "TEAM_ID") for team in (home, away))
    if home_id == away_id:
        raise ValueError("IDENTICAL_TEAMS")
    kickoff = _stamp(fixture.get("date"), "KICKOFF")
    if kickoff >= observed:
        raise ValueError("KICKOFF_NOT_BEFORE_OBSERVATION")
    epoch = fixture.get("timestamp")
    if epoch is not None and (type(epoch) is not int or epoch != math.floor(kickoff.timestamp())):
        raise ValueError("CONFLICTING_KICKOFF_TIMESTAMP")
    season = league.get("season")
    if type(season) is int and season >= 0:
        season = str(season)
    season = _key(season, "SEASON_ID")
    scores = {} if raw.get("score") is None else _mapping(raw["score"], "SCORE")
    full = _pair(scores.get("fulltime"))
    half = _pair(scores.get("halftime"), complete=False)
    _halves(full, half)
    return {
        "provider": "api_football", "provider_id": _positive_id(fixture.get("id"), "FIXTURE_ID"),
        "competition_id": _positive_id(league.get("id"), "COMPETITION_ID"), "season_id": season,
        "home_id": home_id, "away_id": away_id,
        "home_team": _text(home.get("name"), "HOME_TEAM"),
        "away_team": _text(away.get("name"), "AWAY_TEAM"),
        "kickoff": _iso(kickoff), "match_date": kickoff.date().isoformat(),
        "kickoff_local_time": None, "status": status, "score_scope": "REGULATION",
        "home_goals": full[0], "away_goals": full[1],
        "half_home_goals": half[0], "half_away_goals": half[1],
        "observed_at": _iso(observed), "source_url": source_url,
        "source_sha256": source_sha256, "raw": raw,
    }


def _archive_fields(row):
    row = _mapping(row, "ARCHIVE_ROW")
    result = {name: _key(row.get(name), name.upper())
              for name in ("provider", "source_record_key", "league_code", "season_code")}
    result.update({name: _text(row.get(name), name.upper())
                   for name in ("home_team", "away_team")})
    result["match_date"] = _date(row.get("match_date")).isoformat()
    for name in ("full_time_home", "full_time_away", "half_time_home", "half_time_away"):
        result[name] = _score(row.get(name))
    for prefix in ("full_time", "half_time"):
        outcome = row.get(prefix + "_result")
        if outcome is not None and outcome not in ("H", "D", "A"):
            raise ValueError("INVALID_RESULT")
        home, away = (result[prefix + "_" + side] for side in ("home", "away"))
        if (outcome is not None and home is not None and away is not None
                and outcome != ("H" if home > away else "A" if away > home else "D")):
            raise ValueError("INCONSISTENT_RESULT")
    local = row.get("kickoff_local_time")
    if local is not None:
        try:
            if not isinstance(local, str) or not re.fullmatch(r"[0-9]{2}:[0-9]{2}(?::[0-9]{2})?", local):
                raise ValueError
            time.fromisoformat(local)
        except (TypeError, ValueError):
            raise ValueError("INVALID_LOCAL_TIME") from None
    result["kickoff_local_time"] = local
    _halves((result["full_time_home"], result["full_time_away"]),
            (result["half_time_home"], result["half_time_away"]))
    return result


def _stats_archive(row, raw, observed):
    expected = {"id": _stats_id(row["source_record_key"], "mt"),
                "competition_id": _stats_id(row["league_code"], "comp"),
                "season_id": _stats_id(row["season_code"], "sn")}
    if any(raw.get(name) != value for name, value in expected.items()):
        raise ValueError("ARCHIVE_RAW_IDENTITY_MISMATCH")
    if raw.get("status") != "finished":
        raise ValueError("NOT_FINISHED")
    teams = [_mapping(raw.get(side + "_team"), "TEAM") for side in ("home", "away")]
    identifiers = [_stats_id(team.get("id"), "tm") for team in teams]
    if identifiers[0] == identifiers[1]:
        raise ValueError("IDENTICAL_TEAMS")
    if any(team.get("name") != row[side + "_team"]
           for team, side in zip(teams, ("home", "away"), strict=True)):
        raise ValueError("ARCHIVE_RAW_TEAM_MISMATCH")
    kickoff = _stamp(raw.get("utc_date"), "KICKOFF")
    if kickoff >= observed:
        raise ValueError("KICKOFF_NOT_BEFORE_OBSERVATION")
    if kickoff.date().isoformat() != row["match_date"] or row["kickoff_local_time"] is not None:
        raise ValueError("ARCHIVE_RAW_TIME_MISMATCH")
    scores = {} if raw.get("score") is None else _mapping(raw["score"], "SCORE")
    full = _pair(scores.get("regulation"))
    if full != (row["full_time_home"], row["full_time_away"]):
        raise ValueError("ARCHIVE_RAW_SCORE_MISMATCH")
    if row["half_time_home"] is not None or row["half_time_away"] is not None:
        raise ValueError("UNPROVEN_HALF_TIME_SCOPE")
    return identifiers, _iso(kickoff), "finished", "REGULATION", full, (None, None)


def _csv_date(value):
    match = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{2}|[0-9]{4})", value)
    if not match:
        raise ValueError("INVALID_RAW_MATCH_DATE")
    day, month, year = (int(part) for part in match.groups())
    if len(match[3]) == 2:
        year += 1900 if year >= 93 else 2000
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        raise ValueError("INVALID_RAW_MATCH_DATE") from None


def _csv_score(value, *, optional=False):
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,3}", value):
        raise ValueError("INVALID_RAW_SCORE")
    return int(value)


def _fd_archive(row, raw, observed):
    if not re.fullmatch(r"football_data_co_uk:[0-9a-f]{64}", row["source_record_key"]):
        raise ValueError("INVALID_PROVIDER_ID")
    values = {}
    for key, value in raw.items():
        if not isinstance(value, str) or key.strip() in values:
            raise ValueError("INVALID_RAW_CSV_ROW")
        values[key.strip()] = value.strip()
    expected = {"Div": row["league_code"], "HomeTeam": row["home_team"],
                "AwayTeam": row["away_team"]}
    if (any(values.get(name) != value for name, value in expected.items())
            or _csv_date(values.get("Date", "")) != row["match_date"]):
        raise ValueError("ARCHIVE_RAW_IDENTITY_MISMATCH")
    if row["home_team"] == row["away_team"]:
        raise ValueError("IDENTICAL_TEAMS")
    if _date(row["match_date"]) > observed.date():
        raise ValueError("FUTURE_MATCH_DATE")
    full = tuple(_csv_score(values.get(name)) for name in ("FTHG", "FTAG"))
    half = tuple(_csv_score(values.get(name), optional=True) for name in ("HTHG", "HTAG"))
    if (full != (row["full_time_home"], row["full_time_away"])
            or half != (row["half_time_home"], row["half_time_away"])):
        raise ValueError("ARCHIVE_RAW_SCORE_MISMATCH")
    if values.get("FTR") != ("H" if full[0] > full[1] else "A" if full[1] > full[0] else "D"):
        raise ValueError("INCONSISTENT_RAW_RESULT")
    if values.get("HTR"):
        if values["HTR"] not in ("H", "D", "A"):
            raise ValueError("INVALID_RAW_RESULT")
        if None not in half and values["HTR"] != (
                "H" if half[0] > half[1] else "A" if half[1] > half[0] else "D"):
            raise ValueError("INCONSISTENT_RAW_RESULT")
    local = values.get("Time") or None
    if local is not None:
        match = re.fullmatch(r"([0-9]{1,2}):([0-9]{2})", local)
        if not match or int(match[1]) > 23 or int(match[2]) > 59:
            raise ValueError("INVALID_RAW_LOCAL_TIME")
        local = f"{int(match[1]):02d}:{match[2]}"
    if local != row["kickoff_local_time"]:
        raise ValueError("ARCHIVE_RAW_TIME_MISMATCH")
    return (None, None), None, "FINISHED", "SOURCE_FULL_TIME_UNVERIFIED", full, half


def normalize_archive_result(row, raw, *, observed_at, source_url, source_sha256):
    """Normalize a source-namespaced immutable sports_archive observation.

    Both the archive row and its detached raw row must agree. FD completion is
    evidenced by its full-time score/result; its unknown local timezone remains
    unknown. TheStats missing regulation scores are never replaced by totals.
    """
    row, raw = _archive_fields(row), _json_copy(raw)
    observed, source_url, source_sha256 = _evidence(observed_at, source_url, source_sha256)
    if row["provider"] == "the_stats_api":
        native, kickoff, status, scope, full, half = _stats_archive(row, raw, observed)
    elif row["provider"] == "football_data_co_uk":
        native, kickoff, status, scope, full, half = _fd_archive(row, raw, observed)
    else:
        raise ValueError("UNSUPPORTED_ARCHIVE_PROVIDER")
    return {
        "provider": row["provider"], "provider_id": row["source_record_key"],
        "competition_id": row["league_code"], "season_id": row["season_code"],
        "home_id": native[0], "away_id": native[1],
        "home_team": row["home_team"], "away_team": row["away_team"],
        "kickoff": kickoff, "match_date": row["match_date"],
        "kickoff_local_time": row["kickoff_local_time"], "status": status, "score_scope": scope,
        "home_goals": full[0], "away_goals": full[1],
        "half_home_goals": half[0], "half_away_goals": half[1],
        "observed_at": _iso(observed), "source_url": source_url,
        "source_sha256": source_sha256, "raw": raw,
    }
