"""Read-only, point-in-time training inputs for the weather specialist.

CSV archives deliberately remain outside this join: team names are not verified
cross-provider identities. Reanalysis is retrospective model data, never a
forecast that was known before a historical match. Its actual receipt timestamp
is retained so callers can enforce their own training and evaluation cutoffs.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

_VERSION = "weather-dataset-v1"
_MAX_SNAPSHOTS = 20_000
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_BYTES = 512 * 1024 * 1024
_MAX_MATCHES = 200_000
_MAX_WEATHER_ROWS = 100_000
_MAX_WEATHER_BYTES = 64 * 1024 * 1024
_WEATHER_RANGES = {
    "temperature_c": (-100, 70),
    "relative_humidity_pct": (0, 100),
    "precipitation_mm": (0, 1000),
    "wind_speed_kmh": (0, 500),
    "wind_gusts_kmh": (0, 500),
    "weather_code": (0, 99),
}


class _Rejected(ValueError):
    pass


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _time(value) -> datetime:
    try:
        value = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("not aware")
        return value.astimezone(UTC)
    except (ValueError, TypeError) as error:
        raise _Rejected("invalid_timestamp") from error


def _id(value) -> str:
    if type(value) is int and value > 0:
        return f"api_football:{value}"
    if isinstance(value, str) and value.isascii() and value.isdigit() and int(value) > 0:
        return f"api_football:{int(value)}"
    raise _Rejected("invalid_provider_identity")


def _name(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Rejected("missing_team_name")
    return value.strip()


def _match(item: dict, observed: datetime, cutoff: datetime) -> dict:
    try:
        fixture, teams, league = item["fixture"], item["teams"], item["league"]
        status = fixture["status"]["short"]
        if status not in {"FT", "AET", "PEN"}:
            raise _Rejected("not_completed")
        kickoff = _time(fixture["date"])
        if kickoff >= cutoff:
            raise _Rejected("future_kickoff")
        if observed < kickoff + timedelta(minutes=90):
            raise _Rejected("result_observed_before_regulation_end")
        # For AET/PEN, goals includes extra time; only score.fulltime is 90 minutes.
        score = item["score"]["fulltime"]
        goals = (score["home"], score["away"])
        if any(type(goal) is not int or goal < 0 for goal in goals):
            raise _Rejected("missing_or_invalid_90_minute_score")
        home, away = _id(teams["home"]["id"]), _id(teams["away"]["id"])
        if home == away:
            raise _Rejected("same_team_identity")
        season = league.get("season")
        if type(season) is not int or not 1900 <= season <= 2200:
            raise _Rejected("invalid_season")
        kind = league.get("type")
        kind = kind.upper() if isinstance(kind, str) else "UNKNOWN"
        if kind not in {"LEAGUE", "CUP"}:
            kind = "UNKNOWN"
        venue = fixture.get("venue") or {}
        return {
            "fixture_id": _id(fixture["id"]), "provider": "api_football",
            "home_team_id": home, "away_team_id": away,
            "home_team": _name(teams["home"]["name"]),
            "away_team": _name(teams["away"]["name"]),
            "league_id": _id(league["id"]), "competition_type": kind, "season": season,
            "kickoff": kickoff.isoformat(), "home_goals": goals[0], "away_goals": goals[1],
            "result_period": "FULL_TIME_90_MINUTES", "status": status,
            "observed_at": observed.isoformat(), "venue_id": venue.get("id"),
            "venue_name": venue.get("name"), "venue_city": venue.get("city"),
        }
    except (KeyError, TypeError, AttributeError) as error:
        raise _Rejected("invalid_fixture_structure") from error


def _fixtures(connection, raw_root: Path, cutoff: datetime, counts: Counter) -> dict:
    latest = {}
    total_bytes = 0
    rows = connection.execute(
        "SELECT content_hash,fetched_at,relative_path,size_bytes,source_url "
        "FROM raw_snapshots WHERE provider='api_football' AND resource_type='fixtures' "
        "ORDER BY fetched_at,content_hash"
    )
    for index, row in enumerate(rows, 1):
        if index > _MAX_SNAPSHOTS:
            raise ValueError("weather dataset snapshot limit exceeded; use a partitioned archive")
        counts["fixture_snapshots_seen"] += 1
        try:
            observed = _time(row["fetched_at"])
            if observed > cutoff:
                counts["fixture_snapshots_after_cutoff"] += 1
                continue
            path = (raw_root / row["relative_path"]).resolve()
            if not path.is_relative_to(raw_root) or path.stat().st_size > _MAX_SNAPSHOT_BYTES:
                raise _Rejected("unsafe_or_oversize_snapshot")
            raw = path.read_bytes()
            total_bytes += len(raw)
            if total_bytes > _MAX_TOTAL_BYTES:
                raise ValueError("weather dataset byte limit exceeded; use a partitioned archive")
            if len(raw) != row["size_bytes"] or hashlib.sha256(raw).hexdigest() != row["content_hash"]:
                raise _Rejected("fixture_snapshot_hash_or_size_mismatch")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("errors"):
                raise _Rejected("invalid_fixture_response")
            items = payload.get("response")
            if not isinstance(items, list):
                raise _Rejected("invalid_fixture_response")
        except (OSError, json.JSONDecodeError, UnicodeError) as error:
            counts[f"rejected_snapshot:{type(error).__name__}"] += 1
            continue
        except _Rejected as error:
            counts[f"rejected_snapshot:{error}"] += 1
            continue
        counts["fixture_snapshots_read"] += 1
        for item in items:
            counts["fixture_versions_seen"] += 1
            try:
                identity = _id(item["fixture"]["id"])
            except (KeyError, TypeError, _Rejected):
                counts["rejected_fixture:invalid_provider_identity"] += 1
                continue
            try:
                match = _match(item, observed, cutoff)
                reason = None
            except _Rejected as error:
                match, reason = None, str(error)
            previous = latest.get(identity)
            fingerprint = _hash({"match": match, "reason": reason})
            if previous is not None and observed < previous[0]:
                continue
            if previous is not None and observed == previous[0]:
                if fingerprint != previous[1]:
                    # Conflicting equally recent versions have no reliable ordering.
                    latest[identity] = (observed, None, None, "ambiguous_latest_version")
                continue
            if match is not None:
                match["source_content_hash"] = row["content_hash"]
                match["source_url"] = row["source_url"]
            latest[identity] = (observed, fingerprint, match, reason)
            if len(latest) > _MAX_MATCHES:
                raise ValueError("weather dataset fixture limit exceeded; partition the archive")
    result = {}
    for identity, (_, _, match, reason) in latest.items():
        if match is None:
            counts[f"rejected_fixture:{reason}"] += 1
        else:
            result[identity] = match
    counts["fixture_snapshot_bytes_read"] = total_bytes
    return result


def _weather_record(row, match: dict, cutoff: datetime, kind: str) -> dict:
    value = json.loads(row["snapshot_json"])
    if _hash(value) != row["snapshot_id"]:
        raise _Rejected("weather_snapshot_hash_mismatch")
    weather = value["weather"]
    observed, target = _time(value["observed_at"]), _time(value["target_time"])
    if observed != _time(row["observed_at"]) or observed > cutoff:
        raise _Rejected("weather_time_mismatch")
    if kind == "REANALYSIS" and target > observed:
        raise _Rejected("weather_time_mismatch")
    if weather.get("observed_at") is not None and _time(weather["observed_at"]) != observed:
        raise _Rejected("weather_time_mismatch")
    if value.get("weather_kind") != kind or value.get("status") != "SOURCED":
        raise _Rejected("not_sourced_weather")
    if weather.get("weather_kind") != kind or weather.get("status") != "SOURCED":
        raise _Rejected("not_sourced_weather")
    if value["fixture_id"] != match["fixture_id"] or weather["fixture_id"] != match["fixture_id"]:
        raise _Rejected("weather_identity_mismatch")
    kickoff = _time(match["kickoff"])
    if _time(value["kickoff"]) != kickoff:
        raise _Rejected("weather_kickoff_mismatch")
    if kind == "FORECAST" and not (
        kickoff - timedelta(hours=48) <= observed <= kickoff - timedelta(minutes=15)
    ):
        raise _Rejected("forecast_outside_prematch_window")
    if abs(target - kickoff) > timedelta(hours=1):
        raise _Rejected("weather_target_outside_match_hour")
    issued = value.get("provider_issue_time")
    if issued is not None and _time(issued) > observed:
        raise _Rejected("weather_issue_after_receipt")
    raw = value.get("raw_payload")
    if raw is not None and _hash(raw) != value.get("raw_payload_hash"):
        raise _Rejected("weather_raw_hash_mismatch")
    result = {}
    for key, (low, high) in _WEATHER_RANGES.items():
        number = weather.get(key)
        if number is not None and (
            type(number) not in (int, float) or not math.isfinite(number)
            or not low <= number <= high
        ):
            raise _Rejected("invalid_weather_value")
        result[key] = number
    if all(number is None for number in result.values()):
        raise _Rejected("weather_values_missing")
    return result | {
        "weather_kind": kind, "kind": kind,
        "status": "SOURCED", "observed_at": observed.isoformat(),
        "target_time": target.isoformat(), "provider": value["provider"],
        "model": weather.get("model"), "source_url": value.get("source_url"),
        "raw_payload_hash": value.get("raw_payload_hash"), "snapshot_id": row["snapshot_id"],
        "time_basis": value.get("time_basis"), "latitude": weather.get("latitude"),
        "longitude": weather.get("longitude"),
        "coordinate_precision": weather.get("coordinate_precision"),
        "historical_use": (
            "RETROSPECTIVE_REANALYSIS_NOT_PREMATCH_KNOWLEDGE" if kind == "REANALYSIS"
            else "ARCHIVED_PREMATCH_FORECAST"
        ),
    }


def _join_weather(connection, matches: dict, cutoff: datetime, counts: Counter) -> None:
    latest = {}
    total_bytes = 0
    rows = connection.execute(
        "SELECT snapshot_id,fixture_id,weather_kind,observed_at,snapshot_json "
        "FROM sports_weather_snapshots WHERE weather_kind IN ('REANALYSIS','FORECAST') "
        "AND status='SOURCED' ORDER BY observed_at,snapshot_id"
    )
    for index, row in enumerate(rows, 1):
        if index > _MAX_WEATHER_ROWS:
            raise ValueError("weather dataset weather-row limit exceeded; partition the archive")
        total_bytes += len(row["snapshot_json"].encode("utf-8"))
        if total_bytes > _MAX_WEATHER_BYTES:
            raise ValueError("weather dataset weather-byte limit exceeded; partition the archive")
        kind = row["weather_kind"]
        counts[f"{kind.lower()}_versions_seen"] += 1
        try:
            observed = _time(row["observed_at"])
        except _Rejected:
            counts["rejected_weather:invalid_timestamp"] += 1
            continue
        if observed > cutoff:
            counts[f"{kind.lower()}_after_cutoff"] += 1
            continue
        identity = row["fixture_id"]
        match = matches.get(identity)
        if match is None:
            counts[f"{kind.lower()}_without_matching_completed_fixture"] += 1
            continue
        if kind == "FORECAST":
            kickoff = _time(match["kickoff"])
            if not kickoff - timedelta(hours=48) <= observed <= kickoff - timedelta(minutes=15):
                counts["forecast_outside_prematch_window"] += 1
                continue
        key = identity, kind
        previous = latest.get(key)
        if previous is None or observed >= previous[0]:
            latest[key] = (observed, row)
    for (identity, kind), (_, row) in latest.items():
        match = matches[identity]
        try:
            key = "weather" if kind == "REANALYSIS" else "prematch_weather"
            match[key] = _weather_record(row, match, cutoff, kind)
        except (KeyError, TypeError, ValueError) as error:
            reason = str(error) if isinstance(error, _Rejected) else "invalid_weather_structure"
            counts[f"rejected_weather:{reason}"] += 1


def load_weather_dataset(root: Path, *, as_of: datetime) -> dict:
    """Return immutable-source inputs; performs no writes, requests or fuzzy joins.

    ``matches`` contains all usable completed fixtures, including those without
    weather, for chronological team/opponent strength estimates. ``weather`` is
    reanalysis; ``prematch_weather`` is an archived forecast received 48 hours to
    15 minutes before kickoff. Joins require exact provider IDs and the original
    kickoff. Missing values remain absent. The hash covers selected inputs.
    """
    cutoff = _time(as_of)
    root = Path(root).resolve()
    database = root / "data" / "goal_analysis.sqlite3"
    counts = Counter()
    matches = {}
    if database.exists():
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")  # one consistent read snapshot for both sources
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "raw_snapshots" in tables:
                matches = _fixtures(connection, (root / "data" / "raw").resolve(), cutoff, counts)
            else:
                counts["missing_fixture_snapshot_table"] += 1
            if "sports_weather_snapshots" in tables:
                _join_weather(connection, matches, cutoff, counts)
            else:
                counts["missing_weather_table"] += 1
        finally:
            connection.close()
    else:
        counts["missing_database"] += 1
    ordered = sorted(matches.values(), key=lambda item: (item["kickoff"], item["fixture_id"]))
    counts["completed_matches"] = len(ordered)
    counts["matches_with_reanalysis"] = sum("weather" in item for item in ordered)
    counts["matches_without_reanalysis"] = len(ordered) - counts["matches_with_reanalysis"]
    counts["matches_with_prematch_forecast"] = sum("prematch_weather" in item for item in ordered)
    counts["unknown_competition_type"] = sum(
        item["competition_type"] == "UNKNOWN" for item in ordered
    )
    from goal_analysis.normalization.training_datasets import snapshot

    manifest = snapshot(ordered, role='weather_reference', as_of=cutoff,
        recipe={'version': _VERSION, 'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        lineage={'identity_policy': 'EXACT_API_FOOTBALL_IDS_NO_CROSS_PROVIDER_NAME_JOIN'})['manifest']
    return {
        "matches": ordered, "coverage": dict(sorted(counts.items())),
        'dataset_manifest': manifest,
        "data_hash": _hash({"version": _VERSION, "matches": ordered}),
        "as_of": cutoff.isoformat(), "version": _VERSION,
        "identity_policy": "EXACT_API_FOOTBALL_IDS_NO_CROSS_PROVIDER_NAME_JOIN",
    }
