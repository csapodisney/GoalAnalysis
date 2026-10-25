"""Bounded, deterministic persona selectors over one shared evidence snapshot.

No model fitting, provider access, language-model calls or state writes occur here.
Personality scores are transparent heuristics, never calibrated probabilities.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.engine.model_recommendations import (
    MARKETS,
    SETTLEMENT,
    _name,
    _number,
    _probabilities,
    _quote_matches,
    _time,
)
from goal_analysis.engine.ticket_options import bounded_ticket_groups
from goal_analysis.engine.ticket_scoring import normalize_score
from goal_analysis.engine.veto_overrides import league_base_rate_context, verified_veto_override
from goal_analysis.normalization.live_identity import fixture_identity
from goal_analysis.quote_metadata import quote_metadata
from goal_analysis.team.metrics import _quote_valid

from .catalog import CATALOG_VERSION, TIPSTER_CATALOG

VERSION = "arthur-tipster-recommendations-v6-fixed-accumulators"
DIVERSITY_OPTIONS_VERSION = "arthur-club-diversity-options-v5-broader-search"
# Local CPU search only. Shared defaults (including numeric profiles) and the
# protected DAILY_223 path remain unchanged; persona gates still apply first.
CLUB_OPTION_BEAM_WIDTH = 128
CLUB_MAX_TICKET_OPTIONS = 512
BERLIN = ZoneInfo("Europe/Berlin")
PRODUCTS = {**MARKETS, "btts_no": ("btts", "no")}
KEYS = {product: key for key, product in PRODUCTS.items()}
REASONS = {
    "NO_FIXTURES": "Nincs még el nem kezdődött, egyértelműen azonosított mérkőzés a kiválasztott napra.",
    "NO_VALID_QUOTES": "Nincs a mérkőzéshez és piachoz ellenőrizhető eredeti szolgáltatói szorzó.",
    "INSUFFICIENT_EVIDENCE": "A karakter szabályaihoz nincs elég ellenőrizhető történeti vagy közös modelladat.",
    "NO_COMPLIANT_COMBINATION": "Nincs a karakter meccsszámának, szorzótartományának és alátámasztási feltételeinek megfelelő kombináció.",
}


def _fixtures(rows, target, now):
    rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    counts = Counter(row.get("fixture_id") for row in rows if isinstance(row.get("fixture_id"), str))
    result = {}
    for row in rows:
        identifier, kickoff = row.get("fixture_id"), _time(row.get("kickoff"))
        try:
            fixture_identity(row)
        except (ValueError, TypeError, KeyError):
            continue
        if (counts[identifier] != 1 or kickoff is None or kickoff <= now
                or kickoff.astimezone(BERLIN).date().isoformat() != target
                or row.get("provider_status") != "NS"
                or not _name(row.get("home_team")) or not _name(row.get("away_team"))
                or _name(row["home_team"]) == _name(row["away_team"])):
            continue
        result[identifier] = row
    return result


def _models(results, fixtures, now):
    """Index supplied predictions once; missing/late provenance is not evidence."""
    output = defaultdict(dict)
    for source in ("gauss", "kepler", "laplace"):
        result = results.get(source, {})
        if (not isinstance(result, dict) or result.get("status") not in {"READY", "MODEL_READY", "OK"}
                or result.get("profile_id", source) != source):
            continue
        rows = result.get("predictions", [])
        if not isinstance(rows, list):
            continue
        rows = [row for row in rows if isinstance(row, dict) and isinstance(row.get("fixture_id"), str)]
        counts = Counter(row["fixture_id"] for row in rows)
        for row in rows:
            identifier = row["fixture_id"]
            details = row.get("model_details", {})
            if not isinstance(details, dict):
                continue
            cutoff = _time(details.get("cutoff"))
            if identifier not in fixtures or counts[identifier] != 1 or cutoff is None or cutoff > now:
                continue
            values = _probabilities(row, source)
            stability = row.get("stability", {})
            if source == "gauss":
                if not isinstance(stability, dict):
                    continue
                values = {key: value for key, value in values.items()
                          if _number(stability.get(key)) and 0 <= stability[key] <= 0.08}
            if "btts_yes" in values:
                values["btts_no"] = 1.0 - values["btts_yes"]
            if values:
                output[identifier][source] = {"values": values, "details": details,
                                               "gauss_stability": stability if source == "gauss" else {}}
    return output


def _evidence(candidate, enriched, now):
    """Only the existing, hashed history-enrichment evidence is a ranking input."""
    snapshot = (enriched.get("history_analysis") or {}).get("snapshot_sha256")
    if not isinstance(snapshot, str) or not re.fullmatch(r"[a-f0-9]{64}", snapshot):
        return [], {}
    output, scores = [], {}
    rows = candidate.get("evidence", [])
    if not isinstance(rows, list):
        return [], {}
    for evidence in rows:
        if not isinstance(evidence, dict):
            continue
        category = evidence.get("category")
        observed = _time(evidence.get("observed_at"))
        if (category not in {"historical", "venue_form"}
                or evidence.get("source_id") != "history-sha256:" + snapshot
                or evidence.get("evidence_id") != f"history-v1:{candidate['candidate_id']}:{category}"
                or observed is None or observed > now
                or any(not _number(evidence.get(k)) or not 0 <= evidence[k] <= 1
                       for k in ("strength", "reliability"))):
            continue
        # Duplicate categories cannot amplify their own weight.
        if category in scores:
            return [], {}
        scores[category] = evidence["strength"] * evidence["reliability"]
        output.append(deepcopy(evidence))
    return output, scores


def _verified_quote(candidate, fixture):
    market, selection = candidate.get("market_key"), candidate.get("selection_key")
    if (market, selection) not in KEYS or not _quote_matches(candidate, fixture, market, selection):
        return False
    if not all(isinstance(candidate.get(k), str) and candidate[k].strip()
               for k in ("candidate_id", "region", "currency")):
        return False
    try:
        url = urlsplit(candidate["quote_source_id"])
        if url.port not in (None, 443) or url.fragment:
            return False
        # Reuse the supervisor's stricter source identity/market crosswalk check.
        return _quote_valid({"fixture_id": fixture["fixture_id"],
                             "market": "FULL_TIME:" + market, "selection": selection,
                             "quote_provenance": candidate})
    except (ValueError, TypeError):
        return False


def _pool(enriched, fixtures, models, now):
    candidates = enriched.get("candidates", [])
    if not isinstance(candidates, list):
        return [], 0
    candidates = [c for c in candidates if isinstance(c, dict) and isinstance(c.get("candidate_id"), str)]
    counts = Counter(c["candidate_id"] for c in candidates)
    output, quotes = [], 0
    for candidate in candidates:
        fixture_id = candidate.get("fixture_id")
        if not isinstance(fixture_id, str) or fixture_id not in fixtures or counts[candidate["candidate_id"]] != 1:
            continue
        if not _verified_quote(candidate, fixtures[fixture_id]):
            continue
        quotes += 1
        evidence, scores = _evidence(candidate, enriched, now)
        output.append({"quote": candidate, "fixture": fixtures[fixture_id], "evidence": evidence,
                       "history": scores.get("historical", 0.0), "venue": scores.get("venue_form", 0.0),
                       "key": KEYS[(candidate["market_key"], candidate["selection_key"])],
                       "market_context": {},
                       "models": models.get(fixture_id, {})})
    expected = {"h2h": {"home", "draw", "away"},
                "totals_2_5": {"over", "under"}, "btts": {"yes", "no"}}
    groups = defaultdict(list)
    for row in output:
        quote = row["quote"]
        group = (quote["fixture_id"], quote["market_key"], quote["period"],
                 quote["bookmaker"], quote["region"], quote["currency"],
                 quote.get("quote_provider"), quote.get("line", quote.get("handicap")))
        groups[group].append(row)
    for rows in groups.values():
        outcomes = [row["quote"]["selection_key"] for row in rows]
        market = rows[0]["quote"]["market_key"]
        if set(outcomes) != expected.get(market) or len(outcomes) != len(set(outcomes)):
            continue
        overround = math.fsum(1 / row["quote"]["decimal_price"] for row in rows)
        if not math.isfinite(overround) or overround <= 1:
            continue
        for row in rows:
            raw = 1 / row["quote"]["decimal_price"]
            row["market_context"] = {
                "method": "PROPORTIONAL_DEVIG_COMPLETE_MARKET",
                "outcome_count": len(rows), "overround": overround,
                "raw_implied_probability": raw, "fair_probability": raw / overround,
            }
    return output, quotes


def _contrarian_analysis(row, persona, now, *, require_review=True):
    """Validate a same-product historical rise and a separate model value case.

    Rising odds alone do not identify retail sentiment or their factual cause.
    The historical adapter verifies source records; this boundary rechecks the
    identity, timestamps and arithmetic instead of trusting a status label.
    """
    policy, quote = persona["selection_policy"], row["quote"]
    if not policy.get("contrarian_value"):
        return {}
    movement = quote.get("odds_movement")
    if not isinstance(movement, dict) or movement.get("status") != "VERIFIED_RISE":
        return None
    baseline, current = movement.get("baseline_price"), movement.get("current_price")
    observed = quote.get('quote_provider') == 'the_stats_api'
    older = _time(movement.get('baseline_observed_at' if observed else 'baseline_quoted_at'))
    newer = _time(movement.get('current_observed_at' if observed else 'current_quoted_at'))
    as_of = _time(movement.get("observed_as_of"))
    quoted_at = _time(quote.get('quote_observed_at')) if observed else _time(quote.get("quote_timestamp_raw", quote.get("quoted_at")))
    identity = movement.get("identity")
    keys = ("fixture_id", "market_key", "selection_key", "period", "settlement", "bookmaker",
            "region", "currency", "quote_provider")
    expected = {key: quote.get(key, SETTLEMENT if key == "settlement" else None) for key in keys}
    if (not _number(baseline) or not _number(current) or not 1 < baseline < current
            or not math.isclose(current, quote["decimal_price"], rel_tol=1e-12)
            or older is None or newer is None or as_of is None or quoted_at is None
            or not older < newer <= as_of <= now or newer != quoted_at
            or not 24 <= (now - older).total_seconds() / 3600 <= 48
            or not isinstance(identity, dict) or any(identity.get(key) != value for key, value in expected.items())
            or not isinstance(movement.get("source_run_id"), str) or not movement["source_run_id"]
            or not isinstance(movement.get("evidence_id"), str) or not movement["evidence_id"]
            or not re.fullmatch(r"[a-f0-9]{64}", str(movement.get("source_snapshot_sha256", "")))):
        return None
    rise = current / baseline - 1
    if (rise < policy["contrarian_min_rise"] - 1e-12
            or not _number(movement.get("relative_change"))
            or not math.isclose(rise, movement["relative_change"], rel_tol=1e-9, abs_tol=1e-12)):
        return None
    probability = row["probability"]
    if probability is None or not any(source in row["components"] for source in ("gauss", "laplace")):
        return None
    expected_return = probability * current
    if expected_return <= policy["contrarian_min_expected_return"]:
        return None
    review = quote.get("contrarian_review")
    if require_review and (
        not isinstance(review, dict) or review.get("status") != "COMPLETE"
        or review.get("candidate_id") != quote["candidate_id"]
        or _time(review.get("reviewed_at")) is None
        or not newer <= _time(review.get("reviewed_at")) <= now
        or any(not isinstance(review.get(key), str) or not review[key].strip() for key in ("price_case", "math_case"))
        or review.get("cause_status") not in {"SOURCED", "UNKNOWN"}
        or not isinstance(review.get("cause_source_urls"), list)
        or (review.get("cause_status") == "SOURCED" and not review["cause_source_urls"])
    ):
        return None
    return {
        "kind": "CONTRARIAN_VALUE_UNCALIBRATED", "baseline_price": baseline,
        "current_price": current, "relative_change": rise,
        **({'baseline_observed_at': older.isoformat(), 'current_observed_at': newer.isoformat(),
            'time_basis': 'LOCAL_SNAPSHOT_OBSERVATIONS'} if observed else
           {'baseline_quoted_at': older.isoformat(), 'current_quoted_at': newer.isoformat()}),
        "baseline_age_hours": (now - older).total_seconds() / 3600,
        "model_probability": probability, "supporting_models": deepcopy(row["components"]),
        "expected_return": expected_return, "estimated_edge": expected_return - 1,
        "minimum_expected_return_exclusive": policy["contrarian_min_expected_return"],
        "minimum_rise": policy["contrarian_min_rise"],
        "evidence_id": movement["evidence_id"], "odds_movement": deepcopy(movement),
        "explanation": deepcopy(review) if require_review else None,
        "retail_sentiment_inferred": False, "calibrated_probability": False,
    }


def _ranked(base, persona, now, *, require_contrarian_review=True, propose_soft_veto=False):
    policy = persona["selection_policy"]
    output = []
    for row in base:
        price, key = row["quote"]["decimal_price"], row["key"]
        allowed = policy.get("allowed_market_keys")
        if allowed is not None and key not in allowed:
            continue
        override = verified_veto_override(row["quote"], now=now)
        overridden = set((override or {}).get("gates", []))
        context = row["quote"].get("veto_override_context")
        proposed = set(context.get("gates", [])) if isinstance(context, dict) else set()
        if (price >= persona["target_odds_band"]["max"] and "HIGH_ODDS" not in overridden
                and not (propose_soft_veto and "HIGH_ODDS" in proposed)):
            continue
        if ("LEAGUE_BASE_RATE" in proposed and "LEAGUE_BASE_RATE" not in overridden
                and not propose_soft_veto):
            continue
        components = {source: result["values"][key] for source, result in row["models"].items()
                      if key in result["values"] and persona["model_weights"][source] > 0}
        total = sum(persona["model_weights"][source] for source in components)
        weights = {source: persona["model_weights"][source] / total for source in components}
        probability = sum(weights[source] * value for source, value in components.items()) if components else None
        spread = max(components.values()) - min(components.values()) if len(components) > 1 else None
        edge = probability * price - 1 if probability is not None else None
        fair = row["market_context"].get("fair_probability")
        market_disagreement = probability - fair if probability is not None and fair is not None else None
        if (len(components) < policy["min_models"]
                or any(source not in components for source in policy.get("required_models", []))
                or (probability is not None and probability < policy["min_probability"] - 1e-12)
                or (spread is not None and spread > policy["max_model_spread"])
                or (policy["min_edge"] is not None and (edge is None or edge < policy["min_edge"] - 1e-12))
                or (policy["min_market_disagreement"] is not None and (
                    market_disagreement is None
                    or market_disagreement < policy["min_market_disagreement"] - 1e-12))
                or (probability is None and row["history"] + row["venue"] <= 0)):
            continue
        case = _contrarian_analysis({**row, "probability": probability, "components": components},
                                    persona, now, require_review=require_contrarian_review)
        if case is None:
            continue
        # Every channel is dimensionless before applying persona weights.
        # Model probabilities remain unchanged in their own diagnostic fields;
        # the resulting weighted support is explicitly a heuristic, not p(win).
        bonus_scale = max(policy["market_bonus"].values(), default=0.0)
        components_normalized = {
            "model_probability": normalize_score(probability or 0.0),
            "estimated_edge": max(0.0, edge or 0.0) / (1 + max(0.0, edge or 0.0)),
            "negative_estimated_edge": max(0.0, -(edge or 0.0)) / (1 + max(0.0, -(edge or 0.0))),
            "market_disagreement": normalize_score(max(0.0, market_disagreement or 0.0), scale=0.15),
            "historical_evidence": normalize_score(row["history"]),
            "venue_evidence": normalize_score(row["venue"]),
            "market_preference": normalize_score(policy["market_bonus"].get(key, 0.0),
                                                  scale=bonus_scale or 1.0),
            "model_spread": normalize_score(spread or 0.0),
        }
        weights_normalized = {
            "model_probability": policy["probability_weight"], "estimated_edge": policy["edge_weight"],
            "negative_estimated_edge": -policy["edge_weight"],
            "market_disagreement": policy["market_weight"],
            "historical_evidence": policy["history_weight"], "venue_evidence": policy["venue_weight"],
            "market_preference": bonus_scale, "model_spread": -policy["spread_penalty"],
        }
        scale = sum(value for value in weights_normalized.values() if value > 0) or 1.0
        terms = {name: value * weights_normalized[name] / scale
                 for name, value in components_normalized.items()}
        output.append({**row, "probability": probability, "components": components,
                       "weights": weights, "spread": spread, "edge": edge,
                       "market_fair_probability": fair,
                       "market_disagreement": market_disagreement,
                       "score_terms": terms, "score": normalize_score(sum(terms.values())),
                       "normalized_components": components_normalized, "score_scale": scale,
                       "contrarian_analysis": case, "veto_override": override})
    return sorted(output, key=lambda row: (-row["score"], row["quote"]["candidate_id"]))


def _ticket_rank(rows, persona, preferred=()):
    """Rank for the persona's declared construction; more legs are not a reward."""
    scores = [row["score"] for row in rows]
    exposure = tuple(sorted((row["quote"]["fixture_id"], row["quote"]["market_key"],
                             row["quote"]["selection_key"], row["quote"]["period"]) for row in rows))
    book = rows[0]["quote"]["bookmaker"]
    book_rank = next((i for i, name in enumerate(preferred)
                      if book == name or book.startswith(name + "_")), len(preferred))
    return (-_scoring(rows, persona)["ticket_score"], -min(scores), -sum(scores) / len(scores), exposure,
            -math.prod(row["quote"]["decimal_price"] for row in rows), book_rank,
            tuple(sorted(row["quote"]["candidate_id"] for row in rows)))


def _scoring(rows, persona):
    """Weak-leg-aware unit utility with a bounded preferred-length fit."""
    scores = [row["score"] for row in rows]
    if not scores:
        return {"ticket_score": 0.0, "normalized_quality": 0.0,
                "normalized_leg_scores": [], "length_factor": 0.0}
    mean = math.fsum(scores) / len(scores)
    weakest = min(scores)
    preferred = persona["preferred_legs"]
    length_factor = max(0.70, 1 - 0.12 * abs(len(rows) - preferred))
    policy = persona["selection_policy"]
    market_bonus = competition_bonus = 0.0
    if len(rows) > 1:
        market_ratio = (len({row["key"] for row in rows}) - 1) / (len(rows) - 1)
        competitions = {row["fixture"].get("competition") for row in rows}
        competition_ratio = (len(competitions) - 1) / (len(rows) - 1)
        market_bonus = min(0.05, policy["market_diversity"] * 0.025 * market_ratio)
        competition_bonus = min(0.04, policy["competition_diversity"] * 0.02 * competition_ratio)
    base = 0.75 * mean + 0.25 * weakest
    final = min(1.0, base * length_factor + market_bonus + competition_bonus)
    return {
        "version": "arthur-persona-ticket-utility-v2",
        "score_kind": "NORMALIZED_PERSONA_SUPPORT_HEURISTIC",
        "normalized_leg_scores": scores, "normalized_quality": mean,
        "weakest_leg_quality": weakest, "preferred_legs": preferred,
        "actual_legs": len(rows), "length_factor": length_factor,
        "market_diversity_bonus": market_bonus,
        "competition_diversity_bonus": competition_bonus,
        "ticket_score": final, "calibrated_probability": False,
    }


def _options(ranked, persona, preferred=(), *, fixture_coverage=False):
    options, search = bounded_ticket_groups(
        ranked, rank=lambda rows: _ticket_rank(rows, persona, preferred),
        fixture=lambda row: row["quote"]["fixture_id"],
        cohort=lambda row: tuple(row["quote"][key] for key in ("bookmaker", "region", "currency")),
        price=lambda row: row["quote"]["decimal_price"],
        identity=lambda row: tuple(row["quote"][key] for key in
                                   ("fixture_id", "market_key", "selection_key", "period")),
        max_legs=persona["max_legs"], max_price=persona["target_odds_band"]["max"],
        min_legs=persona["min_legs"], min_price=persona["target_odds_band"]["min"],
        beam_width=CLUB_OPTION_BEAM_WIDTH, max_options=CLUB_MAX_TICKET_OPTIONS,
        fixture_coverage=fixture_coverage,
        # A candidate-specific exception authorizes that selection alone; it
        # cannot silently approve an arbitrarily larger combined ticket price.
        allow_price_overflow=lambda group: len(group) == 1 and "HIGH_ODDS" in (
            (group[0].get("veto_override") or {}).get("gates", [])),
    )
    # A verified individual HIGH_ODDS exception remains a singleton only;
    # it cannot authorize extra legs or bypass the other evidence requirements.
    if persona["min_legs"] > 1:
        exceptions = [(row,) for row in ranked
                      if row["quote"]["decimal_price"] > persona["target_odds_band"]["max"]
                      and "HIGH_ODDS" in ((row.get("veto_override") or {}).get("gates", []))]
        options = sorted([*options, *exceptions], key=lambda rows: _ticket_rank(rows, persona, preferred))
    eligible_fixtures = {row["quote"]["fixture_id"] for row in ranked}
    option_fixtures = {row["quote"]["fixture_id"] for group in options for row in group}
    # These counts expose computational/shape pruning, not missing data or a
    # claim that every eligible leg can form a compliant complete ticket.
    search.update(
        eligible_fixture_count=len(eligible_fixtures),
        retained_option_fixture_count=len(option_fixtures),
        eligible_fixtures_without_option_count=len(eligible_fixtures - option_fixtures),
    )
    return options, search


def _coverage_shadow(ranked, persona, preferred, options, search):
    """Bounded search comparison only; never substitute publication choices."""
    if not search["bounded_search"] or not search["eligible_fixtures_without_option_count"]:
        return None
    challenger, probe = _options(ranked, persona, preferred, fixture_coverage=True)
    before = {row["quote"]["fixture_id"] for group in options for row in group}
    after = {row["quote"]["fixture_id"] for group in challenger for row in group}
    introduced = after - before
    example = next((group for group in challenger
                    if {row["quote"]["fixture_id"] for row in group} & introduced), ())
    return {
        "version": "club-fixture-coverage-search-shadow-v1", "mode": "SHADOW", "status": "COMPARED",
        "publication_choices_unchanged": True, "new_model_fits": 0, "new_api_or_llm_calls": 0,
        "same_eligible_source_rows": True, "new_probability_or_evidence_threshold": False,
        "beam_width": CLUB_OPTION_BEAM_WIDTH, "max_options": CLUB_MAX_TICKET_OPTIONS,
        "baseline_options": len(options), "challenger_options": len(challenger),
        "eligible_fixtures": search["eligible_fixture_count"],
        "baseline_retained_fixtures": len(before), "challenger_retained_fixtures": len(after),
        "newly_represented_fixture_ids": sorted(introduced),
        "no_longer_represented_fixture_ids": sorted(before - after),
        "example_new_fixture_route_candidate_ids": [row["quote"]["candidate_id"] for row in example],
        "additional_combinations_examined": probe["combinations_examined"],
        "challenger_is_exhaustive_or_promoted": False,
    }


def _coverage_active(ranked, persona, preferred, options, search):
    from .coverage_search import VERSION as COVERAGE_VERSION
    from .coverage_search import promote

    try:
        return promote(ranked, persona, options, search,
                       challenger=lambda: _options(ranked, persona, preferred, fixture_coverage=True),
                       rank=lambda rows: _ticket_rank(rows, persona, preferred),
                       maximum=CLUB_MAX_TICKET_OPTIONS)
    except Exception as error:  # noqa: BLE001 - failed optional search keeps validated baseline.
        return options, search, {'version': COVERAGE_VERSION, 'mode': 'ACTIVE',
                                 'status': 'UNAVAILABLE_BASELINE_RETAINED',
                                 'failure_class': type(error).__name__,
                                 'publication_choices_unchanged': True}


def _select(ranked, persona, preferred=()):
    options, search = _options(ranked, persona, preferred)
    return (list(options[0]) if options else [], search["combinations_examined"],
            search["bookmaker_pools_searched"])


def _leg(row, persona, now):
    quote = row["quote"]
    leg = deepcopy(quote)
    identity = [persona["profile_id"], quote["candidate_id"]]
    components = row["components"]
    leg.update(
        candidate_id=persona["profile_id"] + "-" + canonical_sha256(identity)[:20],
        source_candidate_id=quote["candidate_id"], quote_candidate_id=quote["candidate_id"],
        model_probability=row["probability"], model_stability=row["spread"], estimated_edge=row["edge"],
        confidence="LOW", confidence_kind="UNCALIBRATED_SHARED_MODEL_ESTIMATE" if components
        else "EVIDENCE_HEURISTIC_NOT_PROBABILITY", evidence=deepcopy(row["evidence"]),
        missing_support=[name for name, value in (("historical", row["history"]), ("venue_form", row["venue"])) if not value],
        model_details={
            "method": "SHARED_MODEL_WEIGHTED_MEAN" if components else "HISTORY_EVIDENCE_HEURISTIC",
            "calibration": "unvalidated", "shared_model_probabilities": deepcopy(components),
            "shared_model_weights": deepcopy(row["weights"]),
            "shared_model_details": {source: deepcopy(row["models"][source]["details"]) for source in components},
            "stability_kind": "SHARED_MODEL_SPREAD_MAX_MINUS_MIN" if row["spread"] is not None else "UNKNOWN_SINGLE_OR_NO_MODEL",
            "derived_complement": row["key"] == "btts_no" and bool(components),
            "selection_score": row["score"], "selection_score_kind": "HEURISTIC_NOT_PROBABILITY",
            "selection_score_terms": deepcopy(row["score_terms"]),
            "normalized_score_components": deepcopy(row.get("normalized_components", {})),
            "normalization_scale": row.get("score_scale", 1.0),
            "market_context": deepcopy(row.get("market_context", {})),
            "market_fair_probability": row.get("market_fair_probability"),
            "model_market_disagreement": row.get("market_disagreement"),
        },
        selection_reason=f"{persona['name']}: {', '.join(persona['preferences'])}. A sorrend heurisztikus, nem nyerési garancia.",
    )
    leg.update(quote_metadata(quote.get("quote_timestamp_raw", quote.get("quoted_at")), now))
    if row.get("contrarian_analysis"):
        leg["contrarian_analysis"] = deepcopy(row["contrarian_analysis"])
    if row.get("veto_override"):
        leg["veto_override"] = {
            **deepcopy(row["veto_override"]), "profile_upper_price": persona["target_odds_band"]["max"],
            "raw_decimal_price": quote["decimal_price"],
        }
    return leg


def contrarian_candidate_context(enriched, model_results, settings, now):
    """Return deterministic value cases for the existing bounded Astra review.

    This preparatory view bypasses only the explanation requirement; it does
    not publish tickets or waive any price, model or movement eligibility gate.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    target = str(settings.get("date") or enriched.get("date") or now.astimezone(BERLIN).date())
    fixtures = _fixtures(enriched.get("fixtures", []), target, now)
    models = _models(model_results, fixtures, now)
    base, _ = _pool(enriched, fixtures, models, now)
    enabled = set(settings.get("enabled_profiles", []))
    contexts = {}
    for persona in TIPSTER_CATALOG:
        if persona["profile_id"] not in enabled or not persona["selection_policy"].get("contrarian_value"):
            continue
        for row in _ranked(base, persona, now, require_contrarian_review=False, propose_soft_veto=True):
            identifier = row["quote"]["candidate_id"]
            item = contexts.setdefault(identifier, {
                "candidate_id": identifier, "profile_ids": [],
                **deepcopy(row["contrarian_analysis"]),
            })
            item["profile_ids"].append(persona["profile_id"])
    return contexts


def club_veto_candidate_context(enriched, model_results, settings, now):
    """Propose named soft-cap reviews without granting publication authority."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    target = str(settings.get("date") or enriched.get("date") or now.astimezone(BERLIN).date())
    fixtures = _fixtures(enriched.get("fixtures", []), target, now)
    models = _models(model_results, fixtures, now)
    base, _ = _pool(enriched, fixtures, models, now)
    enabled, contexts = set(settings.get("enabled_profiles", [])), {}
    proposed = deepcopy(base)
    for row in proposed:
        row["quote"]["veto_override_context"] = {"gates": ["HIGH_ODDS"]}
    for persona in TIPSTER_CATALOG:
        if persona["profile_id"] not in enabled:
            continue
        for row in _ranked(proposed, persona, now, require_contrarian_review=False, propose_soft_veto=True):
            maximum = persona["target_odds_band"]["max"]
            high = row["quote"]["decimal_price"] >= maximum
            _, _, league = _history_support_details(row, enriched, target, settings, now)
            if not high and not league:
                continue
            identifier = row["quote"]["candidate_id"]
            context = contexts.setdefault(identifier, {
                "gates": [], "profile_ids": [], "profile_upper_price_limits": {},
                "raw_decimal_price": row["quote"]["decimal_price"],
                "interpretation": "Név szerinti lágy profilkorlát felülvizsgálata; nem engedély és nem valószínűség.",
            })
            context["profile_ids"].append(persona["profile_id"])
            context["profile_upper_price_limits"][persona["profile_id"]] = maximum
            if high:
                context["gates"] = sorted(set(context["gates"]) | {"HIGH_ODDS"})
            if league:
                context.update({key: value for key, value in league.items() if key != "gates"})
                context["gates"] = sorted(set(context["gates"]) | {"LEAGUE_BASE_RATE"})
    return contexts


def _history_support_details(row, enriched, target, settings, now):
    """Compute the unchanged specialist floor and a narrow league-rate context."""
    # Lazy import avoids loading the portfolio adapter for ordinary persona use.
    from goal_analysis.engine.daily_223 import WEIGHTS, Daily223Policy
    from goal_analysis.engine.portfolio import permissive, prepare_portfolio_candidate

    strictness = settings.get("strictness", 35)
    policy = Daily223Policy(**enriched.get("policy", {}))
    if not permissive(settings) and (not row["history"] or not row["venue"]):
        return None, None, None
    try:
        prepared = prepare_portfolio_candidate(row["quote"], date.fromisoformat(target), now, policy, settings)
        missing_weight = sum(WEIGHTS[key] for key in prepared["missing_support"])
        penalty = missing_weight / 100 * strictness * 0.30
        support = round(max(0, prepared["support_score"] - penalty), 6)
        minimum = 0 if permissive(settings) else 3 + strictness * 0.12
        analysis = next((item for item in enriched.get("history_analysis", {}).get("candidates", [])
                         if item.get("candidate_id") == row["quote"]["candidate_id"]), None)
        context = league_base_rate_context(row["quote"], analysis, support, minimum)
        return support, minimum, context
    except (ValueError, KeyError, TypeError, OverflowError):
        return None, None, None


def _history_option_support(row, enriched, target, settings, now):
    """Preserve hard history gates; override only an evidenced soft rate floor."""
    support, minimum, context = _history_support_details(row, enriched, target, settings, now)
    if support is None:
        return None
    override = verified_veto_override(row["quote"], now=now)
    allowed = context and "LEAGUE_BASE_RATE" in (override or {}).get("gates", [])
    return support if support >= minimum or allowed else None


def _diversity_template(item, rows, persona, target, now, history_support):
    """A complete, detached ticket; allocation belongs to the coordinator."""
    result = deepcopy(item)
    legs = [_leg(row, persona, now) for row in rows]
    result.update(legs=legs, status="READY", reason_code=None, abstention_reason=None,
                  combined_price=math.prod(leg["decimal_price"] for leg in legs),
                  bookmaker=legs[0]["bookmaker"])
    result["quality_warnings"] = result["quality_warnings"][:3]
    if any(leg["model_probability"] is None for leg in legs):
        result["quality_warnings"].append(
            "Egyes választások csak leíró történeti bizonyítékon alapulnak; modellvalószínűségük ismeretlen.")
    result["model_summary"]["selected_legs"] = len(legs)
    result["selection_diagnostics"].update(
        selected_fixture_ids=sorted(leg["fixture_id"] for leg in legs),
        selected_modeled_leg_count=sum(leg["model_probability"] is not None for leg in legs),
        selected_negative_estimated_edge_leg_count=sum(
            leg["estimated_edge"] is not None and leg["estimated_edge"] < 0 for leg in legs),
        diversity_options_version=DIVERSITY_OPTIONS_VERSION,
        history_only_weighted_support=history_support,
        scoring=_scoring(rows, persona),
    )
    result["recommendation_id"] = canonical_sha256(
        [VERSION, target, persona["profile_id"], now.isoformat(),
         [(leg["candidate_id"], leg["decimal_price"], leg["model_probability"]) for leg in legs]])[:24]
    return result


def _positive_support(row):
    """One existing alternative gate, shared by both construction paths."""
    return _number(row["score"]) and row["score"] > 0


def build_tipster_recommendations(
    enriched: dict, model_results: dict, settings: dict, now: datetime,
    *, diversity_options: dict | None = None, support_guard_mode: str = "LIVE",
    option_search_mode: str = "SHADOW",
) -> list:
    """Build paper cards; SHADOW compares the existing alternative support gate.

    The guard shares the already enforced alternative rule, not a newly fitted
    probability/quality threshold. SHADOW preserves the primary card and options.
    Coverage search defaults to SHADOW; explicit ACTIVE supplies bounded routes.
    Context review and the allocator still decide publication eligibility.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    if support_guard_mode not in {"SHADOW", "LIVE"}:
        raise ValueError("support_guard_mode must be SHADOW or LIVE")
    if option_search_mode not in {"SHADOW", "OFF", "ACTIVE"}:
        raise ValueError("option_search_mode must be SHADOW, OFF or ACTIVE")
    if option_search_mode == "ACTIVE" and support_guard_mode != "LIVE":
        raise ValueError("ACTIVE coverage requires the enforced support guard")
    target = str(settings.get("date") or enriched.get("date") or now.astimezone(BERLIN).date())
    fixtures = _fixtures(enriched.get("fixtures", []), target, now)
    models = _models(model_results, fixtures, now)
    base, quote_count = _pool(enriched, fixtures, models, now)
    enabled = set(settings.get("enabled_profiles", []))
    output = []
    history_support_cache = {}
    for persona in TIPSTER_CATALOG:
        profile = persona["profile_id"]
        if profile not in enabled:
            continue
        ranked = _ranked(base, persona, now)
        supported = [row for row in ranked if _positive_support(row)]
        excluded = [row["quote"]["candidate_id"] for row in ranked if not _positive_support(row)]
        preferred = [name for name in enriched.get("preferred_bookmakers", []) if isinstance(name, str)]
        primary_pool = supported if support_guard_mode == "LIVE" else ranked
        options, search = _options(primary_pool, persona, preferred)
        search_shadow = None
        if option_search_mode == "SHADOW" and support_guard_mode == "LIVE":
            try:
                search_shadow = _coverage_shadow(supported, persona, preferred, options, search)
            except Exception:  # noqa: BLE001 - optional probe never destroys stable construction.
                search_shadow = {"version": "club-fixture-coverage-search-shadow-v1", "mode": "SHADOW",
                                 "status": "UNAVAILABLE", "publication_choices_unchanged": True}
        search_active = None
        if option_search_mode == "ACTIVE":
            options, search, search_active = _coverage_active(primary_pool, persona, preferred, options, search)
        chosen = list(options[0]) if options else []
        legs = [_leg(row, persona, now) for row in chosen]
        reason = (None if legs else "NO_FIXTURES" if not fixtures else "NO_VALID_QUOTES" if not quote_count
                  else "INSUFFICIENT_EVIDENCE" if not primary_pool else "NO_COMPLIANT_COMBINATION")
        warnings = [
            "Fiktív karakter közös modell- és adatbázisból; nem önállóan betanított vagy független módszer.",
            "Kísérleti ajánlat: nincs kalibrált nyerési esély vagy igazolt nyereségesség.",
            "A karakterek ugyanazokat a meccseket is választhatják; együtt is veszíthetnek.",
        ]
        if reason:
            warnings.append(REASONS[reason])
            if persona["selection_policy"].get("contrarian_value"):
                warnings.append(
                    "Ellenáramú ajánlat csak összevethető, 24–48 órás árforrással, legalább 10% áremelkedéssel, "
                    "GAUSS/LAPLACE becslés × aktuális ár > 1,15 értékkel és külön Astra-magyarázattal készülhet.")
        elif any(leg["model_probability"] is None for leg in legs):
            warnings.append("Egyes választások csak leíró történeti bizonyítékon alapulnak; modellvalószínűségük ismeretlen.")
        item = {
            "profile_id": profile, "profile_name": persona["name"], "persona": deepcopy(persona),
            "date": target, "created_at": now.isoformat(), "strategy_version": VERSION,
            "catalog_version": CATALOG_VERSION, "method_kind": "SHARED_EVIDENCE_PERSONA",
            "source_style": deepcopy(persona["source_style"]),
            "experimental": True, "status": "READY" if legs else "ABSTAINED",
            "reason_code": reason, "abstention_reason": REASONS.get(reason),
            "confidence": "LOW", "confidence_kind": "UNCALIBRATED_SHARED_EVIDENCE_HEURISTIC",
            "probability": None, "odds_pending": False,
            "combined_price": math.prod(leg["decimal_price"] for leg in legs) if legs else None,
            "combined_price_kind": "THEORETICAL_PRODUCT_NOT_BOOKMAKER_COMBO_QUOTE",
            "bookmaker": legs[0]["bookmaker"] if legs else None,
            "stake_eur": None, "portfolio_ticket_id": None, "selected_for_portfolio": False, "playable": False,
            "publication_scope": "PAPER_AUDITION", "legs": legs,
            "model_status": "SHARED_POOL_READY" if primary_pool else "SHARED_POOL_INSUFFICIENT",
            "model_diagnostics": [],
            "model_summary": {"fixtures_considered": len(fixtures), "valid_quotes": quote_count,
                              "eligible_selections": len(primary_pool), "selected_legs": len(legs),
                              **search,
                              "additional_model_fits": 0, "additional_ai_calls": 0},
            "selection_diagnostics": {
                "input_fixture_count": len(fixtures),
                "verified_quote_fixture_count": len({row["quote"]["fixture_id"] for row in base}),
                "ranked_fixture_count": len({row["quote"]["fixture_id"] for row in primary_pool}),
                "shared_model_fixture_count": len(models),
                "ranked_shared_model_fixture_count": len({row["quote"]["fixture_id"]
                                                          for row in primary_pool if row["components"]}),
                "selected_fixture_ids": sorted({leg["fixture_id"] for leg in legs}),
                "allowed_market_keys": deepcopy(persona["selection_policy"]["allowed_market_keys"]),
                "required_models": deepcopy(persona["selection_policy"]["required_models"]),
                "selected_modeled_leg_count": sum(leg["model_probability"] is not None for leg in legs),
                "selected_negative_estimated_edge_leg_count": sum(
                    leg["estimated_edge"] is not None and leg["estimated_edge"] < 0 for leg in legs),
                "scope": "SHARED_EVIDENCE_NOT_INDEPENDENT_MODELS",
                "selection_mode": persona["selection_mode"], "target_odds_minimum_enforced": True,
                "scoring": _scoring(chosen, persona) if chosen else None,
                "contrarian_value_required": bool(persona["selection_policy"].get("contrarian_value")),
            },
            "quality_warnings": warnings,
            "reasoning": [persona["tagline"], persona["personality"],
                          "A karakter piaci, modell- és történeti súlyai befolyásolják a választást.",
                          (f"Állandó profilforma: {persona['min_legs']}–{persona['max_legs']} meccs, "
                          f"{persona['target_odds_band']['min']}–{persona['target_odds_band']['max']}-es összszorzó. "
                          "Ezen belül az átlagos és a leggyengébb láb alátámasztása rangsorol."),
                          "Egy szelvényen csak különböző mérkőzések és egyazon iroda/régió/pénznem árai szerepelhetnek."],
            "risk_notes": [
                "Minden választás veszíthet; a rövidebb ajánlat és alacsonyabb szorzó sem jelent nyerési garanciát.",
                "Az árak elméleti szorzata nem igazolt irodai kombinált ajánlat; a lábak valószínűségét nem szorozzuk össze.",
                "Az ár időbélyege tájékoztató, soha nem kizáró feltétel. Nincs automatikus fogadás.",
            ],
        }
        if legs:
            item["recommendation_id"] = canonical_sha256(
                [VERSION, target, profile, now.isoformat(),
                 [(leg["candidate_id"], leg["decimal_price"], leg["model_probability"]) for leg in legs]])[:24]
        if search_shadow is not None:
            item["selection_diagnostics"]["option_search_shadow"] = search_shadow
        if search_active is not None:
            item["selection_diagnostics"]["option_search_active"] = search_active
        if excluded:
            guarded_options = (_options(supported, persona, preferred)[0]
                               if support_guard_mode == "SHADOW" else options)
            guarded = list(guarded_options[0]) if guarded_options else []
            item["selection_diagnostics"]["positive_support_guard"] = {
                "version": "club-positive-support-parity-v1", "mode": support_guard_mode,
                "scope": "PRIMARY_CARD",
                "rule": "EXISTING_ALTERNATIVE_FINITE_POSITIVE_SUPPORT",
                "excluded_candidate_ids": excluded,
                "would_select_candidate_ids": [row["quote"]["candidate_id"] for row in guarded],
                "would_abstain": not bool(guarded),
                "primary_guard_enforced": support_guard_mode == "LIVE",
            }
        if diversity_options is not None:
            # Screen every source before constructing alternatives. A weak top
            # product cannot hide a compliant alternative from the same game.
            eligible = []
            history_support = {}
            for row in supported if persona["max_legs"] >= 1 else []:
                support = None
                if not row["components"]:
                    source_id = row["quote"]["candidate_id"]
                    if source_id not in history_support_cache:
                        history_support_cache[source_id] = _history_option_support(
                            row, enriched, target, settings, now)
                    support = history_support_cache[source_id]
                    if support is None:
                        continue
                eligible.append(row)
                history_support[row["quote"]["candidate_id"]] = support
            eligible_options, option_search = _options(eligible, persona, preferred)
            alternative_active = None
            if option_search_mode == "ACTIVE":
                eligible_options, option_search, alternative_active = _coverage_active(
                    eligible, persona, preferred, eligible_options, option_search)
            templates = []
            for rows in eligible_options:
                supports = [history_support[row["quote"]["candidate_id"]] for row in rows]
                support = min((value for value in supports if value is not None), default=None)
                recommendation = _diversity_template(item, rows, persona, target, now, support)
                recommendation["selection_diagnostics"]["alternative_search"] = option_search
                if alternative_active is not None:
                    recommendation["selection_diagnostics"]["alternative_search_active"] = alternative_active
                scoring = _scoring(rows, persona)
                templates.append({"quality": scoring["ticket_score"], "scoring": scoring,
                                  "recommendation": recommendation})
            diversity_options[profile] = templates
        output.append(item)
    # Report actual overlap without distorting rankings to manufacture variety.
    published = [item for item in output if item["legs"]]
    exposure = Counter(leg["fixture_id"] for item in published for leg in item["legs"])
    signatures = defaultdict(list)
    for item in published:
        signature = tuple(sorted((leg["fixture_id"], leg["market_key"], leg["selection_key"],
                                  leg["period"]) for leg in item["legs"]))
        signatures[signature].append(item["profile_id"])
    for item in output:
        diagnostics = item["selection_diagnostics"]
        diagnostics.update(
            club_published_profile_count=len(published),
            club_selected_fixture_count=len(exposure),
            club_modeled_leg_count=sum(leg["model_probability"] is not None
                                       for card in published for leg in card["legs"]),
            club_negative_estimated_edge_leg_count=sum(
                leg["estimated_edge"] is not None and leg["estimated_edge"] < 0
                for card in published for leg in card["legs"]),
            club_max_fixture_profile_count=max(exposure.values(), default=0),
            club_max_fixture_profile_share=(max(exposure.values(), default=0) / len(published)
                                            if published else None),
            selected_fixture_profile_counts={identifier: exposure[identifier]
                                              for identifier in diagnostics["selected_fixture_ids"]},
            duplicate_exposure_profile_ids=next(
                (profiles for profiles in signatures.values() if item["profile_id"] in profiles
                 and len(profiles) > 1), []),
        )
    return output
