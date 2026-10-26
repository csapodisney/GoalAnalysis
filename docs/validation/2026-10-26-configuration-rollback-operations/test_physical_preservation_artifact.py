"""Whole-byte main/WAL identity is stronger than row-count equality alone."""
from pathlib import Path
import sqlite3
import sys

import pytest

HERE = Path(__file__).resolve().parent;sys.path.insert(0, str(HERE))
import preservation_reader_v4 as reader


def example(root):
    (root/'data/arthur').mkdir(parents=True);(root/'reports').mkdir()
    (root/'reports/old.json').write_bytes(b'{"original":true}')
    (root/'data/arthur/run.lock').write_bytes(b'initial-lock')
    (root/'data/orphan-wal').write_bytes(b'protected-orphan')
    db = sqlite3.connect(root/'data/live.sqlite3');db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE actual (id INTEGER PRIMARY KEY,value BLOB)')
    db.executemany('INSERT INTO actual VALUES (?,?)', [(1,b'one'),(2,b'two')]);db.commit()
    return db


def test_unchanged_main_and_complete_committed_wal_exact(tmp_path):
    with example(tmp_path):
        before=reader.snapshot(tmp_path)
        assert before['sqlite_databases']['data/live.sqlite3']['wal_file_complete_bytes']['bytes']>0
        (tmp_path/'data/arthur/run.lock').write_bytes(b'changed-runtime-only')
        proof=reader.compare(before,reader.snapshot(tmp_path))
        assert proof['committed_sqlite_rows_exact']==2
        assert proof['sqlite_main_and_complete_wal_bytes_exact']
        assert 'data/orphan-wal' in before['raw_files']


@pytest.mark.parametrize('change',['same_count_value','delete','add','schema','report','orphan'])
def test_any_committed_or_raw_mutation_rejected(tmp_path,change):
    with example(tmp_path) as db:
        before=reader.snapshot(tmp_path)
        if change=='same_count_value':db.execute("UPDATE actual SET value=x'33' WHERE id=1")
        elif change=='delete':db.execute('DELETE FROM actual WHERE id=1')
        elif change=='add':db.execute("INSERT INTO actual VALUES (3,x'33')")
        elif change=='schema':db.execute('CREATE INDEX value_index ON actual(value)')
        else:(tmp_path/('reports/old.json' if change=='report' else 'data/orphan-wal')).write_bytes(b'changed')
        db.commit()
        with pytest.raises(AssertionError):reader.compare(before,reader.snapshot(tmp_path))


def test_checkpoint_is_unresolved_physical_change_never_silently_accepted_or_called_data_loss(tmp_path):
    with example(tmp_path) as db:
        before=reader.snapshot(tmp_path);db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        with pytest.raises(AssertionError,match='REQUIRES_SEMANTIC_REVIEW'):
            reader.compare(before,reader.snapshot(tmp_path))


@pytest.mark.parametrize('limit',['files','bytes_','rows','seconds'])
def test_explicit_finite_bounds(tmp_path,limit):
    with example(tmp_path),pytest.raises(RuntimeError):
        reader.snapshot(tmp_path,bounds=reader.Bounds(**{limit:0}))


def test_all_existing_row_values_same_count_still_checked_by_bytes(tmp_path):
    with example(tmp_path) as db:
        before=reader.snapshot(tmp_path);db.execute("UPDATE actual SET value=x'00' WHERE id=2");db.commit()
        after=reader.snapshot(tmp_path)
        assert before['sqlite_databases']['data/live.sqlite3']['table_rows']==after['sqlite_databases']['data/live.sqlite3']['table_rows']
        assert before['sqlite_databases']['data/live.sqlite3']['wal_file_complete_bytes']!=after['sqlite_databases']['data/live.sqlite3']['wal_file_complete_bytes']
        with pytest.raises(AssertionError):reader.compare(before,after)
