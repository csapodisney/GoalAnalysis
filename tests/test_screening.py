from datetime import date, datetime, timedelta, timezone

from goal_analysis.normalization.models import Competition, Fixture, FixtureStatus, Team
from goal_analysis.screening import RejectionCode, ScreeningPolicy, screen_fixtures


def fixture(
    fixture_id: str,
    kickoff: datetime,
    competition_id: str = "DE1",
    status: FixtureStatus = FixtureStatus.SCHEDULED,
) -> Fixture:
    return Fixture(
        id=fixture_id,
        provider="test",
        competition=Competition(competition_id, competition_id),
        home_team=Team(f"{fixture_id}-home", "Home"),
        away_team=Team(f"{fixture_id}-away", "Away"),
        kickoff=kickoff,
        status=status,
    )


def policy(maximum: int = 30) -> ScreeningPolicy:
    return ScreeningPolicy(
        target_date=date(2026, 9, 20),
        timezone_name="Europe/Berlin",
        allowed_competition_ids=frozenset({"DE1", "GB1"}),
        shortlist_max=maximum,
    )


def test_filters_ineligible_fixtures_with_explicit_reasons() -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    matches = [
        fixture("accepted", now + timedelta(hours=3)),
        fixture("wrong-league", now + timedelta(hours=3), competition_id="XX1"),
        fixture("started", now - timedelta(minutes=1)),
        fixture("live", now + timedelta(hours=3), status=FixtureStatus.LIVE),
        fixture("tomorrow", now + timedelta(days=1, hours=3)),
    ]

    result = screen_fixtures(matches, policy(), now)

    assert [item.fixture.id for item in result.accepted] == ["accepted"]
    assert {item.code for item in result.rejected} == {
        RejectionCode.COMPETITION_NOT_ALLOWED,
        RejectionCode.ALREADY_STARTED,
        RejectionCode.STATUS_NOT_SCHEDULED,
        RejectionCode.NOT_ON_TARGET_DATE,
    }


def test_duplicate_provider_id_is_rejected() -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    match = fixture("same", now + timedelta(hours=3))

    result = screen_fixtures([match, match], policy(), now)

    assert len(result.accepted) == 1
    assert result.rejected[0].code is RejectionCode.DUPLICATE_FIXTURE


def test_ranking_is_odds_free_and_deterministic() -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    low = fixture("low", now + timedelta(hours=2))
    high = fixture("high", now + timedelta(hours=4))
    scores = {"low": 1.0, "high": 2.0}

    result = screen_fixtures([low, high], policy(), now, lambda item: scores[item.id])

    assert [item.fixture.id for item in result.accepted] == ["high", "low"]


def test_shortlist_cap_rejects_but_does_not_backfill() -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    matches = [fixture(str(index), now + timedelta(hours=index + 1)) for index in range(3)]

    result = screen_fixtures(matches, policy(maximum=2), now)

    assert len(result.accepted) == 2
    assert result.rejected[-1].code is RejectionCode.SHORTLIST_CAP
