"""Read an existing calendar cache, or configured routing, without collecting data."""
import json
import re
import sqlite3
import time as elapsed
from contextlib import closing
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from goal_analysis.providers.the_stats_leagues import LEAGUES

VERSION = 'readonly-competition-catalogue-v1'
NAMESPACE = 'thestatsapi-context-v1'
BASE_URL = 'https://api.thestatsapi.com/api/football/matches'
MAX_PAGES, MAX_RAW_BYTES, MAX_TOTAL_BYTES = 10, 2 * 1024 * 1024, 20 * 1024 * 1024
FRESH_FOR = timedelta(minutes=5)
SERIE_C = {138, 942, 943}


def _stamp(value):
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        raise ValueError('INVALID_AWARE_CLOCK') from None
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('INVALID_AWARE_CLOCK')
    return result.astimezone(UTC)


def _query(target, page):
    offset = datetime.combine(target, time(12), ZoneInfo('Europe/Berlin')).strftime('%z')
    return dict(sorted({'date_from': target.isoformat(), 'date_to': target.isoformat(),
                        'utc_offset': offset[:3] + ':' + offset[3:],
                        'page': page, 'per_page': 100}.items()))


def _configured(config):
    rows, seen = [], set()
    for item in config.get('leagues', []):
        key = item.get('api_football_id')
        if type(key) is not int or not 0 < key <= 1_000_000 or key in seen:
            raise ValueError('INVALID_OR_DUPLICATE_CONFIGURED_ID')
        seen.add(key)
        native = LEAGUES.get(str(key))
        rows.append({'id': key, 'provider_id': native[0] if native else None,
            'name': native[1] if native else str(item.get('competition_id') or key),
            'country': (native[2] or '') if native else '', 'season': item.get('season'),
            'type': 'NATIONAL' if item.get('competition_type') == 'NATIONAL' else 'CLUB',
            'configured': True, 'default_selected': native is not None,
            'provider_mapping_status': 'EXACT_CONFIGURED_NATIVE_ROUTING' if native else 'UNKNOWN_UNMAPPED_CONFIG_ID',
            'fixture_count': None, 'fixtures': [], 'fallback': False,
            'availability': 'UNKNOWN', 'provenance': 'CONFIGURED_ROUTING_ONLY'})
    return rows


def _page(db, root, target, page, now):
    query = _query(target, page)
    key = json.dumps(['/football/matches', query], sort_keys=True, separators=(',', ':'))
    row = db.execute('SELECT value_json,content_hash,created_at,expires_at FROM cache_entries '
                     'WHERE namespace=? AND cache_key=?', (NAMESPACE, key)).fetchone()
    if row is None:
        raise ValueError('MISSING_CALENDAR_CACHE_PAGE')
    if len(row['value_json'].encode('utf-8')) > 65536:
        raise ValueError('CACHE_ENVELOPE_SIZE_EXCEEDED')
    if sha256(row['value_json'].encode('utf-8')).hexdigest() != row['content_hash']:
        raise ValueError('CORRUPT_CACHE_ENVELOPE')
    created, expires = _stamp(row['created_at']), _stamp(row['expires_at'])
    source = json.loads(row['value_json'])['source']
    observed = _stamp(source['observed_at'])
    expected = BASE_URL + '?' + urlencode(query)
    if (source.get('provider') != 'the_stats_api' or source.get('source_url') != expected
            or source.get('published_at') is not None or not re.fullmatch('[0-9a-f]{64}', str(source.get('content_hash')))
            or observed != created or not created < expires or observed > now):
        raise ValueError('CACHE_SOURCE_SCOPE_OR_CLOCK_MISMATCH')
    meta = db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?', (source['content_hash'],)).fetchone()
    if meta is None:
        raise ValueError('MISSING_PHYSICAL_RAW_METADATA')
    raw_root = (root / 'data/raw').resolve()
    raw_path = (raw_root / meta['relative_path']).resolve()
    if not raw_path.is_relative_to(raw_root):
        raise ValueError('RAW_PATH_OUTSIDE_ARCHIVE')
    if (meta['provider'] != 'the_stats_api' or meta['resource_type'] != 'daily_context'
            or type(meta['size_bytes']) is not int or not 0 <= meta['size_bytes'] <= MAX_RAW_BYTES
            or not raw_path.is_file() or raw_path.stat().st_size != meta['size_bytes']):
        raise ValueError('RAW_METADATA_SCOPE_OR_SIZE_MISMATCH')
    raw = raw_path.read_bytes()
    if sha256(raw).hexdigest() != source['content_hash']:
        raise ValueError('RAW_PHYSICAL_HASH_MISMATCH')
    proof_kind = 'EXACT_PHYSICAL_CACHE_SOURCE_NOT_NEW_HTTP'
    observation = None
    if any(key in source for key in ('http_observation_id', 'http_receipt_id', 'http_observation_status')):
        try:
            from goal_analysis.storage.http_observations import read_http_observation

            if source.get('http_observation_status') != 'VERIFIED':
                raise ValueError('INVALID_EXPLICIT_HTTP_CACHE_PROOF')
            status = db.execute('SELECT http_status,authenticated FROM the_stats_http_observations '
                                'WHERE response_id=?', (source.get('http_observation_id'),)).fetchone()
            if status is None or status['http_status'] != 200 or status['authenticated'] != 1:
                raise ValueError('INVALID_EXPLICIT_HTTP_CACHE_PROOF')
            body, observation = read_http_observation(db, root, source.get('http_observation_id'), now, source=source)
            proof_kind = 'EXISTING_AUTHENTICATED_HTTP_CACHE_OBSERVATION_NOT_QUOTE_TIME'
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
            raise ValueError('INVALID_EXPLICIT_HTTP_CACHE_PROOF') from None
    else:
        if meta['source_url'] != expected or _stamp(meta['fetched_at']) != observed:
            raise ValueError('RAW_METADATA_SCOPE_OR_SIZE_MISMATCH')
        body = json.loads(raw)
    details, data = body.get('meta', {}), body.get('data')
    total = details.get('total_pages')
    if (not isinstance(data, list) or len(data) > 100 or details.get('page') != page
            or type(details.get('page')) is not int or details.get('per_page') != 100
            or type(total) is not int or not 0 <= total <= MAX_PAGES
            or (data and not page <= total) or (not data and total != 0)):
        raise ValueError('INVALID_OR_BOUNDED_CALENDAR_PAGINATION')
    return data, total, {'content_hash': source['content_hash'], 'source_url': expected,
        'observed_at': source['observed_at'], 'cache_expires_at': row['expires_at'],
        'cache_fresh': now < expires and now - observed < FRESH_FOR,
        'physical_raw_bytes': len(raw), 'page': page, 'source_proof_kind': proof_kind,
        'existing_http_observation_proof': observation,
        'raw_first_source_url': meta['source_url'], 'raw_first_fetched_at': meta['fetched_at']}


def read_competition_catalogue(root, config, target, *, as_of, connection_factory=None):
    """No provider, credential, storage constructor, schema or cache mutation."""
    root, now = Path(root).resolve(), _stamp(as_of)
    if type(target) is not date:
        raise ValueError('EXACT_TARGET_DATE_REQUIRED')
    configured = _configured(config)
    result = {'version': VERSION, 'date': target.isoformat(), 'competitions': configured,
        'status': 'CONFIGURED_FALLBACK', 'cache_status': 'MISSING_DB', 'sources': [],
        'provider_requests': 0, 'cache_writes': 0, 'database_initializations': 0,
        'credential_decryptions': 0, 'fixture_counts_known': False,
        'source_proof_kind': 'PHYSICAL_CACHE_HASH_URL_CLOCK_NOT_NEW_HTTP_OR_AVAILABILITY_ADMISSION',
        'serie_c_fallback_below': 15, 'refresh_semantics': 'LOCAL_READ_ONLY_NORMAL_COLLECTOR_REFRESHES_SOURCES'}
    path = root / 'data/goal_analysis.sqlite3'
    if not path.is_file():
        return result
    started = elapsed.monotonic()
    factory = connection_factory if connection_factory is not None else sqlite3.connect
    try:
        with closing(factory(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            db.set_progress_handler(lambda: int(elapsed.monotonic() - started > 2), 1000)
            rows, total, first = _page(db, root, target, 1, now)
            sources = [first]
            for page in range(2, total + 1):
                batch, declared, proof = _page(db, root, target, page, now)
                if declared != total:
                    raise ValueError('INCONSISTENT_CALENDAR_PAGINATION')
                rows.extend(batch)
                sources.append(proof)
            if sum(source['physical_raw_bytes'] for source in sources) > MAX_TOTAL_BYTES:
                raise ValueError('TOTAL_RAW_SIZE_EXCEEDED')
        grouped, seen = {}, set()
        for row in rows:
            identifier, comp = row['id'], row.get('competition_id')
            nested = row.get('competition')
            if (not re.fullmatch(r'mt_\d{1,30}', str(identifier)) or identifier in seen
                    or not re.fullmatch(r'comp_\d{1,30}', str(comp))
                    or isinstance(nested, dict) and nested.get('id') != comp
                    or _stamp(row['utc_date']).astimezone(ZoneInfo('Europe/Berlin')).date() != target):
                raise ValueError('INVALID_DUPLICATE_CROSSDATE_OR_CONFLICTING_FIXTURE_IDENTITY')
            seen.add(identifier)
            grouped.setdefault(comp, []).append(row)
        entries = {row['id']: row for row in deepcopy(configured)}
        for key, native in LEAGUES.items():
            if native[0] in grouped and int(key) not in entries:
                entries[int(key)] = {'id': int(key), 'provider_id': native[0], 'name': native[1],
                    'country': native[2] or '', 'season': target.year,
                    'type': 'NATIONAL' if int(key) in {5, 10} else 'CLUB', 'configured': False,
                    'default_selected': int(key) in {5, 10}, 'provider_mapping_status': 'EXACT_NATIVE_ROUTING',
                    'fixture_count': None, 'fixtures': [], 'fallback': False}
        pending = sum(row.get('status') == 'scheduled' and _stamp(row['utc_date']) > now + timedelta(minutes=15)
                      for item in configured if item['provider_id'] for row in grouped.get(item['provider_id'], []))
        fresh = all(source['cache_fresh'] for source in sources)
        for item in entries.values():
            if item['provider_id'] is None:
                continue
            matches = grouped.get(item['provider_id'], [])
            item.update(fixture_count=len(matches), availability='SNAPSHOT_FIXTURES' if matches else 'NO_FIXTURES_AT_SNAPSHOT',
                provenance='VERIFIED_CACHED_RAW' if fresh else 'STALE_VERIFIED_CACHED_RAW',
                source_observed_at=min(s['observed_at'] for s in sources),
                fixtures=[{'home': x['home_team']['name'], 'away': x['away_team']['name'],
                           'kickoff': _stamp(x['utc_date']).isoformat()} for x in matches[:5]])
            if item['id'] in SERIE_C and pending < 15 and matches:
                item.update(fallback=True, default_selected=True)
        result.update(competitions=list(entries.values()), status='CACHED_SNAPSHOT' if fresh else 'STALE_SNAPSHOT',
            cache_status='VALID', sources=sources, fixture_counts_known=all(x['provider_id'] for x in configured))
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError) as error:
        codes = {'INVALID_AWARE_CLOCK', 'MISSING_CALENDAR_CACHE_PAGE', 'CACHE_ENVELOPE_SIZE_EXCEEDED',
            'CORRUPT_CACHE_ENVELOPE', 'CACHE_SOURCE_SCOPE_OR_CLOCK_MISMATCH',
            'MISSING_PHYSICAL_RAW_METADATA', 'RAW_PATH_OUTSIDE_ARCHIVE',
            'RAW_METADATA_SCOPE_OR_SIZE_MISMATCH', 'RAW_PHYSICAL_HASH_MISMATCH',
            'INVALID_OR_BOUNDED_CALENDAR_PAGINATION', 'INCONSISTENT_CALENDAR_PAGINATION',
            'TOTAL_RAW_SIZE_EXCEEDED', 'INVALID_DUPLICATE_CROSSDATE_OR_CONFLICTING_FIXTURE_IDENTITY',
            'INVALID_EXPLICIT_HTTP_CACHE_PROOF'}
        result['cache_status'] = str(error) if str(error) in codes else 'CACHE_UNAVAILABLE_OR_INVALID'
    return result
