"""A targeted correction never rewrites DAILY_223 or unrelated profile objects."""

import importlib.util
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rebuild-arthur-kronikas.py"
SPEC = importlib.util.spec_from_file_location("kronikas_rebuild_script", SCRIPT)
REBUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REBUILD)
NOW = datetime(2026, 10, 17, 8, tzinfo=UTC)


def card(profile, *fixtures, price=12):
    return {"profile_id": profile, "profile_name": profile, "status": "DRAFT",
            "date": "2026-10-17", "combined_price": price, "bookmaker": "test",
            "ticket_id": "ticket-" + profile, "created_at": NOW.isoformat(),
            "legs": [{"fixture_id": fixture, "candidate_id": fixture,
                      "kickoff": (NOW + timedelta(hours=5)).isoformat(), "decimal_price": 2,
                      "market_key": "totals_2_5", "selection_key": "over", "period": "FULL_TIME"}
                     for fixture in fixtures]}


def option(item, quality=0.9):
    return {"ticket": deepcopy(item), "recommendation": deepcopy(item), "quality": quality}


@pytest.fixture
def inputs(monkeypatch):
    daily = {**card("daily223", "a", "b", "c"), "strategy_version": "protected-v1"}
    legacy = card("merlin", "d", "e", "f")
    old = card("kronikas", "old", price=1.11)
    source = {"run_id": "source", "date": "2026-10-17",
              "observed_at": (NOW - timedelta(hours=2)).isoformat(),
              "settings": {"enabled_profiles": ["daily223", "merlin", "kronikas"]},
              "astra": {"status": "COMPLETE", "reviewed_at": (NOW - timedelta(hours=1)).isoformat()},
              **{group: deepcopy([daily, legacy, old]) for group in REBUILD.COMMON.PRESERVED_GROUPS}}
    source["preview_tickets"] = [deepcopy(legacy)]
    replacement = card("kronikas", "g", "h", "i")
    artifacts = {"candidate_analysis": {"observed_at": source["observed_at"], "candidates": []},
                 "fixtures": {"records": []}, "numeric_models": {}}

    def generate(enriched, settings, *, astra_review, allowed_profiles, diversity_options):
        assert allowed_profiles == ["kronikas"]
        assert enriched["observed_at"] == NOW.isoformat()
        assert settings["enabled_profiles"] == ["kronikas"]
        diversity_options["kronikas"] = [option(replacement)]
        return {"profiles": [{"profile_id": "kronikas", "published": True}]}

    monkeypatch.setattr(REBUILD, "build_portfolio", generate)
    monkeypatch.setattr(REBUILD, "build_recommendations", lambda *a: [deepcopy(replacement)])
    monkeypatch.setattr(REBUILD, "_base_diversity_recommendations", lambda *a: None)
    monkeypatch.setattr(REBUILD, "_eligible", lambda item, now, day:
                        REBUILD.COMMON.allows_new_ticket(item["recommendation"], now))
    monkeypatch.setattr(REBUILD.COMMON, "attach_strategy_provenance", lambda *a: {})
    return source, artifacts, replacement


def test_rebuild_preserves_every_other_profile_and_source_timestamps(inputs):
    source, artifacts, replacement = inputs
    original_source, original_artifacts = deepcopy(source), deepcopy(artifacts)
    result = REBUILD.rebuild(source, artifacts, NOW, "replacement")
    assert source == original_source and artifacts == original_artifacts
    assert REBUILD.preserved(result) == REBUILD.preserved(source)
    assert result["recommendations"][-1]["legs"] == replacement["legs"]
    assert result["recommendations"][-1]["selected_for_portfolio"]
    assert result["recommendations"][-1]["status"] == "DRAFT"
    assert result["astra"]["reviewed_at"] == source["astra"]["reviewed_at"]
    assert result["astra"]["scope"] == "INHERITED_SOURCE_REVIEW"
    assert result["astra"]["fresh_for_kronikas_rebuild"] is False
    assert result["kronikas_rebuild"]["source_candidate_observed_at"] == artifacts["candidate_analysis"]["observed_at"]
    assert result["usage"]["openai"]["requests"] == 0
    assert result["preview_tickets"] == source["preview_tickets"]


def test_no_alternative_abstains_without_fallback_single(inputs, monkeypatch):
    source, artifacts, _replacement = inputs
    monkeypatch.setattr(REBUILD, "select_kronikas", lambda *a: None)
    result = REBUILD.rebuild(source, artifacts, NOW, "replacement")
    assert result["recommendations"][-1]["status"] == "ABSTAINED"
    assert result["recommendations"][-1]["legs"] == []
    assert not any(item["profile_id"] == "kronikas" for item in result["tickets"])
    assert REBUILD.preserved(source) == REBUILD.preserved(result)


def test_selector_avoids_duplicate_and_keeps_partial_overlap(inputs):
    anchors = [card("daily223", "a", "b", "c"), card("merlin", "d", "e", "f")]
    duplicate = card("kronikas", "a", "b", "c")
    partial = card("kronikas", "a", "g", "h")
    selected = REBUILD.select_kronikas([option(duplicate), option(partial, 0.8)], anchors,
                                      NOW, "2026-10-17")
    # Three tickets allow only one occurrence per fixture; add a fourth anchor
    # so the 50% cap can permit one justified shared match.
    assert selected is None
    selected = REBUILD.select_kronikas([option(duplicate), option(partial, 0.8)],
                                      [*anchors, card("gauss", "j", "k", "l")], NOW, "2026-10-17")
    assert selected["recommendation"]["legs"] == partial["legs"]


def test_selector_prefers_fully_reviewed_valid_ticket_over_unreviewed_option(inputs):
    anchors = [card("daily223", "a", "b", "c"), card("merlin", "d", "e", "f")]
    draft = option(card("kronikas", "g", "h", "i"), 0.9)
    reviewed = option(card("kronikas", "j", "k", "l"), 0.7)
    reviewed["ticket"]["status"] = "READY"
    reviewed["recommendation"]["status"] = "REVIEWED"
    result = REBUILD.select_kronikas([draft, reviewed], anchors, NOW, "2026-10-17")
    assert result["recommendation"]["status"] == "REVIEWED"
    assert result["quality"] == 0.7


def test_selector_prefers_main_safe_reviewed_alternative(inputs):
    main = card("daily223", "a", "b", "c")
    main["status"] = "READY"
    anchors = [main, card("merlin", "d", "e", "f"), card("gauss", "n", "o", "p")]
    shared = option(card("kronikas", "a", "g", "h"), 0.9)
    separate = option(card("kronikas", "j", "k", "l"), 0.7)
    for item in (shared, separate):
        item["ticket"]["status"] = "READY"
        item["recommendation"]["status"] = "REVIEWED"
    result = REBUILD.select_kronikas([shared, separate], anchors, NOW, "2026-10-17", [main])
    assert result["quality"] == 0.7


def test_main_exposure_limit_keeps_reviewed_card_without_rewriting_daily(inputs, monkeypatch):
    source, artifacts, _replacement = inputs
    source["tickets"] = [deepcopy(source["tickets"][0]), deepcopy(source["tickets"][-1])]
    source["tickets"][0]["status"] = "READY"
    source["recommendations"].insert(1, card("gauss", "n", "o", "p"))
    source["diversification"] = {"assigned": {}, "abstained_profiles": {}, "ready_exposure": {"stale": True}}
    shared = option(card("kronikas", "a", "g", "h"))
    shared["ticket"]["status"] = "READY"
    shared["recommendation"]["status"] = "REVIEWED"
    shared.update(adjusted_quality=0.9, similarity={})
    monkeypatch.setattr(REBUILD, "select_kronikas", lambda *a: shared)
    result = REBUILD.rebuild(source, artifacts, NOW, "replacement")
    assert REBUILD.preserved(result) == REBUILD.preserved(source)
    assert len(result["tickets"]) == 1
    assert result["recommendations"][-1]["status"] == "REVIEWED"
    assert result["recommendations"][-1]["selected_for_portfolio"] is False
    assert result["recommendations"][-1]["portfolio_ticket_id"] is None
    assert result["profile_proposals"][-1]["profile_id"] == "kronikas"
    assert result["diversification"]["ready_exposure"]["compliant"]
    assert result["diversification"]["ready_exposure"]["ready_ticket_count"] == 1
    assert result["funnel"]["tickets"] == 1
    assert result["construction_status"] == "PARTIAL"
    assert result["kronikas_rebuild"]["main_slot_reason"] == "READY_MAIN_PACKAGE_EXPOSURE_LIMIT"


@pytest.mark.parametrize("minutes", [0, 14, 15])
def test_lockout_of_immutable_card_stops_correction(inputs, minutes):
    source, artifacts, _replacement = inputs
    source["recommendations"][0]["legs"][0]["kickoff"] = (NOW + timedelta(minutes=minutes)).isoformat()
    with pytest.raises(ValueError, match="15-minute"):
        REBUILD.rebuild(source, artifacts, NOW, "replacement")


def review_input():
    candidate = {"candidate_id": "g", "market_key": "totals_2_5", "selection_key": "over",
                 "period": "FULL_TIME", "evidence": [{"evidence_id": "evidence-g"}]}
    source = {"run_id": "source"}
    artifacts = {"candidate_analysis": {"observed_at": (NOW - timedelta(hours=1)).isoformat(),
                                        "candidates": [candidate]}}
    review = {"status": "COMPLETE", "reviewed_at": NOW.isoformat(), "sources": [],
              "reviews": [{"candidate_id": "g", "assessment": "support", "support_arguments": ["Saved history"],
                           "risk_notes": ["No guaranteed result"], "evidence_ids": ["evidence-g"], "source_urls": []}]}
    envelope = {"source_run_id": "source", "candidate_sha256": {"g": REBUILD.COMMON._hash(candidate)},
                "review": review}
    return source, artifacts, envelope


def test_supplement_validates_candidate_bound_evidence():
    source, artifacts, envelope = review_input()
    validated = REBUILD.supplementary_review(envelope, source, artifacts, NOW)
    assert validated == envelope["review"]


@pytest.mark.parametrize("mutation", ["wrong_run", "wrong_snapshot", "future", "unbound_evidence", "wrong_market"])
def test_supplement_rejects_changed_or_unbound_evidence(mutation):
    source, artifacts, envelope = review_input()
    if mutation == "wrong_run":
        envelope["source_run_id"] = "another"
    elif mutation == "wrong_snapshot":
        envelope["candidate_sha256"]["g"] = "wrong"
    elif mutation == "future":
        envelope["review"]["reviewed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif mutation == "unbound_evidence":
        envelope["review"]["reviews"][0]["evidence_ids"] = ["someone-else"]
    else:
        artifacts["candidate_analysis"]["candidates"][0]["market_key"] = "h2h"
    with pytest.raises(ValueError):
        REBUILD.supplementary_review(envelope, source, artifacts, NOW)
