"""Versioned generation settings; no provider, model, database or live runtime."""

import json
from copy import deepcopy

import pytest

from goal_analysis.config.portfolio import (
    DEFAULT_SETTINGS,
    PROFILE_IDS,
    load_settings,
    validate_settings,
)


def test_current_defaults_and_detached_validation():
    before = deepcopy(DEFAULT_SETTINGS)
    result = validate_settings({})
    assert result["operating_contract_version"] == 2
    assert (result["target_ticket_count"], result["max_ticket_count"]) == (15, 30)
    assert (result["target_min_odds"], result["target_max_odds"]) == (15.0, 40.0)
    assert len(result["enabled_profiles"]) == len(set(PROFILE_IDS)) == 30
    assert result["stake_eur"] == 1.0
    assert result["openai"] == {
        "backend": "codex_chatgpt", "model": "gpt-6-astra", "reasoning_effort": "medium",
        "web_search": True, "max_output_tokens": 8000, "max_candidates": 30,
    }
    result["enabled_profiles"].clear()
    result["openai"]["max_candidates"] = 15
    assert DEFAULT_SETTINGS == before


@pytest.mark.parametrize("registry_version", [None, 2, 3, 4])
def test_unversioned_saved_defaults_upgrade_only_in_memory(tmp_path, registry_version, monkeypatch):
    path = tmp_path / "settings.json"
    saved = {
        "schema_version": 3, "strictness": 71,
        "target_ticket_count": 2, "max_ticket_count": 5, "stake_eur": 1.0,
        "target_min_odds": 10.0, "target_max_odds": 50.0,
        "enabled_profiles": ["daily223", "merlin"],
        "openai": {"model": "gpt-6-astra", "reasoning_effort": "high",
                   "max_output_tokens": 4000, "max_candidates": 21, "web_search": False},
    }
    if registry_version is not None:
        saved["profile_registry_version"] = registry_version
    original = b"\xef\xbb\xbf" + (json.dumps(saved, indent=3) + "\r\n").encode("utf-8")
    path.write_bytes(original)
    monkeypatch.setenv("OPENAI_API_KEY", "settings-test-secret")

    result = load_settings(path)

    assert result["operating_contract_version"] == 2
    assert result["profile_registry_version"] == 4
    assert (result["target_ticket_count"], result["max_ticket_count"]) == (15, 30)
    assert (result["target_min_odds"], result["target_max_odds"]) == (15.0, 40.0)
    assert result["enabled_profiles"][:2] == saved["enabled_profiles"]
    assert len(result["enabled_profiles"]) == 30 and set(result["enabled_profiles"]) == set(PROFILE_IDS)
    assert result["strictness"] == 71 and result["stake_eur"] == 1.0
    assert result["openai"] == {"backend": "codex_chatgpt", **saved["openai"]}
    assert "settings-test-secret" not in json.dumps(result)
    assert load_settings(path) == result and path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_current_load_enables_research_roster_without_replacing_other_settings(tmp_path):
    path = tmp_path / "settings.json"
    saved = {**deepcopy(DEFAULT_SETTINGS), "enabled_profiles": ["daily223", "gauss"],
             "target_ticket_count": 20, "target_min_odds": 16.0, "target_max_odds": 38.0}
    path.write_text(json.dumps(saved), encoding="utf-8")
    original = path.read_bytes()
    result = load_settings(path)
    assert set(result["enabled_profiles"]) == set(PROFILE_IDS)
    assert {k: v for k, v in result.items() if k != "enabled_profiles"} == {
        k: v for k, v in saved.items() if k != "enabled_profiles"
    }
    assert path.read_bytes() == original


@pytest.mark.parametrize("stored_marker", [False, True])
def test_explicit_legacy_replay_keeps_old_rules_roster_and_bytes(tmp_path, stored_marker):
    path = tmp_path / "archive.json"
    saved = {
        "profile_registry_version": 4, "target_ticket_count": 2, "max_ticket_count": 5,
        "target_min_odds": 10.0, "target_max_odds": 50.0,
        "enabled_profiles": ["daily223", "merlin"],
    }
    if stored_marker:
        saved["operating_contract_version"] = 1
    path.write_text(json.dumps(saved), encoding="utf-8")
    original = path.read_bytes()
    result = load_settings(path, **({} if stored_marker else {"operating_contract_version": 1}))
    assert result == validate_settings({**saved, "operating_contract_version": 1})
    assert (result["target_ticket_count"], result["max_ticket_count"]) == (2, 5)
    assert (result["target_min_odds"], result["target_max_odds"]) == (10.0, 50.0)
    assert result["enabled_profiles"] == ["daily223", "merlin"]
    assert path.read_bytes() == original


def test_missing_file_uses_current_defaults_without_creating_it(tmp_path):
    path = tmp_path / "missing.json"
    assert load_settings(path) == validate_settings({})
    assert not path.exists()
    assert load_settings(path, operating_contract_version=1) == validate_settings({
        "operating_contract_version": 1
    })


@pytest.mark.parametrize("patch", [
    {"operating_contract_version": True}, {"operating_contract_version": "1"},
    {"operating_contract_version": 0}, {"operating_contract_version": 3},
    {"target_ticket_count": True}, {"target_ticket_count": 14}, {"target_ticket_count": 31},
    {"max_ticket_count": True}, {"max_ticket_count": 5}, {"max_ticket_count": 30.0},
    {"stake_eur": True}, {"stake_eur": 2},
    {"target_min_odds": True}, {"target_min_odds": 14.99},
    {"target_max_odds": 40.01}, {"target_max_odds": float("inf")},
    {"target_min_odds": float("nan")}, {"target_min_odds": 35, "target_max_odds": 20},
])
def test_current_validation_rejects_legacy_rules_and_invalid_ranges(patch):
    with pytest.raises(ValueError):
        validate_settings(patch)


@pytest.mark.parametrize("patch", [
    {"target_ticket_count": False}, {"target_ticket_count": 0}, {"target_ticket_count": 6},
    {"max_ticket_count": 30}, {"max_ticket_count": True}, {"stake_eur": True},
    {"target_min_odds": True}, {"target_min_odds": 1.99}, {"target_max_odds": 101},
    {"target_min_odds": 60, "target_max_odds": 50},
])
def test_legacy_validation_still_rejects_invalid_ranges(patch):
    with pytest.raises(ValueError):
        validate_settings({"operating_contract_version": 1, **patch})


def test_current_and_legacy_boundaries_and_no_argument_mutation():
    current = {"target_ticket_count": 30, "target_min_odds": 15, "target_max_odds": 40}
    legacy = {"operating_contract_version": 1, "target_ticket_count": 5,
              "target_min_odds": 2, "target_max_odds": 100}
    before = deepcopy((current, legacy))
    assert validate_settings(current)["target_ticket_count"] == 30
    assert validate_settings(legacy)["max_ticket_count"] == 5
    assert (current, legacy) == before


@pytest.mark.parametrize("patch", [
    {"target_ticket_count": True}, {"max_ticket_count": 5.0},
    {"profile_registry_version": 2.0}, {"profile_registry_version": 3.0},
    {"enabled_profiles": ["daily223", "daily223"]},
    {"enabled_profiles": ["daily223", "unknown"]},
    {"enabled_profiles": ["daily223", []]}, {"enabled_profiles": ["merlin"]},
])
def test_effective_migration_does_not_hide_invalid_saved_settings(tmp_path, patch):
    path = tmp_path / "invalid.json"
    saved = {"target_ticket_count": 2, "max_ticket_count": 5,
             "target_min_odds": 10, "target_max_odds": 50, **patch}
    path.write_text(json.dumps(saved), encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(ValueError):
        load_settings(path)
    assert path.read_bytes() == original
