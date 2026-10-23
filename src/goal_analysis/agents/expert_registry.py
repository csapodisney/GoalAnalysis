"""Read-only role directory derived from Arthur's actual profile catalogues.

A configured role is not a successful invocation, and twenty personas are not
twenty independently trained language models. This directory does not run any
agent, access credentials, modify membership or change published tickets.
"""

from __future__ import annotations

from goal_analysis.config.portfolio import MODEL_PROFILE_IDS, PROFILE_IDS
from goal_analysis.engine.portfolio import PROFILES
from goal_analysis.jobs.grok_advisory import enabled as grok_enabled
from goal_analysis.jobs.nemotron import enabled as codex_advisory_enabled
from goal_analysis.tipsters.catalog import TIPSTER_PROFILE_IDS, get_catalogue


def network_registry(root, settings, report, expert_advisory, team) -> list[dict]:
    """Describe available modules/configuration and the current saved expert view."""
    settings, report = settings or {}, report or {}
    expert_advisory, team = expert_advisory or {}, team or {}
    enabled = set(settings.get("enabled_profiles", []))
    members = {row["profile_id"]: row for row in team.get("members", [])
               if isinstance(row, dict) and isinstance(row.get("profile_id"), str)}
    personas = {row["profile_id"]: row for row in get_catalogue()}
    ai = settings.get("openai") or {}
    rows = [
        {"id": "arthur_astra", "name": "Arthur · Astra", "role": "Közös elemzés és összeállítás",
         "status": "CONFIGURED" if ai.get("backend") == "codex_chatgpt" else "UNKNOWN",
         "detail": ("A mentett Codex-beállítást használja. A beállítás nem igazolja az aktuális "
                    "kapcsolatot vagy egy elemzés lefutását.")},
        {"id": "daniel", "name": "Dániel", "role": "Ellenérvek és szerkezeti kockázatok vizsgálata",
         "status": "CONFIGURED" if ai.get("backend") == "codex_chatgpt" else "UNKNOWN",
         "detail": "A közös elemzésben a legerősebb ellenérvet keresi és a szükséges vétót jelzi; nem külön független modell."},
        {"id": "szent_gral", "name": "Szent Grál", "role": "Csapattagság és próbamérés",
         "status": "LOCAL" if team.get("mode") == "LIVE_PROFILE_SUPERVISION" else "NOT_RUN",
         "detail": "A mentett teljesítményből követi a tagok állapotát; a részletes kártyák lent láthatók."},
    ]
    for profile_id in PROFILE_IDS:
        person = personas.get(profile_id)
        member = members.get(profile_id, {})
        status = "CONFIGURED" if profile_id in enabled else "DISABLED"
        if member.get("status") in {"PROBATION", "SUSPENDED"}:
            status = member["status"] if profile_id in enabled else "DISABLED"
        if profile_id == "daily223":
            role, status, detail = (
                "Önálló 2×2×3 konstrukció", "PROTECTED",
                "Védett szabályrendszer; a közös szakértői súlyozás nem módosítja.",
            )
        elif profile_id in MODEL_PROFILE_IDS:
            role = "Helyi numerikus modell"
            detail = "Saját matematikai módszerrel számol; nem külön nyelvimodell-hívás."
        elif profile_id in TIPSTER_PROFILE_IDS:
            role = "Fiktív klubtipster · állandó kiválasztási szabályok"
            detail = person["tagline"] + " Közös adatokra épül, nem külön betanított LLM."
        else:
            role = "Saját piaci szabályokat követő specialista"
            detail = "Önálló szelvénystílus a közösen ellenőrzött mérkőzéskínálatból."
        rows.append({"id": profile_id, "name": PROFILES[profile_id]["name"],
                     "role": role, "status": status, "detail": detail})
    rows.extend([
        {"id": "codex_advisory", "name": "Codex · második vélemény",
         "role": "Kockázati és adatminőségi ellenőrzés",
         "status": "CONFIGURED" if codex_advisory_enabled(root) else "DISABLED",
         "detail": "A korábbi Nemotron-szerepkört Codex végzi; nincs aktív Nemotron API-hívás."},
        {"id": "grok_review", "name": "Grok · közös szelvényvélemény",
         "role": "A teljes csomag érvei és kifogásai egy kérésben",
         "status": "CONFIGURED" if grok_enabled(root) else "DISABLED",
         "detail": "A már letöltött adatcsomagot kapja. A bekapcsolás nem jelenti, hogy minden mai vélemény elkészült."},
        {"id": "historical_learning", "name": "Történeti tanulás",
         "role": "Eredmények, adatminőség és fejlődési tervek",
         "status": "LOCAL", "detail": "A megőrzött naplókat elemzi; új súlyokat csak ellenőrzéssel lehet bevezetni."},
    ])
    available = expert_advisory.get("status") == "AVAILABLE"
    if report and expert_advisory.get("run_id") != report.get("run_id"):
        available = False
    if report and expert_advisory.get("date") != report.get("date"):
        available = False
    weather = expert_advisory.get("weather") or {}
    weather_rows = weather.get("fixtures", []) if available else []
    weighted = sum(row.get("live_weight_eligible") is True for row in weather_rows)
    motivation = expert_advisory.get("motivation") or {}
    motivation_rows = motivation.get("fixtures", []) if available else []
    supported = sum(bool(row.get("standing_evidence") or row.get("human_factors")
                         or row.get("standings_context")) for row in motivation_rows)
    rows.extend([
        {"id": "weather_expert", "name": "Időjárási szakértő",
         "role": "Időjárás, csapatspecifikus minták és hasonló ellenfelek",
         "status": "ADVISORY" if weighted else "INSUFFICIENT_DATA" if available else "NOT_RUN",
         "detail": (f"{len(weather_rows)} vizsgált meccs; {weighted} súlyozásra alkalmas eredmény. "
                    "A történeti megfigyelés és az előrejelzés külön ellenőrzést kap."
                    if available else "Ehhez a csomaghoz még nincs mentett szakértői vélemény.")},
        {"id": "motivation_expert", "name": "Motivációs szakértő · Grok",
         "role": "Tabella, versenytét és forrásolt hírek",
         "status": "ADVISORY" if supported else "INSUFFICIENT_DATA" if available else "NOT_RUN",
         "detail": (f"{supported} meccshez van tabella vagy forrásolt állítás. A tabella önmagában "
                    "nem bizonyít játékosi motivációt; a nézőpont a közös Grok-csomag része."
                    if available else "A közös Grok-csomaghoz készít elő bizonyítékot; még nincs mentett napi vélemény.")},
    ])
    return rows
