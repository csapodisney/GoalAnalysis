"""Ticket-level deduplication, soft similarity and final READY-package exposure.

DAILY objects are fixed anchors. Research aliases never dilute actual exposure.
All utilities are declared normalized support, not calibrated probabilities.
"""

from __future__ import annotations

import math
from collections import Counter
from copy import deepcopy
from datetime import datetime

from goal_analysis.config.portfolio import TIPSTER_PROFILE_IDS
from goal_analysis.engine.publication_window import allows_new_ticket
from goal_analysis.engine.ticket_scoring import normalize_score

VERSION = "arthur-soft-ticket-similarity-v3-club-reuse"
OVERLAP_THRESHOLD = 0.50
SIMILARITY_FACTOR = 0.70
MAX_READY_FIXTURE_SHARE = 0.50
BEAM_WIDTH = 96
ERRORS = {"MODEL_ERROR", "DEPENDENCY_MISSING", "INSUFFICIENT_HISTORY", "UNAVAILABLE",
          "SUSPENDED", "DATA_BLOCKED", "FAILED", "ERROR"}


def abstain(item, reason, details=None):
    result = deepcopy(item)
    result.update(status="ABSTAINED", legs=[], combined_price=None, bookmaker=None,
                  playable=False, selected_for_portfolio=False, portfolio_ticket_id=None,
                  stake_eur=None, confidence="LOW", construction_verified=False, reason_code=reason,
                  abstention_reason="Nincs megfelelő, nem duplikált szelvény a meglévő adat- és minőségi feltételekkel.")
    result.pop("recommendation_id", None)
    result["diversification"] = {"policy_version": VERSION, **(details or {})}
    result.setdefault("quality_warnings", []).append(result["abstention_reason"])
    if isinstance(result.get("model_summary"), dict):
        result["model_summary"]["selected_legs"] = 0
    diagnostics = result.get("selection_diagnostics", {})
    for key in list(diagnostics):
        if key.startswith(("selected_", "club_")) or key == "duplicate_exposure_profile_ids":
            diagnostics.pop(key)
    return result


def _text(value):
    return str(value if value is not None else "").strip().casefold()


def ticket_signature(ticket):
    """Economic selections, excluding persona, bookmaker and quote IDs."""
    return tuple(sorted((
        _text(leg.get("fixture_id")), _text(leg.get("market_key")),
        _text(leg.get("selection_key")), _text(leg.get("period")),
        _text(leg.get("line", leg.get("handicap", ""))),
    ) for leg in ticket.get("legs", [])))


def _fixtures(ticket):
    return frozenset(leg["fixture_id"] for leg in ticket.get("legs", []))


def ticket_similarity(candidate, selected):
    """True Jaccard and directional overlap differ: 2/4 versus 2/3."""
    a, b = _fixtures(candidate), _fixtures(selected)
    la, lb = set(ticket_signature(candidate)), set(ticket_signature(selected))
    return {
        "fixture_jaccard": len(a & b) / len(a | b) if a | b else 0.0,
        "selection_jaccard": len(la & lb) / len(la | lb) if la | lb else 0.0,
        "candidate_fixture_overlap": len(a & b) / len(a) if a else 0.0,
        "exact_duplicate": bool(la) and la == lb,
    }


def _template(option):
    return option.get("recommendation") or option.get("ticket") or {}


def _eligible(option, now, day):
    quality, template = option.get("quality"), _template(option)
    legs = template.get("legs", [])
    if (type(quality) not in (int, float) or not math.isfinite(quality)
            or quality < 0 or not legs or template.get("status") in ERRORS
            or template.get("date", day) != day or not allows_new_ticket(template, now)):
        return False
    if template.get("profile_id") in TIPSTER_PROFILE_IDS:
        from goal_analysis.tipsters.contracts import valid_construction

        if not valid_construction(template, now=now):
            return False
    from goal_analysis.engine.specialist_contracts import valid_specialist_construction

    if not valid_specialist_construction(template):
        return False
    if ("ticket" in option and "recommendation" in option
            and ticket_signature(option["ticket"]) != ticket_signature(option["recommendation"])):
        return False
    try:
        fixtures = [leg["fixture_id"] for leg in legs]
        if len(set(fixtures)) != len(fixtures):
            return False
        for leg in legs:
            kickoff = datetime.fromisoformat(leg["kickoff"])
            if (kickoff.tzinfo is None or kickoff.utcoffset() is None or kickoff <= now
                    or not isinstance(leg["fixture_id"], str) or not leg["fixture_id"].strip()
                    or not all(leg.get(key) for key in ("market_key", "selection_key", "period"))):
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def _against(option, anchors):
    comparisons = [(a.get("profile_id"), ticket_similarity(_template(option), a)) for a in anchors]
    if any(sim["exact_duplicate"] for _, sim in comparisons):
        return None
    overlap = max((sim["candidate_fixture_overlap"] for _, sim in comparisons), default=0.0)
    factor = SIMILARITY_FACTOR if overlap > OVERLAP_THRESHOLD else 1.0
    reuse_factor, maximum_uses = 1.0, 0
    if _template(option).get("profile_id") in TIPSTER_PROFILE_IDS:
        # Whole-ticket similarity misses one popular fixture recurring across
        # many otherwise different club combinations. Softly price that reuse
        # without banning a match or relaxing the profile's shape/value gates.
        distinct = {ticket_signature(anchor): anchor for anchor in anchors}
        counts = Counter(f for anchor in distinct.values() for f in _fixtures(anchor))
        uses = [counts[f] for f in _fixtures(_template(option))]
        maximum_uses = max(uses, default=0)
        mean_uses = math.fsum(uses) / len(uses) if uses else 0.0
        reuse_factor = 1 / (1 + 0.12 * maximum_uses + 0.05 * mean_uses)
    return {**option, "adjusted_quality": option["quality"] * factor * reuse_factor, "similarity": {
        "penalty_factor": factor, "max_candidate_fixture_overlap": overlap,
        "club_fixture_reuse_factor": reuse_factor,
        "club_max_prior_fixture_uses": maximum_uses,
        "max_fixture_jaccard": max((sim["fixture_jaccard"] for _, sim in comparisons), default=0.0),
        "max_selection_jaccard": max((sim["selection_jaccard"] for _, sim in comparisons), default=0.0),
        "penalized_against_profiles": [p for p, sim in comparisons
                                       if sim["candidate_fixture_overlap"] > OVERLAP_THRESHOLD],
    }}


def ready_exposure(tickets):
    """Count final READY main tickets once, excluding recommendation aliases."""
    ready = [t for t in tickets if t.get("status") == "READY"]
    counts = Counter(f for t in ready for f in _fixtures(t))
    limit = max(1, math.floor(len(ready) * MAX_READY_FIXTURE_SHARE)) if ready else 0
    return {
        "ready_ticket_count": len(ready), "maximum_fixture_share": MAX_READY_FIXTURE_SHARE,
        "allowed_tickets_per_fixture": limit, "fixture_ticket_counts": dict(sorted(counts.items())),
        "fixture_ticket_shares": {f: n / len(ready) for f, n in sorted(counts.items())},
        "single_ready_ticket_exception": len(ready) == 1,
        "compliant": all(n <= limit for n in counts.values()),
    }


def _option_order(option):
    return (-option.get("adjusted_quality", option["quality"]), -option["quality"],
            ticket_signature(_template(option)), str(_template(option).get("bookmaker", "")),
            str(_template(option).get("ticket_id", _template(option).get("recommendation_id", ""))))


def _ranked_package(chosen, daily_anchors):
    """Higher unpenalized rank is always the reference, independent of beam path."""
    anchors, ranked = list(daily_anchors), {}
    for profile, option in sorted(chosen.items(), key=lambda row: (
            -row[1]["quality"], ticket_signature(_template(row[1])), row[0])):
        adjusted = _against(option, anchors)
        if adjusted is None:
            return None
        ranked[profile] = adjusted
        anchors.append(_template(option))
    return math.fsum(o["adjusted_quality"] for o in ranked.values()), ranked


def _exposure_excess(tickets):
    exposure = ready_exposure(tickets)
    return sum(max(0, count - exposure["allowed_tickets_per_fixture"])
               for count in exposure["fixture_ticket_counts"].values()), exposure


def _repair_recommendation_exposure(chosen, pools, daily, daily_anchors, main_profiles):
    """Prefer complete alternatives, then abstention, to enforce package exposure.

    Main portfolio selections are already jointly constrained and are retained.
    Research/profile recommendations may change only to another independently
    eligible option from the same profile. No leg is added and no score gate is
    relaxed.
    """
    selected = dict(chosen)
    repairs = []

    def package_rows(values):
        return [*daily, *[_template(option) for option in values.values()]]

    for _ in range(max(1, len(selected) * 3)):
        current_excess, exposure = _exposure_excess(package_rows(selected))
        if current_excess == 0:
            return selected, exposure, repairs
        violating = {fixture for fixture, count in exposure["fixture_ticket_counts"].items()
                     if count > exposure["allowed_tickets_per_fixture"]}
        alternatives = []
        for profile, old in selected.items():
            if profile in main_profiles or not (_fixtures(_template(old)) & violating):
                continue
            for candidate in pools.get(profile, []):
                if ticket_signature(_template(candidate)) == ticket_signature(_template(old)):
                    continue
                trial = {**selected, profile: candidate}
                ranked = _ranked_package(trial, daily_anchors)
                if ranked is None:
                    continue
                _score, assigned = ranked
                excess, trial_exposure = _exposure_excess(package_rows(assigned))
                if excess < current_excess:
                    alternatives.append((current_excess - excess,
                                         assigned[profile]["adjusted_quality"], profile,
                                         assigned, trial_exposure))
        if alternatives:
            improvement, _quality, profile, selected, exposure = max(
                alternatives, key=lambda row: (row[0], row[1], row[2]))
            repairs.append({"profile_id": profile, "action": "ALTERNATIVE_SELECTED",
                            "exposure_excess_reduction": improvement})
            continue
        removable = [(option.get("adjusted_quality", option["quality"]), profile)
                     for profile, option in selected.items()
                     if profile not in main_profiles and _fixtures(_template(option)) & violating]
        if not removable:
            return selected, exposure, repairs
        _quality, profile = min(removable, key=lambda row: (row[0], row[1]))
        selected.pop(profile)
        repairs.append({"profile_id": profile, "action": "ABSTAINED_FOR_EXPOSURE"})
        ranked = _ranked_package(selected, daily_anchors)
        if ranked is not None:
            _score, selected = ranked
    _excess, exposure = _exposure_excess(package_rows(selected))
    return selected, exposure, repairs


def _main_package(pools, profiles, daily, capacity, daily_anchors=None):
    """Joint bounded search, validating the final READY denominator after pruning."""
    daily_anchors = daily if daily_anchors is None else daily_anchors
    protected_ready = sum(t.get("status") == "READY" for t in daily)
    for size in range(min(capacity, len(profiles)), -1, -1):
        states = [(0.0, {})]
        optimistic_limit = max(1, math.floor((protected_ready + size) * MAX_READY_FIXTURE_SHARE))
        for _depth in range(size):
            next_states = {}
            for utility, chosen in states:
                anchors = [*daily_anchors, *[_template(o) for o in chosen.values()]]
                for profile in profiles:
                    if profile in chosen:
                        continue
                    candidates = []
                    for option in pools[profile]:
                        if "ticket" not in option or option["ticket"].get("status") != "READY":
                            continue
                        adjusted = _against(option, anchors)
                        if adjusted is None:
                            continue
                        tickets = [*daily, *[o["ticket"] for o in chosen.values()], option["ticket"]]
                        if any(n > optimistic_limit for n in ready_exposure(tickets)["fixture_ticket_counts"].values()):
                            continue
                        candidates.append(adjusted)
                    for adjusted in sorted(candidates, key=_option_order)[:24]:
                        package = _ranked_package({**chosen, profile: {**adjusted,
                            "adjusted_quality": adjusted["quality"]}}, daily_anchors)
                        if package is None:
                            continue
                        score, assigned = package
                        key = tuple(sorted((p, ticket_signature(o["ticket"])) for p, o in assigned.items()))
                        if key not in next_states or score > next_states[key][0]:
                            next_states[key] = score, assigned
            states = sorted(next_states.values(), key=lambda state: (
                -state[0], tuple((p, ticket_signature(o["ticket"])) for p, o in sorted(state[1].items())),
            ))[:BEAM_WIDTH]
            if not states:
                break
        valid = [(utility, chosen) for utility, chosen in states if len(chosen) == size
                 and ready_exposure([*daily, *[o["ticket"] for o in chosen.values()]])["compliant"]]
        if valid:
            return valid[0][1]
    return {}


def allocate(report, options, settings, now, *, plan=None):
    if any(report.get(k) in ERRORS | {"TEAM_PAUSED"} for k in ("construction_status", "status")):
        return report
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Diversification requires an aware publication clock")
    plan = plan or report.get("team") or {}
    states = plan.get("profile_states", {})
    roster = list(dict.fromkeys([*settings.get("enabled_profiles", []), *options]))
    order = {p: i for i, p in enumerate(roster)}
    cards = {i["profile_id"]: i for i in report.get("recommendations", [])}
    pools, bests = {}, {}
    for profile in roster:
        current = cards.get(profile, {})
        if (profile == "daily223" or states.get(profile) == "SUSPENDED"
                or current.get("status") in ERRORS or current.get("publication_scope") == "SUSPENDED"):
            continue
        valid = [deepcopy(o) for o in options.get(profile, []) if _eligible(o, now, report["date"])]
        legacy_scale = max(1.0, max((o["quality"] for o in valid if not o.get("scoring")), default=1.0))
        unique = {}
        for option in valid:
            if not option.get("scoring"):
                option["quality"] = normalize_score(option["quality"], legacy_scale) * math.sqrt(len(_fixtures(_template(option))))
                option["scoring"] = {"score_kind": "LEGACY_RELATIVE_UTILITY_NOT_PROBABILITY",
                                     "normalization_scale": legacy_scale}
            if _template(option).get("profile_id") != profile:
                continue
            signature = ticket_signature(_template(option))
            if signature not in unique or _option_order(option) < _option_order(unique[signature]):
                unique[signature] = option
        pools[profile] = sorted(unique.values(), key=_option_order)
        bests[profile] = max((o["quality"] for o in pools[profile]), default=None)
    daily_tickets = [i for i in report.get("tickets", []) if i["profile_id"] == "daily223"]
    daily_anchors = list(daily_tickets)
    for group in ("recommendations", "profile_proposals"):
        for item in report.get(group, []):
            if (item["profile_id"] == "daily223" and item.get("legs")
                    and item.get("status") != "TARGET_PROPOSAL"
                    and item.get("construction_verified") is not False
                    and type(item.get("combined_price")) in (int, float) and item["combined_price"] > 1
                    and ticket_signature(item) not in {ticket_signature(t) for t in daily_anchors}):
                daily_anchors.append(item)

    def scope(profile):
        return cards.get(profile, {}).get("publication_scope") or (
            "PAPER_AUDITION" if states.get(profile) == "PROBATION" else "LIVE")

    main_profiles = [p for p in roster if p in pools and scope(p) == "LIVE" and p != "available"
                     and any("ticket" in o and o["ticket"].get("status") == "READY" for o in pools[p])]
    main_profiles.sort(key=lambda p: (-(bests[p] or 0), order[p]))
    capacity = max(0, min(settings.get("target_ticket_count", 2), settings.get("max_ticket_count", 5)) - len(daily_tickets))
    chosen = _main_package(pools, main_profiles, daily_tickets, capacity, daily_anchors)
    main = set(chosen)
    main_order = list(chosen)
    anchors = [*daily_anchors, *[_template(o) for o in chosen.values()]]
    remaining = {p for p in roster if p in pools and p not in chosen}
    for tier in ("LIVE", "PAPER_AUDITION", "OTHER"):
        active = {p for p in remaining if (scope(p) if scope(p) in {"LIVE", "PAPER_AUDITION"} else "OTHER") == tier}
        while active:
            candidates = []
            for profile in sorted(active, key=order.get):
                adjusted = [a for o in pools[profile] if (a := _against(o, anchors)) is not None]
                if adjusted:
                    candidates.append((profile, min(adjusted, key=_option_order)))
            if not candidates:
                break
            profile, option = min(candidates, key=lambda row: (*_option_order(row[1]), order[row[0]]))
            chosen[profile] = option
            anchors.append(_template(option))
            active.remove(profile)
            remaining.remove(profile)

    daily_recommendations = [item for item in report.get("recommendations", [])
                             if item.get("profile_id") == "daily223"
                             and item.get("status") == "READY" and item.get("legs")]
    chosen, recommendation_exposure, exposure_repairs = _repair_recommendation_exposure(
        chosen, pools, daily_recommendations, daily_anchors, main,
    )
    main.intersection_update(chosen)
    main_order = [profile for profile in main_order if profile in main]

    def quality_diagnostics(profile):
        # Observational only: compare within this persona's eligible option
        # pool, before similarity/reuse penalties. This is not a probability
        # or an imposed quality floor, and it never participates in selection.
        best = bests[profile]
        return {
            "own_best_quality": best,
            "retained_own_quality_ratio": chosen[profile]["quality"] / best if best > 0 else None,
        }

    def decorate(template, profile):
        result = deepcopy(template)
        for key in ("publication_scope", "team_status", "team_plan_sha256"):
            if key in cards.get(profile, {}):
                result[key] = cards[profile][key]
        result.setdefault("publication_scope", scope(profile))
        result.setdefault("team_status", states.get(profile, "ACTIVE"))
        if plan.get("plan_sha256"):
            result["team_plan_sha256"] = plan["plan_sha256"]
        option = chosen[profile]
        expert_ranking = {key: deepcopy(value) for key, value in option.get('scoring', {}).items()
                          if key in {'weather_expert', 'the_stats_api'}}
        if expert_ranking:
            # Make the actual data contribution auditable on the selected card,
            # not only in a detached provider supplement.
            result['expert_ranking'] = expert_ranking
        result["diversification"] = {"policy_version": VERSION,
            "unpenalized_score": option["quality"], "adjusted_score": option["adjusted_quality"],
            **quality_diagnostics(profile), **option["similarity"]}
        return result

    report["tickets"] = [*daily_tickets, *[decorate(chosen[p]["ticket"], p) for p in main_order]]
    for group in ("profile_proposals", "preview_tickets"):
        preserved = [i for i in report.get(group, []) if i["profile_id"] == "daily223"]
        if group == "profile_proposals":
            preserved.extend(decorate(o["ticket"], p) for p, o in chosen.items() if "ticket" in o)
        report[group] = preserved
    abstentions = {p: {"reason_code": "NO_ELIGIBLE_NON_DUPLICATE_TICKET",
                       "original_best_quality": bests.get(p), "eligible_option_count": len(pools[p])}
                   for p in pools if p not in chosen}
    result_cards = []
    for item in report.get("recommendations", []):
        profile = item["profile_id"]
        option = chosen.get(profile)
        if profile == "daily223":
            result_cards.append(item)
        elif option and "recommendation" in option:
            card = decorate(option["recommendation"], profile)
            card.update(selected_for_portfolio=profile in main,
                        portfolio_ticket_id=option["ticket"]["ticket_id"] if profile in main else None)
            result_cards.append(card)
        elif item.get("status") in ERRORS or not item.get("legs"):
            result_cards.append(item)
        else:
            details = abstentions.setdefault(profile, {"reason_code": "NO_ELIGIBLE_NON_DUPLICATE_TICKET"})
            result_cards.append(abstain(item, details["reason_code"], details))
    if "recommendations" in report:
        report["recommendations"] = result_cards
    selected_profiles = {i["profile_id"] for i in report["tickets"]}
    for item in report.get("profiles", []):
        profile = item["profile_id"]
        item["published"] = profile in selected_profiles
        if profile in abstentions:
            item.update(construction_status="ABSTAINED", reason="Nincs megfelelő, nem duplikált szelvény.")
    report["diversification"] = {
        "policy_version": VERSION, "own_quality_retention": None,
        "similarity_threshold": OVERLAP_THRESHOLD, "similarity_factor": SIMILARITY_FACTOR,
        "similarity_penalty_basis": "SHARED_FIXTURES_DIVIDED_BY_CANDIDATE_FIXTURES",
        "jaccard_reported_separately": True, "non_daily_fixture_limit": None,
        "search_method": "BOUNDED_JOINT_READY_PACKAGE_BEAM_THEN_SOFT_RESEARCH_SELECTION",
        "ready_exposure": ready_exposure(report["tickets"]),
        "ready_recommendation_exposure": recommendation_exposure,
        "recommendation_exposure_repairs": exposure_repairs,
        "assigned": {p: {"fixture_ids": sorted(_fixtures(_template(o))), "quality": o["quality"],
            "adjusted_quality": o["adjusted_quality"], "main_ticket": p in main,
            "scope": scope(p), **quality_diagnostics(p), **o["similarity"]} for p, o in chosen.items()},
        "abstained_profiles": abstentions,
    }
    report["construction_status"] = (
        "COMPLETE" if len(report["tickets"]) >= settings.get("target_ticket_count", 2)
        and ("daily223" not in plan.get("live_profiles", settings.get("enabled_profiles", [])) or bool(daily_tickets))
        else "PARTIAL" if report["tickets"] else "CONSTRUCTION_INCOMPLETE")
    _refresh_diagnostics(report)
    return report


def _refresh_diagnostics(report):
    club = [i for i in report.get("recommendations", []) if i["profile_id"] in TIPSTER_PROFILE_IDS]
    published = [i for i in club if i.get("legs")]
    exposure = Counter(f for i in published for f in _fixtures(i))
    signatures = {}
    for item in published:
        signatures.setdefault(ticket_signature(item), []).append(item["profile_id"])
    for item in club:
        legs = item.get("legs", [])
        if isinstance(item.get("model_summary"), dict):
            item["model_summary"]["selected_legs"] = len(legs)
        item.setdefault("selection_diagnostics", {}).update(
            selected_fixture_ids=sorted({l["fixture_id"] for l in legs}),
            selected_modeled_leg_count=sum(l.get("model_probability") is not None for l in legs),
            selected_negative_estimated_edge_leg_count=sum(l.get("estimated_edge") is not None and l["estimated_edge"] < 0 for l in legs),
            club_published_profile_count=len(published), club_selected_fixture_count=len(exposure),
            club_modeled_leg_count=sum(l.get("model_probability") is not None for c in published for l in c["legs"]),
            club_negative_estimated_edge_leg_count=sum(l.get("estimated_edge") is not None and l["estimated_edge"] < 0 for c in published for l in c["legs"]),
            club_max_fixture_profile_count=max(exposure.values(), default=0),
            club_max_fixture_profile_share=max(exposure.values(), default=0) / len(published) if published else None,
            selected_fixture_profile_counts={l["fixture_id"]: exposure[l["fixture_id"]] for l in legs},
            duplicate_exposure_profile_ids=next((ps for ps in signatures.values() if item["profile_id"] in ps and len(ps) > 1), []),
        )
    if "tipster_club" in report:
        report["tipster_club"]["published_count"] = len(published)
