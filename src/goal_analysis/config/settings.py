from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class Settings:
    timezone_name: str = "Europe/Berlin"
    database_path: Path = Path("data/goal_analysis.sqlite3")
    raw_snapshot_dir: Path = Path("data/raw")
    cache_dir: Path = Path("data/cache")
    shortlist_min: int = 15
    shortlist_max: int = 30

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def validate(self) -> None:
        if self.shortlist_min < 1:
            raise ValueError("shortlist_min must be positive")
        if self.shortlist_max < self.shortlist_min:
            raise ValueError("shortlist_max must be >= shortlist_min")
