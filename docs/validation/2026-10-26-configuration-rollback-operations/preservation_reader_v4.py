"""Complete physical SQLite main/WAL and raw-file preservation, bounded reads.

The real inventory has18,148,065 rows in26 databases. Hashing every byte of
each main file AND its complete WAL proves preservation without an expensive
Python row-materialization pass. Schema and COUNTs are also read transactionally.
SHM/lock coordination bytes carry no committed rows. Physical checkpoint changes
are explicitly unresolved here, never silently accepted or called data loss.
"""
from contextlib import closing
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time

VERSION = 'configuration-preservation-whole-sqlite-main-wal-v4'


class Bounds:
    def __init__(self, seconds=1800, files=20000, bytes_=32 * 1024**3,
                 rows=25_000_000, tables=2000):
        self.deadline = time.monotonic() + seconds
        self.seconds, self.files, self.bytes, self.rows, self.tables = seconds, files, bytes_, rows, tables

    def check(self):
        if time.monotonic() > self.deadline:
            raise RuntimeError('PRESERVATION_READER_TIME_BOUND')


def file_identity(path, bounds):
    bounds.check()
    before = path.stat();digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024**2):
            digest.update(chunk);bounds.check()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError('PHYSICAL_FILE_CHANGED_DURING_READ:' + str(path))
    return {'bytes': after.st_size, 'sha256': digest.hexdigest()}


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def database_identity(path, bounds):
    bounds.check()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic() > bounds.deadline), 10000)
        db.execute('BEGIN')
        try:
            schema = [list(row) for row in db.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name,tbl_name,sql')]
            names = [r[1] for r in schema if r[0] == 'table']
            if len(names) > bounds.tables:
                raise RuntimeError('PRESERVATION_READER_TABLE_BOUND')
            tables = {}
            for name in names:
                bounds.check()
                count = db.execute('SELECT COUNT(*) FROM ' + quote(name)).fetchone()[0]
                bounds.rows -= count
                if bounds.rows < 0:
                    raise RuntimeError('PRESERVATION_READER_INVENTORY_ROW_BOUND')
                tables[name] = count
            # Hash while this real read transaction is still open.
            main = file_identity(path, bounds)
            wal_path = Path(str(path) + '-wal')
            wal = file_identity(wal_path, bounds) if wal_path.exists() else {
                'bytes': 0, 'sha256': hashlib.sha256(b'').hexdigest()}
            return {'schema': schema, 'table_rows': tables, 'main_file': main,
                    'wal_file_complete_bytes': wal,
                    'user_version': db.execute('PRAGMA user_version').fetchone()[0],
                    'application_id': db.execute('PRAGMA application_id').fetchone()[0],
                    'encoding': db.execute('PRAGMA encoding').fetchone()[0]}
        finally:
            db.rollback()


def snapshot(root, *, bounds=None):
    root = Path(root).resolve();bounds = bounds or Bounds()
    paths, size = [], 0
    for folder in ('data', 'reports'):
        for path in sorted((root / folder).rglob('*')):
            bounds.check()
            if path.is_symlink() or path.resolve() != path.absolute():
                raise ValueError('PRESERVATION_READER_REDIRECTED_PATH')
            if path.is_file():
                paths.append(path);size += path.stat().st_size
                if len(paths) > bounds.files or size > bounds.bytes:
                    raise RuntimeError('PRESERVATION_READER_INVENTORY_BOUND')
    lock = root / 'data/arthur/run.lock'
    databases = []
    for path in paths:
        bounds.check()
        if path == lock:
            continue
        with path.open('rb') as stream:
            if stream.read(16) == b'SQLite format 3\0':
                databases.append(path)
    owned_sidecars = {Path(str(p) + s) for p in databases for s in ('-wal', '-shm', '-journal')}
    raw, sqlite, exclusions = {}, {}, {}
    for path in paths:
        bounds.check();name = path.relative_to(root).as_posix()
        if path == lock:
            exclusions[name] = 'application coordination lock, no committed business data'
        elif path in databases:
            sqlite[name] = database_identity(path, bounds)
        elif path in owned_sidecars:
            if path.name.endswith('-wal'):
                exclusions[name] = 'complete WAL bytes hashed in owning database identity'
            elif path.name.endswith('-shm'):
                exclusions[name] = 'SQLite shared-memory reader coordination, no committed rows'
            else:
                # A rollback journal is not discarded as presumed harmless.
                raw[name] = file_identity(path, bounds)
        else:
            raw[name] = file_identity(path, bounds)
    return {'version': VERSION, 'captured_at': datetime.now(UTC).isoformat(),
            'root': str(root), 'inventory_files': len(paths), 'inventory_bytes': size,
            'raw_files': raw, 'sqlite_databases': sqlite, 'runtime_byte_exclusions': exclusions,
            'method': 'EXACT_ALL_RAW_MAIN_AND_COMPLETE_WAL_BYTES_PLUS_TRANSACTIONAL_SCHEMA_COUNTS',
            'no_per_row_hash_or_sampling_claim': True,
            'limits': {'seconds': bounds.seconds, 'files': bounds.files, 'bytes': bounds.bytes,
                       'remaining_count_rows': bounds.rows, 'tables_per_database': bounds.tables}}


def compare(before, after):
    if before['version'] != VERSION or after['version'] != VERSION or before['root'] != after['root']:
        raise ValueError('PRESERVATION_READER_VERSION_OR_ROOT')
    if before['raw_files'] != after['raw_files']:
        raise AssertionError('PRESERVATION_RAW_FILES_DIFFER')
    if before['sqlite_databases'] != after['sqlite_databases']:
        raise AssertionError('PHYSICAL_SQLITE_MAIN_OR_WAL_OR_SCHEMA_COUNTS_CHANGED_REQUIRES_SEMANTIC_REVIEW')
    return {'raw_files_exact': len(before['raw_files']),
            'committed_sqlite_databases_exact': len(before['sqlite_databases']),
            'committed_sqlite_rows_exact': sum(sum(d['table_rows'].values())
                                             for d in before['sqlite_databases'].values()),
            'sqlite_main_and_complete_wal_bytes_exact': True,
            'rows_not_sampled_or_skipped': True,
            'per_row_hashes_not_computed_or_claimed': True,
            'sqlite_shm_and_lock_coordination_bytes_not_claimed_immutable': True}
