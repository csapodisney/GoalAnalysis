"""Saved-input correction preserves protected cards and publication boundaries."""

import importlib.util
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rebuild-arthur-club.py"
SPEC = importlib.util.spec_from_file_location("club_rebuild_script", SCRIPT)
REBUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REBUILD)
NOW = datetime(2026, 10, 17, 8, tzinfo=UTC)


@pytest.fixture(autouse=True)
def prevalidated_selector_options(monkeypatch):
    """These operational tests supply selector outputs; catalog contracts have separate tests."""
    monkeypatch.setattr(REBUILD, "_eligible", lambda item, now, _day:
                        REBUILD.allows_new_ticket(item["recommendation"], now))


def card(profile, *fixtures, price=12):
    return {"profile_id": profile, "profile_name": profile, "status": "READY",
            "date": "2026-10-17", "combined_price": price, "bookmaker": "test",
            "legs": [{"fixture_id": fixture, "kickoff": (NOW + timedelta(hours=5)).isoformat(),
                      "market_key": "totals_2_5", "selection_key": "over", "period": "FULL_TIME"}
                     for fixture in fixtures]}


def option(item, quality=0.9):
    return {"recommendation": item, "quality": quality}


def test_rebuild_preserves_nonclub_json_and_source_review(monkeypatch):
    club = min(REBUILD.CLUB)
    old = card(club, "old")
    daily = {**card("daily223", "a", "b", "c"), "strategy_version": "protected-v1"}
    legacy = card("merlin", "d", "e")
    source = {"run_id": "source", "date": "2026-10-17", "observed_at": (NOW - timedelta(hours=2)).isoformat(),
              "settings": {"enabled_profiles": ["daily223", "merlin", club]},
              "astra": {"status": "COMPLETE", "reviewed_at": (NOW - timedelta(hours=1)).isoformat()},
              "tickets": [daily], "preview_tickets": [], "profile_proposals": [legacy],
              "recommendations": [daily, legacy, old],
              "profiles": [{"profile_id": "daily223", "published": True},
                           {"profile_id": club, "published": True}]}
    unchanged = deepcopy(source)
    replacement = card(club, "f", "g", "h")

    def generate(_enriched, _models, _settings, _now, *, diversity_options):
        diversity_options[club] = [option(replacement)]
        return [deepcopy(replacement)]

    monkeypatch.setattr(REBUILD, "build_tipster_recommendations", generate)
    monkeypatch.setattr(REBUILD, "attach_strategy_provenance", lambda scoped, _settings: {"new": True})
    artifacts = {"candidate_analysis": {"candidates": []}, "numeric_models": {},
                 "fixtures": {"records": []}}
    result = REBUILD.rebuild(source, artifacts, NOW, "new")
    assert source == unchanged
    assert REBUILD.preserved(source) == REBUILD.preserved(result)
    assert result["recommendations"][0] == daily
    assert result["astra"]["reviewed_at"] == source["astra"]["reviewed_at"]
    assert result["astra"]["status"] == source["astra"]["status"]
    assert result["astra"]["scope"] == "INHERITED_SOURCE_REVIEW"
    assert result["astra"]["fresh_for_club_rebuild"] is False
    assert "nem készült új webes ellenőrzés" in result["astra"]["message"]
    assert result["club_rebuild"]["source_observed_at"] == source["observed_at"]
    assert result["club_rebuild"]["provider_requests"] == 0
    assert result["club_rebuild"]["astra_review_status"] == "INHERITED_SOURCE_REVIEW_NOT_REFRESHED"
    assert result["recommendations"][-1]["legs"] == replacement["legs"]


def test_soft_overlap_keeps_partial_overlap_and_avoids_exact_duplicate():
    first, second = sorted(REBUILD.CLUB)[:2]
    anchor = card("daily223", "a", "b", "c")
    duplicate = card(first, "a", "b", "c")
    partial = card(first, "a", "d", "e")
    other = card(second, "f", "g", "h")
    # Four fixed nonduplicate cards make one allowed shared fixture meaningful.
    anchors = [anchor, card("merlin", "j", "k"), card("gauss", "l", "m")]
    result, audit = REBUILD.select_clubs([duplicate, other],
        {first: [option(duplicate), option(partial, 0.8)], second: [option(other)]},
        anchors, NOW, "2026-10-17")
    assert result[0]["legs"] == partial["legs"]
    assert audit["compliant"] and audit["maximum_fixture_count"] == 2


def test_excessive_final_exposure_cannot_publish():
    club = min(REBUILD.CLUB)
    new = card(club, "shared", "c")
    with pytest.raises(ValueError, match="50%"):
        REBUILD.select_clubs([new], {club: [option(new)]},
                             [card("daily223", "shared", "a"), card("merlin", "shared", "b")],
                             NOW, "2026-10-17")


@pytest.mark.parametrize("shared_fixture", [False, True])
def test_final_exposure_excludes_suspended_cards(monkeypatch, shared_fixture):
    active, suspended = sorted(REBUILD.CLUB)[:2]
    daily = card("daily223", "a")
    legacy = card("merlin", "a" if shared_fixture else "b", "c")
    replacements = [card(active, "d", "e"), card(suspended, "f", "g")]
    source = {"run_id": "source", "date": "2026-10-17", "observed_at": NOW.isoformat(),
              "settings": {"enabled_profiles": ["daily223", "merlin", active, suspended]},
              "astra": {"status": "COMPLETE", "reviewed_at": NOW.isoformat()},
              "team": {"test": True}, "tickets": [daily],
              "recommendations": [daily, legacy, *deepcopy(replacements)]}

    def generate(_enriched, _models, _settings, _now, *, diversity_options):
        for item in replacements:
            diversity_options[item["profile_id"]] = [option(item)]
        return deepcopy(replacements)

    def suspend(scoped, _plan):
        for item in scoped["recommendations"]:
            if item["profile_id"] == suspended:
                item.update(legs=[], status="SUSPENDED", combined_price=None)

    monkeypatch.setattr(REBUILD, "build_tipster_recommendations", generate)
    monkeypatch.setattr(REBUILD, "apply_publication_policy", suspend)
    monkeypatch.setattr(REBUILD, "attach_strategy_provenance", lambda scoped, _settings: {})
    artifacts = {"candidate_analysis": {"candidates": []}, "numeric_models": {},
                 "fixtures": {"records": []}}
    if shared_fixture:
        with pytest.raises(ValueError, match="50%"):
            REBUILD.rebuild(source, artifacts, NOW, "new")
    else:
        result = REBUILD.rebuild(source, artifacts, NOW, "new")
        assert result["club_rebuild"]["exposure"]["package_count"] == 3
        assert result["tipster_club"]["published_count"] == 1


@pytest.mark.parametrize("minutes", [0, 14, 15])
def test_protected_card_in_lockout_stops_whole_correction(minutes):
    protected = card("daily223", "a")
    protected["legs"][0]["kickoff"] = (NOW + timedelta(minutes=minutes)).isoformat()
    with pytest.raises(ValueError, match="15-minute"):
        REBUILD.validate_boundary({"date": "2026-10-17", "recommendations": [protected]}, NOW)
