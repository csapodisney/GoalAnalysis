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
from goal_analysis.config.portfolio import MODEL_PROFILE_IDS
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
from goal_analysis.engine.specialist_contracts import SPECIALIST_CONTRACTS, contract_metadata
from goal_analysis.engine.ticket_scoring import ticket_quality
from goal_analysis.engine.veto_overrides import league_base_rate_context, verified_veto_override
from goal_analysis.tipsters.catalog import TIPSTER_CATALOG, TIPSTER_PROFILE_IDS

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
    "gauss": {"name": "GAUSS – Góltérkép", "market": None, "experimental": True},
    "kepler": {
        "name": "KEPLER – Erőviszony és formafordulat", "market": None, "experimental": True
    },
    "laplace": {"name": "LAPLACE – Poisson-alapvonal", "market": None, "experimental": True},
}
PROFILES.update({persona["profile_id"]: {"name": persona["name"], "market": None, "experimental": True}
                 for persona in TIPSTER_CATALOG})
SEPARATE_PROFILE_IDS = (*MODEL_PROFILE_IDS, *TIPSTER_PROFILE_IDS)
DEFAULT_PROFILES = tuple(
    key for key in PROFILES if key != "h1_over05" and key not in SEPARATE_PROFILE_IDS
)
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
    )
    warnings = []
    if any(key in item["missing_support"] for key in ("historical", "venue_form")):
        warnings.append(
            "Hiányos vagy gyenge történeti / hazai-vendég formaadat. Az elérhető szorzó alapján is bekerülhetett; nincs teljes statisztikai alátámasztás."
        )
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
        "stake_eur": 1.0,
        "target_min_odds": 10.0,
        "target_max_odds": 50.0,
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


def _specialist_scoring(legs: tuple | list) -> dict:
    """A bounded evidence utility, never a ticket win probability.

    WEIGHTS define an absolute 0..100 evidence scale. Scaling by that fixed
    range preserves differences between profiles and does not inflate a weak
    profile's best observation to certainty. Existing eligibility gates run
    before this utility is calculated.
    """
    return ticket_quality(
        [leg["weighted_support_score"] for leg in legs],
        raw_kind="EVIDENCE_HEURISTIC", scale=100.0,
    )


def _specialist_rank(legs: tuple | list) -> tuple:
    scoring = _specialist_scoring(legs)
    return (
        -scoring["ticket_score"],
        -min(scoring["normalized_leg_scores"]),
        -scoring["normalized_quality"],
        len(legs),
        tuple(sorted(leg["candidate_id"] for leg in legs)),
    )


def _bounded_specialist_combinations(options: list, limit: int = 64) -> list:
    """Retain routes of every supported length for later exposure allocation."""
    ranked = sorted((legs for legs in options if len(legs) > 1), key=_specialist_rank)
    quota, retained, seen = max(1, limit // 5), [], set()
    for length in range(2, 7):
        for legs in [legs for legs in ranked if len(legs) == length][:quota]:
            identity = tuple(sorted(leg["candidate_id"] for leg in legs))
            retained.append(legs)
            seen.add(identity)
    for legs in ranked:
        identity = tuple(sorted(leg["candidate_id"] for leg in legs))
        if len(retained) == limit:
            break
        if identity not in seen:
            retained.append(legs)
            seen.add(identity)
    return sorted(retained, key=_specialist_rank)


def _specialist_options(items: list[dict], maximum: float, *, minimum: float | None = None,
                        min_legs: int = 1, max_legs: int = 6) -> list[list[dict]]:
    """Keep every eligible single plus bounded, evidence-ranked combinations.

    Combinations contain at most six distinct fixtures, respect the original
    upper price bound, and are made within the caller's quote context. Price
    bands preserve feasible routes through the bounded search; price itself
    never increases the score. Singles remain available to the subsequent
    concentration policy even when strong combinations rank ahead of them.
    """
    upper = Decimal(str(maximum))
    eligible = [item for item in items if Decimal(str(item["decimal_price"])) <= upper]
    if not eligible:
        return []
    states = {length: [] for length in range(1, 7)}

    def beam(combinations):
        buckets, seen = defaultdict(list), set()
        for legs in sorted(combinations, key=_specialist_rank):
            identity = tuple(sorted(leg["candidate_id"] for leg in legs))
            if identity in seen:
                continue
            seen.add(identity)
            bucket = int(log2(float(_product(legs))) * 8)
            if len(buckets[bucket]) < 6:
                buckets[bucket].append(legs)
        return [legs for key in sorted(buckets) for legs in buckets[key]]

    for item in _specialist_pool(eligible):
        for length in range(6, 1, -1):
            additions = [
                (*legs, item) for legs in states[length - 1]
                if all(leg["fixture_id"] != item["fixture_id"] for leg in legs)
                and _product((*legs, item)) <= upper
            ]
            if additions:
                states[length] = beam([*states[length], *additions])
        states[1] = beam([*states[1], (item,)])
    # Floors constrain completed tickets before per-length quotas. Prefixes
    # below the floor must remain available to grow into valid accumulators.
    def complete(legs):
        return min_legs <= len(legs) <= max_legs and (
            minimum is None or _product(legs) >= Decimal(str(minimum)))

    combinations = _bounded_specialist_combinations(
        [legs for length in range(2, 7) for legs in states[length] if complete(legs)]
    )
    return [list(legs) for legs in sorted(
        [*((item,) for item in eligible if complete((item,))), *combinations], key=_specialist_rank,
    )]


def _matches_profile(item: dict, identifier: str) -> bool:
    if identifier in SEPARATE_PROFILE_IDS:
        return False  # Numeric models must never fall back to heuristic specialist picks.
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


def specialist_veto_override_contexts(enriched_input: dict, settings: dict, now=None) -> dict:
    """Offer genuine soft-rate vetoes for explicit review, without admitting them.

    The protected DAILY pipeline and input remain untouched. Merely presenting
    a context does not grant an override; construction later requires a matching
    actual COMPLETE Astra review with source-backed approval.
    """
    settings = _settings(settings)
    now = now or _timestamp(enriched_input["observed_at"])
    target = date.fromisoformat(enriched_input["date"])
    policy = Daily223Policy(**enriched_input.get("policy", {}))
    raw = enriched_input.get("candidates", [])
    if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
        return {}
    identifiers = Counter(item.get("candidate_id") for item in raw)
    try:
        products = Counter(_product_key(item) for item in raw)
    except (KeyError, TypeError):
        return {}
    analyses = {item.get("candidate_id"): item
                for item in enriched_input.get("history_analysis", {}).get("candidates", [])}
    minimum = 0 if permissive(settings) else 3 + settings["strictness"] * 0.12
    contexts = {}
    enabled = [p for p in settings["enabled_profiles"] if p != "daily223" and p not in SEPARATE_PROFILE_IDS]
    for candidate in raw:
        try:
            identifier = candidate.get("candidate_id")
            if identifiers[identifier] != 1 or products[_product_key(candidate)] != 1:
                continue
            item = prepare_portfolio_candidate(candidate, target, now, policy, settings)
            if not any(_matches_profile(item, profile) for profile in enabled):
                continue
            analysis = analyses.get(identifier)
            if item["period"] == "FIRST_HALF" and not _half_history_verified(analysis):
                continue
            missing_weight = sum(WEIGHTS[key] for key in item["missing_support"])
            support = round(max(0, item["support_score"] - missing_weight / 100 * settings["strictness"] * 0.30), 6)
            context = league_base_rate_context(candidate, analysis, support, minimum)
            if context:
                contexts[identifier] = context
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    return contexts


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
    items: list[dict], minimum: float | None, maximum: float, *, allow_fallback: bool = False
) -> list[dict] | None:
    """Bounded, deterministic beam; market evidence orders all alternatives.

    A retained price band is a feasibility aid, never a confidence boost. No
    candidate below the caller's evidence threshold is added to reach odds.
    """
    states = {length: [] for length in range(1, 7)}
    upper = Decimal(str(maximum))
    if minimum is None:
        alternatives = _specialist_options(items, maximum)
        return alternatives[0] if alternatives else None
    lower = Decimal(str(minimum))
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
    profile: str, legs: list[dict], target: str, observed: str, settings: dict, review: dict | None,
    *, quality_first: bool = False,
) -> dict:
    quality_first = quality_first and profile != "daily223"
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
        or (not quality_first and (
            _product(legs) < Decimal(str(settings["target_min_odds"]))
            or _product(legs) > Decimal(str(settings["target_max_odds"]))
            or len(legs) == 1
        ))
    )
    if fallback:
        warnings.append(
            "Tartalék kutatási összeállítás: nincs megfelelő önálló profilajánlat; a ténylegesen elérhető választás szerepel rajta."
            if quality_first else
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
    if quality_first:
        identity["selection_mode"] = "EVIDENCE_FIRST_NO_MINIMUM_ODDS"
        identity.update(contract_metadata(profile))
        identity["ticket_scoring"] = _specialist_scoring(legs)
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
        **({"confidence": "LOW"} if profile == "available" else {}),
        **({"selection_mode": "EVIDENCE_FIRST_NO_MINIMUM_ODDS",
            "minimum_combined_price": None, "maximum_combined_price": settings["target_max_odds"],
            "minimum_legs": 1,
            "selection_diagnostics": {"ticket_scoring": _specialist_scoring(legs)}}
           if quality_first else {}),
        **contract_metadata(profile),
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
    enriched_input: dict, settings: dict, *, astra_review: dict | None = None,
    allowed_profiles: list[str] | None = None,
    diversity_options: dict | None = None,
) -> dict:
    """Build up to five independent tickets, reserving the DAILY_223 slot first.

    Requires ``enrich_daily223_input`` output. Astra reviews annotate immutable
    choices; they do not manufacture source facts or change historical scores.
    ``COMPLETE`` describes construction, while ticket DRAFT/READY describes
    whether the optional Astra review covered every selected leg.
    """
    settings = _settings(settings)
    if allowed_profiles is not None:
        if any(p not in PROFILES or p == "available" for p in allowed_profiles):
            raise ValueError("Unknown managed profile")
        settings["enabled_profiles"] = [p for p in settings["enabled_profiles"] if p in allowed_profiles]
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
    prepared, daily_prepared, diagnostics = [], [], list(enriched_input.get("data_issues", []))
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
                "source_provider", "provider_fixture_id", "provider_home_team_id", "provider_away_team_id",
                "provider_league_id", "provider_season_id", "config_league_id",
                'quote_observed_at', 'quote_source_sha256', 'quote_time_basis', 'provider_bookmaker',
            ):
                if key in candidate:
                    item[key] = candidate[key]
            # DAILY_223 is a price-role constrained method, not the generic
            # specialist's absolute-frequency screen. Keep every independently
            # validated/history-supported product for its own role solver.
            # Ranking retains the existing uncertainty/caution adjustments.
            if "daily223" in settings["enabled_profiles"]:
                daily_prepared.append(item)
            if item["weighted_support_score"] < minimum_quality:
                context = league_base_rate_context(
                    candidate, item.get("history_analysis"), item["weighted_support_score"], minimum_quality,
                )
                override = verified_veto_override(
                    {**candidate, "veto_override_context": context}, review=astra_review,
                ) if context else None
                if not override or "LEAGUE_BASE_RATE" not in override["gates"]:
                    raise ValueError("BELOW_CONFIGURED_EVIDENCE_THRESHOLD")
                # The DAILY pool keeps the pre-existing item byte for byte.
                # The exception is a separate non-DAILY annotation, never a
                # score/probability increase or a history/identity relaxation.
                item = dict(item, veto_override=override)
                diagnostics.append({"candidate_id": identifier,
                                    "status": "ASTRA_SOURCE_VERIFIED_SOFT_VETO_OVERRIDE",
                                    "override": override})
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
    profiles, proposals, profile_alternatives = [], {}, {}
    # DAILY_223 receives its own original eligible pool, before the unrelated
    # specialist score floor. Its hard identity/history/price guards remain.
    daily_options = []
    groups = sorted({(item["bookmaker"], item["region"], item["currency"]) for item in prepared})
    daily_groups = sorted({(item["bookmaker"], item["region"], item["currency"]) for item in daily_prepared})
    daily_by_id = {item["candidate_id"]: item for item in daily_prepared}
    for group in daily_groups:
        # _prepare already validated these independent raw candidates. Use the
        # same role solver with explicit uncertainty-adjusted ranking, preserving
        # every original evidence score in the emitted legs.
        scoped = [
            dict(item, support_score=item["weighted_support_score"])
            for item in daily_prepared
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
                    daily_by_id[item["candidate_id"]],
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
            "candidate_count": len(daily_prepared),
            "construction_status": "COMPLETE" if daily_options else "CONSTRUCTION_INCOMPLETE",
            "reason": ""
            if daily_options
            else "Nincs három külön mérkőzés ugyanannál az irodánál a 2×2×3 feltételekkel.",
        }
    )
    if "daily223" not in settings["enabled_profiles"]:
        profiles.clear()
    for identifier in settings["enabled_profiles"]:
        if identifier == "daily223" or identifier in SEPARATE_PROFILE_IDS:
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
                        "status": "SPECIALIST_FULL_EVIDENCE_SCAN",
                        "input_candidates": len(scoped),
                        "searched_candidates": len(scoped),
                    }
                )
            if not scoped:
                continue
            contract = SPECIALIST_CONTRACTS.get(identifier)
            options.extend(_specialist_options(
                scoped, min(settings["target_max_odds"], contract["max_price"]) if contract
                else settings["target_max_odds"],
                **({"minimum": contract["min_price"], "min_legs": contract["min_legs"],
                    "max_legs": contract["max_legs"]} if contract else {}),
            ))
        option_key = lambda legs: (
            *_specialist_rank(legs)[:3],
            _bookmaker_rank(legs[0]["bookmaker"], preferred),
            len(legs),
            -_product(legs),
            _specialist_rank(legs)[-1],
        )
        options.sort(key=option_key)
        # Bound multi-ticket alternatives across quote cohorts, without dropping
        # any independently eligible single needed for later risk allocation.
        options = [legs for legs in options if len(legs) == 1] + _bounded_specialist_combinations(options)
        options.sort(key=option_key)
        profile_alternatives[identifier] = options
        if diversity_options is not None:
            diversity_options[identifier] = [
                {"quality": _specialist_scoring(legs)["ticket_score"],
                 "scoring": _specialist_scoring(legs),
                 "ticket": _ticket(
                     identifier, legs, target.isoformat(), now.isoformat(), settings, astra_review,
                     quality_first=True,
                 )} for legs in options
            ]
        if options:
            selected = options[0]
            proposals[identifier] = _ticket(
                identifier, selected, target.isoformat(), now.isoformat(), settings, astra_review,
                quality_first=True,
            )
        profiles.append(
            {
                "profile_id": identifier,
                "profile_name": PROFILES[identifier]["name"],
                "candidate_count": len(matching),
                "construction_status": "COMPLETE" if options else "CONSTRUCTION_INCOMPLETE",
                "reason": ""
                if options
                else "Nincs a profil bizonyítottsági, adat- és kombinációs feltételeinek megfelelő szelvény.",
                **contract_metadata(identifier),
            }
        )
    if loose and not proposals and prepared and allowed_profiles is None:
        options = [
            legs
            for group in groups
            for legs in _specialist_options(
                [
                    item
                    for item in prepared
                    if (item["bookmaker"], item["region"], item["currency"]) == group
                ],
                settings["target_max_odds"],
            )
        ]
        if options:
            options.sort(key=lambda legs: (
                *_specialist_rank(legs)[:3],
                _bookmaker_rank(legs[0]["bookmaker"], preferred),
                len(legs),
                -_product(legs),
                _specialist_rank(legs)[-1],
            ))
            options = [legs for legs in options if len(legs) == 1] + _bounded_specialist_combinations(options)
            options.sort(key=lambda legs: (
                *_specialist_rank(legs)[:3],
                _bookmaker_rank(legs[0]["bookmaker"], preferred),
                len(legs),
                -_product(legs),
                _specialist_rank(legs)[-1],
            ))
            legs = options[0]
            profile_alternatives["available"] = options
            if diversity_options is not None:
                diversity_options["available"] = [
                    {"quality": _specialist_scoring(legs)["ticket_score"],
                     "scoring": _specialist_scoring(legs),
                     "ticket": _ticket(
                         "available", legs, target.isoformat(), now.isoformat(), settings,
                         astra_review, quality_first=True,
                     )} for legs in options
                ]
            proposals["available"] = _ticket(
                "available", legs, target.isoformat(), now.isoformat(), settings, astra_review,
                quality_first=True,
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
            _specialist_rank(ticket["legs"]),
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
            # A profile's top combination can equal DAILY verbatim. Keep the
            # protected ticket and try the next independently eligible option.
            alternatives = profile_alternatives.get(ticket["profile_id"], [])
            replacement = next(
                (legs for legs in alternatives if exposure({"legs": legs}) not in exposures),
                None,
            )
            if replacement:
                ticket = _ticket(
                    ticket["profile_id"], replacement, target.isoformat(), now.isoformat(),
                    settings, astra_review, quality_first=True,
                )
                proposals[ticket["profile_id"]] = ticket
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
    complete = count >= settings["target_ticket_count"] and (
        "daily223" not in settings["enabled_profiles"] or "daily223" in published
    )
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
            "strategy_version": "arthur-portfolio-v3-independent-daily223",
            "daily223_eligibility": "INDEPENDENT_VALIDATED_HISTORY_POOL_BEFORE_SPECIALIST_SCORE_FLOOR",
            "specialist_selection": {
                "mode": "EVIDENCE_FIRST_NO_MINIMUM_ODDS", "minimum_combined_price": None,
                "maximum_combined_price": settings["target_max_odds"], "minimum_legs": 1,
                "maximum_legs": 6, "legacy_target_min_odds_applied": False,
                "ranking": "MEAN_NORMALIZED_EVIDENCE_TIMES_SQRT_DISTINCT_FIXTURE_COUNT",
                "normalization_scale": 100.0,
                "score_is_probability": False,
                "profile_contracts": {key: contract_metadata(key) for key in settings["enabled_profiles"]
                                      if key in SPECIALIST_CONTRACTS},
            },
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
        "daily223_eligible_candidate_count": len(daily_prepared),
        "astra_status": (astra_review or {}).get("status", "NOT_RUN"),
        "real_wager_placed": False,
        "betting_approved": False,
    }
    report["report_sha256"] = canonical_sha256(report)
    return report
