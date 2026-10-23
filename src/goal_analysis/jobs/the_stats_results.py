"""Resolve actual result sources, including exact joins for legacy saved bets."""
from datetime import timedelta

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.identity import exact_name, native_id
from goal_analysis.providers.base import ProviderError
from goal_analysis.providers.the_stats_leagues import LEAGUES


def _result(row, source):
    native_id(row['id'], 'mt')
    score = row.get('score') or {}
    status = {'scheduled': 'NS', 'live': 'LIVE', 'finished': 'FT',
              'postponed': 'PST', 'cancelled': 'CANC'}.get(row.get('status'), 'UNKNOWN')
    # Internal settlement shape, with explicit native provider and original data.
    # No provider ID or after-extra-time total is relabelled as an API-Football result.
    return {'provider': 'the_stats_api', 'provider_match_id': row['id'], 'source': source,
            'fixture': {'id': row['id'], 'status': {'short': status}},
            'score': {'fulltime': score.get('regulation'),
                      'halftime': {'home': score.get('half_time_home'), 'away': score.get('half_time_away')}},
            'native_result': row}


def resolve_result(client, leg, *, resources=None):
    from .the_stats_context import Resources
    from .the_stats_live import _page

    resources = resources or Resources(client.root, client, client._clock, 20)
    identifier = str(leg['fixture_id'])
    if identifier.startswith('the_stats_api:'):
        match = native_id(identifier.removeprefix('the_stats_api:'), 'mt')
        row, source, error = resources.get('/football/matches/' + match)
        if error or not isinstance(row, dict) or row.get('id') != match:
            raise ProviderError('TheStats requested result unavailable')
    else:
        # The integer legacy ID has no numerical relation to TheStats IDs.
        # An exact competition + oriented names + kickoff join is mandatory.
        import json

        from goal_analysis.config.daily_223_live import validate_live_config

        config = validate_live_config(json.loads((client.root / 'config/daily223-live.json').read_text('utf-8-sig')))
        leagues = [x for x in config['leagues'] if x['competition_id'] == leg.get('competition')]
        if len(leagues) != 1 or str(leagues[0]['api_football_id']) not in LEAGUES:
            raise ProviderError('Legacy result has no verified TheStats competition mapping')
        comp = LEAGUES[str(leagues[0]['api_football_id'])][0]
        kickoff = utc(leg['kickoff'])
        query = {'competition_id': comp, 'date_from': kickoff.date().isoformat(),
                 'date_to': kickoff.date().isoformat(), 'utc_offset': '+00:00'}
        rows, source, pages = _page(resources, query, 1)
        if pages > 1:
            raise ProviderError('Ambiguous paginated legacy result lookup')
        matches = [row for row in rows if row.get('competition_id') == comp
                   and utc(row['utc_date']) == kickoff
                   and all(exact_name(row[side + '_team']['name']) == exact_name(leg[side + '_team'])
                           for side in ('home', 'away'))]
        if len(matches) != 1:
            raise ProviderError('No unique exact TheStats result for legacy fixture')
        row = matches[0]
    if (utc(row['utc_date']) != utc(leg['kickoff'])
            or any(exact_name(row[side + '_team']['name']) != exact_name(leg[side + '_team'])
                   for side in ('home', 'away'))):
        raise ProviderError('TheStats result identity mismatch')
    if row.get('status') == 'finished' and utc(row['utc_date']) + timedelta(minutes=90) > utc(source['observed_at']):
        raise ProviderError('Premature finished result')
    return _result(row, source)
