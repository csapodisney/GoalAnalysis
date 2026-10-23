from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents.motivation_expert import (
    analyze_motivation,
    compact_motivation_context,
)

NOW = datetime(2026, 10, 18, 10, tzinfo=UTC)
PAST = NOW - timedelta(hours=1)
FIXTURE = {"fixture_id": "api_football:123", "api_football_home_team_id": "1",
           "api_football_away_team_id": "2", "api_football_league_id": "78", "season": 2026}


def standing():
    return {"fixture_id": FIXTURE["fixture_id"], "team_id": "1", "competition_id": "78",
            "season": 2026, "points": 40, "games_remaining": 5,
            "observed_at": PAST.isoformat(), "source_url": "https://league.example/table",
            "rules": {"verified": True, "observed_at": PAST.isoformat(), "points_per_win": 3,
                      "source_url": "https://league.example/rules", "boundaries": [{
                          "objective": "EUROPE_RACE", "target_points": 48,
                          "max_challenger_points": 60, "complete_contender_coverage": True}]}}


def claim(text="The coach says several players may be rested."):
    post_id = (int(PAST.timestamp() * 1000) - 1288834974657) << 22
    return {"fixture_id": FIXTURE["fixture_id"], "claim_id": "c1", "kind": "ROTATION",
            "claim": text, "author": "ClubNews", "source_url": f"https://x.com/ClubNews/status/{post_id}",
            "published_at": PAST.isoformat(), "fetched_at": (PAST + timedelta(minutes=1)).isoformat(),
            "observed_at": (PAST + timedelta(minutes=2)).isoformat(), "content_status": "UNVERIFIED",
            "provenance_status": "CITATION_AND_TIMESTAMP_CHECKED"}


def analyze(**kwargs):
    return analyze_motivation([FIXTURE], as_of=NOW, **kwargs)


def test_missing_data_is_unknown_not_unmotivated_and_inputs_are_unchanged():
    original = deepcopy(FIXTURE)
    result = analyze()
    assert FIXTURE == original
    assert result["fixtures"][0]["stakes"] == ["UNKNOWN"]
    assert result["fixtures"][0]["probability_adjustment"] == 0
    assert result["advisory_only"] is True
    assert result["counts"] == {"fixtures": 1, "with_standings": 0, "with_claims": 0, "review_required": 0}


@pytest.mark.parametrize(("points", "games", "status"), [
    (40, 5, "EUROPE_RACE"), (61, 0, "QUALIFIED"), (40, 2, "ELIMINATED"),
    (48, 0, "UNKNOWN"), (60, 1, "UNKNOWN"),
])
def test_stakes_use_strict_mathematical_boundaries_not_tiebreak_guesses(points, games, status):
    row = standing()
    row.update(points=points, games_remaining=games)
    result = analyze(standings=[row])["fixtures"][0]
    evidence = result["standing_evidence"][0]
    assert result["stakes"] == [status]
    assert evidence["point_gap"] == 48 - points
    assert evidence["maximum_final_points"] == points + 3 * games
    assert evidence["max_points_available"] == 3 * games
    assert evidence["tiebreak_status"] == "UNKNOWN"


@pytest.mark.parametrize("objective", ["TITLE_RACE", "RELEGATION_RACE"])
def test_distinct_objectives_require_explicit_rules(objective):
    row = standing()
    row["rules"]["boundaries"][0]["objective"] = objective
    assert analyze(standings=[row])["fixtures"][0]["stakes"] == [objective]


@pytest.mark.parametrize("change", [
    {"competition_id": "79"}, {"season": 2025}, {"team_id": "99"}, {"points": True},
    {"games_remaining": -1}, {"observed_at": (NOW + timedelta(seconds=1)).isoformat()},
    {"observed_at": "2026-10-18T09:00:00"}, {"source_url": "file:///secret"},
])
def test_invalid_standings_do_not_invent_motivation(change):
    row = standing()
    row.update(change)
    result = analyze(standings=[row])
    assert result["fixtures"][0]["stakes"] == ["UNKNOWN"]
    assert result["skipped"] == {"INVALID_OR_UNAVAILABLE_STANDINGS": 1}


@pytest.mark.parametrize("mutate", [
    lambda rules: rules.update(verified=False),
    lambda rules: rules.update(observed_at=(NOW + timedelta(seconds=1)).isoformat()),
    lambda rules: rules["boundaries"][0].update(complete_contender_coverage=False),
    lambda rules: rules["boundaries"][0].update(max_challenger_points=30),
    lambda rules: rules["boundaries"][0].update(objective="VERY_MOTIVATED"),
])
def test_missing_rule_proof_or_incomplete_boundary_rejected(mutate):
    row = standing()
    mutate(row["rules"])
    assert analyze(standings=[row])["fixtures"][0]["stakes"] == ["UNKNOWN"]


def test_newsstore_format_and_accepted_grok_claims_are_supported_with_no_uplift():
    row = claim()
    row["origin_claim_id"] = row.pop("claim_id")
    row["source_fetched_at"] = row.pop("fetched_at")
    before = deepcopy(row)
    result = analyze(claims=[row])
    assert row == before
    evidence = result["fixtures"][0]["human_factors"][0]
    assert evidence["status"] == "SOURCED_CLAIM_UNVERIFIED"
    assert evidence["source_url"] == row["source_url"]
    assert result["fixtures"][0]["probability_adjustment"] == 0


@pytest.mark.parametrize("change", [
    {"provenance_status": "UNVERIFIED"}, {"content_status": "TRUE"}, {"author": "Other"},
    {"published_at": (PAST + timedelta(seconds=2)).isoformat()},
    {"fetched_at": (NOW + timedelta(seconds=1)).isoformat()},
    {"observed_at": (NOW + timedelta(seconds=1)).isoformat()},
    {"observed_at": PAST.isoformat()},
    {"source_url": "https://x.com.evil.example/ClubNews/status/123"}, {"kind": "MOTIVATED"},
])
def test_unproven_future_or_bad_news_is_not_used(change):
    row = claim()
    row.update(change)
    result = analyze(claims=[row])
    assert result["fixtures"][0]["human_factors"] == []
    assert result["skipped"] == {"INVALID_OR_UNAVAILABLE_CLAIM": 1}


def test_potentially_conflicting_claims_are_retained_and_flagged_for_grok():
    first = claim("The coach plans to rotate the starting eleven.")
    second = {**claim("The coach plans the strongest starting eleven."), "claim_id": "c2"}
    result = analyze(claims=[first, second, first])
    item = result["fixtures"][0]
    assert len(item["human_factors"]) == 2
    assert item["review_required"] is True
    compact = compact_motivation_context(result, max_claims_per_fixture=1)
    assert compact["fixtures"][0]["claims_omitted"] == 1
    assert "DAILY_223 unchanged" in compact["policy"]
    assert compact["fixtures"][0]["claims"][0]["status"] == "SOURCED_CLAIM_UNVERIFIED"


def test_wrong_fixture_is_ignored_and_empty_report_is_supported():
    row = claim()
    row["fixture_id"] = "other"
    assert analyze(claims=[row])["counts"]["with_claims"] == 0
    assert analyze_motivation([], as_of=NOW)["fixtures"] == []
    with pytest.raises(ValueError, match="timezone-aware"):
        analyze_motivation([], as_of=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        compact_motivation_context(analyze(), max_claims_per_fixture=True)
