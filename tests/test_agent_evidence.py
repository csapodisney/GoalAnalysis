"""Only owned TESTONLY SQLite files; stored news never becomes verified truth."""
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs.agent_evidence import read_current_news
from goal_analysis.storage import Database
from goal_analysis.storage.sports_news import NewsStore

PUBLISHED = datetime(2026, 11, 12, 11, tzinfo=UTC)
OBSERVED = PUBLISHED + timedelta(minutes=6)


def _store(tmp_path):
    path = tmp_path / 'TESTONLY-news.sqlite3'
    store = NewsStore(Database(path))
    store.initialize()
    post_id = (int(PUBLISHED.timestamp() * 1000) - 1288834974657) << 22
    url = f'https://x.com/ClubNews/status/{post_id}'
    claims = [{'claim_id': f'claim-{index}', 'fixture_id': fixture_id, 'kind': 'INJURY',
        'claim': f'TESTONLY reported injury {index}', 'source_url': url, 'author': 'ClubNews',
        'published_at': PUBLISHED.isoformat(),
        'fetched_at': (PUBLISHED + timedelta(minutes=5)).isoformat(),
        'content_status': 'UNVERIFIED', 'provenance_status': 'CITATION_AND_TIMESTAMP_CHECKED'}
        for index, fixture_id in enumerate(['TESTONLY:1', 'TESTONLY:2', 'TESTONLY:outside'])]
    store.ingest_grok_result({'status': 'COMPLETE', 'model': 'TESTONLY',
        'citations': [url], 'claims': claims}, request_id='TESTONLY-request',
        observed_at=OBSERVED, model='TESTONLY')
    with sqlite3.connect(path) as connection:
        connection.execute('PRAGMA journal_mode=DELETE')
    return path, store


def test_full_target_news_uses_original_clocks_and_preserves_database(tmp_path):
    path, store = _store(tmp_path)
    expected = [row for row in store.claims(as_of=OBSERVED) if row['fixture_id'] != 'TESTONLY:outside']
    before = path.read_bytes()
    rows = read_current_news(path, fixture_ids=['TESTONLY:1', 'TESTONLY:2', 'TESTONLY:missing'], as_of=OBSERVED)
    assert rows == expected
    assert len(rows) == 2 and all(row['content_status'] == 'UNVERIFIED' for row in rows)
    assert all(row['representation'] == 'MODEL_GENERATED_SUMMARY' for row in rows)
    assert path.read_bytes() == before
    assert read_current_news(path, fixture_ids=['TESTONLY:1'], as_of=PUBLISHED) == []
    assert path.read_bytes() == before


def test_news_absent_store_has_no_creation_or_schema_side_effect(tmp_path):
    path = tmp_path / 'TESTONLY-absent.sqlite3'
    with pytest.raises(ValueError, match='NEWS_STORE_ABSENT'):
        read_current_news(path, fixture_ids=['TESTONLY:1'], as_of=OBSERVED)
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_news_corrupt_assertion_is_local_error_without_repair(tmp_path):
    path, _ = _store(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute('DROP TRIGGER sports_news_assertions_no_update')
        connection.execute("UPDATE sports_news_assertions SET claim='TESTONLY corrupt' WHERE fixture_id='TESTONLY:1'")
    before = path.read_bytes()
    with pytest.raises(ValueError, match='NEWS_ASSERTION_INTEGRITY_FAILED'):
        read_current_news(path, fixture_ids=['TESTONLY:1'], as_of=OBSERVED)
    assert path.read_bytes() == before


def test_news_bound_is_visible_error_not_partial_first_n(tmp_path):
    path, _ = _store(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='NEWS_READ_BYTES_EXCEEDED_NO_TRUNCATION'):
        read_current_news(path, fixture_ids=['TESTONLY:1', 'TESTONLY:2'], as_of=OBSERVED, max_bytes=10)
    assert path.read_bytes() == before
