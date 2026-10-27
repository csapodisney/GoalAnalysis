"""Bounded SHADOW winner-count scenarios from existing numeric predictions.

One coherent outcome is shared by every ticket on a fixture. Market-pattern
coupling is an explicit IPF scenario, not a recovered model score grid. Across
fixtures independence is assumed, never independence of overlapping tickets.
Missing markets/models remain outside the estimated subset, with count bounds.
"""

from __future__ import annotations

import itertools
import math
import random
from collections import Counter, defaultdict
from datetime import datetime

from goal_analysis.agents import canonical_sha256

VERSION = "arthur-portfolio-distribution-shadow-v1"
MODELS = ("gauss", "kepler", "laplace")
FAMILIES = {"h2h": ("home", "draw", "away"), "totals_2_5": ("over", "under"), "btts": ("yes", "no")}
KEYS = {"h2h": ("home", "draw", "away"), "totals_2_5": ("over25", "under25"), "btts": ("btts_yes", "btts_no")}
SETTLEMENT = "REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES"
MAX_CARDS, MAX_FIXTURES, MAX_EXACT_STATES, TRIALS = 30, 180, 4096, 16384


def _probability(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def _partitions(prediction, families):
    raw = prediction.get("probabilities", {})
    result = {}
    for family in families:
        values = [raw.get(key) for key in KEYS[family]]
        if family == "btts" and values[1] is None and _probability(values[0]):
            values[1] = 1 - values[0]
        if not all(_probability(v) for v in values) or not math.isclose(sum(values), 1, abs_tol=1e-6):
            raise ValueError("MISSING_OR_INVALID_MARKET_PROBABILITIES")
        # Existing model marginal totals accept rounding at1e-6. Normalize only
        # this scenario's copied categorical partition; never edit the model.
        total = sum(values)
        result[family] = dict(zip(FAMILIES[family], (v / total for v in values), strict=True))
    return result


def joint_patterns(prediction, families):
    """IPF over feasible football market patterns with a uniform pattern prior.

    All supported full-time binary/result patterns occur on the0..5 score grid;
    scores themselves are not assigned probabilities or claimed forecasts.
    No3.5/H1/handicap/corner probability is inferred from this enumeration.
    """
    families = tuple(sorted(set(families)))
    margins = _partitions(prediction, families)
    patterns = sorted({tuple({"h2h": "home" if h > a else "draw" if h == a else "away",
        "totals_2_5": "over" if h + a > 2 else "under", "btts": "yes" if h and a else "no"}[f]
        for f in families) for h in range(6) for a in range(6)})
    patterns = [p for p in patterns if all(margins[f][p[i]] > 0 for i, f in enumerate(families))]
    if not patterns:
        raise ValueError("INCOMPATIBLE_MARKET_MARGINALS")
    masses = [1 / len(patterns)] * len(patterns)
    for iteration in range(1000):
        for column, family in enumerate(families):
            current = {value: math.fsum(m for p, m in zip(patterns, masses, strict=True) if p[column] == value)
                       for value in FAMILIES[family]}
            if any(target > 0 and current[value] <= 0 for value, target in margins[family].items()):
                raise ValueError("INCOMPATIBLE_MARKET_MARGINALS")
            masses = [mass * margins[family][pattern[column]] / current[pattern[column]]
                      for pattern, mass in zip(patterns, masses, strict=True)]
        residual = max(abs(math.fsum(m for p, m in zip(patterns, masses, strict=True) if p[i] == value) - target)
                       for i, family in enumerate(families) for value, target in margins[family].items())
        if residual <= 1e-10:
            break
    else:
        raise ValueError("JOINT_SCENARIO_DID_NOT_CONVERGE")
    return {"families": list(families), "patterns": [list(p) for p in patterns], "masses": masses,
            "marginals": margins, "iterations": iteration + 1, "max_marginal_residual": residual,
            "coupling": "IPF_UNIFORM_FEASIBLE_MARKET_PATTERN_PRIOR_NOT_ORIGINAL_MODEL_SCORE_GRID"}


def _count_distribution(fixtures, count, seed):
    """Exact mask-DP where bounded; deterministic shared-fixture simulation else."""
    all_alive = (1 << count) - 1
    states = {all_alive: 1.0}
    exact = True
    for fixture in fixtures:
        updated = defaultdict(float)
        for alive, mass in states.items():
            for mask, probability in fixture:
                updated[alive & mask] += mass * probability
                if len(updated) > MAX_EXACT_STATES:
                    exact = False
                    break
            if not exact:
                break
        if not exact:
            break
        states = updated
    if exact:
        bins = [0.0] * (count + 1)
        for alive, mass in states.items():
            bins[alive.bit_count()] += mass
        return {"method": "EXACT_SHARED_FIXTURE_MASK_DP", "probability_mass": bins,
                "trials": None, "seed": None, "sampling_standard_error_upper_bound": 0.0}
    rng = random.Random(seed)
    cumulative = []
    for fixture in fixtures:
        running, choices = 0.0, []
        for mask, probability in fixture:
            running += probability
            choices.append((running, mask))
        choices[-1] = (1.0, choices[-1][1])
        cumulative.append(choices)
    bins = Counter()
    for _ in range(TRIALS):
        alive = all_alive
        for fixture in cumulative:
            draw = rng.random()
            for cutoff, mask in fixture:
                if draw < cutoff:
                    alive &= mask
                    break
        bins[alive.bit_count()] += 1
    return {"method": "SEEDED_SHARED_FIXTURE_MONTE_CARLO", "probability_mass": [bins[i] / TRIALS for i in range(count + 1)],
            "trials": TRIALS, "seed": seed, "sampling_standard_error_upper_bound": .5 / math.sqrt(TRIALS)}


def _time(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp if stamp.tzinfo is not None and stamp.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def _scenario(cards, model, as_of):
    predictions = model.get("predictions", [])
    indices = defaultdict(list)
    for row in predictions:
        indices[row.get("fixture_id")].append(row)
    covered, missing, targets = [], [], defaultdict(set)
    for card in cards:
        reason = None if model.get("status") == "READY" else "MODEL_NOT_READY"
        seen = set()
        for leg in card["legs"]:
            if reason:
                break
            fixture, family = leg.get("fixture_id"), leg.get("market_key")
            if (not isinstance(fixture, str) or fixture in seen or family not in FAMILIES
                    or leg.get("selection_key") not in FAMILIES.get(family, ())
                    or leg.get("period") != "FULL_TIME" or leg.get("settlement") != SETTLEMENT):
                reason = "UNSUPPORTED_PRODUCT_OR_REPEATED_FIXTURE"
                break
            seen.add(fixture)
            if len(indices.get(fixture, [])) != 1:
                reason = "MISSING_OR_AMBIGUOUS_FIXTURE_PREDICTION"
                break
            cutoff = _time(indices[fixture][0].get("model_details", {}).get("cutoff"))
            if as_of is None or cutoff is None or cutoff > as_of:
                reason = "MISSING_OR_FUTURE_MODEL_CUTOFF"
                break
            try:
                _partitions(indices[fixture][0], [family])
            except ValueError as error:
                reason = str(error)
                break
        if reason:
            missing.append({"card_sha256": card["sha256"], "profile_id": card["profile_id"], "reason": reason})
        else:
            covered.append(card)
            for leg in card["legs"]:
                targets[leg["fixture_id"]].add(leg["market_key"])
    if len(targets) > MAX_FIXTURES:
        return {"status": "UNAVAILABLE", "reason": "FIXTURE_BUDGET_EXCEEDED"}
    joint = {}
    try:
        for fixture, families in sorted(targets.items()):
            joint[fixture] = joint_patterns(indices[fixture][0], families)
    except ValueError as error:
        return {"status": "UNAVAILABLE", "reason": str(error), "missing_cards": missing}
    all_alive = (1 << len(covered)) - 1
    mask_rows, marginal_hits = [], [1.0] * len(covered)
    for fixture, pattern in joint.items():
        mask_probabilities = defaultdict(float)
        for outcome, mass in zip(pattern["patterns"], pattern["masses"], strict=True):
            realized = dict(zip(pattern["families"], outcome, strict=True))
            mask = all_alive
            for index, card in enumerate(covered):
                for leg in card["legs"]:
                    if leg["fixture_id"] == fixture and realized[leg["market_key"]] != leg["selection_key"]:
                        mask &= ~(1 << index)
            mask_probabilities[mask] += mass
        row = sorted(mask_probabilities.items())
        for index in range(len(covered)):
            marginal_hits[index] *= math.fsum(m for mask, m in row if mask & (1 << index))
        mask_rows.append(row)
    expectation = math.fsum(marginal_hits) if covered else None
    # Analytic second moment uses the same fixture outcomes; overlapping tickets
    # are never convolved as independent Bernoulli trials.
    pair_hits = []
    for first, second in itertools.combinations(range(len(covered)), 2):
        both = (1 << first) | (1 << second)
        pair_hits.append(math.prod(math.fsum(mass for mask, mass in row if mask & both == both) for row in mask_rows))
    variance = max(0.0, expectation + 2 * math.fsum(pair_hits) - expectation ** 2) if covered else None
    seed = int(canonical_sha256(joint)[:16], 16)
    distribution = _count_distribution(mask_rows, len(covered), seed) if covered else None
    if distribution:
        distribution.update(probability_at_least_three=math.fsum(distribution["probability_mass"][3:]),
                            probability_at_least_four=math.fsum(distribution["probability_mass"][4:]))
    return {"status": "COMPLETE_SCENARIO" if not missing and covered else "PARTIAL_SCENARIO" if covered else "NOT_ESTIMABLE",
        "covered_card_count": len(covered), "unestimated_card_count": len(missing), "missing_cards": missing,
        "covered_cards": [{"card_sha256": card["sha256"], "profile_id": card["profile_id"], "scenario_win_probability": p}
                          for card, p in zip(covered, marginal_hits, strict=True)],
        "covered_expected_winners": expectation, "covered_winner_count_variance": variance,
        "whole_portfolio_expected_winners": expectation if not missing and covered else None,
        "whole_portfolio_expectation_bounds": {"lower": expectation or 0.0, "upper": (expectation or 0.0) + len(missing)},
        "whole_probability_at_least_three_bounds": ({
            "lower": math.fsum(distribution["probability_mass"][3:]),
            "upper": math.fsum(distribution["probability_mass"][max(0, 3 - len(missing)):])} if distribution else None),
        "covered_winner_count_distribution": distribution,
        "whole_portfolio_winner_count_distribution": distribution if not missing and covered else None,
        "fixture_scenarios": joint, "numeric_predictions_sha256": canonical_sha256(predictions)}


def build_portfolio_distribution(report, artifacts):
    """Separate main tickets and all READY recommendations; never count both."""
    packages = {}
    for group in ("tickets", "recommendations"):
        cards, seen, duplicates = [], set(), 0
        for card in report.get(group, []) or []:
            if card.get("status") != "READY" or not card.get("legs"):
                continue
            # Deduplicate only an exact repeated record. Separate ticket IDs,
            # including the same persona's, count as separate perfectly
            # dependent wagers when their selections coincide.
            identity = canonical_sha256(card)
            if identity in seen:
                duplicates += 1
                continue
            seen.add(identity)
            cards.append({"profile_id": card.get("profile_id"), "sha256": canonical_sha256(card), "legs": card["legs"]})
        cards.sort(key=lambda c: c["sha256"])
        if len(cards) > MAX_CARDS:
            packages[group] = {"status": "UNAVAILABLE", "reason": "READY_CARD_BUDGET_EXCEEDED", "ready_card_count": len(cards)}
            continue
        models = artifacts.get("numeric_models", {})
        packages[group] = {"ready_card_count": len(cards), "duplicate_same_profile_records_not_counted_twice": duplicates,
            "status": "NO_READY_CARDS" if not cards else "SCENARIOS_ONLY",
            "models": {name: _scenario(cards, models.get(name) or {},
                _time(report.get("finished_at", report.get("observed_at")))) for name in MODELS} if cards else {}}
    result = {"version": VERSION, "mode": "SHADOW", "decision_effect": "NONE", "run_id": report.get("run_id"),
        "date": report.get("date"), "packages": packages,
        "assumptions": ["UNCALIBRATED_MODEL_MARGINALS_NOT_HEURISTIC_SCORES_OR_INVERSE_ODDS",
            "INDEPENDENCE_ACROSS_DISTINCT_FIXTURES_CONDITIONAL_ON_A_FIXED_MODEL_SCENARIO",
            "ONE_SHARED_COHERENT_MARKET_PATTERN_PER_FIXTURE_ACROSS_ALL_TICKETS",
            "IPF_COUPLING_IS_AN_ASSUMPTION_NOT_THE_ORIGINAL_GAUSS_OR_LAPLACE_SCORE_GRID",
            "MODEL_SCENARIOS_SEPARATE_NO_ARBITRARY_ENSEMBLE_OR_INDEPENDENT_MODEL_VOTES",
            "TRAINING_INPUT_POINT_IN_TIME_AVAILABILITY_AND_MODEL_CALIBRATION_NOT_CERTIFIED",
            "MISSING_MARKETS_REMAIN_UNESTIMATED_NOT_ZERO_PROBABILITY",
            "COUNTS_ARE_PREDICTIVE_SCENARIOS_NOT_SETTLED_KPI_OR_RETURN_ESTIMATES"],
        "budgets": {"max_cards_per_package": MAX_CARDS, "max_fixtures": MAX_FIXTURES,
                    "max_exact_states": MAX_EXACT_STATES, "simulation_trials": TRIALS},
        "external_requests": 0}
    try:
        result["selected_leg_partial_scenarios"] = selected_leg_partial_scenarios(report, artifacts)
    except (AttributeError, KeyError, TypeError, ValueError):
        # An optional research component cannot erase the original packages.
        result["selected_leg_partial_scenarios"] = {"version": SELECTED_LEG_VERSION,
            "mode": "SHADOW", "decision_effect": "NONE", "status": "UNAVAILABLE",
            "reason": "SELECTED_LEG_RESEARCH_COMPONENT_INVALID_INPUT", "external_requests": 0}
    if "candidate_input" in artifacts or "fixtures" in artifacts:
        try:
            result["non_ready_selected_leg_research"] = non_ready_selected_leg_research(report, artifacts)
        except (AttributeError, KeyError, TypeError, ValueError):
            result["non_ready_selected_leg_research"] = {"version": NON_READY_VERSION,
                "mode": "SHADOW", "decision_effect": "NONE", "status": "UNAVAILABLE",
                "reason": "NON_READY_RESEARCH_SOURCE_INPUT_INVALID", "external_requests": 0}
    result["sha256"] = canonical_sha256(result)
    return result


SELECTED_LEG_VERSION = "selected-leg-partial-scenario-shadow-v1"
SCENARIO_BOUNDS_LABEL = "SCENARIO_MODEL_ESTIMATED_BOUNDS_NOT_EMPIRICAL_TRUE_PROBABILITY_OR_EDGE"


def _selected_leg_binding(leg, repeated, indices, model, as_of):
    """Return an explicit reason, never an invented market probability."""
    if model.get("status") != "READY":
        return "MODEL_NOT_READY"
    fixture, family = leg.get("fixture_id"), leg.get("market_key")
    if (not isinstance(fixture, str) or fixture in repeated or family not in FAMILIES
            or leg.get("selection_key") not in FAMILIES.get(family, ())
            or leg.get("period") != "FULL_TIME" or leg.get("settlement") != SETTLEMENT):
        return "UNSUPPORTED_PRODUCT_OR_REPEATED_FIXTURE"
    if len(indices.get(fixture, [])) != 1:
        return "MISSING_OR_AMBIGUOUS_FIXTURE_PREDICTION"
    prediction = indices[fixture][0]
    cutoff = _time(prediction.get("model_details", {}).get("cutoff"))
    if as_of is None or cutoff is None or cutoff > as_of:
        return "MISSING_OR_FUTURE_MODEL_CUTOFF"
    try:
        _partitions(prediction, [family])
    except ValueError as error:
        return str(error)
    return "VALID_MODEL_MARKET_MARGINAL"


def _selected_leg_scenario(cards, model, as_of):
    """Preserve known legs as research events without estimating unknown legs.

    The existing shared-fixture kernel supplies all event distributions. Its
    marginal/IPF assumptions remain explicit, including the cross-fixture
    conditional-independence assumption. Bounds apply only inside that model
    scenario, not to empirical true probabilities or betting edge.
    """
    predictions = model.get("predictions", [])
    indices = defaultdict(list)
    for prediction in predictions:
        indices[prediction.get("fixture_id")].append(prediction)
    references, events = [], []
    for card in cards:
        fixture_counts = Counter(leg.get("fixture_id") for leg in card["legs"]
                                 if isinstance(leg.get("fixture_id"), str))
        repeated = {fixture for fixture, count in fixture_counts.items() if count > 1}
        bindings, known = [], []
        for position, leg in enumerate(card["legs"]):
            reason = _selected_leg_binding(leg, repeated, indices, model, as_of)
            bindings.append({"source_leg_index": position, "source_leg_sha256": canonical_sha256(leg),
                             "leg": dict(leg), "binding": reason})
            if reason == "VALID_MODEL_MARKET_MARGINAL":
                known.append(leg)
        reference = {"source_card_sha256": card["sha256"], "profile_id": card["profile_id"],
                     "source_card_identity": card["source_card_identity"],
                     "source_leg_count": len(card["legs"]), "known_leg_count": len(known),
                     "unknown_leg_count": len(card["legs"]) - len(known), "leg_bindings": bindings,
                     "research_event_is_new_ticket": False,
                     "known_leg_event_probability": None, "scenario_model_full_card_probability": None,
                     "scenario_model_full_card_probability_bounds": None, "bounds_interpretation": SCENARIO_BOUNDS_LABEL}
        if not known:
            reference["status"] = "NO_COVERED_LEGS_NOT_ESTIMABLE"
            references.append(reference)
            continue
        event = {"profile_id": card["profile_id"], "legs": known,
                 "sha256": canonical_sha256({"component": SELECTED_LEG_VERSION,
                     "source_card_sha256": card["sha256"], "known_legs": known})}
        individual = _scenario([event], model, as_of)
        # A per-card event has no repeated fixture and only independently
        # validated supported products. Keep the failure explicit regardless.
        covered = individual.get("covered_cards", [])
        if individual.get("status") != "COMPLETE_SCENARIO" or len(covered) != 1:
            reference.update(status="KNOWN_LEG_EVENT_UNAVAILABLE", reason=individual.get("reason", individual["status"]))
            references.append(reference)
            continue
        probability = covered[0]["scenario_win_probability"]
        complete = len(known) == len(card["legs"])
        reference.update(status="FULLY_COVERED_RESEARCH_EVENT" if complete else "PARTIALLY_COVERED_RESEARCH_EVENT",
            research_event_sha256=event["sha256"], known_leg_event_probability=probability,
            scenario_model_full_card_probability=probability if complete else None,
            scenario_model_full_card_probability_bounds={"lower": probability if complete else 0.0, "upper": probability},
            fixture_scenarios=individual["fixture_scenarios"])
        references.append(reference)
        events.append(event)
    joint = _scenario(events, model, as_of) if events else None
    # Names deliberately describe known-leg research events, never full-ticket
    # winners. Distinct source tickets/stakes stay separate perfectly dependent
    # events if their known selections coincide.
    joint_result = None if joint is None else {
        "status": joint["status"], "reason": joint.get("reason"),
        "known_leg_event_count": len(events),
        "expected_satisfied_known_leg_events": joint.get("covered_expected_winners"),
        "satisfied_known_leg_event_count_variance": joint.get("covered_winner_count_variance"),
        "satisfied_known_leg_event_count_distribution": joint.get("covered_winner_count_distribution"),
        "fixture_scenarios": joint.get("fixture_scenarios", {}),
        "interpretation": "RESEARCH_EVENTS_NOT_WAGERS_OR_ESTIMATES_FOR_UNKNOWN_FULL_CARDS"}
    return {"status": "RESEARCH_SCENARIOS_ONLY" if events else "NOT_ESTIMABLE",
            "source_card_count": len(cards), "cards": references, "joint_known_leg_events": joint_result,
            "numeric_predictions_sha256": canonical_sha256(predictions)}


def selected_leg_partial_scenarios(report, artifacts):
    """Additive bounded coverage for the same separate READY source cohorts."""
    packages = {}
    for group in ("tickets", "recommendations"):
        cards, seen, duplicates = [], set(), 0
        for card in report.get(group, []) or []:
            if card.get("status") != "READY" or not card.get("legs"):
                continue
            identity = canonical_sha256(card)
            if identity in seen:
                duplicates += 1
                continue
            seen.add(identity)
            source_identity = {key: card[key] for key in (
                "ticket_id", "portfolio_ticket_id", "profile_id", "status", "publication_scope", "stake", "unit_stake",
                "stake_units", "wager_id") if key in card}
            cards.append({"profile_id": card.get("profile_id"), "sha256": identity, "legs": card["legs"],
                          "source_card_identity": source_identity})
        cards.sort(key=lambda item: item["sha256"])
        reason = "READY_CARD_BUDGET_EXCEEDED" if len(cards) > MAX_CARDS else None
        references = sum(len(card["legs"]) for card in cards)
        # Explicit component bound, in leg references. No first-N truncation.
        if reason is None and references > MAX_CARDS * MAX_FIXTURES:
            reason = "SELECTED_LEG_REFERENCE_BUDGET_EXCEEDED"
        fixtures = {leg.get("fixture_id") for card in cards for leg in card["legs"]
                    if isinstance(leg.get("fixture_id"), str)}
        if reason is None and len(fixtures) > MAX_FIXTURES:
            reason = "FIXTURE_BUDGET_EXCEEDED"
        packages[group] = {"status": "UNAVAILABLE" if reason else "NO_READY_CARDS" if not cards else "RESEARCH_SCENARIOS_ONLY",
                           "reason": reason, "source_ready_card_count": len(cards),
                           "exact_duplicate_records_not_counted_twice": duplicates,
                           "source_leg_reference_count": references,
                           "models": {name: _selected_leg_scenario(cards,
                               artifacts.get("numeric_models", {}).get(name) or {},
                               _time(report.get("finished_at", report.get("observed_at"))))
                               for name in MODELS} if cards and reason is None else {}}
    return {"version": SELECTED_LEG_VERSION, "mode": "SHADOW", "decision_effect": "NONE",
            "scope": "EXISTING_SEPARATE_READY_COHORTS_NO_STATUS_PROMOTION_OR_NEW_TICKET",
            "bounds_interpretation": SCENARIO_BOUNDS_LABEL, "packages": packages,
            "assumptions": ["UNKNOWN_LEGS_UNESTIMATED_NEVER_ZERO_HALF_OR_INVERSE_ODDS",
                            "KNOWN_LEG_EVENT_IS_NOT_A_REPLACEMENT_TICKET_OR_NEW_WAGER",
                            "SAME_MODEL_MARGINALS_AND_SHARED_FIXTURE_COUPLING_AS_ORIGINAL_KERNEL",
                            "SYNTHETIC_TEST_TARGETS_NOT_ACTUAL_OLD_DAILY_OR_POINT_IN_TIME_FORECASTS"],
            "budgets": {"max_cards_per_package": MAX_CARDS, "max_fixtures": MAX_FIXTURES,
                        "max_leg_references_per_package": MAX_CARDS * MAX_FIXTURES}, "external_requests": 0}


# Additive non-READY research; existing READY entry points/kernels stay intact.
NON_READY_VERSION = "non-ready-selected-leg-research-v1"
NON_READY_STATUSES = {"DRAFT", "REVIEWED", "MODEL_READY", "ODDS_PENDING", "TARGET_PROPOSAL"}
NON_READY_MAX_SOURCE_CANDIDATES = 10000
NON_READY_PRODUCT = ("fixture_id", "market_key", "selection_key", "period", "settlement")


def _research_source_binding(leg, candidates, fixtures, as_of, profile):
    """Exact retained source correspondence, never current offer/PIT proof."""
    identifier = leg.get("source_candidate_id") or leg.get("quote_candidate_id") or leg.get("candidate_id")
    if not isinstance(identifier, str) or not isinstance(leg.get("fixture_id"), str):
        return {"status": "UNKNOWN", "reason": "EXACT_SOURCE_IDENTIFIERS_REQUIRED"}
    matching, targets = candidates.get(identifier, []), fixtures.get(leg["fixture_id"], [])
    if len(matching) != 1:
        return {"status": "UNKNOWN", "reason": "MISSING_OR_AMBIGUOUS_SOURCE_CANDIDATE"}
    source = matching[0]
    original_alias, quote_alias = leg.get("source_candidate_id"), leg.get("quote_candidate_id")
    source_id = leg.get("candidate_id")
    if original_alias is not None and quote_alias is not None and original_alias != quote_alias:
        return {"status": "UNKNOWN", "reason": "CONFLICTING_SUPPLIED_CANDIDATE_IDENTIFIERS"}
    if source_id is not None and source_id != identifier:
        virtual = f"{profile}-" + canonical_sha256([profile, leg["fixture_id"], leg.get("market_key"),
            leg.get("selection_key"), source.get("bookmaker")])[:24]
        if not (original_alias == quote_alias == identifier and source_id == virtual and source_id not in candidates):
            return {"status": "UNKNOWN", "reason": "CONFLICTING_SUPPLIED_CANDIDATE_IDENTIFIERS"}
    if any(key not in leg or key not in source or leg[key] != source[key] for key in NON_READY_PRODUCT):
        return {"status": "UNKNOWN", "reason": "SOURCE_PRODUCT_IDENTITY_MISMATCH"}
    family = leg.get("market_key")
    for value in (leg.get("line"), source.get("line")):
        if value is not None:
            try:
                valid = family == "totals_2_5" and type(value) in (str, int, float) and float(value) == 2.5
            except (ValueError, OverflowError):
                valid = False
            if not valid:
                return {"status": "UNKNOWN", "reason": "EXPLICIT_MARKET_LINE_MISMATCH"}
    if len(targets) != 1 or not isinstance(targets[0], dict):
        return {"status": "UNKNOWN", "reason": "MISSING_OR_AMBIGUOUS_SOURCE_FIXTURE"}
    target = targets[0]
    kickoff, source_kickoff, target_kickoff = (_time(row.get("kickoff")) for row in (leg, source, target))
    if (kickoff is None or kickoff != source_kickoff or kickoff != target_kickoff
            or any(leg.get(key) != target.get(key) for key in (
                "provider_fixture_id", "provider_league_id", "provider_season_id",
                "provider_home_team_id", "provider_away_team_id") if key in leg or key in target)):
        return {"status": "UNKNOWN", "reason": "SOURCE_FIXTURE_IDENTITY_OR_KICKOFF_MISMATCH"}
    if as_of is None:
        return {"status": "UNKNOWN", "reason": "AWARE_REPORT_CUTOFF_REQUIRED"}
    for key in ("decimal_price", "bookmaker", "quote_provider", "quote_source_id", "quote_source_sha256"):
        # Missing odds do not veto an otherwise valid numeric research event.
        if leg.get(key) is not None and leg[key] != source.get(key):
            return {"status": "UNKNOWN", "reason": "EXPLICIT_QUOTE_CORRESPONDENCE_MISMATCH"}
    for key in ("quote_observed_at", "quoted_at"):
        if leg.get(key) is not None:
            observed, original = _time(leg[key]), _time(source.get(key))
            if observed is None or observed != original or observed > as_of:
                return {"status": "UNKNOWN", "reason": "EXPLICIT_QUOTE_CLOCK_MISMATCH"}
    return {"status": "BOUND", "reason": "EXACT_RETAINED_PRODUCT_FIXTURE_CORRESPONDENCE",
            "candidate_id": identifier, "candidate_sha256": canonical_sha256(source),
            "fixture_sha256": canonical_sha256(target),
            "quote_price_known": leg.get("decimal_price") is not None,
            "provider_quote_clock_known": _time(leg.get("quoted_at")) is not None,
            "not_current_quote_or_PIT_authentication": True}


def _non_ready_model_research(cards, sources, model, as_of):
    indices = defaultdict(list)
    for prediction in model.get("predictions", []):
        indices[prediction.get("fixture_id")].append(prediction)
    references, projected = [], []
    for card, source_bindings in zip(cards, sources, strict=True):
        fixtures = Counter(leg.get("fixture_id") for leg in card["legs"] if isinstance(leg.get("fixture_id"), str))
        repeated = {key for key, count in fixtures.items() if count > 1}
        bindings, known = [], []
        for position, (leg, source) in enumerate(zip(card["legs"], source_bindings, strict=True)):
            try:
                reason = source["reason"] if source["status"] != "BOUND" else _selected_leg_binding(
                    leg, repeated, indices, model, as_of)
            except OverflowError:
                reason = "INVALID_LOCAL_MODEL_PROBABILITY"
            if reason == "VALID_MODEL_MARKET_MARGINAL":
                prediction = indices[leg["fixture_id"]][0]
                cutoff = _time(prediction.get("model_details", {}).get("cutoff"))
                version = prediction.get("model_details", {}).get("version")
                if (cutoff is None or cutoff >= _time(leg["kickoff"])
                        or not isinstance(version, str) or not version):
                    reason = "PREMATCH_MODEL_VERSION_AND_CUTOFF_REQUIRED"
            bindings.append({"source_leg_index": position, "source_leg_sha256": canonical_sha256(leg),
                             "leg": dict(leg), "source_binding": source, "binding": reason})
            if reason == "VALID_MODEL_MARKET_MARGINAL":
                known.append(leg)
        reference = {"source_card_sha256": card["sha256"], "source_card_identity": card["source_card_identity"],
            "source_card_index": card["source_card_index"], "profile_id": card["profile_id"],
            "source_leg_count": len(card["legs"]), "known_leg_count": len(known),
            "unknown_leg_count": len(card["legs"]) - len(known), "leg_bindings": bindings,
            "status": "NO_COVERED_LEGS_NOT_ESTIMABLE", "known_leg_event_probability": None,
            "scenario_model_full_card_probability": None, "scenario_model_full_card_probability_bounds": None,
            "bounds_interpretation": SCENARIO_BOUNDS_LABEL, "research_event_is_new_ticket": False,
            "source_status_promoted": False}
        references.append(reference)
        if known:
            projected.append({"profile_id": card["profile_id"], "sha256": card["sha256"], "legs": known,
                              "source_card_identity": card["source_card_identity"]})
    # Reuse the unchanged coherent kernel through the already accepted known-leg
    # helper. Projected subsets never escape as replacements for source cards.
    measured = _selected_leg_scenario(projected, model, as_of) if projected else None
    by_hash = {row["source_card_sha256"]: row for row in measured["cards"]} if measured else {}
    for reference in references:
        result = by_hash.get(reference["source_card_sha256"])
        if result is None:
            continue
        probability = result["known_leg_event_probability"]
        if probability is None:
            reference.update(status="KNOWN_LEG_EVENT_UNAVAILABLE", reason=result.get("reason", result["status"]))
            continue
        complete = reference["known_leg_count"] == reference["source_leg_count"]
        reference.update(status="FULLY_COVERED_RESEARCH_EVENT" if complete else "PARTIALLY_COVERED_RESEARCH_EVENT",
            known_leg_event_probability=probability,
            scenario_model_full_card_probability=probability if complete else None,
            scenario_model_full_card_probability_bounds={"lower": probability if complete else 0.0, "upper": probability},
            fixture_scenarios=result.get("fixture_scenarios", {}))
    return {"status": "RESEARCH_SCENARIOS_ONLY" if projected else "NOT_ESTIMABLE", "cards": references,
            "source_card_count": len(cards), "source_leg_count": sum(row["source_leg_count"] for row in references),
            "known_leg_count": sum(row["known_leg_count"] for row in references),
            "unknown_leg_count": sum(row["unknown_leg_count"] for row in references),
            "joint_known_leg_events": measured["joint_known_leg_events"] if measured else None,
            "numeric_predictions_sha256": canonical_sha256(model.get("predictions", []))}


def non_ready_selected_leg_research(report, artifacts):
    """Canonical non-READY recommendation research; never publish or promote."""
    rows = report.get("recommendations", []) or []
    candidate_rows = artifacts.get("candidate_input", {}).get("candidates", [])
    fixture_source = artifacts.get("fixtures", [])
    fixture_rows = fixture_source.get("records") if isinstance(fixture_source, dict) else fixture_source
    as_of = _time(report.get("finished_at", report.get("observed_at")))
    observed = _time(fixture_source.get("observed_at")) if isinstance(fixture_source, dict) else None
    fixture_clock_valid = not isinstance(fixture_source, dict) or (observed is not None and as_of is not None and observed <= as_of)
    if not isinstance(rows, list) or not isinstance(candidate_rows, list) or not isinstance(fixture_rows, list):
        raise TypeError("NON_READY_RESEARCH_LIST_INPUTS_REQUIRED")
    result = {"version": NON_READY_VERSION, "mode": "SHADOW", "decision_effect": "NONE",
        "scope": "CANONICAL_RECOMMENDATIONS_NON_READY_RESEARCH_NOT_WAGERS", "status": "NOT_ESTIMABLE",
        "source_stage": "recommendations", "profile_proposals_are_not_additional_events": True,
        "supported_source_statuses": sorted(NON_READY_STATUSES), "card_accounting": [], "models": {},
        "bounds_interpretation": SCENARIO_BOUNDS_LABEL, "external_requests": 0,
        "source_authentication": "RETAINED_REPORT_INPUT_CORRESPONDENCE_NOT_PIT_OR_CALIBRATION",
        "fixture_source_kind": "NORMAL_OBSERVED_RECORDS_ENVELOPE" if isinstance(fixture_source, dict) else "DIRECT_LIST_PURE_CONTENT_NOT_PHYSICAL_AUTHENTICATION",
        "fixture_source_sha256": canonical_sha256(fixture_source),
        "fixture_source_observed_at": fixture_source.get("observed_at") if isinstance(fixture_source, dict) else None,
        "budgets": {"max_cards": MAX_CARDS, "max_fixtures": MAX_FIXTURES,
                    "max_leg_references": MAX_CARDS * MAX_FIXTURES,
                    "max_source_candidates": NON_READY_MAX_SOURCE_CANDIDATES}}
    if (len(rows) > MAX_CARDS or len(candidate_rows) > NON_READY_MAX_SOURCE_CANDIDATES
            or len(fixture_rows) > MAX_FIXTURES):
        result["accounting"] = {"input_card_count": len(rows), "candidate_source_count": len(candidate_rows),
            "fixture_source_count": len(fixture_rows), "unexamined_card_count": len(rows),
            "budget_failure_means_unexamined_not_empty_or_zero": True}
        if len(rows) <= MAX_CARDS:
            result["card_accounting"] = [{"source_card_index": index,
                "source_card_sha256": canonical_sha256(card), "status": "UNEXAMINED",
                "reason": "COMPLETE_SOURCE_BUDGET_EXCEEDED"} for index, card in enumerate(rows)]
        result.update(status="UNAVAILABLE", reason="NON_READY_RESEARCH_COMPLETE_INPUT_BUDGET_EXCEEDED")
        return result
    if not fixture_clock_valid:
        result["accounting"] = {"input_card_count": len(rows), "unexamined_card_count": len(rows)}
        result["card_accounting"] = [{"source_card_index": index, "source_card_sha256": canonical_sha256(card),
            "status": "UNEXAMINED", "reason": "MISSING_NAIVE_MALFORMED_OR_FUTURE_FIXTURE_SOURCE_CLOCK"}
            for index, card in enumerate(rows)]
        result.update(status="UNAVAILABLE", reason="MISSING_NAIVE_MALFORMED_OR_FUTURE_FIXTURE_SOURCE_CLOCK")
        return result
    candidates, fixtures = defaultdict(list), defaultdict(list)
    for row in candidate_rows:
        if isinstance(row, dict) and isinstance(row.get("candidate_id"), str):
            candidates[row["candidate_id"]].append(row)
    for row in fixture_rows:
        if isinstance(row, dict) and isinstance(row.get("fixture_id"), str):
            fixtures[row["fixture_id"]].append(row)
    identity_records = defaultdict(set)
    for card in rows:
        if isinstance(card, dict):
            for key in ("recommendation_id", "ticket_id"):
                value = card.get(key)
                if isinstance(value, str) and value:
                    identity_records[(key, value)].add(canonical_sha256(card))
    conflicts = {key for key, versions in identity_records.items() if len(versions) > 1}
    cards, seen = [], set()
    for index, card in enumerate(rows):
        account = {"source_card_index": index, "source_card_sha256": canonical_sha256(card),
                   "status": "EXCLUDED", "reason": "MALFORMED_SOURCE_CARD"}
        result["card_accounting"].append(account)
        if not isinstance(card, dict):
            continue
        account["source_status"] = card.get("status")
        conflict_fields = [key for key in ("recommendation_id", "ticket_id")
            if isinstance(card.get(key), str) and (key, card[key]) in conflicts]
        if conflict_fields:
            account.update(reason="CONFLICTING_SOURCE_CARD_IDENTITY", conflicting_identity_fields=conflict_fields)
            continue
        if not isinstance(card.get("status"), str) or card["status"] not in NON_READY_STATUSES:
            account["reason"] = "READY_REMAINS_ORIGINAL_PATH" if card.get("status") == "READY" else "UNSUPPORTED_SOURCE_STATUS"
            continue
        legs = card.get("legs", [])
        if not isinstance(legs, list) or any(not isinstance(leg, dict) for leg in legs):
            continue
        if not legs:
            account["reason"] = "EMPTY_SOURCE_CARD"
            continue
        identity = account["source_card_sha256"]
        if identity in seen:
            account.update(status="DUPLICATE", reason="EXACT_SAME_SOURCE_RECORD")
            continue
        seen.add(identity)
        account.update(status="INCLUDED", reason="CANONICAL_NON_READY_RESEARCH_SOURCE")
        cards.append({"sha256": identity, "legs": legs, "profile_id": card.get("profile_id"),
            "source_card_index": index, "source_card_identity": {key: card[key] for key in (
                "recommendation_id", "ticket_id", "portfolio_ticket_id", "profile_id", "status", "stake", "stake_units",
                "unit_stake", "wager_id") if key in card}})
    reference_count = sum(len(card["legs"]) for card in cards)
    fixture_ids = {leg.get("fixture_id") for card in cards for leg in card["legs"] if isinstance(leg.get("fixture_id"), str)}
    if reference_count > MAX_CARDS * MAX_FIXTURES or len(fixture_ids) > MAX_FIXTURES:
        result.update(status="UNAVAILABLE", reason="NON_READY_RESEARCH_COMPLETE_REFERENCE_BUDGET_EXCEEDED")
        return result
    result["accounting"] = {"input_card_count": len(rows), "included_card_count": len(cards),
        "partitions": dict(Counter(row["status"] for row in result["card_accounting"])),
        "source_leg_references": reference_count, "source_fixture_count": len(fixture_ids),
        "source_keyset_sha256": canonical_sha256(result["card_accounting"]),
        "candidate_source_count": len(candidate_rows),
        "invalid_candidate_source_indices": [index for index, row in enumerate(candidate_rows)
            if not isinstance(row, dict) or not isinstance(row.get("candidate_id"), str)],
        "fixture_source_count": len(fixture_rows),
        "invalid_fixture_source_indices": [index for index, row in enumerate(fixture_rows)
            if not isinstance(row, dict) or not isinstance(row.get("fixture_id"), str)],
        "candidate_input_keyset_sha256": canonical_sha256([[index, canonical_sha256(row)]
            for index, row in enumerate(candidate_rows)]),
        "fixture_input_keyset_sha256": canonical_sha256(fixture_rows)}
    as_of = _time(report.get("finished_at", report.get("observed_at")))
    sources = [[_research_source_binding(leg, candidates, fixtures, as_of, card["profile_id"]) for leg in card["legs"]] for card in cards]
    if cards:
        for name in MODELS:
            try:
                result["models"][name] = _non_ready_model_research(cards, sources,
                    artifacts.get("numeric_models", {}).get(name) or {}, as_of)
            except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
                result["models"][name] = {"status": "UNAVAILABLE", "reason": "INVALID_LOCAL_MODEL_SOURCE",
                                           "source_card_count": len(cards), "cards": [], "joint_known_leg_events": None}
    result["status"] = "RESEARCH_SCENARIOS_ONLY" if cards else "NO_SUPPORTED_NON_READY_CARDS"
    return result
