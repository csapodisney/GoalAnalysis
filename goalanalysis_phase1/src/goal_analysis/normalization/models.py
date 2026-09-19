from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Mapping


class FixtureStatus(StrEnum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINISHED = "finished"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Competition:
    id: str
    name: str
    country_code: str | None = None


@dataclass(frozen=True, slots=True)
class Team:
    id: str
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Fixture:
    id: str
    provider: str
    competition: Competition
    home_team: Team
    away_team: Team
    kickoff: datetime
    status: FixtureStatus = FixtureStatus.SCHEDULED
    provider_ids: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if self.home_team.id == self.away_team.id:
            raise ValueError("home_team and away_team must differ")
        if self.kickoff.tzinfo is None or self.kickoff.utcoffset() is None:
            raise ValueError("kickoff must be timezone-aware")


@dataclass(frozen=True, slots=True)
class OddsQuote:
    fixture_id: str
    bookmaker: str
    market_key: str
    selection_key: str
    decimal_price: float
    quoted_at: datetime
    provider: str

    def __post_init__(self) -> None:
        if self.decimal_price <= 1.0:
            raise ValueError("decimal_price must be greater than 1.0")
        if self.quoted_at.tzinfo is None or self.quoted_at.utcoffset() is None:
            raise ValueError("quoted_at must be timezone-aware")

    def age_seconds(self, now: datetime | None = None) -> float:
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None or reference.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return (reference - self.quoted_at).total_seconds()
