"""Canonical current-contract research output; no provider or wager operations."""

import math
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.portfolio import MODEL_PROFILE_IDS, PROFILE_IDS
from goal_analysis.engine.diversification import ticket_signature
from goal_analysis.engine.publication_window import allows_new_ticket
from goal_analysis.normalization.live_identity import fixture_identity
from goal_analysis.operating_policy import (
    CAUTIOUS_PROFILE_IDS,
    MAX_GENERATED_TICKETS,
    MIN_DAILY_TICKETS,
    current_contract,
)
from goal_analysis.team.metrics import _quote_valid
from goal_analysis.tipsters.catalog import CATALOG_VERSION, TIPSTER_BY_ID
from goal_analysis.tipsters.recommendations import PRODUCTS, _contrarian_analysis

GROUPS = ('profile_proposals', 'recommendations', 'tickets')
STATUSES = {'READY', 'DRAFT', 'MODEL_READY', 'REVIEWED'}
SCOPES = {'LIVE', 'PAPER', 'PAPER_AUDITION', 'RESEARCH'}
QUOTE_FIELDS = ('fixture_id', 'market_key', 'selection_key', 'period', 'line', 'handicap',
                'decimal_price', 'bookmaker', 'region', 'currency', 'quote_provider',
                'quote_source_id', 'quote_source_sha256', 'quote_observed_at',
                'quote_timestamp_raw', 'quoted_at', 'quote_time_basis', 'settlement',
                'provider_market_id', 'provider_bookmaker', 'kickoff', 'home_team', 'away_team',
                'source_provider', 'provider_fixture_id', 'provider_league_id', 'provider_season_id',
                'provider_home_team_id', 'provider_away_team_id', 'api_football_fixture_id',
                'api_football_home_team_id', 'api_football_away_team_id')
CORE_MARKETS = {
    'kronikas': ('totals_2_5', 'over', 'FULL_TIME'),
    'ritmusor': ('btts', 'yes', 'FULL_TIME'),
    'parharcmester': ('h2h', None, 'FULL_TIME'),
    'orszem': ('totals_2_5', 'under', 'FULL_TIME'),
    'merlin': ('h2h', 'draw', 'FULL_TIME'),
    'h1_over05': ('totals_0_5', 'over', 'FIRST_HALF'),
}


def _signature(card):
    legs = []
    for leg in card.get('legs', []):
        if not isinstance(leg, dict):
            return None
        row = {key: leg.get(key) for key in QUOTE_FIELDS}
        row['source_candidate_id'] = (leg.get('quote_candidate_id') or leg.get('source_candidate_id')
                                      or leg.get('candidate_id'))
        legs.append(row)
    try:
        return canonical_sha256(sorted(legs, key=canonical_sha256)) if legs else None
    except (TypeError, ValueError):
        return None


def _cards(report):
    for group in GROUPS:
        for card in report.get(group, []):
            if isinstance(card, dict):
                yield group, card


def capture_profile_decisions(report):
    """Keep detached own decisions before allocation or optional review removes them."""
    if not current_contract(report.get('settings', report.get('policy', {}))):
        return report
    saved = report.setdefault('profile_decision_sources', [])
    seen = {(item['card'].get('profile_id'), _signature(item['card'])) for item in saved}
    for group, card in _cards(report):
        if (card.get('status') not in STATUSES or not card.get('legs')
                or card.get('construction_verified') is False or card.get('odds_pending') is True):
            continue
        key = (card.get('profile_id'), _signature(card))
        if key[1] is not None and key not in seen:
            saved.append({'source_group': group, 'card': deepcopy(card)})
            seen.add(key)
    return report


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _fallback(card):
    proof = card.get('selection_diagnostics', {}).get('odds_exception', {})
    return (isinstance(proof, dict) and card.get('profile_id') in CAUTIOUS_PROFILE_IDS
            and proof.get('status') == 'APPLIED' and proof.get('applied') is True
            and proof.get('full_day_coverage') is True
            and type(proof.get('primary_options_count')) is int and proof['primary_options_count'] == 0
            and proof.get('reason') == 'NO_OWN_15_40_OPTION_AFTER_FULL_DAY_SEARCH')


def _own_shape(card, cutoff):
    profile, legs = card['profile_id'], card['legs']
    persona = TIPSTER_BY_ID.get(profile)
    if persona is not None:
        if (card.get('catalog_version') != CATALOG_VERSION
                or not persona['min_legs'] <= len(legs) <= persona['max_legs']):
            return False
        policy = persona['selection_policy']
        for leg in legs:
            product = (leg['market_key'], leg['selection_key'])
            allowed = policy.get('allowed_market_keys')
            if allowed is not None and product not in {PRODUCTS[key] for key in allowed}:
                return False
            probabilities = leg.get('model_details', {}).get('shared_model_probabilities', {})
            if (len(probabilities) < policy['min_models']
                    or any(source not in probabilities for source in policy.get('required_models', []))
                    or any(not _number(value) or not 0 <= value <= 1 for value in probabilities.values())):
                return False
            probability, edge, spread = (leg.get('model_probability'), leg.get('estimated_edge'),
                                         leg.get('model_stability'))
            total_weight = sum(persona['model_weights'].get(source, 0) for source in probabilities)
            expected_probability = (sum(persona['model_weights'].get(source, 0) * value
                                         for source, value in probabilities.items()) / total_weight
                                    if total_weight else None)
            if (not _number(probability) or probability < policy['min_probability'] - 1e-12
                    or expected_probability is None
                    or not math.isclose(probability, expected_probability, rel_tol=1e-9)
                    or not _number(edge) or not math.isclose(edge, probability * leg['decimal_price'] - 1, rel_tol=1e-9, abs_tol=1e-12)
                    or policy['min_edge'] is not None and (
                        not _number(edge) or edge < policy['min_edge'] - 1e-12)
                    or spread is not None and (not _number(spread) or spread > policy['max_model_spread'])):
                return False
            if policy.get('contrarian_value'):
                quote = {**leg, 'candidate_id': leg.get('quote_candidate_id')
                         or leg.get('source_candidate_id') or leg['candidate_id']}
                if not leg.get('contrarian_analysis') or _contrarian_analysis(
                        {'quote': quote, 'probability': probability, 'components': probabilities},
                        persona, cutoff) is None:
                    return False
            minimum_disagreement = policy.get('min_market_disagreement')
            disagreement = leg.get('model_details', {}).get('model_market_disagreement')
            if minimum_disagreement is not None and (
                    not _number(disagreement) or disagreement < minimum_disagreement - 1e-12):
                return False
    elif profile in CORE_MARKETS:
        expected = CORE_MARKETS[profile]
        if not 2 <= len(legs) <= 6 or profile == 'kronikas' and len(legs) < 3:
            return False
        if any(any(expected[index] is not None and leg[key] != expected[index]
                   for index, key in enumerate(('market_key', 'selection_key', 'period')))
               for leg in legs):
            return False
        if profile == 'parharcmester' and any(leg['selection_key'] not in {'home', 'away'} for leg in legs):
            return False
    elif profile in MODEL_PROFILE_IDS:
        if not 1 <= len(legs) <= 3:
            return False
        if any(not _number(leg.get('model_probability')) or not .40 <= leg['model_probability'] <= 1
               or not _number(leg.get('estimated_edge')) or leg['estimated_edge'] < .03 - 1e-12
               or not math.isclose(leg['estimated_edge'], leg['model_probability'] * leg['decimal_price'] - 1,
                                   rel_tol=1e-9, abs_tol=1e-12)
               or profile == 'gauss' and (not _number(leg.get('model_stability'))
                                          or not 0 <= leg['model_stability'] <= .08)
               for leg in legs):
            return False
    return True


def _invalid(card, report, cutoff):
    if card.get('profile_id') not in PROFILE_IDS:
        return 'UNKNOWN_PROFILE'
    if card.get('status') not in STATUSES or card.get('construction_verified') is False:
        return 'NOT_A_COMPLETE_PROFILE_DECISION'
    legs = card.get('legs')
    if not isinstance(legs, list) or not legs or any(not isinstance(leg, dict) for leg in legs):
        return 'EMPTY_OR_INVALID_LEGS'
    if card.get('date', report['date']) != report['date']:
        return 'WRONG_TARGET_DAY'
    if not allows_new_ticket(card, cutoff):
        return 'PREMATCH_LOCKOUT'
    identities = [leg.get('fixture_id') for leg in legs]
    if any(not isinstance(value, str) for value in identities) or len(set(identities)) != len(identities):
        return 'DUPLICATE_OR_INVALID_FIXTURE'
    try:
        for leg in legs:
            fixture_identity(leg)
            kickoff = datetime.fromisoformat(leg['kickoff'])
            if kickoff.astimezone(ZoneInfo('Europe/Berlin')).date().isoformat() != report['date']:
                return 'WRONG_FIXTURE_DAY'
            if (not _number(leg.get('decimal_price')) or not 1 < leg['decimal_price'] <= 1000
                    or not all(isinstance(leg.get(key), str) and leg[key].strip()
                               for key in ('candidate_id', 'market_key', 'selection_key', 'period',
                                           'bookmaker', 'region', 'currency'))
                    or not _quote_valid({'fixture_id': leg['fixture_id'],
                                         'market': leg['period'] + ':' + leg['market_key'],
                                         'selection': leg['selection_key'], 'quote_provenance': leg})):
                return 'INVALID_PRICE_OR_QUOTE_SOURCE'
        if len({(leg['bookmaker'], leg['region'], leg['currency']) for leg in legs}) != 1:
            return 'MIXED_QUOTE_COHORT'
        product = math.prod(leg['decimal_price'] for leg in legs)
        if not _number(card.get('combined_price')) or not math.isclose(product, card['combined_price'], rel_tol=1e-9):
            return 'COMBINED_PRICE_MISMATCH'
        if card['profile_id'] == 'daily223':
            band = card.get('odds_band', card.get('daily223_odds_band'))
            tolerance = .02 if band == 'NEAR_TARGET' else 0
            prices = sorted(leg['decimal_price'] for leg in legs)
            if (len(legs) != 3 or band not in {'STRICT', 'NEAR_TARGET'}
                    or any(price + 1e-12 < floor * (1 - tolerance)
                           for price, floor in zip(prices, (2, 2, 3)))
                    or product + 1e-12 < 12 * (1 - tolerance)):
                return 'DAILY223_CONSTRUCTION_INVALID'
        elif not (15 <= product <= 40 or 5 <= product < 15 and _fallback(card)):
            return 'OUTSIDE_OWN_APPROVED_ODDS_RANGE'
        if not _own_shape(card, cutoff):
            return 'OWN_STRATEGY_CONSTRUCTION_INVALID'
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return 'INVALID_NATIVE_IDENTITY_OR_PRODUCT'
    return None


def _exact_ready(card, report):
    if card.get('status') != 'READY':
        return False
    binding = card.get('profile_context_review', {})
    if (binding.get('status') == 'SOURCE_BOUND'
            and len(binding.get('legs', [])) == len(card['legs'])
            and all(row.get('status') == 'EXACT_PROFILE_CONTEXT_REVIEW_BOUND' for row in binding['legs'])):
        return True
    if report.get('astra', {}).get('status') != 'COMPLETE':
        return False
    for leg in card['legs']:
        answer = leg.get('astra_review', {})
        identifiers = {leg.get(key) for key in ('candidate_id', 'source_candidate_id', 'quote_candidate_id')}
        if (not isinstance(answer.get('candidate_id'), str) or answer['candidate_id'] not in identifiers
                or answer.get('assessment') not in {'support', 'caution'}):
            return False
    return True


def _materialize(card, source_group, report, plan):
    result = deepcopy(card)
    source_status = card['status']
    result['status'] = 'READY' if _exact_ready(card, report) else 'DRAFT'
    profile = card['profile_id']
    states = plan.get('profile_states', {})
    scope = card.get('publication_scope')
    if scope not in SCOPES:
        scope = 'PAPER_AUDITION' if states.get(profile) == 'PROBATION' else 'LIVE' if (
            profile in plan.get('live_profiles', [])) else 'RESEARCH'
    if states.get(profile) == 'PROBATION' and scope == 'LIVE':
        scope = 'PAPER_AUDITION'
    result['publication_scope'] = scope
    result['playable'] = (result['status'] == 'READY' and scope == 'LIVE'
                          and card.get('playable', source_group in {'tickets', 'profile_proposals'}) is True)
    result.setdefault('date', report['date'])
    result.setdefault('created_at', report['observed_at'])
    result['stake_eur'] = 1.0 if result['playable'] else None
    result['generation_source_status'] = source_status
    result['generation_source_group'] = source_group
    if not isinstance(result.get('ticket_id'), str) or not result['ticket_id']:
        result['ticket_id'] = 'generated-' + canonical_sha256([
            report.get('run_id', report['observed_at']), profile, _signature(card)])[:24]
    if result['status'] == 'DRAFT' and source_status != 'DRAFT':
        warning = 'EXACT_READY_SOURCE_REVIEW_NOT_ESTABLISHED'
        if warning not in result.setdefault('quality_warnings', []):
            result['quality_warnings'].append(warning)
    return result


def finalize_generated(report, settings, *, team_plan=None, options=None):
    """Build one counted collection, retaining paper decisions and truthful readiness."""
    if not current_contract(settings):
        return report
    report.setdefault('settings', deepcopy(settings))
    capture_profile_decisions(report)
    plan, options = team_plan or report.get('team', {}), options or {}
    cutoff = datetime.fromisoformat(report.get('finished_at', report['observed_at']))
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError('Generated-ticket cutoff must be timezone-aware')
    current = defaultdict(list)
    suspended = set()
    for group, card in _cards(report):
        if card.get('status') == 'SUSPENDED' or card.get('publication_scope') == 'SUSPENDED':
            suspended.add(card.get('profile_id'))
        if card.get('legs') and card.get('status') in STATUSES:
            current[(card.get('profile_id'), _signature(card))].append((group, card))
    captured = defaultdict(list)
    for source in report.get('profile_decision_sources', []):
        original = source['card']
        card, group = deepcopy(original), source['source_group']
        for _new_group, update in current.get((card.get('profile_id'), _signature(card)), []):
            old_scope = card.get('publication_scope')
            card.update(deepcopy(update))
            if old_scope in SCOPES - {'LIVE'} and card.get('publication_scope') == 'LIVE':
                card['publication_scope'] = old_scope
        captured[card.get('profile_id')].append((group, card))
    generated, excluded, signatures, identifiers = [], [], set(), set()
    roster = settings.get('enabled_profiles', PROFILE_IDS)
    roster = ['daily223', *[profile for profile in roster if profile != 'daily223']]
    for profile in dict.fromkeys(roster):
        if profile not in captured:
            continue
        if (profile in plan.get('blocked_profiles', [])
                or profile in suspended or plan.get('profile_states', {}).get(profile) == 'SUSPENDED'):
            excluded.append({'profile_id': profile, 'reason': 'SUSPENDED_PROFILE'})
            continue
        candidates = list(captured[profile])
        # Only this persona's independently constructed alternatives can resolve
        # a full bet duplicate. No new legs or prices are synthesized here.
        alternatives = []
        for option in options.get(profile, []):
            template = option.get('recommendation') or option.get('ticket') or {}
            if template.get('profile_id') == profile:
                alternatives.append(('options', template))
        failure = None
        for group, card in candidates + alternatives:
            failure = _invalid(card, report, cutoff)
            if failure:
                continue
            signature = ticket_signature(card)
            if signature in signatures:
                failure = 'DUPLICATE_FULL_BET_SIGNATURE'
                continue
            ticket = _materialize(card, group, report, plan)
            if ticket['ticket_id'] in identifiers:
                failure = 'DUPLICATE_TICKET_ID'
                continue
            if len(generated) >= MAX_GENERATED_TICKETS:
                failure = 'MAXIMUM_GENERATED_TICKETS_REACHED'
                break
            signatures.add(signature)
            identifiers.add(ticket['ticket_id'])
            generated.append(ticket)
            break
        else:
            excluded.append({'profile_id': profile, 'reason': failure or 'NO_COMPLETE_OWN_CARD'})
            continue
        if failure == 'MAXIMUM_GENERATED_TICKETS_REACHED':
            excluded.append({'profile_id': profile, 'reason': failure})
    report['generated_tickets'] = generated
    report['tickets'] = [ticket for ticket in generated
                         if ticket['publication_scope'] == 'LIVE' and ticket['status'] == 'READY']
    refresh_generation_capacity(report, excluded=excluded)
    return report


def refresh_generation_capacity(report, *, excluded=None):
    """Refresh counters after risk grading without choosing different tickets."""
    if not current_contract(report.get('settings',report.get('policy',{}))):
        return report
    generated=report['generated_tickets']
    if excluded is None:
        excluded=report.get('generation_capacity',{}).get('excluded_profiles',[])
    count = len(generated)
    report['generation_capacity'] = {
        'generated_count': count, 'minimum': MIN_DAILY_TICKETS, 'maximum': MAX_GENERATED_TICKETS,
        'shortfall': max(0, MIN_DAILY_TICKETS - count),
        'status': 'IN_TARGET' if MIN_DAILY_TICKETS <= count <= MAX_GENERATED_TICKETS else 'BELOW_TARGET',
        'status_counts': dict(Counter(ticket['status'] for ticket in generated)),
        'scope_counts': dict(Counter(ticket['publication_scope'] for ticket in generated)),
        'ready_live_count': sum(ticket['status'] == 'READY' and ticket['publication_scope'] == 'LIVE'
                                for ticket in generated),
        'ready_paper_count': sum(ticket['status'] == 'READY' and ticket['publication_scope'] != 'LIVE'
                                 for ticket in generated),
        'draft_count': sum(ticket['status'] == 'DRAFT' for ticket in generated),
        'excluded_profiles': excluded, 'financial_wager_cap_changed': False,
    }
    return report
