"""Fresh alternatives respect the immutable DAILY and the final publication boundary."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs.protected_regeneration import (
    GROUPS,
    compose_protected_daily,
    validate_protected_daily,
)
from goal_analysis.team.routing import generation_plan
from goal_analysis.tipsters.catalog import CATALOG_VERSION

NOW = datetime(2026, 10, 18, 8, tzinfo=UTC)


def card(profile, fixtures, *, ticket=True):
    result = {
        "profile_id": profile, "profile_name": profile, "date": "2026-10-18", "status": "READY",
        "created_at": (NOW - timedelta(hours=2)).isoformat(), "publication_scope": "LIVE",
        "quality_warnings": [], "combined_price": 3 ** len(fixtures),
        "legs": [{"fixture_id": fixture, "candidate_id": profile + fixture,
                  "kickoff": (NOW + timedelta(hours=5)).isoformat(), "decimal_price": 3,
                  "market_key": "totals_2_5", "selection_key": "over", "period": "FULL_TIME",
                  "bookmaker": "book", "currency": "EUR", "region": "eu"}
                 for fixture in fixtures],
    }
    result["ticket_id" if ticket else "recommendation_id"] = profile + "-" + "-".join(fixtures)
    return result


def case():
    protected = card("daily223", ["A", "B", "C"])
    protected.update(strategy_version="daily-original", strategy_manifest_sha256="original",
                     immutable_marker={"retain": [1, 2, 3]})
    source = {
        "run_id": "frozen-r2", "date": "2026-10-18",
        "observed_at": (NOW - timedelta(hours=2)).isoformat(),
        "tickets": [deepcopy(protected)], "recommendations": [deepcopy(protected)],
        "profile_proposals": [deepcopy(protected)], "preview_tickets": [deepcopy(protected)],
        "profiles": [{"profile_id": "daily223", "published": False, "sentinel": "original"}],
        "strategy_manifest": {"manifest_sha256": "original"},
    }
    alternative = card("merlin", ["D", "E", "F"])
    duplicate = card("merlin", ["A", "B", "C"])
    regenerated_daily = card("daily223", ["X", "Y", "Z"])
    fresh = {
        "run_id": "new-run", "date": "2026-10-18", "construction_status": "COMPLETE",
        "observed_at": (NOW - timedelta(hours=1)).isoformat(), "finished_at": NOW.isoformat(),
        "settings": {"enabled_profiles": ["daily223", "merlin"], "target_ticket_count": 2,
                     "max_ticket_count": 5},
        "tickets": [regenerated_daily, deepcopy(duplicate)],
        "recommendations": [deepcopy(regenerated_daily), deepcopy(duplicate)],
        "profile_proposals": [], "preview_tickets": [],
        "profiles": [{"profile_id": "daily223", "published": True},
                     {"profile_id": "merlin", "published": True}],
        "strategy_manifest": {"manifest_sha256": "fresh"},
        "funnel": {"tickets": 2, "recommendations": 2},
        "fixtures": [{"fixture_id": fixture, "fresh": True, "provider_status": "NS",
                      "kickoff": (NOW + timedelta(hours=5)).isoformat()}
                     for fixture in ("A", "B", "C", "D", "E", "F")],
        "numeric_models": {"gauss": {"unmodified": True}},
    }
    for group in ("tickets", "recommendations"):
        fresh[group][1].update(strategy_version="merlin-fresh", strategy_manifest_sha256="fresh")
    options = {"merlin": [
        {"quality": 1.0, "scoring": {"kind": "NORMALIZED"},
         "ticket": deepcopy(duplicate), "recommendation": deepcopy(duplicate)},
        {"quality": 0.9, "scoring": {"kind": "NORMALIZED"},
         "ticket": deepcopy(alternative), "recommendation": deepcopy(alternative)},
    ]}
    return fresh, source, options


def compose(arguments, **kwargs):
    fresh, source, options = arguments
    return compose_protected_daily(
        fresh, source, options, now=NOW, expected_source_run_id="frozen-r2",
        expected_source_report_sha256=canonical_sha256(source), **kwargs,
    )


def daily(report):
    return {group: [row for row in report.get(group, []) if row["profile_id"] == "daily223"]
            for group in GROUPS}


def validate(report, source, now=NOW):
    return validate_protected_daily(report, source, now=now, expected_source_run_id="frozen-r2",
                                     expected_source_report_sha256=canonical_sha256(source))


def test_reallocation_uses_original_daily_anchors_and_preserves_every_protected_object():
    arguments = case()
    original = deepcopy(arguments)
    result = compose(arguments)
    assert arguments == original
    assert daily(result) == daily(arguments[1])
    selected = next(card for card in result["recommendations"] if card["profile_id"] == "merlin")
    assert [leg["fixture_id"] for leg in selected["legs"]] == ["D", "E", "F"]
    assert selected["strategy_version"] == "merlin-fresh"
    assert selected["strategy_manifest_sha256"] == "fresh"
    assert result["numeric_models"] == arguments[0]["numeric_models"]
    assert result["fixtures"] == arguments[0]["fixtures"]
    assert result["protected_regeneration"]["daily223_exactly_preserved"]
    assert result["protected_regeneration"]["ready_exposure"]["compliant"]
    assert result["protected_regeneration"]["additional_provider_requests"] == 0
    unhashed = deepcopy(result)
    supplied = unhashed.pop("report_sha256")
    assert canonical_sha256(unhashed) == supplied


def test_current_team_routing_cannot_relabel_daily_or_lose_original_profile_flags():
    arguments = case()
    arguments[0]["team"] = generation_plan(["daily223", "merlin"])
    result = compose(arguments)
    assert daily(result) == daily(arguments[1])
    assert result["profiles"][0]["published"] is False
    merlin = next(row for row in result["recommendations"] if row["profile_id"] == "merlin")
    assert merlin["team_plan_sha256"] == arguments[0]["team"]["plan_sha256"]


def test_team_suspension_conflicting_with_frozen_daily_blocks_whole_operation():
    arguments = case()
    arguments[0]["team"] = generation_plan(["daily223", "merlin"], {
        "members": [{"profile_id": "daily223", "status": "SUSPENDED"}],
    })
    with pytest.raises(ValueError, match="team plan conflicts"):
        compose(arguments)


@pytest.mark.parametrize("group", GROUPS)
def test_post_review_validation_detects_any_protected_metadata_mutation(group):
    arguments = case()
    result = compose(arguments)
    result[group][0]["changed"] = True
    with pytest.raises(ValueError, match="Protected DAILY objects changed"):
        validate(result, arguments[1])


@pytest.mark.parametrize("minutes", [-1, 0, 14, 15])
def test_expired_protected_daily_stops_whole_regeneration_without_deleting_it(minutes):
    arguments = case()
    for rows in daily(arguments[1]).values():
        for row in rows:
            for leg in row.get("legs", []):
                leg["kickoff"] = (NOW + timedelta(minutes=minutes)).isoformat()
    before = deepcopy(arguments)
    with pytest.raises(ValueError, match="Protected DAILY reaches the 15-minute"):
        compose(arguments)
    assert arguments == before


def test_non_daily_lockout_is_pruned_without_shortening_or_replacing_daily():
    arguments = case()
    for option in arguments[2]["merlin"]:
        for group in ("ticket", "recommendation"):
            option[group]["legs"][0]["kickoff"] = (NOW + timedelta(minutes=14)).isoformat()
    result = compose(arguments)
    assert daily(result) == daily(arguments[1])
    assert [row["profile_id"] for row in result["tickets"]] == ["daily223"]
    merlin = next(row for row in result["recommendations"] if row["profile_id"] == "merlin")
    assert merlin["legs"] == []


def test_second_validation_detects_publication_race_after_composition():
    arguments = case()
    result = compose(arguments)
    with pytest.raises(ValueError, match="15-minute"):
        validate(result, arguments[1], now=NOW + timedelta(hours=4, minutes=45))


def test_source_hash_and_source_run_are_explicit_preconditions():
    fresh, source, options = case()
    with pytest.raises(ValueError, match="source report hash"):
        compose_protected_daily(fresh, source, options, now=NOW,
                                 expected_source_run_id="frozen-r2",
                                 expected_source_report_sha256="0" * 64)
    with pytest.raises(ValueError, match="source run identity"):
        compose_protected_daily(fresh, source, options, now=NOW,
                                 expected_source_run_id="other")


def test_existing_kronikas_contract_cannot_be_relaxed_after_grok_review():
    arguments = case()
    result = compose(arguments)
    # Three 2.0 legs total8, below the existing Kronikas10 minimum.
    kronikas = card("kronikas", ["K1", "K2", "K3"])
    kronikas["combined_price"] = 8
    for leg in kronikas["legs"]:
        leg["decimal_price"] = 2
    result["recommendations"].append(kronikas)
    with pytest.raises(ValueError, match="Fixed construction contract violated: kronikas"):
        validate(result, arguments[1])


def test_existing_daily_cap_remains_enforced_after_later_package_mutation():
    arguments = case()
    result = compose(arguments)
    result["tickets"][1]["legs"][0]["fixture_id"] = "A"
    with pytest.raises(ValueError, match="fixture exposure limit"):
        validate(result, arguments[1])


@pytest.mark.parametrize("failure", ["DATA_BLOCKED", "UNAVAILABLE", "ERROR", "TEAM_PAUSED"])
def test_failed_fresh_run_cannot_masquerade_as_successful_daily_only_revision(failure):
    arguments = case()
    arguments[0]["construction_status"] = failure
    with pytest.raises(ValueError, match="Failed fresh collection"):
        compose(arguments)


def test_old_review_is_invalidated_after_package_recomposition():
    arguments = case()
    arguments[0]["grok_review"] = {"status": "COMPLETE", "review_id": "wrong-package"}
    result = compose(arguments)
    assert result["grok_review"]["status"] == "PENDING_RECOMPOSED_PACKAGE_REVIEW"
    assert "review_id" not in result["grok_review"]


def test_same_day_and_genuinely_new_run_required():
    arguments = case()
    arguments[0]["date"] = "2026-10-19"
    with pytest.raises(ValueError, match="same day"):
        compose(arguments)
    arguments = case()
    arguments[0]["run_id"] = "frozen-r2"
    with pytest.raises(ValueError, match="new immutable run id"):
        compose(arguments)
    arguments = case()
    arguments[0]["observed_at"] = arguments[1]["observed_at"]
    with pytest.raises(ValueError, match="newer than the source"):
        compose(arguments)


@pytest.mark.parametrize("change,error", [
    ("missing", "missing from the fresh calendar"),
    ("rescheduled", "kickoff changed"),
    ("cancelled", "not confirmed scheduled"),
])
def test_original_daily_cannot_be_carried_forward_against_contradicting_fresh_fixtures(change, error):
    arguments = case()
    if change == "missing":
        arguments[0]["fixtures"].pop(0)
    elif change == "rescheduled":
        arguments[0]["fixtures"][0]["kickoff"] = (NOW + timedelta(hours=7)).isoformat()
    else:
        arguments[0]["fixtures"][0]["provider_status"] = "CANC"
    before = deepcopy(arguments)
    with pytest.raises(ValueError, match=error):
        compose(arguments)
    assert arguments == before


def test_configured_nonclub_odds_bounds_are_applied_before_allocation_without_changing_daily():
    arguments = case()
    arguments[0]["settings"].update(target_min_odds=28, target_max_odds=40)
    result = compose(arguments)
    assert daily(result) == daily(arguments[1])
    assert result["tickets"][0]["combined_price"] == 27  # DAILY is exempt from this policy.
    assert [row["profile_id"] for row in result["tickets"]] == ["daily223"]
    merlin = next(row for row in result["recommendations"] if row["profile_id"] == "merlin")
    assert not merlin["legs"]


def test_low_generic_combination_cannot_reappear_in_post_grok_repair():
    arguments = case()
    result = compose(arguments)
    result["recommendations"].append(card("gauss", ["G1", "G2"]))  # 3*3=9.
    with pytest.raises(ValueError, match="Publication odds contract violated: gauss"):
        validate(result, arguments[1])


def test_fixed_club_band_below_ten_is_kept_instead_of_being_replaced_by_generic_bounds():
    arguments = case()
    result = compose(arguments)
    club = card("rovid_lotti", ["L1", "L2", "L3"])
    club.update(catalog_version=CATALOG_VERSION, combined_price=8)
    for leg in club["legs"]:
        leg["decimal_price"] = 2
    result["recommendations"].append(club)
    assert validate(result, arguments[1])["daily223_exactly_preserved"]
