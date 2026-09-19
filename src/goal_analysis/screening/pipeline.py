from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from goal_analysis.normalization.models import Fixture, FixtureStatus

from .rules import Rejection, RejectionCode

ScoreFunction = Callable[[Fixture], float]


@dataclass(frozen=True, slots=True)
class ScreeningPolicy:
    target_date: date
    timezone_name: str
    allowed_competition_ids: frozenset[str]
    shortlist_max: int = 30

    def validate(self) -> None:
        if not self.allowed_competition_ids:
            raise ValueError("allowed_competition_ids must not be empty")
        if self.shortlist_max < 1:
            raise ValueError("shortlist_max must be positive")
        ZoneInfo(self.timezone_name)


@dataclass(frozen=True, slots=True)
class RankedFixture:
    fixture: Fixture
    score: float


@dataclass(frozen=True, slots=True)
class ScreeningResult:
    accepted: tuple[RankedFixture, ...]
    rejected: tuple[Rejection, ...]
    input_count: int


def screen_fixtures(
    fixtures: Iterable[Fixture],
    policy: ScreeningPolicy,
    now: datetime,
    score: ScoreFunction | None = None,
) -> ScreeningResult:
    """Build an explainable shortlist without consulting any odds."""

    policy.validate()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    timezone = ZoneInfo(policy.timezone_name)
    observed_at = now.astimezone(timezone)
    score_fn = score or (lambda _: 0.0)
    seen: set[tuple[str, str]] = set()
    eligible: list[RankedFixture] = []
    rejected: list[Rejection] = []
    input_count = 0

    for fixture in fixtures:
        input_count += 1
        identity = (fixture.provider, fixture.id)
        if identity in seen:
            rejected.append(
                Rejection(fixture.id, RejectionCode.DUPLICATE_FIXTURE, "provider/id repeated")
            )
            continue
        seen.add(identity)

        if fixture.competition.id not in policy.allowed_competition_ids:
            rejected.append(
                Rejection(
                    fixture.id,
                    RejectionCode.COMPETITION_NOT_ALLOWED,
                    fixture.competition.id,
                )
            )
            continue

        local_kickoff = fixture.kickoff.astimezone(timezone)
        if local_kickoff.date() != policy.target_date:
            rejected.append(
                Rejection(
                    fixture.id,
                    RejectionCode.NOT_ON_TARGET_DATE,
                    local_kickoff.date().isoformat(),
                )
            )
            continue

        if fixture.status is not FixtureStatus.SCHEDULED:
            rejected.append(
                Rejection(
                    fixture.id,
                    RejectionCode.STATUS_NOT_SCHEDULED,
                    fixture.status.value,
                )
            )
            continue

        if local_kickoff <= observed_at:
            rejected.append(
                Rejection(
                    fixture.id,
                    RejectionCode.ALREADY_STARTED,
                    local_kickoff.isoformat(),
                )
            )
            continue

        eligible.append(RankedFixture(fixture=fixture, score=float(score_fn(fixture))))

    eligible.sort(key=lambda item: (-item.score, item.fixture.kickoff, item.fixture.id))
    accepted = eligible[: policy.shortlist_max]
    for item in eligible[policy.shortlist_max :]:
        rejected.append(
            Rejection(
                item.fixture.id,
                RejectionCode.SHORTLIST_CAP,
                f"ranked below top {policy.shortlist_max}",
            )
        )

    return ScreeningResult(
        accepted=tuple(accepted),
        rejected=tuple(rejected),
        input_count=input_count,
    )
