"""Append-only SHADOW context events with conservative local availability.

Sources and canonical identity are validated before insertion. Old receipts are
never backdated: a backfill first becomes available when it is actually linked.
"""
import json
import re
from contextlib import closing
from datetime import UTC, datetime
from urllib.parse import urlsplit

from goal_analysis.normalization.canonical import check_id, utc

from .canonical_registry import CanonicalRegistry, digest, encoded

VERSION = 'fixture-context-events-v1'
CATEGORIES = frozenset({'squad', 'player_season_stats', 'injuries_suspensions', 'lineup',
                        'weather_forecast', 'weather_observation', 'weather_reanalysis', 'news'})


def stamp(value):
    return utc(value).isoformat(timespec='microseconds')


def clean_event(value, now):
    if not isinstance(value, dict) or set(value) != {
            'fixture_id', 'category', 'subject_id', 'payload', 'source_provider', 'source_url',
            'source_sha256', 'origin_reference', 'event_time', 'observed_at', 'published_at'}:
        raise ValueError('INVALID_CONTEXT_SCHEMA')
    check_id(value['fixture_id'], 'fixture')
    if value['category'] not in CATEGORIES or not isinstance(value['payload'], dict) or not value['payload']:
        raise ValueError('INVALID_CONTEXT_CATEGORY_OR_PAYLOAD')
    if value['subject_id'] is not None:
        if value['category'] == 'player_season_stats':
            check_id(value['subject_id'], 'player')
        elif value['category'] in {'squad', 'injuries_suspensions'}:
            check_id(value['subject_id'], 'team')
        else:
            raise ValueError('UNEXPECTED_CONTEXT_SUBJECT')
    elif value['category'] in {'squad', 'injuries_suspensions', 'player_season_stats'}:
        raise ValueError('MISSING_CONTEXT_SUBJECT')
    if not re.fullmatch(r'[0-9a-f]{64}', str(value['source_sha256'])):
        raise ValueError('INVALID_CONTEXT_SOURCE_HASH')
    url = urlsplit(value['source_url'])
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment
            or any(name in (url.query or '').casefold() for name in ('api_key=', 'apikey=', 'token=', 'secret='))):
        raise ValueError('INVALID_CONTEXT_SOURCE_URL')
    expected_provider = ('the_stats_api' if value['category'] in {
        'squad', 'player_season_stats', 'injuries_suspensions', 'lineup'} else
        'open_meteo' if value['category'].startswith('weather_') else 'grok')
    if value['source_provider'] != expected_provider:
        raise ValueError('INVALID_CONTEXT_SOURCE_PROVIDER')
    if expected_provider == 'the_stats_api' and url.netloc != 'api.thestatsapi.com':
        raise ValueError('INVALID_CONTEXT_PROVIDER_HOST')
    if expected_provider == 'open_meteo' and url.netloc not in {'api.open-meteo.com', 'archive-api.open-meteo.com'}:
        raise ValueError('INVALID_CONTEXT_PROVIDER_HOST')
    if not isinstance(value['origin_reference'], str) or not 0 < len(value['origin_reference']) <= 1000:
        raise ValueError('INVALID_CONTEXT_ORIGIN')
    # Reject non-finite values and detached mutable input before any write.
    result = json.loads(encoded(value))
    for key in ('observed_at', 'published_at', 'event_time'):
        result[key] = stamp(result[key]) if result[key] is not None else None
    if result['observed_at'] is None or utc(result['observed_at']) > now:
        raise ValueError('FUTURE_OR_MISSING_CONTEXT_OBSERVATION')
    if result['published_at'] is not None and utc(result['published_at']) > utc(result['observed_at']):
        raise ValueError('CONTEXT_PUBLICATION_AFTER_OBSERVATION')
    return result


class FixtureContexts:
    def __init__(self, database, *, clock=lambda: datetime.now(UTC)):
        self.database, self.clock = database, clock
        self.registry = CanonicalRegistry(database, clock=clock)

    def initialize(self):
        self.registry.initialize()
        with self.database.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS fixture_context_events (
                event_id TEXT PRIMARY KEY, fixture_id TEXT NOT NULL REFERENCES canonical_entities(entity_id),
                category TEXT NOT NULL, subject_id TEXT REFERENCES canonical_entities(entity_id),
                observed_at TEXT NOT NULL, published_at TEXT, event_time TEXT,
                ingested_at TEXT NOT NULL, available_at TEXT,
                record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS fixture_context_receipts (
                event_id TEXT PRIMARY KEY REFERENCES fixture_context_events(event_id),
                available_at TEXT NOT NULL, record_sha256 TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS fixture_context_origins (
                event_id TEXT NOT NULL REFERENCES fixture_context_events(event_id),
                origin_reference TEXT NOT NULL, ingested_at TEXT NOT NULL,
                PRIMARY KEY(event_id,origin_reference))''')
            db.execute('''CREATE INDEX IF NOT EXISTS fixture_context_asof
                ON fixture_context_events(fixture_id,category,observed_at,ingested_at)''')
            for table in ('fixture_context_events', 'fixture_context_receipts', 'fixture_context_origins'):
                for action in ('UPDATE', 'DELETE'):
                    db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                        BEFORE {action} ON {table}
                        BEGIN SELECT RAISE(ABORT, 'fixture contexts are append-only'); END''')

    def append(self, events):
        now = utc(self.clock())
        prepared = [clean_event(row, now) for row in events]
        for row in prepared:
            fixture = self.registry.entity(row['fixture_id'], as_of=now)
            if fixture is None or fixture.KIND != 'fixture':
                raise ValueError('MISSING_CANONICAL_FIXTURE')
            if row['subject_id'] is not None:
                subject = self.registry.entity(row['subject_id'], as_of=now)
                if subject is None:
                    raise ValueError('MISSING_CANONICAL_SUBJECT')
                if subject.KIND == 'team' and subject.entity_id not in {fixture.home_team_id, fixture.away_team_id}:
                    raise ValueError('CONTEXT_TEAM_NOT_IN_FIXTURE')
                native_field = 'player_id' if subject.KIND == 'player' else 'team_id'
                native_subject = row['payload'].get(native_field)
                if self.registry.resolve('the_stats_api', subject.KIND, native_subject, as_of=now) != subject.entity_id:
                    raise ValueError('CONTEXT_NATIVE_SUBJECT_MISMATCH')
                team = self.registry.resolve('the_stats_api', 'team', row['payload'].get('team_id'), as_of=now)
                if team not in {fixture.home_team_id, fixture.away_team_id}:
                    raise ValueError('CONTEXT_TEAM_NOT_IN_FIXTURE')
            if row['category'] == 'lineup' and any(self.registry.resolve('the_stats_api', 'team',
                    row['payload'].get(side + '_team_id'), as_of=now) != getattr(fixture, side + '_team_id')
                    for side in ('home', 'away')):
                raise ValueError('CONTEXT_LINEUP_TEAM_MISMATCH')
        created, ids = 0, []
        with self.database.transaction() as db:
            for row in prepared:
                event_id = digest([VERSION, {k:v for k,v in row.items() if k != 'origin_reference'}])
                ids.append(event_id)
                record = {**row, 'version': VERSION, 'event_id': event_id,
                          'ingested_at': stamp(now), 'available_at': None,
                          'availability_basis': 'AWAITING_COMMITTED_SOURCE_WITNESS'}
                serialized = encoded(record)
                created += db.execute('INSERT OR IGNORE INTO fixture_context_events VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (event_id, row['fixture_id'], row['category'], row['subject_id'], row['observed_at'],
                     row['published_at'], row['event_time'], stamp(now), None, serialized, digest(record))).rowcount
                db.execute('INSERT OR IGNORE INTO fixture_context_origins VALUES (?,?,?)',
                           (event_id, row['origin_reference'], stamp(now)))
        # Read committed contents, THEN take the wall-clock witness. A crash
        # before this second transaction leaves events invisible, not backdated.
        with closing(self.database.connect()) as db:
            committed = [db.execute('SELECT event_id,record_sha256,ingested_at FROM fixture_context_events WHERE event_id=?',
                                   (identifier,)).fetchone() for identifier in sorted(set(ids))]
        witnessed = utc(self.clock())
        if witnessed < now or any(utc(row['ingested_at']) > witnessed for row in committed):
            raise ValueError('CLOCK_MOVED_BACKWARDS')
        with self.database.transaction() as db:
            for row in committed:
                db.execute('INSERT OR IGNORE INTO fixture_context_receipts VALUES (?,?,?)',
                           (row['event_id'], stamp(witnessed), row['record_sha256']))
        return {'created': created, 'reused': len(prepared) - created}

    def records(self, fixture_id, *, as_of, categories=None, limit=1000):
        check_id(fixture_id, 'fixture')
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('INVALID_CONTEXT_QUERY_LIMIT')
        if categories is not None and (not categories or not set(categories) <= CATEGORIES):
            raise ValueError('INVALID_CONTEXT_QUERY_CATEGORIES')
        cutoff = stamp(as_of)
        category_clause = '' if categories is None else ' AND category IN (' + ','.join('?' for _ in categories) + ')'
        parameters = (fixture_id, cutoff, cutoff, cutoff, cutoff, *(sorted(categories) if categories is not None else ()), limit)
        with closing(self.database.connect()) as db:
            rows = db.execute('''SELECT e.*,r.available_at AS witnessed_at,r.record_sha256 AS witnessed_hash
                FROM fixture_context_events e JOIN fixture_context_receipts r USING(event_id) WHERE fixture_id=?
                AND observed_at<=? AND r.available_at<=? AND ingested_at<=?
                AND (published_at IS NULL OR published_at<=?)''' + category_clause + '''
                ORDER BY r.available_at,e.event_id LIMIT ?''', parameters).fetchall()
        result = []
        for row in rows:
            value = json.loads(row['record_json'])
            if (digest(value) != row['record_sha256'] or row['witnessed_hash'] != row['record_sha256']
                    or value['event_id'] != row['event_id']):
                raise ValueError('CORRUPT_CONTEXT_EVENT')
            if any(value[k] != row[k] for k in ('fixture_id', 'category', 'subject_id', 'observed_at',
                    'published_at', 'event_time', 'available_at', 'ingested_at')):
                raise ValueError('CORRUPT_CONTEXT_INDEX')
            clean = clean_event({k: v for k, v in value.items() if k not in {
                'version', 'event_id', 'ingested_at', 'available_at', 'availability_basis'}}, utc(as_of))
            if (digest([VERSION, {k:v for k,v in clean.items() if k != 'origin_reference'}]) != value['event_id']
                    or utc(row['witnessed_at']) < utc(row['ingested_at'])):
                raise ValueError('CORRUPT_CONTEXT_ID_OR_WITNESS')
            if self.registry.entity(fixture_id, as_of=as_of) is None:
                raise ValueError('MISSING_CANONICAL_FIXTURE_AT_CUTOFF')
            if categories is None or row['category'] in categories:
                result.append({**value, 'available_at': row['witnessed_at'],
                               'availability_basis': 'SOURCE_PRESENCE_UPPER_BOUND'})
        return result
