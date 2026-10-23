import hashlib
import io
import zipfile
from datetime import UTC, date, datetime, timedelta

import pytest

from goal_analysis.jobs.football_data_archive import (
    DownloadFailure,
    archive_members,
    fetch_archives,
    ingest_archives,
)
from goal_analysis.storage import Database, SnapshotStore

NOW = datetime(2026, 10, 18, tzinfo=UTC)
CSV = b'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H\nE0,01/08/2025,A,B,2,1,H,2.2\n'


def zipped(files=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        for name, content in (files or {'E0.csv': CSV}).items():
            z.writestr(name, content)
    return buffer.getvalue()


def test_download_cache_import_provenance_and_idempotence(tmp_path):
    calls = []
    raw = zipped()

    def transport(url):
        calls.append(url)
        return raw, {'ETag': 'v1'}

    first = fetch_archives(tmp_path, [2025], clock=lambda: NOW, transport=transport)
    cached = fetch_archives(tmp_path, [2025], clock=lambda: NOW + timedelta(hours=1), transport=transport)
    assert first['requests'] == 1 and cached['requests'] == 0 and len(calls) == 1
    assert cached['archives'][0]['fetched_at'] == NOW.isoformat()
    result = ingest_archives(tmp_path, cached, as_of=date(2026, 10, 18))
    repeat = ingest_archives(tmp_path, cached, as_of=date(2026, 10, 18))
    assert result['summary']['total_unique_matches'] == 1
    assert repeat['files'][0]['observations_created'] == 0
    db = Database(tmp_path / 'data/goal_analysis.sqlite3')
    store = SnapshotStore(db, tmp_path / 'data/raw')
    assert store.read(hashlib.sha256(raw).hexdigest()) == raw
    assert store.read(hashlib.sha256(CSV).hexdigest()) == CSV


@pytest.mark.parametrize('status', [403, 429, 500])
def test_rate_limit_and_errors_stop_without_retries(tmp_path, status):
    calls = []

    def transport(url):
        calls.append(url)
        raise DownloadFailure('HTTP_ERROR', status, '60')

    result = fetch_archives(tmp_path, [2023, 2024, 2025], clock=lambda: NOW, transport=transport)
    assert len(calls) == result['requests'] == 1
    assert result['archives'][0]['retry_after'] == '60'


def test_missing_season_continues_and_local_limit_caps_requests(tmp_path):
    pauses, calls = [], []

    def transport(url):
        calls.append(url)
        if '2526' in url:
            raise DownloadFailure('HTTP_ERROR', 404)
        return zipped(), {}

    result = fetch_archives(tmp_path, [2023, 2024, 2025], max_downloads=2,
                            clock=lambda: NOW, transport=transport, pause=pauses.append)
    assert len(calls) == 2 and pauses == [1.0]
    assert [x['status'] for x in result['archives']] == ['UNAVAILABLE', 'DOWNLOADED', 'LOCAL_LIMIT']


def test_archive_paths_never_extract_and_invalid_zip_rejects(tmp_path):
    assert archive_members(zipped({'../E0.csv': b'bad', 'E0.csv': CSV})) == [('E0', CSV)]
    assert not (tmp_path / 'E0.csv').exists()
    with pytest.raises(ValueError, match='INVALID_ARCHIVE'):
        archive_members(b'not a zip')
    with pytest.raises(ValueError, match='ARCHIVE_TOO_LARGE'):
        archive_members(zipped({f'{i}.txt': b'x' for i in range(101)}))


@pytest.mark.parametrize('limit', [True, 1.5, -1, 41])
def test_invalid_budget_rejects_before_network(tmp_path, limit):
    with pytest.raises(ValueError, match='INVALID_DOWNLOAD_CONFIGURATION'):
        fetch_archives(tmp_path, [2025], max_downloads=limit, clock=lambda: NOW)


def test_future_cached_timestamp_rejects(tmp_path):
    fetch_archives(tmp_path, [2025], clock=lambda: NOW, transport=lambda url: (zipped(), {}))
    with pytest.raises(ValueError, match='INVALID_CACHED_TIMESTAMP'):
        fetch_archives(tmp_path, [2025], clock=lambda: NOW - timedelta(hours=1))
