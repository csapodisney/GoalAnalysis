"""Deterministic prematch joins. No aliases, fuzzy matching or ID inference."""
from collections import Counter
from copy import deepcopy
from datetime import timedelta
import re
import unicodedata

from .canonical import utc


def exact_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing team name")
    # Preserve punctuation and accents: A-B and AB are different names.
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def native_id(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r"_[0-9]+", value):
        raise ValueError("invalid provider identity")
    return value


def field_id(row, field):
    direct = row.get(field + "_id")
    nested = row.get(field)
    nested = nested.get("id") if isinstance(nested, dict) else None
    if direct is not None and nested is not None and direct != nested:
        raise ValueError("conflicting provider identity fields")
    return direct if direct is not None else nested


def match_fixture(fixture, rows, *, competition_id, as_of, verified_team_ids=None):
    """Return a detached unique row or an explicit missing-link/lockout result.

    competition_id must come from an explicit league mapping. Optional native
    team IDs must come from a verified mapping, not a name-derived guess.
    """
    def result(status="MISSING_LINK", reason=None, row=None, method=None):
        return {"status": status, "reason": reason, "method": method, "row": deepcopy(row)}

    try:
        now, kickoff = utc(as_of), utc(fixture["kickoff"])
        native_id(competition_id, "comp")
        if kickoff <= now + timedelta(minutes=15):
            return result("LOCKED", "PREMATCH_LOCKOUT")
        if verified_team_ids is not None:
            if not isinstance(verified_team_ids, tuple) or len(verified_team_ids) != 2:
                raise ValueError("invalid verified team mapping")
            home_id, away_id = (native_id(value, "tm") for value in verified_team_ids)
            if home_id == away_id:
                raise ValueError("identical verified participants")
        else:
            expected_names = tuple(exact_name(fixture[side + "_team"]) for side in ("home", "away"))
            if expected_names[0] == expected_names[1]:
                raise ValueError("identical participants")
    except (TypeError, KeyError, ValueError, OverflowError):
        return result(reason="INVALID_FIXTURE_OR_MAPPING")
    if not isinstance(rows, list):
        return result(reason="INVALID_PROVIDER_ROWS")
    identities = Counter(row.get("id") for row in rows
                         if isinstance(row, dict) and isinstance(row.get("id"), str))
    matches = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            native_id(row.get("id"), "mt")
            native_id(field_id(row, "season"), "sn")
            if (field_id(row, "competition") != competition_id
                    or utc(row.get("utc_date")) != kickoff or row.get("status") != "scheduled"):
                continue
            teams = tuple(row[side + "_team"] for side in ("home", "away"))
            identifiers = tuple(native_id(team["id"], "tm") for team in teams)
            if identifiers[0] == identifiers[1]:
                continue
            if verified_team_ids is not None:
                if identifiers != verified_team_ids:
                    continue
            elif tuple(exact_name(team.get("name")) for team in teams) != expected_names:
                continue
            matches.append(row)
        except (TypeError, KeyError, ValueError, OverflowError):
            continue
    if len(matches) != 1 or (matches and identities[matches[0]["id"]] != 1):
        return result(reason="AMBIGUOUS_EXACT_MATCH" if matches else "NO_EXACT_MATCH")
    return result("MATCHED", row=matches[0], method="VERIFIED_PROVIDER_IDS" if verified_team_ids
                  else "EXACT_NAMES_KICKOFF_EXPLICIT_LEAGUE")
