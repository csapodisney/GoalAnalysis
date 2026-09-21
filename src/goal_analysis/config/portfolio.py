"""Versioned operating settings for the independent Arthur tipster desk."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

PROFILE_IDS = ["daily223", "kronikas", "ritmusor", "parharcmester", "orszem", "merlin", "h1_over05"]
DEFAULT_SETTINGS = {
    "schema_version": 3,
    "strictness": 35,
    "target_ticket_count": 2,
    "max_ticket_count": 5,
    "stake_eur": 5.0,
    "target_min_odds": 10.0,
    "target_max_odds": 40.0,
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
    result = deepcopy(DEFAULT_SETTINGS)
    result.update(deepcopy(value))
    if result["schema_version"] != 3:
        raise ValueError("Arthur settings schema_version 3 szükséges.")
    for name, low, high in (("strictness", 0, 100), ("target_ticket_count", 1, 5)):
        if type(result[name]) is not int or not low <= result[name] <= high:
            raise ValueError(f"{name}: {low}–{high} közötti egész szám szükséges.")
    if type(result["max_ticket_count"]) is not int or result["max_ticket_count"] != 5:
        raise ValueError("A napi felső korlát öt szelvény, a DAILY_223-mal együtt.")
    if type(result["stake_eur"]) not in (int, float) or result["stake_eur"] != 5:
        raise ValueError("A rögzített tét szelvényenként 5 EUR.")
    for name in ("target_min_odds", "target_max_odds"):
        if type(result[name]) not in (int, float) or not 2 <= result[name] <= 100:
            raise ValueError(f"Érvénytelen {name}.")
    if result["target_min_odds"] > result["target_max_odds"]:
        raise ValueError("A cél-szorzótartomány fordított.")
    profiles = result["enabled_profiles"]
    if not isinstance(profiles, list) or any(p not in PROFILE_IDS for p in profiles):
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
    if ai["reasoning_effort"] not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError("Érvénytelen Astra reasoning_effort.")
    if type(ai["web_search"]) is not bool:
        raise ValueError("web_search: logikai érték szükséges.")
    if type(ai["max_output_tokens"]) is not int or not 2000 <= ai["max_output_tokens"] <= 12000:
        raise ValueError("max_output_tokens: 2000–12000 közötti egész szám szükséges.")
    if type(ai["max_candidates"]) is not int or not 15 <= ai["max_candidates"] <= 30:
        raise ValueError("max_candidates: 15–30 közötti egész szám szükséges.")
    result["openai"] = ai
    return result


def load_settings(path: Path) -> dict:
    return (
        validate_settings(json.loads(path.read_text("utf-8-sig")))
        if path.exists()
        else validate_settings({})
    )


def save_settings(path: Path, value: dict) -> dict:
    validated = validate_settings(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(validated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return validated
