"""Independent, uncalibrated model estimates for paper testing only.

Selection depends on model outputs and matched provider prices. Legacy support
scores and reviewer opinions never enter the ranking or change probabilities.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.engine.publication_window import allows_new_leg
from goal_analysis.engine.ticket_options import bounded_ticket_groups
from goal_analysis.engine.ticket_scoring import normalize_score, ticket_quality
from goal_analysis.operating_policy import TARGET_MAX_ODDS, TARGET_MIN_ODDS, current_contract
from goal_analysis.quote_metadata import quote_metadata

VERSION = "arthur-independent-model-recommendations-v3-normalized-tickets"
DIVERSITY_OPTIONS_VERSION = "arthur-model-diversity-options-v2-full-tickets"
PROFILES = {"gauss": "GAUSS – Góltérkép", "kepler": "KEPLER – Erőviszony és formafordulat",
            "laplace": "LAPLACE – Poisson-alapvonal"}
MARKETS = {
    "home": ("h2h", "home"),
    "draw": ("h2h", "draw"),
    "away": ("h2h", "away"),
    "over25": ("totals_2_5", "over"),
    "under25": ("totals_2_5", "under"),
    "btts_yes": ("btts", "yes"),
}
ERROR_STATUSES = {"INSUFFICIENT_HISTORY", "DEPENDENCY_MISSING", "MODEL_ERROR", "NO_FIXTURES"}
SETTLEMENT = "REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES"


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _time(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _name(value):
    return " ".join(value.casefold().split()) if isinstance(value, str) else ""


def _quote_matches(quote, fixture, market, selection):
    """Only use exact normalized fixture products with an identified feed source."""
    if not isinstance(quote, dict):
        return False
    if (
        quote.get("fixture_id") != fixture["fixture_id"]
        or quote.get("market_key") != market
        or quote.get("selection_key") != selection
        or quote.get("period") != "FULL_TIME"
        or _time(quote.get("kickoff")) != _time(fixture["kickoff"])
        or any(_name(quote.get(k)) != _name(fixture[k]) for k in ("home_team", "away_team"))
        or ("quote_available" in quote and quote["quote_available"] is not True)
        or quote.get("event_status", "SCHEDULED") != "SCHEDULED"
        or quote.get("settlement", SETTLEMENT) != SETTLEMENT
        or not _number(quote.get("decimal_price"))
        or not 1 < quote["decimal_price"] <= 1000
        or not _name(quote.get("bookmaker"))
    ):
        return False
    for field in (
        "api_football_fixture_id",
        "api_football_home_team_id",
        "api_football_away_team_id",
        "source_provider", "provider_fixture_id", "provider_home_team_id", "provider_away_team_id",
        "provider_league_id", "provider_season_id",
    ):
        if field in quote and field in fixture and str(quote[field]) != str(fixture[field]):
            return False
    try:
        url = urlsplit(quote.get("quote_source_id", ""))
        if url.scheme != "https" or url.username or url.password:
            return False
        provider = quote.get("quote_provider")
        if provider == 'the_stats_api':
            from goal_analysis.providers.the_stats_odds import valid_quote_source

            return valid_quote_source(quote, fixture)
        if provider == "api_football":
            query = parse_qs(url.query)
            identifier = str(fixture.get("api_football_fixture_id", ""))
            return (
                url.hostname == "v3.football.api-sports.io"
                and url.path == "/odds"
                and bool(identifier)
                and query.get("fixture") == [identifier]
                and len(query.get("bookmaker", [])) == 1
                and len(query.get("bet", [])) == 1
                and query["bookmaker"][0].isdigit()
                and query["bet"][0].isdigit()
                and quote["bookmaker"].endswith("_api_football_" + query["bookmaker"][0])
                and (
                    "provider_market_id" not in quote
                    or query["bet"] == [str(quote["provider_market_id"])]
                )
            )
        if provider == "the_odds_api":
            parts = url.path.strip("/").split("/")
            return (
                url.hostname == "api.the-odds-api.com"
                and len(parts) == 6
                and parts[:2] == ["v4", "sports"]
                and parts[3] == "events"
                and parts[5] == "odds"
                and bool(parts[4])
                and (not fixture.get("sport_key") or parts[2] == fixture["sport_key"])
            )
    except (TypeError, ValueError):
        return False
    return False


def _probabilities(prediction, profile):
    raw = prediction.get("probabilities", {})
    if not isinstance(raw, dict):
        return {}
    values = {k: v for k, v in raw.items() if k in MARKETS and _number(v) and 0 <= v <= 1}
    for keys in (("home", "draw", "away"), ("over25", "under25")):
        if not all(k in values for k in keys) or abs(sum(values[k] for k in keys) - 1) > 1e-5:
            for k in keys:
                values.pop(k, None)
    return {k: v for k, v in values.items() if profile != "kepler" or k in ("home", "draw", "away")}


def _normalized_support(leg):
    """Dimensionless ranking support; never a joint win probability.

    Individual model probability stays unchanged. Positive estimated edge is
    mapped monotonically to [0, 1) before its equal-weight combination with
    probability. Missing-price cohorts use probability alone.
    """
    probability = normalize_score(leg["model_probability"])
    edge = leg["estimated_edge"]
    return probability if edge is None else (probability + max(0.0, edge) / (1 + max(0.0, edge))) / 2


def _scoring(legs):
    return ticket_quality([_normalized_support(leg) for leg in legs],
                          raw_kind="NORMALIZED_MODEL_SUPPORT_HEURISTIC")


def _selection_rank(legs, preferred=()):
    """Mean normalized support times sqrt(distinct fixtures), then quality ties."""
    quality = [_normalized_support(leg) for leg in legs]
    book = legs[0].get("bookmaker") or ""
    book_rank = next((i for i, name in enumerate(preferred)
                      if book == name or book.startswith(name + "_")), len(preferred))
    return (
        -_scoring(legs)["ticket_score"], -min(quality), -sum(quality) / len(quality),
        -min(leg["model_probability"] for leg in legs),
        max(leg["model_stability"] or 0 for leg in legs), book_rank,
        tuple(sorted((leg["kickoff"], leg["fixture_id"], leg["market_key"], leg["selection_key"])
                     for leg in legs)),
        -math.prod(leg["decimal_price"] or 1 for leg in legs),
        tuple(sorted(leg["candidate_id"] for leg in legs)),
    )


def _options(priced, unpriced, preferred=(), max_price=50, *, min_price=None):
    # Priced and unpriced selection quality never share a cohort. Three is a
    # computational construction ceiling, not a required number of legs.
    return bounded_ticket_groups(
        priced or unpriced, rank=lambda legs: _selection_rank(legs, preferred),
        fixture=lambda leg: leg["fixture_id"],
        cohort=lambda leg: (leg.get("bookmaker"), leg.get("region"), leg.get("currency")),
        price=lambda leg: leg["decimal_price"],
        identity=lambda leg: (leg["fixture_id"], leg["market_key"], leg["selection_key"], leg["period"]),
        max_legs=3, max_price=max_price, min_price=min_price,
    )


def _select(priced, unpriced, preferred=()):
    options, _ = _options(priced, unpriced, preferred)
    return list(options[0]) if options else []


def _leg(profile, fixture, prediction, key, probability, stability, now, quote=None):
    market, selection = MARKETS[key]
    leg = deepcopy(fixture)
    if quote:
        # Preserve provenance, but never accept probability/evidence fields from quotes.
        for name in (
            "decimal_price",
            "bookmaker",
            "bookmaker_name",
            "region",
            "currency",
            "quote_provider",
            "quote_source_id",
            "provider_market_id",
            "quote_available",
            'quote_observed_at', 'quote_source_sha256', 'quote_time_basis', 'provider_bookmaker',
        ):
            if name in quote:
                leg[name] = deepcopy(quote[name])
        leg.update(quote_metadata(quote.get("quote_timestamp_raw", quote.get("quoted_at")), now))
    identity = [profile, fixture["fixture_id"], market, selection, (quote or {}).get("bookmaker")]
    leg.update(
        candidate_id=f"{profile}-" + canonical_sha256(identity)[:24],
        event_status="SCHEDULED",
        market_key=market,
        selection_key=selection,
        period="FULL_TIME",
        settlement=SETTLEMENT,
        decimal_price=quote["decimal_price"] if quote else None,
        bookmaker=quote["bookmaker"] if quote else None,
        quote_available=bool(quote),
        model_probability=probability,
        model_details=deepcopy(prediction.get("model_details", {})),
        model_stability=stability,
        estimated_edge=probability * quote["decimal_price"] - 1 if quote else None,
        confidence="LOW",
        confidence_kind="UNCALIBRATED_MODEL_ESTIMATE",
        evidence=[],
        missing_support=[],
        sensitivity_note="Kísérleti, még nem kalibrált modellbecslés; papírteszthez.",
    )
    if quote:
        leg["source_candidate_id"] = quote.get("candidate_id")
        leg["quote_candidate_id"] = quote.get("candidate_id")
    return leg


def _model_input_trace(settings, fixtures, quotes, predictions, profile, status, upcoming,
                       modeled, priced, selected, now):
    supplied = settings.get("calendar_universe")
    universe = supplied if isinstance(supplied, list) else fixtures
    events = [event for event in universe if isinstance(event, dict)]
    ids = sorted({event["fixture_id"] for event in events
                  if isinstance(event.get("fixture_id"), str) and event["fixture_id"]})
    receipt = settings.get("calendar_scope_receipt", {})
    expected = receipt.get("provider_fixture_ids") if isinstance(receipt, dict) else None
    full = (isinstance(supplied, list) and len(ids) == len(universe)
            and isinstance(expected, list) and all(isinstance(identifier, str) for identifier in expected)
            and len(set(expected)) == len(expected) and set(expected) == set(ids)
            and receipt.get("scope") == "ACCESSIBLE_PROVIDER_FOOTBALL_DAY"
            and receipt.get("status") == "COMPLETE" and receipt.get("full_product_run") is True
            and receipt.get("provider_calendar_status") == "COMPLETE")
    fixture_ids = {fixture.get("fixture_id") for fixture in fixtures}
    by_quote, by_prediction, ranked, chosen = (defaultdict(list) for _ in range(4))
    for rows, grouped in ((quotes, by_quote), (predictions, by_prediction), (priced, ranked), (selected, chosen)):
        for item in rows:
            if isinstance(item, dict):
                grouped[item.get("fixture_id")].append(item)
    rejections, decisions = {}, []
    for event in events:
        identifier = event.get("fixture_id")
        if identifier not in ids:
            continue
        reasons = []
        if event.get("status") == "UNMAPPED_COMPETITION":
            reasons.append("UNMAPPED_COMPETITION")
        if identifier not in fixture_ids:
            reasons.append("NO_VALID_NORMALIZED_FIXTURE")
        elif identifier not in upcoming:
            reasons.append("PREMATCH_LOCKOUT" if not allows_new_leg(event, now) else "INVALID_NORMALIZED_FIXTURE")
        own_predictions = by_prediction[identifier]
        if status in ERROR_STATUSES:
            reasons.append("MODEL_UNAVAILABLE:" + status)
        if not own_predictions:
            reasons.append("MODEL_PREDICTION_UNKNOWN")
        elif len(own_predictions) != 1:
            reasons.append("DUPLICATE_MODEL_PREDICTION")
        probability_map = _probabilities(own_predictions[0], profile) if len(own_predictions) == 1 else {}
        variations = own_predictions[0].get("stability") if len(own_predictions) == 1 else None
        if own_predictions and not probability_map:
            reasons.append("MODEL_PROBABILITIES_UNKNOWN_OR_INVALID")
        available = []
        for quote in by_quote[identifier]:
            source_id, reason = quote.get("candidate_id"), None
            key = next((key for key, product in MARKETS.items()
                        if product == (quote.get("market_key"), quote.get("selection_key"))), None)
            fixture = upcoming.get(identifier)
            if (fixture is None or key is None
                    or not _quote_matches(quote, fixture, *MARKETS[key])):
                reason = "INVALID_OR_UNMATCHED_QUOTE"
            else:
                if isinstance(source_id, str):
                    available.append(source_id)
                probability = probability_map.get(key)
                stability = variations.get(key) if isinstance(variations, dict) else None
                if profile == "kepler" and key not in {"home", "draw", "away"}:
                    reason = "MARKET_NOT_ESTIMATED_BY_PROFILE"
                elif probability is None:
                    reason = "MODEL_PROBABILITY_UNKNOWN"
                elif probability < .40:
                    reason = "MODEL_PROBABILITY_BELOW_FLOOR"
                elif profile == "gauss" and (not _number(stability) or not 0 <= stability <= .08):
                    reason = "GAUSS_STABILITY_REQUIRED"
                elif probability * quote["decimal_price"] - 1 < .03 - 1e-12:
                    reason = "ESTIMATED_EDGE_BELOW_FLOOR"
                elif quote["decimal_price"] > TARGET_MAX_ODDS:
                    reason = "LEG_PRICE_ABOVE_PROFILE_MAXIMUM"
            if reason:
                reasons.append(reason)
                if isinstance(source_id, str):
                    rejections[source_id] = reason
        if not available:
            reasons.append("NO_VALID_QUOTES")
        decisions.append({
            "fixture_id": identifier, "provider_league_id": event.get("provider_league_id"),
            "competition_name": event.get("competition_name"), "source_id": event.get("source_id"),
            "status": "SELECTED" if chosen[identifier] else "RANKED" if ranked[identifier] else "REJECTED",
            "model_prediction_available": identifier in modeled,
            "reasons": sorted(set(reasons)), "available_candidate_ids": sorted(available),
            "ranked_candidate_ids": [leg["candidate_id"] for leg in ranked[identifier]],
            "selected_candidate_ids": [leg["candidate_id"] for leg in chosen[identifier]],
        })
    return {
        "input_fixture_ids": ids, "input_fixture_count": len(ids),
        "input_universe_source": "calendar_universe" if isinstance(supplied, list) else "NORMALIZED_FIXTURES_WITHOUT_FULL_DAY_PROOF",
        "all_daily_fixtures_considered": full, "fixture_decisions": decisions,
        "model_input_fixture_ids": sorted(identifier for identifier in fixture_ids if isinstance(identifier, str)),
        "ranked_candidate_ids": [leg["candidate_id"] for leg in priced],
        "ranked_fixture_ids": sorted({leg["fixture_id"] for leg in priced}),
        "selected_source_candidate_ids": [leg["source_candidate_id"] for leg in selected
                                          if isinstance(leg.get("source_candidate_id"), str)],
        "candidate_rejections": rejections,
    }


def build_model_recommendations(
    model_results: dict,
    fixtures: list,
    priced_candidates: list,
    settings: dict,
    now: datetime,
    review: dict | None = None,
    *,
    diversity_options: dict | None = None,
) -> list:
    """Build one normalized-support paper-test ticket per enabled model.

    Known prices with insufficient edge cannot reappear as missing-price picks.
    Quote age is informational. Reviews are attached only after immutable ranking.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    current = current_contract(settings)
    target = settings.get("date") or settings.get("target_date")
    target = str(
        target or next((f.get("target_date") for f in fixtures if f.get("target_date")), "")
    )
    target = target or now.astimezone(ZoneInfo("Europe/Berlin")).date().isoformat()
    counts = Counter(f.get("fixture_id") for f in fixtures)
    calendar = settings.get("calendar_universe") if current else None
    calendar_events = {event.get("fixture_id"): event for event in calendar
                       if isinstance(event, dict)} if isinstance(calendar, list) else None
    calendar_counts = Counter(event.get("fixture_id") for event in calendar
                              if isinstance(event, dict)) if isinstance(calendar, list) else None
    upcoming = {}
    for fixture in fixtures:
        stamp = _time(fixture.get("kickoff"))
        identifier = fixture.get("fixture_id")
        if (
            isinstance(identifier, str)
            and identifier
            and counts[identifier] == 1
            and stamp is not None
            and stamp > now
            and (not current or allows_new_leg(fixture, now))
            and (calendar_events is None or identifier in calendar_events
                 and calendar_counts[identifier] == 1
                 and _time(calendar_events[identifier].get("kickoff")) == stamp)
            and stamp.astimezone(ZoneInfo("Europe/Berlin")).date().isoformat() == target
            and fixture.get("provider_status") == "NS"
            and _name(fixture.get("home_team"))
            and _name(fixture.get("away_team"))
            and _name(fixture["home_team"]) != _name(fixture["away_team"])
        ):
            upcoming[identifier] = fixture
    output = []
    for profile in dict.fromkeys(settings.get("enabled_profiles", [])):
        if profile not in PROFILES:
            continue
        result = model_results.get(profile) or {}
        status = result.get("status", "INSUFFICIENT_HISTORY")
        predictions = result.get("predictions", [])
        if not isinstance(predictions, list) or result.get("profile_id", profile) != profile:
            predictions, status = [], "MODEL_ERROR"
        prediction_counts = Counter(p.get("fixture_id") for p in predictions if isinstance(p, dict))
        priced, unpriced, modeled, valid_quotes = [], [], set(), 0
        diagnostics = deepcopy(result.get("diagnostics", []))
        if status not in ERROR_STATUSES and status not in {"READY", "MODEL_READY", "OK"}:
            status = "MODEL_ERROR"
        if status not in ERROR_STATUSES:
            for prediction in predictions:
                if not isinstance(prediction, dict):
                    continue
                identifier = prediction.get("fixture_id")
                if identifier not in upcoming or prediction_counts[identifier] != 1:
                    continue
                fixture = upcoming[identifier]
                probabilities = _probabilities(prediction, profile)
                if probabilities:
                    modeled.add(identifier)
                for key, probability in probabilities.items():
                    variations = prediction.get("stability", {})
                    stability = (
                        variations.get(key)
                        if profile == "gauss" and isinstance(variations, dict)
                        else None
                    )
                    if profile == "gauss" and (
                        not _number(stability) or not 0 <= stability <= 0.08
                    ):
                        continue
                    market, selection = MARKETS[key]
                    quotes = [
                        q
                        for q in priced_candidates
                        if _quote_matches(q, fixture, market, selection)
                    ]
                    valid_quotes += len(quotes)
                    if quotes:
                        for quote in quotes:
                            edge = probability * quote["decimal_price"] - 1
                            if probability >= 0.40 and edge >= 0.03 - 1e-12:
                                priced.append(
                                    _leg(
                                        profile,
                                        fixture,
                                        prediction,
                                        key,
                                        probability,
                                        stability,
                                        now,
                                        quote,
                                    )
                                )
                    elif probability >= 0.50:
                        unpriced.append(
                            _leg(profile, fixture, prediction, key, probability, stability, now)
                        )
        preferred = [name for name in settings.get("preferred_bookmakers", [])
                     if isinstance(name, str)]
        search_priced = [leg for leg in priced if leg["decimal_price"] <= TARGET_MAX_ODDS] if current else priced
        options, search = _options(search_priced, [] if current else unpriced, preferred,
                                   TARGET_MAX_ODDS if current else settings.get("target_max_odds", 50),
                                   min_price=TARGET_MIN_ODDS if current else None)
        legs = list(options[0]) if options else []
        if not upcoming:
            card_status = "NO_FIXTURES"
        elif status in ERROR_STATUSES:
            card_status = status
        elif legs:
            card_status = "DRAFT" if current else "ODDS_PENDING" if legs[0]["decimal_price"] is None else "MODEL_READY"
        else:
            card_status = "NO_STABLE_SELECTION"
        # Reviewer input cannot add prices, probabilities, legs, or ranking features.
        for leg in legs:
            leg["fixture_context"] = deepcopy(
                [
                    item
                    for item in (review or {}).get("fixture_context", [])
                    if isinstance(item, dict) and item.get("fixture_id") == leg["fixture_id"]
                ]
            )
        warnings = ["Kísérleti modell: a valószínűségek még nem kalibráltak; csak papírteszt."]
        if card_status == "ODDS_PENDING":
            warnings.append(
                "Ehhez a választáshoz nincs ellenőrzött szolgáltatói ár; hozam nem számítható."
            )
        elif card_status == "NO_STABLE_SELECTION":
            warnings.append(
                "Nincs a valószínűségi, becsültelőny- és stabilitási küszöböknek megfelelő választás."
            )
        elif not legs:
            warnings.append(
                result.get("message")
                or "Nem állítható össze megfelelően alátámasztott modellajánlat."
            )
        item = {
            "profile_id": profile,
            "profile_name": PROFILES[profile],
            "date": target,
            "created_at": now.isoformat(),
            "strategy_version": VERSION,
            "method_kind": "LOCAL_STATISTICAL_MODEL",
            "experimental": True,
            "selection_mode": "EVIDENCE_FIRST", "target_odds_minimum_enforced": False,
            "model_status": status,
            "model_diagnostics": diagnostics,
            "model_summary": {
                "fixtures_considered": len(upcoming),
                "predictions_received": len(predictions),
                "modeled_fixtures": len(modeled),
                "eligible_selections": len(priced) + len(unpriced),
                "selected_legs": len(legs),
                "valid_quotes": valid_quotes,
                "priced_selections": len(priced),
                "odds_pending_selections": len(unpriced),
                "selection_method": search["search_method"],
                "compared_selections": len(priced or unpriced),
                **search,
            },
            "selection_diagnostics": {
                "selection_mode": "EVIDENCE_FIRST", "target_odds_minimum_enforced": False,
                "selection_basis": "ESTIMATED_EDGE" if priced else "MODEL_PROBABILITY" if unpriced else None,
                "bounded_search": search["bounded_search"],
                "scoring": _scoring(legs) if legs else None,
                "max_legs": 3,
                "selected_min_estimated_edge": min((leg["estimated_edge"] for leg in legs
                                                     if leg["estimated_edge"] is not None), default=None),
                "selected_min_model_probability": min((leg["model_probability"] for leg in legs), default=None),
            },
            "status": card_status,
            "confidence": "LOW",
            "confidence_kind": "UNCALIBRATED_MODEL_ESTIMATE",
            "probability": None,
            "odds_pending": card_status == "ODDS_PENDING",
            "combined_price": math.prod(l["decimal_price"] for l in legs)
            if legs and card_status in {"MODEL_READY", "DRAFT"}
            else None,
            "combined_price_kind": "THEORETICAL_PRODUCT_NOT_BOOKMAKER_COMBO_QUOTE",
            "bookmaker": legs[0]["bookmaker"] if legs else None,
            "stake_eur": None,
            "portfolio_ticket_id": None,
            "selected_for_portfolio": False,
            "playable": False,
            "legs": legs,
            "quality_warnings": warnings,
            "reasoning": [
                "Önálló, kísérleti modellbecslés; egy- és többmeccses ajánlatok összevetésével.",
                "A rangsor normalizált egyedi modellalátámasztás átlaga × a különböző mérkőzések számának négyzetgyöke; ez heurisztika, nem közös nyerési valószínűség.",
                "Nincs kötelező összszorzó vagy darabszám; a meglévő valószínűségi, előny- és stabilitási küszöbök megmaradnak.",
            ],
            "risk_notes": [
                "A becsült előny nem igazolt nyereségesség; a modell kalibrálása és visszatesztje még hiányzik.",
                "A választások függhetnek egymástól; közös nyerési valószínűséget nem számolunk.",
            ],
        }
        if legs:
            item["recommendation_id"] = canonical_sha256(
                [
                    VERSION,
                    target,
                    profile,
                    now.isoformat(),
                    [(l["candidate_id"], l["model_probability"], l["decimal_price"]) for l in legs],
                ]
            )[:24]
        if current:
            item.update(operating_contract_version=2, selection_mode="PROFILE_ACCUMULATOR",
                        target_odds_minimum_enforced=True, minimum_combined_price=TARGET_MIN_ODDS,
                        maximum_combined_price=TARGET_MAX_ODDS, minimum_legs=1, maximum_legs=3)
            item["selection_diagnostics"].update(
                _model_input_trace(settings, fixtures, priced_candidates, predictions, profile, status,
                                   upcoming, modeled, search_priced, legs, now),
                selection_mode="PROFILE_ACCUMULATOR", target_odds_minimum_enforced=True,
                primary_odds_range={"minimum": TARGET_MIN_ODDS, "maximum": TARGET_MAX_ODDS},
            )
            item["model_summary"]["compared_selections"] = len(search_priced)
            item["reasoning"][-1] = "Saját 15–40-es összszorzó, 1–3 külön mérkőzés; az eredeti modellvalószínűség-, előny- és stabilitási feltételek változatlanok."
            if legs:
                item["quality_warnings"].append("Opcionális pontos profil-review nélkül DRAFT; a modell becslése ettől nem változik.")
            else:
                item["risk_notes"].append("Nincs saját, valódi árú 15–40-es opció; hiányzó modell vagy ár UNKNOWN, nem becsült pótlás.")
        if diversity_options is not None:
            # Complete alternatives keep the original probability/source gates.
            # The coordinator applies ticket similarity and exposure controls.
            templates = []
            for group in options:
                option = deepcopy(item)
                selected = deepcopy(list(group))
                for leg in selected:
                    leg["fixture_context"] = deepcopy([
                        context for context in (review or {}).get("fixture_context", [])
                        if isinstance(context, dict) and context.get("fixture_id") == leg["fixture_id"]
                    ])
                pending = selected[0]["decimal_price"] is None
                option.update(
                    legs=selected, status="DRAFT" if current else "ODDS_PENDING" if pending else "MODEL_READY",
                    odds_pending=pending,
                    combined_price=None if pending else math.prod(leg["decimal_price"] for leg in selected),
                    bookmaker=selected[0]["bookmaker"],
                )
                scoring = _scoring(selected)
                option["model_summary"]["selected_legs"] = len(selected)
                option["selection_diagnostics"].update(
                    selected_min_estimated_edge=min((leg["estimated_edge"] for leg in selected
                                                     if leg["estimated_edge"] is not None), default=None),
                    selected_min_model_probability=min(leg["model_probability"] for leg in selected),
                    diversity_options_version=DIVERSITY_OPTIONS_VERSION,
                    scoring=scoring,
                )
                if current:
                    trace = option["selection_diagnostics"]
                    trace["selected_source_candidate_ids"] = [leg["source_candidate_id"] for leg in selected
                                                               if isinstance(leg.get("source_candidate_id"), str)]
                    selected_ids = defaultdict(list)
                    for leg in selected:
                        selected_ids[leg["fixture_id"]].append(leg["candidate_id"])
                    for decision in trace["fixture_decisions"]:
                        decision["selected_candidate_ids"] = selected_ids[decision["fixture_id"]]
                        decision["status"] = "SELECTED" if decision["selected_candidate_ids"] else "RANKED" if decision["ranked_candidate_ids"] else "REJECTED"
                option["quality_warnings"] = [
                    "Kísérleti modell: a valószínűségek még nem kalibráltak; csak papírteszt."]
                if pending:
                    option["quality_warnings"].append(
                        "Ehhez a választáshoz nincs ellenőrzött szolgáltatói ár; hozam nem számítható.")
                option["recommendation_id"] = canonical_sha256([
                    VERSION, target, profile, now.isoformat(),
                    [(leg["candidate_id"], leg["model_probability"], leg["decimal_price"]) for leg in selected],
                ])[:24]
                templates.append({"quality": scoring["ticket_score"], "scoring": scoring,
                                  "recommendation": option})
            diversity_options[profile] = templates
        output.append(item)
    return output
