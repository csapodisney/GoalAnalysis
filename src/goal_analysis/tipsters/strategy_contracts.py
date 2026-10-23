"""Versioned declarations of actual club policy; no new filter or rank effect.

This compiles the live catalogue and its selector dependencies, rather than
maintaining a second editable rulebook. Ticket-specific lineage/publication and
promotion remain separate. In particular ANY competition type is not a newly
inferred cup preference or proof that every input league is sufficiently covered.
"""
import hashlib
import math
from copy import deepcopy
from pathlib import Path

from goal_analysis.agents import canonical_sha256
from goal_analysis.engine.model_recommendations import MARKETS
from goal_analysis.tipsters.catalog import CATALOG_VERSION, TIPSTER_CATALOG

VERSION = 'club-strategy-declarations-v1'
SOURCE_FILES = (
    'tipsters/strategy_contracts.py', 'tipsters/catalog.py', 'tipsters/contracts.py',
    'tipsters/profile_config.py', 'tipsters/profiles.json',
    'tipsters/recommendations.py', 'engine/model_recommendations.py',
    'engine/ticket_options.py', 'engine/ticket_scoring.py', 'engine/veto_overrides.py',
    'engine/daily_223.py', 'engine/portfolio.py', 'team/metrics.py',
    'normalization/live_identity.py', 'quote_metadata.py',
)


def _sources():
    root = Path(__file__).resolve().parent.parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def _number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('INVALID_CLUB_POLICY_NUMBER')


def _contract(persona, sources):
    p = deepcopy(persona)
    policy, weights, band = p['selection_policy'], p['model_weights'], p['target_odds_band']
    products = {**MARKETS, 'btts_no': ('btts', 'no')}
    allowed = policy['allowed_market_keys']
    if allowed is not None and (not allowed or len(set(allowed)) != len(allowed)
                               or not set(allowed) <= products.keys()):
        raise ValueError('INVALID_CLUB_MARKET_POLICY')
    if set(weights) != {'gauss', 'kepler', 'laplace'}:
        raise ValueError('EXPLICIT_NUMERIC_MODEL_WEIGHTS_REQUIRED')
    for value in weights.values():
        _number(value, 0, 1)
    if not math.isclose(sum(weights.values()), 1, abs_tol=1e-10, rel_tol=0):
        raise ValueError('MODEL_WEIGHTS_MUST_SUM_TO_ONE')
    if not (type(p['min_legs']) is type(p['max_legs']) is type(p['preferred_legs']) is int
            and 1 <= p['min_legs'] <= p['preferred_legs'] <= p['max_legs'] <= 6):
        raise ValueError('FIXED_CLUB_SHAPE_REQUIRED')
    _number(band['min'], 10, 50)
    _number(band['max'], band['min'], 50)
    for key in ('min_probability', 'max_model_spread'):
        _number(policy[key], 0, 1)
    if type(policy['min_models']) is not int or not 1 <= policy['min_models'] <= 3:
        raise ValueError('NUMERIC_MODEL_COUNT_REQUIRED')
    if any(model not in weights or weights[model] <= 0 for model in policy['required_models']):
        raise ValueError('REQUIRED_MODEL_DISABLED_OR_UNKNOWN')
    body = {
        'schema_version': 1, 'declaration_version': VERSION, 'catalog_version': CATALOG_VERSION,
        'profile_id': p['profile_id'], 'profile_name': p['name'],
        'scope': 'CURRENT_CLUB_PERSONA_POLICY', 'mode': 'SHADOW_DECLARATION',
        'preferred_markets': [{
            'model_market_key': key, 'market_key': products[key][0], 'selection_key': products[key][1],
            'period': 'FULL_TIME', 'heuristic_preference_bonus': policy['market_bonus'].get(key, 0.0)}
            for key in sorted(products if allowed is None else allowed)],
        'market_scope': 'ALL_SUPPORTED_PERSONA_MARKETS' if allowed is None else 'EXPLICIT_ALLOWED_MARKETS',
        'construction': {key: p[key] for key in ('min_legs', 'max_legs', 'preferred_legs',
                                                'preferred_combined_price', 'selection_mode')},
        'odds_band': deepcopy(band),
        'competition_type_policy': {'preference': 'UNSPECIFIED', 'allowed_scope': 'ANY_PROVIDER_COVERED_TYPE',
            'persona_type_filter_enforced': False, 'unknown_type_excluded_by_persona': False,
            'upstream_competition_coverage_still_required': True},
        'numeric_model_weights': weights,
        'evidence_thresholds': {key: deepcopy(policy[key]) for key in (
            'min_models', 'required_models', 'min_probability', 'max_model_spread',
            'min_edge', 'min_market_disagreement')},
        'selection_policy_snapshot': policy,
        'evidence_semantics': {
            'probability_channels': 'SHARED_GAUSS_KEPLER_LAPLACE_ESTIMATES_NOT_INDEPENDENT_PERSONA_MODELS',
            'ranking_channels': 'NORMALIZED_EVIDENCE_HEURISTICS_NOT_CALIBRATED_WIN_PROBABILITIES',
            'primary_history_gate': 'NO_SEPARATE_HARD_HISTORY_OR_VENUE_FLOOR_IN_PERSONA_RANKER',
            'alternate_history_gate': 'SHARED_HISTORY_SUPPORT_DEPENDS_ON_STRICTNESS_AND_HISTORY_POLICY',
            'alternate_gate_implementation': 'tipsters/recommendations.py:_history_option_support',
            'component_stability_gate_implementation': 'tipsters/recommendations.py:_models',
            'model_component_weights_renormalized_over_available_positive_weights': True},
        'source_bound_exceptions': [{
            'kind': 'HIGH_ODDS_SINGLE_SELECTION',
            'authority': 'engine/veto_overrides.py:verified_veto_override',
            'scope': 'EXACT_VERIFIED_SELECTION_ONLY', 'may_relax': ['normal_leg_count', 'upper_profile_odds_band'],
            'other_numeric_model_and_source_gates_unchanged': True}],
        'shared_constraints': ['EXACT_PROVIDER_FIXTURE_AND_MARKET_IDENTITIES',
            'VERIFIED_SOURCE_PRICES', 'DISTINCT_FIXTURES', 'SAME_BOOKMAKER_REGION_CURRENCY',
            'SHARED_PREPUBLICATION_LOCKOUT_AND_ELIGIBILITY_GATES', 'EXPLICIT_ABSTENTION_IF_NO_COMBINATION'],
        'effective_run_dependencies': ['strictness', 'history_enrichment_policy', 'candidate_source_snapshot',
                                       'upstream_fixture_and_quote_admission', 'portfolio_allocation'],
        'source_style': deepcopy(p['source_style']), 'implementation_files_sha256': sources,
        'ticket_specific_coverage_or_calibration_certified': False, 'decision_effect': False,
        'old_ticket_contract_binding_or_relabelling': False,
    }
    digest = canonical_sha256(body)
    return {**body, 'contract_sha256': digest, 'version_id': p['profile_id'] + '-v1-' + digest[:16]}


def club_strategy_contracts():
    """Detached declarations from actual policy; fixtures, LLM and caller overrides are not inputs."""
    sources = _sources()  # One shared source pass per registry, not per persona.
    profiles = [p['profile_id'] for p in TIPSTER_CATALOG]
    if len(set(profiles)) != len(profiles) or any(not p.startswith('tipster_') for p in profiles):
        raise ValueError('UNIQUE_CLUB_ONLY_IDENTITIES_REQUIRED')
    contracts = [_contract(persona, sources) for persona in TIPSTER_CATALOG]
    registry = {'version': VERSION, 'mode': 'SHADOW_DECLARATION', 'status': 'AVAILABLE', 'scope': 'CLUB_PERSONAS_ONLY',
                'contracts': contracts, 'daily223_included': False, 'new_eligibility_or_ranking_effect': False}
    return {**registry, 'registry_sha256': canonical_sha256(registry)}


def matches_current_contract(value):
    """A self-signed relaxed snapshot has no authority; this is an audit, never a veto."""
    if not isinstance(value, dict):
        return False
    expected = next((c for c in club_strategy_contracts()['contracts']
                     if c['profile_id'] == value.get('profile_id')), None)
    return expected is not None and value == expected
