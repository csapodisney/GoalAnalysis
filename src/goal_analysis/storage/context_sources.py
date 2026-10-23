"""Bounded discovery of actual calendar/XI observations, never new receipts.

The first raw URL/time identifies an immutable archive entry. Later HTTP
responses may bind those same bytes to another URL/time. Select the latest
known observation per exact bytes/URL, then validate it with read_source_raw.
Discovery is not proof; invalid selected observations cannot fall back to an
older convenient raw receipt.
"""
from datetime import timedelta

from goal_analysis.normalization.canonical import utc


def context_sources(db, *, cutoff, where, params, limit, max_bytes, max_age_hours=None):
    """Internal SQL scope uses a.source_url; callers supply only fixed clauses."""
    cutoff = utc(cutoff)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    http = '''SELECT NULL AS content_hash,NULL AS source_url,NULL AS observed_at,
        NULL AS response_id,NULL AS receipt_id WHERE 0'''
    args = []
    if {'the_stats_http_observations', 'the_stats_http_witnesses'} <= tables:
        http = '''SELECT content_hash,source_url,observed_at,response_id,receipt_id
            FROM (SELECT o.*,w.receipt_id,
                row_number() OVER (PARTITION BY o.content_hash,o.source_url
                    ORDER BY o.observed_at DESC,w.available_at DESC,o.response_id) AS ordinal
                FROM the_stats_http_observations o JOIN the_stats_http_witnesses w USING(response_id)
                WHERE julianday(o.observed_at)<=julianday(?)
                AND julianday(o.ingested_at)<=julianday(?)
                AND julianday(w.available_at)<=julianday(?) AND ('''+where.replace(
                    'a.source_url','o.source_url')+''')) WHERE ordinal=1'''
        args.extend([cutoff.isoformat()]*3)
        args.extend(params)
    age = ''
    if max_age_hours is not None:
        age = ' AND julianday(a.observed_at)>=julianday(?)'
    args.extend(params)  # legacy raw scope
    args.extend(params)  # outer alias scope
    args.append(cutoff.isoformat())
    if max_age_hours is not None:
        args.append((cutoff-timedelta(hours=max_age_hours)).isoformat())
    args.append(limit+1)
    rows = db.execute('WITH available_http AS ('+http+'''), aliases AS (
        SELECT content_hash,source_url,fetched_at AS observed_at,NULL AS response_id,NULL AS receipt_id
        FROM raw_snapshots r WHERE provider='the_stats_api' AND ('''+where.replace(
            'a.source_url','r.source_url')+''')
        AND NOT EXISTS (SELECT 1 FROM available_http h
            WHERE h.content_hash=r.content_hash AND h.source_url=r.source_url)
        UNION ALL SELECT * FROM available_http)
        SELECT r.*,a.source_url AS alias_url,a.observed_at AS alias_observed,
            a.response_id AS alias_id,a.receipt_id AS alias_receipt
        FROM aliases a JOIN raw_snapshots r USING(content_hash)
        WHERE r.provider='the_stats_api' AND ('''+where+''')
        AND julianday(a.observed_at)<=julianday(?)'''+age+'''
        ORDER BY a.observed_at DESC,a.content_hash,a.source_url LIMIT ?''', args).fetchall()
    if len(rows)>limit or sum(r['size_bytes'] for r in rows)>max_bytes:
        raise ValueError('CONTEXT_SOURCE_BUDGET_EXCEEDED')
    result = []
    for value in rows:
        row = dict(value)
        source = {'provider':'the_stats_api', 'content_hash':row['content_hash'],
                  'source_url':row.pop('alias_url')}
        observed, identifier, receipt = (row.pop(k) for k in
            ('alias_observed','alias_id','alias_receipt'))
        # SQLite time comparison is coarser than Python at the microsecond edge.
        if utc(observed)>cutoff or (max_age_hours is not None and
                cutoff-utc(observed)>timedelta(hours=max_age_hours)):
            continue
        if identifier is not None:
            source.update(observed_at=observed,http_observation_id=identifier,
                          http_receipt_id=receipt,http_observation_status='VERIFIED')
        result.append((row,source))
    return result
