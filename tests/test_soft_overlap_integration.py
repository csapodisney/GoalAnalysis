"""Ticket aliases retain full construction and honest, bounded review coverage."""

from copy import deepcopy
from datetime import timedelta
from math import prod

from test_daily_223 import NOW
from test_daily_223 import candidate as _candidate
from test_portfolio import source

from goal_analysis.engine.recommendations import build_recommendations
from goal_analysis.jobs.portfolio import _base_diversity_recommendations, _guard_final_publication


def candidate(*args, **changes):
    """Use an unchanged specialist for generic evidence/utility regressions."""
    return _candidate(*args, **{"market_key": "btts", "selection_key": "yes", **changes})


def combination(rows):
    return {
        "quality": 0.8,
        "ticket": {
            "ticket_id": "synthetic-multi-ticket",
            "profile_id": "ritmusor",
            "profile_name": "Ritmusőr",
            "date": NOW.date().isoformat(),
            "created_at": NOW.isoformat(),
            "status": "DRAFT",
            "quality_warnings": [],
            "combined_price": prod(row["decimal_price"] for row in rows),
            "legs": deepcopy(rows),
        },
    }


def test_partial_review_never_upgrades_a_multileg_ticket_or_shortens_its_alias():
    rows = [candidate("a", 1.6), candidate("b", 1.7)]
    options = {"ritmusor": [combination(rows)]}
    review = {
        "status": "COMPLETE",
        "research_status": "COMPLETE_WITH_SOURCES",
        "reviews": [{"candidate_id": "a", "assessment": "support"}],
    }
    _base_diversity_recommendations(options, source(rows), {}, NOW, review, {})
    option = options["ritmusor"][0]
    assert option["ticket"]["status"] == "DRAFT"
    alias = option["recommendation"]
    assert alias["status"] == "DRAFT"
    assert {leg["candidate_id"] for leg in alias["legs"]} == {"a", "b"}
    assert alias["combined_price"] == option["ticket"]["combined_price"]
    assert any("Astra" in warning for warning in alias["quality_warnings"])
    assert not alias["playable"] and alias["stake_eur"] is None


def test_started_leg_discards_the_whole_option_instead_of_publishing_shorter_alias():
    rows = [candidate("expired", 1.6, kickoff=NOW.isoformat()), candidate("future", 1.7)]
    options = {"ritmusor": [combination(rows)]}
    _base_diversity_recommendations(options, source(rows), {}, NOW, {}, {})
    assert options["ritmusor"] == []


def test_missing_snapshot_candidate_does_not_turn_a_combination_into_research_fallback():
    rows = [candidate("missing", 1.6), candidate("present", 1.7)]
    options = {"ritmusor": [combination(rows)]}
    _base_diversity_recommendations(options, source(rows[1:]), {}, NOW, {}, {})
    assert options["ritmusor"] == []


def test_preliminary_refusal_preserves_new_reason_and_its_details():
    refusal = {"reason_code": "EXACT_TICKET_DUPLICATE", "duplicate_of": "another-profile"}
    cards = build_recommendations(
        source([]), [], {"enabled_profiles": ["ritmusor"]}, NOW,
        {"diversification": {"abstained_profiles": {"ritmusor": refusal}}},
    )
    assert cards[0]["reason_code"] == "EXACT_TICKET_DUPLICATE"
    assert cards[0]["diversification"]["duplicate_of"] == "another-profile"
    assert cards[0]["status"] == "ABSTAINED" and not cards[0]["legs"]


def test_15_minute_publication_guard_removes_whole_daily_wrapper_without_rewriting_legs():
    rows = [_candidate("near", 2, kickoff=(NOW + timedelta(minutes=15)).isoformat()),
            _candidate("future-b", 2), _candidate("future-c", 3)]
    ticket = combination(rows)["ticket"]
    ticket["profile_id"] = "daily223"
    original_legs = deepcopy(ticket["legs"])
    report = {
        "tickets": [ticket], "profile_proposals": [deepcopy(ticket)], "preview_tickets": [],
        "recommendations": [deepcopy(ticket)], "profiles": [{"profile_id": "daily223"}],
    }
    _guard_final_publication(report, NOW)
    assert not report["tickets"] and not report["profile_proposals"]
    assert report["recommendations"][0]["reason_code"] == "EVENT_WITHIN_15_MINUTE_PUBLICATION_LOCKOUT"
    assert ticket["legs"] == original_legs
    assert report["profiles"][0]["published"] is False


def test_locked_multileg_option_does_not_get_materialized_as_a_shorter_alias():
    rows = [candidate("near", 1.6, kickoff=(NOW + timedelta(minutes=14)).isoformat()),
            candidate("future", 1.7)]
    options = {"ritmusor": [combination(rows)]}
    _base_diversity_recommendations(options, source(rows), {}, NOW, {}, {})
    assert options["ritmusor"] == []
