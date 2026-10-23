"""Confirmed repair uses complete existing alternatives and never rewrites anchors."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.engine.grok_repair import repair
from goal_analysis.tipsters.catalog import CATALOG_VERSION, TIPSTER_BY_ID

NOW = datetime(2026, 10, 18, 9, tzinfo=UTC)


def card(profile, *fixtures, main=False, status="READY"):
    return {"profile_id": profile, "status": status, "date": "2026-10-18",
            "ticket_id" if main else "recommendation_id": profile + "-" + "-".join(fixtures),
            "combined_price": 2 ** len(fixtures), "publication_scope": "LIVE",
            "team_status": "ACTIVE", "team_plan_sha256": "plan", "playable": False,
            "legs": [{"fixture_id": fixture, "kickoff": (NOW + timedelta(hours=3)).isoformat(),
                      "market_key": "totals_2_5", "selection_key": "over",
                      "period": "FULL_TIME", "decimal_price": 2.0,
                      "bookmaker": "test", "region": "eu", "currency": "EUR"}
                     for fixture in fixtures]}


def option(profile, *fixtures, quality=1, main=True):
    result = {"quality": quality, "recommendation": card(profile, *fixtures)}
    if main:
        result["ticket"] = card(profile, *fixtures, main=True)
    return result


def report(*, profile="test", status="READY", main=True):
    rec = card(profile, "bad", status=status)
    rec.update(selected_for_portfolio=main, portfolio_ticket_id=f"{profile}-bad" if main else None)
    daily = card("daily223", "daily", main=True)
    data = {"date": "2026-10-18", "sentinel": {"never": "touch"},
            "recommendations": [deepcopy(daily), rec, card("anchor", "fixed")],
            "tickets": [daily], "profile_proposals": [deepcopy(daily)],
            "preview_tickets": [], "profiles": [{"profile_id": profile, "sentinel": "keep"}]}
    if main:
        ticket = card(profile, "bad", main=True, status=status)
        data["tickets"].append(ticket)
        data["profile_proposals"].append(deepcopy(ticket))
    return data


def concern(profile="test", fixture="bad"):
    return {"profile_id": profile, "fixture_id": fixture, "reason": "Confirmed absence",
            "source_urls": ["https://club.example/confirmed-news"], "concern_id": profile + fixture}


def row(data, profile="test", group="recommendations"):
    return next(item for item in data[group] if item["profile_id"] == profile)


def test_only_confirmed_profile_repaired_aliases_and_governance_preserved():
    original = report()
    options = {"test": [option("test", "bad", quality=10), option("test", "new")]}
    confirmation = [concern(), concern("daily223", "daily")]
    before = deepcopy((original, options, confirmation))
    result, audit = repair(original, options, confirmation, NOW)
    assert (original, options, confirmation) == before
    assert result is not original and result["sentinel"] is not original["sentinel"]
    assert audit["changed_profiles"] == ["test"] and not audit["unresolved"]
    assert audit["protected_daily"] == [concern("daily223", "daily")]
    assert row(result)["legs"][0]["fixture_id"] == "new"
    for group in ("tickets", "recommendations", "profile_proposals"):
        assert row(result, "daily223", group) == row(original, "daily223", group)
        selected, old = row(result, group=group), row(original, group=group)
        assert selected["legs"] == row(result)["legs"]
        for key in ("status", "publication_scope", "team_status", "team_plan_sha256", "playable"):
            assert selected[key] == old[key]
    assert row(result)["selected_for_portfolio"] is True
    assert row(result)["portfolio_ticket_id"] == row(result, group="tickets")["ticket_id"]
    assert row(result, group="profile_proposals")["ticket_id"] == row(result)["portfolio_ticket_id"]
    assert row(result, "anchor") == row(original, "anchor")
    assert result["profiles"] == original["profiles"]
    assert audit["changed"][0]["concerns"] == [concern()]
    assert audit["package_exposure"]["compliant"] and audit["ready_exposure"]["compliant"]


@pytest.mark.parametrize("status", ["DRAFT", "REVIEWED", "MODEL_READY"])
def test_original_status_never_promoted(status):
    original = report(status=status)
    result, audit = repair(original, {"test": [option("test", "new")]}, [concern()], NOW)
    assert audit["changed_profiles"] == ["test"]
    for group in ("recommendations", "tickets", "profile_proposals"):
        assert row(result, group=group)["status"] == status


def test_ready_original_cannot_promote_draft_alternative_through_status_preservation():
    original, alternative = report(), option("test", "new")
    alternative["ticket"]["status"] = "DRAFT"
    result, audit = repair(original, {"test": [alternative]}, [concern()], NOW)
    assert result == original and not audit["changed_profiles"] and audit["unresolved"]


def test_avoids_all_confirmed_fixtures_and_exact_duplicate_tries_next():
    original = report(main=False)
    row(original)["legs"].append(card("test", "bad2")["legs"][0])
    row(original)["combined_price"] = 4
    options = {"test": [option("test", "bad2", quality=9, main=False),
                        option("test", "fixed", quality=8, main=False),
                        option("test", "clean", quality=1, main=False)]}
    result, audit = repair(original, options, [concern(), concern(fixture="bad2")], NOW)
    assert row(result)["legs"][0]["fixture_id"] == "clean"
    assert audit["changed"][0]["avoided_fixture_ids"] == ["bad", "bad2"]


def test_same_fixture_different_market_still_obeys_whole_package_cap():
    original = report(main=False)  # three unique tickets => max one occurrence
    high = option("test", "fixed", quality=10, main=False)
    high["recommendation"]["legs"][0]["selection_key"] = "under"
    result, audit = repair(original, {"test": [high, option("test", "new", main=False)]},
                           [concern()], NOW)
    assert row(result)["legs"][0]["fixture_id"] == "new"
    assert audit["package_exposure"]["unique_ticket_count"] == 3


def test_main_cap_still_applies_when_larger_recommendation_pack_allows_overlap():
    original = report()
    original["recommendations"].extend([card("more1", "m1"), card("more2", "m2")])
    high = option("test", "daily", quality=10)
    for key in ("recommendation", "ticket"):
        high[key]["legs"][0]["selection_key"] = "under"
    result, _ = repair(original, {"test": [high, option("test", "new")]}, [concern()], NOW)
    assert row(result)["legs"][0]["fixture_id"] == "new"


@pytest.mark.parametrize("minutes", [0, 14, 15])
def test_replacement_strict_lockout_keeps_original(minutes):
    original = report()
    alternative = option("test", "new")
    for key in ("recommendation", "ticket"):
        alternative[key]["legs"][0]["kickoff"] = (NOW + timedelta(minutes=minutes)).isoformat()
    result, audit = repair(original, {"test": [alternative]}, [concern()], NOW)
    assert result == original and audit["unresolved"][0]["reason_code"] == "NO_ELIGIBLE_REPLACEMENT"


def test_original_lockout_refuses_rewrite_and_preserves_daily_in_all_groups():
    original = report()
    for group in ("tickets", "recommendations", "profile_proposals"):
        row(original, group=group)["legs"][0]["kickoff"] = (NOW + timedelta(minutes=15)).isoformat()
    original["preview_tickets"] = [card("DAILY_223", "protected-preview")]
    result, audit = repair(original, {"test": [option("test", "new")]},
                           [concern(), concern("DAILY_223", "protected-preview")], NOW)
    assert result == original and audit["unresolved"][0]["reason_code"] == "PREMATCH_LOCKOUT"
    assert audit["protected_daily"]


@pytest.mark.parametrize("damage", ["other_profile", "missing_ticket", "different_alias", "bad_quality"])
def test_injected_or_incomplete_options_never_replace_original(damage):
    original, alternative = report(), option("test", "new")
    if damage == "other_profile":
        alternative["ticket"]["profile_id"] = "injected"
    elif damage == "missing_ticket":
        alternative.pop("ticket")
    elif damage == "different_alias":
        alternative["ticket"]["legs"][0]["fixture_id"] = "different"
    else:
        alternative["quality"] = float("nan")
    result, audit = repair(original, {"test": [alternative]}, [concern()], NOW)
    assert result == original and not audit["changed_profiles"] and audit["unresolved"]


def test_live_persona_contract_not_caller_relaxed_rules():
    profile = next(key for key, value in TIPSTER_BY_ID.items() if value["min_legs"] >= 3)
    original = report(profile=profile, main=False)
    invalid = option(profile, "new", main=False)
    invalid["recommendation"].update(catalog_version=CATALOG_VERSION, minimum_legs=1,
                                     minimum_combined_price=1)
    result, audit = repair(original, {profile: [invalid]}, [concern(profile)], NOW)
    assert result == original and audit["unresolved"]


def test_sequential_repairs_keep_other_anchors_fixed_and_cannot_duplicate_first():
    original = report(main=False)
    original["recommendations"].append(card("second", "bad-second"))
    choices = {"second": [option("second", "first-choice", quality=4, main=False)],
               "test": [option("test", "first-choice", quality=10, main=False),
                        option("test", "second-choice", quality=1, main=False)]}
    result, audit = repair(original, choices, [concern(), concern("second", "bad-second")], NOW)
    assert audit["changed_profiles"] == ["second", "test"]
    assert row(result, "second")["legs"][0]["fixture_id"] == "first-choice"
    assert row(result)["legs"][0]["fixture_id"] == "second-choice"
    assert row(result, "anchor") == row(original, "anchor")


def test_no_replacement_and_unconfirmed_profile_leave_whole_report_identical():
    original = report()
    result, audit = repair(original, {}, [concern()], NOW)
    assert result == original and audit["unresolved"]
    unchanged, audit = repair(original, {"test": [option("test", "new")]}, [], NOW)
    assert unchanged == original and not audit["changed"] and not audit["unresolved"]


def test_changed_assignment_and_main_exposure_diagnostics_follow_replacement():
    original = report()
    original["diversification"] = {"assigned": {
        "test": {"fixture_ids": ["bad"], "quality": 2, "adjusted_quality": 2,
                 "own_best_quality": 2, "retained_own_quality_ratio": 1, "main_ticket": True},
        "anchor": {"fixture_ids": ["fixed"], "quality": .5, "adjusted_quality": .4}},
        "ready_exposure": {"old": "value"}}
    result, audit = repair(original, {"test": [option("test", "new")]}, [concern()], NOW)
    assigned = result["diversification"]["assigned"]
    assert assigned["test"]["fixture_ids"] == ["new"]
    assert assigned["test"]["quality"] == assigned["test"]["adjusted_quality"] == 1
    assert assigned["test"]["retained_own_quality_ratio"] == .5
    assert assigned["anchor"] == original["diversification"]["assigned"]["anchor"]
    assert result["diversification"]["ready_exposure"] == audit["ready_exposure"]
    assert "bad" not in audit["ready_exposure"]["fixture_ticket_counts"]


def test_existing_soft_overlap_ranking_prefers_stronger_adjusted_option():
    original = report(main=False)
    original["recommendations"][2] = card("anchor", "fixed", "shared", "third")
    original["recommendations"].append(card("other", "other"))
    choices = {"test": [option("test", "fixed", "shared", "new", quality=1, main=False),
                        option("test", "a", "b", "c", quality=.8, main=False)]}
    result, audit = repair(original, choices, [concern()], NOW)
    assert {leg["fixture_id"] for leg in row(result)["legs"]} == {"a", "b", "c"}
    assert audit["changed"][0]["quality"] == .8


def test_naive_clock_rejected_before_work():
    with pytest.raises(ValueError, match="aware"):
        repair(report(), {}, [], NOW.replace(tzinfo=None))
