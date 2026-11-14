"""Explicit new-ticket rules; absent markers retain historical interpretation."""
from __future__ import annotations

import json
import math
from copy import deepcopy
from decimal import Decimal

VERSION = 'TWELVE_MAIN_MIN10_SIX_FIXTURES_V1'
MINIMUM_COMBINED_PRICE = 10.0
MAXIMUM_DISTINCT_FIXTURES = 6
PREFERRED_COMBINED_PRICE = (15.0, 20.0)


def product_contract():
    """A detached, exact version marker, not an editable settings overlay."""
    return {'version': VERSION, 'main_slot_count': 12,
            'minimum_combined_price': MINIMUM_COMBINED_PRICE,
            'maximum_combined_price': None,
            'maximum_distinct_fixtures': MAXIMUM_DISTINCT_FIXTURES,
            'preferred_combined_price': list(PREFERRED_COMBINED_PRICE),
            'preferred_range_is_hard': False,
            'personality_risk_limits_preserved': True}


def validate_contract(value):
    if value is not None and json.dumps(value, sort_keys=True, allow_nan=False) != json.dumps(
            product_contract(), sort_keys=True, allow_nan=False):
        raise ValueError('UNKNOWN_OR_CHANGED_TICKET_PRODUCT_CONTRACT')
    return value is not None


def report_contract(report):
    value = report.get('ticket_product_contract')
    if 'ticket_product_contract' in report and value is None:
        raise ValueError('NULL_TICKET_PRODUCT_CONTRACT')
    validate_contract(value)
    return deepcopy(value)


def bound_product_contract(card, report):
    policy, card_policy = report_contract(report), report_contract(card)
    if card_policy != policy and not (policy is not None and card_policy is None
                                      and card.get('profile_id') == 'daily223'):
        raise ValueError('TICKET_PRODUCT_CONTRACT_BINDING_MISMATCH')
    return policy


def preferred_label(total):
    if type(total) not in (int, float) or not math.isfinite(total):
        raise ValueError('FINITE_COMBINED_PRICE_REQUIRED')
    return ('IN_PREFERRED_RANGE' if PREFERRED_COMBINED_PRICE[0] <= total <= PREFERRED_COMBINED_PRICE[1]
            else 'OUTSIDE_PREFERRED_RANGE_NOT_A_REJECTION')


def preference_key(total):
    """Soft ordering only: no card/quote is removed by this preference."""
    low, high = PREFERRED_COMBINED_PRICE
    return (0, 0.0) if low <= total <= high else (1, min(abs(total - low), abs(total - high)))


def persona_contract(persona, contract):
    """Retain canonical own model rules and risk limits on a detached persona."""
    if not validate_contract(contract):
        raise ValueError('EXPLICIT_TICKET_PRODUCT_CONTRACT_REQUIRED')
    result = deepcopy(persona)
    band = result['target_odds_band']
    minimum, maximum = max(MINIMUM_COMBINED_PRICE, band['min']), band['max']
    if (type(maximum) not in (int, float) or not math.isfinite(maximum) or maximum < minimum):
        raise ValueError('INVALID_CANONICAL_PERSONA_RISK_LIMIT')
    result['target_odds_band'] = {'min': minimum, 'max': maximum}
    result['max_legs'] = min(MAXIMUM_DISTINCT_FIXTURES, result['max_legs'])
    result['preferred_combined_price'] = min(maximum, max(minimum,
        min(PREFERRED_COMBINED_PRICE[1], max(PREFERRED_COMBINED_PRICE[0], result['preferred_combined_price']))))
    result['ticket_product_contract'] = deepcopy(contract)
    # Existing current-persona model/history gates remain active; version2 is not redefined.
    result['operating_contract'] = {'version': 2, 'cautious_fallback': False,
                                    'price_policy_authority': VERSION}
    return result


def core_limits(profile):
    from goal_analysis.engine.specialist_contracts import SPECIALIST_CONTRACTS

    own = SPECIALIST_CONTRACTS.get(profile, {})
    return {'min': max(MINIMUM_COMBINED_PRICE, own.get('min_price', MINIMUM_COMBINED_PRICE)),
            'max': own.get('max_price'), 'minimum_legs': own.get('min_legs', 2),
            'maximum_legs': min(MAXIMUM_DISTINCT_FIXTURES, own.get('max_legs', MAXIMUM_DISTINCT_FIXTURES))}


def product_failure(card, *, own_persona=None):
    """Only shape/odds; existing source, clock, model and publication gates still run."""
    legs = card.get('legs')
    if not isinstance(legs, list) or not 1 <= len(legs) <= MAXIMUM_DISTINCT_FIXTURES:
        return 'TICKET_PRODUCT_LEG_COUNT'
    ids = [row.get('fixture_id') for row in legs if isinstance(row, dict)]
    if (len(ids) != len(legs) or any(not isinstance(key, str) or not key for key in ids)
            or len(set(ids)) != len(ids)):
        return 'DUPLICATE_OR_INVALID_FIXTURE'
    prices = [row.get('decimal_price') for row in legs]
    if any(type(price) not in (int, float) or not math.isfinite(price) or not 1 < price <= 1000
           for price in prices):
        return 'INVALID_PRICE_OR_QUOTE_SOURCE'
    total = math.prod(prices)
    quoted = card.get('combined_price')
    if (type(quoted) not in (int, float) or not math.isfinite(quoted)
            or not math.isclose(total, quoted, rel_tol=1e-9)):
        return 'COMBINED_PRICE_MISMATCH'
    own_core = core_limits(card.get('profile_id'))
    if total < own_core['min'] or own_core['max'] is not None and total > own_core['max']:
        return 'OUTSIDE_OWN_APPROVED_ODDS_RANGE'
    if own_persona is not None:
        band = own_persona['target_odds_band']
        if not max(MINIMUM_COMBINED_PRICE, band['min']) <= total <= band['max']:
            return 'OUTSIDE_OWN_APPROVED_ODDS_RANGE'
    return None


def validate_option_product(option):
    """Repeated pure guard at tool, FINAL and saved state boundaries; old options unchanged."""
    card = option.get('canonical_card')
    if not isinstance(card, dict) or 'ticket_product_contract' not in card:
        return
    from goal_analysis.tipsters.catalog import TIPSTER_BY_ID

    report_contract(card)
    expected_legs = [{key: value for key, value in row.items()
                      if key not in {'model_probability', 'model_probability_id'}} for row in card['legs']]
    if option.get('agent_id') != card.get('profile_id') or option.get('legs') != expected_legs:
        raise ValueError('NEW_PRODUCT_OPTION_LEGS_OR_OWNER_CHANGED')
    failure = product_failure(card, own_persona=TIPSTER_BY_ID.get(card.get('profile_id')))
    if failure is not None:
        raise ValueError(failure)


def feasible_search_ceiling(rows, max_legs=MAXIMUM_DISTINCT_FIXTURES):
    """Input-derived computational bound, never a global admission/target ceiling.

    Caller supplies one own eligible quote cohort. Every fixture participates;
    choosing its largest quote produces an upper bound for any valid <=6 group.
    The one-ULP outward rounding cannot exclude a group through float rounding.
    """
    if type(max_legs) is not int or not 1 <= max_legs <= MAXIMUM_DISTINCT_FIXTURES:
        raise ValueError('BOUNDED_DISTINCT_FIXTURE_COUNT_REQUIRED')
    prices = {}
    for row in rows:
        key, price = row['fixture_id'], row['decimal_price']
        if (not isinstance(key, str) or not key or type(price) not in (int, float)
                or not math.isfinite(price) or not 1 < price <= 1000):
            raise ValueError('INVALID_SEARCH_SOURCE_PRICE_OR_FIXTURE')
        prices[key] = max(prices.get(key, Decimal(1)), Decimal(str(price)))
    upper = math.prod(sorted(prices.values(), reverse=True)[:max_legs])
    return math.nextafter(float(upper), math.inf)
