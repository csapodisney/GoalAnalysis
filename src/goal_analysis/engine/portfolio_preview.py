"""Unpriced, explicitly provisional match selections; never financial tickets."""

from collections import Counter
from copy import deepcopy
from datetime import datetime

from goal_analysis.agents import canonical_sha256
from goal_analysis.features.daily_223_history import enrich_daily223_input

from .portfolio import PROFILES, permissive


def preview_candidates(fixtures, priced_input, history, settings):
    priced = {
        (c["fixture_id"], c["market_key"], c["selection_key"], c["period"])
        for c in priced_input["candidates"]
    }
    profiles = [p for p in settings["enabled_profiles"] if PROFILES[p]["market"]]
    if not profiles:
        profiles = ["kronikas"]
    rows = []
    now = datetime.fromisoformat(priced_input["observed_at"])
    for fixture in fixtures:
        if (
            fixture["provider_status"] != "NS"
            or datetime.fromisoformat(fixture["kickoff"]) <= now
        ):
            continue
        for profile in profiles:
            market, selection, period = PROFILES[profile]["market"]
            for choice in [selection] if selection else ["home", "away"]:
                if (fixture["fixture_id"], market, choice, period) in priced:
                    continue
                rows.append(
                    {
                        **fixture,
                        "candidate_id": f"preview:{fixture['fixture_id']}:{profile}:{choice}",
                        "profile_id": profile,
                        "market_key": market,
                        "selection_key": choice,
                        "period": period,
                        "decimal_price": None,
                        "bookmaker": None,
                        "odds_pending": True,
                        "evidence": [],
                    }
                )
    enriched = enrich_daily223_input({**priced_input, "candidates": rows}, history)
    analyses = {c["candidate_id"]: c for c in enriched["history_analysis"]["candidates"]}
    for row in enriched["candidates"]:
        row["historical_context"] = analyses[row["candidate_id"]]
        row["support_score"] = round(
            sum(e["strength"] * e["reliability"] for e in row["evidence"]), 4
        )
        row["missing_support"] = [
            category
            for category in ("historical", "venue_form")
            if not any(e["category"] == category and e["strength"] > 0 for e in row["evidence"])
        ]
    return enriched["candidates"]


def build_previews(candidates, settings, priced_tickets, now, review=None):
    remaining = min(settings["target_ticket_count"], settings["max_ticket_count"]) - len(
        priced_tickets
    )
    if remaining <= 0:
        return []
    reviews = {r["candidate_id"]: r for r in (review or {}).get("reviews", [])}
    existing = {t["profile_id"] for t in priced_tickets}
    groups = {}
    for row in candidates:
        if row["profile_id"] in existing or datetime.fromisoformat(row["kickoff"]) <= now:
            continue
        if not permissive(settings) and row["missing_support"]:
            continue
        groups.setdefault(row["profile_id"], []).append(row)
    exposure, tickets = Counter(), []
    for profile, rows in sorted(
        groups.items(), key=lambda group: (-max(r["support_score"] for r in group[1]), group[0])
    ):
        legs, used = [], set()
        for row in sorted(
            rows,
            key=lambda r: (
                exposure[r["fixture_id"]],
                -r["support_score"],
                r["kickoff"],
                r["candidate_id"],
            ),
        ):
            if row["fixture_id"] in used:
                continue
            leg = deepcopy(row)
            leg["astra_review"] = reviews.get(row["candidate_id"], {})
            legs.append(leg)
            used.add(row["fixture_id"])
            if len(legs) == 3:
                break
        warnings = [
            "A szorzó és a fogadóirodai elérhetőség hiányzik. Frissítésig előzetes összeállítás; összszorzó és várható kifizetés nem számítható."
        ]
        if any(l["missing_support"] for l in legs):
            warnings.append(
                "Hiányos történeti / formaadat: a profil piaci ötletet ad, teljes statisztikai alátámasztás nélkül."
            )
        if not review or review.get("status") != "COMPLETE":
            warnings.append(
                "Astra ellenőrzése nem áll rendelkezésre; a helyi profilválasztást látod."
            )
        elif any(leg["candidate_id"] not in reviews for leg in legs):
            warnings.append(
                "Astra ellenőrzése nem minden jelöltre készült el; az érintett lábak helyi profilválasztások."
            )
        tickets.append(
            {
                "preview_id": canonical_sha256([l["candidate_id"] for l in legs])[:20],
                "profile_id": profile,
                "profile_name": PROFILES[profile]["name"],
                "date": legs[0]["target_date"],
                "created_at": now.isoformat(),
                "status": "ODDS_PENDING",
                "odds_pending": True,
                "playable": False,
                "combined_price": None,
                "bookmaker": None,
                "stake_eur": None,
                "legs": legs,
                "quality_warnings": warnings,
                "reasoning": [
                    "Az elérhető történeti minta alapján rangsorolt profiljelöltek. Hiányzó minta esetén a kezdési idő dönti el a sorrendet."
                ],
                "risk_notes": [
                    "A profilok külön alternatívák; egymással ellentétes kimenetelt is vizsgálhatnak."
                ],
            }
        )
        exposure.update(used)
        if len(tickets) >= remaining:
            break
    return tickets
