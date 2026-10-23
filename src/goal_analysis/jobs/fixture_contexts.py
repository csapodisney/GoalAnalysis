"""Link downloaded facts to canonical fixtures, offline and without decisions.

TheStats facts are re-normalized from their original bytes. Missing identities
remain explicit gaps. No API, model, wager, source-table update or name guess.
"""
import hashlib
import json
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from goal_analysis.agents.grok_stats_data import _source, validate_stats_context
from goal_analysis.normalization.canonical import (
    CanonicalFixture,
    CanonicalPlayer,
    CanonicalSeason,
    CanonicalTeam,
    canonical_id,
    utc,
)
from goal_analysis.normalization.identity import exact_name, native_id
from goal_analysis.storage.canonical_registry import (
    MappingEvidence,
    ProviderBinding,
    digest,
)
from goal_analysis.storage.db import Database
from goal_analysis.storage.fixture_contexts import FixtureContexts
from goal_analysis.storage.source_observations import http_source

from .the_stats_context import _canonical

FACTS = frozenset({'squad', 'player_season_stats', 'injuries_suspensions', 'lineup'})


def raw_source(root, database, source, now):
    source = _source(source, now)
    with closing(database.connect()) as db:
        actual = http_source(db, root, source, now)
        if actual is not None:
            return actual[0]['data']
        row = db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?', (source['content_hash'],)).fetchone()
        # Content-addressed raw storage keeps only its first URL. Two real team
        # endpoints can legitimately return byte-identical empty absence lists.
        # An exact, hash-verified per-request cache receipt proves the other URL.
        extra_receipts = [] if row is None or row['source_url'] == source['source_url'] else db.execute('''
            SELECT value_json,content_hash,created_at FROM cache_entries
            WHERE namespace='thestatsapi-context-v1' AND json_extract(value_json,'$.source.source_url')=?''',
            (source['source_url'],)).fetchall()
    proven_url = row is not None and row['source_url'] == source['source_url']
    for receipt in extra_receipts:
        if hashlib.sha256(receipt['value_json'].encode()).hexdigest() == receipt['content_hash']:
            cached = _source(json.loads(receipt['value_json'])['source'], now)
            proven_url |= cached == source and utc(receipt['created_at']) == utc(source['observed_at'])
    if (row is None or row['provider'] != 'the_stats_api' or not proven_url
            or utc(row['fetched_at']) > utc(source['observed_at'])):
        raise ValueError('MISSING_RAW_CONTEXT_PROOF')
    raw_root = (root / 'data/raw').resolve()
    path = (raw_root / row['relative_path']).resolve()
    if not path.is_relative_to(raw_root):
        raise ValueError('CONTEXT_RAW_PATH_OUTSIDE_ARCHIVE')
    raw = path.read_bytes()
    if len(raw) != row['size_bytes'] or hashlib.sha256(raw).hexdigest() != source['content_hash']:
        raise ValueError('CORRUPT_RAW_CONTEXT')
    return json.loads(raw)['data']


def bind_fixture(root, database, registry, fixture, context, now):
    from .the_stats_live import _identity

    if fixture.get('source_provider') == 'the_stats_api':
        from goal_analysis.normalization.live_identity import fixture_identity

        match = fixture_identity(fixture)
        source = fixture['fixture_source']
    elif context and context['status'] == 'MATCHED':
        match, source = context['mapping']['match_id'], context['mapping']['source']
    else:
        raise ValueError('MISSING_LINK')
    payload = raw_source(root, database, source, now)
    rows = payload if isinstance(payload, list) else [payload]
    matching = [row for row in rows if isinstance(row, dict) and row.get('id') == match]
    if len(matching) != 1:
        raise ValueError('AMBIGUOUS_CANONICAL_FIXTURE_SOURCE')
    row = matching[0]
    match, comp, season, home, away = _identity(row)
    if (utc(row['utc_date']) != utc(fixture['kickoff']) or any(
            exact_name(row[side + '_team']['name']) != exact_name(fixture[side + '_team']) for side in ('home', 'away'))):
        raise ValueError('CANONICAL_FIXTURE_IDENTITY_MISMATCH')
    if fixture.get('source_provider') == 'the_stats_api' and any(fixture[key] != value for key, value in (
            ('provider_league_id', comp), ('provider_season_id', season), ('provider_home_team_id', home),
            ('provider_away_team_id', away), ('provider_fixture_id', match))):
        raise ValueError('CANONICAL_FIXTURE_IDENTITY_MISMATCH')
    comp_id = registry.resolve('the_stats_api', 'competition', comp, as_of=now)
    competition = registry.entity(comp_id, as_of=now) if comp_id else None
    if competition is None:
        raise ValueError('MISSING_VERIFIED_CANONICAL_COMPETITION')
    ids = {'fixture': canonical_id('fixture', 'the_stats_api:' + match),
           'season': canonical_id('season', 'the_stats_api:' + season), 'competition': comp_id,
           'home': canonical_id('team', 'the_stats_api:' + home), 'away': canonical_id('team', 'the_stats_api:' + away)}
    entities = [competition,
        CanonicalSeason(entity_id=ids['season'], competition_id=comp_id, label=season),
        CanonicalTeam(entity_id=ids['home'], name=row['home_team']['name']),
        CanonicalTeam(entity_id=ids['away'], name=row['away_team']['name']),
        CanonicalFixture(entity_id=ids['fixture'], competition_id=comp_id, season_id=ids['season'],
            home_team_id=ids['home'], away_team_id=ids['away'], kickoff=utc(row['utc_date']),
            status={'scheduled': 'SCHEDULED', 'finished': 'FINISHED', 'live': 'LIVE', 'postponed': 'POSTPONED',
                    'cancelled': 'CANCELLED'}.get(row.get('status'), 'UNKNOWN'))]
    binding = [ProviderBinding('the_stats_api', kind, native, ids[key]) for kind, native, key in (
        ('fixture', match, 'fixture'), ('season', season, 'season'), ('team', home, 'home'), ('team', away, 'away'))]
    evidence = MappingEvidence('NATIVE_PROVIDER_ID', source['source_url'], source['content_hash'], utc(source['observed_at']))
    registry.register(entities, binding, evidence)
    if fixture['fixture_id'].startswith('api_football:'):
        legacy = fixture['fixture_id'].partition(':')[2]
        if not legacy.isascii() or not legacy.isdecimal() or int(legacy) <= 0:
            raise ValueError('INVALID_LEGACY_FIXTURE_ID')
        registry.register(entities, [ProviderBinding('api_football', 'fixture', legacy, ids['fixture'])],
            MappingEvidence('EXACT_NAMES_KICKOFF_EXPLICIT_LEAGUE', source['source_url'], source['content_hash'], utc(source['observed_at'])))
    return ids['fixture']


def link_report(root, report, *, clock=lambda: datetime.now(UTC), include_saved_context=True):
    if type(include_saved_context) is not bool:
        raise ValueError('BOOLEAN_SAVED_CONTEXT_LINK_FLAG_REQUIRED')
    root, now = Path(root).resolve(), utc(clock())
    database = Database(root / 'data/goal_analysis.sqlite3')
    store = FixtureContexts(database, clock=clock)
    store.initialize()
    registry = store.registry
    collection = report.get('sports_supplement', {}).get('the_stats_api') or {}
    counts, gaps, links = Counter(), Counter(), {}
    for fixture in report.get('fixtures', []):
        fid, context = fixture.get('fixture_id'), None
        try:
            raw_context = collection.get('context_by_fixture', {}).get(fid)
            if raw_context:
                context = validate_stats_context({**raw_context, 'version': collection['version'],
                    'provider': collection['provider'], 'observed_at': collection['observed_at']}, fixture, now)
            canonical = bind_fixture(root, database, registry, fixture, context, now)
            links[fid] = canonical
            counts['fixtures_linked'] += 1
        except (ValueError, TypeError, KeyError, OSError):
            gaps['MISSING_OR_INVALID_FIXTURE_LINK'] += 1
            continue
        if not context or context['status'] != 'MATCHED':
            gaps['NO_VALIDATED_SPORTS_CONTEXT'] += 1
            continue
        facts = []
        for fact in context['facts']:
            if fact['category'] not in FACTS or fact['status'] != 'AVAILABLE':
                continue
            try:
                raw = raw_source(root, database, fact['source'], now)
                normalized = _canonical(fact['category'], raw, fact['subject_id'], context['mapping'])
                if normalized != fact['data']:
                    raise ValueError('CONTEXT_FACT_DIFFERS_FROM_ORIGINAL_BYTES')
                facts.append(fact)
            except (ValueError, TypeError, KeyError, OSError):
                gaps['INVALID_FACT_RAW_PROOF'] += 1
        # Only actually supplied player names form canonical player records.
        for fact in facts:
            groups = ('players',) if fact['category'] == 'squad' else ('home_players', 'away_players') if fact['category'] == 'lineup' else ()
            for group in groups:
                for player in fact['data'][group]:
                    try:
                        identifier = native_id(player['player_id'], 'pl')
                        entity = CanonicalPlayer(entity_id=canonical_id('player', 'the_stats_api:' + identifier), name=player['name'])
                        registry.register([entity], [ProviderBinding('the_stats_api', 'player', identifier, entity.entity_id)],
                            MappingEvidence('NATIVE_PROVIDER_ID', fact['source']['source_url'], fact['source']['content_hash'], utc(fact['source']['observed_at'])))
                    except (ValueError, TypeError, KeyError):
                        gaps['MISSING_PLAYER_IDENTITY'] += 1
        for fact in facts:
            category, subject = fact['category'], None
            if category != 'lineup':
                subject = registry.resolve('the_stats_api', 'player' if category == 'player_season_stats' else 'team',
                                           fact['subject_id'], as_of=clock())
                if subject is None:
                    gaps['MISSING_CANONICAL_SUBJECT'] += 1
                    continue
            event = {'fixture_id': canonical, 'category': category, 'subject_id': subject,
                'payload': fact['data'], 'source_provider': 'the_stats_api',
                'source_url': fact['source']['source_url'], 'source_sha256': fact['source']['content_hash'],
                'origin_reference': 'report:' + str(report['run_id']), 'event_time': None,
                'observed_at': fact['source']['observed_at'], 'published_at': None}
            added = store.append([event])
            counts['events_created'] += added['created']
            counts['events_reused'] += added['reused']
    if include_saved_context:
        counts.update(link_saved_context(database, store, registry, links, now))
    return {'version': 'canonical-fixture-context-link-v1', 'mode': 'SHADOW', 'external_requests': 0,
            'ticket_decision_changes': 0, 'counts': dict(counts), 'gaps': dict(gaps), 'fixture_links': links}


def link_saved_context(database, store, registry, links, now):
    counts = Counter()
    with closing(database.connect()) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        weather = []
        if 'sports_weather_snapshots' in tables:
            for fid in links:
                weather.extend(db.execute('SELECT * FROM sports_weather_snapshots WHERE fixture_id=?', (fid,)).fetchall())
    for row in weather:
        if row['fixture_id'] not in links or row['status'] != 'SOURCED' or utc(row['observed_at']) > now:
            continue
        value = json.loads(row['snapshot_json'])
        if digest(value) != row['snapshot_id']:
            raise ValueError('CORRUPT_SAVED_WEATHER_CONTEXT')
        fixture = registry.entity(links[row['fixture_id']], as_of=store.clock())
        if utc(value['kickoff']) != fixture.kickoff:
            raise ValueError('WEATHER_CANONICAL_KICKOFF_MISMATCH')
        event = {'fixture_id': fixture.entity_id, 'category': 'weather_' + row['weather_kind'].lower(),
            'subject_id': None, 'payload': value, 'source_provider': 'open_meteo',
            'source_url': row['source_url'], 'source_sha256': row['snapshot_id'],
            'origin_reference': 'sports_weather_snapshots:' + row['snapshot_id'], 'event_time': row['target_time'],
            'observed_at': row['observed_at'], 'published_at': row['provider_issue_time']}
        result = store.append([event])
        counts['weather_created'] += result['created']
        counts['weather_reused'] += result['reused']
    if 'sports_news_assertions' in tables:
        from goal_analysis.storage.sports_news import NewsStore

        claims = [claim for fid in links for claim in NewsStore(database).claims(fixture_id=fid, as_of=now)]
        for claim in claims:
            canonical = links.get(claim['fixture_id'])
            if canonical is None:
                continue
            assertion_fields = ('source_platform', 'source_key', 'source_url', 'author', 'published_at',
                'event_at', 'fixture_namespace', 'fixture_id', 'kind', 'claim', 'content_status',
                'provenance_status', 'representation')
            if digest({key: claim[key] for key in assertion_fields}) != claim['content_hash']:
                raise ValueError('CORRUPT_SAVED_NEWS_ASSERTION')
            event = {'fixture_id': canonical, 'category': 'news', 'subject_id': None, 'payload': claim,
                'source_provider': 'grok', 'source_url': claim['source_url'], 'source_sha256': claim['content_hash'],
                'origin_reference': 'sports_news_assertions:' + str(claim['assertion_id']),
                'event_time': None, 'observed_at': claim['observed_at'], 'published_at': claim['published_at']}
            result = store.append([event])
            counts['news_created'] += result['created']
            counts['news_reused'] += result['reused']
    return counts


def shadow_archive(root, report, *, clock=lambda: datetime.now(UTC)):
    """Optional post-save local index; failure cannot block the daily package."""
    path = Path(root) / 'config/canonical-contexts.json'
    if not path.exists():
        return {'status': 'DISABLED', 'mode': 'SHADOW', 'external_requests': 0}
    try:
        config = json.loads(path.read_text('utf-8-sig'))
        if config != {'enabled': True, 'mode': 'SHADOW'}:
            return {'status': 'DISABLED', 'mode': 'SHADOW', 'external_requests': 0}
        proof = link_report(root, report, clock=clock)
        from goal_analysis.jobs.competition_types import archive_competition_types

        proof['competition_types'] = archive_competition_types(root, report, as_of=clock())
        from goal_analysis.jobs.schedule_shadow import archive_schedule

        proof['team_schedule'] = archive_schedule(root, report, as_of=clock())
        from goal_analysis.jobs.rotation_shadow import archive_rotation

        proof['rotation'] = archive_rotation(root, report, as_of=clock())
        from goal_analysis.jobs.stakes_shadow import archive_stakes

        proof['standings_stakes'] = archive_stakes(root, report, as_of=clock())
        from goal_analysis.jobs.human_factors_shadow import archive_human_factors

        proof['human_factors'] = archive_human_factors(root, report, as_of=clock())
        return {**proof, 'status': 'PARTIAL' if proof['gaps'] else 'COMPLETE'}
    except Exception:  # noqa: BLE001 - do not expose provider content or block publication
        return {'status': 'UNAVAILABLE', 'mode': 'SHADOW', 'external_requests': 0,
                'reason': 'CANONICAL_CONTEXT_ARCHIVE_FAILED'}
