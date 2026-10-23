"""Physical presence at NOW cannot manufacture old or fresh HTTP knowledge."""
import subprocess
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from test_native_history_shadow import NOW, put, setup

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import daily_native_shadow
from goal_analysis.jobs.native_history_shadow import native_history_view
from goal_analysis.jobs.native_source_admission import admit_history
from goal_analysis.storage.raw_admission import admit_raw_sources
from goal_analysis.storage.raw_evidence import read_witnessed_raw


def snapshot(database):
    with closing(database.connect()) as db:
        return {name: [dict(r) for r in db.execute(f'SELECT * FROM {name} ORDER BY rowid')]
                for name in ('raw_snapshots', 'temporal_receipts')}


def test_targeted_physical_presence_is_known_only_now_and_old_records_remain_exact(tmp_path):
    database, _, fixture = setup(tmp_path, witness=False)
    before = snapshot(database)
    keys = [r['content_hash'] for r in before['raw_snapshots']]
    result = admit_raw_sources(database, tmp_path, keys, clock=lambda: NOW)
    assert result['created'] == 2 and result['kind'].startswith('CURRENT_PHYSICAL')
    assert snapshot(database)['raw_snapshots'] == before['raw_snapshots']
    view = native_history_view(tmp_path, [fixture], as_of=NOW)
    assert len(view['history']) == 1
    assert not native_history_view(tmp_path, [fixture], as_of=NOW-timedelta(microseconds=1))['history']
    after = snapshot(database)
    assert all(r['available_at'] == NOW.isoformat(timespec='microseconds') for r in after['temporal_receipts'])
    assert all(r['observed_at'] != r['available_at'] for r in after['temporal_receipts'])
    again = admit_raw_sources(database, tmp_path, keys, clock=lambda: NOW+timedelta(minutes=5))
    assert again['created'] == 0 and again['reused'] == 2
    assert snapshot(database) == after


def test_history_scope_never_witnesses_target_prices_or_unrelated_competitions(tmp_path):
    database, _, fixture = setup(tmp_path, witness=False)
    from test_native_history_shadow import raw_match

    put(database, tmp_path, [raw_match(competition_id='comp_2')], comp='comp_2')
    original = deepcopy(fixture)
    result = admit_history(tmp_path, [fixture])
    assert result['created'] == 1 and result['target_or_price_sources_admitted'] == 0
    state = snapshot(database)
    witnessed = {r['source_key'] for r in state['temporal_receipts']}
    assert fixture['fixture_source']['content_hash'] not in witnessed and fixture == original
    assert all('competition_id=comp_1' in r['source_url'] and 'status=finished' in r['source_url']
               for r in state['raw_snapshots'] if r['content_hash'] in witnessed)
    # An admitted old historical body cannot certify stale/unwitnessed targets.
    assert not native_history_view(tmp_path, [fixture], as_of=NOW+timedelta(days=1))['fixtures']


@pytest.mark.parametrize('failure', ['payload', 'metadata', 'future', 'missing'])
def test_invalid_sources_are_rejected_without_new_receipt(tmp_path, failure):
    database, _, fixture = setup(tmp_path, witness=False)
    key = fixture['fixture_source']['content_hash']
    with closing(database.connect()) as db:
        row = dict(db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?', (key,)).fetchone())
    if failure == 'payload':
        (tmp_path/'data/raw'/row['relative_path']).write_bytes(b'bad')
    elif failure == 'metadata':
        with database.transaction() as db:
            db.execute('UPDATE raw_snapshots SET size_bytes=size_bytes+1 WHERE content_hash=?', (key,))
    elif failure == 'future':
        with database.transaction() as db:
            db.execute('UPDATE raw_snapshots SET fetched_at=? WHERE content_hash=?',
                       ((NOW+timedelta(seconds=1)).isoformat(), key))
    else:
        key = 'a'*64
    result = admit_raw_sources(database, tmp_path, [key], clock=lambda: NOW)
    assert result['created'] == 0 and result['status'] == 'GAPS' and result['rejected']
    assert snapshot(database)['temporal_receipts'] == []


def test_changed_metadata_cannot_obtain_second_version_for_same_raw_hash(tmp_path):
    database, _, fixture = setup(tmp_path)
    before = snapshot(database)['temporal_receipts']
    key = fixture['fixture_source']['content_hash']
    with database.transaction() as db:
        db.execute('UPDATE raw_snapshots SET resource_type=? WHERE content_hash=?', ('modified', key))
    result = admit_raw_sources(database, tmp_path, [key], clock=lambda: NOW)
    assert result['created'] == result['reused'] == 0
    assert result['rejected'][0]['reason'] == 'SOURCE_CHANGED_AFTER_WITNESS'
    assert snapshot(database)['temporal_receipts'] == before


@pytest.mark.parametrize('keys', [[{}], ['bad'], ['a'*64]*2, ['a'*64]*201, None])
def test_explicit_key_bounds_checked_before_io(tmp_path, keys):
    from goal_analysis.storage import Database

    path = tmp_path/'missing/db.sqlite3'
    with pytest.raises(ValueError, match='BOUNDED_EXPLICIT'):
        admit_raw_sources(Database(path), tmp_path, keys)
    assert not path.exists()


def test_scope_budget_applied_before_any_admission(tmp_path, monkeypatch):
    from test_native_history_shadow import raw_match

    from goal_analysis.jobs import native_source_admission as module

    database, _, fixture = setup(tmp_path, witness=False)
    put(database, tmp_path, [raw_match('mt_2')], page=2)
    monkeypatch.setattr(module, 'MAX_SOURCES', 1)
    assert admit_history(tmp_path, [fixture])['status'] == 'SOURCE_LIMIT'
    assert snapshot(database)['temporal_receipts'] == []


def test_global_clock_cannot_move_behind_prior_witness(tmp_path):
    database, _, fixture = setup(tmp_path)
    with pytest.raises(ValueError, match='CLOCK_MOVED_BACKWARDS'):
        admit_raw_sources(database, tmp_path, [fixture['fixture_source']['content_hash']],
                          clock=lambda: NOW-timedelta(microseconds=1))


def test_byte_budget_prevents_large_reads(tmp_path, monkeypatch):
    from goal_analysis.storage import raw_admission

    database, _, fixture = setup(tmp_path, witness=False)
    monkeypatch.setattr(raw_admission, 'MAX_TOTAL_BYTES', 1)
    result = admit_raw_sources(database, tmp_path, [fixture['fixture_source']['content_hash']], clock=lambda: NOW)
    assert result['created'] == 0 and 'BYTE_BUDGET' in result['rejected'][0]['reason']


def test_admission_cannot_refresh_old_raw_or_native_http_target_clock(tmp_path):
    database, _, fixture = setup(tmp_path, witness=False)
    key = fixture['fixture_source']['content_hash']
    admit_raw_sources(database, tmp_path, [key], clock=lambda: NOW)
    from goal_analysis.jobs.xg_specialist_shadow import _targets

    fixture['fixture_source']['observed_at'] = (NOW-timedelta(hours=1)).isoformat()
    assert not _targets(tmp_path, {fixture['fixture_id']: fixture}, NOW)[0]
    with closing(database.connect()) as db:
        row = dict(db.execute('SELECT * FROM raw_snapshots WHERE content_hash=?', (key,)).fetchone())
        _, proof = read_witnessed_raw(db, row, tmp_path, NOW)
    assert proof['times']['observed_at'] == (NOW-timedelta(hours=1)).isoformat(timespec='microseconds')
    assert proof['times']['available_at'] == NOW.isoformat(timespec='microseconds')


def test_daily_preparation_restarts_cutoff_after_actual_admission_without_external_fetch(tmp_path, monkeypatch):
    from test_daily_native_shadow import NOW as CURRENT
    from test_daily_native_shadow import staged

    run_id, report, _, _ = staged(tmp_path)
    from goal_analysis.jobs import native_source_admission

    prepared = []
    def admit(root, fixtures):
        prepared.append(1)
        return {'status': 'WITNESSED_NOW', 'created': 1}
    monkeypatch.setattr(native_source_admission, 'admit_history', admit)
    calls = []
    def spawn(command, **kwargs):
        calls.append(command)
        assert datetime.fromisoformat(command[command.index('--as-of')+1]) > CURRENT
        assert prepared == [1]
        raise subprocess.TimeoutExpired(command, 1)
    monkeypatch.setattr(daily_native_shadow.subprocess, 'run', spawn)
    result = daily_native_shadow.run(tmp_path, run_id, canonical_sha256(report),
        {'enabled_profiles': []}, as_of=CURRENT, prepare_sources=True)
    assert result['source_admission']['created'] == 1 and len(calls) == 1


def test_metadata_race_between_committed_read_and_receipt_commit_is_rejected(tmp_path):
    database, _, fixture = setup(tmp_path, witness=False)
    key = fixture['fixture_source']['content_hash']
    def changed():
        with database.transaction() as db:
            db.execute('UPDATE raw_snapshots SET resource_type=? WHERE content_hash=?', ('race', key))
        return NOW
    result = admit_raw_sources(database, tmp_path, [key], clock=changed)
    assert result['created'] == 0 and result['rejected'][0]['reason'] == 'SOURCE_CHANGED_AFTER_COMMITTED_READ'
    assert snapshot(database)['temporal_receipts'] == []
