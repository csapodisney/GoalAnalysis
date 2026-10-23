"""Read exact HTTP source aliases without changing legacy raw receipts.

Opaque pointers are authoritative. Rows which lost those pointers during
normalization may resolve only the exact (bytes, URL, actual observation time)
tuple, never the newest response for those bytes. Reads never admit data.
"""
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.leakage import LookAheadError

from .http_observations import read_http_observation
from .raw_evidence import read_witnessed_raw


def source_key(source):
    return tuple(source.get(k) for k in ('content_hash', 'source_url', 'observed_at',
        'http_observation_id', 'http_receipt_id', 'http_observation_status'))


def http_source(db, root, source, cutoff):
    """None means legacy, not a failed explicit or exact HTTP observation."""
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if any(k in source for k in ('http_observation_id', 'http_receipt_id', 'http_observation_status')):
        if not {'the_stats_http_observations', 'the_stats_http_witnesses'} <= tables:
            raise ValueError('MISSING_HTTP_SOURCE_PROOF_TABLES')
        if source.get('http_observation_status') != 'VERIFIED':
            raise ValueError('UNVERIFIED_HTTP_SOURCE')
        return read_http_observation(db, root, source.get('http_observation_id'), cutoff, source=source)
    if source.get('observed_at') is None:
        return None
    if 'the_stats_http_observations' not in tables:
        return None
    if 'the_stats_http_witnesses' not in tables:
        raise ValueError('MISSING_HTTP_SOURCE_PROOF_TABLES')
    # Indexed, bounded exact lookup. No nearest/newest timestamp substitution.
    rows = db.execute('''SELECT o.response_id,w.available_at FROM the_stats_http_observations o
        LEFT JOIN the_stats_http_witnesses w USING(response_id)
        WHERE o.content_hash=? AND o.source_url=? AND o.observed_at=?
        ORDER BY w.available_at,o.response_id LIMIT 17''',
        (source['content_hash'], source['source_url'],
         utc(source['observed_at']).isoformat(timespec='microseconds'))).fetchall()
    if not rows:
        if db.execute('''SELECT 1 FROM the_stats_http_observations
            WHERE content_hash=? AND source_url=? LIMIT 1''',
            (source['content_hash'], source['source_url'])).fetchone():
            raise ValueError('MISSING_EXACT_HTTP_SOURCE_OBSERVATION')
        return None
    if len(rows) > 16:
        raise ValueError('HTTP_ALIAS_OBSERVATION_LIMIT')
    for row in rows:
        if row['available_at'] is None:
            raise ValueError('UNWITNESSED_HTTP_SOURCE_ALIAS')
        try:
            return read_http_observation(db, root, row['response_id'], cutoff)
        except LookAheadError as error:
            if not str(error).startswith('FUTURE_'):
                raise
    raise ValueError('HTTP_SOURCE_ALIAS_NOT_AVAILABLE_AT_CUTOFF')


def read_source_raw(db, row, root, cutoff, source):
    if row['content_hash'] != source['content_hash'] or row['provider'] != 'the_stats_api':
        raise ValueError('RAW_SOURCE_ALIAS_HASH_OR_PROVIDER_MISMATCH')
    actual = http_source(db, root, source, cutoff)
    if actual is not None:
        return actual
    body, proof = read_witnessed_raw(db, row, root, cutoff)
    if (proof['source_url'] != source['source_url'] or
            (source.get('observed_at') is not None and
             utc(proof['times']['observed_at']) != utc(source['observed_at']))):
        raise ValueError('RAW_SOURCE_ALIAS_URL_OR_CLOCK_MISMATCH')
    return body, proof
