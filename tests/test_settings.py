import pytest

from goal_analysis.config.settings import Settings


def test_default_timezone_is_berlin() -> None:
    settings = Settings()
    settings.validate()
    assert settings.timezone.key == "Europe/Berlin"


def test_shortlist_bounds_are_validated() -> None:
    with pytest.raises(ValueError, match="shortlist_max"):
        Settings(shortlist_min=30, shortlist_max=15).validate()
