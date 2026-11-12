"""Bounded read-only access to already stored source-bound team news."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path

from goal_analysis.jobs.agent_snapshot import identifiers, json_text, require
from goal_analysis.storage.sports_news import _assertion, _digest, _stamp


def read_current_news(path, *, fixture_ids, as_of, max_bytes=16 * 1024 * 1024, seconds=15):
    """Read every eligible assertion for the supplied full-day IDs, or fail visibly.

    Missing/corrupt stores are local UNKNOWN at the executor boundary. No import,
    source retrieval, schema creation or invented publication clock occurs here.
    A content hash authenticates stored structure, not the truth of an X claim.
    """
    fixtures = identifiers(fixture_ids)
    require(isinstance(as_of, datetime) and as_of.tzinfo is not None
            and as_of.utcoffset() is not None, 'AWARE_NEWS_CUTOFF_REQUIRED')
    require(type(max_bytes) is int and 0 < max_bytes <= 16 * 1024 * 1024
            and type(seconds) in (int, float) and 0 < seconds <= 15, 'BOUNDED_NEWS_READ_REQUIRED')
    path = Path(path).resolve()
    require(path.is_file(), 'NEWS_STORE_ABSENT')
    deadline = time.monotonic() + seconds
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=min(seconds, 5))
    connection.row_factory = sqlite3.Row
    allowed_tables = {'sports_news_assertions', 'sports_news_imports', 'sports_news_import_claims'}

    def authorize(action, table, _column, _database, _trigger):
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if table in allowed_tables else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK if action in {
            sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE,
        } else sqlite3.SQLITE_DENY

    rows, size = [], 0
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        connection.set_authorizer(authorize)
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        cutoff = _stamp(as_of, 'as_of')
        for fixture_id in fixtures:
            require(time.monotonic() < deadline, 'NEWS_READ_TIME_EXCEEDED')
            # Same latest eligible provenance rule as NewsStore.claims, streamed
            # instead of fetchall. The total retained-byte cap is not a claim
            # about SQLite's memory use while decoding one corrupt oversized row.
            cursor = connection.execute('''WITH ranked AS (
                SELECT a.*, i.request_id, i.model, i.observed_at, i.import_id,
                    i.license_status, r.origin_claim_id, r.source_fetched_at,
                    ROW_NUMBER() OVER(PARTITION BY a.assertion_id
                        ORDER BY i.observed_at DESC, i.import_id DESC, r.origin_claim_id) AS rank
                FROM sports_news_assertions a
                JOIN sports_news_import_claims r USING(assertion_id)
                JOIN sports_news_imports i USING(import_id)
                WHERE a.fixture_id = ? AND i.observed_at <= ?
                    AND r.source_fetched_at <= ? AND a.published_at <= ?
            ) SELECT * FROM ranked WHERE rank = 1 ORDER BY published_at, assertion_id''',
                (fixture_id, cutoff, cutoff, cutoff))
            for value in cursor:
                require(time.monotonic() < deadline, 'NEWS_READ_TIME_EXCEEDED')
                row = {key: value[key] for key in value.keys() if key != 'rank'}  # noqa: SIM118 - sqlite3.Row iterates values.
                require(all(row[field] == _stamp(row[field], field) for field in
                    ('published_at', 'source_fetched_at', 'observed_at')),
                    'NEWS_NONCANONICAL_KNOWLEDGE_CLOCK')
                # Reuse the existing assertion validator and the original clocks.
                assertion, _, _ = _assertion({**row, 'claim_id': row['origin_claim_id'],
                    'fetched_at': row['source_fetched_at']}, row['observed_at'])
                require(all(row.get(key) == value for key, value in assertion.items())
                        and _digest(assertion) == row['content_hash'], 'NEWS_ASSERTION_INTEGRITY_FAILED')
                require(datetime.fromisoformat(row['observed_at']) <= as_of,
                        'NEWS_OBSERVED_AFTER_CUTOFF')
                size += len(json_text(row).encode('utf-8'))
                require(size <= max_bytes, 'NEWS_READ_BYTES_EXCEEDED_NO_TRUNCATION')
                rows.append(dict(row))
        require(time.monotonic() < deadline, 'NEWS_READ_TIME_EXCEEDED')
        return rows
    finally:
        connection.set_authorizer(None)
        connection.rollback()
        connection.close()
