from datetime import datetime, timezone

import pytest

from goal_analysis.normalization.models import (
    Competition,
    Fixture,
    OddsQuote,
    Team,
)


def test_fixture_requires_timezone_aware_kickoff() -> None:
    competition = Competition(id="bundesliga", name="Bundesliga", country_code="DE")
    home = Team(id="home", name="Home")
    away = Team(id="away", name="Away")

    with pytest.raises(ValueError, match="timezone-aware"):
        Fixture(
            id="fixture-1",
            provider="test",
            competition=competition,
            home_team=home,
            away_team=away,
            kickoff=datetime(2026, 9, 20, 15, 30),
        )


def test_fixture_rejects_same_team() -> None:
    competition = Competition(id="bundesliga", name="Bundesliga")
    team = Team(id="same", name="Same")

    with pytest.raises(ValueError, match="must differ"):
        Fixture(
            id="fixture-1",
            provider="test",
            competition=competition,
            home_team=team,
            away_team=team,
            kickoff=datetime(2026, 9, 20, 13, 30, tzinfo=timezone.utc),
        )


def test_odds_quote_age_is_deterministic() -> None:
    quote = OddsQuote(
        fixture_id="fixture-1",
        bookmaker="example",
        market_key="total_goals_2_5",
        selection_key="over",
        decimal_price=1.75,
        quoted_at=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
        provider="test",
    )

    age = quote.age_seconds(datetime(2026, 9, 20, 12, 10, tzinfo=timezone.utc))
    assert age == 600
