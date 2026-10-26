"""TESTONLY exact-page normal producer, no live transport or production data."""
import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import timedelta

import pytest
from test_historical_incremental_sink import NOW, Transport, checkpoint, match, page, plan, run

from goal_analysis.jobs import historical_page_availability as admission
from goal_analysis.jobs import the_stats_history as history
from goal_analysis.jobs.historical_result_sink import HistoricalResultSink
from goal_analysis.jobs.portfolio import RunLock
from goal_analysis.storage import Database
from goal_analysis.storage.temporal import TemporalStore


def ro(root):
    return admission._ro(root)


def receipts(root):
    with closing(ro(root)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='temporal_receipts'").fetchone():
            return []
        return [dict(r) for r in db.execute('SELECT * FROM temporal_receipts')]


def candidate(root, lock):
    work = root / 'data/arthur/the-stats-history' / plan()['plan_sha256']
    source = history._read(work / 'page-00000-000001.json')
    return admission.preflight_page(root, source, plan(), lock, clock=lambda: NOW)


def seeded(root, rows=None):
    run(root, plan(), Transport(page(rows)), unified_results=True)


def test_actual_fresh_normal_sink_exact_witnesses_before_checkpoint(tmp_path):
    with RunLock(tmp_path / 'data/arthur/run.lock') as lock:
        report = run(tmp_path, plan(), Transport(page([match(1), match(2)])),
                     unified_results=True, root_lock=lock)
    assert report['pages'][0]['availability'] == {'status': 'COMPLETED', 'new_receipts': 3}
    assert history._read(checkpoint(tmp_path, plan()))['query_index'] == 1
    rows = receipts(tmp_path)
    assert len(rows) == 3
    assert {r['namespace'] for r in rows} == {'raw', 'result'}
    for row in rows:
        times = json.loads(row['times_json'])
        assert times['availability_basis'] == 'SOURCE_PRESENCE_UPPER_BOUND'
        assert row['published_at'] is None


def test_default_unified_mode_without_existing_root_lock_has_no_receipt_side_effect(tmp_path):
    seeded(tmp_path)
    assert receipts(tmp_path) == []


def test_preexisting_cached_page_is_not_retrospectively_admitted(tmp_path, monkeypatch):
    original = history._atomic
    def stop(path, value):
        if path.name == 'checkpoint.json':
            raise OSError('TESTONLY stop before cursor')
        return original(path, value)
    monkeypatch.setattr(history, '_atomic', stop)
    with pytest.raises(OSError):
        seeded(tmp_path)
    monkeypatch.setattr(history, '_atomic', original)
    with RunLock(tmp_path / 'data/arthur/run.lock') as lock:
        result = run(tmp_path, plan(), Transport(), unified_results=True, root_lock=lock, max_requests=0)
    assert result['requests'] == 0 and result['cached_pages'] == 1
    assert result['pages'][0]['availability']['status'] == 'NO_ADMISSION_RETAINED_CACHED_PAGE'
    assert receipts(tmp_path) == []


@pytest.mark.parametrize('rows', ([], [match(1), match(2, status='live')],
                                  [match(1), match(1, score={'regulation': {'home': 0, 'away': 0}})],
                                  [match(1), match(1)]))
def test_empty_partial_conflicting_and_duplicate_page_never_samples_witnesses(tmp_path, rows):
    payload = page(rows)
    if not rows:
        payload['meta']['total_pages'] = 0
    with RunLock(tmp_path / 'data/arthur/run.lock') as lock:
        report = run(tmp_path, plan(), Transport(payload), unified_results=True, root_lock=lock)
    assert report['pages'][0]['availability']['status'] == 'NO_ADMISSION_EMPTY_OR_NONEXACT_PAGE'
    assert receipts(tmp_path) == []


def test_unknown_regulation_score_is_not_dropped_or_fabricated(tmp_path):
    with RunLock(tmp_path / 'data/arthur/run.lock') as lock:
        report = run(tmp_path, plan(), Transport(page([match(1, score={'home': 9, 'away': 8})])),
                     unified_results=True, root_lock=lock)
    assert report['pages'][0]['availability']['new_receipts'] == 2
    with closing(ro(tmp_path)) as db:
        record = json.loads(db.execute('SELECT record_json FROM historical_result_versions').fetchone()[0])
        assert record['home_goals'] is record['away_goals'] is None
        assert record['half_home_goals'] is record['half_away_goals'] is None


@pytest.mark.parametrize('damage', ('url', 'clock', 'hash', 'plan', 'page'))
def test_invalid_source_rejected_before_any_new_writer_constructor(tmp_path, monkeypatch, damage):
    seeded(tmp_path)
    with RunLock(tmp_path / 'data/arthur/run.lock') as lock:
        value = candidate(tmp_path, lock)
        source = deepcopy(value['page'])
        if damage == 'url': source['source_url'] += '&different=1'
        if damage == 'clock': source['observed_at'] = (NOW - timedelta(seconds=1)).isoformat()
        if damage == 'hash': source['content_hash'] = '0' * 64
        if damage == 'plan': source['plan_sha256'] = '0' * 64
        if damage == 'page': source['params']['page'] = -1
        def forbidden(*args, **kwargs):
            raise AssertionError('NEW_WRITER_REACHED')
        monkeypatch.setattr(TemporalStore, 'initialize', forbidden)
        with pytest.raises(ValueError):
            admission.preflight_page(tmp_path, source, plan(), lock, clock=lambda: NOW)
    assert receipts(tmp_path) == []


def test_lock_required_before_database_access(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('DB_REACHED')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    with pytest.raises(ValueError, match='ROOT_RUN_LOCK'):
        admission.preflight_page(tmp_path, {}, {}, None)


def test_exact_prior_reuse_has_no_new_writer_or_retimestamp(tmp_path, monkeypatch):
    seeded(tmp_path)
    with RunLock(tmp_path / 'data/arthur/run.lock') as lock:
        value = candidate(tmp_path, lock)
        assert admission.admit_page(tmp_path, value, lock, clock=lambda: NOW)['new_receipts'] == 2
        before = receipts(tmp_path)
        def forbidden(*args, **kwargs):
            raise AssertionError('REUSE_MUST_NOT_INITIALIZE_WRITER')
        monkeypatch.setattr(TemporalStore, 'initialize', forbidden)
        result = admission.admit_page(tmp_path, value, lock, clock=lambda: NOW + timedelta(seconds=1))
    assert result['status'] == 'READONLY_ALREADY_COMPLETED'
    assert receipts(tmp_path) == before


@pytest.mark.parametrize('race', ('raw', 'foreign_version'))
def test_cas_change_rejects_all_witnesses(tmp_path, monkeypatch, race):
    seeded(tmp_path)
    with RunLock(tmp_path / 'data/arthur/run.lock') as lock:
        value = candidate(tmp_path, lock)
        original = admission._save
        def change(path, event):
            original(path, event)
            if not path.name.endswith('-intent.json'):
                return
            if race == 'raw':
                raw = tmp_path / 'data/raw' / value['raw']['relative_path']
                raw.write_bytes(raw.read_bytes() + b' ')
            else:
                from goal_analysis.storage.historical_results import HistoricalResults
                record = deepcopy(next(iter(value['records'].values())))
                record['provider_id'] = 'mt_999'
                HistoricalResults(Database(tmp_path / 'data/goal_analysis.sqlite3'), clock=lambda: NOW).ingest([record])
        monkeypatch.setattr(admission, '_save', change)
        with pytest.raises(ValueError):
            admission.admit_page(tmp_path, value, lock, clock=lambda: NOW)
    assert receipts(tmp_path) == []
    outcome = json.loads(next((tmp_path / 'data/arthur/historical-page-availability').glob('*-outcome.json')).read_bytes())
    assert outcome['status'] == 'STOPPED' and not outcome['committed']


def test_postcommit_stop_blocks_cached_normal_retry_before_sink(tmp_path, monkeypatch):
    original = admission._save
    failed = []
    def fail_once(path, value):
        if path.name.endswith('-outcome.json') and value['status'] == 'COMPLETED' and not failed:
            failed.append(True)
            raise OSError('TESTONLY postcommit output failure')
        return original(path, value)
    monkeypatch.setattr(admission, '_save', fail_once)
    with RunLock(tmp_path / 'data/arthur/run.lock') as lock, pytest.raises(OSError, match='postcommit'):
        run(tmp_path, plan(), Transport(page()), unified_results=True, root_lock=lock)
    assert len(receipts(tmp_path)) == 2
    assert not checkpoint(tmp_path, plan()).exists()
    monkeypatch.setattr(admission, '_save', original)
    def forbidden(*args, **kwargs):
        raise AssertionError('STOPPED_PAGE_MUST_NOT_REENTER_SINK')
    monkeypatch.setattr(HistoricalResultSink, '__init__', forbidden)
    with RunLock(tmp_path / 'data/arthur/run.lock') as lock, pytest.raises(RuntimeError, match='STOPPED_NO_RETRY'):
        run(tmp_path, plan(), Transport(), unified_results=True, root_lock=lock, max_requests=0)


def test_clock_rollback_or_future_source_cannot_admit(tmp_path):
    seeded(tmp_path)
    with RunLock(tmp_path / 'data/arthur/run.lock') as lock:
        value = candidate(tmp_path, lock)
        with pytest.raises(ValueError):
            admission.admit_page(tmp_path, value, lock, clock=lambda: NOW - timedelta(seconds=1))
        ticks = iter((NOW + timedelta(seconds=5), NOW + timedelta(seconds=4)))
        with pytest.raises(ValueError, match='CLOCK_MOVED_BACKWARDS'):
            admission.admit_page(tmp_path, value, lock, clock=lambda: next(ticks))
    assert receipts(tmp_path) == []


def test_precommit_sql_failure_rolls_back_raw_and_results_and_preserves_stop(tmp_path, monkeypatch):
    original = TemporalStore.initialize
    def fail_result(self):
        original(self)
        with self.database.transaction() as db:
            db.execute("""CREATE TRIGGER testonly_receipt_failure BEFORE INSERT ON temporal_receipts
                WHEN NEW.namespace='result' BEGIN SELECT RAISE(ABORT,'TESTONLY precommit failure'); END""")
    monkeypatch.setattr(TemporalStore, 'initialize', fail_result)
    with RunLock(tmp_path / 'data/arthur/run.lock') as lock, pytest.raises(sqlite3.IntegrityError, match='precommit'):
        run(tmp_path, plan(), Transport(page()), unified_results=True, root_lock=lock)
    assert receipts(tmp_path) == []
    assert not checkpoint(tmp_path, plan()).exists()
    with closing(ro(tmp_path)) as db:
        assert db.execute('SELECT count(*) FROM historical_result_versions').fetchone()[0] == 1
    outcome = json.loads(next((tmp_path / 'data/arthur/historical-page-availability').glob('*-outcome.json')).read_bytes())
    assert outcome['status'] == 'STOPPED' and outcome['durable_new_receipts'] == 0


def test_invalid_nonempty_prior_receipt_rejects_before_new_writer(tmp_path, monkeypatch):
    seeded(tmp_path)
    database = Database(tmp_path / 'data/goal_analysis.sqlite3')
    TemporalStore(database, tmp_path / 'data/raw', clock=lambda: NOW).initialize()
    with RunLock(tmp_path / 'data/arthur/run.lock') as lock:
        value = candidate(tmp_path, lock)
        with database.transaction() as db:
            db.execute('INSERT INTO temporal_receipts VALUES (?,?,?,?,?,?,?,?,?)',
                ('bad-testonly-id', 'raw', value['raw']['content_hash'], '0' * 64,
                 NOW.isoformat(), None, None, NOW.isoformat(), '{}'))
        def forbidden(*args, **kwargs):
            raise AssertionError('INVALID_PRIOR_MUST_NOT_REACH_NEW_WRITER')
        monkeypatch.setattr(TemporalStore, 'initialize', forbidden)
        with pytest.raises(ValueError, match='PRIOR_RECEIPT'):
            admission.admit_page(tmp_path, value, lock, clock=lambda: NOW)
    assert len(receipts(tmp_path)) == 1
    assert not (tmp_path / 'data/arthur/historical-page-availability').exists()
