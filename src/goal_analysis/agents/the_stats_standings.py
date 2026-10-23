"""TheStats descriptive tables; no inferred UEFA/relegation qualification rules."""
from collections import defaultdict

from goal_analysis.normalization.identity import native_id
from goal_analysis.normalization.live_identity import field_id


def collect(fixtures, client, *, as_of, max_requests, clock):
    from goal_analysis.jobs.the_stats_context import Resources

    from .motivation_standings import _context, _integer

    resources = Resources(client.root, client, clock, max_requests)
    scopes = defaultdict(list)
    for fixture in fixtures:
        if fixture.get('source_provider') == 'the_stats_api':
            scopes[(field_id(fixture, 'league'), native_id(fixture['provider_season_id'], 'sn'))].append(fixture)
    contexts, rows, errors = {}, [], []
    for (comp, season), members in sorted(scopes.items()):
        data, source, error = resources.get(f'/football/competitions/{comp}/seasons/{season}/standings')
        if error or not isinstance(data, list):
            errors.append({'league_id': comp, 'code': error or 'INVALID_STANDINGS'})
            continue
        normalized, groups, seen, ambiguous = [], {}, set(), set()
        for item in data:
            team = item.get('team', {}).get('id') if isinstance(item, dict) else None
            if team in seen:
                ambiguous.add(team)
            seen.add(team)
        if ambiguous:
            errors.append({'league_id': comp, 'code': 'AMBIGUOUS_PROVIDER_STANDINGS',
                           'ambiguous_team_ids': sorted(str(team) for team in ambiguous), 'source': source})
            continue
        seen = set()
        try:
            for item in data:
                team = native_id(item['team']['id'], 'tm')
                if team in seen:
                    raise ValueError('Ambiguous standings team')
                seen.add(team)
                group = item.get('group_label')
                group_index = groups.setdefault(group, len(groups))
                normalized.append({'team_id': team, 'team_name': item['team']['name'],
                    'rank': _integer(item['position'], 1), 'points': _integer(item['points'], -1000),
                    'played': _integer(item['matches_played']),
                    'goals_difference': _integer(item['goal_difference'], -10000),
                    'form': item.get('form'), 'provider_zone': None, 'group': group,
                    'group_index': group_index, 'provider_updated_at': None})
        except (ValueError, KeyError, TypeError):
            errors.append({'league_id': comp, 'code': 'INVALID_STANDINGS'})
            continue
        for fixture in members:
            snapshot = {'rows': normalized, 'league_id': comp, 'season': fixture['season'],
                        'group_count': len(groups), 'fetched_at': source['observed_at'],
                        'source_url': source['source_url']}
            context = _context(fixture, snapshot)
            context.update(provider='the_stats_api', provider_season_id=season)
            contexts[fixture['fixture_id']] = context
        rows.extend({**row, 'league_id': comp, 'provider_season_id': season,
                     'observed_at': source['observed_at'], 'source_url': source['source_url']} for row in normalized)
    return {'rows': rows, 'context_by_fixture': contexts, 'requests': resources.usage['requests'],
            'errors': errors, 'deferred': [], 'observed_at': clock().isoformat(),
            'provider': 'the_stats_api', 'usage': resources.usage}
