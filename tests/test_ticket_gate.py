from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.engine import TicketGateError, TicketGatePolicy, evaluate_ticket
from goal_analysis.normalization.models import OddsQuote

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)


def fixture(fixture_id: str, rank: int, selected: bool = True) -> dict:
    return {
        "fixture_id": fixture_id,
        "rank": rank,
        "arthur": {
            "market_key": "totals_2_5",
            "selection_key": "over",
        },
        "final": {
            "selected": selected,
            "blocked_by_structural_veto": False,
            "veto_roles": [],
        },
    }


def run(fixtures=None) -> dict:
    items = fixtures or [fixture("first", 1), fixture("second", 2)]
    return {
        "run_type": "kerekasztal_shadow",
        "fact_packet_sha256": "f" * 64,
        "ranking_frozen_at": "2026-09-20T08:00:00+00:00",
        "ranking_order": [item["fixture_id"] for item in items],
        "prices_attached": False,
        "fixtures": items,
    }


def quote(fixture_id: str, bookmaker: str, price: float, age_seconds: int = 30) -> OddsQuote:
    return OddsQuote(
        fixture_id,
        bookmaker,
        "totals_2_5",
        "over",
        price,
        NOW - timedelta(seconds=age_seconds),
        "test",
    )


def test_prices_do_not_change_frozen_football_order() -> None:
    quotes = [
        quote("first", "book-a", 1.5),
        quote("second", "book-a", 3.0),
        quote("first", "book-b", 2.0),
        quote("second", "book-b", 2.0),
    ]

    result = evaluate_ticket(run(), quotes, NOW)

    assert result["football_selection_order"] == ["first", "second"]
    assert [item["fixture_id"] for item in result["legs"]] == ["first", "second"]
    assert result["bookmaker"] == "book-a"
    assert result["combined_price"] == 4.5
    assert result["constraints"]["odds_weight_in_football_selection"] == 0


def test_quotes_must_cover_every_leg_at_one_bookmaker() -> None:
    result = evaluate_ticket(
        run(),
        [quote("first", "book-a", 2.0), quote("second", "book-b", 2.0)],
        NOW,
    )

    assert result["ticket_ready"] is False
    assert result["bookmaker"] is None
    assert [item["fixture_id"] for item in result["unpriced_requests"]] == [
        "first",
        "second",
    ]


def test_stale_and_unselected_quotes_cannot_enter_ticket() -> None:
    fixtures = [fixture("chosen", 1), fixture("rejected", 2, selected=False)]
    quotes = [
        quote("chosen", "book-a", 2.0, age_seconds=601),
        quote("rejected", "book-a", 9.0),
    ]

    result = evaluate_ticket(run(fixtures), quotes, NOW)

    assert result["ticket_ready"] is False
    assert {item["reason"] for item in result["rejected_quotes"]} == {
        "stale_quote",
        "not_in_frozen_selection",
    }


def test_maximum_legs_caps_without_backfill_or_reordering() -> None:
    fixtures = [fixture(str(index), index) for index in range(1, 5)]
    quotes = [quote(str(index), "book-a", 1.5) for index in range(1, 5)]

    result = evaluate_ticket(run(fixtures), quotes, NOW, TicketGatePolicy(maximum_legs=2))

    assert [item["fixture_id"] for item in result["legs"]] == ["1", "2"]
    assert result["capped_fixture_ids"] == ["3", "4"]


def test_structural_veto_in_selected_fixture_fails_closed() -> None:
    item = fixture("bad", 1)
    item["final"]["blocked_by_structural_veto"] = True

    with pytest.raises(TicketGateError, match="structural-veto"):
        evaluate_ticket(run([item]), [quote("bad", "book-a", 2.0)], NOW)


def test_missing_arthur_market_fails_closed() -> None:
    item = fixture("missing", 1)
    item["arthur"]["market_key"] = None

    with pytest.raises(TicketGateError, match="lacks market"):
        evaluate_ticket(run([item]), [], NOW)
