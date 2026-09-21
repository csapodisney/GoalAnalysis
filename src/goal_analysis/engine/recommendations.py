"""Daily specialist recommendations, independent of the selected wager portfolio."""

from copy import deepcopy
from datetime import datetime

from goal_analysis.agents import canonical_sha256
from goal_analysis.engine.portfolio import PROFILES, _matches_profile, _product
from goal_analysis.quote_metadata import quote_metadata

VERSION = "arthur-daily-recommendations-v1"
TOPICS = ("weather", "coach", "lineup", "absences", "workload", "motivation")


def build_recommendations(enriched, unpriced, settings, now, report, review=None):
    """Produce every enabled profile when real, upcoming fixtures exist.

    Missing evidence reduces confidence, never changes a fixture or invents odds.
    DAILY_223 without verified slots remains a labelled target proposal.
    """
    reviews = {r["candidate_id"]: r for r in (review or {}).get("reviews", [])}
    proposals = {t["profile_id"]: t for t in report.get("profile_proposals", [])}
    selected = {t["profile_id"]: t for t in report.get("tickets", [])}
    analyses = {
        a["candidate_id"]: a for a in enriched.get("history_analysis", {}).get("candidates", [])
    }
    candidates = []
    for raw in enriched.get("candidates", []) + unpriced:
        if datetime.fromisoformat(raw["kickoff"]) <= now:
            continue
        c = deepcopy(raw)
        if c.get("decimal_price") is not None:
            c.update(quote_metadata(c.get("quote_timestamp_raw", c.get("quoted_at")), now))
        c["historical_context"] = analyses.get(c["candidate_id"], c.get("historical_context", {}))
        c.setdefault(
            "support_score",
            sum(e.get("strength", 0) * e.get("reliability", 0) for e in c.get("evidence", [])),
        )
        c.setdefault(
            "missing_support",
            [
                k
                for k in ("historical", "venue_form")
                if not any(
                    e.get("category") == k and e.get("strength", 0) > 0
                    for e in c.get("evidence", [])
                )
            ],
        )
        c["astra_review"] = reviews.get(c["candidate_id"], {})
        c["web_quotes"] = [
            q for q in (review or {}).get("web_odds", []) if q["candidate_id"] == c["candidate_id"]
        ]
        c["fixture_context"] = [
            x
            for x in (review or {}).get("fixture_context", [])
            if x["fixture_id"] == c["fixture_id"]
        ]
        candidates.append(c)
    index = {c["candidate_id"]: c for c in candidates}
    output = []
    for profile in settings["enabled_profiles"]:
        base = proposals.get(profile)
        legs = [
            deepcopy(index[l["candidate_id"]])
            for l in (base or {}).get("legs", [])
            if l["candidate_id"] in index
        ]
        if base and (
            len(legs) != len(base["legs"])
            or (reviews and any(l["candidate_id"] not in reviews for l in legs))
        ):
            legs, base = [], None
        if not legs:
            pool = [c for c in candidates if profile == "daily223" or _matches_profile(c, profile)]
            if reviews and any(c["candidate_id"] in reviews for c in pool):
                pool = [c for c in pool if c["candidate_id"] in reviews]
            used = set()
            for c in sorted(
                pool,
                key=lambda c: (
                    c["astra_review"].get("assessment") == "caution",
                    -c["support_score"],
                    c["kickoff"],
                    c["candidate_id"],
                ),
            ):
                if c["fixture_id"] in used:
                    continue
                legs.append(deepcopy(c))
                used.add(c["fixture_id"])
                if len(legs) == 3:
                    break
        warnings = list((base or {}).get("quality_warnings", []))
        if not legs:
            output.append(
                {
                    "profile_id": profile,
                    "profile_name": PROFILES[profile]["name"],
                    "status": "NO_FIXTURES",
                    "legs": [],
                    "confidence": "LOW",
                    "quality_warnings": [
                        "Nincs még el nem kezdődött, valós mérkőzés a napi adatforrásban."
                    ],
                }
            )
            continue
        odds_pending = any(l.get("decimal_price") is None for l in legs)
        bookmakers = {l.get("bookmaker") for l in legs if l.get("bookmaker")}
        if odds_pending:
            warnings.append(
                "Szorzó hiányzik; az ajánlat eredménye követhető, pénzügyi hozama még nem számítható."
            )
        if any(l["missing_support"] for l in legs):
            warnings.append("Hiányos történeti / formaadat; gyenge bizonyítottságú ajánlat.")
        if profile == "daily223" and not base:
            warnings.append(
                "DAILY_223 célajánlat: a három külön meccs és a 2/2/3 szorzófeltételek még nem igazoltak."
            )
            for i, leg in enumerate(legs):
                leg["target_price"] = 3.0 if i == 2 else 2.0
        reviewed = all(l["candidate_id"] in reviews for l in legs)
        if not reviewed:
            warnings.append("Astra még nem értékelte az összes választást.")
        elif (review or {}).get("research_status") != "COMPLETE_WITH_SOURCES":
            warnings.append(
                "Astra helyi adatokat értékelt; a friss webes háttér ellenőrzése hiányos."
            )
        confidence = (
            "LOW"
            if not reviewed
            or any(
                l["missing_support"] or l["astra_review"].get("assessment") == "caution"
                for l in legs
            )
            else "MEDIUM"
        )
        if (
            confidence == "MEDIUM"
            and (review or {}).get("research_status") == "COMPLETE_WITH_SOURCES"
            and all(l["astra_review"].get("assessment") == "support" for l in legs)
        ):
            confidence = "HIGH"
        selected_ticket = selected.get(profile)
        if selected_ticket and {l["candidate_id"] for l in selected_ticket["legs"]} != {
            l["candidate_id"] for l in legs
        }:
            selected_ticket = None
        identity = [
            [l["fixture_id"], l["market_key"], l["selection_key"], l["period"]] for l in legs
        ]
        output.append(
            {
                "recommendation_id": canonical_sha256(
                    [VERSION, enriched["date"], profile, now.isoformat(), identity]
                )[:24],
                "profile_id": profile,
                "profile_name": PROFILES[profile]["name"],
                "date": enriched["date"],
                "created_at": now.isoformat(),
                "strategy_version": VERSION,
                "status": "ODDS_PENDING" if odds_pending else "REVIEWED" if reviewed else "DRAFT",
                "odds_pending": odds_pending,
                "confidence": confidence,
                "confidence_kind": "EVIDENCE_STRENGTH_NOT_CALIBRATED_PROBABILITY",
                "probability": None,
                "combined_price": None if odds_pending else float(_product(legs)),
                "combined_price_kind": "THEORETICAL_PRODUCT_NOT_BOOKMAKER_COMBO_QUOTE",
                "bookmaker": next(iter(bookmakers))
                if len(bookmakers) == 1
                else "Több iroda"
                if bookmakers
                else None,
                "stake_eur": None,
                "portfolio_ticket_id": (selected_ticket or {}).get("ticket_id"),
                "selected_for_portfolio": selected_ticket is not None,
                "playable": False,
                "legs": legs,
                "quality_warnings": list(dict.fromkeys(warnings)),
                "reasoning": (base or {}).get(
                    "reasoning",
                    [
                        "A saját profil piacához tartozó, elérhető bizonyítékok szerint rangsorolt ajánlat."
                    ],
                ),
                "risk_notes": [
                    "A profilok alternatívák; azonos meccsek miatt együtt is veszíthetnek, és ellenkező kimeneteleket is javasolhatnak.",
                    "Az összszorzó a feltüntetett árak elméleti szorzata; az egyes választások árforrása a részletekben látható.",
                ],
            }
        )
    return output


def recommendation_packets(recommendations, extras, maximum):
    """Round-robin coverage so a priced profile cannot starve unpriced specialists."""
    result, seen = [], set()
    groups = [r["legs"] for r in recommendations if r["legs"]]
    for i in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if i < len(group):
                c = group[i]
                if c["candidate_id"] not in seen:
                    result.append(deepcopy(c))
                    seen.add(c["candidate_id"])
    for c in extras:
        if c["candidate_id"] not in seen:
            result.append(deepcopy(c))
            seen.add(c["candidate_id"])
    return result[:maximum]
