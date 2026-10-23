"""Compact, deterministic audit of which data actually influenced a package."""

from __future__ import annotations

import re
from collections import Counter

from goal_analysis.agents import canonical_sha256

VERSION = "arthur-decision-data-usage-v3"
STATUSES = {"USED_DIRECT", "USED_RANKING", "USED_REVIEW", "STORED_ONLY", "MISSING"}


def _entry(status, role, *, observations=0, reason=None):
    if status not in STATUSES or type(observations) is not int or observations < 0:
        raise ValueError("Invalid decision data usage entry")
    result = {"status": status, "role": role, "observations": observations}
    if reason:
        result["reason"] = reason
    return result


def _available_stats_facts(report):
    collection = (report.get("sports_supplement", {}).get("the_stats_api") or {})
    counts = Counter()
    for record in (collection.get("context_by_fixture") or {}).values():
        if not isinstance(record, dict):
            continue
        for fact in record.get("facts", []):
            if isinstance(fact, dict) and fact.get("status") == "AVAILABLE":
                counts[str(fact.get("category"))] += 1
    return counts


def _positive(value):
    return type(value) is int and value > 0


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _history_usage(report, artifacts, models, history):
    """Recorded fit inputs and selected DAILY heuristic evidence are distinct.

    These are correspondence traces, not independent dataset/PIT validation.
    A prediction alone does not prove which historical rows a model consumed.
    """
    manifests = {m['dataset_id']: m for model in models.values() if isinstance(model, dict)
        for m in model.get('training_datasets', {}).get('manifests', []) if isinstance(m, dict)
        and _positive(m.get('row_count')) and all(_digest(m.get(k)) for k in
            ('dataset_id', 'rows_sha256', 'lineage_sha256'))}
    history_hash = artifacts.get('history', {}).get('snapshot_sha256')
    daily_evidence = set()
    if history and _digest(history_hash):
        for group in ('tickets', 'recommendations', 'profile_proposals', 'preview_tickets'):
            for card in report.get(group, []):
                if card.get('profile_id') != 'daily223':
                    continue
                for leg in card.get('legs', []):
                    for evidence in leg.get('evidence', []):
                        if (evidence.get('category') in {'historical', 'venue_form'}
                                and evidence.get('source_id') == 'history-sha256:'+history_hash
                                and evidence.get('evidence_id')):
                            # `fresh` is a per-view display annotation added by
                            # the existing solver, not a second historical fact.
                            daily_evidence.add(canonical_sha256({k: v for k, v in evidence.items() if k != 'fresh'}))
    status = 'USED_DIRECT' if manifests or daily_evidence else 'STORED_ONLY' if history else 'MISSING'
    return status, manifests, daily_evidence


def _weather_usage(report, artifacts):
    from goal_analysis.agents.weather_expert import _stamp, _weather

    expert = report.get('expert_advisory', {})
    rows = expert.get('weather', {}).get('fixtures', [])
    fixtures = {f.get('fixture_id'): f for f in artifacts.get('fixtures', {}).get('records', [])}
    cutoff = _stamp(report.get('finished_at') or expert.get('observed_at'))
    accepted, teams = {}, {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        fid = row.get('fixture_id'); kickoff = _stamp(fixtures.get(fid, {}).get('kickoff'))
        if cutoff is None or kickoff is None:
            continue
        # Only the existing expert's accepted, normalized weather is review
        # evidence. An UNKNOWN row, raw forecast or empty team shell is not.
        value, _ = _weather(row.get('weather'), kickoff=kickoff, cutoff=cutoff, forecast=True)
        if value is None:
            continue
        previous = accepted.get(fid)
        if previous is not None and previous != row:
            accepted[fid] = None  # divergent same-fixture records cannot win by list order
        elif fid not in accepted:
            accepted[fid] = row
    accepted = {fid: row for fid, row in accepted.items() if row is not None}
    for fid, row in accepted.items():
        for side, team in row.get('teams', {}).items():
            if isinstance(team, dict) and _positive(team.get('matched_history_count')):
                teams[fid, side] = team
    ranked = _positive(report.get('expert_ranking', {}).get('weather_weighted_options'))
    validated = any(r.get('live_weight_eligible') is True for r in accepted.values())
    status = 'USED_RANKING' if ranked and validated else 'USED_REVIEW' if accepted else 'MISSING'
    history_status = ('USED_RANKING' if ranked and validated and teams else
                      'USED_REVIEW' if teams else 'MISSING')
    return status, history_status, len(accepted), len(teams), len(rows)


def _stats_usage(facts, ranked):
    # These counters are emitted by the existing option-ranking application.
    # Mere AVAILABLE category metadata proves storage, not review or application.
    return 'USED_RANKING' if _positive(ranked) else 'STORED_ONLY' if facts else 'MISSING'


def _movement_evidence_id(movement):
    """Read the existing adapter's evidence; never calculate or change a signal."""
    if not isinstance(movement, dict) or movement.get("status") not in {
        "VERIFIED_RISE", "NO_SIGNIFICANT_RISE",
    }:
        return None
    clocks = ('baseline_observed_at', 'current_observed_at') if movement.get('time_basis') == 'LOCAL_SNAPSHOT_OBSERVATIONS' else ('baseline_quoted_at', 'current_quoted_at')
    if not all(movement.get(key) for key in (
        "source_run_id", "source_snapshot_sha256", *clocks, "baseline_quote_source_id", "current_quote_source_id",
    )):
        return None
    try:
        expected = "odds-movement:" + canonical_sha256(
            {key: value for key, value in movement.items() if key != "evidence_id"})
    except (TypeError, ValueError):
        return None
    return expected if movement.get("evidence_id") == expected else None


def _local_odds_history_usage(report, movement_candidates):
    # The current quote snapshot is an output saved for a FUTURE comparison.
    # Only adapter-produced comparisons establish historical coverage now.
    available = {
        identifier for candidate in (movement_candidates or [])
        if isinstance(candidate, dict)
        if (identifier := _movement_evidence_id(candidate.get("odds_movement")))
    }
    selected = set()
    for group in ("tickets", "recommendations", "profile_proposals", "preview_tickets"):
        for item in report.get(group, []) or []:
            for leg in item.get("legs", []):
                analysis = leg.get("contrarian_analysis")
                if isinstance(analysis, dict):
                    identifier = _movement_evidence_id(analysis.get("odds_movement"))
                    if identifier:
                        selected.add(identifier)
    # Published contrarian legs retain their comparison proof, including in
    # older reports without the optional candidate telemetry.
    available.update(selected)
    role = "line_movement_and_contrarian_value"
    if not available:
        return _entry("MISSING", role, reason="NO_VERIFIED_COMPARABLE_HISTORY")
    ranked = report.get("tipster_club", {}).get("contrarian_eligible_source_candidates", 0)
    # This existing diagnostic is emitted after _ranked has accepted the
    # source candidates; a successful history read alone is not usage evidence.
    ranking_used = bool(selected) or (type(ranked) is int and ranked > 0)
    return _entry(
        "USED_RANKING" if ranking_used else "STORED_ONLY", role,
        observations=len(available),
        reason=("VERIFIED_COMPARISONS_USED_BY_CONTRARIAN_RANKING" if ranking_used
                else "COMPARABLE_HISTORY_AVAILABLE_NO_CONFIRMED_RANKING_USE"),
    )


def build_decision_data_usage(report, artifacts, *, candidate_review=None, feedback_used=False,
                             odds_movement_candidates=None):
    """Describe influence, not mere collection, without mutating either input."""
    fixtures = artifacts.get("fixtures", {}).get("records", [])
    candidates = artifacts.get("candidate_input", {}).get("candidates", [])
    history = artifacts.get("history", {}).get("records", [])
    models = artifacts.get("numeric_models", {})
    model_predictions = sum(len(value.get("predictions", [])) for value in models.values()
                            if isinstance(value, dict))
    review = candidate_review if isinstance(candidate_review, dict) else {}
    expert = report.get("expert_advisory", {})
    ranking = report.get("expert_ranking", {})
    stats_ranking = ranking.get("the_stats_api", {})
    stats_facts = _available_stats_facts(report)
    weather_status, weather_history_status, weather_count, weather_team_analyses, weather_rows_seen = _weather_usage(report, artifacts)
    motivation_rows = expert.get("motivation", {}).get("fixtures", [])
    grok = report.get("grok_review", {})
    changed = grok.get("changed_profiles", [])

    xg_status = _stats_usage(stats_facts.get('team_season_stats'), stats_ranking.get('evidence_attached_options'))
    player_status = _stats_usage(stats_facts.get('player_season_stats'), stats_ranking.get('player_context_legs'))
    absence_count = stats_facts.get('injuries_suspensions', 0) + stats_facts.get('lineup', 0)
    absence_status = _stats_usage(absence_count, stats_ranking.get('player_availability_adjusted_legs'))
    history_status, consumed_manifests, daily_history_evidence = _history_usage(report, artifacts, models, history)
    motivation_count = sum(bool(row.get("standing_evidence") or row.get("human_factors")
                                or row.get("standings_context"))
                           for row in motivation_rows if isinstance(row, dict))
    grok_status = ("USED_DIRECT" if changed else "USED_REVIEW"
                   if grok.get("status") in {"COMPLETE", "CACHED"} else "MISSING")

    sources = {
        "fixture_calendar": _entry("USED_DIRECT" if fixtures else "MISSING",
            "candidate_universe_and_lockout", observations=len(fixtures)),
        "current_odds": _entry("USED_DIRECT" if candidates else "MISSING",
            "market_products_prices_and_ticket_odds", observations=len(candidates)),
        ("the_stats_match_history" if artifacts.get('history', {}).get('provider') == 'the_stats_api'
         else "api_football_match_history"): _entry(history_status,
            "numeric_models_and_historical_form", observations=len(history),
            reason='RECORDED_TRAINING_OR_SELECTED_DAILY_FORM_EVIDENCE' if history_status == 'USED_DIRECT'
            else 'INPUT_SUPPLIED_WITHOUT_CONFIRMED_CONSUMPTION'),
        "numeric_model_history": _entry('USED_DIRECT' if consumed_manifests else 'STORED_ONLY' if history else 'MISSING',
            'recorded_training_dataset_inputs_not_prediction_or_selection_proof', observations=len(consumed_manifests)),
        "daily_historical_form": _entry('USED_DIRECT' if daily_history_evidence else 'MISSING',
            'selected_daily223_form_evidence_bound_to_input_snapshot', observations=len(daily_history_evidence)),
        "numeric_models": _entry("USED_DIRECT" if model_predictions else "MISSING",
            "candidate_probabilities_and_model_profiles", observations=model_predictions),
        "local_odds_history": _local_odds_history_usage(report, odds_movement_candidates),
        "the_stats_team_xg": _entry(xg_status, "season_xg_cross_check",
            observations=stats_facts.get("team_season_stats", 0)),
        "the_stats_player_stats": _entry(player_status, "key_scorer_attack_share",
            observations=stats_facts.get("player_season_stats", 0)),
        "the_stats_availability": _entry(absence_status,
            "active_absence_or_confirmed_lineup_check",
            observations=stats_facts.get("injuries_suspensions", 0) + stats_facts.get("lineup", 0)),
        "open_meteo_weather": _entry(weather_status, "market_specific_weather_evidence",
            observations=weather_count),
        "team_specific_weather_history": _entry(
            weather_history_status,
            "home_away_opponent_strength_matched_weather_holdout",
            observations=weather_team_analyses),
        "motivation_and_news": _entry("USED_REVIEW" if motivation_count else "MISSING",
            "grok_package_context_and_verified_repair", observations=motivation_count),
        "astra_codex_candidate_review": _entry(
            "USED_RANKING" if review.get("status") == "COMPLETE" else "MISSING",
            "candidate_evidence_and_caution", observations=len(review.get("reviews", []))),
        "grok_package_review": _entry(grok_status, "whole_package_critique_and_bounded_repair",
            observations=len(grok.get("critiques", []))),
        "settled_performance_feedback": _entry(
            "USED_REVIEW" if feedback_used else "STORED_ONLY",
            "review_context_not_automatic_training", observations=0),
        "the_stats_and_football_data_archives": _entry("STORED_ONLY",
            "future_point_in_time_training_and_plausibility", observations=0),
        "the_stats_current_odds": _entry(
            "STORED_ONLY" if stats_facts.get("match_odds") else "MISSING",
            "shadow_quote_comparison_and_grok_context_not_live_pricing",
            observations=stats_facts.get("match_odds", 0)),
    }
    context_review = report.get('candidate_context_review', {})
    if context_review:
        for name in ('players', 'xg', 'schedule', 'travel', 'rotation', 'weather'):
            rows = [r for r in context_review.get('usage', []) if r.get('category') == name]
            considered = [r for r in rows if r.get('source_status') == 'SOURCED'
                          and r.get('response_considered') and r.get('context_evidence_cited')]
            applied = [r for r in considered if r.get('existing_caution_assessment_applies')]
            status = ('USED_RANKING' if applied else 'USED_REVIEW' if considered else 'STORED_ONLY'
                      if any(r.get('source_status') == 'SOURCED' for r in rows) else 'MISSING')
            sources['preselection_context_'+name] = _entry(status,
                'source_cited_candidate_review_not_calibrated_probability_or_causal_gain',
                observations=len(considered))
    return {
        "version": VERSION,
        "semantics": "INFLUENCE_NOT_COLLECTION",
        'usage_scope': 'PACKAGE_STAGE_TRACES_NOT_SELECTED_LEG_TRUTH_OR_POINT_IN_TIME_CERTIFICATION',
        'coverage': {'weather_rows_processed': weather_rows_seen, 'weather_forecasts_reviewed': weather_count,
            'weather_team_histories_reviewed': weather_team_analyses, 'available_stats_facts': dict(stats_facts),
            'history_rows_supplied': len(history), 'recorded_training_dataset_ids': sorted(consumed_manifests),
            'selected_daily_history_evidence_count': len(daily_history_evidence),
            'available_facts_alone_do_not_prove_application': True},
        "sources": sources,
        "counts": dict(sorted(Counter(item["status"] for item in sources.values()).items())),
        "daily223_protected": True,
    }
