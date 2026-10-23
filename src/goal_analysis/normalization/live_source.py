"""Validate a native TheStats receipt URL without discarding request identity."""
from urllib.parse import parse_qsl, urlsplit

from goal_analysis.providers.the_stats_api import TheStatsApiError, _parameters, _route


def stats_source_url(value):
    parts = urlsplit(value)
    if (parts.scheme != 'https' or parts.netloc != 'api.thestatsapi.com'
            or parts.fragment or not parts.path.startswith('/api/')):
        raise ValueError('Invalid TheStats source')
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    params = dict(pairs)
    if len(params) != len(pairs):
        raise ValueError('Repeated source parameter')
    for key in ('page', 'per_page', 'limit', 'matchday'):
        if key in params:
            params[key] = int(params[key])
    for key in ('current',):
        if key in params:
            if params[key] not in {'true', 'false'}:
                raise ValueError('Invalid source boolean')
            params[key] = params[key] == 'true'
    try:
        _, keys, maximum, required = _route(parts.path.removeprefix('/api'))
        _parameters(params, keys, maximum, required)
    except TheStatsApiError:
        raise ValueError('Invalid TheStats source route or parameter') from None
    return value
