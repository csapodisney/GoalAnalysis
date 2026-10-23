"""Daily specialist recommendations, independent of the selected wager portfolio."""

from copy import deepcopy
from datetime import datetime

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.portfolio import MODEL_PROFILE_IDS
from goal_analysis.engine.portfolio import PROFILES, _matches_profile, _product
from goal_analysis.engine.specialist_contracts import (
    SPECIALIST_CONTRACTS,
    contract_metadata,
    valid_specialist_construction,
)
from goal_analysis.quote_metadata import quote_metadata
from goal_analysis.tipsters.catalog import TIPSTER_PROFILE_IDS

VERSION = "arthur-daily-recommendations-v3-evidence-first-specialists"
DAILY223_VERSION = "arthur-daily-recommendations-v2-verified-construction"
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
        if profile in (*MODEL_PROFILE_IDS, *TIPSTER_PROFILE_IDS):
            continue  # Independent numeric recommendations are supplied by their own engine.
        refusal = report.get("diversification", {}).get("abstained_profiles", {}).get(profile)
        if profile != "daily223" and refusal is not None:
            from goal_analysis.engine.diversification import abstain

            output.append(abstain({
                "profile_id": profile, "profile_name": PROFILES[profile]["name"],
                "date": enriched["date"], "created_at": now.isoformat(),
                "strategy_version": VERSION, "quality_warnings": [],
            }, refusal.get("reason_code", "NO_ELIGIBLE_NON_DUPLICATE_TICKET"), refusal))
            continue
        base = proposals.get(profile)
        legs = [
            deepcopy(index[l["candidate_id"]])
            for l in (base or {}).get("legs", [])
            if l["candidate_id"] in index
        ]
        if base and (
            len(legs) != len(base["legs"])
        ):
            legs, base = [], None
        if profile in SPECIALIST_CONTRACTS and (
            not base or not valid_specialist_construction({**base, "legs": legs})
        ):
            from goal_analysis.engine.diversification import abstain

            output.append(abstain({
                "profile_id": profile, "profile_name": PROFILES[profile]["name"],
                "date": enriched["date"], "created_at": now.isoformat(),
                "strategy_version": SPECIALIST_CONTRACTS[profile]["version"],
                "quality_warnings": [], "probability": None, "odds_pending": False,
                "confidence_kind": "EVIDENCE_STRENGTH_NOT_CALIBRATED_PROBABILITY",
                **contract_metadata(profile),
            }, "SPECIALIST_CONSTRUCTION_NOT_MET"))
            continue
        if not legs:
            pool = [c for c in candidates if profile == "daily223" or _matches_profile(c, profile)]
            # Review coverage is bounded. An unreviewed specialist candidate is
            # still eligible; silently whitelisting the shared review shortlist
            # discarded better local evidence and narrowed unrelated profiles.
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
        elif profile == "daily223":
            original_legs = {leg["candidate_id"]: leg for leg in base["legs"]}
            for leg in legs:
                for key in ("slot", "target_price"):
                    if key in original_legs[leg["candidate_id"]]:
                        leg[key] = original_legs[leg["candidate_id"]][key]
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
        target_proposal = profile == "daily223" and not base
        unconstructed = profile != "daily223" and not base
        if target_proposal or unconstructed:
            confidence = "LOW"
        if unconstructed:
            warnings.append(
                "Nem készült a profil ellenőrzött feltételeinek megfelelő szelvény; ezek kutatási ötletek."
            )
        selected_ticket = selected.get(profile)
        if selected_ticket and {l["candidate_id"] for l in selected_ticket["legs"]} != {
            l["candidate_id"] for l in legs
        }:
            selected_ticket = None
        identity = [
            [l["fixture_id"], l["market_key"], l["selection_key"], l["period"]] for l in legs
        ]
        strategy_version = DAILY223_VERSION if profile == "daily223" else VERSION
        if profile in SPECIALIST_CONTRACTS:
            strategy_version = SPECIALIST_CONTRACTS[profile]["version"]
        output.append(
            {
                "recommendation_id": canonical_sha256(
                    [strategy_version, enriched["date"], profile, now.isoformat(), identity]
                )[:24],
                "profile_id": profile,
                "profile_name": PROFILES[profile]["name"],
                "date": enriched["date"],
                "created_at": now.isoformat(),
                "strategy_version": strategy_version,
                "status": "TARGET_PROPOSAL" if target_proposal else "DRAFT" if unconstructed else "ODDS_PENDING" if odds_pending else "REVIEWED" if reviewed else "DRAFT",
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
                **({"daily223_constraints_verified": not target_proposal,
                    "daily223_odds_band": (base or {}).get("odds_band")}
                   if profile == "daily223" else {}),
                **({"construction_verified": not unconstructed,
                    "selection_mode": (base or {}).get("selection_mode", "UNCONSTRUCTED_RESEARCH_IDEAS"),
                    "minimum_combined_price": None, "minimum_legs": 1}
                   if profile != "daily223" else {}),
                **contract_metadata(profile),
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
    """Keep selected portfolio legs, then round-robin the wider profile set.

    ``extras`` starts with the preliminary portfolio's selected legs. Letting the
    larger recommendation pool run first could consume the bounded Astra window
    and leave the final DAILY_223 legs unreviewed on a broad match calendar.
    """
    result, seen = [], set()
    for candidate in extras:
        if len(result) >= maximum:
            return result
        if candidate["candidate_id"] not in seen:
            result.append(deepcopy(candidate))
            seen.add(candidate["candidate_id"])
    groups = [r["legs"] for r in recommendations if r["legs"]]
    for i in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if len(result) >= maximum:
                return result
            if i < len(group):
                c = group[i]
                if c["candidate_id"] not in seen:
                    result.append(deepcopy(c))
                    seen.add(c["candidate_id"])
    return result
