from pathlib import Path

from goal_analysis.config import load_competition_catalog


def test_top_league_catalog_loads() -> None:
    catalog = load_competition_catalog(Path("config/competitions.toml"))

    assert catalog.timezone == "Europe/Berlin"
    assert catalog.shortlist_min == 15
    assert catalog.shortlist_max == 30
    assert catalog.allowed_ids == frozenset({"DE1", "GB1", "IT1", "ES1", "FR1"})
