"""Independent, uncalibrated model estimates for paper testing only.

Selection depends on model outputs and matched provider prices. Legacy support
scores and reviewer opinions never enter the ranking or change probabilities.
"""

from __future__ import annotations

import math
from collections import Counter
from copy import deepcopy
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.engine.ticket_options import bounded_ticket_groups
from goal_analysis.engine.ticket_scoring import normalize_score, ticket_quality
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


def _options(priced, unpriced, preferred=(), max_price=50):
    # Priced and unpriced selection quality never share a cohort. Three is a
    # computational construction ceiling, not a required number of legs.
    return bounded_ticket_groups(
        priced or unpriced, rank=lambda legs: _selection_rank(legs, preferred),
        fixture=lambda leg: leg["fixture_id"],
        cohort=lambda leg: (leg.get("bookmaker"), leg.get("region"), leg.get("currency")),
        price=lambda leg: leg["decimal_price"],
        identity=lambda leg: (leg["fixture_id"], leg["market_key"], leg["selection_key"], leg["period"]),
        max_legs=3, max_price=max_price,
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
    target = settings.get("date") or settings.get("target_date")
    target = str(
        target or next((f.get("target_date") for f in fixtures if f.get("target_date")), "")
    )
    target = target or now.astimezone(ZoneInfo("Europe/Berlin")).date().isoformat()
    counts = Counter(f.get("fixture_id") for f in fixtures)
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
        options, search = _options(priced, unpriced, preferred, settings.get("target_max_odds", 50))
        legs = list(options[0]) if options else []
        if not upcoming:
            card_status = "NO_FIXTURES"
        elif status in ERROR_STATUSES:
            card_status = status
        elif legs:
            card_status = "ODDS_PENDING" if legs[0]["decimal_price"] is None else "MODEL_READY"
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
            if legs and card_status == "MODEL_READY"
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
                    legs=selected, status="ODDS_PENDING" if pending else "MODEL_READY",
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
