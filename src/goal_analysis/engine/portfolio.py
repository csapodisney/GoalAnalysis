"""Independent specialist tickets over one immutable, enriched price snapshot.

Scores are interpretable evidence heuristics, not estimated win probabilities.
Construction never places a wager and cannot promise a daily available ticket.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from copy import deepcopy
from datetime import date
from decimal import Decimal
from itertools import combinations
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
from goal_analysis.engine.publication_window import allows_new_leg
from goal_analysis.engine.specialist_contracts import SPECIALIST_CONTRACTS, contract_metadata
from goal_analysis.engine.ticket_scoring import ticket_quality
from goal_analysis.engine.veto_overrides import league_base_rate_context, verified_veto_override
from goal_analysis.operating_policy import (
    EXCEPTIONAL_MIN_ODDS,
    MAX_GENERATED_TICKETS,
    MIN_DAILY_TICKETS,
    OPERATING_CONTRACT_VERSION,
    TARGET_MAX_ODDS,
    TARGET_MIN_ODDS,
    cautious_fallback_allowed,
    current_contract,
    generation_capacity,
    generation_limits,
)
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


def _core_universe(enriched, raw):
    supplied = enriched.get("calendar_universe")
    universe = supplied if isinstance(supplied, list) else enriched.get("fixtures")
    source = "calendar_universe" if isinstance(supplied, list) else "NORMALIZED_FIXTURES_WITHOUT_FULL_DAY_PROOF"
    if not isinstance(universe, list):
        universe = list({item.get("fixture_id"): item for item in raw
                         if isinstance(item.get("fixture_id"), str)}.values())
        source = "CANDIDATE_FIXTURES_WITHOUT_FULL_DAY_PROOF"
    events = [item for item in universe if isinstance(item, Mapping)]
    ids = sorted({item["fixture_id"] for item in events
                  if isinstance(item.get("fixture_id"), str) and item["fixture_id"]})
    receipt = enriched.get("calendar_scope_receipt", enriched.get("scope_receipt", {}))
    expected = receipt.get("provider_fixture_ids") if isinstance(receipt, Mapping) else None
    full = (isinstance(supplied, list) and len(ids) == len(universe)
            and isinstance(expected, list) and all(isinstance(item, str) for item in expected)
            and len(set(expected)) == len(expected) and set(expected) == set(ids)
            and receipt.get("scope") == "ACCESSIBLE_PROVIDER_FOOTBALL_DAY"
            and receipt.get("status") == "COMPLETE" and receipt.get("full_product_run") is True
            and receipt.get("provider_calendar_status") == "COMPLETE")
    return events, ids, source, full


def _core_pool(raw, target, now, policy, settings, analyses, products, identifiers, universe, review):
    """Prepare truthful legal products without a shared history or quality veto."""
    prepared, rejected = [], {}
    events = {item["fixture_id"]: item for item in universe
              if isinstance(item.get("fixture_id"), str)}
    reviews = {item.get("candidate_id"): item for item in (review or {}).get("reviews", [])}
    for candidate in raw:
        identifier = candidate.get("candidate_id")
        try:
            if not isinstance(identifier, str) or not identifier:
                raise ValueError("INVALID_CANDIDATE_ID")
            if products[_product_key(candidate)] > 1 or identifiers[identifier] != 1:
                raise ValueError("DUPLICATE_CURRENT_PRODUCT")
            if events and candidate.get("fixture_id") not in events:
                raise ValueError("NOT_IN_RECEIVED_CALENDAR")
            event = events.get(candidate.get("fixture_id"), candidate)
            if not allows_new_leg(event, now) or not allows_new_leg(candidate, now):
                raise ValueError("PREMATCH_LOCKOUT")
            if event.get("kickoff") and _timestamp(event["kickoff"]) != _timestamp(candidate["kickoff"]):
                raise ValueError("CALENDAR_KICKOFF_MISMATCH")
            source = dict(candidate)
            if source.get("evidence") is None:
                source["evidence"] = []
            if not source.get("sensitivity_note"):
                source["sensitivity_note"] = "Érzékenységi háttérelemzés: UNKNOWN."
            item = _prepare(source, target, now, policy, require_history=False)
            missing = sum(WEIGHTS[key] for key in item["missing_support"])
            penalty = missing / 100 * settings["strictness"] * 0.30
            caution = (review or {}).get("status") == "COMPLETE" and reviews.get(identifier, {}).get("assessment") == "caution"
            review_penalty = settings["strictness"] * 0.1 if caution else 0
            item.update(uncertainty_penalty=round(penalty, 6), astra_caution_penalty=round(review_penalty, 6),
                        weighted_support_score=round(max(0, item["support_score"] - penalty - review_penalty), 6),
                        history_analysis=analyses.get(identifier), quality_warnings=[])
            if item["missing_support"]:
                item["quality_warnings"].append(
                    "Nem forrásolt háttér: " + ", ".join(item["missing_support"]) + "; UNKNOWN.")
            if candidate.get("fixture_snapshot_stale"):
                item["requires_refresh"] = True
                item["quality_warnings"].append("A mentett mérkőzésállapot frissítést igényel.")
            for key in (
                "api_football_fixture_id", "api_football_home_team_id", "api_football_away_team_id",
                "source_provider", "provider_fixture_id", "provider_home_team_id", "provider_away_team_id",
                "provider_league_id", "provider_season_id", "config_league_id", "quote_observed_at",
                "quote_source_sha256", "quote_time_basis", "provider_bookmaker",
            ):
                if key in candidate:
                    item[key] = candidate[key]
            prepared.append(item)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            if isinstance(identifier, str):
                rejected[identifier] = str(error)
    return sorted(prepared, key=_quality), rejected


def _core_trace(raw, available, ranked, selected, rejected, universe_info, now, *, searched_ids=None):
    universe, ids, source, full = universe_info
    indexed = [defaultdict(list) for _ in range(4)]
    for rows, target in zip((raw, available, ranked, selected), indexed, strict=True):
        for item in rows:
            if isinstance(item.get("candidate_id"), str):
                target[item.get("fixture_id")].append(item["candidate_id"])
    decisions = []
    for event in universe:
        identifier = event.get("fixture_id")
        if identifier not in ids:
            continue
        reasons = []
        if event.get("status") == "UNMAPPED_COMPETITION":
            reasons.append("UNMAPPED_COMPETITION")
        if not allows_new_leg(event, now):
            reasons.append("PREMATCH_LOCKOUT")
        if not indexed[1][identifier]:
            reasons.append("NO_VALID_QUOTES")
        reasons.extend(rejected[candidate] for candidate in indexed[0][identifier] if candidate in rejected)
        decisions.append({
            "fixture_id": identifier, "provider_league_id": event.get("provider_league_id"),
            "competition_name": event.get("competition_name"), "source_id": event.get("source_id"),
            "status": "SELECTED" if indexed[3][identifier] else "RANKED" if indexed[2][identifier] else "REJECTED",
            "reasons": sorted(set(reasons)), "source_candidate_ids": sorted(indexed[0][identifier]),
            "available_candidate_ids": sorted(indexed[1][identifier]),
            "ranked_candidate_ids": sorted(indexed[2][identifier]),
            "selected_candidate_ids": sorted(indexed[3][identifier]),
        })
    if searched_ids is None:
        cohorts = defaultdict(list)
        for item in ranked:
            cohorts[(item["bookmaker"], item["region"], item["currency"])].append(item)
        searched_ids = {item["candidate_id"] for cohort in cohorts.values() for item in _specialist_pool(cohort)}
    return {
        "input_fixture_ids": ids, "input_fixture_count": len(ids), "input_universe_source": source,
        "all_daily_fixtures_considered": full, "fixture_decisions": decisions,
        "ranked_candidate_ids": [item["candidate_id"] for item in ranked],
        "ranked_fixture_ids": sorted({item["fixture_id"] for item in ranked}),
        "selected_source_candidate_ids": [item["candidate_id"] for item in selected],
        "candidate_rejections": deepcopy(rejected),
        "searched_candidate_ids": sorted(searched_ids),
        "deferred_candidate_ids": sorted({item["candidate_id"] for item in ranked} - searched_ids),
        "search_method": "BOUNDED_DIVERSIFIED_BEAM_NOT_EXHAUSTIVE_OPTIMUM",
    }


def _effective_core_contract(profile, settings, *, fallback=False):
    result = contract_metadata(profile)
    if not current_contract(settings) or profile == "daily223":
        return result
    minimum = EXCEPTIONAL_MIN_ODDS if fallback else TARGET_MIN_ODDS
    maximum = TARGET_MIN_ODDS if fallback else TARGET_MAX_ODDS
    result.update(minimum_combined_price=minimum, maximum_combined_price=maximum,
                  minimum_legs=SPECIALIST_CONTRACTS.get(profile, {}).get("min_legs", 2),
                  maximum_legs=6, operating_contract_version=OPERATING_CONTRACT_VERSION)
    result.setdefault("selection_mode", "PROFILE_ACCUMULATOR")
    if "construction_contract" in result:
        result["construction_contract"].update(min_price=minimum, max_price=maximum,
                                                operating_contract_version=OPERATING_CONTRACT_VERSION)
    return result


def _primary_absence_proven(matching):
    """A bounded search miss cannot authorize the cautious exception by itself."""
    groups = defaultdict(list)
    for item in matching:
        if item["decimal_price"] <= TARGET_MAX_ODDS:
            groups[(item["bookmaker"], item["region"], item["currency"])].append(item)
    for cohort in groups.values():
        prices = sorted(Decimal(str(item["decimal_price"])) for item in cohort)
        if len(prices) < 2:
            continue
        if prices[0] * prices[1] > Decimal(str(TARGET_MAX_ODDS)):
            continue
        upper = Decimal(1)
        for price in prices[-6:]:
            upper *= price
        if upper < Decimal(str(TARGET_MIN_ODDS)):
            continue
        if len(cohort) > 6:
            return False
        if any(TARGET_MIN_ODDS <= _product(legs) <= TARGET_MAX_ODDS
               for length in range(2, len(cohort) + 1) for legs in combinations(cohort, length)
               if len({leg["fixture_id"] for leg in legs}) == len(legs)):
            return False
    return True


def _settings(settings: dict) -> dict:
    current = current_contract(settings)
    result = {
        "strictness": 35,
        "target_ticket_count": MIN_DAILY_TICKETS if current else 2,
        "max_ticket_count": MAX_GENERATED_TICKETS if current else 5,
        "stake_eur": 1.0,
        "target_min_odds": TARGET_MIN_ODDS if current else 10.0,
        "target_max_odds": TARGET_MAX_ODDS if current else 50.0,
        "enabled_profiles": [p for p in PROFILES if p != "available"] if current else list(DEFAULT_PROFILES),
    }
    result.update({key: settings[key] for key in result if key in settings})
    if current:
        result["operating_contract_version"] = OPERATING_CONTRACT_VERSION
    _number(result["strictness"], "strictness", 0, 100)
    _number(result["stake_eur"], "stake_eur", 0.01, 100000)
    _number(result["target_min_odds"], "target_min_odds", 1.01, 1000)
    _number(result["target_max_odds"], "target_max_odds", result["target_min_odds"], 1000)
    if current and not TARGET_MIN_ODDS <= result["target_min_odds"] <= result["target_max_odds"] <= TARGET_MAX_ODDS:
        raise ValueError("Current primary odds range must stay within 15–40")
    generation_limits(result)
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
    current = current_contract(settings)
    exceptional = current and quality_first and profile == "orszem" and _product(legs) < TARGET_MIN_ODDS
    metadata = _effective_core_contract(profile, settings, fallback=exceptional)
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
    if current and profile != "daily223":
        reasons.append(
            f"{PROFILES[profile]['name']}: saját {PROFILES[profile]['market'][0]}/"
            f"{PROFILES[profile]['market'][1] or 'home/away'}/{PROFILES[profile]['market'][2]} "
            "piac, külön mérkőzések és a ténylegesen jegyzett árak; "
            "a meglévő bizonyítottsági pontszám rangsorol, a hiányzó háttér UNKNOWN.")
    warnings = list(
        dict.fromkeys(message for leg in legs for message in leg.get("quality_warnings", []))
    )
    fallback = exceptional or profile != "daily223" and (
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
    if exceptional:
        warnings.append("CAUTIOUS_ODDS_EXCEPTION_AFTER_FULL_DAY_SEARCH")
        reasons.append("Kivételes óvatos 5–15-ös sáv: a teljes napi bemeneten nem volt saját 15–40-es opció.")
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
        identity.update(metadata)
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
        **metadata,
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
    current = current_contract(settings)
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
    identifiers = Counter(item.get("candidate_id") if isinstance(item.get("candidate_id"), str) else None
                          for item in raw) if current else Counter(item.get("candidate_id") for item in raw)
    prepared, daily_prepared, diagnostics = [], [], list(enriched_input.get("data_issues", []))
    loose = permissive(settings)
    minimum_quality = 0 if loose else 3 + settings["strictness"] * 0.12
    reviews = {item.get("candidate_id"): item for item in (astra_review or {}).get("reviews", [])}
    universe_info = _core_universe(enriched_input, raw) if current else None
    core_prepared, core_rejected = _core_pool(
        raw, target, now, policy, settings, analyses, products, identifiers, universe_info[0], astra_review
    ) if current else ([], {})
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
            if current:
                continue  # The six specialists use their independent legal-price pool below.
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
    if current:
        prepared = core_prepared
    prepared.sort(key=_quality)
    preferred = enriched_input.get("preferred_bookmakers", ["betano"])
    profiles, proposals, profile_alternatives = [], {}, {}
    profile_inputs, profile_rejections, odds_exceptions = {}, {}, {}
    # DAILY_223 receives its own original eligible pool, before the unrelated
    # specialist score floor. Its hard identity/history/price guards remain.
    daily_options, daily_search_ids = [], set()
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
        daily_search_ids.update(item["candidate_id"] for item in search)
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
        if current:
            rejected = {**core_rejected, **{
                item["candidate_id"]: "PROFILE_MARKET_MISMATCH" for item in prepared
                if not _matches_profile(item, identifier)
            }}
            rejected.update({item["candidate_id"]: "LEG_PRICE_ABOVE_PROFILE_MAXIMUM" for item in matching
                             if item["decimal_price"] > TARGET_MAX_ODDS})
            matching = [item for item in matching if item["decimal_price"] <= TARGET_MAX_ODDS]
            if identifier == "h1_over05":
                if not loose:
                    rejected.update({item["candidate_id"]: "FIRST_HALF_HISTORY_REQUIRED" for item in matching
                                     if not _half_history_verified(item["history_analysis"])})
                    matching = [item for item in matching if _half_history_verified(item["history_analysis"])]
                else:
                    matching = [deepcopy(item) for item in matching]
                    for item in matching:
                        if not _half_history_verified(item["history_analysis"]):
                            item["quality_warnings"].append("Félidős történeti minta: UNKNOWN; nem teljes idejű helyettesítő adat.")
            profile_inputs[identifier], profile_rejections[identifier] = matching, rejected
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
                scoped, TARGET_MAX_ODDS if current else min(settings["target_max_odds"], contract["max_price"]) if contract
                else settings["target_max_odds"],
                **({"minimum": TARGET_MIN_ODDS, "min_legs": contract["min_legs"] if contract else 2,
                    "max_legs": contract["max_legs"] if contract else 6} if current else
                   {"minimum": contract["min_price"], "min_legs": contract["min_legs"],
                    "max_legs": contract["max_legs"]} if contract else {}),
            ))
        if current:
            full_day = universe_info[3]
            exception = {"status": "PRIMARY_RANGE" if options else "NOT_AUTHORIZED",
                         "primary_options_count": len(options), "full_day_coverage": full_day,
                         "applied": False}
            if cautious_fallback_allowed(identifier, settings, options,
                                         all_daily_fixtures_considered=full_day):
                proven = _primary_absence_proven(matching)
                exception["primary_absence_proven"] = proven
                if proven:
                    options = [legs for group in groups for legs in _specialist_options(
                        [item for item in matching if (item["bookmaker"], item["region"], item["currency"]) == group],
                        TARGET_MIN_ODDS, minimum=EXCEPTIONAL_MIN_ODDS, min_legs=2,
                    ) if _product(legs) < TARGET_MIN_ODDS]
                    exception.update(status="APPLIED" if options else "NO_FALLBACK_OPTION",
                                     applied=bool(options), reason="NO_OWN_15_40_OPTION_AFTER_FULL_DAY_SEARCH")
                else:
                    exception["reason"] = "PRIMARY_SEARCH_COMPLETENESS_NOT_PROVEN"
            odds_exceptions[identifier] = exception
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
                **_effective_core_contract(identifier, settings,
                                           fallback=odds_exceptions.get(identifier, {}).get("applied", False)),
            }
        )
    if not current and loose and not proposals and prepared and allowed_profiles is None:
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
    def annotate(ticket):
        identifier = ticket["profile_id"]
        ranked = daily_prepared if identifier == "daily223" else profile_inputs[identifier]
        rejected = ({item["candidate_id"]: item.get("status", item.get("code", "UNKNOWN_REJECTION")) for item in diagnostics
                     if isinstance(item, Mapping) and isinstance(item.get("candidate_id"), str)
                     and item["candidate_id"] not in {row["candidate_id"] for row in daily_prepared}}
                    if identifier == "daily223" else profile_rejections[identifier])
        trace = _core_trace(raw, daily_prepared if identifier == "daily223" else prepared,
                            ranked, ticket.get("legs", []), rejected, universe_info, now,
                            searched_ids=daily_search_ids if identifier == "daily223" else None)
        if identifier == "daily223":
            trace["search_method"] = "PROTECTED_DAILY223_ORIGINAL_ROLE_SOLVER"
        else:
            trace["odds_exception"] = deepcopy(odds_exceptions[identifier])
        ticket.setdefault("selection_diagnostics", {}).update(trace)
        return trace

    if current:
        for profile in profiles:
            identifier = profile["profile_id"]
            source_card = proposals.get(identifier, {"profile_id": identifier, "legs": []})
            profile["selection_diagnostics"] = deepcopy(annotate(source_card))
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
                if current:
                    annotate(ticket)
                else:
                    proposals[ticket["profile_id"]] = ticket
        if exposure(ticket) in exposures:
            duplicate_profiles.add(ticket["profile_id"])
            diagnostics.append(
                {"profile_id": ticket["profile_id"], "status": "DUPLICATE_TICKET_EXPOSURE"}
            )
            continue
        if len(tickets) >= generation_capacity(settings):
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
                "mode": "PROFILE_ACCUMULATOR" if current else "EVIDENCE_FIRST_NO_MINIMUM_ODDS",
                "minimum_combined_price": TARGET_MIN_ODDS if current else None,
                "maximum_combined_price": TARGET_MAX_ODDS if current else settings["target_max_odds"], "minimum_legs": 2 if current else 1,
                "maximum_legs": 6, "legacy_target_min_odds_applied": False,
                "ranking": "MEAN_NORMALIZED_EVIDENCE_TIMES_SQRT_DISTINCT_FIXTURE_COUNT",
                "normalization_scale": 100.0,
                "score_is_probability": False,
                "profile_contracts": {key: _effective_core_contract(key, settings) for key in settings["enabled_profiles"]
                                      if key in SPECIALIST_CONTRACTS},
            },
            "data_veto_enabled": not loose if not current else False,
            "fallback_enabled": loose if not current else any(item["applied"] for item in odds_exceptions.values()),
            **({"specialist_eligibility": "PROFILE_LOCAL;DAILY223_UNCHANGED",
                "fallback_policy": "CAUTIOUS_PROVEN_FULL_DAY_ONLY"} if current else {}),
            "score_kind": "EVIDENCE_HEURISTIC_NOT_PROBABILITY",
            "minimum_weighted_support": round(minimum_quality, 6) if not current else None,
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
