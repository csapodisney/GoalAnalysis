"""Read-only, source-bound evidence for final cards; never a publication gate.

Hashes prove correspondence to the supplied frozen report/artifacts, not source
truth, temporal availability, calibration or a causal benefit from a feature.
No package-level coverage counter is interpreted as selected-leg data usage.
"""

from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from math import isclose, isfinite

from goal_analysis.agents import canonical_sha256

VERSION = "arthur-ticket-evidence-v1"
GROUPS = ("tickets", "recommendations", "profile_proposals", "preview_tickets")
CATEGORIES = ("odds", "model", "form", "xg", "players", "context")
PRODUCT = ("fixture_id", "market_key", "selection_key", "period", "bookmaker", "decimal_price")
PROBABILITY_KEYS = {("h2h", "home"): "home", ("h2h", "draw"): "draw",
                    ("h2h", "away"): "away", ("totals_2_5", "over"): "over25",
                    ("totals_2_5", "under"): "under25", ("btts", "yes"): "btts_yes",
                    ("btts", "no"): "btts_no"}


def _number(value):
    return type(value) in (int, float) and isfinite(value)


class _Sources:
    def __init__(self, report, artifacts):
        self.roots = {"report": report, "artifacts": artifacts}
        self.records = {}

    def add(self, root, path):
        value = self.roots[root]
        for key in path:
            value = value[key]
        identity = {"root": root, "path": list(path), "sha256": canonical_sha256(value)}
        identifier = canonical_sha256(identity)
        if identifier not in self.records:
            self.records[identifier] = {**identity, "payload": deepcopy(value)}
        return identifier


def _section(status="MISSING", refs=(), *, reason=None, **extra):
    return {"status": status, "source_refs": list(dict.fromkeys(refs)),
            "reason": reason or status, **extra}


def _index(rows, key):
    result = defaultdict(list)
    for index, row in enumerate(rows):
        if isinstance(row, dict) and isinstance(row.get(key), str):
            result[row[key]].append(index)
    return result


def _odds(leg, quotes, indices, sources):
    identifier = leg.get("source_candidate_id") or leg.get("quote_candidate_id") or leg.get("candidate_id")
    matches = indices.get(identifier, [])
    selected = {key: deepcopy(leg.get(key)) for key in (*PRODUCT, "quoted_at", "quote_observed_at",
        "quote_provider", "quote_source_id", "quote_source_sha256", "quote_time_basis")}
    if len(matches) != 1:
        return _section("AMBIGUOUS" if matches else "MISSING", selected=selected,
                        reason="SOURCE_CANDIDATE_NOT_UNIQUELY_BOUND")
    index = matches[0]
    quote = quotes[index]
    mismatches = [key for key in PRODUCT if key not in quote or key not in leg or quote[key] != leg[key]]
    mismatches += [key for key in ("kickoff", "settlement", "quote_provider", "quote_source_id",
        "quote_source_sha256", "quote_observed_at") if key in quote and quote[key] != leg.get(key)]
    # Provider clocks may be normalized to UTC in the selected leg. A missing
    # clock must never be replaced by the receipt or a generated current time.
    def clock(raw):
        try:
            parsed = datetime.fromisoformat(raw)
            return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
        except (TypeError, ValueError):
            return None
    raw_clock = quote.get("quote_timestamp_raw", quote.get("quoted_at"))
    if clock(raw_clock) != clock(leg.get("quoted_at")):
        mismatches.append("quoted_at")
    return _section("CONFLICT" if mismatches else "SELECTED_QUOTE_BOUND",
        [sources.add("artifacts", ("candidate_input", "candidates", index))],
        reason="SELECTED_PRODUCT_SOURCE_DIFFERENCE" if mismatches else "EXACT_SELECTED_PRODUCT_AND_SOURCE",
        selected=selected, mismatched_fields=mismatches,
        provider_quote_clock_known=clock(leg.get("quoted_at")) is not None,
        receipt_is_not_provider_quote_time=True)


def _model(leg, profile, models, model_indices, sources):
    probability = leg.get("model_probability")
    details = leg.get("model_details") or {}
    numeric = _number(probability) and 0 <= probability <= 1 and "HEURISTIC" not in str(leg.get("confidence_kind", ""))
    expected = details.get("shared_model_probabilities") if numeric else {}
    if not expected and numeric and profile in models:
        expected = {profile: probability}
    refs, bindings = [], []
    key = PROBABILITY_KEYS.get((leg.get("market_key"), leg.get("selection_key")))
    for name, value in (expected or {}).items():
        matches = model_indices.get(name, {}).get(leg.get("fixture_id"), [])
        match = len(matches) == 1
        if match:
            index = matches[0]
            prediction = models[name]["predictions"][index]
            actual = prediction.get("probabilities", {}).get(key)
            if key == "btts_no" and actual is None:
                yes = prediction.get("probabilities", {}).get("btts_yes")
                actual = 1 - yes if _number(yes) else None
            match = _number(actual) and _number(value) and isclose(actual, value, abs_tol=1e-12, rel_tol=1e-12)
            refs.append(sources.add("artifacts", ("numeric_models", name, "predictions", index)))
            if "training_datasets" in models[name]:
                refs.append(sources.add("artifacts", ("numeric_models", name, "training_datasets")))
        bindings.append({"profile_id": name, "selected_component": value, "prediction_matches": match})
    aggregate_matches = numeric and bool(bindings)
    if details.get("shared_model_probabilities"):
        weights = details.get("shared_model_weights") or {}
        aggregate_matches = (numeric and set(weights) == set(expected)
            and all(_number(w) and w > 0 for w in weights.values())
            and all(_number(p) and 0 <= p <= 1 for p in expected.values())
            and isclose(sum(weights.values()), 1, abs_tol=1e-12)
            and isclose(sum(weights[n] * p for n, p in expected.items()), probability, abs_tol=1e-12))
    return _section("SELECTED_ESTIMATE_RECORDED" if numeric else "MISSING",
        refs, reason="UNCALIBRATED_NOT_A_TICKET_WIN_PROBABILITY" if numeric else "NO_SELECTED_NUMERIC_PROBABILITY",
        selected_probability=probability if numeric else None, model_details=deepcopy(details),
        confidence_kind=leg.get("confidence_kind"), component_bindings=bindings,
        all_components_bound=aggregate_matches and all(row["prediction_matches"] for row in bindings))


def _history_binding_context(report, artifacts):
    """Verify the frozen history digest and original analysis clock, once.

    This is correspondence to supplied inputs, not independent source truth.
    A later finish/receipt time cannot make future evidence eligible.
    """
    from goal_analysis.features.daily_223_history import validate_history

    try:
        analysis = artifacts["candidate_analysis"]
        history = artifacts["history"]
        metadata = analysis["history_analysis"]
        validate_history(history)
        now = datetime.fromisoformat(analysis["observed_at"])
        reported = datetime.fromisoformat(report["observed_at"])
        collected = datetime.fromisoformat(history["collected_at"])
        if any(stamp.tzinfo is None or stamp.utcoffset() is None for stamp in (now, reported, collected)):
            return None
        if (now != reported or collected > now
                or metadata["feature_version"] != "daily223-history-v1"
                or metadata["snapshot_sha256"] != history["snapshot_sha256"]
                or metadata["sources"] != history["sources"]):
            return None
        return history["snapshot_sha256"], now, collected
    except (KeyError, TypeError, ValueError):
        return None


def _derived_history_matches(retained, source, leg, context):
    """Reproduce only the original history evidence's computed fresh=True.

    No other field is ignored or normalized. Duplicated identities, caller
    flags and future/invalid clocks never establish a new source binding.
    """
    if context is None or any(key not in source or key not in leg or source[key] != leg[key] for key in PRODUCT):
        return False
    digest, now, collected = context
    fields = {"evidence_id", "category", "statement", "claim_type", "source_id", "observed_at",
              "strength", "reliability"}
    originals = source.get("evidence", [])
    identifiers = [e.get("evidence_id") for e in retained]
    if len(set(identifiers)) != len(identifiers):
        return False
    for selected in retained:
        matches = [e for e in originals if e.get("evidence_id") == selected.get("evidence_id")]
        if len(matches) != 1:
            return False
        original = matches[0]
        category = original.get("category")
        if (set(original) != fields or set(selected) != fields | {"fresh"}
                or selected["fresh"] is not True or category not in {"historical", "venue_form"}
                or original["evidence_id"] != f"history-v1:{source['candidate_id']}:{category}"
                or original["source_id"] != f"history-sha256:{digest}"
                or original["claim_type"] != "INFERENCE"
                or not isinstance(original["statement"], str) or not original["statement"].strip()
                or not all(_number(original[k]) and 0 <= original[k] <= 1 for k in ("strength", "reliability"))
                or canonical_sha256({k: selected[k] for k in fields}) != canonical_sha256(original)):
            return False
        try:
            stamp = datetime.fromisoformat(original["observed_at"])
            if stamp.tzinfo is None or stamp.utcoffset() is None or stamp > min(now, collected):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _preselection_review(report, artifacts, leg, sources):
    """Bind only the actual selected leg's retained candidate review."""
    mode = report.get('candidate_context_review', {}).get('mode')
    if mode is None:
        return None
    if mode != 'ACTIVE':
        return {'mode': mode, 'status': 'SHADOW_CAPTURE_NOT_USED_BY_SELECTED_LEG', 'categories': []}
    identifier = leg.get('source_candidate_id') or leg.get('candidate_id')
    if (report.get('profile_context_review', {}).get('mode') == 'ACTIVE'
            and identifier != leg.get('candidate_id')):
        from goal_analysis.jobs.profile_context_review import original_quote, quote_index

        try:
            linked, _ = original_quote(leg, quote_index(artifacts.get('candidate_input', {}).get('candidates', [])))
        except (ValueError, KeyError, TypeError):
            return {'mode': mode, 'status': 'NO_EXACT_ORIGINAL_QUOTE_CONTEXT_LINK', 'categories': []}
        if linked != identifier:
            return {'mode': mode, 'status': 'NO_EXACT_ORIGINAL_QUOTE_CONTEXT_LINK', 'categories': []}
    locations = [(('candidate_review_actual_packets', i), p)
                 for i, p in enumerate(artifacts.get('candidate_review_actual_packets', []))]
    locations += [(('candidate_review_follow_up_packets', i, j), p)
                  for i, batch in enumerate(artifacts.get('candidate_review_follow_up_packets', []))
                  for j, p in enumerate(batch)]
    matching = [(path, p) for path, p in locations if p.get('candidate_id') == identifier
                and p.get('fixture_id') == leg.get('fixture_id')]
    unique = {canonical_sha256(p): (path, p) for path, p in matching}
    reviews = [(i, r) for i, r in enumerate(report.get('astra', {}).get('reviews', []))
               if r.get('candidate_id') == identifier]
    if (len(unique) != 1 or len(reviews) != 1 or report.get('astra', {}).get('status') != 'COMPLETE'
            or leg.get('astra_review') != reviews[0][1]):
        return {'mode': mode, 'status': 'NO_EXACT_RETAINED_SELECTED_LEG_REVIEW', 'categories': []}
    path, packet = next(iter(unique.values()))
    context = packet.get('decision_context', {})
    supplied = artifacts.get('candidate_context_inputs', {}).get('contexts', {}).get(leg['fixture_id'])
    if supplied is not None and supplied != context:
        raise ValueError('Selected context differs from actual frozen source input')
    packet_batch = artifacts[path[0]] if len(path) == 2 else artifacts[path[0]][path[1]]
    bound = []
    for index, batch in enumerate(artifacts.get('candidate_review_response_batches', [])):
        response = batch.get('response', {})
        packed = response.get('decision_context_input', {})
        if (batch.get('packet_sha256') != canonical_sha256(packet_batch)
                or response.get('status') != 'COMPLETE'
                or reviews[0][1] not in response.get('reviews', [])):
            continue
        actual_hash = sha256(json.dumps(packed, sort_keys=True, ensure_ascii=False,
                                        allow_nan=False).encode()).hexdigest()
        if actual_hash != response.get('decision_context_input_sha256'):
            raise ValueError('Actual candidate review input hash mismatch')
        from goal_analysis.agents.astra_review import _expand_shared_facts, _ReviewError

        # The archived hash binds the sent wire; verify references before comparing
        # its fully restored facts with the original frozen packet.
        try:
            packed = _expand_shared_facts(packed)
        except (_ReviewError, KeyError, TypeError, ValueError) as error:
            raise ValueError('Invalid shared facts in actual candidate review input') from error
        requested = [p for p in packed.get('candidates', []) if p.get('candidate_id') == identifier]
        if (len(requested) != 1 or any(requested[0].get(k) != packet.get(k)
                for k in ('candidate_id', 'fixture_id', 'market_key', 'selection_key', 'period', 'decimal_price'))
                or requested[0].get('decision_context', {}).get('context_id') != context.get('context_id')
                or context not in packed.get('decision_contexts', [])):
            raise ValueError('Actual review request differs from selected context or market')
        bound.append(index)
    if len(bound) != 1:
        return {'mode': mode, 'status': 'NO_ACTUAL_SOURCE_BOUND_REVIEW_REQUEST', 'categories': []}
    from goal_analysis.jobs.candidate_context_review import usage

    review_index, answer = reviews[0]
    refs = [sources.add('artifacts', path), sources.add('report', ('astra', 'reviews', review_index))]
    refs.append(sources.add('artifacts', ('candidate_review_response_batches', bound[0])))
    if supplied is not None:
        refs.append(sources.add('artifacts', ('candidate_context_inputs', 'contexts', leg['fixture_id'])))
    rows = usage([packet], {'reviews': [answer]})
    return {'mode': mode, 'status': 'EXACT_SELECTED_LEG_REVIEW_BOUND', 'source_refs': refs,
            'categories': rows, 'new_category_weights': False,
            'semantics': 'MODEL_DECLARED_SOURCE_CONSIDERATION_AND_EXISTING_CAUTION_NOT_CAUSAL_GAIN'}


def build_ticket_evidence(report: dict, artifacts: dict) -> dict:
    """Bind every final card/leg, including abstentions and previews, additively.

    RETAINED/RANKING_TRACE records describe existing selector outputs. Available
    supplemental data alone never receives that designation. All missing and
    conflicting links remain visible; they neither veto nor repair a ticket.
    """
    sources = _Sources(report, artifacts)
    quotes = artifacts.get("candidate_input", {}).get("candidates", [])
    quote_indices = _index(quotes, "candidate_id")
    analysis = artifacts.get("candidate_analysis", {}).get("candidates", [])
    analysis_indices = _index(analysis, "candidate_id")
    models = artifacts.get("numeric_models", {})
    model_indices = {name: _index(value.get("predictions", []), "fixture_id")
                     for name, value in models.items() if isinstance(value, dict)}
    collection = report.get("sports_supplement", {}).get("the_stats_api") or {}
    contexts = collection.get("context_by_fixture") or {}
    advisory = report.get("expert_advisory") or {}
    advisory_indices = {name: _index((advisory.get(name) or {}).get("fixtures", []), "fixture_id")
                        for name in ("weather", "motivation")}
    entries = []
    history_checked, history_context = False, None
    for group in GROUPS:
        for card_index, card in enumerate(report.get(group, []) or []):
            card_path = (group, card_index)
            legs = []
            for leg_index, leg in enumerate(card.get("legs", []) or []):
                fixture = leg.get("fixture_id")
                selected_path = (*card_path, "legs", leg_index)
                selected_ref = sources.add("report", selected_path)
                identifier = leg.get("source_candidate_id") or leg.get("candidate_id")
                form_refs = []
                retained = [e for e in leg.get("evidence", []) if e.get("category") in {"historical", "venue_form"}]
                matches = analysis_indices.get(identifier, [])
                if retained and len(matches) == 1:
                    source = analysis[matches[0]]
                    if all(e in source.get("evidence", []) for e in retained):
                        form_refs.append(sources.add("artifacts", ("candidate_analysis", "candidates", matches[0])))
                    else:
                        if not history_checked:
                            history_context = _history_binding_context(report, artifacts)
                            history_checked = True
                        if _derived_history_matches(retained, source, leg, history_context):
                            form_refs.append(sources.add("artifacts", ("candidate_analysis", "candidates", matches[0])))
                            for path in (("history", "snapshot_sha256"), ("history", "collected_at"),
                                    ("candidate_analysis", "history_analysis", "snapshot_sha256"),
                                    ("candidate_analysis", "observed_at")):
                                form_refs.append(sources.add("artifacts", path))
                sections = {"odds": _odds(leg, quotes, quote_indices, sources),
                    "model": _model(leg, card.get("profile_id"), models, model_indices, sources),
                    "form": _section("RETAINED_SELECTION_EVIDENCE" if form_refs else "MISSING",
                        form_refs, reason="MATCHED_RETAINED_HISTORY_INPUT" if form_refs else "NO_UNIQUE_RETAINED_HISTORY_INPUT",
                        retained_evidence=deepcopy(retained)),
                    "xg": _section(), "players": _section(), "context": _section()}
                available = defaultdict(list)
                for i, fact in enumerate((contexts.get(fixture) or {}).get("facts", [])):
                    if fact.get("status") == "AVAILABLE":
                        category = fact.get("category")
                        domain = ("xg" if category == "team_season_stats" else "players" if category in
                            {"player_season_stats", "squad", "injuries_suspensions", "lineup"} else "context")
                        available[domain].append(sources.add("report", ("sports_supplement", "the_stats_api",
                            "context_by_fixture", fixture, "facts", i)))
                for name, by_fixture in advisory_indices.items():
                    for i in by_fixture.get(fixture, []):
                        available["context"].append(sources.add("report", ("expert_advisory", name, "fixtures", i)))
                for domain in ("xg", "players", "context"):
                    if available[domain]:
                        sections[domain] = _section("AVAILABLE_NOT_CONFIRMED_USED", available[domain],
                            reason="SOURCE_RECORD_AVAILABLE_NOT_SELECTED_LEG_USAGE_OR_FRESHNESS_CERTIFICATION")
                stats = card.get("expert_ranking", {}).get("the_stats_api") or {}
                trace_indices = [i for i, row in enumerate(stats.get("legs", [])) if
                    row.get("leg_index") == leg_index and all(row.get(key) == leg.get(key)
                    for key in ("fixture_id", "market_key", "selection_key"))
                    and row.get("quoted_price") == leg.get("decimal_price")]
                if len(trace_indices) == 1:
                    i = trace_indices[0]
                    row = stats["legs"][i]
                    ref = sources.add("report", (*card_path, "expert_ranking", "the_stats_api", "legs", i))
                    sections["xg"] = _section("RANKING_TRACE_RECORDED", [ref, *available["xg"]],
                        reason="SELECTED_OPTION_XG_UTILITY_CROSSCHECK_NOT_LIVE_PROBABILITY_EDIT")
                    if row.get("player_context") or row.get("absence_context"):
                        sections["players"] = _section("RANKING_TRACE_RECORDED", [ref, *available["players"]],
                            reason="SELECTED_OPTION_PLAYER_CROSSCHECK",
                            availability_applied=row.get("player_availability_applied") is True,
                            data_coverage=deepcopy(row.get("data_coverage", {})))
                if leg.get("fixture_context") or leg.get("astra_review") or leg.get("contrarian_analysis") or leg.get("veto_override"):
                    sections["context"]["retained_selected_context_ref"] = selected_ref
                    sections["context"]["retained_context_is_not_causal_or_source_truth_certification"] = True
                preselection = _preselection_review(report, artifacts, leg, sources)
                if preselection is not None:
                    sections['context']['preselection_review'] = preselection
                    for domain, categories in (('players', {'players'}), ('xg', {'xg'}),
                                               ('context', {'schedule', 'travel', 'rotation', 'weather'})):
                        considered = [r for r in preselection.get('categories', [])
                                      if r['category'] in categories and r['source_status'] == 'SOURCED'
                                      and r['response_considered'] and r['context_evidence_cited']]
                        if considered:
                            sections[domain]['candidate_review_categories'] = [r['category'] for r in considered]
                            sections[domain]['candidate_review_source_refs'] = preselection['source_refs']
                            if sections[domain]['status'] in {'MISSING', 'AVAILABLE_NOT_CONFIRMED_USED'}:
                                sections[domain]['status'] = 'REVIEW_SOURCE_CITED'
                                sections[domain]['reason'] = 'EXACT_SELECTED_LEG_CONTEXT_REVIEW_NOT_NUMERIC_OR_CAUSAL_GAIN'
                gaps = [{"domain": name, "reason": section["reason"]}
                        for name, section in sections.items()
                        if section["status"] not in {"SELECTED_QUOTE_BOUND", "RETAINED_SELECTION_EVIDENCE", "RANKING_TRACE_RECORDED", "REVIEW_SOURCE_CITED"}
                        and not (name == "model" and section["all_components_bound"])]
                legs.append({"leg_index": leg_index, "fixture_id": fixture,
                    "selected_leg_ref": selected_ref, "sections": sections, "gaps": gaps})
            entries.append({"location": list(card_path), "profile_id": card.get("profile_id"),
                "ticket_id": card.get("ticket_id"), "status": card.get("status"),
                "card_sha256": canonical_sha256(card), "leg_count": len(legs), "legs": legs,
                "strategy_version": card.get("strategy_version"),
                "strategy_manifest_sha256": card.get("strategy_manifest_sha256"),
                "card_ranking_trace": deepcopy(card.get("expert_ranking", {})),
                "empty_card_reason": card.get("reason") or card.get("message") if not legs else None})
    result = {"version": VERSION, "mode": "AUDIT_ONLY", "decision_effect": "NONE",
        "run_id": report.get("run_id"), "date": report.get("date"),
        "as_of": report.get("finished_at", report.get("observed_at")),
        "strategy_manifest_sha256": report.get("strategy_manifest", {}).get("manifest_sha256"),
        "semantics": "SOURCE_CORRESPONDENCE_AND_RETAINED_TRACES_NOT_CAUSAL_OR_POINT_IN_TIME_CERTIFICATION",
        "external_requests": 0, "cards": entries, "sources": sources.records}
    result["bundle_sha256"] = canonical_sha256(result)
    return result


def validate_ticket_evidence(bundle: dict, report: dict, artifacts: dict) -> None:
    """Rebind to actual frozen inputs, not just a self-consistent bundle hash.

    A digest is an integrity check, not a signature or independent verification
    that the supplied report is the authentic one. Preserve its archive receipt.
    """
    if bundle != build_ticket_evidence(report, artifacts):
        raise ValueError("Ticket evidence differs from frozen source inputs or final selected cards")
