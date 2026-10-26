"""Exact one-page SHADOW witnesses; never a retained-row backfill or cursor scan.

Only a newly fetched normal page under the caller's existing RootRunLock may
enter this boundary. Original source and ingestion clocks remain immutable.
"""
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from goal_analysis.normalization.canonical import canonical_id, utc
from goal_analysis.normalization.historical_results import normalize_archive_result
from goal_analysis.normalization.leakage import require_known_before
from goal_analysis.normalization.live_source import stats_source_url
from goal_analysis.normalization.temporal import project_times
from goal_analysis.storage import Database
from goal_analysis.storage.canonical_registry import digest, encoded
from goal_analysis.storage.historical_results import identity, validate
from goal_analysis.storage.temporal import TemporalStore, _source_hash

from .portfolio import RunLock

MAX_BYTES = 2_000_000
MAX_ROWS = 100
MAX_SECONDS = 20


def _lock(root, lock):
    if (not isinstance(lock, RunLock) or lock.stream is None or lock.stream.closed
            or lock.configuration_recovery
            or lock.path.resolve() != (root / 'data/arthur/run.lock').resolve()):
        raise ValueError('EXISTING_ROOT_RUN_LOCK_REQUIRED')


def _ro(root):
    started = time.monotonic()
    db = sqlite3.connect((root / 'data/goal_analysis.sqlite3').as_uri() + '?mode=ro',
                         uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    db.set_progress_handler(lambda: int(time.monotonic() - started > MAX_SECONDS), 1000)
    return db


class _Records:
    def __init__(self):
        self.records = []

    def ingest(self, dataset, **source):
        for row in dataset['records']:
            self.records.append(normalize_archive_result(row, row['raw'],
                observed_at=source['fetched_at'], source_url=source['source_url'],
                source_sha256=source['content_hash']))
        return {'matches_created': 0, 'observations_created': 0, 'imports_created': 0}


def _page(db, root, page, plan, cutoff):
    from . import the_stats_history as history

    history._validate_plan(plan)
    raw = db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?',
                     (page['content_hash'],)).fetchone()
    if raw is None:
        raise ValueError('COMMITTED_RAW_MISSING')
    raw = dict(raw)
    path = (root / 'data/raw' / raw['relative_path']).resolve()
    if (not path.is_relative_to((root / 'data/raw').resolve())
            or not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES):
        raise ValueError('BOUNDED_PHYSICAL_RAW_REQUIRED')
    body = path.read_bytes()
    if (raw['provider'] != 'the_stats_api' or raw['resource_type'] != 'historical_matches'
            or sha256(body).hexdigest() != page['content_hash']
            or len(body) != raw['size_bytes']):
        raise ValueError('RAW_HASH_SIZE_PROVIDER_MISMATCH')
    if page['plan_sha256'] != plan['plan_sha256'] or utc(page['observed_at']) > cutoff:
        raise ValueError('PAGE_PLAN_OR_CLOCK_MISMATCH')
    params = page['params']
    parsed = urlsplit(stats_source_url(page['source_url']))
    query = parse_qs(parsed.query)
    if (parsed.path != '/api/football/matches' or any(len(v) != 1 for v in query.values())
            or {k: v[0] for k, v in query.items()} != {k: str(v) for k, v in params.items()}
            or type(params['page']) is not int or type(params['per_page']) is not int
            or not 1 <= params['page'] <= 100_000 or params['per_page'] != MAX_ROWS):
        raise ValueError('EXACT_PAGE_URL_PARAMS_REQUIRED')
    scope = {k: v for k, v in params.items() if k not in ('page', 'per_page')}
    if scope not in plan['queries']:
        raise ValueError('PAGE_NOT_IN_FROZEN_PLAN')
    rows, _ = history._page(json.loads(body), params['page'], MAX_ROWS)
    if not rows:
        return {'status': 'NO_ADMISSION_EMPTY_OR_NONEXACT_PAGE',
                'diagnostics': {'accepted_rows': 0, 'rejected_rows': 0, 'rejection_counts': {}},
                'empty_page_without_admission': True, 'source_provenance_accepted': False}
    if (raw['source_url'] != page['source_url']
            or utc(raw['fetched_at']) != utc(page['observed_at'])):
        raise ValueError('RAW_SOURCE_ALIAS_URL_OR_CLOCK_MISMATCH')
    recorder = _Records()
    diagnostics = history._ingest(recorder, rows, scope, plan, page)
    records = {digest(validate(r, now=cutoff)): r for r in recorder.records}
    # An empty or quarantined page may retain its existing legacy semantics,
    # but cannot silently grant witnesses to a convenient accepted subset.
    if not rows or diagnostics['rejected_rows'] or len(records) != len(rows):
        return {'status': 'NO_ADMISSION_EMPTY_OR_NONEXACT_PAGE', 'diagnostics': diagnostics}
    return {'status': 'EXACT_PAGE_PREFLIGHT', 'raw': raw, 'page': page, 'plan': plan,
            'records': records, 'keyset_sha256': digest(sorted(records))}


def preflight_page(root, page, plan, lock, *, clock=lambda: datetime.now(UTC)):
    """Pure/RO source gate BEFORE constructing the incremental canonical Sink."""
    root = Path(root).resolve()
    _lock(root, lock)
    with closing(_ro(root)) as db:
        result = _page(db, root, page, plan, utc(clock()))
    # Freeze caller-owned dictionaries; they are not target ingestion proofs.
    return json.loads(encoded(result))


def _snapshot(db, root, candidate, cutoff):
    fresh = _page(db, root, candidate['page'], candidate['plan'], cutoff)
    if fresh != candidate:
        raise ValueError('PAGE_SOURCE_CHANGED_AFTER_PREFLIGHT')
    keys = sorted(candidate['records'])
    sources = {('raw', candidate['raw']['content_hash']): candidate['raw']}
    rows = list(db.execute('''SELECT v.rowid AS _sequence,v.* FROM historical_result_versions v
        JOIN historical_result_matches m USING(match_id) WHERE m.provider='the_stats_api'
        AND v.observed_at=? AND json_extract(v.record_json,'$.source_sha256')=? ORDER BY v.rowid LIMIT 101''',
        (utc(candidate['page']['observed_at']).isoformat(timespec='microseconds'),
         candidate['raw']['content_hash'])))
    if len(rows) != len(keys) or {r['version_id'] for r in rows} != set(keys):
        raise ValueError('EXACT_COMMITTED_PAGE_KEYSET_REQUIRED')
    for row in rows:
        row = dict(row)
        record = json.loads(row['record_json'])
        if (record != candidate['records'][row['version_id']]
                or row['identity_sha256'] != identity(record)
                or row['match_id'] != canonical_id('fixture', 'the_stats_api:' + record['provider_id'])
                or utc(row['observed_at']) != utc(record['observed_at'])
                or not utc(row['observed_at']) <= utc(row['ingested_at']) <= cutoff):
            raise ValueError('COMMITTED_RESULT_IDENTITY_OR_CLOCK_MISMATCH')
        sources['result', row['version_id']] = row
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='temporal_receipts'").fetchone()
    prior = {}
    maximum = db.execute('SELECT max(available_at) FROM temporal_receipts').fetchone()[0] if exists else None
    if maximum is not None and utc(maximum) > cutoff:
        raise ValueError('CLOCK_MOVED_BACKWARDS')
    for (namespace, key), source in sources.items():
        receipts = list(db.execute('SELECT * FROM temporal_receipts WHERE namespace=? AND source_key=?',
                                  (namespace, key))) if exists else []
        if len(receipts) > 1:
            raise ValueError('AMBIGUOUS_PRIOR_RECEIPT')
        if not receipts:
            continue
        row = dict(receipts[0])
        source_sha = _source_hash(source)
        times = project_times(namespace, source, available_at=row['available_at'])
        if (row['receipt_id'] != digest([namespace, key, source_sha]) or row['source_sha256'] != source_sha
                or row['times_json'] != encoded(times)
                or any(row[k] != times[k] for k in ('observed_at', 'published_at', 'ingested_at', 'available_at'))):
            raise ValueError('PRIOR_RECEIPT_SOURCE_OR_CLOCK_MISMATCH')
        require_known_before(times, as_of=cutoff)
        prior[namespace, key] = row
    return sources, prior


def _save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def cached_page_status(root, candidate, lock, *, clock=lambda: datetime.now(UTC)):
    """No retrospective admission, and no retry of a partial fresh-page attempt."""
    root = Path(root).resolve()
    _lock(root, lock)
    if candidate.get('status') != 'EXACT_PAGE_PREFLIGHT':
        return {'status': candidate['status'], 'new_receipts': 0}
    key = digest([candidate['page'], sorted(candidate['records'])])
    folder = root / 'data/arthur/historical-page-availability'
    if (folder / (key + '-intent.json')).exists():
        # admit_page refuses every STOP/incomplete intent before a writer exists.
        return admit_page(root, candidate, lock, clock=clock)
    return {'status': 'NO_ADMISSION_RETAINED_CACHED_PAGE', 'new_receipts': 0}


def admit_page(root, candidate, lock, *, clock=lambda: datetime.now(UTC)):
    """Exact committed page, once journal, atomic witnesses, truthful STOP.

    The caller must only supply the fresh-page preflight from its current normal
    fetch; cached pages never enter this integration. No source schema or row is
    repaired here. Failure blocks checkpoint advance and automatic re-admission.
    """
    root = Path(root).resolve()
    _lock(root, lock)
    if candidate.get('status') != 'EXACT_PAGE_PREFLIGHT':
        raise ValueError('COMPLETE_PAGE_PREFLIGHT_REQUIRED')
    started = time.monotonic()
    read_started = utc(clock())
    with closing(_ro(root)) as db:
        sources, prior = _snapshot(db, root, candidate, read_started)
    read_completed = utc(clock())
    if read_completed < read_started:
        raise ValueError('CLOCK_MOVED_BACKWARDS')
    identity_key = digest([candidate['page'], sorted(candidate['records'])])
    folder = root / 'data/arthur/historical-page-availability'
    intent = folder / (identity_key + '-intent.json')
    outcome = folder / (identity_key + '-outcome.json')
    if intent.exists():
        previous = json.loads(outcome.read_bytes()) if outcome.exists() else {}
        if previous.get('status') != 'COMPLETED' or len(prior) != len(sources):
            raise RuntimeError('PAGE_ATTEMPT_EXISTS_PRESERVE_STOPPED_NO_RETRY')
        return {'status': 'READONLY_ALREADY_COMPLETED', 'new_receipts': 0}
    if len(prior) == len(sources):
        return {'status': 'READONLY_PRIOR_RECEIPTS_REUSED', 'new_receipts': 0}
    folder.mkdir(parents=True, exist_ok=True)
    _save(intent, {'at': read_completed.isoformat(), 'identity': identity_key,
                   'exact_keys': sorted(candidate['records']), 'automatic_retry': False})
    committed, receipt_ids = False, []
    try:
        # New write-capable stores exist only after full committed source gate.
        database = Database(root / 'data/goal_analysis.sqlite3')
        TemporalStore(database, root / 'data/raw', clock=clock).initialize()
        with closing(sqlite3.connect(database.path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            db.set_progress_handler(lambda: int(time.monotonic() - started > MAX_SECONDS), 1000)
            try:
                recheck_at = utc(clock())
                if recheck_at < read_completed:
                    raise ValueError('CLOCK_MOVED_BACKWARDS')
                current_sources, current_prior = _snapshot(db, root, candidate, recheck_at)
                if current_sources != sources or current_prior != prior:
                    raise ValueError('COMMITTED_PAGE_OR_RECEIPTS_CHANGED_CAS')
                available = utc(clock())
                if available < recheck_at or time.monotonic() - started > MAX_SECONDS:
                    raise ValueError('CLOCK_OR_FINITE_PAGE_BOUND')
                for (namespace, key), source in sources.items():
                    if (namespace, key) in prior:
                        continue
                    source_sha = _source_hash(source)
                    times = project_times(namespace, source, available_at=available)
                    receipt_id = digest([namespace, key, source_sha])
                    db.execute('INSERT INTO temporal_receipts VALUES (?,?,?,?,?,?,?,?,?)',
                        (receipt_id, namespace, key, source_sha, times['observed_at'], times['published_at'],
                         times['ingested_at'], times['available_at'], encoded(times)))
                    receipt_ids.append(receipt_id)
                db.commit()
                committed = True
            except BaseException:
                if not committed:
                    db.rollback()
                raise
        _save(outcome, {'status': 'COMPLETED', 'committed': True, 'receipt_ids': receipt_ids,
                        'new_receipts': len(receipt_ids), 'availability_basis': 'SOURCE_PRESENCE_UPPER_BOUND'})
        return {'status': 'COMPLETED', 'new_receipts': len(receipt_ids)}
    except BaseException as error:
        if not outcome.exists():
            _save(outcome, {'status': 'STOPPED', 'committed': committed,
                'durable_new_receipts': len(receipt_ids) if committed else 0,
                'error': type(error).__name__, 'automatic_retry': False})
        raise
