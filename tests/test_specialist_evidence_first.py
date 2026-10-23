"""Evidence eligibility is independent of a desired payout multiplier."""

from copy import deepcopy

from test_daily_223 import NOW
from test_daily_223 import candidate as _candidate
from test_portfolio import source

from goal_analysis.engine.portfolio import _construct_specialist, build_portfolio
from goal_analysis.engine.recommendations import build_recommendations


def candidate(*args, **changes):
    """Use an unchanged specialist for generic evidence/utility regressions."""
    return _candidate(*args, **{"market_key": "btts", "selection_key": "yes", **changes})


def test_stronger_low_price_single_beats_padding_with_weaker_high_price():
    rows = [candidate("strong", 1.35, strength=0.8), candidate("padding", 20, strength=0.1)]
    report = build_portfolio(source(rows), {"enabled_profiles": ["ritmusor"]})
    selected = report["tickets"][0]
    assert selected["profile_id"] == "ritmusor"
    assert [leg["candidate_id"] for leg in selected["legs"]] == ["strong"]
    assert selected["combined_price"] == 1.35 and not selected["fallback"]
    assert selected["minimum_combined_price"] is None and selected["minimum_legs"] == 1
    assert report["policy"]["target_min_odds"] == 10  # DAILY identity settings remain unchanged.
    assert report["policy"]["specialist_selection"]["legacy_target_min_odds_applied"] is False


def test_reviewed_supported_single_is_ready_without_false_fallback_warning():
    row = candidate("single", 1.35)
    review = {"status": "COMPLETE", "reviews": [{"candidate_id": "single", "assessment": "support"}]}
    report = build_portfolio(source([row]), {"enabled_profiles": ["ritmusor"]}, astra_review=review)
    selected = report["tickets"][0]
    assert selected["status"] == "READY" and not selected["fallback"]
    assert not selected["quality_warnings"] and not selected["real_wager_placed"]
    unreviewed = build_portfolio(source([row]), {"enabled_profiles": ["ritmusor"]})["tickets"][0]
    assert unreviewed["status"] == "DRAFT"


def test_single_does_not_relax_evidence_history_or_maximum_price():
    for bad in (candidate("weak", 1.35, strength=0.01), candidate("too-expensive", 51)):
        assert not build_portfolio(source([bad]), {"enabled_profiles": ["ritmusor"]})["tickets"]
    missing = candidate("no-venue", 1.35)
    missing["evidence"] = missing["evidence"][:1]
    assert not build_portfolio(source([missing]), {"enabled_profiles": ["ritmusor"]})["tickets"]


def test_quality_precedes_book_preference_but_candidate_id_does_not_break_quality_tie():
    rows = [candidate("a-weaker", 1.5, strength=0.7, bookmaker="betano"),
            candidate("z-stronger", 1.5, strength=0.8, bookmaker="other")]
    report = build_portfolio(source(rows), {"enabled_profiles": ["ritmusor"]})
    assert report["tickets"][0]["bookmaker"] == "other"
    rows[0] = candidate("zz-preferred", 1.5, strength=0.8, bookmaker="betano")
    rows[1]["candidate_id"] = "aa-other"
    report = build_portfolio(source(rows), {"enabled_profiles": ["ritmusor"]})
    assert report["tickets"][0]["bookmaker"] == "betano"


def test_unconstructed_specialist_research_stays_draft_low_even_when_reviewed():
    row = candidate("weak", 1.35, strength=0.01)
    payload = source([row])
    report = build_portfolio(payload, {"enabled_profiles": ["ritmusor"]})
    review = {"status": "COMPLETE", "research_status": "COMPLETE_WITH_SOURCES",
              "reviews": [{"candidate_id": "weak", "assessment": "support"}]}
    output = build_recommendations(payload, [], {"enabled_profiles": ["ritmusor"]}, NOW, report, review)
    idea = output[0]
    assert idea["status"] == "DRAFT" and idea["confidence"] == "LOW"
    assert not idea["construction_verified"] and not idea["playable"]
    assert idea["portfolio_ticket_id"] is None and not idea["selected_for_portfolio"]


def test_legacy_constructor_retains_odds_target_and_available_fallback():
    rows = []
    for index in range(3):
        row = candidate(str(index), 1.4)
        row.update(weighted_support_score=80, support_score=80)
        rows.append(row)
    original = deepcopy(rows)
    assert _construct_specialist(rows, 10, 40) is None
    fallback = _construct_specialist(rows, 10, 40, allow_fallback=True)
    assert len(fallback) == 3
    quality = _construct_specialist(rows, None, 40)
    assert len(quality) == 3
    assert rows == original


def test_quality_first_scans_more_than_sixty_four_valid_candidates():
    rows = []
    for index in range(130):
        item = candidate(f"candidate-{index:03}", 1.1 + index / 100)
        item.update(weighted_support_score=65 + index / 100, support_score=70)
        rows.append(item)
    chosen = _construct_specialist(rows, None, 40)
    assert len(chosen) == 6 and chosen[0]["candidate_id"] == "candidate-129"
    assert len({row["fixture_id"] for row in chosen}) == 6


def test_emergency_available_combination_has_no_odds_padding_and_remains_explicit_draft():
    rows = [candidate(str(index), 1.4, strength=0.8) for index in range(3)]
    review = {"status": "COMPLETE", "reviews": [
        {"candidate_id": row["candidate_id"], "assessment": "support"} for row in rows]}
    report = build_portfolio(source(rows), {
        "strictness": 0, "enabled_profiles": ["daily223", "gauss"],
    }, astra_review=review)
    selected = report["tickets"][0]
    assert selected["profile_id"] == "available" and len(selected["legs"]) == 3
    assert abs(selected["combined_price"] - 1.4 ** 3) < 1e-12
    assert selected["minimum_combined_price"] is None
    assert selected["fallback"] and selected["quality_warnings"]
    assert selected["status"] == "DRAFT" and selected["confidence"] == "LOW"
    assert selected["real_wager_placed"] is False


def test_emergency_available_ranks_evidence_before_book_preference():
    rows = [candidate("preferred-weak", 1.4, strength=0.7, bookmaker="betano"),
            candidate("other-strong", 1.4, strength=0.8, bookmaker="other")]
    report = build_portfolio(source(rows), {
        "strictness": 0, "enabled_profiles": ["daily223", "gauss"],
    })
    selected = report["tickets"][0]
    assert selected["bookmaker"] == "other" and selected["fallback"]
