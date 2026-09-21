from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CompetitionConfig:
    id: str
    name: str
    country_code: str
    api_football_id: int


@dataclass(frozen=True, slots=True)
class CompetitionCatalog:
    timezone: str
    shortlist_min: int
    shortlist_max: int
    competitions: tuple[CompetitionConfig, ...]

    @property
    def allowed_ids(self) -> frozenset[str]:
        return frozenset(item.id for item in self.competitions)


def load_competition_catalog(path: Path) -> CompetitionCatalog:
    with Path(path).open("rb") as handle:
        raw = tomllib.load(handle)

    competitions = tuple(
        CompetitionConfig(
            id=item["id"],
            name=item["name"],
            country_code=item["country_code"],
            api_football_id=int(item["api_football_id"]),
        )
        for item in raw.get("competitions", [])
    )
    catalog = CompetitionCatalog(
        timezone=raw["timezone"],
        shortlist_min=int(raw["shortlist_min"]),
        shortlist_max=int(raw["shortlist_max"]),
        competitions=competitions,
    )
    if not catalog.competitions:
        raise ValueError("competition catalog must not be empty")
    if catalog.shortlist_min < 1 or catalog.shortlist_max < catalog.shortlist_min:
        raise ValueError("invalid shortlist bounds")
    if len(catalog.allowed_ids) != len(catalog.competitions):
        raise ValueError("competition ids must be unique")
    return catalog
