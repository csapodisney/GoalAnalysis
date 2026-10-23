"""Actual local raw bytes require a source-bound receipt at the decision cutoff."""
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.storage.db import Database
from goal_analysis.storage.raw_evidence import read_witnessed_raw
from goal_analysis.storage.snapshots import SnapshotStore
from goal_analysis.storage.temporal import TemporalStore

NOW = datetime(2026, 10, 21, 10, tzinfo=UTC)


@pytest.fixture
def retained(tmp_path):
    database = Database(tmp_path / 'data/sports.sqlite3')
    database.initialize()
    store = SnapshotStore(database, tmp_path / 'data/raw')
    saved = store.save('the_stats_api', 'fixture', b'{"data":{"id":"mt_1"}}',
        'application/json', fetched_at=NOW - timedelta(hours=1),
        source_url='https://api.thestatsapi.com/api/football/matches/mt_1')
    temporal = TemporalStore(database, tmp_path / 'data/raw', clock=lambda: NOW - timedelta(minutes=1))
    temporal.initialize()
    with closing(database.connect()) as db:
        row = dict(db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?',
                              (saved.content_hash,)).fetchone())
    return tmp_path, database, temporal, row


def read(retained, *, row=None, cutoff=NOW):
    root, database, _, original = retained
    with closing(database.connect()) as db:
        db.execute('PRAGMA query_only=ON')
        return read_witnessed_raw(db, row or original, root, cutoff)


def test_exact_bytes_receipt_and_cutoff_roundtrip_is_read_only(retained):
    root, database, temporal, row = retained
    assert temporal.capture('raw')['created'] == 1
    with closing(database.connect()) as db:
        before = list(db.iterdump())
    payload, proof = read(retained)
    assert payload == {'data': {'id': 'mt_1'}}
    assert proof['source_key'] == row['content_hash']
    assert proof['source_url'] == row['source_url']
    assert proof['receipt_id'] and proof['source_sha256']
    assert read(retained) == (payload, proof)
    with closing(database.connect()) as db:
        assert list(db.iterdump()) == before
    assert (root / 'data/raw' / row['relative_path']).is_file()


def test_missing_receipt_is_not_created_by_reader(retained):
    with pytest.raises(ValueError, match='UNWITNESSED_SOURCE'):
        read(retained)
    with closing(retained[1].connect()) as db:
        assert db.execute('SELECT count(*) FROM temporal_receipts').fetchone()[0] == 0


def test_real_receipt_after_cutoff_cannot_be_backdated(retained):
    retained[2].capture('raw')
    with pytest.raises(ValueError):
        read(retained, cutoff=NOW - timedelta(minutes=1, microseconds=1))
    assert read(retained, cutoff=NOW - timedelta(minutes=1))[0]['data']['id'] == 'mt_1'


@pytest.mark.parametrize('change', [
    {'relative_path': '../outside.json'}, {'source_url': 'https://example.test/forged'},
    {'size_bytes': 1},
])
def test_metadata_changed_after_witness_is_rejected_before_path_or_url_use(retained, change):
    retained[2].capture('raw')
    with pytest.raises(ValueError, match='SOURCE_CHANGED_AFTER_WITNESS'):
        read(retained, row={**retained[3], **change})


def test_same_size_payload_tampering_is_detected(retained):
    retained[2].capture('raw')
    path = retained[0] / 'data/raw' / retained[3]['relative_path']
    path.write_bytes(path.read_bytes().replace(b'mt_1', b'mt_2'))
    with pytest.raises(ValueError, match='RAW_HASH_OR_SIZE_MISMATCH'):
        read(retained)


def test_missing_physical_payload_is_not_reconstructed(retained):
    retained[2].capture('raw')
    path = retained[0] / 'data/raw' / retained[3]['relative_path']
    path.unlink()
    with pytest.raises(FileNotFoundError):
        read(retained)
