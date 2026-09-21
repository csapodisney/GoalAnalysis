from __future__ import annotations

import csv
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from goal_analysis.normalization.models import OddsQuote


class CsvOddsProvider:
    """Offline odds adapter for shadow runs and provider-contract validation."""

    name = "csv_odds"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def get_quotes(
        self,
        fixture_ids: Sequence[str],
        market_keys: Sequence[str],
        observed_at: datetime,
    ) -> Sequence[OddsQuote]:
        del observed_at
        fixture_filter = set(fixture_ids)
        market_filter = set(market_keys)
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle)
            required = {
                "fixture_id",
                "bookmaker",
                "market_key",
                "selection_key",
                "decimal_price",
                "quoted_at",
            }
            missing = required - set(rows.fieldnames or ())
            if missing:
                raise ValueError(f"odds CSV missing columns: {', '.join(sorted(missing))}")
            return tuple(
                OddsQuote(
                    fixture_id=row["fixture_id"],
                    bookmaker=row["bookmaker"],
                    market_key=row["market_key"],
                    selection_key=row["selection_key"],
                    decimal_price=float(row["decimal_price"]),
                    quoted_at=datetime.fromisoformat(row["quoted_at"]),
                    provider=self.name,
                )
                for row in rows
                if row["fixture_id"] in fixture_filter and row["market_key"] in market_filter
            )
