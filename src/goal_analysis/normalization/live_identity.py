"""Explicit live provider identities; numeric legacy IDs are never fabricated."""
import re

from goal_analysis.providers.api_football_history import positive_id

PREFIXES = {'fixture': 'mt', 'league': 'comp', 'home_team': 'tm', 'away_team': 'tm'}


def source_provider(row):
    value = row.get('source_provider', 'api_football')
    if value not in {'api_football', 'the_stats_api'}:
        raise ValueError('Unknown live sports provider')
    return value


def provider_id(value, field, provider):
    if provider == 'api_football':
        return positive_id(value)
    if (provider != 'the_stats_api' or not isinstance(value, str)
            or not re.fullmatch(PREFIXES[field] + r'_[0-9]{1,30}', value)):
        raise ValueError('Invalid native TheStats identity')
    return value


def field_id(row, field):
    provider = source_provider(row)
    key = ('provider_' if provider == 'the_stats_api' else 'api_football_') + field + '_id'
    return provider_id(row.get(key), field, provider)


def fixture_identity(row):
    identifier = field_id(row, 'fixture')
    if row.get('fixture_id') != source_provider(row) + ':' + identifier:
        raise ValueError('Native and application fixture identities disagree')
    if source_provider(row) == 'the_stats_api':
        for field in ('league', 'home_team', 'away_team'):
            field_id(row, field)
        if (field_id(row, 'home_team') == field_id(row, 'away_team')
                or not re.fullmatch(r'sn_[0-9]{1,30}', str(row.get('provider_season_id')))):
            raise ValueError('Invalid native fixture participants or season')
    return identifier


def config_league_id(row):
    return row.get('config_league_id') if source_provider(row) == 'the_stats_api' else row.get('api_football_league_id')
