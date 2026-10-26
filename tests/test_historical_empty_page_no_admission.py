"""TESTONLY empty-page cursor controls; no source admission or live transport."""
import json
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from test_historical_incremental_sink import NOW, match, page, plan

from goal_analysis.jobs import historical_page_availability as production
from goal_analysis.jobs import the_stats_history as history
from goal_analysis.jobs.historical_result_sink import HistoricalResultSink
from goal_analysis.jobs.portfolio import RunLock
from goal_analysis.providers.the_stats_api import BASE_URL
from goal_analysis.storage import Database, SnapshotStore


def fixture(root, rows, *, alias=True):
    database = Database(root / 'data/goal_analysis.sqlite3')
    database.initialize()
    payload = page(rows)
    if not rows: payload['meta']['total_pages'] = 0
    body = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    selected = plan()
    params = {**selected['queries'][0], 'page': 1, 'per_page': 100}
    url = BASE_URL + '/football/matches?' + urlencode(params)
    observed = NOW.isoformat()
    SnapshotStore(database, root / 'data/raw').save('the_stats_api', 'historical_matches', body, 'application/json',
        NOW - timedelta(days=1) if alias else NOW, url + '&old-source=1' if alias else url)
    contract = {'content_hash': sha256(body).hexdigest(), 'source_url': url,
        'observed_at': observed, 'plan_sha256': selected['plan_sha256'], 'params': params}
    return database, contract, selected


def ro(database):
    import sqlite3
    db = sqlite3.connect(database.path.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
    return db


def test_empty_alias_is_zero_admission_not_new_provenance(tmp_path):
    db, contract, selected = fixture(tmp_path, [])
    candidate = production._page
    with closing(ro(db)) as connection:
        result = candidate(connection, tmp_path, contract, selected, NOW)
    assert result['status'] == 'NO_ADMISSION_EMPTY_OR_NONEXACT_PAGE'
    assert result['empty_page_without_admission'] and not result['source_provenance_accepted']
    assert result['diagnostics']['accepted_rows'] == result['diagnostics']['rejected_rows'] == 0


@pytest.mark.parametrize('rows', ([match()], [match(score={'home': 8, 'away': 9})], [match(), match()],
                                  [match(), match(score={'regulation': {'home': 0, 'away': 0}})]))
def test_every_nonempty_alias_still_rejects_before_normalizer(tmp_path, rows, monkeypatch):
    db, contract, selected = fixture(tmp_path, rows)
    candidate = production._page
    def forbidden(*args, **kwargs): raise AssertionError('NONEMPTY_ALIAS_MUST_REJECT_BEFORE_NORMALIZATION')
    monkeypatch.setattr(history, '_ingest', forbidden)
    with closing(ro(db)) as connection, pytest.raises(ValueError, match='RAW_SOURCE_ALIAS_URL_OR_CLOCK_MISMATCH'):
        candidate(connection, tmp_path, contract, selected, NOW)


@pytest.mark.parametrize('damage', ('url', 'plan', 'future', 'page', 'hash'))
def test_empty_still_requires_current_page_contract(tmp_path, damage):
    db, original, selected = fixture(tmp_path, [])
    contract = deepcopy(original)
    if damage == 'url': contract['source_url'] += '&wrong=1'
    if damage == 'plan': contract['plan_sha256'] = '0' * 64
    if damage == 'future': contract['observed_at'] = (NOW + timedelta(seconds=1)).isoformat()
    if damage == 'page': contract['params']['page'] = 0
    if damage == 'hash': contract['content_hash'] = '0' * 64
    candidate = production._page
    with closing(ro(db)) as connection, pytest.raises((ValueError, KeyError)):
        candidate(connection, tmp_path, contract, selected, NOW)


def test_cached_empty_cursor_advances_without_sink_or_receipt_writer(tmp_path):
    db, contract, selected = fixture(tmp_path, [])
    candidate, runner = production._page, history._run
    work = tmp_path / 'data/arthur/the-stats-history' / selected['plan_sha256']
    history._atomic(work / 'page-00000-000001.json', contract)
    def forbidden(*args, **kwargs): raise AssertionError('EMPTY_MUST_NOT_CREATE_SINK_OR_WITNESS_OR_HTTP')
    class NoQuota:
        def legacy_state(self): return None
    with RunLock(tmp_path / 'data/arthur/run.lock') as lock, patch.object(history, '_run', runner), \
         patch.object(production, '_page', candidate), patch.object(HistoricalResultSink, '__init__', forbidden), \
         patch.object(production, 'admit_page', forbidden):
        result = history.run_history(tmp_path, selected, execute=True, daily_work_complete=True,
            max_requests=0, max_pages=1, api_key='TESTONLY-NONSECRET', transport=forbidden,
            clock=lambda: NOW, quota_observer=NoQuota(), unified_results=True, root_lock=lock)
    assert result['requests'] == 0 and result['processed_pages'] == result['cached_pages'] == 1
    assert result['pages'][0]['availability']['new_receipts'] == 0
    assert history._read(work / 'checkpoint.json')['query_index'] == 1
    with closing(ro(db)) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name IN ('historical_result_versions','temporal_receipts')").fetchone()
