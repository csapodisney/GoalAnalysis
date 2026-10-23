"""Versioned request ordering only; no prediction or ticket scoring rules."""
from datetime import UTC, datetime

LEGACY = 'category-first-v1'
FAIR = 'cache-first-fixture-round-robin-shadow-v1'


def fixture_order(row):
    try:
        kickoff = datetime.fromisoformat(str(row.get('kickoff')))
        if kickoff.utcoffset() is None:
            raise ValueError('Naive kickoff')
        kickoff = kickoff.astimezone(UTC)
    except (ValueError, TypeError):
        kickoff = datetime.max.replace(tzinfo=UTC)
    return kickoff, str(row.get('fixture_id', ''))


def core_plan(matched):
    """One lineup opportunity each, then alternate team xG/absences fairly.

    Per-fixture queues are interleaved, never drained one fixture at a time.
    The first side/category alternates across the kickoff/ID-ordered calendar;
    outcomes, odds and heuristic scores cannot influence request allocation.
    Every fixture has five jobs; budget shortfalls remain explicit.
    """
    queues = []
    for index, record in enumerate(sorted(matched, key=fixture_order)):
        mapping = record['mapping']
        teams = [mapping['home_team_id'], mapping['away_team_id']]
        categories = ['team_season_stats', 'injuries_suspensions']
        if index % 2:
            teams.reverse()
            categories.reverse()
        queue = [('lineup', mapping['match_id'])]
        queue.extend((category, team) for team in teams for category in categories)
        queues.append([(record, category, subject) for category, subject in queue])
    return [queue[round_index] for round_index in range(5) for queue in queues]
