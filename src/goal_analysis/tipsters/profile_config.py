"""Pinned, bounded club configuration. No environment/caller/daily overrides.

Load once into read-only shared containers; detached deep copies remain ordinary
JSON-compatible values for reports and explicitly separate SHADOW experiments.
"""
import hashlib
import json
import math
import re
from copy import deepcopy
from pathlib import Path

CONFIGURATION_SHA256 = 'aaa25c6c4dae89b243ba0297feaf7aafdf493ffe36447c41fbd8e716b1912c09'
CATALOG_VERSION = 'arthur-tipster-personas-v7-odds-band-10-50'
CONFIGURATION_PATH = Path(__file__).with_name('profiles.json')
MAX_BYTES = 2 * 1024 * 1024
MARKETS = {'home', 'draw', 'away', 'over25', 'under25', 'btts_yes', 'btts_no'}
MODELS = {'gauss', 'kepler', 'laplace'}
PROFILE_FIELDS = {'catalog_version', 'fictional', 'max_legs', 'method_kind', 'min_legs',
                  'model_weights', 'name', 'personality', 'preferences', 'preferred_combined_price',
                  'preferred_legs', 'profile_id', 'reference_odds_band', 'risk', 'selection_mode',
                  'selection_policy', 'source_style', 'tagline', 'target_odds_band',
                  'target_odds_minimum_enforced'}
POLICY_FIELDS = {'allowed_market_keys', 'competition_diversity', 'edge_weight', 'history_weight',
                 'market_bonus', 'market_diversity', 'market_weight', 'max_model_spread', 'min_edge',
                 'min_market_disagreement', 'min_models', 'min_probability', 'preferred_price',
                 'price_penalty', 'probability_weight', 'required_models', 'spread_penalty', 'venue_weight'}
CONTRARIAN_FIELDS = {'contrarian_min_expected_return', 'contrarian_min_rise', 'contrarian_value'}


class ProfileConfigurationError(ValueError):
    """A deployment/configuration integrity error, never a reason to relax rules."""


def _deny(*_args, **_kwargs):
    raise TypeError('SHARED_TIPSTER_CONFIGURATION_IS_READ_ONLY')


class _FrozenDict(dict):
    __slots__ = ()

    def __new__(cls, *args, **kwargs):
        result = dict.__new__(cls)
        dict.__init__(result, *args, **kwargs)
        return result

    def __init__(self, *_args, **_kwargs):
        pass  # Construction is in __new__; later __init__ calls cannot rewrite policy.

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _deny

    def __deepcopy__(self, memo):
        result = {}
        memo[id(self)] = result
        result.update((deepcopy(k, memo), deepcopy(v, memo)) for k, v in self.items())
        return result


class _FrozenList(list):
    __slots__ = ()

    def __new__(cls, *args, **kwargs):
        result = list.__new__(cls)
        list.__init__(result, *args, **kwargs)
        return result

    def __init__(self, *_args, **_kwargs):
        pass

    __setitem__ = __delitem__ = __iadd__ = __imul__ = append = extend = insert = pop = remove = clear = sort = reverse = _deny

    def __deepcopy__(self, memo):
        result = []
        memo[id(self)] = result
        result.extend(deepcopy(v, memo) for v in self)
        return result


def freeze(value):
    if isinstance(value, dict):
        return _FrozenDict((k, freeze(v)) for k, v in value.items())
    if isinstance(value, list):
        return _FrozenList(freeze(v) for v in value)
    return value


def _number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ProfileConfigurationError('INVALID_FIXED_PROFILE_NUMBER')


def _unique(values, allowed):
    if (not isinstance(values, list) or len(values) != len(set(values))
            or not set(values) <= allowed):
        raise ProfileConfigurationError('INVALID_FIXED_PROFILE_MEMBERS')


def _validate(value):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'catalog_version', 'profiles'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['catalog_version'] != CATALOG_VERSION
            or not isinstance(value['profiles'], list) or len(value['profiles']) != 20):
        raise ProfileConfigurationError('INVALID_FIXED_PROFILE_SCHEMA')
    ids = []
    for p in value['profiles']:
        if not isinstance(p, dict) or set(p) != PROFILE_FIELDS:
            raise ProfileConfigurationError('INVALID_FIXED_PROFILE_FIELDS')
        identifier = p['profile_id']
        if not isinstance(identifier, str) or not re.fullmatch(r'tipster_[a-z0-9_]+', identifier):
            raise ProfileConfigurationError('INVALID_FIXED_PROFILE_ID')
        ids.append(identifier)
        if (p['catalog_version'] != CATALOG_VERSION or p['fictional'] is not True
                or p['target_odds_minimum_enforced'] is not True
                or p['method_kind'] != 'SHARED_EVIDENCE_PERSONA'
                or p['selection_mode'] != 'PROFILE_ACCUMULATOR'):
            raise ProfileConfigurationError('INVALID_FIXED_PROFILE_KIND')
        if any(not isinstance(p[k], str) or not 0 < len(p[k]) <= 2000 for k in ('name', 'personality', 'tagline', 'risk')):
            raise ProfileConfigurationError('INVALID_FIXED_PROFILE_TEXT')
        if (not isinstance(p['preferences'], list) or not p['preferences']
                or any(not isinstance(s, str) or not 0 < len(s) <= 500 for s in p['preferences'])
                or not isinstance(p['source_style'], dict)):
            raise ProfileConfigurationError('INVALID_FIXED_PROFILE_DESCRIPTION')
        band = p['target_odds_band']
        if not isinstance(band, dict) or set(band) != {'min', 'max'} or p['reference_odds_band'] != band:
            raise ProfileConfigurationError('INVALID_FIXED_PROFILE_BAND')
        _number(band['min'], 10, 50)
        _number(band['max'], band['min'], 50)
        _number(p['preferred_combined_price'], band['min'], band['max'])
        if (any(type(p[k]) is not int for k in ('min_legs', 'preferred_legs', 'max_legs'))
                or not 2 <= p['min_legs'] <= p['preferred_legs'] <= p['max_legs'] <= 6):
            raise ProfileConfigurationError('INVALID_FIXED_PROFILE_SHAPE')
        weights, policy = p['model_weights'], p['selection_policy']
        if not isinstance(weights, dict) or set(weights) != MODELS:
            raise ProfileConfigurationError('INVALID_FIXED_PROFILE_MODELS')
        for w in weights.values():
            _number(w, 0, 1)
        if not math.isclose(sum(weights.values()), 1, rel_tol=0, abs_tol=1e-10):
            raise ProfileConfigurationError('FIXED_PROFILE_WEIGHTS_MUST_SUM_TO_ONE')
        if (not isinstance(policy, dict) or not POLICY_FIELDS <= set(policy)
                or set(policy) - POLICY_FIELDS - CONTRARIAN_FIELDS):
            raise ProfileConfigurationError('INVALID_FIXED_PROFILE_POLICY_FIELDS')
        for k in ('min_probability', 'max_model_spread'):
            _number(policy[k], 0, 1)
        for k in POLICY_FIELDS - {'allowed_market_keys', 'market_bonus', 'required_models',
                                  'min_models', 'min_market_disagreement', 'min_probability', 'max_model_spread'}:
            _number(policy[k], 0, 100)
        if policy['min_market_disagreement'] is not None:
            _number(policy['min_market_disagreement'], 0, 1)
        if type(policy['min_models']) is not int or not 1 <= policy['min_models'] <= 3:
            raise ProfileConfigurationError('INVALID_FIXED_PROFILE_MODEL_COUNT')
        _unique(policy['required_models'], MODELS)
        if any(weights[m] <= 0 for m in policy['required_models']):
            raise ProfileConfigurationError('REQUIRED_FIXED_PROFILE_MODEL_DISABLED')
        if policy['allowed_market_keys'] is not None:
            _unique(policy['allowed_market_keys'], MARKETS)
            if not policy['allowed_market_keys']:
                raise ProfileConfigurationError('EMPTY_FIXED_PROFILE_MARKET_SCOPE')
        if not isinstance(policy['market_bonus'], dict) or not set(policy['market_bonus']) <= MARKETS:
            raise ProfileConfigurationError('INVALID_FIXED_PROFILE_MARKET_BONUS')
        for bonus in policy['market_bonus'].values():
            _number(bonus, 0, 100)
        if set(policy) & CONTRARIAN_FIELDS:
            if not CONTRARIAN_FIELDS <= set(policy) or policy['contrarian_value'] is not True:
                raise ProfileConfigurationError('INCOMPLETE_FIXED_CONTRARIAN_POLICY')
            _number(policy['contrarian_min_rise'], 0, 1)
            _number(policy['contrarian_min_expected_return'], 1.15, 100)
    if len(set(ids)) != 20:
        raise ProfileConfigurationError('DUPLICATE_FIXED_PROFILE_ID')
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProfileConfigurationError('DUPLICATE_PROFILE_JSON_KEY')
        result[key] = value
    return result


def _constant(_value):
    raise ProfileConfigurationError('NONFINITE_PROFILE_JSON_VALUE')


def load_configuration():
    """Only this release's bundled exact bytes; never read working-dir overrides."""
    try:
        if CONFIGURATION_PATH.stat().st_size > MAX_BYTES:
            raise ProfileConfigurationError('PROFILE_CONFIGURATION_TOO_LARGE')
        raw = CONFIGURATION_PATH.read_bytes()
        if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != CONFIGURATION_SHA256:
            raise ProfileConfigurationError('PROFILE_CONFIGURATION_HASH_MISMATCH')
        return freeze(_validate(json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)))
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, RecursionError) as error:
        raise ProfileConfigurationError('PROFILE_CONFIGURATION_UNAVAILABLE_OR_INVALID') from error
