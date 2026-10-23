"""All calendar identities and fixtures in these scope tests are synthetic."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import pytest

from goal_analysis.jobs.league_scope import resolve_default_leagues

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)
DAY = date(2026, 10, 16)


def league(identifier=78):
    return {"competition_id": f"TEST_{identifier}", "api_football_id": identifier,
            "season": 2026, "history_seasons": [2025, 2026],
            "odds_sport_key": f"soccer_arthur_{identifier}", "competition_type": "LEAGUE"}


def fixture(identifier, competition=78, kickoff=None, season=2026, status="NS"):
    return {
        "fixture": {"id": identifier, "date": (kickoff or NOW + timedelta(hours=3)).isoformat(),
                    "status": {"short": status}},
        "league": {"id": competition, "season": season, "country": "Synthetic"},
        "teams": {"home": {"id": identifier * 2, "name": f"Home {identifier}"},
                  "away": {"id": identifier * 2 + 1, "name": f"Away {identifier}"}},
    }


def resolve(rows, configured=None, now=NOW):
    return resolve_default_leagues({"response": rows}, DAY, configured or [league()], now)


def ids(resolved):
    return [item["api_football_id"] for item in resolved]


@pytest.mark.parametrize("count, expected", [(14, [78, 138]), (15, [78])])
def test_sparse_threshold_uses_15_pending_fixtures(count, expected):
    rows = [fixture(index) for index in range(1, count + 1)] + [fixture(100, 138)]
    assert ids(resolve(rows)) == expected


def test_all_available_groups_use_provider_season_and_internal_route():
    rows = [fixture(100 + index, identifier, season=2027)
            for index, identifier in enumerate((138, 942, 943))]
    added = resolve(rows)[1:]
    assert ids(added) == [138, 942, 943]
    assert [item["competition_id"] for item in added] == ["IT3_A", "IT3_B", "IT3_C"]
    assert all(item["season"] == 2027 and item["history_seasons"] == [2026, 2027] for item in added)
    assert [item["odds_sport_key"] for item in added] == ["soccer_arthur_138", "soccer_arthur_942", "soccer_arthur_943"]


@pytest.mark.parametrize("seconds, expected", [(899, [78]), (900, [78]), (901, [78, 138])])
def test_fallback_requires_strict_15_minute_lockout(seconds, expected):
    assert ids(resolve([fixture(100, 138, NOW + timedelta(seconds=seconds))])) == expected


def test_locked_default_fixture_does_not_hide_sparse_calendar():
    rows = [fixture(index) for index in range(1, 15)]
    rows += [fixture(15, kickoff=NOW + timedelta(minutes=15)), fixture(100, 138)]
    assert ids(resolve(rows)) == [78, 138]


def test_count_and_fallback_ignore_started_finished_and_wrong_day():
    rows = [fixture(index) for index in range(1, 15)]
    rows += [fixture(15, status="1H"), fixture(16, status="FT"),
             fixture(17, kickoff=NOW - timedelta(minutes=1)),
             fixture(18, kickoff=NOW + timedelta(days=1)), fixture(100, 138)]
    assert ids(resolve(rows)) == [78, 138]
    assert ids(resolve([fixture(100, 138, NOW + timedelta(days=1))])) == [78]


def test_duplicate_default_identity_is_excluded_in_full():
    rows = [fixture(index) for index in range(1, 16)]
    rows += [deepcopy(rows[0]), fixture(100, 138)]
    assert ids(resolve(rows)) == [78, 138]


def test_duplicate_fallback_identity_is_excluded_in_full():
    row = fixture(100, 138)
    assert ids(resolve([row, deepcopy(row)])) == [78]


def test_duplicate_across_unselected_league_cannot_activate_fallback():
    assert ids(resolve([fixture(100, 138), fixture(100, 999)])) == [78]


def test_malformed_duplicate_venue_still_excludes_original_identity():
    duplicate = fixture(100, 138)
    duplicate["fixture"]["venue"] = ["malformed"]
    assert ids(resolve([fixture(100, 138), duplicate])) == [78]


@pytest.mark.parametrize("mutation", [
    lambda row: row["league"].pop("season"),
    lambda row: row["league"].update(season=True),
    lambda row: row["league"].update(season="2026"),
    lambda row: row["league"].update(season=2201),
    lambda row: row["fixture"].update(date="malformed"),
    lambda row: row["fixture"].update(date="2026-10-16T18:00:00"),
    lambda row: row["fixture"].update(venue=["malformed"]),
    lambda row: row["teams"]["away"].update(id=row["teams"]["home"]["id"]),
])
def test_malformed_fallback_fixture_never_activates_group(mutation):
    row = fixture(100, 138)
    mutation(row)
    assert ids(resolve([None, "bad", {}, row])) == [78]


def test_ambiguous_season_does_not_activate_group():
    assert ids(resolve([fixture(100, 138), fixture(101, 138, season=2027)])) == [78]


def test_only_available_groups_are_added_and_configured_data_is_unchanged():
    configured = [league()]
    payload = {"response": [fixture(100, 942)]}
    original_config, original_payload = deepcopy(configured), deepcopy(payload)
    result = resolve_default_leagues(payload, DAY, configured, NOW)
    assert ids(result) == [78, 942]
    assert configured == original_config and payload == original_payload
    result[0]["history_seasons"].append(2000)
    assert configured == original_config


def test_late_clock_rechecks_eligibility_and_never_revives_started_fixture():
    rows = [fixture(100, 138)]
    assert ids(resolve(rows)) == [78, 138]
    assert ids(resolve(rows, now=NOW + timedelta(hours=3))) == [78]


def test_existing_explicit_serie_c_config_is_preserved_without_duplicate():
    configured = [league(), league(138)]
    assert resolve([fixture(100, 138)], configured) == configured


def test_respects_maximum_40_leagues():
    configured = [league(index) for index in range(1, 40)]
    assert len(resolve([fixture(100, 138), fixture(101, 942)], configured)) == 40
    configured.append(league(40))
    assert resolve([fixture(100, 138)], configured) == configured


@pytest.mark.parametrize("payload", [{}, {"response": None}, {"response": {}}, None])
def test_missing_calendar_does_not_expand(payload):
    configured = [league()]
    assert resolve_default_leagues(payload, DAY, configured, NOW) == configured


def test_naive_clock_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve([], now=NOW.replace(tzinfo=None))
