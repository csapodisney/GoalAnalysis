from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from goal_analysis.normalization.models import Fixture


@dataclass(frozen=True, slots=True)
class HistoricalMatch:
    """A settled regular-time result used for deterministic feature calculation."""

    id: str
    competition_id: str
    kickoff: datetime
    home_team_id: str
    away_team_id: str
    home_goals: int
    away_goals: int

    def __post_init__(self) -> None:
        if self.kickoff.tzinfo is None or self.kickoff.utcoffset() is None:
            raise ValueError("kickoff must be timezone-aware")
        if self.home_team_id == self.away_team_id:
            raise ValueError("home_team_id and away_team_id must differ")
        if self.home_goals < 0 or self.away_goals < 0:
            raise ValueError("goals must not be negative")


@dataclass(frozen=True, slots=True)
class TeamContext:
    team_id: str
    current_coach_since: datetime | None = None

    def __post_init__(self) -> None:
        if self.current_coach_since is not None and (
            self.current_coach_since.tzinfo is None or self.current_coach_since.utcoffset() is None
        ):
            raise ValueError("current_coach_since must be timezone-aware")


@dataclass(frozen=True, slots=True)
class FeaturePolicy:
    lookback_days: int = 365
    minimum_sample: int = 5
    target_sample: int = 12

    def __post_init__(self) -> None:
        if self.lookback_days < 1:
            raise ValueError("lookback_days must be positive")
        if self.minimum_sample < 1:
            raise ValueError("minimum_sample must be positive")
        if self.target_sample < self.minimum_sample:
            raise ValueError("target_sample must be at least minimum_sample")


@dataclass(frozen=True, slots=True)
class GoalFeatureSet:
    fixture_id: str
    home_sample: int
    away_sample: int
    home_goals_for: float | None
    home_goals_against: float | None
    away_goals_for: float | None
    away_goals_against: float | None
    average_total_goals: float | None
    over_2_5_rate: float | None
    btts_rate: float | None
    both_team_scoring_rate: float | None
    coverage: float

    def to_dict(self) -> dict[str, float | int | str | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RankingBreakdown:
    score: float
    goal_level: float | None
    over_2_5: float | None
    btts: float | None
    scoring_reliability: float | None
    coverage: float

    def to_dict(self) -> dict[str, float | None]:
        return asdict(self)


class GoalFeatureEngine:
    """Calculate an odds-free goal profile from long home/away history.

    Missing or insufficient evidence stays ``None`` and is never converted to zero.
    Ranking weights are explicit and versioned here; they shortlist research targets,
    not betting selections.
    """

    RANKING_VERSION = "goal-shortlist-v1"

    def __init__(
        self,
        history: Iterable[HistoricalMatch],
        contexts: Iterable[TeamContext] = (),
        policy: FeaturePolicy | None = None,
    ) -> None:
        self.history = tuple(history)
        self.contexts = {item.team_id: item for item in contexts}
        self.policy = policy or FeaturePolicy()

    def calculate(self, fixture: Fixture) -> GoalFeatureSet:
        earliest = fixture.kickoff - timedelta(days=self.policy.lookback_days)
        home_start = self._effective_start(fixture.home_team.id, earliest)
        away_start = self._effective_start(fixture.away_team.id, earliest)
        home = [
            item
            for item in self.history
            if item.competition_id == fixture.competition.id
            and item.home_team_id == fixture.home_team.id
            and home_start <= item.kickoff < fixture.kickoff
        ]
        away = [
            item
            for item in self.history
            if item.competition_id == fixture.competition.id
            and item.away_team_id == fixture.away_team.id
            and away_start <= item.kickoff < fixture.kickoff
        ]
        home.sort(key=lambda item: (item.kickoff, item.id))
        away.sort(key=lambda item: (item.kickoff, item.id))

        enough_home = len(home) >= self.policy.minimum_sample
        enough_away = len(away) >= self.policy.minimum_sample
        usable = enough_home and enough_away
        combined = list({item.id: item for item in home + away}.values())

        home_for = _mean(item.home_goals for item in home) if enough_home else None
        home_against = _mean(item.away_goals for item in home) if enough_home else None
        away_for = _mean(item.away_goals for item in away) if enough_away else None
        away_against = _mean(item.home_goals for item in away) if enough_away else None
        coverage = min(len(home), self.policy.target_sample) / self.policy.target_sample
        coverage *= min(len(away), self.policy.target_sample) / self.policy.target_sample

        return GoalFeatureSet(
            fixture_id=fixture.id,
            home_sample=len(home),
            away_sample=len(away),
            home_goals_for=home_for,
            home_goals_against=home_against,
            away_goals_for=away_for,
            away_goals_against=away_against,
            average_total_goals=(
                _mean(item.home_goals + item.away_goals for item in combined) if usable else None
            ),
            over_2_5_rate=(
                _mean((item.home_goals + item.away_goals) >= 3 for item in combined)
                if usable
                else None
            ),
            btts_rate=(
                _mean(item.home_goals > 0 and item.away_goals > 0 for item in combined)
                if usable
                else None
            ),
            both_team_scoring_rate=(
                _mean(item.home_goals > 0 for item in home)
                * _mean(item.away_goals > 0 for item in away)
                if usable
                else None
            ),
            coverage=round(coverage, 6),
        )

    def rank(self, features: GoalFeatureSet) -> RankingBreakdown:
        goal_level = (
            min(features.average_total_goals / 5.0, 1.0)
            if features.average_total_goals is not None
            else None
        )
        components = (
            (goal_level, 0.35),
            (features.over_2_5_rate, 0.25),
            (features.btts_rate, 0.20),
            (features.both_team_scoring_rate, 0.10),
        )
        evidence_score = sum(value * weight for value, weight in components if value is not None)
        known_weight = sum(weight for value, weight in components if value is not None)
        normalized = evidence_score / known_weight if known_weight else 0.0
        score = normalized * 90.0 + features.coverage * 10.0
        return RankingBreakdown(
            score=round(score, 6),
            goal_level=_rounded(goal_level),
            over_2_5=_rounded(features.over_2_5_rate),
            btts=_rounded(features.btts_rate),
            scoring_reliability=_rounded(features.both_team_scoring_rate),
            coverage=features.coverage,
        )

    def evaluate(self, fixture: Fixture) -> tuple[GoalFeatureSet, RankingBreakdown]:
        features = self.calculate(fixture)
        return features, self.rank(features)

    def _effective_start(self, team_id: str, earliest: datetime) -> datetime:
        context = self.contexts.get(team_id)
        if context is None or context.current_coach_since is None:
            return earliest
        return max(earliest, context.current_coach_since)


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return round(sum(items) / len(items), 6)


def _rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None
