"""Read one actual hash/receipt-bound raw source at a known UTC cutoff."""
import hashlib
import json

from goal_analysis.normalization.leakage import require_known_before
from goal_analysis.normalization.live_source import stats_source_url
from goal_analysis.normalization.temporal import project_times
from goal_analysis.storage.canonical_registry import digest, encoded
from goal_analysis.storage.temporal import _source_hash


def read_witnessed_raw(db, row, root, cutoff):
    witness = db.execute("SELECT * FROM temporal_receipts WHERE namespace='raw' AND source_key=?",
                         (row['content_hash'],)).fetchone()
    if witness is None:
        raise ValueError('UNWITNESSED_SOURCE')
    if _source_hash(row) != witness['source_sha256']:
        raise ValueError('SOURCE_CHANGED_AFTER_WITNESS')
    times = project_times('raw', row, available_at=witness['available_at'])
    require_known_before(times, as_of=cutoff)
    if (encoded(times) != witness['times_json'] or witness['receipt_id'] !=
            digest(['raw', row['content_hash'], witness['source_sha256']])):
        raise ValueError('INVALID_SOURCE_WITNESS')
    stats_source_url(row['source_url'])
    path = (root/'data/raw'/row['relative_path']).resolve()
    if not path.is_relative_to((root/'data/raw').resolve()):
        raise ValueError('RAW_PATH_OUTSIDE_ARCHIVE')
    if path.stat().st_size != row['size_bytes']:
        raise ValueError('RAW_HASH_OR_SIZE_MISMATCH')
    raw = path.read_bytes()
    if len(raw) != row['size_bytes'] or hashlib.sha256(raw).hexdigest() != row['content_hash']:
        raise ValueError('RAW_HASH_OR_SIZE_MISMATCH')
    return json.loads(raw), {'source_key': row['content_hash'], 'source_sha256': witness['source_sha256'],
        'receipt_id': witness['receipt_id'], 'source_url': row['source_url'], 'times': times}
