"""Targeted physical raw availability witnesses, at actual read time only.

This is NOT an HTTP observation, quote timestamp, cache refresh or proof that
data was available historically. Existing source rows/receipts never change.
"""
import re
from contextlib import closing
from datetime import UTC, datetime

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.temporal import project_times

from .canonical_registry import digest, encoded
from .http_observations import _raw
from .raw_evidence import read_witnessed_raw
from .temporal import TemporalStore, _source_hash

MAX_SOURCES = 200
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def admit_raw_sources(database, root, keys, *, clock=lambda: datetime.now(UTC)):
    if (not isinstance(keys, list) or len(keys) > MAX_SOURCES
            or any(not isinstance(k, str) or not re.fullmatch('[0-9a-f]{64}', k) for k in keys)
            or len(set(keys)) != len(keys)):
        raise ValueError('BOUNDED_EXPLICIT_RAW_KEYS_REQUIRED')
    result = {'selected': len(keys), 'created': 0, 'reused': 0, 'rejected': [],
        'kind': 'CURRENT_PHYSICAL_SOURCE_PRESENCE_NOT_HTTP_OR_QUOTE_TIME',
        'external_requests': 0, 'original_rows_modified': 0}
    if not keys:
        return {**result, 'status': 'NO_SOURCES'}
    TemporalStore(database, root/'data/raw').initialize()
    prepared, total = [], 0
    with closing(database.connect()) as db:
        bound = db.execute('SELECT max(available_at) FROM temporal_receipts').fetchone()[0]
    last_clock = utc(bound) if bound is not None else None
    for key in sorted(keys):
        try:
            # NEW committed source read, verified bytes, THEN actual clock.
            with closing(database.connect()) as db:
                meta = db.execute("SELECT size_bytes FROM raw_snapshots WHERE content_hash=? AND provider='the_stats_api'",
                                  (key,)).fetchone()
                if meta is None or not 0 < meta[0] <= MAX_TOTAL_BYTES-total:
                    raise ValueError('MISSING_SOURCE_OR_RAW_BYTE_BUDGET')
                row, _ = _raw(db, root, key)
                total += row['size_bytes']
                available = utc(clock())
                if last_clock is not None and available < last_clock:
                    raise ValueError('CLOCK_MOVED_BACKWARDS')
                last_clock = available
                prior = db.execute("SELECT * FROM temporal_receipts WHERE namespace='raw' AND source_key=?",
                                   (key,)).fetchall()
                if prior:
                    if len(prior) != 1:
                        raise ValueError('AMBIGUOUS_RAW_RECEIPT')
                    read_witnessed_raw(db, row, root, available)
                    result['reused'] += 1
                    continue
            times = project_times('raw', row, available_at=available)
            source_hash = _source_hash(row)
            values = (digest(['raw', key, source_hash]), 'raw', key, source_hash,
                times['observed_at'], times['published_at'], times['ingested_at'],
                times['available_at'], encoded(times))
            prepared.append((key, source_hash, values))
        except (ValueError, KeyError, TypeError, OSError) as error:
            if str(error) == 'CLOCK_MOVED_BACKWARDS':
                raise
            result['rejected'].append({'source_key': key, 'reason': str(error)})
    with database.transaction(immediate=True) as db:
        for key, expected, values in prepared:
            row = db.execute("SELECT * FROM raw_snapshots WHERE content_hash=? AND provider='the_stats_api'",
                             (key,)).fetchone()
            prior = db.execute("SELECT source_sha256 FROM temporal_receipts WHERE namespace='raw' AND source_key=?",
                               (key,)).fetchall()
            if (row is None or _source_hash(dict(row)) != expected
                    or prior and (len(prior) != 1 or prior[0][0] != expected)):
                result['rejected'].append({'source_key': key, 'reason': 'SOURCE_CHANGED_AFTER_COMMITTED_READ'})
                continue
            created = db.execute('INSERT OR IGNORE INTO temporal_receipts VALUES (?,?,?,?,?,?,?,?,?)', values).rowcount
            result['created'] += created
            result['reused'] += not created
    return {**result, 'verified_bytes': total, 'status': 'GAPS' if result['rejected'] else 'WITNESSED_NOW'}
