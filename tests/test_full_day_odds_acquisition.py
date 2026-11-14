import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from goal_analysis.jobs.daily_223_live import run_daily223_live
from goal_analysis.providers.base import ProviderError
from goal_analysis.providers.the_stats_odds import TheStatsOddsFeed, event_from_prices

NOW = datetime(2026, 11, 14, 8, tzinfo=UTC)


def _fixture(number):
    return {'fixture_id': f'the_stats_api:mt_{number}', 'source_provider': 'the_stats_api',
        'provider_fixture_id': f'mt_{number}', 'provider_league_id': 'comp_1',
        'provider_home_team_id': f'tm_{number * 2}', 'provider_away_team_id': f'tm_{number * 2 + 1}',
        'provider_season_id': 'sn_1', 'config_league_id': 1, 'competition_type': 'LEAGUE',
        'home_team': f'Home {number}', 'away_team': f'Away {number}', 'sport_key': 'soccer_test',
        'kickoff': (NOW + timedelta(hours=4)).isoformat(), 'provider_status': 'NS',
        'target_date': NOW.date().isoformat()}


def _prices(number):
    raw = {'match_id': f'mt_{number}', 'bookmakers': [{'bookmaker': 'Test Book', 'markets': {
        'match_odds': {key: {'last_seen': price} for key, price in
            [('home', '2.1'), ('draw', '3.2'), ('away', '3.5')]}}}]}
    source = {'source_url': f'https://api.thestatsapi.com/api/football/matches/mt_{number}/odds',
        'content_hash': 'a' * 64, 'observed_at': NOW.isoformat()}
    return raw, source


def _feed(modes, *, counted=True):
    feed = object.__new__(TheStatsOddsFeed)
    feed.region, feed.max_credits, feed.clock = 'eu', 40, lambda: NOW
    feed.fixtures, feed.issues, feed.usage, feed.acquisition_outcomes = {}, [], [], []
    feed.calls = feed.reserved_credits = 0
    feed.full_day_acquisition = None
    feed.client = SimpleNamespace(last_usage=SimpleNamespace(monthly_remaining=1000))
    if counted:
        feed.client.request_count = 0

    class Resources:
        def __init__(self):
            self.usage = {'requests': 0 if 'network_bad_identity' in modes.values() else 40,
                'max_requests': 40, 'stopped_reason': None}
            self.lookups = []

        def get(self, endpoint, *, ttl):
            assert ttl == timedelta(minutes=5)
            number = int(endpoint.split('/')[-2].removeprefix('mt_'))
            self.lookups.append(number)
            mode = modes[number]
            if mode == 'budget':
                return None, None, 'REQUEST_BUDGET'
            raw, source = _prices(number)
            if mode == 'network_bad_identity':
                self.usage['requests'] += 1
                feed.client.request_count += 1
                raw['match_id'] = 'mt_999'
            return raw, source, None

    feed.resources = Resources()
    feed.bind_fixtures([_fixture(number) for number in modes])
    return feed


def test_budget_reason_survives_and_later_cached_sibling_remains_available():
    feed = _feed({1: 'budget', 2: 'cache'})
    original = deepcopy(feed.fixtures)
    result = feed.bulk('soccer_test', NOW.date())
    raw, source = _prices(2)
    assert result == [event_from_prices(raw, _fixture(2), source, {'h2h', 'totals'})]
    assert feed.fixtures == original and feed.resources.lookups == [1, 2]
    refusal, reused = feed.acquisition_outcomes
    assert refusal['reason_code'] == 'REQUEST_BUDGET'
    assert refusal['request_state'] == 'NO_NEW_TRANSPORT'
    assert refusal['resource_attempt_delta'] == refusal['transport_attempt_delta'] == 0
    assert reused['outcome'] == 'EVENT_AVAILABLE' and reused['priced'] is True
    assert reused['request_state'] == 'NO_NEW_TRANSPORT' and reused['source_ref'] == source
    assert feed.issues[0]['acquisition_index'] == 0
    assert feed.issues[0]['reason_code'] == 'REQUEST_BUDGET'
    assert feed.resources.usage['stopped_reason'] is None


def test_transport_attempt_and_original_identity_rejection_remain_distinct():
    feed = _feed({1: 'network_bad_identity'})
    with pytest.raises(ValueError, match='fixture identity mismatch'):
        feed._event(_fixture(1), {'h2h', 'totals'})
    row = feed.acquisition_outcomes[0]
    assert row['outcome'] == 'UNAVAILABLE'
    assert row['reason_code'] == 'SOURCE_OR_MARKET_NORMALIZATION_REJECTED'
    assert row['request_state'] == 'TRANSPORT_ATTEMPTED'
    assert row['resource_attempt_delta'] == row['transport_attempt_delta'] == 1
    assert row['source_ref'] is None and row['priced'] is None


def test_missing_counter_is_unknown_and_prematch_guard_does_not_call_resources():
    feed = _feed({1: 'cache'}, counted=False)
    assert feed._event(_fixture(1), {'h2h'})['bookmakers']
    assert feed.acquisition_outcomes[0]['request_state'] == 'UNKNOWN'
    assert feed.acquisition_outcomes[0]['transport_attempt_delta'] is None
    blocked = {**_fixture(1), 'kickoff': (NOW + timedelta(minutes=15)).isoformat()}
    with pytest.raises(ProviderError, match='pre-match lockout'):
        feed._event(blocked, {'h2h'})
    assert feed.resources.lookups == [1]
    assert feed.acquisition_outcomes[-1]['request_state'] == 'NOT_ENTERED'
    assert feed.acquisition_outcomes[-1]['reason_code'] == 'PREMATCH_LOCKOUT'


def test_actual_collector_keeps_calendar_budget_reason_and_acquisition_artifact(monkeypatch):
    from goal_analysis.jobs import the_stats_live

    fixtures = [_fixture(1), _fixture(2)]
    collected = {'fixtures': {'observed_at': NOW.isoformat(), 'records': fixtures},
        'history': {'records': [], 'data_issues': []}}
    monkeypatch.setattr(the_stats_live, 'collect_live', lambda *args, **kwargs: deepcopy(collected))
    config = {'schema_version': 1, 'odds_region': 'eu', 'preferred_bookmakers': ['test_book'],
        'account_region': 'DE', 'currency': 'EUR', 'max_football_calls': 60,
        'max_odds_credits': 40, 'extra_markets': [], 'team_aliases': {},
        'leagues': [{'competition_id': 'TEST', 'api_football_id': 1, 'season': 2026,
            'history_seasons': [2026], 'odds_sport_key': 'soccer_test', 'competition_type': 'LEAGUE'}]}
    feed = _feed({1: 'budget', 2: 'budget'})
    bundle = run_daily223_live(config, SimpleNamespace(provider='the_stats_api'), feed,
        None, NOW.date(), lambda: NOW, construct_legacy=False, operating_contract_version=2)
    artifacts = bundle['artifacts']
    assert artifacts['fixtures']['records'] == fixtures
    assert artifacts['report']['construction_status'] == 'COLLECTION_COMPLETE'
    assert artifacts['candidate_input']['candidates'] == []
    reasons = [row for row in artifacts['report']['data_issues'] if row.get('reason_code') == 'REQUEST_BUDGET']
    assert [row['fixture_id'] for row in reasons] == [row['fixture_id'] for row in fixtures]
    assert all(row['request_state'] == 'NO_NEW_TRANSPORT' for row in reasons)
    assert artifacts['odds_acquisition'] == feed.acquisition_outcomes
    assert len(artifacts['odds_acquisition']) == 2
    assert any(row['status'] == 'NO_ACCEPTED_ODDS_EVENTS' for row in artifacts['report']['data_issues'])
    assert not any(row['status'] == 'EMPTY_ODDS_RESPONSE' for row in artifacts['report']['data_issues'])
    assert artifacts['config']['max_odds_credits'] == 40


class _Clock:
    def __init__(self):
        self.now, self.seconds, self.sleeps = NOW, 0.0, []

    def clock(self):
        return self.now

    def monotonic(self):
        return self.seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)
        self.seconds += seconds


def _authority():
    return {'version': 'NORMAL_FULL_DAY_ODDS_V1', 'run_id': '2026-11-14-TESTONLY',
        'target_date': '2026-11-14', 'authority': 'CURRENT_TEN_AGENT_NORMAL_FACTORY'}


def _actual_resources_feed(fixtures, timer):
    from goal_analysis.jobs.the_stats_context import Resources

    class EmptyConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, *_):
            return self

        def fetchone(self):
            return None

    client = SimpleNamespace(request_count=0, max_requests=40, last_response=None,
        last_raw_response=None, last_usage=SimpleNamespace(monthly_remaining=1000,
            minute_remaining=119, minute_reset=int(NOW.timestamp()) + 60), endpoints=[])

    def request(endpoint, params):
        assert params == {}
        client.endpoints.append(endpoint)
        client.request_count += 1
        number = int(endpoint.split('/')[-2].removeprefix('mt_'))
        raw, _ = _prices(number)
        raw['bookmakers'][0]['markets']['btts'] = {
            'yes': {'last_seen': '1.8'}, 'no': {'last_seen': '2.1'}}
        body = {'data': raw}
        client.last_raw_response = json.dumps(body).encode()
        client.last_response = {'observed_at': timer.clock().isoformat(), 'authenticated': True,
            'payload_sha256': hashlib.sha256(client.last_raw_response).hexdigest(),
            'source_url': 'https://api.thestatsapi.com/api' + endpoint}
        client.last_usage = SimpleNamespace(monthly_remaining=1000-client.request_count,
            minute_remaining=119, minute_reset=int(timer.clock().timestamp()) + 60)
        return body

    client.request = request
    resource = object.__new__(Resources)
    resource.root, resource.client, resource.clock = Path('TESTONLY'), client, timer.clock
    resource.database = SimpleNamespace(connect=EmptyConnection)
    resource.cache = SimpleNamespace(set=lambda *args, **kwargs: None)
    resource.snapshots = SimpleNamespace(save=lambda provider, kind, raw, *args:
        SimpleNamespace(content_hash=hashlib.sha256(raw).hexdigest()))
    resource._http_observations = SimpleNamespace(admit=lambda receipt: {'http_observation_status': 'VERIFIED'})
    resource.memo, resource.unavailable_capabilities = {}, set()
    resource.usage = {'requests': 0, 'cache_hits': 0, 'max_requests': 40,
        'stopped_reason': None, 'by_endpoint': {}}
    feed = object.__new__(TheStatsOddsFeed)
    feed.region, feed.max_credits, feed.clock = 'eu', 40, timer.clock
    feed.client, feed.resources = client, resource
    feed.fixtures, feed.issues, feed.usage, feed.acquisition_outcomes = {}, [], [], []
    feed.calls = feed.reserved_credits = 0
    feed.full_day_acquisition = None
    feed.bind_fixtures(fixtures)
    return feed


def _memo(feed, number):
    endpoint = f'/football/matches/mt_{number}/odds'
    key = json.dumps([endpoint, {}], sort_keys=True, separators=(',', ':'))
    raw, source = _prices(number)
    feed.resources.memo[(key, timedelta(minutes=5), False)] = (raw, source, None)
    return source


def test_full_day_actual_collector_resolves_n_and_fetches_all_markets_once(monkeypatch):
    from goal_analysis.jobs import the_stats_live

    timer, fixtures = _Clock(), [_fixture(1), _fixture(2)]
    feed = _actual_resources_feed(fixtures, timer)
    collected = {'fixtures': {'observed_at': NOW.isoformat(), 'records': fixtures},
        'history': {'records': [], 'data_issues': []}}
    monkeypatch.setattr(the_stats_live, 'collect_live', lambda *args, **kwargs: deepcopy(collected))
    config = {'schema_version': 1, 'odds_region': 'eu', 'preferred_bookmakers': ['test_book'],
        'account_region': 'DE', 'currency': 'EUR', 'max_football_calls': 60,
        'max_odds_credits': 40, 'extra_markets': ['btts', 'totals_h1'], 'team_aliases': {},
        'leagues': [{'competition_id': 'TEST', 'api_football_id': 1, 'season': 2026,
            'history_seasons': [2026], 'odds_sport_key': 'soccer_test', 'competition_type': 'LEAGUE'}]}
    artifacts = run_daily223_live(config, SimpleNamespace(provider='the_stats_api'), feed,
        None, NOW.date(), timer.clock, construct_legacy=False, operating_contract_version=2,
        full_day_odds_authority=_authority())['artifacts']
    assert artifacts['fixtures']['records'] == fixtures
    assert artifacts['report']['construction_status'] == 'COLLECTION_COMPLETE'
    assert artifacts['config']['max_odds_credits'] == feed.max_credits == 40
    assert feed.client.request_count == len(set(feed.client.endpoints)) == 2
    assert 'odds_extra' not in artifacts
    scope = artifacts['odds_acquisition_scope']
    assert scope['eligible_fixture_count'] == scope['effective_max_requests'] == 2
    assert scope['authority'] == _authority() and scope['configured_legacy_max_requests'] == 40
    assert scope['new_request_admissions'] == scope['transport_attempts'] == 2
    assert scope['customer_currency_cost'] is None
    assert 'btts' in artifacts['candidate_input']['market_coverage']
    assert any(row['status'] == 'REQUESTED_MARKETS_UNAVAILABLE' and 'totals_h1' in row['markets']
        for row in artifacts['report']['data_issues'])
    assert all(row['source_ref']['observed_at'] == NOW.isoformat() for row in feed.acquisition_outcomes)


def test_actual_resources_cache_precedes_header_pacing_and_no_request_is_retried():
    timer, fixtures = _Clock(), [_fixture(1), _fixture(2)]
    feed = _actual_resources_feed(fixtures, timer)
    source = _memo(feed, 1)
    feed.configure_full_day(fixtures, _authority(), ['btts'], monotonic=timer.monotonic, sleep=timer.sleep)
    feed.client.last_usage.minute_remaining = 2
    feed.client.last_usage.minute_reset = int(NOW.timestamp()) + 10
    feed.client.last_response = {'observed_at': NOW.isoformat(), 'authenticated': True}
    first = feed._event(fixtures[0], {'h2h'})
    assert first['bookmakers'] and timer.sleeps == [] and feed.client.request_count == 0
    assert feed.acquisition_outcomes[-1]['source_ref'] == source
    second = feed._event(fixtures[1], {'h2h', 'btts'})
    assert second['bookmakers'] and timer.sleeps == [10.0]
    assert feed.client.request_count == 1
    assert feed._admit_full_day_request('/football/matches/mt_2/odds') == 'FULL_DAY_REQUEST_ALREADY_ADMITTED'
    assert feed.client.request_count == 1 and feed.acquisition_receipt()['wait_seconds'] == 10


def test_ceiling_deadline_and_unknown_reset_refuse_new_transport_but_keep_cache():
    timer = _Clock()
    fixtures = [_fixture(number) for number in range(1, 514)]
    feed = _actual_resources_feed(fixtures, timer)
    source = _memo(feed, 1)
    feed.configure_full_day(fixtures, _authority(), [], monotonic=timer.monotonic, sleep=timer.sleep)
    assert feed.acquisition_receipt()['scope_reason'] == 'FULL_DAY_HARD_CEILING'
    assert feed.acquisition_receipt()['eligible_fixture_count'] == 513
    assert feed.acquisition_receipt()['effective_max_requests'] == 0
    assert feed._event(fixtures[0], {'h2h'})['bookmakers']
    with pytest.raises(ProviderError, match='REQUEST_BUDGET'):
        feed._event(fixtures[1], {'h2h'})
    assert feed.client.request_count == 0 and feed.acquisition_outcomes[0]['source_ref'] == source
    timer, fixtures = _Clock(), [_fixture(1), _fixture(2)]
    feed = _actual_resources_feed(fixtures, timer)
    feed.configure_full_day(fixtures, _authority(), [], monotonic=timer.monotonic, sleep=timer.sleep)
    feed.client.last_usage.minute_remaining, feed.client.last_usage.minute_reset = 1, None
    feed.client.last_response = {'observed_at': NOW.isoformat(), 'authenticated': True}
    with pytest.raises(ProviderError, match='QUOTA_PACING_UNKNOWN'):
        feed._event(fixtures[0], {'h2h'})
    timer.seconds = 1800
    with pytest.raises(ProviderError, match='FULL_DAY_ADMISSION_DEADLINE'):
        feed._event(fixtures[1], {'h2h'})
    assert feed.client.request_count == 0 and timer.sleeps == []
    assert feed.resources.usage['stopped_reason'] is None


def test_normal_factory_explicit_opt_in_default_false_and_foreign_authority_rejected(tmp_path):
    from goal_analysis.agents.profile_roster import AGENT_IDS
    from goal_analysis.jobs.ten_agent_normal import NormalAgentExecutor, make_normal_agent_executor

    default = NormalAgentExecutor(host=lambda request: {}, journal_root=tmp_path, clock=lambda: NOW)
    assert default.enable_full_day_odds is False
    settings = {'operating_contract_version': 2, 'enabled_profiles': list(AGENT_IDS),
        'openai': {'model': 'TESTONLY', 'reasoning_effort': 'low', 'max_output_tokens': 1000,
            'web_search': False}}
    actual = make_normal_agent_executor(tmp_path, settings, clock=lambda: NOW)
    assert actual.enable_full_day_odds is True and actual.enable_ticket_product_contract is True
    timer, fixtures = _Clock(), [_fixture(1)]
    feed = _actual_resources_feed(fixtures, timer)
    with pytest.raises(ValueError, match='EXACT_FULL_DAY_ODDS_AUTHORITY_REQUIRED'):
        feed.configure_full_day(fixtures, {**_authority(), 'run_id': '2026-11-13-foreign'}, [],
            monotonic=timer.monotonic, sleep=timer.sleep)
    assert feed.full_day_acquisition is None and feed.resources.usage['max_requests'] == 40
    assert feed.client.max_requests == 40 and feed.client.request_count == 0

    feed = _actual_resources_feed(fixtures, timer)
    feed.configure_full_day(fixtures, _authority(), [], monotonic=timer.monotonic, sleep=timer.sleep)
    older = {'provider': 'the_stats_api', 'observed_at': (NOW-timedelta(seconds=1)).isoformat(),
        'http_status': 200, 'usage': {'monthly_remaining': 0, 'monthly_reset': int(NOW.timestamp())+3600}}
    newer = {'provider': 'the_stats_api', 'observed_at': NOW.isoformat(), 'http_status': 200,
        'usage': {'minute_remaining': 119, 'minute_reset': int(NOW.timestamp())+60}}
    feed.client.quota_observer = SimpleNamespace(legacy_state=lambda: {**newer, '_receipts': [older, newer]})
    with pytest.raises(ProviderError, match='USAGE_LIMIT_EXCEEDED'):
        feed._event(fixtures[0], {'h2h'})
    assert feed.client.request_count == 0
    older['usage'] = {'retry_after': 30}
    with pytest.raises(ProviderError, match='PROVIDER_RETRY_AFTER'):
        feed._event(fixtures[0], {'h2h'})
    assert feed.client.request_count == 0 and timer.sleeps == []


def test_changed_shared_quota_after_wait_refuses_without_transport_or_second_wait():
    cases = [('monthly_zero', 'USAGE_LIMIT_EXCEEDED'),
        ('auth_denied', 'PROVIDER_ACCESS_REQUIRES_RECHECK'),
        ('rate_denied', 'RATE_LIMITED'), ('later_reset', 'QUOTA_PACING_DEFERRED')]
    for mode, expected in cases:
        timer, fixtures = _Clock(), [_fixture(1)]
        feed = _actual_resources_feed(fixtures, timer)
        first = {'provider': 'the_stats_api', 'observed_at': NOW.isoformat(), 'http_status': 200,
            'usage': {'monthly_remaining': 1000, 'monthly_reset': int(NOW.timestamp())+3600,
                'minute_remaining': 2, 'minute_reset': int(NOW.timestamp())+10}}
        state = {'rows': [first]}
        feed.client.quota_observer = SimpleNamespace(legacy_state=lambda state=state: {
            **state['rows'][-1], '_receipts': state['rows']})

        def changing_sleep(seconds, *, timer=timer, state=state, mode=mode):
            timer.sleep(seconds)
            fresh = {'provider': 'the_stats_api', 'observed_at': timer.clock().isoformat(),
                'http_status': 200, 'usage': {}}
            if mode == 'monthly_zero':
                fresh['usage'] = {'monthly_remaining': 0, 'monthly_reset': int(NOW.timestamp())+3600}
            elif mode == 'auth_denied':
                fresh['http_status'] = 401
            elif mode == 'rate_denied':
                fresh['http_status'] = 429
            else:
                fresh['usage'] = {'minute_remaining': 2, 'minute_reset': int(NOW.timestamp())+70}
            state['rows'].append(fresh)

        feed.configure_full_day(fixtures, _authority(), [], monotonic=timer.monotonic,
            sleep=changing_sleep)
        with pytest.raises(ProviderError, match=expected):
            feed._event(fixtures[0], {'h2h'})
        assert timer.sleeps == [10.0]
        assert feed.client.request_count == feed.resources.usage['requests'] == 0
        assert feed.client.endpoints == [] and feed._odds_admitted == set()
        assert feed.acquisition_outcomes[-1]['reason_code'] == expected
        assert feed.acquisition_outcomes[-1]['request_state'] == 'NO_NEW_TRANSPORT'
        assert feed.acquisition_receipt()['refusal_counts'][expected] == 1
