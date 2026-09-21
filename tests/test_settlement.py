from datetime import UTC, datetime

import pytest

from goal_analysis.settlement import FinalScore, SettlementError, settle_ticket

SETTLED_AT = datetime(2026, 9, 20, 20, 0, tzinfo=UTC)


def ticket(legs=None):
    return {
        "gate_type": "post_ranking_ticket_gate",
        "ticket_ready": True,
        "ranking_integrity_sha256": "r" * 64,
        "bookmaker": "book-a",
        "legs": legs
        or [
            {
                "fixture_id": "one",
                "market_key": "totals_2_5",
                "selection_key": "over",
                "decimal_price": 1.8,
            },
            {
                "fixture_id": "two",
                "market_key": "btts",
                "selection_key": "yes",
                "decimal_price": 1.7,
            },
        ],
    }


def score(fixture_id, home, away):
    return FinalScore(fixture_id, home, away, SETTLED_AT)


def test_winning_ticket_uses_frozen_prices() -> None:
    result = settle_ticket(ticket(), [score("one", 2, 1), score("two", 1, 1)], SETTLED_AT)

    assert result["ticket_status"] == "win"
    assert result["return_multiplier"] == 3.06
    assert result["net_units"] == 2.06
    assert len(result["frozen_ticket_sha256"]) == 64


def test_one_losing_leg_loses_ticket() -> None:
    result = settle_ticket(ticket(), [score("one", 1, 0), score("two", 1, 1)], SETTLED_AT)

    assert result["ticket_status"] == "loss"
    assert result["return_units"] == 0.0
    assert result["net_units"] == -1.0


def test_void_integer_total_reduces_multiplier() -> None:
    legs = [
        {
            "fixture_id": "one",
            "market_key": "totals_3_0",
            "selection_key": "over",
            "decimal_price": 2.0,
        },
        {
            "fixture_id": "two",
            "market_key": "match_result",
            "selection_key": "home",
            "decimal_price": 1.5,
        },
    ]

    result = settle_ticket(ticket(legs), [score("one", 2, 1), score("two", 2, 0)], SETTLED_AT)

    assert [item["status"] for item in result["legs"]] == ["void", "win"]
    assert result["return_multiplier"] == 1.5


def test_missing_score_fails_closed() -> None:
    with pytest.raises(SettlementError, match="missing final score"):
        settle_ticket(ticket(), [score("one", 2, 1)], SETTLED_AT)


def test_unsupported_market_fails_closed() -> None:
    legs = [
        {
            "fixture_id": "one",
            "market_key": "corners",
            "selection_key": "over",
            "decimal_price": 2.0,
        }
    ]

    with pytest.raises(SettlementError, match="unsupported"):
        settle_ticket(ticket(legs), [score("one", 2, 1)], SETTLED_AT)
