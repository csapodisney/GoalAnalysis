from datetime import UTC, datetime, timedelta

from goal_analysis.features import (
    FeaturePolicy,
    GoalFeatureEngine,
    HistoricalMatch,
    TeamContext,
)
from goal_analysis.normalization.models import Competition, Fixture, Team

KICKOFF = datetime(2026, 9, 20, 15, 0, tzinfo=UTC)


def fixture(fixture_id: str = "next") -> Fixture:
    return Fixture(
        fixture_id,
        "test",
        Competition("DE1", "Bundesliga"),
        Team("home", "Home"),
        Team("away", "Away"),
        KICKOFF,
    )


def match(index: int, home_goals: int, away_goals: int) -> HistoricalMatch:
    return HistoricalMatch(
        str(index),
        "DE1",
        KICKOFF - timedelta(days=index + 1),
        "home",
        "away",
        home_goals,
        away_goals,
    )


def test_features_use_long_home_and_away_profiles() -> None:
    history = [match(index, 2, 1) for index in range(5)]
    engine = GoalFeatureEngine(history, policy=FeaturePolicy(minimum_sample=5))

    features, ranking = engine.evaluate(fixture())

    assert features.home_sample == 5
    assert features.away_sample == 5
    assert features.average_total_goals == 3.0
    assert features.over_2_5_rate == 1.0
    assert features.btts_rate == 1.0
    assert ranking.score > 0


def test_insufficient_evidence_is_unknown_not_zero() -> None:
    engine = GoalFeatureEngine([match(1, 0, 0)], policy=FeaturePolicy(minimum_sample=2))

    features, ranking = engine.evaluate(fixture())

    assert features.average_total_goals is None
    assert features.over_2_5_rate is None
    assert features.btts_rate is None
    assert ranking.goal_level is None


def test_future_and_previous_coach_results_are_excluded() -> None:
    recent = match(2, 3, 1)
    old = match(40, 5, 5)
    future = HistoricalMatch("future", "DE1", KICKOFF + timedelta(days=1), "home", "away", 9, 9)
    contexts = [
        TeamContext("home", KICKOFF - timedelta(days=10)),
        TeamContext("away", KICKOFF - timedelta(days=10)),
    ]
    engine = GoalFeatureEngine([recent, old, future], contexts, FeaturePolicy(minimum_sample=1))

    features = engine.calculate(fixture())

    assert features.home_sample == 1
    assert features.away_sample == 1
    assert features.average_total_goals == 4.0


def test_head_to_head_match_is_not_double_weighted() -> None:
    history = [
        match(1, 5, 0),
        HistoricalMatch(
            "home-other",
            "DE1",
            KICKOFF - timedelta(days=3),
            "home",
            "other-away",
            0,
            0,
        ),
        HistoricalMatch(
            "away-other",
            "DE1",
            KICKOFF - timedelta(days=4),
            "other-home",
            "away",
            0,
            0,
        ),
    ]
    engine = GoalFeatureEngine(history, policy=FeaturePolicy(minimum_sample=1))

    features = engine.calculate(fixture())

    assert features.average_total_goals == 1.666667
