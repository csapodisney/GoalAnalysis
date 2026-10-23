"""Actual source/cache/canonical/strict-consumer plumbing, synthetic HTTP only."""
import json
import subprocess
from copy import deepcopy
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from test_player_specialist_shadow import NOW, environment
from test_player_specialist_shadow import run as specialist

from goal_analysis.agents import canonical_sha256
from goal_analysis.jobs import daily_native_shadow
from goal_analysis.jobs import player_collection_shadow as module
from goal_analysis.jobs.the_stats_context import Resources
from goal_analysis.normalization.canonical import utc
from goal_analysis.providers.the_stats_api import TheStatsApiClient, TheStatsApiUsage
from goal_analysis.storage import SnapshotStore
from goal_analysis.storage.fixture_contexts import FixtureContexts
from goal_analysis.storage.provider_quota import SharedQuota
from goal_analysis.storage.temporal import TemporalStore


def setup(root, monkeypatch, *, quota=True, expired=False, fault=None):
    original_append = FixtureContexts.append
    with pytest.MonkeyPatch.context() as fixture_patch:
        fixture_patch.setattr(FixtureContexts, 'append', lambda self, events: original_append(
            self, [event for event in events if event['category'] != 'player_season_stats']))
        data, models, db, _ = environment(root, squad=False)
    for fixture in data['fixtures']:
        fixture['config_league_id'] = 3
    targets = [{'id': f['provider_fixture_id'], 'competition_id': f['provider_league_id'],
        'season_id': f['provider_season_id'], 'utc_date': f['kickoff'], 'status': 'scheduled',
        'is_neutral': False, **{side+'_team': {'id': f['provider_'+side+'_team_id'],
            'name': f[side+'_team']} for side in ('home', 'away')}} for f in data['fixtures']]
    source = SnapshotStore(db, root/'data/raw').save('the_stats_api', 'named_target',
        json.dumps({'data': targets}).encode(), 'application/json', NOW-timedelta(minutes=1),
        'https://api.thestatsapi.com/api/football/matches?status=scheduled')
    for f in data['fixtures']:
        f['fixture_source'] = {'provider': 'the_stats_api', 'content_hash': source.content_hash,
            'source_url': source.source_url, 'observed_at': (NOW-timedelta(minutes=1)).isoformat(),
            'resource_type': 'daily_context', 'published_at': None}
    TemporalStore(db, root/'data/raw', clock=lambda: NOW-timedelta(seconds=1)).capture('raw', limit=100)
    calls = []

    def transport(url, headers, **kwargs):
        assert headers['Authorization'] == 'Bearer synthetic-player-collection'
        assert kwargs['timeout'] == 3
        path = urlsplit(url).path; params = parse_qs(urlsplit(url).query); calls.append((path, params))
        subject = path.split('/')[-2]
        if '/teams/' in path:
            player = 'pl_'+subject[3:]
            body = [{'id': player, 'name': 'Player', 'position': 'F',
                     'current_team': {'id': 'tm_999' if fault == 'wrong_team' else subject}}]
        else:
            body = {'player_id': subject, 'team_id': 'tm_'+subject[3:],
                'competition_id': params['competition_id'][0], 'season_id': params['season_id'][0],
                'appearances': 10, 'starts': 10, 'minutes_played': 900,
                'scoring': {'goals': 8, 'assists': 3, 'expected_goals': 5,
                            'expected_assists': 3, 'xg_matches': 10}}
        response_headers = {'X-RateLimit-Limit': '120', 'X-RateLimit-Remaining': str(119-len(calls)),
            'X-RateLimit-Reset': str(int((NOW+timedelta(minutes=1)).timestamp())),
            'X-Monthly-Quota-Limit': '100000', 'X-Monthly-Quota-Remaining': '90000',
            'X-Monthly-Quota-Reset': str(int((NOW+timedelta(days=30)).timestamp()))}
        return ({'error': {'code': 'RATE_LIMITED'}} if fault == '429' else {'data': body}), response_headers, 429 if fault == '429' else 200

    def factory(root, **kwargs):
        return TheStatsApiClient('synthetic-player-collection', transport=transport, **kwargs)
    monkeypatch.setattr(module, 'TheStatsPrimaryClient', factory)
    if quota:
        observed = NOW-timedelta(minutes=2) if expired else NOW
        q = SharedQuota(root, kind='daily', clock=lambda: observed); identifier = q.begin()
        q.finish(identifier, {'authenticated': True, 'http_status': 200, 'observed_at': observed.isoformat()},
            TheStatsApiUsage(minute_limit=120, minute_remaining=119,
                minute_reset=int((observed+timedelta(minutes=1)).timestamp()), monthly_limit=100000,
                monthly_remaining=90000, monthly_reset=int((NOW+timedelta(days=30)).timestamp())))
    return data, models, calls, factory


def collect(root, data, **kwargs):
    return module.collect(root, data['fixtures'], clock=lambda: NOW, **kwargs)


def test_real_new_roster_stats_are_consumed_by_unchanged_strict_specialist(tmp_path, monkeypatch):
    from goal_analysis.jobs import fixture_contexts

    monkeypatch.setattr(fixture_contexts, 'link_saved_context',
                        lambda *_a, **_k: pytest.fail('PLAYER_ONLY_NO_WEATHER_NEWS_RELINK'))
    data, models, calls, _ = setup(tmp_path, monkeypatch)
    original = deepcopy((data, models))
    assert specialist(tmp_path, data, models)['supported_selections'] == []
    result = collect(tmp_path, data)
    assert result['usage']['requests'] == len(calls) == 6
    assert result['accepted_context_categories']['squad'] == 3
    assert result['accepted_context_categories']['player_season_stats'] == 3
    assert result['supported_fixture_count'] == 3
    after = specialist(tmp_path, data, models)
    assert len(after['candidate_legs']) == 3
    assert after['status'] == 'SHADOW_CANDIDATE'
    assert (data, models) == original and result['model_fits'] == result['llm_requests'] == result['ledger_writes'] == 0
    assert not after['live_decision_effect'] and result['probability_adjustment'] == 0
    for view in result['fixture_evidence'].values():
        assert view['lineup_status'] == 'MISSING' and view['signals']
        assert all(utc(event['available_at']) <= NOW for event in view['source_events'])


def test_second_pass_is_cache_only_with_no_quota_needed_and_no_new_events(tmp_path, monkeypatch):
    data, _, calls, _ = setup(tmp_path, monkeypatch)
    first = collect(tmp_path, data); calls.clear()
    monkeypatch.setattr(module.SharedQuota, 'allowance', lambda *_: pytest.fail('CACHE_MUST_WIN'))
    second = collect(tmp_path, data, max_requests=0)
    assert first['supported_fixture_count'] == second['supported_fixture_count'] == 3
    assert second['external_requests'] == 0 and not calls
    assert sum(row['status'] == 'REUSED_AVAILABLE_CONTEXT' for row in second['outcomes']) == 6
    assert second['canonical_linking']['counts']['events_created'] == 0


@pytest.mark.parametrize('fault', ['wrong_team', '429'])
def test_bad_response_does_not_create_player_support_and_quota_denial_stops(tmp_path, monkeypatch, fault):
    data, _, calls, _ = setup(tmp_path, monkeypatch, fault=fault)
    result = collect(tmp_path, data)
    assert result['supported_fixture_count'] == 0
    assert result['accepted_context_categories'].get('squad', 0) == 0
    if fault == '429':
        assert len(calls) == 1


def test_unknown_quota_never_fetches(tmp_path, monkeypatch):
    data, _, calls, _ = setup(tmp_path, monkeypatch, quota=False)
    result = collect(tmp_path, data)
    assert not calls and result['external_requests'] == 0
    assert any(row['status'] == 'UNKNOWN_QUOTA' for row in result['outcomes'])


def test_expired_minute_can_use_one_real_source_request_not_fake_header(tmp_path, monkeypatch):
    data, _, calls, _ = setup(tmp_path, monkeypatch, expired=True)
    result = collect(tmp_path, data)
    assert result['quota_bootstrap_requests'] == 1 and len(calls) == 6
    assert result['supported_fixture_count'] == 3


def test_network_budget_does_not_expand_to_complete_player_subset(tmp_path, monkeypatch):
    data, _, calls, _ = setup(tmp_path, monkeypatch)
    result = collect(tmp_path, data, max_requests=2)
    assert len(calls) == result['external_requests'] == 2
    assert result['supported_fixture_count'] == 0
    assert any(row['status'] == 'REQUEST_BUDGET' for row in result['outcomes'])


def test_complete_cache_prepass_uses_later_fixture_before_small_network_budget(tmp_path, monkeypatch):
    data, _, calls, factory = setup(tmp_path, monkeypatch)
    last = data['fixtures'][-1]; team = last['provider_away_team_id']
    client = factory(tmp_path, max_requests=2, timeout=3, clock=lambda: NOW)
    resources = Resources(tmp_path, client, lambda: NOW, 2)
    resources.get('/football/teams/'+team+'/players')
    resources.get('/football/players/pl_'+team[3:]+'/stats',
                  {'season_id': last['provider_season_id'], 'competition_id': last['provider_league_id']})
    calls.clear(); result = collect(tmp_path, data, max_requests=1)
    assert len(calls) == 1 and result['usage']['cache_hits'] == 2
    assert result['supported_fixture_count'] == 1


@pytest.mark.parametrize('time', [NOW+timedelta(minutes=31), NOW+timedelta(hours=6)-timedelta(minutes=15)])
def test_stale_or_locked_targets_do_not_trigger_requests(tmp_path, monkeypatch, time):
    data, _, calls, _ = setup(tmp_path, monkeypatch)
    result = module.collect(tmp_path, data['fixtures'], clock=lambda: time)
    assert result['verified_targets'] == result['external_requests'] == 0 and not calls


def test_time_limit_is_hard_and_invalid_or_duplicate_input_rejected(tmp_path, monkeypatch):
    data, _, calls, _ = setup(tmp_path, monkeypatch)
    result = collect(tmp_path, data, max_seconds=4)
    assert not calls and all(row['status'] == 'COLLECTION_TIME_LIMIT' for row in result['outcomes'])
    with pytest.raises(ValueError, match='DUPLICATE'):
        module.collect(tmp_path, data['fixtures']*2)
    with pytest.raises(ValueError, match='BUDGET'):
        collect(tmp_path, data, max_requests=21)


@pytest.mark.parametrize('flag', [None, 1, 'false'])
def test_saved_context_switch_requires_boolean_before_any_storage(tmp_path, flag):
    from goal_analysis.jobs.fixture_contexts import link_report

    with pytest.raises(ValueError, match='BOOLEAN'):
        link_report(tmp_path, {}, include_saved_context=flag)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('fail', [False, True])
def test_parent_runs_player_collection_before_worker_and_failure_is_local(tmp_path, monkeypatch, fail):
    from test_daily_native_shadow import staged

    run_id, report, path, _ = staged(tmp_path); before = path.read_bytes()
    (tmp_path/'config').mkdir()
    (tmp_path/'config/player-collection-shadow.json').write_text(json.dumps(module.POLICY))
    calls = []
    def acquire(*args, **kwargs):
        calls.append('player')
        if fail:
            raise ValueError('sensitive-detail')
        return {'status': 'OBSERVED', 'external_requests': 2, 'records': [], 'fixture_evidence': {}}
    monkeypatch.setattr(module, 'collect', acquire)
    def worker(command, **kwargs):
        calls.append('worker')
        assert 'THE_STATS_API_KEY' not in kwargs['env']
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(daily_native_shadow.subprocess, 'run', worker)
    result = daily_native_shadow.run(tmp_path, run_id, canonical_sha256(report), {}, as_of=NOW, prepare_sources=True)
    assert calls == ['player', 'worker'] and path.read_bytes() == before
    assert result['player_collection']['status'] == ('UNAVAILABLE' if fail else 'OBSERVED')
    assert 'sensitive-detail' not in json.dumps(result)
