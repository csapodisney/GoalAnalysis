"""Bind the real player specialist to options and final legs, SHADOW only.

No network, archive admission, fits, live option mutation, probability change or
publication veto. The underlying specialist owns every source/control gate.
"""
import math
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs.player_specialist_shadow import _supports, inspect_player_specialist
from goal_analysis.jobs.ticket_evidence import GROUPS, PROBABILITY_KEYS
from goal_analysis.normalization.canonical import utc

VERSION = 'source-bound-player-option-shadow-v1'
PRODUCT = ('fixture_id', 'market_key', 'selection_key', 'period', 'bookmaker', 'region',
           'currency', 'decimal_price', 'quote_source_id', 'quote_source_sha256',
           'quote_observed_at', 'quoted_at', 'settlement')


def _product(leg):
    return {field: leg.get(field) for field in PRODUCT}


def _aliases(leg):
    return {leg[k] for k in ('candidate_id', 'source_candidate_id')
            if isinstance(leg.get(k), str) and leg[k]}


def review_player_options(root, report, options, enriched, models, *, as_of):
    """Compute an explicitly hypothetical rank from exact verified selections.

    Unmatched legs contribute factor 1, never disappear from the denominator.
    DAILY is exempt even under a mismatched option dictionary key. No actual
    probability, price, ticket, catalogue or publication order is overwritten.
    """
    cutoff = utc(as_of)
    if not isinstance(report, dict) or not isinstance(options, dict):
        raise TypeError('BOUNDED_PLAYER_OPTION_INPUT_REQUIRED')
    count = sum(len(rows) for rows in options.values() if isinstance(rows, list))
    if count > 10000 or any(not isinstance(rows, list) for rows in options.values()):
        raise ValueError('BOUNDED_PLAYER_OPTION_INPUT_REQUIRED')
    if sum(len((option.get('recommendation') or option.get('ticket') or {}).get('legs', []))
           for rows in options.values() for option in rows) > 10000:
        raise ValueError('BOUNDED_PLAYER_OPTION_LEGS_REQUIRED')
    cards = [(group, index, card) for group in GROUPS
             for index, card in enumerate(report.get(group, []))]
    if sum(len(card.get('legs', [])) for _, _, card in cards) > 10000:
        raise ValueError('BOUNDED_PLAYER_CARD_INPUT_REQUIRED')
    specialist = inspect_player_specialist(root, enriched, models, as_of=cutoff, enabled=True)
    effects = specialist.get('supported_selections', [])
    views = specialist.get('fixture_evidence', {})
    fixtures = {f['fixture_id']: f for f in enriched.get('fixtures', [])}
    # Index exact product AND source alias, not a repeated scan of all effects.
    by_product_alias = {}
    for index, effect in enumerate(effects):
        key = canonical_sha256(_product(effect['quote']))
        for alias in _aliases(effect['quote']):
            by_product_alias.setdefault((key, alias), set()).add(index)
    view_hashes = {fid: canonical_sha256(view) for fid, view in views.items()}

    def leg_review(leg, protected):
        view = views.get(leg.get('fixture_id'), {})
        product_hash = canonical_sha256(_product(leg))
        matches = set().union(*(by_product_alias.get((product_hash, alias), set())
                                for alias in _aliases(leg)))
        # Reference the source events once per fixture; do not copy a large
        # roster/event graph into every alias of every option and selected leg.
        evidence = {'fixture_evidence_ref': ['specialist', 'fixture_evidence', leg.get('fixture_id')],
            'fixture_evidence_sha256': view_hashes.get(leg.get('fixture_id')),
            'gaps': deepcopy(view.get('gaps', {})),
            'lineup_status': view.get('lineup_status', 'MISSING'),
            'scope': view.get('scope', 'NO_VALIDATED_CONTEXT')}
        status, effect, factor = 'NO_VERIFIED_KEY_ABSENCE', None, 1.
        if protected:
            status = 'DAILY_223_EXEMPT'
        elif len(matches) == 1:
            effect = effects[next(iter(matches))]
            factor = effect['applied_score_multiplier']
            status = 'SOURCE_CONTROL_PRICE_BOUND_SHADOW_SUPPORT'
        elif len(matches) > 1:
            status = 'AMBIGUOUS_QUOTE_SUPPORT_NO_CHANGE'
        elif not view.get('source_events'):
            status = 'MISSING_VALIDATED_PLAYER_CONTEXT'
        elif view.get('conflicting_availability'):
            status = 'CONFLICTING_AVAILABILITY_NO_CHANGE'
        elif view.get('signals'):
            key = PROBABILITY_KEYS.get((leg.get('market_key'), leg.get('selection_key')))
            fixture = fixtures.get(leg.get('fixture_id'))
            supported = bool(fixture and key and all(_supports(key, s, fixture) for s in view['signals']))
            status = ('WITHHELD_CONTROL_PRICE_OR_EDGE_UNPROVEN' if supported else
                      'OPPOSED_OR_UNSUPPORTED_DIRECTION_NO_CHANGE')
        elif evidence['lineup_status'] == 'PREDICTED':
            status = 'PREDICTED_XI_INFORMATION_ONLY_NO_CHANGE'
        return {'leg_sha256': canonical_sha256(leg), 'product': _product(leg),
            'candidate_id': leg.get('candidate_id'), 'source_candidate_id': leg.get('source_candidate_id'),
            'status': status, 'shadow_score_multiplier': factor, 'player_evidence': evidence,
            'bound_support': deepcopy(effect), 'probability_adjustment': 0,
            'live_decision_effect': False}

    option_reviews = []
    for profile, rows in options.items():
        for index, option in enumerate(rows):
            card = option.get('recommendation') or option.get('ticket') or {}
            protected = profile == 'daily223' or card.get('profile_id') == 'daily223'
            legs = [leg_review(leg, protected) for leg in card.get('legs', [])]
            quality = option.get('quality')
            valid = type(quality) in (int, float) and math.isfinite(quality) and quality >= 0
            factor = math.exp(math.fsum(math.log(leg['shadow_score_multiplier'])
                for leg in legs) / len(legs)) if legs and valid and not protected else 1.
            hypothetical = quality * factor if valid else None
            if hypothetical is not None and not math.isfinite(hypothetical):
                hypothetical, factor = None, 1.
            option_reviews.append({'profile_id': profile, 'option_index': index,
                'option_sha256': canonical_sha256(option), 'baseline_quality': quality if valid else None,
                'shadow_quality': hypothetical, 'shadow_score_multiplier': factor,
                'protected_daily223': protected, 'legs': legs})
    rankings = []
    for profile in options:
        eligible = [r for r in option_reviews if r['profile_id'] == profile and r['shadow_quality'] is not None]
        baseline = [r['option_index'] for r in sorted(eligible, key=lambda r: (-r['baseline_quality'], r['option_index']))]
        shadow = [r['option_index'] for r in sorted(eligible, key=lambda r: (-r['shadow_quality'], r['option_index']))]
        rankings.append({'profile_id': profile, 'baseline_order': baseline, 'shadow_order': shadow,
                         'rank_order_changed': baseline != shadow})
    card_reviews = [{'group': group, 'card_index': index, 'profile_id': card.get('profile_id'),
        'card_sha256': canonical_sha256(card), 'legs': [leg_review(leg, card.get('profile_id') == 'daily223')
            for leg in card.get('legs', [])]} for group, index, card in cards]
    return {'version': VERSION, 'mode': 'SHADOW', 'as_of': cutoff.isoformat(),
        'implementation_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
        'status': 'COMPLETE', 'live_promotion': False, 'live_decision_effect': False,
        'new_external_requests': 0, 'new_model_fits': 0, 'probability_adjustment': 0,
        'specialist': specialist, 'option_reviews': option_reviews, 'rankings': rankings,
        'card_reviews': card_reviews, 'input_options_sha256': canonical_sha256(options),
        'summary': {'options_reviewed': len(option_reviews), 'cards_reviewed': len(cards),
            'supported_selections': len(effects),
            'hypothetically_weighted_options': sum(r['shadow_quality'] is not None and
                r['shadow_quality'] != r['baseline_quality'] for r in option_reviews),
            'profiles_with_hypothetical_rank_change': sum(r['rank_order_changed'] for r in rankings)}}
