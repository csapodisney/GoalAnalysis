"""Independent specialist tickets over one immutable, enriched price snapshot.

Scores are interpretable evidence heuristics, not estimated win probabilities.
Construction never places a wager and cannot promise a daily available ticket.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from math import log2
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.engine.daily_223 import (
    WEIGHTS,
    Daily223Policy,
    _choose,
    _number,
    _prepare,
    _product,
    _product_key,
    _timestamp,
    diversified_daily223_pool,
)

PROFILES = {
    "daily223": {"name": "DAILY_223 – Önálló 2×2×3", "market": None},
    "kronikas": {"name": "Krónikás – Góllánc", "market": ("totals_2_5", "over", "FULL_TIME")},
    "ritmusor": {"name": "Ritmusőr – Válaszjáték", "market": ("btts", "yes", "FULL_TIME")},
    "parharcmester": {
        "name": "Párharcmester – Erő és stílus",
        "market": ("h2h", None, "FULL_TIME"),
    },
    "orszem": {"name": "Őrszem – Kontroll", "market": ("totals_2_5", "under", "FULL_TIME")},
    "merlin": {"name": "Merlin – Egyensúly", "market": ("h2h", "draw", "FULL_TIME")},
    "h1_over05": {
        "name": "Nyitány – Első félidő over 0,5",
        "market": ("totals_0_5", "over", "FIRST_HALF"),
    },
}
DEFAULT_PROFILES = tuple(key for key in PROFILES if key != "h1_over05")
PROFILES["available"] = {"name": "Arthur – Tartalék összeállítás", "market": None}


def permissive(settings: dict) -> bool:
    return settings.get("strictness", 35) < 34


def prepare_portfolio_candidate(candidate, target, now, policy, settings):
    """Share eligibility with the reviewer; only soft data gaps may be relaxed."""
    loose = permissive(settings)
    item = _prepare(
        candidate,
        target,
        now,
        policy,
        require_history=not loose,
        allow_stale_quotes=loose,
    )
    warnings = []
    if any(key in item["missing_support"] for key in ("historical", "venue_form")):
        warnings.append(
            "Hiányos vagy gyenge történeti / hazai-vendég formaadat. Az elérhető szorzó alapján is bekerülhetett; nincs teljes statisztikai alátámasztás."
        )
    if (now - _timestamp(item["quoted_at"])).total_seconds() > policy.quote_max_age_seconds:
        warnings.append(
            "A szolgáltató szorzója öt percnél régebbi. Tájékoztató ár; megjátszás előtt ellenőrizd az irodánál."
        )
        item["requires_refresh"] = True
    if candidate.get("fixture_snapshot_stale"):
        warnings.append(
            "A mérkőzésállapot utolsó lekérése öt percnél régebbi; a kezdést és az elérhetőséget ellenőrizd."
        )
        item["requires_refresh"] = True
    item["quality_warnings"] = warnings
    return item


def _settings(settings: dict) -> dict:
    result = {
        "strictness": 35,
        "target_ticket_count": 2,
        "max_ticket_count": 5,
        "stake_eur": 5.0,
        "target_min_odds": 10.0,
        "target_max_odds": 40.0,
        "enabled_profiles": list(DEFAULT_PROFILES),
    }
    result.update({key: settings[key] for key in result if key in settings})
    _number(result["strictness"], "strictness", 0, 100)
    _number(result["stake_eur"], "stake_eur", 0.01, 100000)
    _number(result["target_min_odds"], "target_min_odds", 1.01, 1000)
    _number(result["target_max_odds"], "target_max_odds", result["target_min_odds"], 1000)
    for key in ("target_ticket_count", "max_ticket_count"):
        if type(result[key]) is not int or not 1 <= result[key] <= 5:
            raise ValueError(f"{key} must be an integer from 1 to 5")
    if result["target_ticket_count"] > result["max_ticket_count"]:
        raise ValueError("target_ticket_count cannot exceed max_ticket_count")
    enabled = result["enabled_profiles"]
    if not isinstance(enabled, list) or any(
        key not in PROFILES or key == "available" for key in enabled
    ):
        raise ValueError("enabled_profiles contains an unknown profile")
    # The user requested DAILY_223 every day, independently of optional profiles.
    result["enabled_profiles"] = list(dict.fromkeys(["daily223", *enabled]))
    return result


def _quality(item: dict) -> tuple:
    return (-item["weighted_support_score"], -item["support_score"], item["candidate_id"])


def _combination_quality(legs: tuple | list) -> tuple:
    scores = [leg["weighted_support_score"] for leg in legs]
    return (
        -min(scores),
        -sum(scores) / len(scores),
        len(legs),
        tuple(sorted(leg["candidate_id"] for leg in legs)),
    )


def _matches_profile(item: dict, identifier: str) -> bool:
    market, selection, period = PROFILES[identifier]["market"]
    return (
        item["market_key"] == market
        and item["period"] == period
        and (
            item["selection_key"] == selection
            if selection
            else item["selection_key"] in {"home", "away"}
        )
    )


def _half_history_verified(analysis: dict | None) -> bool:
    if (
        not analysis
        or analysis.get("status") != "CALCULATED"
        or analysis.get("period") != "FIRST_HALF"
    ):
        return False
    try:
        return all(
            analysis["profiles"][side][window]["sample_size"] >= 3
            for side in ("home", "away")
            for window in ("historical_20", "venue_10")
        )
    except (TypeError, KeyError):
        return False


def _specialist_pool(items: list[dict], maximum: int = 64) -> list[dict]:
    """Evidence-ranked price bands preserve routes to the combined-odds target."""
    ranked = sorted(items, key=_quality)
    if len(ranked) <= maximum:
        return ranked
    buckets = defaultdict(list)
    for item in ranked:
        buckets[int(log2(item["decimal_price"]) * 4)].append(item)
    result, seen = [], set()
    for distinct in (True, False):
        while len(result) < maximum:
            added = False
            for bucket in sorted(buckets):
                choice = next(
                    (
                        item
                        for item in buckets[bucket]
                        if item not in result and (not distinct or item["fixture_id"] not in seen)
                    ),
                    None,
                )
                if choice:
                    result.append(choice)
                    seen.add(choice["fixture_id"])
                    added = True
                if len(result) == maximum:
                    break
            if not added:
                break
    return sorted(result, key=_quality)


def _beam(states: list[tuple], keep_per_price_band: int = 6) -> list[tuple]:
    buckets, seen = defaultdict(list), set()
    for legs in sorted(states, key=_combination_quality):
        identity = tuple(sorted(leg["candidate_id"] for leg in legs))
        if identity in seen:
            continue
        seen.add(identity)
        bucket = int(log2(float(_product(legs))) * 8)
        if len(buckets[bucket]) < keep_per_price_band:
            buckets[bucket].append(legs)
    return [legs for key in sorted(buckets) for legs in buckets[key]]


def _construct_specialist(
    items: list[dict], minimum: float, maximum: float, *, allow_fallback: bool = False
) -> list[dict] | None:
    """Bounded, deterministic beam; market evidence orders all alternatives.

    A retained price band is a feasibility aid, never a confidence boost. No
    candidate below the caller's evidence threshold is added to reach odds.
    """
    states = {length: [] for length in range(1, 7)}
    upper, lower = Decimal(str(maximum)), Decimal(str(minimum))
    for item in _specialist_pool(items):
        if Decimal(str(item["decimal_price"])) > upper:
            continue
        for length in range(6, 1, -1):
            new = [
                (*legs, item)
                for legs in states[length - 1]
                if all(leg["fixture_id"] != item["fixture_id"] for leg in legs)
                and _product((*legs, item)) <= upper
            ]
            if new:
                states[length] = _beam([*states[length], *new])
        states[1] = _beam([*states[1], (item,)])
    complete = [
        legs
        for length in range(2, 7)
        for legs in states[length]
        if lower <= _product(legs) <= upper
    ]
    if complete:
        return list(min(complete, key=_combination_quality))
    if allow_fallback:
        # Keep the best available route below the target, without inventing a leg.
        alternatives = [legs for length in range(2, 7) for legs in states[length]]
        alternatives = alternatives or states[1]
        if alternatives:
            return list(
                min(alternatives, key=lambda legs: (-_product(legs), _combination_quality(legs)))
            )
        if items:
            return [min(items, key=_quality)]
    return None


def _bookmaker_rank(bookmaker: str, preferred: list[str]) -> int:
    return next(
        (
            i
            for i, key in enumerate(preferred)
            if bookmaker == key or bookmaker.startswith(key + "_")
        ),
        len(preferred),
    )


def _ticket(
    profile: str, legs: list[dict], target: str, observed: str, settings: dict, review: dict | None
) -> dict:
    records = {item.get("candidate_id"): item for item in (review or {}).get("reviews", [])}
    successful = (review or {}).get("status") == "COMPLETE"
    reviewed = successful and all(leg["candidate_id"] in records for leg in legs)
    reasons = [
        item["statement"]
        for leg in legs
        for item in leg["evidence"]
        if item.get("fresh") and item["category"] in {"historical", "venue_form"}
    ]
    risks = [
        "A pontszám szakmai alátámasztás, nem kalibrált nyerési valószínűség.",
        "A kombinált szorzó a választások szorzata; az iroda tényleges kombinációját külön ellenőrizd.",
    ]
    if not reviewed:
        risks.append("Az Astra-ellenőrzés hiányzik vagy nem teljes; ez még tervezet.")
    missing = sorted({key for leg in legs for key in leg["missing_support"]})
    if missing:
        risks.append("Még nem forrásolt értékelési területek: " + ", ".join(missing))
    if profile == "h1_over05":
        risks.append(
            "Első félidős profil: historikus H1-szorzós nyereségessége még nincs igazolva."
        )
    warnings = list(
        dict.fromkeys(message for leg in legs for message in leg.get("quality_warnings", []))
    )
    fallback = profile != "daily223" and (
        profile == "available"
        or _product(legs) < Decimal(str(settings["target_min_odds"]))
        or _product(legs) > Decimal(str(settings["target_max_odds"]))
        or len(legs) == 1
    )
    if fallback:
        warnings.append(
            "Tartalék összeállítás: a megadott célhoz nincs elegendő megfelelő választás. A ténylegesen elérhető mérkőzések és szorzók szerepelnek rajta."
        )
    risks.extend(warnings)
    output_legs = []
    for leg in legs:
        item = dict(leg)
        if successful and leg["candidate_id"] in records:
            record = records[leg["candidate_id"]]
            item["astra_review"] = record
            reasons.extend(record.get("support_arguments", []))
            risks.extend(record.get("risk_notes", []))
        output_legs.append(item)
    identity = {
        "profile_id": profile,
        "date": target,
        "observed_at": observed,
        "leg_snapshot_sha256": canonical_sha256(output_legs),
        "legs": sorted(
            (leg["candidate_id"], leg["decimal_price"], leg["quoted_at"]) for leg in legs
        ),
        "policy": settings,
    }
    return {
        "ticket_id": "ticket-" + canonical_sha256(identity)[:24],
        "profile_id": profile,
        "profile_name": PROFILES[profile]["name"],
        "date": target,
        "created_at": observed,
        "bookmaker": legs[0]["bookmaker"],
        "region": legs[0]["region"],
        "currency": legs[0]["currency"],
        "combined_price": float(_product(legs)),
        "stake_eur": settings["stake_eur"],
        "status": "READY" if reviewed and not warnings else "DRAFT",
        "quality_warnings": warnings,
        "data_quality": "LIMITED" if warnings else "STANDARD",
        "fallback": fallback,
        "requires_refresh": any(leg.get("requires_refresh", False) for leg in legs),
        "legs": output_legs,
        "support_score": round(sum(leg["support_score"] for leg in legs) / len(legs), 6),
        "weighted_support_score": round(
            sum(leg["weighted_support_score"] for leg in legs) / len(legs), 6
        ),
        "reasoning": list(dict.fromkeys(reasons)),
        "risk_notes": list(dict.fromkeys(risks)),
        "real_wager_placed": False,
        "experimental": profile == "h1_over05",
        "provisional": target
        != _timestamp(observed).astimezone(ZoneInfo("Europe/Berlin")).date().isoformat(),
    }


def build_portfolio(
    enriched_input: dict, settings: dict, *, astra_review: dict | None = None
) -> dict:
    """Build up to five independent tickets, reserving the DAILY_223 slot first.

    Requires ``enrich_daily223_input`` output. Astra reviews annotate immutable
    choices; they do not manufacture source facts or change historical scores.
    ``COMPLETE`` describes construction, while ticket DRAFT/READY describes
    whether the optional Astra review covered every selected leg.
    """
    settings = _settings(settings)
    target = date.fromisoformat(enriched_input["date"])
    now = _timestamp(enriched_input["observed_at"])
    policy = Daily223Policy(**enriched_input.get("policy", {}))
    raw = enriched_input["candidates"]
    if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
        raise ValueError("candidates must be a list of objects")
    analyses = {
        item.get("candidate_id"): item
        for item in enriched_input.get("history_analysis", {}).get("candidates", [])
    }
    products = Counter(_product_key(item) for item in raw)
    identifiers = Counter(item.get("candidate_id") for item in raw)
    prepared, diagnostics = [], list(enriched_input.get("data_issues", []))
    loose = permissive(settings)
    minimum_quality = 0 if loose else 3 + settings["strictness"] * 0.12
    reviews = {item.get("candidate_id"): item for item in (astra_review or {}).get("reviews", [])}
    for candidate in raw:
        identifier = candidate.get("candidate_id")
        try:
            if products[_product_key(candidate)] > 1 or identifiers[identifier] != 1:
                raise ValueError("DUPLICATE_CURRENT_PRODUCT")
            item = prepare_portfolio_candidate(candidate, target, now, policy, settings)
            missing_weight = sum(WEIGHTS[key] for key in item["missing_support"])
            penalty = missing_weight / 100 * settings["strictness"] * 0.30
            item["uncertainty_penalty"] = round(penalty, 6)
            caution = (astra_review or {}).get("status") == "COMPLETE" and reviews.get(
                identifier, {}
            ).get("assessment") == "caution"
            review_penalty = settings["strictness"] * 0.1 if caution else 0
            item["astra_caution_penalty"] = round(review_penalty, 6)
            item["weighted_support_score"] = round(
                max(0, item["support_score"] - penalty - review_penalty), 6
            )
            item["history_analysis"] = analyses.get(identifier)
            if item["period"] == "FIRST_HALF" and not _half_history_verified(
                item["history_analysis"]
            ):
                if not loose:
                    raise ValueError("FIRST_HALF_HISTORY_REQUIRED")
                item["quality_warnings"].append(
                    "Az első félidős történeti minta hiányos. Kísérleti választás, igazolt H1-statisztika nélkül."
                )
            for key in (
                "api_football_fixture_id",
                "api_football_home_team_id",
                "api_football_away_team_id",
            ):
                if key in candidate:
                    item[key] = candidate[key]
            if item["weighted_support_score"] < minimum_quality:
                raise ValueError("BELOW_CONFIGURED_EVIDENCE_THRESHOLD")
            if item["quality_warnings"]:
                diagnostics.append(
                    {
                        "candidate_id": identifier,
                        "status": "ACCEPTED_WITH_WARNINGS",
                        "severity": "warning",
                        "message": " ".join(item["quality_warnings"]),
                    }
                )
            prepared.append(item)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            diagnostics.append({"candidate_id": identifier, "status": str(error)})
    prepared.sort(key=_quality)
    preferred = enriched_input.get("preferred_bookmakers", ["betano"])
    profiles, proposals = [], {}
    # DAILY_223 receives its own original candidates, not any specialist's picks.
    daily_options = []
    groups = sorted({(item["bookmaker"], item["region"], item["currency"]) for item in prepared})
    prepared_by_id = {item["candidate_id"]: item for item in prepared}
    for group in groups:
        # _prepare already validated these independent raw candidates. Use the
        # same role solver with explicit uncertainty-adjusted ranking, preserving
        # every original evidence score in the emitted legs.
        scoped = [
            dict(item, support_score=item["weighted_support_score"])
            for item in prepared
            if (item["bookmaker"], item["region"], item["currency"]) == group
        ]
        search, pruning = diversified_daily223_pool(scoped, policy.tolerance)
        diagnostics.extend(pruning)
        chosen, band = _choose(search, Decimal(0)), "STRICT"
        if chosen is None and policy.tolerance:
            chosen, band = _choose(search, Decimal(str(policy.tolerance))), "NEAR_TARGET"
        if chosen:
            legs = [
                dict(
                    prepared_by_id[item["candidate_id"]],
                    slot=index + 1,
                    target_price=3.0 if index == 2 else 2.0,
                )
                for index, item in enumerate(chosen)
            ]
            daily_options.append((legs, band))
    if daily_options:
        legs, band = min(
            daily_options,
            key=lambda pair: (
                _bookmaker_rank(pair[0][0]["bookmaker"], preferred),
                _combination_quality(pair[0]),
            ),
        )
        proposals["daily223"] = _ticket(
            "daily223", legs, target.isoformat(), now.isoformat(), settings, astra_review
        )
        proposals["daily223"]["odds_band"] = band
    profiles.append(
        {
            "profile_id": "daily223",
            "profile_name": PROFILES["daily223"]["name"],
            "candidate_count": len(prepared),
            "construction_status": "COMPLETE" if daily_options else "CONSTRUCTION_INCOMPLETE",
            "reason": ""
            if daily_options
            else "Nincs három külön mérkőzés ugyanannál az irodánál a 2×2×3 feltételekkel.",
        }
    )
    for identifier in settings["enabled_profiles"]:
        if identifier == "daily223":
            continue
        matching = [item for item in prepared if _matches_profile(item, identifier)]
        options = []
        for group in groups:
            scoped = [
                item
                for item in matching
                if (item["bookmaker"], item["region"], item["currency"]) == group
            ]
            if len(scoped) > 64:
                diagnostics.append(
                    {
                        "profile_id": identifier,
                        "bookmaker": group[0],
                        "status": "SPECIALIST_DIVERSIFIED_SEARCH_POOL",
                        "input_candidates": len(scoped),
                        "searched_candidates": 64,
                    }
                )
            if len({item["fixture_id"] for item in scoped}) < (1 if loose else 2):
                continue
            selected = _construct_specialist(
                scoped,
                settings["target_min_odds"],
                settings["target_max_odds"],
                allow_fallback=loose,
            )
            if selected:
                options.append(selected)
        if options:
            selected = min(
                options,
                key=lambda legs: (
                    _product(legs) < Decimal(str(settings["target_min_odds"])),
                    _bookmaker_rank(legs[0]["bookmaker"], preferred),
                    _combination_quality(legs),
                ),
            )
            proposals[identifier] = _ticket(
                identifier, selected, target.isoformat(), now.isoformat(), settings, astra_review
            )
        profiles.append(
            {
                "profile_id": identifier,
                "profile_name": PROFILES[identifier]["name"],
                "candidate_count": len(matching),
                "construction_status": "COMPLETE" if options else "CONSTRUCTION_INCOMPLETE",
                "reason": ""
                if options
                else "Kevés alátámasztott választás, hiányzó piac vagy nem áll össze a célösszszorzó egy irodánál.",
            }
        )
    if loose and not proposals and prepared:
        options = [
            _construct_specialist(
                [
                    item
                    for item in prepared
                    if (item["bookmaker"], item["region"], item["currency"]) == group
                ],
                settings["target_min_odds"],
                settings["target_max_odds"],
                allow_fallback=True,
            )
            for group in groups
        ]
        options = [legs for legs in options if legs]
        if options:
            legs = min(
                options,
                key=lambda legs: (
                    _bookmaker_rank(legs[0]["bookmaker"], preferred),
                    _combination_quality(legs),
                ),
            )
            proposals["available"] = _ticket(
                "available", legs, target.isoformat(), now.isoformat(), settings, astra_review
            )
            profiles.append(
                {
                    "profile_id": "available",
                    "profile_name": PROFILES["available"]["name"],
                    "candidate_count": len(prepared),
                    "construction_status": "COMPLETE",
                    "reason": "A szigorú profilok helyett a ténylegesen elérhető választásokból készült tartalék.",
                }
            )
    tickets = [proposals["daily223"]] if "daily223" in proposals else []
    specialists = sorted(
        (ticket for key, ticket in proposals.items() if key != "daily223"),
        key=lambda ticket: (
            ticket["fallback"],
            _combination_quality(ticket["legs"]),
            ticket["profile_id"],
        ),
    )

    # Target controls publication volume; the hard cap includes DAILY_223.
    def exposure(ticket):
        return tuple(
            sorted(
                (leg["fixture_id"], leg["market_key"], leg["selection_key"], leg["period"])
                for leg in ticket["legs"]
            )
        )

    exposures = {exposure(ticket) for ticket in tickets}
    duplicate_profiles = set()
    for ticket in specialists:
        if exposure(ticket) in exposures:
            duplicate_profiles.add(ticket["profile_id"])
            diagnostics.append(
                {"profile_id": ticket["profile_id"], "status": "DUPLICATE_TICKET_EXPOSURE"}
            )
            continue
        if len(tickets) >= min(settings["target_ticket_count"], settings["max_ticket_count"]):
            continue
        tickets.append(ticket)
        exposures.add(exposure(ticket))
    published = {ticket["profile_id"] for ticket in tickets}
    for profile in profiles:
        profile["published"] = profile["profile_id"] in published
        if profile["profile_id"] in duplicate_profiles:
            profile["reason"] = (
                "Az önálló elemzés azonos választásokat adott egy már megjelenített szelvénnyel; külön szelvényként nem ismételjük meg."
            )
        elif profile["construction_status"] == "COMPLETE" and not profile["published"]:
            profile["reason"] = (
                "Elkészült alternatíva; a beállított napi szelvényszám korlátozza a megjelenítést."
            )
    fixture_use = defaultdict(list)
    for ticket in tickets:
        for leg in ticket["legs"]:
            fixture_use[leg["fixture_id"]].append(ticket["ticket_id"])
    overlap = [
        {"fixture_id": fixture, "ticket_ids": ids, "ticket_count": len(ids)}
        for fixture, ids in sorted(fixture_use.items())
        if len(ids) > 1
    ]
    count = len(tickets)
    complete = count >= settings["target_ticket_count"] and "daily223" in published
    report = {
        "schema_version": 3,
        "module": "ARTHUR_PORTFOLIO",
        "date": target.isoformat(),
        "observed_at": now.isoformat(),
        "timezone": policy.timezone_name,
        "construction_status": "COMPLETE"
        if complete
        else "PARTIAL"
        if count
        else "CONSTRUCTION_INCOMPLETE",
        "tickets": tickets,
        "profile_proposals": list(proposals.values()),
        "profiles": profiles,
        "diagnostics": diagnostics,
        "overlap": overlap,
        "policy": {
            **settings,
            "strategy_version": "arthur-portfolio-v2-permissive",
            "data_veto_enabled": not loose,
            "fallback_enabled": loose,
            "score_kind": "EVIDENCE_HEURISTIC_NOT_PROBABILITY",
            "minimum_weighted_support": round(minimum_quality, 6),
            "uncertainty_penalty_formula": "missing_weight / 100 * strictness * 0.30",
            "astra_caution_penalty_formula": "strictness * 0.10 for reviewed caution",
            "search_method": "BOUNDED_DIVERSIFIED_BEAM_NOT_EXHAUSTIVE_OPTIMUM",
        },
        "input_candidate_count": len(raw),
        "eligible_candidate_count": len(prepared),
        "astra_status": (astra_review or {}).get("status", "NOT_RUN"),
        "real_wager_placed": False,
        "betting_approved": False,
    }
    report["report_sha256"] = canonical_sha256(report)
    return report
