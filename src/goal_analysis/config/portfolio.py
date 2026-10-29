"""Versioned operating settings for the independent Arthur tipster desk."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from goal_analysis.operating_policy import (
    MAX_GENERATED_TICKETS,
    MIN_DAILY_TICKETS,
    OPERATING_CONTRACT_VERSION,
    TARGET_MAX_ODDS,
    TARGET_MIN_ODDS,
)
from goal_analysis.tipsters.catalog import TIPSTER_PROFILE_IDS

MODEL_PROFILE_IDS = ("gauss", "kepler", "laplace")
LEGACY_PROFILE_IDS = [
    "daily223", "kronikas", "ritmusor", "parharcmester", "orszem", "merlin", "h1_over05"
]
PROFILE_IDS = [*LEGACY_PROFILE_IDS, *MODEL_PROFILE_IDS, *TIPSTER_PROFILE_IDS]
DEFAULT_PROBATION_PROFILE_IDS = ("laplace", *TIPSTER_PROFILE_IDS)
DEFAULT_SETTINGS = {
    "schema_version": 3,
    "profile_registry_version": 4,
    "operating_contract_version": OPERATING_CONTRACT_VERSION,
    "strictness": 35,
    "target_ticket_count": MIN_DAILY_TICKETS,
    "max_ticket_count": MAX_GENERATED_TICKETS,
    "stake_eur": 1.0,
    "target_min_odds": TARGET_MIN_ODDS,
    "target_max_odds": TARGET_MAX_ODDS,
    "enabled_profiles": PROFILE_IDS,
    "openai": {
        "backend": "codex_chatgpt",
        "model": "gpt-6-astra",
        "reasoning_effort": "medium",
        "web_search": True,
        "max_output_tokens": 8000,
        "max_candidates": 30,
    },
}


def validate_settings(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - set(DEFAULT_SETTINGS):
        raise ValueError("Ismeretlen Arthur-beállítás; az API-kulcs környezeti változóba kerül.")
    version = value.get("operating_contract_version", OPERATING_CONTRACT_VERSION)
    if type(version) is not int or version not in (1, OPERATING_CONTRACT_VERSION):
        raise ValueError("Arthur operating_contract_version 1 vagy 2 szükséges.")
    result = deepcopy(DEFAULT_SETTINGS)
    if version == 1:
        # Historical/replay rules are opt-in; their recorded settings stay intact.
        result.update(operating_contract_version=1, target_ticket_count=2, max_ticket_count=5,
                      target_min_odds=10.0, target_max_odds=50.0)
    result.update(deepcopy(value))
    if result["schema_version"] != 3:
        raise ValueError("Arthur settings schema_version 3 szükséges.")
    if type(result["profile_registry_version"]) is not int or result["profile_registry_version"] not in (2, 3, 4):
        raise ValueError("Arthur profile_registry_version 2, 3 vagy 4 szükséges.")
    target_low, target_high = (1, 5) if version == 1 else (MIN_DAILY_TICKETS, MAX_GENERATED_TICKETS)
    for name, low, high in (("strictness", 0, 100), ("target_ticket_count", target_low, target_high)):
        if type(result[name]) is not int or not low <= result[name] <= high:
            raise ValueError(f"{name}: {low}–{high} közötti egész szám szükséges.")
    generation_cap = 5 if version == 1 else MAX_GENERATED_TICKETS
    if type(result["max_ticket_count"]) is not int or result["max_ticket_count"] != generation_cap:
        raise ValueError(f"A generált szelvények napi felső korlátja {generation_cap}.")
    if type(result["stake_eur"]) not in (int, float) or result["stake_eur"] != 1:
        raise ValueError("A rögzített tét szelvényenként 1 EUR.")
    odds_low, odds_high = (2, 100) if version == 1 else (TARGET_MIN_ODDS, TARGET_MAX_ODDS)
    for name in ("target_min_odds", "target_max_odds"):
        if type(result[name]) not in (int, float) or not odds_low <= result[name] <= odds_high:
            raise ValueError(f"Érvénytelen {name}.")
    if result["target_min_odds"] > result["target_max_odds"]:
        raise ValueError("A cél-szorzótartomány fordított.")
    profiles = result["enabled_profiles"]
    if not isinstance(profiles, list) or any(
        not isinstance(p, str) or p not in PROFILE_IDS for p in profiles
    ):
        raise ValueError("Ismeretlen tipsterprofil.")
    if "daily223" not in profiles or len(set(profiles)) != len(profiles):
        raise ValueError("A DAILY_223 kötelező; a profilok nem ismétlődhetnek.")
    ai = result["openai"]
    if not isinstance(ai, dict) or set(ai) - set(DEFAULT_SETTINGS["openai"]):
        raise ValueError("Ismeretlen OpenAI-beállítás.")
    ai = {**DEFAULT_SETTINGS["openai"], **ai}
    if ai["backend"] != "codex_chatgpt":
        raise ValueError("Arthur ebben a kiadásban kizárólag a ChatGPT/Codex keretet használja.")
    if ai["model"] != "gpt-6-astra":
        raise ValueError("Ehhez a kiadáshoz az egyeztetett gpt-6-astra modell tartozik.")
    if not isinstance(ai["reasoning_effort"], str) or ai["reasoning_effort"] not in {
        "low", "medium", "high", "xhigh", "max"
    }:
        raise ValueError("Érvénytelen Astra reasoning_effort.")
    if type(ai["web_search"]) is not bool:
        raise ValueError("web_search: logikai érték szükséges.")
    if type(ai["max_output_tokens"]) is not int or not 2000 <= ai["max_output_tokens"] <= 12000:
        raise ValueError("max_output_tokens: 2000–12000 közötti egész szám szükséges.")
    if type(ai["max_candidates"]) is not int or not 15 <= ai["max_candidates"] <= 30:
        raise ValueError("max_candidates: 15–30 közötti egész szám szükséges.")
    result["openai"] = ai
    return result


def load_settings(path: Path, *, operating_contract_version: int | None = None) -> dict:
    """Read effective settings without rewriting files; v1 is explicit replay only."""
    if not path.exists():
        value = {}
    else:
        value = json.loads(path.read_text("utf-8-sig"))
    if operating_contract_version is not None:
        if not isinstance(value, dict):
            return validate_settings(value)
        value["operating_contract_version"] = operating_contract_version
    if (isinstance(value, dict) and "operating_contract_version" not in value):
        # Only unversioned historical defaults are rebased. Explicit v1 remains
        # available for archived/replay use; invalid inputs still reach validation.
        target = value.get("target_ticket_count")
        if type(target) is int and 1 <= target <= 5:
            value["target_ticket_count"] = MIN_DAILY_TICKETS
        if type(value.get("max_ticket_count")) is int and value["max_ticket_count"] == 5:
            value["max_ticket_count"] = MAX_GENERATED_TICKETS
        for name, old, current in (
            ("target_min_odds", 10, TARGET_MIN_ODDS),
            ("target_max_odds", 50, TARGET_MAX_ODDS),
        ):
            if type(value.get(name)) in (int, float) and value[name] == old:
                value[name] = current
        value["operating_contract_version"] = OPERATING_CONTRACT_VERSION
    if isinstance(value, dict) and "profile_registry_version" not in value:
        # One-time effective migration for the user-requested two paper profiles.
        # Do not rewrite saved settings or reactivate previously disabled legacy profiles.
        profiles = value.get("enabled_profiles", LEGACY_PROFILE_IDS)
        if isinstance(profiles, list) and all(isinstance(p, str) for p in profiles):
            # Preserve duplicate/unknown inputs for validation rather than hiding them.
            value["enabled_profiles"] = [*profiles, *(p for p in MODEL_PROFILE_IDS if p not in profiles)]
        value["profile_registry_version"] = 2
    if (isinstance(value, dict) and type(value.get("profile_registry_version")) is int
            and value["profile_registry_version"] == 2):
        profiles = value.get("enabled_profiles", [])
        if isinstance(profiles, list) and all(isinstance(p, str) for p in profiles):
            value["enabled_profiles"] = [*profiles, *([] if "laplace" in profiles else ["laplace"])]
        value["profile_registry_version"] = 3
    if (isinstance(value, dict) and type(value.get("profile_registry_version")) is int
            and value["profile_registry_version"] == 3):
        # Effective one-time roster upgrade, never a write to the user's file.
        # Existing disabled legacy/numeric profiles remain disabled. Version-4
        # saves preserve disabled new personalities on every subsequent read.
        profiles = value.get("enabled_profiles", [*LEGACY_PROFILE_IDS, *MODEL_PROFILE_IDS])
        if isinstance(profiles, list) and all(isinstance(p, str) for p in profiles):
            value["enabled_profiles"] = [*profiles, *(p for p in TIPSTER_PROFILE_IDS if p not in profiles)]
        value["profile_registry_version"] = 4
    if (isinstance(value, dict)
            and value.get("operating_contract_version") == OPERATING_CONTRACT_VERSION):
        # Research participation is independent of PAPER/LIVE eligibility. Keep
        # invalid/duplicate entries visible to validation rather than repairing them.
        profiles = value.get("enabled_profiles", PROFILE_IDS)
        if (isinstance(profiles, list) and all(isinstance(p, str) for p in profiles)
                and "daily223" in profiles):
            value["enabled_profiles"] = [*profiles, *(p for p in PROFILE_IDS if p not in profiles)]
    return validate_settings(value)


def save_settings(path: Path, value: dict) -> dict:
    validated = validate_settings(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(validated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return validated
