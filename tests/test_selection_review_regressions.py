"""Review coverage must not become a shared specialist selection whitelist."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from goal_analysis.agents import canonical_sha256
from goal_analysis.engine.recommendations import build_recommendations, recommendation_packets

NOW = datetime(2026, 10, 14, 10, tzinfo=UTC)


def candidate(identifier, support):
    return {
        "candidate_id": identifier, "fixture_id": "fixture-" + identifier,
        "kickoff": (NOW + timedelta(hours=5)).isoformat(),
        "market_key": "totals_2_5", "selection_key": "over", "period": "FULL_TIME",
        "decimal_price": 2.1, "bookmaker": "test", "support_score": support,
        "evidence": [], "missing_support": [],
    }


def build(profile, *, proposal):
    rows = [candidate("strong-a", 10), candidate("strong-b", 9),
            candidate("strong-c", 8), candidate("reviewed-weak", 1)]
    if profile == "daily223":
        rows[2]["decimal_price"] = 3.1
    else:
        for row in rows:
            row.update(market_key="btts", selection_key="yes")
    enriched = {"date": "2026-10-14", "candidates": rows}
    report = {"profile_proposals": [{"profile_id": profile, "legs": rows[:3]}]
              if proposal else []}
    review = {"status": "COMPLETE", "research_status": "COMPLETE_WITH_SOURCES",
              "reviews": [{"candidate_id": "reviewed-weak", "assessment": "support"}]}
    original = deepcopy([enriched, report, review])
    result = build_recommendations(enriched, [], {"enabled_profiles": [profile]},
                                   NOW, report, review)[0]
    assert [enriched, report, review] == original
    return result


def test_unreviewed_specialist_proposal_is_preserved_with_honest_confidence():
    result = build("ritmusor", proposal=True)
    assert [leg["candidate_id"] for leg in result["legs"]] == ["strong-a", "strong-b", "strong-c"]
    assert result["confidence"] == "LOW" and result["status"] == "DRAFT"
    assert "Astra még nem értékelte az összes választást." in result["quality_warnings"]
    assert result["strategy_version"] == "arthur-daily-recommendations-v3-evidence-first-specialists"


def test_specialist_fallback_ranks_full_pool_regardless_of_review_coverage():
    result = build("ritmusor", proposal=False)
    assert [leg["candidate_id"] for leg in result["legs"]] == ["strong-a", "strong-b", "strong-c"]
    assert result["confidence"] == "LOW"


def test_partial_review_cannot_replace_a_constructed_daily223_with_underpriced_picks():
    result = build("daily223", proposal=True)
    assert [leg["candidate_id"] for leg in result["legs"]] == ["strong-a", "strong-b", "strong-c"]
    assert result["strategy_version"] == "arthur-daily-recommendations-v2-verified-construction"
    identity = [["fixture-" + name, "totals_2_5", "over", "FULL_TIME"]
                for name in ("strong-a", "strong-b", "strong-c")]
    assert result["recommendation_id"] == canonical_sha256(
        ["arthur-daily-recommendations-v2-verified-construction", "2026-10-14", "daily223", NOW.isoformat(), identity]
    )[:24]
    assert result["confidence"] == "LOW" and result["status"] == "DRAFT"
    assert result["daily223_constraints_verified"]
    assert result["combined_price"] > 12


def test_unconstructed_daily223_is_a_low_confidence_target_even_if_fully_reviewed():
    rows = [candidate(str(i), 10) for i in range(3)]
    for row in rows:
        row["decimal_price"] = 1.03
    enriched = {"date": "2026-10-14", "candidates": rows}
    review = {"status": "COMPLETE", "research_status": "COMPLETE_WITH_SOURCES",
              "reviews": [{"candidate_id": row["candidate_id"], "assessment": "support"}
                          for row in rows]}
    result = build_recommendations(enriched, [], {"enabled_profiles": ["daily223"]},
                                   NOW, {}, review)[0]
    assert result["status"] == "TARGET_PROPOSAL" and result["confidence"] == "LOW"
    assert not result["daily223_constraints_verified"] and not result["playable"]
    assert result["portfolio_ticket_id"] is None
    assert result["combined_price"] == 1.03 ** 3
    assert [leg["target_price"] for leg in result["legs"]] == [2.0, 2.0, 3.0]


def test_selected_ticket_legs_cannot_be_starved_by_broad_profile_recommendations():
    selected = [candidate(f"selected-{index}", 10 - index) for index in range(3)]
    broad = [{"legs": [candidate(f"profile-{profile}-{leg}", 1)
                        for leg in range(3)]} for profile in range(20)]

    packets = recommendation_packets(broad, selected, maximum=30)

    assert [item["candidate_id"] for item in packets[:3]] == [
        "selected-0", "selected-1", "selected-2"
    ]
    assert len(packets) == 30


def test_selected_packet_priority_deduplicates_before_round_robin_fill():
    selected = [candidate("shared", 10), candidate("selected", 9)]
    broad = [{"legs": [candidate("shared", 1), candidate("profile", 1)]}]

    packets = recommendation_packets(broad, selected, maximum=3)

    assert [item["candidate_id"] for item in packets] == ["shared", "selected", "profile"]
