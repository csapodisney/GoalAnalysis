from copy import deepcopy
from datetime import timedelta
from urllib.parse import urlparse

import pytest
from test_the_stats_context import KICKOFF, NOW, Transport, report, row

from goal_analysis.jobs.context_request_policy import FAIR, LEGACY, core_plan
from goal_analysis.jobs.expert_advisory import enrich_sports_supplement
from goal_analysis.jobs.the_stats_context import Resources, collect_context
from goal_analysis.providers.the_stats_api import TheStatsApiClient


def day(size=36):
    value, rows = report(), []
    value['fixtures'] = []
    for i in range(size):
        home = {'id': f'tm_{2*i+1}', 'name': f'Home {i}'}
        away = {'id': f'tm_{2*i+2}', 'name': f'Away {i}'}
        value['fixtures'].append({'fixture_id': f'api_football:{i+1}',
            'api_football_league_id': '39', 'home_team': home['name'],
            'away_team': away['name'], 'kickoff': KICKOFF})
        rows.append(row(id=f'mt_{i+1}', home_team=home, away_team=away))
    return value, rows


class FullDay(Transport):
    def __call__(self, url, headers, **kwargs):
        path = urlparse(url).path.removeprefix('/api')
        if path.endswith('/odds/players'):
            self.calls.append(url)
            return {'error': {'code': 'ADDON_REQUIRED'}}, {}, 403
        result, response_headers, status = super().__call__(url, headers, **kwargs)
        if path.endswith('/lineups'):
            identity = int(path.split('/')[3].removeprefix('mt_'))
            result['data'] = {'match_id': f'mt_{identity}', 'type': 'predicted',
                'confirmed': False,
                'home': {'id': f'tm_{2*identity-1}', 'starting_xi': [
                    {'id': f'pl_{2*identity-1}', 'name': 'Home player', 'position': 'F'}]},
                'away': {'id': f'tm_{2*identity}', 'starting_xi': [
                    {'id': f'pl_{2*identity}', 'name': 'Away player', 'position': 'F'}]}}
        return result, response_headers, status


def run(root, value, rows, policy=FAIR, cap=110, now=NOW):
    transport = FullDay(rows)
    client = TheStatsApiClient('safe-test-key', transport, max_requests=110, clock=lambda: now)
    result = collect_context(root, value, client=client, max_requests=cap,
                             clock=lambda: now, request_policy=policy)
    return result, transport


def core_available(result):
    return {fid: [fact for fact in record['facts'] if fact['status'] == 'AVAILABLE'
                  and fact['category'] in {'lineup', 'team_season_stats', 'injuries_suspensions'}]
            for fid, record in result['context_by_fixture'].items()}


def test_cold_36_fixture_control_starves_lineups_fair_policy_distributes_same_110(tmp_path):
    value, rows = day()
    before = deepcopy(value)
    control, _ = run(tmp_path / 'control', value, rows, LEGACY)
    treatment, transport = run(tmp_path / 'fair', value, rows)
    assert value == before
    assert control['usage']['requests'] == treatment['usage']['requests'] == len(transport.calls) == 110
    assert control['coverage']['with_lineups'] == 0
    assert treatment['coverage']['with_lineups'] == 36
    assert all(len(facts) == 3 for facts in core_available(treatment).values())
    assert all({f['category'] for f in facts} == {
        'lineup', 'team_season_stats', 'injuries_suspensions'}
        for facts in core_available(treatment).values())
    assert treatment['coverage']['missing_facts'] > 0  # Not enough budget for both sides/all extras.
    assert treatment['mode'] == 'SHADOW' and treatment['decision_effect'] == 'NONE'
    assert treatment['scheduling']['available_core_facts'] == 108


def test_warm_validated_core_cache_frees_budget_for_all_36_lineups_and_both_sides(tmp_path):
    value, rows = day()
    control, _ = run(tmp_path, value, rows, LEGACY)
    assert control['coverage']['with_lineups'] == 0
    treatment, transport = run(tmp_path, value, rows, now=NOW + timedelta(minutes=1))
    assert treatment['coverage']['with_lineups'] == 36
    assert all(len(facts) == 5 for facts in core_available(treatment).values())
    assert len(transport.calls) <= 110 and treatment['usage']['cache_hits'] > 100
    assert not any(urlparse(url).path.endswith('/stats') and '/teams/' in url
                   for url in transport.calls)


def test_zero_budget_still_uses_valid_cache_and_leaves_misses_explicit(tmp_path):
    value, rows = day(3)
    run(tmp_path, value, rows)
    result, transport = run(tmp_path, value, rows, cap=0, now=NOW + timedelta(minutes=1))
    assert not transport.calls and result['usage']['requests'] == 0
    assert result['coverage']['with_lineups'] == 3
    assert all(len(facts) == 5 for facts in core_available(result).values())


@pytest.mark.parametrize('cap', [0, 1, 2, 3, 6, 10, 110])
def test_never_exceeds_cap_and_missing_never_becomes_fake_fact(tmp_path, cap):
    value, rows = day(8)
    result, transport = run(tmp_path, value, rows, cap=cap)
    assert len(transport.calls) == result['usage']['requests'] <= cap
    for record in result['context_by_fixture'].values():
        for fact in record['facts']:
            if fact['reason'] == 'REQUEST_BUDGET':
                assert fact['status'] == 'UNAVAILABLE' and fact['data'] is None


def test_order_does_not_depend_on_report_input_permutation(tmp_path):
    value, rows = day(8)
    forward, transport = run(tmp_path / 'forward', value, rows, cap=20)
    value['fixtures'].reverse()
    reverse, other = run(tmp_path / 'reverse', value, rows, cap=20)
    # Separate physical HTTP responses now have different acquisition IDs even
    # with an identical synthetic clock. Verify each real local proof BEFORE
    # omitting only those two opaque IDs from the scheduling comparison.
    from contextlib import closing

    from goal_analysis.storage import Database
    from goal_analysis.storage.http_observations import read_http_observation

    def scheduling_view(value, root):
        value = deepcopy(value)
        with closing(Database(root/'data/goal_analysis.sqlite3').connect()) as db:
            def clean(item):
                if isinstance(item, dict):
                    if item.get('http_observation_id'):
                        assert item['http_observation_status'] == 'VERIFIED'
                        read_http_observation(db, root, item['http_observation_id'], NOW, source=item)
                        item.pop('http_observation_id')
                        item.pop('http_receipt_id')
                    for child in item.values():
                        clean(child)
                elif isinstance(item, list):
                    for child in item:
                        clean(child)
            clean(value)
        return value
    assert scheduling_view(forward,tmp_path/'forward') == scheduling_view(reverse,tmp_path/'reverse')
    assert transport.calls == other.calls


def test_plan_round_robin_lineup_first_and_no_fixture_drains_other_fixtures():
    matched = [{'fixture_id': str(i), 'kickoff': KICKOFF, 'mapping': {
        'match_id': f'mt_{i}', 'home_team_id': f'tm_{i*2}',
        'away_team_id': f'tm_{i*2+1}'}} for i in range(1, 5)]
    plan = core_plan(matched)
    assert len(plan) == 20
    assert all(category == 'lineup' for _, category, _ in plan[:4])
    for start in range(0, 20, 4):
        assert len({r['fixture_id'] for r, _, _ in plan[start:start+4]}) == 4


def test_cache_only_miss_makes_no_http_and_does_not_poison_later_fetch(tmp_path):
    transport = FullDay()
    client = TheStatsApiClient('safe-test-key', transport, clock=lambda: NOW)
    resource = Resources(tmp_path, client, lambda: NOW, 1)
    endpoint = '/football/competitions/comp_3039'
    assert resource.get(endpoint, cache_only=True) == (None, None, 'CACHE_MISS')
    assert transport.calls == []
    assert resource.get(endpoint)[2] is None
    assert len(transport.calls) == 1


@pytest.mark.parametrize('status,code', [(401, 'UNAUTHORIZED'), (403, 'FORBIDDEN'), (429, 'RATE_LIMITED')])
def test_auth_or_quota_failure_no_retries_even_in_cache_prepass(tmp_path, status, code):
    value, _ = day(8)
    transport = Transport(failure=(status, code))
    client = TheStatsApiClient('safe-test-key', transport, clock=lambda: NOW)
    result = collect_context(tmp_path, value, client=client, clock=lambda: NOW,
                             request_policy=FAIR)
    assert len(transport.calls) == 1 and result['usage']['stopped_reason'] == code


def test_strict_lockout_never_fetches_even_cached_facts(tmp_path):
    value, rows = day(3)
    run(tmp_path, value, rows)
    now = NOW.replace(hour=15, minute=15)
    result, transport = run(tmp_path, value, rows, now=now)
    assert not transport.calls and result['coverage']['locked'] == 3
    assert result['coverage']['available_facts'] == 0


def test_lockout_crossed_during_lineup_response_stops_all_further_network(tmp_path):
    value, rows = day(3)
    current = [NOW]
    class Late(FullDay):
        def __call__(self, url, headers, **kwargs):
            result = super().__call__(url, headers, **kwargs)
            if urlparse(url).path.endswith('/lineups'):
                current[0] = NOW.replace(hour=15, minute=15)
            return result
    transport = Late(rows)
    client = TheStatsApiClient('safe-test-key', transport, clock=lambda: current[0])
    result = collect_context(tmp_path, value, client=client, clock=lambda: current[0],
                             request_policy=FAIR)
    assert len(transport.calls) == 3  # Mapping plus exactly one late response.
    assert result['coverage']['locked'] == 3
    assert result['coverage']['with_lineups'] == 0


def test_cache_prepass_never_trusts_future_created_metadata_at_zero_budget(tmp_path):
    transport = FullDay()
    client = TheStatsApiClient('safe-test-key', transport, clock=lambda: NOW)
    resource = Resources(tmp_path, client, lambda: NOW, 1)
    endpoint = '/football/competitions/comp_3039'
    assert resource.get(endpoint)[2] is None
    with resource.database.transaction() as db:
        db.execute('UPDATE cache_entries SET created_at=?',
                   ((NOW + timedelta(minutes=1)).isoformat(),))
    zero = Resources(tmp_path, client, lambda: NOW, 0)
    assert zero.get(endpoint, cache_only=True) == (None, None, 'CACHE_MISS')
    assert zero.get(endpoint) == (None, None, 'REQUEST_BUDGET')
    assert len(transport.calls) == 1 and zero.usage['cache_hits'] == 0


def test_shadow_wrapper_preserves_live_supplement_cards_and_no_double_collection(tmp_path):
    value, rows = day(3)
    value.update(tickets=[{'unchanged': 'opaque'}], numeric_models={'unchanged': True},
                 sports_supplement={'the_stats_api': {'old': 'keep'}})
    original = deepcopy(value)
    transport = FullDay(rows)
    client = TheStatsApiClient('safe-test-key', transport, clock=lambda: NOW)
    result = enrich_sports_supplement(tmp_path, value, client=client,
        clock=lambda: NOW, context_shadow=True, max_requests=6)
    assert value == original
    sidecar = result.pop('context_collection_shadow')
    assert result == original
    assert sidecar['mode'] == 'SHADOW' and sidecar['live_promotion'] is False
    assert sidecar['collection']['request_policy'] == FAIR
    assert len(transport.calls) == sidecar['collection']['usage']['requests'] == 6


@pytest.mark.parametrize('policy', [True, None, 'unknown'])
def test_invalid_policy_fails_before_any_call(tmp_path, policy):
    with pytest.raises(ValueError):
        collect_context(tmp_path, report(), request_policy=policy)


@pytest.mark.parametrize('value', [None, 1, 'true'])
def test_shadow_switch_strict_boolean(tmp_path, value):
    with pytest.raises(ValueError):
        enrich_sports_supplement(tmp_path, report(), context_shadow=value)
