from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .goals import HistoricalMatch


def load_history_csv(path: Path) -> tuple[HistoricalMatch, ...]:
    """Load the small provider-neutral history contract used by the feature engine."""

    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        required = {
            "id",
            "competition_id",
            "kickoff",
            "home_team_id",
            "away_team_id",
            "home_goals",
            "away_goals",
        }
        missing = required - set(rows.fieldnames or ())
        if missing:
            raise ValueError(f"history CSV missing columns: {', '.join(sorted(missing))}")
        return tuple(
            HistoricalMatch(
                id=row["id"],
                competition_id=row["competition_id"],
                kickoff=datetime.fromisoformat(row["kickoff"]),
                home_team_id=row["home_team_id"],
                away_team_id=row["away_team_id"],
                home_goals=int(row["home_goals"]),
                away_goals=int(row["away_goals"]),
            )
            for row in rows
        )
