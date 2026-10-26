"""Exact reviewed facility aliases, witnessed match venues and free WGS84 data.

Wikidata coordinates are community data, not surveyed positions. Official club,
municipal or destination pages independently corroborate facility identity.
No fuzzy names, home-stadium assumption, city geocoding, requests or admissions.
"""
import json
import math
import sqlite3
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.identity import field_id, native_id
from goal_analysis.normalization.leakage import require_known_before
from goal_analysis.normalization.training_datasets import sha
from goal_analysis.storage.source_observations import read_source_raw

VERSION = 'reviewed-stadium-coordinates-shadow-v1'
REVIEWED_BUNDLE = '92d68ac3f097bf6a3cb0abef945e94ce2f49e05ef42dc5d7aa7cd480dd1e3dc7'
REVIEWED_AT = '2026-10-23T23:36:57.988099+00:00'
ADDITIONAL_REVIEWED_RELEASES = (('b6131527dc3e35e1e6980205b563fefa4b629892cc47e2e84665555ce1bb4fef', 74, '2026-10-26T23:17:33.559600+00:00', 110_000),)
PREVIOUS_BUNDLE = 'f7bea9515387452b869b3b739f40568e2f8f51c03f865a38ea9decf4cab69e52'
HISTORICAL_RELEASES = (
    ('6a6ef106af3d782708df8df56aa5ce533c99302f3b7fb41eab858c21a66a9ceb',
     56, '2026-10-23T11:29:49.525605+00:00', 80_000),
    ('57926fc865a79717e7039d82c56b90cb1efa5fef37bed4d23eb2ba11544f1011',
     44, '2026-10-22T23:33:32.044417+00:00', 64_000),
    ('191c5960d56fb76b6263b8c422584471c252d9b3e2cf8da81c99c7f12e1b67d6',
     34, '2026-10-22T22:41:40.817079+00:00', 50_000),
    ('d2deaf2a7dbd580a38e6c50c5af9526093e3171c8e0b67cff2db5bb2ba3cc5e5',
     23, '2026-10-22T22:06:59.716819+00:00', 50_000),
    (PREVIOUS_BUNDLE, 6, '2026-10-22T21:25:16.306109+00:00', 50_000),
)


def _bytes(folder, digest, suffix, limit):
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('INVALID_STADIUM_SOURCE_DIGEST')
    path = folder/(digest+suffix)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError('MISSING_OR_OVERSIZED_STADIUM_SOURCE')
    raw = path.read_bytes()
    if len(raw) > limit or sha256(raw).hexdigest() != digest:
        raise ValueError('STADIUM_SOURCE_HASH_MISMATCH')
    return raw


def _times(observed, available):
    return {'observed_at': observed, 'available_at': available, 'published_at': None,
            'ingested_at': None, 'event_time': None, 'event_time_kind': 'UNKNOWN',
            'availability_basis': 'SOURCE_PRESENCE_UPPER_BOUND'}


def read_locations(root, *, as_of):
    """Pinned releases preserve what was actually reviewed at each cutoff."""
    root, cutoff = Path(root).resolve(), utc(as_of);folder = root/'data/arthur/stadium-locations'
    # Select from code-pinned availability, not whichever file happens to exist.
    # A missing/corrupt applicable release must not expose a favorable older one.
    # The 69-record packet is 103,344 bytes. Each historical release keeps its
    # own original byte bound, including the 71,834-byte fifth release.
    releases = (*ADDITIONAL_REVIEWED_RELEASES, (REVIEWED_BUNDLE, 69, REVIEWED_AT, 110_000), *HISTORICAL_RELEASES)
    release = next((r for r in releases if utc(r[2]) <= cutoff), None)
    if release is None:
        raise ValueError('STADIUM_SOURCE_NOT_KNOWN_AT_CUTOFF')
    digest, count, available, byte_limit = release
    packet = json.loads(_bytes(folder, digest, '.json', byte_limit))
    if (packet['version'] != VERSION or packet['license'] != 'CC0-1.0'
            or packet['available_at'] != packet['reviewed_at'] or packet['available_at'] != available
            or len(packet['records']) != count
            or packet['local_review_max_age_days'] != 90):
        raise ValueError('UNREVIEWED_STADIUM_SOURCE_PACKET')
    require_known_before(_times(packet['reviewed_at'], packet['available_at']), as_of=cutoff)
    if cutoff-utc(packet['reviewed_at']) > timedelta(days=90):
        raise ValueError('STADIUM_SOURCE_REVIEW_EXPIRED')
    locations = {}
    for item in packet['records']:
        q = item['wikidata_id'];wd = item['wikidata_source'];official = item['identity_source']
        raw = _bytes(folder, wd['sha256'], '.bin', 2_000_000)
        _bytes(folder, official['sha256'], '.bin', 2_000_000)
        if wd['requested_url'] != 'https://www.wikidata.org/wiki/Special:EntityData/'+q+'.json':
            raise ValueError('STADIUM_ENTITY_SOURCE_IDENTITY_MISMATCH')
        entity = json.loads(raw)['entities'][q]
        if entity['id'] != q or entity['lastrevid'] != item['lastrevid']:
            raise ValueError('STADIUM_ENTITY_REVISION_MISMATCH')
        # Refuse multiple, deprecated, time-qualified or non-value coordinates.
        claims = entity['claims']['P625']
        if (len(claims) != 1 or claims[0]['rank'] not in {'normal', 'preferred'}
                or claims[0].get('qualifiers') or claims[0]['mainsnak']['snaktype'] != 'value'):
            raise ValueError('AMBIGUOUS_OR_INAPPLICABLE_STADIUM_COORDINATES')
        point = claims[0]['mainsnak']['datavalue']['value']
        precision = point['precision']
        if (point != item['coordinate_statement'] or point['globe'] != 'http://www.wikidata.org/entity/Q2'
                or (precision is not None and (type(precision) not in (float, int)
                    or not 0 < precision <= .001))):
            raise ValueError('UNVERIFIED_STADIUM_DATUM_OR_PRECISION')
        for name, bound in (('latitude', 90), ('longitude', 180)):
            value = point[name]
            if type(value) not in (float, int) or not math.isfinite(value) or abs(value) > bound:
                raise ValueError('INVALID_STADIUM_COORDINATES')
        proofs = []
        for source in (wd, official):
            times = _times(source['observed_at'], packet['available_at'])
            require_known_before(times, as_of=cutoff)
            proofs.append({'url': source['source_url'], 'content_sha256': source['sha256'], 'times': times})
        key = (item['native_name'], item['native_city'])
        if key in locations:
            raise ValueError('AMBIGUOUS_REVIEWED_STADIUM_ALIAS')
        p = {'venue_id': 'wikidata:'+q, 'provider': 'wikidata', 'coordinate_scope': 'VERIFIED_STADIUM',
             'latitude': point['latitude'], 'longitude': point['longitude'],
             'reported_precision_degrees': precision, 'precision_is_not_accuracy_radius': True,
             'coordinate_precision_status': 'UNREPORTED' if precision is None else 'SOURCE_REPORTED',
             'native_name': key[0], 'native_city': key[1], 'native_venue_id': None,
             'quality': item['quality'], 'alias_note': item['alias_note'], 'license': packet['license'],
             'bundle_sha256': digest, 'wikidata_revision': item['lastrevid']}
        locations[key] = {'record_id': sha([p, proofs]), 'payload': p, 'payload_sha256': sha(p),
            'times': _times(packet['reviewed_at'], packet['available_at']), 'source_receipts': proofs}
    return locations


def match_locations(root, matches, *, as_of):
    """Actual exact match-detail receipts bind facilities to individual matches.

    Past match locations remain separate from a team's actual route/itinerary.
    Caller-supplied venue text/coordinates/verified flags cannot create links.
    """
    if not isinstance(matches, list) or len(matches) > 1000:
        raise ValueError('BOUNDED_LOCATION_MATCHES_REQUIRED')
    root, cutoff = Path(root).resolve(), utc(as_of);issues = Counter();result = {}
    unique = {};ambiguous = set()
    for match in matches:
        fid = native_id(match['fixture_id'], 'mt')
        if fid in unique and unique[fid] != match:ambiguous.add(fid)
        unique[fid] = match
    issues['CONFLICTING_REQUESTED_MATCH_LOCATIONS'] = len(ambiguous)
    if not ambiguous:del issues['CONFLICTING_REQUESTED_MATCH_LOCATIONS']
    matches = [m for fid,m in unique.items() if fid not in ambiguous]
    try:
        locations = read_locations(root, as_of=cutoff)
    except (ValueError, TypeError, KeyError, OSError):
        return {}, {'REVIEWED_LOCATION_SOURCE_UNAVAILABLE_AT_CUTOFF': len(matches)}
    path = root/'data/goal_analysis.sqlite3'
    if not path.is_file():return {}, {'MISSING_NATIVE_LOCATION_DATABASE': len(matches)}
    from goal_analysis.jobs.native_venue_shadow import _latest

    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cache = {};bytes_read = 0
        for match in matches:
            try:
                fid = native_id(match['fixture_id'], 'mt')
                if fid in result:continue
                descriptor = {'provider_fixture_id': fid}
                if '_venue_source' in match:descriptor['_venue_source'] = match['_venue_source']
                sources = _latest(db, descriptor, cutoff, tables)
                values = []
                for source in sources:
                    key = sha(source)
                    if key not in cache:
                        row = db.execute('''SELECT * FROM raw_snapshots WHERE provider='the_stats_api'
                            AND content_hash=?''', (source['content_hash'],)).fetchone()
                        if row is None or row['size_bytes'] > 2_000_000:
                            raise ValueError('MISSING_OR_OVERSIZED_MATCH_LOCATION_SOURCE')
                        bytes_read += row['size_bytes']
                        if bytes_read > 64*1024*1024 or len(cache) >= 1000:
                            raise ValueError('MATCH_LOCATION_SOURCE_BUDGET')
                        cache[key] = read_source_raw(db, dict(row), root, cutoff, source)
                    values.append(cache[key])
                if len({sha(body) for body, _ in values}) != 1:
                    raise ValueError('AMBIGUOUS_MATCH_LOCATION_SOURCE')
                body, proof = values[0];m = body['data']
                if (m['id'] != fid or field_id(m, 'competition') != match['competition_id']
                        or field_id(m, 'season') != match['season_id']
                        or m['home_team']['id'] != match['home_team_id'] or m['away_team']['id'] != match['away_team_id']
                        or utc(m['utc_date']) != utc(match['kickoff']) or m['status'] != match['status']
                        or m['status'] not in {'scheduled', 'finished'} or type(m.get('is_neutral')) is not bool):
                    raise ValueError('MATCH_LOCATION_IDENTITY_OR_STATUS_CONFLICT')
                if m['status'] == 'scheduled' and not 0 <= (cutoff-utc(proof['times']['observed_at'])).total_seconds() <= 1800:
                    raise ValueError('STALE_CURRENT_MATCH_LOCATION')
                if m['status'] == 'finished' and utc(m['utc_date']) >= cutoff:
                    raise ValueError('FUTURE_COMPLETED_MATCH_LOCATION')
                venue = m.get('venue') or {};location = locations.get((venue.get('name'), venue.get('city')))
                if location is None:raise ValueError('NO_EXACT_REVIEWED_STADIUM_ALIAS')
                p = {**deepcopy(location['payload']), **{k: match[k] for k in (
                    'fixture_id', 'competition_id', 'season_id', 'home_team_id', 'away_team_id', 'kickoff', 'status')},
                    'fixture_provider': 'the_stats_api', 'is_neutral': m['is_neutral'],
                    'binding_basis': 'EXACT_WITNESSED_MATCH_DETAIL_AND_REVIEWED_FACILITY_ALIAS'}
                times = _times(max((location['times']['observed_at'], proof['times']['observed_at']),key=utc),
                               max((location['times']['available_at'], proof['times']['available_at']),key=utc))
                require_known_before(times, as_of=cutoff)
                result[fid] = {'record_id': sha([p, proof, location['record_id']]), 'payload': p,
                    'payload_sha256': sha(p), 'times': times,
                    'source_receipts': deepcopy(location['source_receipts'])+[proof]}
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
                issues[str(error) if isinstance(error, ValueError) else type(error).__name__] += 1
    return result, dict(issues)


def fixture_location(root, fixture, *, as_of):
    from goal_analysis.jobs.schedule_shadow import target_fixture

    match = target_fixture(fixture)
    if fixture.get('_venue_source'):match['_venue_source'] = fixture['_venue_source']
    records, _ = match_locations(root, [match], as_of=as_of)
    return records.get(match['fixture_id'])
