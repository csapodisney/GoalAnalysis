"""Read actual match-venue metadata; coordinates need a separate proven source.

The audited TheStats contract exposes name/city, not a venue ID or coordinates.
Do not invent an ID from a name, join a home stadium, or reinterpret extra fields
as an undocumented coordinate contract. This reader never fetches or admits data.
"""
import sqlite3
from collections import Counter
from contextlib import closing, nullcontext
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs.xg_specialist_shadow import _targets
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.live_identity import fixture_identity
from goal_analysis.normalization.training_datasets import snapshot
from goal_analysis.storage.source_observations import read_source_raw, source_key

VERSION = 'native-match-venue-metadata-shadow-v1'
MAX_BYTES = 64*1024*1024
CONTRACT = 'https://www.thestatsapi.com/docs/api-reference/matches/get'


def _latest(db, fixture, cutoff, tables):
    """An explicit pointer is authoritative; no fallback from a failed source."""
    url = 'https://api.thestatsapi.com/api/football/matches/'+fixture['provider_fixture_id']
    if '_venue_source' in fixture:
        source = fixture['_venue_source']
        if (not isinstance(source, dict) or source.get('source_url') != url
                or source.get('provider', 'the_stats_api') != 'the_stats_api'):
            raise ValueError('INVALID_EXPLICIT_MATCH_VENUE_SOURCE')
        return [source]
    sources = []
    stamp = cutoff.isoformat(timespec='microseconds')
    if 'the_stats_http_observations' in tables:
        if 'the_stats_http_witnesses' not in tables:
            raise ValueError('MISSING_HTTP_SOURCE_PROOF_TABLES')
        records = db.execute('''SELECT o.*,w.receipt_id FROM the_stats_http_observations o
            LEFT JOIN the_stats_http_witnesses w USING(response_id)
            WHERE o.source_url=? AND o.observed_at<=?
            ORDER BY o.observed_at DESC,o.response_id LIMIT 17''', (url, stamp)).fetchall()
        # An overfull same-time group must not become a hash-order winner.
        if len(records) > 16 and records[0]['observed_at'] == records[-1]['observed_at']:
            raise ValueError('VENUE_SOURCE_ALIAS_LIMIT')
        for r in records:
            if utc(r['observed_at']) <= cutoff:
                sources.append({'provider':'the_stats_api', 'content_hash':r['content_hash'], 'source_url':url,
                    'observed_at':r['observed_at'], 'http_observation_id':r['response_id'],
                    'http_receipt_id':r['receipt_id'], 'http_observation_status':'VERIFIED'})
    records = db.execute('''SELECT * FROM raw_snapshots WHERE provider='the_stats_api'
        AND source_url=? AND fetched_at<=? ORDER BY fetched_at DESC,content_hash LIMIT 17''',
        (url, stamp)).fetchall()
    if len(records) > 16 and records[0]['fetched_at'] == records[-1]['fetched_at']:
        raise ValueError('VENUE_RAW_ALIAS_LIMIT')
    for r in records:
        if utc(r['fetched_at']) <= cutoff:
            sources.append({'content_hash':r['content_hash'], 'source_url':url, 'observed_at':r['fetched_at']})
    if not sources:
        raise ValueError('MISSING_ARCHIVED_MATCH_VENUE_DETAIL')
    latest = max(utc(s['observed_at']) for s in sources)
    sources = [s for s in sources if utc(s['observed_at']) == latest]
    # An explicit HTTP observation supersedes the identical legacy tuple, never
    # the other way round. read_source_raw still checks the exact receipt.
    tuples = {(s['content_hash'], utc(s['observed_at'])) for s in sources if 'http_observation_id' in s}
    return [s for s in sources if 'http_observation_id' in s or
            (s['content_hash'], utc(s['observed_at'])) not in tuples]


def _metadata(match, fixture):
    expected = (fixture['provider_fixture_id'], fixture['provider_league_id'],
        fixture['provider_season_id'], fixture['provider_home_team_id'], fixture['provider_away_team_id'])
    actual = (match['id'], match['competition_id'], match['season_id'],
        match['home_team']['id'], match['away_team']['id'])
    if (actual != expected or utc(match['utc_date']) != utc(fixture['kickoff'])
            or match.get('status') != 'scheduled' or match.get('is_neutral') is not False):
        raise ValueError('MATCH_VENUE_IDENTITY_STATUS_OR_NEUTRAL_CONFLICT')
    venue = match.get('venue')
    if not isinstance(venue, dict):
        raise ValueError('MISSING_MATCH_VENUE_METADATA')  # noqa: TRY004 - stable missing-data reason
    for field in ('name', 'city'):
        text = venue.get(field)
        if (not isinstance(text, str) or not 2 <= len(text.strip()) <= 180
                or any(ord(c) < 32 for c in text)):
            raise ValueError('INVALID_OR_MISSING_VENUE_'+field.upper())
    return {'name':venue['name'], 'city':venue['city'], 'basis':'EXACT_MATCH_DETAIL',
        'provider_venue_id':None, 'latitude':None, 'longitude':None,
        'coordinate_status':'UNKNOWN', 'coordinate_reason':'NO_DOCUMENTED_NATIVE_VENUE_ID_OR_COORDINATES',
        'undocumented_fields_present':sorted(set(venue)-{'name','city'}),
        'coordinate_contract':None}


def inspect_native_venues(root, fixtures, *, as_of):
    """Bounded RO source audit. Verified text is not a verified stadium location."""
    if not isinstance(fixtures, list) or len(fixtures) > 1000:
        raise ValueError('BOUNDED_NATIVE_VENUE_FIXTURES_REQUIRED')
    identifiers = [fixture_identity(f) for f in fixtures]
    if len(set(identifiers)) != len(identifiers) or any(f['source_provider'] != 'the_stats_api' for f in fixtures):
        raise ValueError('UNIQUE_NATIVE_VENUE_FIXTURES_REQUIRED')
    root, cutoff = Path(root).resolve(), utc(as_of)
    targets, target_proofs, target_gaps = _targets(root, {f['fixture_id']:f for f in fixtures}, cutoff)
    rows, lineage, issues, cache = [], [], Counter(), {}
    path = root/'data/goal_analysis.sqlite3'
    bytes_read = 0
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) if path.is_file() else nullcontext(None) as db:
        tables = set()
        if db is not None:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for fixture in fixtures:
            fid = fixture['fixture_id']
            row = {'fixture_id':fid, 'kickoff':fixture['kickoff'], 'status':'UNKNOWN', 'reason':None,
                'venue_metadata':None, 'source_receipts':[], 'target_receipt':target_proofs.get(fid),
                'target_is_fresh_and_verified':fid in targets, 'metadata_prematch_eligible':False,
                'metadata_prematch_gap':'UNVERIFIED_MATCH_VENUE_METADATA',
                'coordinate_status':'UNKNOWN', 'location_ready':False,
                'probability_adjustment':0, 'ranking_multiplier':1., 'live_decision_effect':False}
            try:
                if 'raw_snapshots' not in tables:
                    raise ValueError('MISSING_VENUE_DATABASE')
                sources = _latest(db, fixture, cutoff, tables)
                if len({s['content_hash'] for s in sources}) != 1:
                    raise ValueError('AMBIGUOUS_LATEST_MATCH_VENUE_DETAIL')
                for source in sources:
                    key = source_key(source)
                    if key not in cache:
                        meta = db.execute("SELECT * FROM raw_snapshots WHERE content_hash=? AND provider='the_stats_api'",
                            (source['content_hash'],)).fetchone()
                        if meta is None or not 0 < meta['size_bytes'] <= 2_000_000:
                            raise ValueError('MISSING_OR_OVERSIZED_VENUE_SOURCE')
                        if len(cache) >= 1000 or bytes_read+meta['size_bytes'] > MAX_BYTES:
                            raise ValueError('VENUE_SOURCE_BUDGET_EXCEEDED')
                        cache[key] = read_source_raw(db, dict(meta), root, cutoff, source)
                        bytes_read += meta['size_bytes']
                    body, proof = cache[key]
                    match = body.get('data')
                    if not isinstance(match, dict):
                        raise ValueError('MATCH_VENUE_DETAIL_OBJECT_REQUIRED')  # noqa: TRY004 - source schema reason
                    venue = _metadata(match, fixture)
                    row['source_receipts'].append(proof)
                    lineage.append({'namespace':'raw', **proof})
                row.update(status='VENUE_METADATA_VERIFIED_SHADOW', venue_metadata=venue,
                    reason='VENUE_ID_AND_COORDINATE_SOURCE_MISSING')
                fresh = all(0 <= (cutoff-utc(p['times']['observed_at'])).total_seconds() <= 1800
                            for p in row['source_receipts'])
                row['metadata_prematch_eligible'] = bool(fresh and fid in targets and
                    utc(fixture['kickoff']) > cutoff+timedelta(minutes=15))
                row['metadata_prematch_gap'] = (
                    'PREMATCH_LOCKOUT' if utc(fixture['kickoff']) <= cutoff+timedelta(minutes=15) else
                    'STALE_MATCH_VENUE_DETAIL' if not fresh else
                    'UNVERIFIED_FRESH_NATIVE_TARGET' if fid not in targets else None)
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
                row.update(reason=str(error) if isinstance(error, ValueError) else type(error).__name__,
                    source_receipts=[], venue_metadata=None)
            issues[row['reason']] += 1
            rows.append(row)
    recipe = {'version':VERSION, 'implementation_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_contract':CONTRACT, 'documented_fields':['name','city'],
        'coordinate_contract':None, 'mode':'SHADOW', 'max_source_bytes':MAX_BYTES,
        'source_freshness_seconds':1800, 'lockout_seconds':900, 'live_decision_effect':False}
    dataset = snapshot(rows, role='native_match_venue_source_shadow', as_of=cutoff, recipe=recipe, lineage=lineage)
    return {'version':VERSION, 'mode':'SHADOW', 'rows':rows, 'dataset':dataset,
        'issues':dict(issues), 'target_issues':target_gaps, 'external_requests':0, 'production_writes':0,
        'probability_adjustment':0, 'ranking_multiplier':1., 'live_decision_effect':False,
        'summary':{'fixtures':len(rows), 'verified_match_venue_metadata':sum(r['venue_metadata'] is not None for r in rows),
            'prematch_eligible_metadata':sum(r['metadata_prematch_eligible'] for r in rows),
            'verified_venue_coordinate_pairs':0}}


def review_venue_options(root, fixtures, options, specialists, *, as_of):
    """Attach source gaps to each research leg; failure cannot veto other experts."""
    try:
        before = canonical_sha256([fixtures, options, specialists])
        audit = inspect_native_venues(root, fixtures, as_of=as_of)
        from goal_analysis.jobs.schedule_shadow import target_fixture
        from goal_analysis.jobs.stadium_locations_shadow import match_locations

        locations, location_issues = match_locations(root, [target_fixture(f) for f in fixtures], as_of=as_of)
        for row in audit['rows']:
            location = locations.get(row['fixture_id'].removeprefix('the_stats_api:'))
            row['stadium_location'] = location
            row['location_ready'] = bool(location and row['metadata_prematch_eligible'])
            if location is not None:
                row['coordinate_status'] = 'SOURCE_BOUND_STADIUM_COORDINATES_SHADOW'
        audit['location_issues'] = location_issues
        audit['summary']['verified_venue_coordinate_pairs'] = len(locations)
        audit['dataset'] = snapshot(audit['rows'],role='native_match_venue_location_shadow',as_of=utc(as_of),
            recipe={'version':'native-match-venue-with-reviewed-coordinates-v1',
                'metadata_implementation_sha256':sha256(Path(__file__).read_bytes()).hexdigest()},
            lineage=[audit['dataset'],*locations.values()])
        rows = {r['fixture_id']:(i, r) for i, r in enumerate(audit['rows'])}
        count = sum(len(o['recommendation']['legs']) for values in options.values() for o in values)
        count += sum(len(s.get('candidate_legs', [])) for s in specialists.values())
        if count > 10000:
            raise ValueError('BOUNDED_VENUE_LEG_BINDINGS_REQUIRED')

        def bind(leg):
            index, row = rows.get(leg.get('fixture_id'), (None, {}))
            return {'fixture_id':leg.get('fixture_id'), 'leg_sha256':canonical_sha256(leg),
                'context_row_index':index, 'context_sha256':canonical_sha256(row) if row else None,
                'dataset_id':audit['dataset']['manifest']['dataset_id'],
                'data_use':'SOURCE_BOUND_STADIUM_LOCATION_CONTEXT' if row.get('location_ready') else
                    'VENUE_METADATA_ONLY_LOCATION_UNPROVEN' if row.get('venue_metadata') else 'WITHHELD_UNVERIFIED_VENUE',
                'location_ready':row.get('location_ready',False), 'probability_adjustment':0, 'ranking_multiplier':1.}

        reviews = [{'profile_id':key, 'option_index':i, 'option_sha256':canonical_sha256(option),
            'legs':[bind(leg) for leg in option['recommendation']['legs']]}
            for key, values in options.items() for i, option in enumerate(values)]
        specialist_reviews = {key:[bind(leg) for leg in s.get('candidate_legs', [])] for key, s in specialists.items()}
        if before != canonical_sha256([fixtures, options, specialists]):
            raise ValueError('VENUE_SHADOW_INPUT_MUTATED')
        return {'mode':'SHADOW', 'status':'OBSERVED', 'source_audit':audit, 'option_reviews':reviews,
            'specialist_reviews':specialist_reviews, 'leg_bindings':count, 'input_sha256':before,
            'external_requests':0, 'production_writes':0, 'live_decision_effect':False}
    except Exception:  # noqa: BLE001 - optional diagnostic cannot veto other experts or cards
        return {'mode':'SHADOW', 'status':'UNAVAILABLE', 'reason':'NATIVE_VENUE_SOURCE_AUDIT_FAILED',
            'option_reviews':[], 'specialist_reviews':{}, 'leg_bindings':0, 'live_decision_effect':False}
