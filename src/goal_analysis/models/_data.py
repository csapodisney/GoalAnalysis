"""Strict, offline input boundary shared by experimental numerical models."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta

CLOSURE_DELAY = timedelta(hours=3)
MIN_TEAM_MATCHES = 8


class InsufficientHistory(ValueError):
    """A model must abstain instead of manufacturing a probability."""


def aware_time(value) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value)
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required")
    return parsed.astimezone(UTC)


def identifier(value) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("Missing canonical provider ID")
    text = str(value)
    if not text.isascii() or not text.isdecimal() or int(text) <= 0:
        raise ValueError("Positive provider ID required")
    return str(int(text))


def diagnostic(code: str, **context) -> dict:
    return {"code": code, **context}


def fixture_identity(raw: dict, nested: dict) -> tuple[str, str]:
    """Keep the app identity while comparing history with the provider's bare ID."""
    external = raw.get("fixture_id", nested.get("id"))
    text = str(external)
    if raw.get('source_provider') == 'the_stats_api':
        from goal_analysis.normalization.live_identity import fixture_identity as native_fixture

        return text, native_fixture(raw)
    for prefix in ("api_football:", "api-football:"):
        if text.startswith(prefix):
            provider_id = identifier(text[len(prefix):])
            break
    else:
        provider_id = identifier(external)
    explicit = raw.get("api_football_fixture_id")
    if explicit is not None and identifier(explicit) != provider_id:
        raise ValueError("Canonical and provider fixture IDs disagree")
    return text, provider_id


def history_hash(records: list[dict]) -> str:
    content = [{k: v for k, v in row.items() if not k.startswith("_")} for row in records]
    raw = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_fixtures(fixtures: list[dict], now: datetime) -> tuple[list[dict], list[dict]]:
    result, issues, conflicts = {}, [], set()
    for raw in fixtures:
        try:
            fixture = raw.get("fixture", {})
            league = raw.get("league", {})
            teams = raw.get("teams", {})
            external_id, provider_id = fixture_identity(raw, fixture)
            native = raw.get('source_provider') == 'the_stats_api'
            if native:
                from goal_analysis.normalization.live_identity import field_id
            row = {
                "fixture_id": external_id, "provider_fixture_id": provider_id,
                "league_id": field_id(raw, 'league') if native else identifier(raw.get("league_id", raw.get(
                    "api_football_league_id", league.get("id")
                ))),
                "home_team_id": field_id(raw, 'home_team') if native else identifier(raw.get("home_team_id", raw.get(
                    "api_football_home_team_id", teams.get("home", {}).get("id")
                ))),
                "away_team_id": field_id(raw, 'away_team') if native else identifier(raw.get("away_team_id", raw.get(
                    "api_football_away_team_id", teams.get("away", {}).get("id")
                ))),
                "kickoff": aware_time(raw.get("kickoff", fixture.get("date"))),
                "neutral_venue": raw.get("neutral_venue", False),
            }
            status = raw.get("provider_status", raw.get("status", fixture.get("status", "NS")))
            if isinstance(status, dict):
                status = status.get("short")
            if row["home_team_id"] == row["away_team_id"]:
                raise ValueError("A team cannot play itself")
            if type(row["neutral_venue"]) is not bool:
                raise ValueError("neutral_venue must be boolean")
            if row["kickoff"] <= now or status not in {"NS", "TBD", None}:
                issues.append(diagnostic("FIXTURE_NOT_PREMATCH", fixture_id=row["fixture_id"]))
                continue
            key = row["fixture_id"]
            if key in result and row != result[key]:
                conflicts.add(key)
            result.setdefault(key, row)
        except (KeyError, TypeError, ValueError, AttributeError):
            issues.append(diagnostic("INVALID_FIXTURE"))
    for key in sorted(conflicts):
        result.pop(key, None)
        issues.append(diagnostic("CONFLICTING_FIXTURE", fixture_id=key))
    return sorted(result.values(), key=lambda r: (r["kickoff"], r["fixture_id"])), issues


def prepare_history(
    records: list[dict], now: datetime, excluded_ids: set[str]
) -> tuple[list[dict], list[dict]]:
    """Never backdate ingestion or admit unfinished/future/target fixtures.

    observed_at is the provider snapshot's acquisition time, not an asserted
    historical publication time. Both it and the conservative closure bound
    must precede the live decision. Retrospective Pi features explicitly use
    kickoff + 3h as an availability approximation (not a verified backtest).
    """
    clean, conflicts, counts = {}, set(), Counter()
    for raw in records:
        try:
            if raw.get('provider') == 'the_stats_api':
                from goal_analysis.normalization.live_identity import provider_id

                row = {key: provider_id(raw[key], key.removesuffix('_id'), 'the_stats_api')
                       for key in ('fixture_id', 'league_id', 'home_team_id', 'away_team_id')}
            else:
                row = {key: identifier(raw[key]) for key in (
                    "fixture_id", "league_id", "home_team_id", "away_team_id"
                )}
            if row["fixture_id"] in excluded_ids:
                counts["TARGET_HISTORY_EXCLUDED"] += 1
                continue
            kickoff, observed = aware_time(raw["kickoff"]), aware_time(raw["observed_at"])
            if kickoff + CLOSURE_DELAY >= now or observed > now:
                counts["FUTURE_OR_UNCLOSED_HISTORY"] += 1
                continue
            if observed <= kickoff or raw["status"] not in {"FT", "AET", "PEN"}:
                counts["UNFINISHED_OR_INVALID_CHRONOLOGY"] += 1
                continue
            if row["home_team_id"] == row["away_team_id"]:
                raise ValueError("A team cannot play itself")
            for side in ("home", "away"):
                value = raw[f"full_time_{side}"]
                if type(value) is not int or not 0 <= value <= 50:
                    raise ValueError("Regular-time integer score required")
                row[f"full_time_{side}"] = value
            source = raw.get("source_id")
            if not isinstance(source, str) or not source.strip():
                raise ValueError("Source lineage required")
            neutral = raw.get("neutral_venue", False)
            if type(neutral) is not bool:
                raise ValueError("neutral_venue must be boolean")
            row.update(
                kickoff=kickoff.isoformat(), observed_at=observed.isoformat(),
                status=raw["status"], season=raw.get("season"), source_id=source,
                neutral_venue=neutral, _kickoff=kickoff, _observed_at=observed,
            )
            key = row["fixture_id"]
            previous = clean.get(key)
            if previous is not None:
                def comparable(record):
                    return {
                        k: v for k, v in record.items()
                        if k not in {"source_id", "observed_at", "_observed_at"}
                    }
                if comparable(row) != comparable(previous):
                    conflicts.add(key)
                else:
                    counts["DUPLICATE_HISTORY"] += 1
                    # Stable choice independent of input order, retaining actual provenance.
                    clean[key] = min((previous, row), key=lambda r: (
                        r["_observed_at"], r["source_id"]
                    ))
            else:
                clean[key] = row
        except (KeyError, TypeError, ValueError, AttributeError):
            counts["INVALID_HISTORY"] += 1
    for key in conflicts:
        clean.pop(key, None)
    issues = [diagnostic(code, count=count) for code, count in sorted(counts.items())]
    issues += [diagnostic("CONFLICTING_HISTORY", fixture_id=k) for k in sorted(conflicts)]
    return sorted(clean.values(), key=lambda r: (r["_kickoff"], r["fixture_id"])), issues


def window_records(records: list[dict], now: datetime, days: int) -> list[dict]:
    earliest = now - timedelta(days=days)
    return [r for r in records if r["_kickoff"] >= earliest]


def team_counts(records: list[dict]) -> Counter:
    return Counter(team for r in records for team in (r["home_team_id"], r["away_team_id"]))


def sparse_team_core(records: list[dict]) -> tuple[list[dict], dict]:
    """Keep the deterministic four-appearance core, without consulting outcomes.

    Appearance counts include repeated meetings, matching check_competition's
    existing boundary. Removing a sparse team can expose another sparse team;
    remove all currently sparse teams together until the sample is stable.
    Connectivity and league/target sample minimums are still separate vetoes.
    Input is already normalized and cutoff-filtered by prepare_history.
    """
    original = sorted(records, key=lambda r: (r["_kickoff"], r["fixture_id"]))
    retained = original
    initial_teams = set(team_counts(original))
    iterations = 0
    while retained:
        sparse = {team for team, count in team_counts(retained).items() if count < 4}
        if not sparse:
            break
        retained = [row for row in retained
                    if row["home_team_id"] not in sparse and row["away_team_id"] not in sparse]
        iterations += 1
    retained_teams = set(team_counts(retained))
    return retained, {
        "policy": "iterative_four_appearance_team_core_v1",
        "minimum_team_appearances": 4, "iterations": iterations,
        "input_rows": len(original), "retained_rows": len(retained),
        "removed_rows": len(original) - len(retained),
        "input_teams": len(initial_teams), "retained_teams": len(retained_teams),
        "removed_teams": len(initial_teams - retained_teams),
        "removed_team_ids": sorted(initial_teams - retained_teams),
        "input_history_hash": history_hash(original),
        "retained_history_hash": history_hash(retained),
        "selection_note": "Coverage-based sample selection; not outcome-tuned or validated calibration.",
    }


def competition_window(records: list[dict], now: datetime, days: int, minimum: int):
    """Select a fixed window's core, then retain every original competition gate."""
    sample, selection = sparse_team_core(window_records(records, now, days))
    selection.update(window_days=days, minimum_competition_matches=minimum)
    try:
        check_competition(sample, minimum)
    except InsufficientHistory as error:
        error.training_selection = {str(days): selection}
        raise
    return sample, selection


def check_teams(records: list[dict], fixture: dict) -> None:
    counts = team_counts(records)
    if min(counts[fixture["home_team_id"]], counts[fixture["away_team_id"]]) < MIN_TEAM_MATCHES:
        raise InsufficientHistory(f"Mindkét csapatnak legalább {MIN_TEAM_MATCHES} múltbeli meccs kell.")


def check_competition(records: list[dict], minimum: int) -> None:
    counts = team_counts(records)
    if len(records) < minimum or len(counts) < 4:
        raise InsufficientHistory(f"Legalább {minimum} lezárt bajnoki meccs és 4 csapat kell.")
    if min(counts.values()) < 4:
        raise InsufficientHistory("Az egyik történeti csapatnak négynél kevesebb meccse van.")
    # Disjoint team pools do not identify comparable strengths in a single fit.
    links = {team: set() for team in counts}
    for row in records:
        home, away = row["home_team_id"], row["away_team_id"]
        links[home].add(away)
        links[away].add(home)
    seen, pending = set(), [next(iter(links))]
    while pending:
        team = pending.pop()
        if team not in seen:
            seen.add(team)
            pending.extend(links[team] - seen)
    if len(seen) != len(links):
        raise InsufficientHistory("A csapatok történeti meccshálózata nem összefüggő.")
