"""Append-only actual HTTP observations; cache reads never create an observation.

A content hash identifies bytes, not their URL or acquisition time. Preserve the
first raw row and record each actual authenticated response separately. Witness
the observation only AFTER a committed read. Neither time is a bookmaker quote.
"""
import hashlib
import json
import re
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.leakage import require_known_before
from goal_analysis.normalization.live_source import stats_source_url
from goal_analysis.normalization.temporal import project_times

from .canonical_registry import digest, encoded
from .temporal import TemporalStore, _source_hash

VERSION = 'the-stats-http-observation-v1'
MAX_BYTES = 2_000_000


def _stamp(value):
    return utc(value).isoformat(timespec='microseconds')


def _raw(db, root, content_hash):
    row = db.execute("SELECT * FROM raw_snapshots WHERE content_hash=? AND provider='the_stats_api'",
                     (content_hash,)).fetchone()
    if row is None:
        raise ValueError('MISSING_HTTP_RAW_SOURCE')
    row = dict(row)
    stats_source_url(row['source_url'])
    path = (Path(root)/'data/raw'/row['relative_path']).resolve()
    if not path.is_relative_to((Path(root)/'data/raw').resolve()):
        raise ValueError('HTTP_RAW_PATH_OUTSIDE_ARCHIVE')
    if not 0 < row['size_bytes'] <= MAX_BYTES or path.stat().st_size != row['size_bytes']:
        raise ValueError('HTTP_RAW_SIZE_MISMATCH')
    raw = path.read_bytes()
    if len(raw) != row['size_bytes'] or hashlib.sha256(raw).hexdigest() != content_hash:
        raise ValueError('HTTP_RAW_HASH_MISMATCH')
    return row, raw


def read_http_observation(db, root, observation_id, cutoff, *, source=None):
    """Read only an explicit observation ID, never choose a convenient new time."""
    cutoff = utc(cutoff)
    if not isinstance(observation_id, str) or not re.fullmatch('[0-9a-f]{32}', observation_id):
        raise ValueError('INVALID_HTTP_OBSERVATION_ID')
    row = db.execute('SELECT * FROM the_stats_http_observations WHERE response_id=?',
                     (observation_id,)).fetchone()
    witness = db.execute('SELECT * FROM the_stats_http_witnesses WHERE response_id=?',
                         (observation_id,)).fetchone()
    if row is None or witness is None:
        raise ValueError('UNWITNESSED_HTTP_OBSERVATION')
    row = dict(row)
    raw_row, raw = _raw(db, root, row['content_hash'])
    if _source_hash(raw_row) != row['raw_metadata_sha256']:
        raise ValueError('HTTP_RAW_METADATA_CHANGED')
    if not 200 <= row['http_status'] < 300 or row['authenticated'] != 1:
        raise ValueError('UNSUCCESSFUL_HTTP_OBSERVATION')
    stats_source_url(row['source_url'])
    row_hash = digest(row)
    times = json.loads(witness['times_json'])
    expected = {'event_time': None, 'event_time_kind': 'UNKNOWN', 'published_at': None,
        'observed_at': row['observed_at'], 'ingested_at': row['ingested_at'],
        'available_at': witness['available_at'], 'availability_basis': 'SOURCE_PRESENCE_UPPER_BOUND'}
    if (times != expected or witness['observation_sha256'] != row_hash
            or witness['receipt_id'] != digest([VERSION, observation_id, row_hash])
            or utc(raw_row['fetched_at']) > utc(row['observed_at'])):
        raise ValueError('INVALID_HTTP_OBSERVATION_WITNESS')
    require_known_before(times, as_of=cutoff)
    if source is not None and (source.get('provider') != 'the_stats_api'
            or source.get('content_hash') != row['content_hash']
            or source.get('source_url') != row['source_url']
            or utc(source['observed_at']) != utc(row['observed_at'])
            or source.get('http_observation_id') != observation_id
            or source.get('http_receipt_id') != witness['receipt_id']):
        raise ValueError('HTTP_SOURCE_ALIAS_DIFFERS_FROM_OBSERVATION')
    return json.loads(raw), {'source_key': row['content_hash'], 'source_url': row['source_url'],
        'source_sha256': row['raw_metadata_sha256'], 'observation_id': observation_id,
        'observation_sha256': row_hash, 'receipt_id': witness['receipt_id'], 'times': times,
        'kind': 'ACTUAL_AUTHENTICATED_HTTP_RESPONSE_NOT_QUOTE_TIME'}


class HttpObservations:
    def __init__(self, database, root, *, clock=None):
        self.database, self.root = database, Path(root).resolve()
        self.clock = clock or (lambda: datetime.now(UTC))
        self._initialized = False

    def initialize(self):
        if self._initialized:
            return
        TemporalStore(self.database, self.root/'data/raw').initialize()
        with self.database.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS the_stats_http_observations (
                response_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
                source_url TEXT NOT NULL, observed_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
                raw_metadata_sha256 TEXT NOT NULL, http_status INTEGER NOT NULL,
                authenticated INTEGER NOT NULL)''')
            db.execute('''CREATE INDEX IF NOT EXISTS http_observation_source_time
                ON the_stats_http_observations(content_hash,source_url,observed_at)''')
            db.execute('''CREATE TABLE IF NOT EXISTS the_stats_http_witnesses (
                response_id TEXT PRIMARY KEY REFERENCES the_stats_http_observations(response_id),
                receipt_id TEXT NOT NULL UNIQUE, observation_sha256 TEXT NOT NULL,
                available_at TEXT NOT NULL, times_json TEXT NOT NULL)''')
            db.execute('''CREATE INDEX IF NOT EXISTS http_witness_asof
                ON the_stats_http_witnesses(available_at,response_id)''')
            for table in ('the_stats_http_observations', 'the_stats_http_witnesses'):
                for operation in ('UPDATE', 'DELETE'):
                    db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                        BEFORE {operation} ON {table} BEGIN
                        SELECT RAISE(ABORT,'HTTP observations are append-only'); END''')
        self._initialized = True

    def admit(self, receipt):
        """Called only after an actual successful client request, never on cache.

        Client-issued response ID makes retries idempotent; the same bytes from
        two real responses retain two IDs. No caller-supplied ingestion clock.
        A failure after the first commit leaves an UNWITNESSED record, not proof.
        """
        if (receipt.get('provider') != 'the_stats_api' or receipt.get('authenticated') is not True
                or type(receipt.get('http_status')) is not int or not 200 <= receipt['http_status'] < 300
                or not re.fullmatch('[0-9a-f]{32}', str(receipt.get('response_id')))
                or not re.fullmatch('[0-9a-f]{64}', str(receipt.get('payload_sha256')))):
            raise ValueError('SUCCESSFUL_CLIENT_HTTP_RECEIPT_REQUIRED')
        url, observed, ingested = stats_source_url(receipt['source_url']), _stamp(receipt['observed_at']), _stamp(self.clock())
        if observed > ingested:
            raise ValueError('HTTP_RECEIPT_FROM_FUTURE')
        self.initialize()
        with self.database.transaction(immediate=True) as db:
            raw_row, _ = _raw(db, self.root, receipt['payload_sha256'])
            if utc(raw_row['fetched_at']) > utc(observed):
                raise ValueError('RAW_SOURCE_FROM_FUTURE')
            identity = {'response_id': receipt['response_id'], 'content_hash': receipt['payload_sha256'],
                'source_url': url, 'observed_at': observed, 'raw_metadata_sha256': _source_hash(raw_row),
                'http_status': receipt['http_status'], 'authenticated': 1}
            old = db.execute('SELECT * FROM the_stats_http_observations WHERE response_id=?',
                             (receipt['response_id'],)).fetchone()
            if old is not None:
                if {k: old[k] for k in identity} != identity:
                    raise ValueError('CONFLICTING_HTTP_RESPONSE_ID')
            else:
                db.execute('INSERT INTO the_stats_http_observations VALUES (?,?,?,?,?,?,?,?)',
                    tuple({**identity, 'ingested_at': ingested}[k] for k in
                          ('response_id','content_hash','source_url','observed_at','ingested_at',
                           'raw_metadata_sha256','http_status','authenticated')))
        # This is a NEW committed read, then an actual wall-clock witness.
        with closing(self.database.connect()) as db:
            row = dict(db.execute('SELECT * FROM the_stats_http_observations WHERE response_id=?',
                                  (receipt['response_id'],)).fetchone())
            raw_row, _ = _raw(db, self.root, row['content_hash'])
            if _source_hash(raw_row) != row['raw_metadata_sha256']:
                raise ValueError('HTTP_RAW_METADATA_CHANGED')
        available = _stamp(self.clock())
        times = {'event_time': None, 'event_time_kind': 'UNKNOWN', 'published_at': None,
            'observed_at': row['observed_at'], 'ingested_at': row['ingested_at'], 'available_at': available,
            'availability_basis': 'SOURCE_PRESENCE_UPPER_BOUND'}
        require_known_before(times, as_of=available)
        raw_times = project_times('raw', raw_row, available_at=available)
        raw_hash, row_hash = _source_hash(raw_row), digest(row)
        receipt_id = digest([VERSION, row['response_id'], row_hash])
        with self.database.transaction(immediate=True) as db:
            prior = db.execute("SELECT source_sha256 FROM temporal_receipts WHERE namespace='raw' AND source_key=?",
                               (row['content_hash'],)).fetchone()
            if prior is not None and prior[0] != raw_hash:
                raise ValueError('RAW_SOURCE_CHANGED_AFTER_WITNESS')
            db.execute('INSERT OR IGNORE INTO temporal_receipts VALUES (?,?,?,?,?,?,?,?,?)',
                (digest(['raw', row['content_hash'], raw_hash]), 'raw', row['content_hash'], raw_hash,
                 raw_times['observed_at'], raw_times['published_at'], raw_times['ingested_at'],
                 available, encoded(raw_times)))
            db.execute('INSERT OR IGNORE INTO the_stats_http_witnesses VALUES (?,?,?,?,?)',
                (row['response_id'], receipt_id, row_hash, available, encoded(times)))
        with closing(self.database.connect()) as db:
            _, proof = read_http_observation(db, self.root, row['response_id'], self.clock())
        return {'http_observation_id': row['response_id'], 'http_receipt_id': proof['receipt_id'],
                'http_observation_status': 'VERIFIED'}
