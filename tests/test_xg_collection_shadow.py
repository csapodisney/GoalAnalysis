"""Real stored sources/receipts/strict xG consumer, synthetic HTTP only."""
import json
import subprocess
from copy import deepcopy
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from test_xg_specialist_shadow import NOW
from test_xg_specialist_shadow import environment as target_environment

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import daily_native_shadow
from goal_analysis.jobs import xg_collection_shadow as module
from goal_analysis.jobs.the_stats_context import Resources
from goal_analysis.jobs.xg_shadow import inspect_xg_rates
from goal_analysis.normalization.canonical import utc
from goal_analysis.providers.the_stats_api import TheStatsApiClient, TheStatsApiUsage
from goal_analysis.storage import SnapshotStore
from goal_analysis.storage.provider_quota import SharedQuota
from goal_analysis.storage.temporal import TemporalStore


def setup(root, monkeypatch, *, quota=True, expired=False, fault=None, witness=True, change=None):
    data, _, db, _ = target_environment(root, history_teams=[])
    raw = SnapshotStore(db, root/'data/raw'); history = []
    for index, fixture in enumerate(data['fixtures']):
        for side in ('home', 'away'):
            for number in range(3):
                opponent = f'tm_{100+index*6+number}'
                other = 'away' if side == 'home' else 'home'
                row = {'id': f'mt_{1000+len(history)}', 'competition_id': fixture['provider_league_id'],
                    'season_id': fixture['provider_season_id'], 'status': 'finished',
                    'utc_date': (NOW-timedelta(days=number+1)).isoformat(),
                    side+'_team': {'id': fixture['provider_'+side+'_team_id']}, other+'_team': {'id': opponent},
                    'is_neutral': False, 'xg_available': True, 'xg_quality': 'full',
                    'score': {'went_to_extra_time': False, 'went_to_penalties': False}}
                history.append(row)
    if change:
        history[0].update(change)
    source = raw.save('the_stats_api', 'xg_history', json.dumps({'data': history}).encode(),
        'application/json', NOW-timedelta(minutes=2),
        'https://api.thestatsapi.com/api/football/matches?status=finished&per_page=100')
    if witness:
        TemporalStore(db, root/'data/raw', clock=lambda: NOW-timedelta(seconds=1)).capture('raw', limit=100)
    calls = []
    def transport(url, headers, **kwargs):
        assert kwargs['timeout'] == 3
        calls.append(url)
        native = urlsplit(url).path.split('/')[-2]
        body = {'match_id': 'mt_999999' if fault == 'wrong_id' else native,
            'overview': {'expected_goals': {'first_half': {'home': .8, 'away': .5},
                'second_half': {'home': .7, 'away': .6}, 'all': {'home': 99., 'away': 99.}}}}
        if fault == 'missing_half':
            del body['overview']['expected_goals']['first_half']['home']
        usage = {'X-RateLimit-Limit': '120', 'X-RateLimit-Remaining': str(119-len(calls)),
            'X-RateLimit-Reset': str(int((NOW+timedelta(minutes=1)).timestamp())),
            'X-Monthly-Quota-Limit': '100000', 'X-Monthly-Quota-Remaining': '90000',
            'X-Monthly-Quota-Reset': str(int((NOW+timedelta(days=30)).timestamp()))}
        return ({'error': {'code': 'RATE_LIMITED'}} if fault == '429' else {'data': body}), usage, 429 if fault == '429' else 200
    def factory(root, **kwargs):
        return TheStatsApiClient('synthetic-xg-source-only', transport=transport, **kwargs)
    monkeypatch.setattr(module, 'TheStatsPrimaryClient', factory)
    if quota:
        observed = NOW-timedelta(minutes=2) if expired else NOW
        q = SharedQuota(root, kind='daily', clock=lambda: observed); identifier = q.begin()
        q.finish(identifier, {'authenticated': True, 'http_status': 200, 'observed_at': observed.isoformat()},
            TheStatsApiUsage(minute_limit=120, minute_remaining=119,
                minute_reset=int((observed+timedelta(minutes=1)).timestamp()), monthly_limit=100000,
                monthly_remaining=90000, monthly_reset=int((NOW+timedelta(days=30)).timestamp())))
    return data, db, history, calls, factory, source


def collect(root, data, **kwargs):
    return module.collect(root, data['fixtures'], clock=lambda: NOW, **kwargs)


def test_new_match_sources_feed_unchanged_strict_xg_reader_without_old_replay_leak(tmp_path, monkeypatch):
    data, _, _, calls, _, _ = setup(tmp_path, monkeypatch)
    original = deepcopy(data)
    assert inspect_xg_rates(tmp_path, as_of=NOW)['fit']['accepted_matches'] == 0
    result = collect(tmp_path, data)
    assert result['authenticated_requests'] == result['external_requests'] == len(calls) == 18
    assert result['accepted_matches'] == 18 and result['planned_matches'] == 18
    fit = inspect_xg_rates(tmp_path, as_of=NOW)
    assert fit['fit']['accepted_matches'] == 18
    assert inspect_xg_rates(tmp_path, as_of=NOW-timedelta(seconds=1))['fit']['accepted_matches'] == 0
    for row in fit['envelopes']:
        assert row['payload']['home_xg'] == pytest.approx(1.5)
        assert row['payload']['away_xg'] == pytest.approx(1.1)
        assert utc(row['payload']['available_at']) == NOW
    assert data == original and not result['live_decision_effect']
    assert result['llm_requests'] == result['model_fits'] == result['ledger_writes'] == 0


def test_second_pass_uses_witnessed_raw_without_quota_or_http(tmp_path, monkeypatch):
    data, _, _, calls, _, _ = setup(tmp_path, monkeypatch)
    collect(tmp_path, data); calls.clear()
    monkeypatch.setattr(SharedQuota, 'allowance', lambda *_: pytest.fail('STORED_SOURCES_FIRST'))
    result = collect(tmp_path, data, max_requests=0)
    assert not calls and result['accepted_matches'] == 18
    assert {r['status'] for r in result['outcomes']} == {'REUSED_WITNESSED_XG'}


def test_committed_cached_stats_receive_only_actual_new_presence_receipt(tmp_path, monkeypatch):
    from goal_analysis.storage.http_observations import HttpObservations

    data, db, history, calls, factory, _ = setup(tmp_path, monkeypatch, quota=False)
    resources = Resources(tmp_path, factory(tmp_path, max_requests=1, timeout=3, clock=lambda: NOW), lambda: NOW, 1)
    def observer_unavailable(*args):
        raise ValueError('OBSERVATION_UNAVAILABLE')
    with monkeypatch.context() as observer_patch:
        observer_patch.setattr(HttpObservations, 'admit', observer_unavailable)
        resources.get('/football/matches/'+history[0]['id']+'/stats')
    calls.clear()
    with db.connect() as connection:
        key = connection.execute("SELECT content_hash FROM raw_snapshots WHERE source_url LIKE '%/mt_1000/stats'").fetchone()[0]
        assert not connection.execute("SELECT 1 FROM temporal_receipts WHERE namespace='raw' AND source_key=?", (key,)).fetchone()
    result = collect(tmp_path, data, max_requests=0)
    assert not calls and result['accepted_matches'] == 1
    assert inspect_xg_rates(tmp_path, as_of=NOW)['fit']['accepted_matches'] == 1
    assert inspect_xg_rates(tmp_path, as_of=NOW-timedelta(seconds=1))['fit']['accepted_matches'] == 0


def test_request_budget_does_not_expand_to_fill_history(tmp_path, monkeypatch):
    data, _, _, calls, _, _ = setup(tmp_path, monkeypatch)
    result = collect(tmp_path, data, max_requests=2)
    assert len(calls) == result['accepted_matches'] == 2
    assert sum(r['status'] == 'REQUEST_BUDGET' for r in result['outcomes']) == 16


def test_unknown_quota_never_fetches(tmp_path, monkeypatch):
    data, _, _, calls, _, _ = setup(tmp_path, monkeypatch, quota=False)
    result = collect(tmp_path, data)
    assert not calls and not result['accepted_matches']
    assert {r['status'] for r in result['outcomes']} == {'UNKNOWN_QUOTA'}


def test_expired_quota_has_one_necessary_real_get_bootstrap(tmp_path, monkeypatch):
    data, _, _, calls, _, _ = setup(tmp_path, monkeypatch, expired=True)
    result = collect(tmp_path, data, max_requests=2)
    assert len(calls) == 2 and result['quota_bootstrap_requests'] == 1


@pytest.mark.parametrize('fault', ['wrong_id', 'missing_half', '429'])
def test_bad_response_never_fills_missing_xg_with_all_or_zero(tmp_path, monkeypatch, fault):
    data, _, _, calls, _, _ = setup(tmp_path, monkeypatch, fault=fault)
    result = collect(tmp_path, data, max_requests=2)
    assert not result['accepted_matches']
    assert inspect_xg_rates(tmp_path, as_of=NOW)['fit']['accepted_matches'] == 0
    assert len(calls) == (1 if fault == '429' else 2)


@pytest.mark.parametrize('change', [{'xg_quality': 'partial'}, {'is_neutral': None},
    {'score': {'went_to_extra_time': True, 'went_to_penalties': False}},
    {'season_id': 'sn_999'}, {'utc_date': (NOW+timedelta(hours=1)).isoformat()},
    {'home_team': None}, {'score': None}])
def test_unknown_quality_or_wrong_scope_is_not_a_candidate(tmp_path, monkeypatch, change):
    data, _, _, calls, _, _ = setup(tmp_path, monkeypatch, change=change)
    result = collect(tmp_path, data)
    assert len(calls) == result['accepted_matches'] == 17
    assert not any('mt_1000/stats' in url for url in calls)


def test_later_cached_match_is_consumed_before_small_network_budget(tmp_path, monkeypatch):
    data, _, history, calls, factory, _ = setup(tmp_path, monkeypatch)
    resources = Resources(tmp_path, factory(tmp_path, max_requests=1, timeout=3, clock=lambda: NOW), lambda: NOW, 1)
    resources.get('/football/matches/'+history[-1]['id']+'/stats'); calls.clear()
    result = collect(tmp_path, data, max_requests=1)
    assert len(calls) == 1 and result['accepted_matches'] == 2
    assert any(e['payload']['fixture_id'] == history[-1]['id'] for e in result['envelopes'])


def test_corrupt_latest_raw_is_not_hidden_by_refetch_or_old_fallback(tmp_path, monkeypatch):
    data, db, _, calls, _, _ = setup(tmp_path, monkeypatch)
    collect(tmp_path, data); calls.clear()
    with db.connect() as connection:
        relative = connection.execute("SELECT relative_path FROM raw_snapshots WHERE source_url LIKE '%/mt_1000/stats'").fetchone()[0]
    path = tmp_path/'data/raw'/relative; path.write_bytes(path.read_bytes()+b' ')
    result = collect(tmp_path, data)
    assert not calls and result['accepted_matches'] == 17
    assert any(r['status'] == 'LATEST_STORED_XG_UNPROVEN' for r in result['outcomes'])


def test_unwitnessed_metadata_never_authorizes_a_stats_get(tmp_path, monkeypatch):
    data, _, _, calls, _, _ = setup(tmp_path, monkeypatch, witness=False)
    result = collect(tmp_path, data)
    assert not calls and result['planned_matches'] == 0


def test_stale_current_target_blocks_even_cached_history(tmp_path, monkeypatch):
    data, _, _, calls, _, _ = setup(tmp_path, monkeypatch)
    result = module.collect(tmp_path, data['fixtures'], clock=lambda: NOW+timedelta(minutes=31))
    assert not calls and result['verified_targets'] == 0


@pytest.mark.parametrize('fail', [False, True])
def test_parent_acquisition_precedes_worker_and_failure_is_local(tmp_path, monkeypatch, fail):
    from test_daily_native_shadow import staged

    from goal_analysis.jobs import native_source_admission

    run_id, report, path, _ = staged(tmp_path); original = path.read_bytes(); order = []
    (tmp_path/'config').mkdir(); (tmp_path/'config/xg-collection-shadow.json').write_text(json.dumps(module.POLICY))
    monkeypatch.setattr(native_source_admission, 'admit_history', lambda *_a: {'status': 'NO_DATA'})
    def acquire(*a, **k):
        order.append('xg')
        if fail:
            raise ValueError('sensitive-secret')
        return {'status': 'OBSERVED', 'external_requests': 2, 'envelopes': [], 'outcomes': []}
    monkeypatch.setattr(module, 'collect', acquire)
    def worker(command, **kwargs):
        order.append('worker')
        assert 'THE_STATS_API_KEY' not in kwargs['env']
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(daily_native_shadow.subprocess, 'run', worker)
    result = daily_native_shadow.run(tmp_path, run_id, canonical_sha256(report), {}, as_of=NOW, prepare_sources=True)
    assert order == ['xg', 'worker'] and path.read_bytes() == original
    assert result['xg_collection']['status'] == ('UNAVAILABLE' if fail else 'OBSERVED')
    assert 'sensitive-secret' not in json.dumps(result)
