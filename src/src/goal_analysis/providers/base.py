from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, Sequence

from goal_analysis.normalization.models import Fixture, OddsQuote


class ProviderError(RuntimeError):
    """Base error for provider failures that should be logged and retried explicitly."""


class FixtureProvider(Protocol):
    """Provider-neutral live fixture contract."""

    @property
    def name(self) -> str: ...

    def list_fixtures(
        self,
        target_date: date,
        competition_ids: Sequence[str] | None = None,
    ) -> Sequence[Fixture]: ...


class OddsProvider(Protocol):
    """Odds stay separate from football ranking and evidence generation."""

    @property
    def name(self) -> str: ...

    def get_quotes(
        self,
        fixture_ids: Sequence[str],
        market_keys: Sequence[str],
        observed_at: datetime,
    ) -> Sequence[OddsQuote]: ...
