from copy import deepcopy

import pytest

from goal_analysis.operating_policy import (
    cautious_fallback_allowed,
    current_contract,
    effective_persona,
    generation_capacity,
    generation_limits,
)

CURRENT = {"operating_contract_version": 2}


def character(profile="tipster_fek_tade"):
    return {
        "profile_id": profile,
        "target_odds_band": {"min": 10, "max": 14},
        "preferred_combined_price": 12,
        "model_weights": {"gauss": .4, "kepler": .3, "laplace": .3},
        "selection_policy": {"min_models": 2, "min_probability": .55},
        "min_legs": 4,
        "max_legs": 6,
    }


def test_current_target_is_not_the_hard_capacity_and_legacy_stays_explicit():
    assert generation_limits(CURRENT) == (15, 30)
    assert generation_capacity(CURRENT) == 30
    assert generation_limits({**CURRENT, "target_ticket_count": 20}) == (20, 30)
    assert generation_limits({"operating_contract_version": 1}) == (2, 5)
    assert not current_contract({})


def test_engine_accepts_current_capacity_and_all_personalities_without_old_five_cap():
    from goal_analysis.engine.portfolio import _settings

    result = _settings(CURRENT)
    assert result["operating_contract_version"] == 2
    assert generation_limits(result) == (15, 30)
    assert len(result["enabled_profiles"]) == 30
    assert (result["target_min_odds"], result["target_max_odds"]) == (15, 40)
    assert _settings({})["max_ticket_count"] == 5
    with pytest.raises(ValueError):
        _settings({**CURRENT, "target_min_odds": 5})


@pytest.mark.parametrize("settings", [
    {**CURRENT, "target_ticket_count": 14},
    {**CURRENT, "target_ticket_count": 31},
    {**CURRENT, "target_ticket_count": True},
    {**CURRENT, "max_ticket_count": 5},
    {**CURRENT, "max_ticket_count": False},
    {"operating_contract_version": True},
    {"operating_contract_version": 3},
])
def test_bad_capacity_or_version_cannot_reintroduce_the_old_limit(settings):
    with pytest.raises(ValueError):
        generation_limits(settings)


def test_new_odds_do_not_mutate_the_historical_personality_or_model_rules():
    original = character()
    before = deepcopy(original)
    current = effective_persona(original, CURRENT)
    assert current["target_odds_band"] == {"min": 15, "max": 40}
    assert current["preferred_combined_price"] == 15
    assert current["selection_policy"] == before["selection_policy"]
    assert current["model_weights"] == before["model_weights"]
    assert (current["min_legs"], current["max_legs"]) == (4, 6)
    current["model_weights"]["gauss"] = 0
    assert original == before
    assert effective_persona(original, {"operating_contract_version": 1}) == before


def test_protected_daily_is_not_rewritten_by_general_odds_policy():
    daily = {"profile_id": "daily223", "protected_minimum": [2, 2, 3]}
    assert effective_persona(daily, CURRENT) == daily
    with pytest.raises(ValueError):
        effective_persona(daily, CURRENT, cautious_fallback=True)


def test_empty_shortlist_without_full_day_search_cannot_trigger_fallback():
    profile = "tipster_fek_tade"
    assert not cautious_fallback_allowed(
        profile, CURRENT, [], all_daily_fixtures_considered=False)
    assert not cautious_fallback_allowed(
        profile, CURRENT, [{"option": "valid-primary"}], all_daily_fixtures_considered=True)
    assert cautious_fallback_allowed(
        profile, CURRENT, [], all_daily_fixtures_considered=True)
    assert not cautious_fallback_allowed(
        "tipster_meresz_hunor", CURRENT, [], all_daily_fixtures_considered=True)


def test_cautious_range_and_non_cautious_rejection_are_explicit():
    result = effective_persona(character(), CURRENT, cautious_fallback=True)
    assert result["target_odds_band"] == {"min": 5, "max": 15}
    assert result["operating_contract"]["cautious_fallback"] is True
    with pytest.raises(ValueError):
        effective_persona(character("tipster_meresz_hunor"), CURRENT, cautious_fallback=True)


@pytest.mark.parametrize("coverage, options", [(1, []), (None, []), (True, {}), (True, "")])
def test_fallback_requires_typed_search_evidence(coverage, options):
    with pytest.raises(ValueError if type(coverage) is not bool else TypeError):
        cautious_fallback_allowed(
            "tipster_fek_tade", CURRENT, options, all_daily_fixtures_considered=coverage)
