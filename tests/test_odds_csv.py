from datetime import UTC, datetime

import pytest

from goal_analysis.providers import CsvOddsProvider


def test_csv_odds_provider_filters_fixture_and_market(tmp_path) -> None:
    path = tmp_path / "odds.csv"
    path.write_text(
        "fixture_id,bookmaker,market_key,selection_key,decimal_price,quoted_at\n"
        "one,book-a,totals_2_5,over,1.75,2026-09-20T09:55:00+00:00\n"
        "two,book-a,totals_2_5,over,1.90,2026-09-20T09:55:00+00:00\n"
        "one,book-a,btts,yes,1.80,2026-09-20T09:55:00+00:00\n",
        "utf-8",
    )

    quotes = CsvOddsProvider(path).get_quotes(
        ["one"], ["totals_2_5"], datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    )

    assert len(quotes) == 1
    assert quotes[0].decimal_price == 1.75


def test_csv_odds_provider_requires_contract_columns(tmp_path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("fixture_id,bookmaker\none,book-a\n", "utf-8")

    with pytest.raises(ValueError, match="missing columns"):
        CsvOddsProvider(path).get_quotes(
            ["one"], ["totals_2_5"], datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
        )
