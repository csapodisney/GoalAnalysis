"""Indicative web quotes must match identities, period, sources and information cutoff."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents.web_research import TOPICS, validate_research_details

NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)
URL = "https://example.com/event/football"
CANDIDATE = {
    "candidate_id": "c1",
    "fixture_id": "api_football:1",
    "home_team": "Home",
    "away_team": "Away",
    "kickoff": (NOW + timedelta(hours=3)).isoformat(),
    "market_key": "totals_0_5",
    "selection_key": "over",
    "period": "FIRST_HALF",
}
QUOTE = {k: v for k, v in CANDIDATE.items() if k != "fixture_id"} | {
    "decimal_price": 1.6,
    "bookmaker": "Example",
    "observed_at": NOW.isoformat(),
    "source_url": URL,
}


def validate(q, searched=True):
    return validate_research_details(
        {"odds_quotes": [q]},
        [CANDIDATE],
        [{"url": URL, "title": "Event"}],
        NOW.isoformat(),
        searched,
    )


def test_valid_source_matched_web_odds_are_indicative_and_context_defaults_unknown():
    result = validate(QUOTE)
    assert result["web_odds"][0]["requires_bookmaker_check"] is True
    assert result["web_odds"][0]["indicative"] is True
    assert {c["topic"] for c in result["fixture_context"]} == set(TOPICS)
    assert all(c["status"] == "UNKNOWN" for c in result["fixture_context"])


@pytest.mark.parametrize(
    "patch",
    [
        {"period": "FULL_TIME"},
        {"home_team": "Other"},
        {"decimal_price": float("nan")},
        {"decimal_price": True},
        {"candidate_id": "other"},
        {"source_url": "https://wrong.example.com/"},
        {"observed_at": (NOW + timedelta(minutes=1)).isoformat()},
        {"observed_at": (NOW - timedelta(days=2)).isoformat()},
        {"kickoff": (NOW + timedelta(days=1)).isoformat()},
    ],
)
def test_wrong_or_stale_web_quote_never_changes_candidate(patch):
    before = deepcopy(CANDIDATE)
    assert validate(QUOTE | patch)["web_odds"] == []
    assert CANDIDATE == before


def test_no_observed_search_never_produces_sourced_quotes():
    assert validate(QUOTE, searched=False)["web_odds"] == []
