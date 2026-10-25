"""Resolve the actual match venue on demand, never a guessed home stadium."""
from datetime import timedelta

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.live_identity import field_id, fixture_identity


def resolve_venue(resources, fixture, *, refresh_existing=False):
    if fixture.get('source_provider') != 'the_stats_api' or (fixture.get('venue_city') and not refresh_existing):
        return dict(fixture), None
    if utc(resources.clock()) >= utc(fixture['kickoff']) - timedelta(minutes=15):
        return dict(fixture), {'status': 'LOCKED'}
    match = fixture_identity(fixture)
    row, source, error = resources.get('/football/matches/' + match)
    if error:
        return dict(fixture), {'status': error}
    from .the_stats_live import _identity

    actual = _identity(row)
    expected = (match, field_id(fixture, 'league'), fixture['provider_season_id'],
                field_id(fixture, 'home_team'), field_id(fixture, 'away_team'))
    if actual != expected or utc(row['utc_date']) != utc(fixture['kickoff']) or row.get('status') != 'scheduled':
        return dict(fixture), {'status': 'IDENTITY_OR_STATUS_MISMATCH'}
    venue = row.get('venue') or {}
    city, name = venue.get('city'), venue.get('name')
    if not isinstance(city, str) or len(city.strip()) < 2:
        return dict(fixture), {'status': 'VENUE_MISSING', 'source': source}
    return {**fixture, 'venue_city': city, 'venue_name': name, '_venue_source': source}, {'status': 'VERIFIED', 'source': source}
