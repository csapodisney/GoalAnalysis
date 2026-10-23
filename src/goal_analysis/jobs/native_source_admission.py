"""Admit only existing, scoped native HISTORY sources at actual NOW.

Target metadata and prices are deliberately excluded: freshness requires a
real response, never a new local presence witness for an old cached payload.
"""
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from goal_analysis.normalization.identity import native_id
from goal_analysis.normalization.live_source import stats_source_url
from goal_analysis.storage.db import Database
from goal_analysis.storage.raw_admission import MAX_SOURCES, admit_raw_sources


def admit_history(root, fixtures):
    root = Path(root).resolve()
    if not isinstance(fixtures, list) or len(fixtures) > 1000:
        raise ValueError('BOUNDED_NATIVE_FIXTURES_REQUIRED')
    comps = set()
    for fixture in fixtures:
        if fixture.get('source_provider') == 'the_stats_api':
            comps.add(native_id(fixture['provider_league_id'], 'comp'))
    path = root/'data/goal_analysis.sqlite3'
    if not comps or not path.is_file():
        return {'status': 'NO_SCOPED_NATIVE_HISTORY', 'created': 0, 'external_requests': 0}
    clauses, params = [], []
    for comp in sorted(comps):
        token = 'competition_id='+comp
        clauses.append('(instr(source_url,?)>0 OR substr(source_url,-?)=?)')
        params.extend((token+'&', len(token), token))
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        rows = db.execute("SELECT content_hash,source_url FROM raw_snapshots WHERE provider='the_stats_api' "
            "AND source_url LIKE 'https://api.thestatsapi.com/api/football/matches?%' "
            "AND instr(source_url,'status=finished')>0 AND ("+' OR '.join(clauses)+
            ') ORDER BY content_hash LIMIT ?', (*params, MAX_SOURCES+1)).fetchall()
    if len(rows) > MAX_SOURCES:
        return {'status': 'SOURCE_LIMIT', 'created': 0, 'external_requests': 0}
    keys, rejected = [], 0
    for row in rows:
        try:
            url = urlsplit(stats_source_url(row['source_url']))
            query = parse_qs(url.query)
            if (url.path != '/api/football/matches' or query.get('status') != ['finished']
                    or len(query.get('competition_id', [])) != 1 or query['competition_id'][0] not in comps
                    or query.get('utc_offset') != ['+00:00']
                    or len(query.get('date_from', [])) != 1 or len(query.get('date_to', [])) != 1
                    or date.fromisoformat(query['date_from'][0]).isoformat() != query['date_from'][0]
                    or date.fromisoformat(query['date_to'][0]).isoformat() != query['date_to'][0]
                    or query['date_from'][0] > query['date_to'][0]):
                raise ValueError('INVALID_HISTORY_SOURCE_SCOPE')
            keys.append(row['content_hash'])
        except (ValueError, KeyError, TypeError):
            rejected += 1
    result = admit_raw_sources(Database(path), root, keys)
    return {**result, 'competition_ids': sorted(comps), 'metadata_rejected': rejected,
        'target_or_price_sources_admitted': 0,
        'status': 'GAPS' if rejected else result['status']}
