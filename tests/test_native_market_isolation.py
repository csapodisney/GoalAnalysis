"""A missing native product must not replace the fixture or report success."""
from copy import deepcopy
from urllib.parse import urlsplit

import pytest
from test_the_stats_primary import NOW, config, setup

from goal_analysis.jobs.daily_223_live import run_daily223_live
from goal_analysis.jobs.the_stats_live import collect_live
from goal_analysis.providers.base import ProviderError
from goal_analysis.providers.the_stats_odds import TheStatsOddsFeed


def native_setup(tmp_path, monkeypatch, *, btts="MISSING", no_totals=False):
    transport, client, cache, _ = setup(tmp_path)
    original = transport.__call__

    def modified(url, headers, **kwargs):
        payload, headers, status = original(url, headers, **kwargs)
        if urlsplit(url).path.endswith('/odds'):
            payload = deepcopy(payload)
            for book in payload['data']['bookmakers']:
                if btts == "MISSING":
                    book['markets'].pop('btts', None)
                else:
                    book['markets']['btts'] = btts
                if no_totals:
                    book['markets'].pop('total_goals', None)
        return payload, headers, status

    client._transport = modified

    def forbidden(*_, **__):
        raise AssertionError('RETIRED_PROVIDER_OR_WHOLE_FIXTURE_RECOVERY_FORBIDDEN')

    monkeypatch.setattr('goal_analysis.providers.api_football.ApiFootballClient.request', forbidden)
    monkeypatch.setattr('goal_analysis.providers.daily_223_odds.Daily223OddsFeed.bulk', forbidden)
    monkeypatch.setattr('goal_analysis.providers.daily_223_odds.Daily223OddsFeed.extra', forbidden)
    monkeypatch.setattr('goal_analysis.jobs.odds_recovery.recover_odds', forbidden)
    feed = TheStatsOddsFeed(tmp_path, 'eu', 20, client=client, clock=lambda: NOW)
    return transport, client, cache, feed


@pytest.mark.parametrize("btts", ["MISSING", {}, [], {"yes": {"last_seen": 2}},
    {"yes": {"last_seen": "bad"}, "no": {"last_seen": 2}}])
def test_empty_requested_market_is_unavailable_not_success(tmp_path, monkeypatch, btts):
    _, client, _, feed = native_setup(tmp_path, monkeypatch, btts=btts)
    fixtures = collect_live(config(), client, NOW.date(), lambda: NOW)['fixtures']['records']
    feed.bind_fixtures(fixtures)
    f = fixtures[0]
    with pytest.raises(ProviderError, match='requested markets unavailable'):
        feed.extra(f['sport_key'], f['provider_fixture_id'], ['btts'])


def test_missing_btts_preserves_exact_base_candidates_and_marks_partial_coverage(tmp_path, monkeypatch):
    transport, client, cache, feed = native_setup(tmp_path, monkeypatch)
    base = run_daily223_live(config(), client, feed, cache, NOW.date(), lambda: NOW,
                            construct_legacy=False)['artifacts']['candidate_input']['candidates']
    before = len(transport.calls)
    cfg = config(); cfg['extra_markets'] = ['btts']
    result = run_daily223_live(cfg, client, feed, cache, NOW.date(), lambda: NOW,
                              construct_legacy=False)['artifacts']
    assert result['candidate_input']['candidates'] == base
    assert base and all(c['quote_provider'] == 'the_stats_api' for c in base)
    assert result['odds_recovery']['api_football_calls'] == 0
    assert len(transport.calls) == before  # Native raw-cache hits, no paid retry/fallback.
    issues = result['candidate_input']['data_issues']
    assert len([i for i in issues if i.get('market') == 'btts'
                and i['status'] == 'OPTIONAL_MARKETS_UNAVAILABLE']) == 3
    partial = next(i for i in issues if i['status'] == 'OPTIONAL_MARKETS_PARTIAL_COVERAGE')
    assert partial['requested_event_markets'] == 3 and partial['retrieved_event_markets'] == 0


def test_missing_btts_does_not_discard_other_optional_market(tmp_path, monkeypatch):
    _, client, cache, feed = native_setup(tmp_path, monkeypatch)
    cfg = config(); cfg['extra_markets'] = ['btts', 'totals_h1']
    a = run_daily223_live(cfg, client, feed, cache, NOW.date(), lambda: NOW,
                         construct_legacy=False)['artifacts']
    products = {(c['market_key'], c['period']) for c in a['candidate_input']['candidates']}
    assert {('h2h', 'FULL_TIME'), ('totals_2_5', 'FULL_TIME'), ('totals_0_5', 'FIRST_HALF')} <= products
    assert not any(c['market_key'] == 'btts' for c in a['candidate_input']['candidates'])
    partial = next(i for i in a['candidate_input']['data_issues']
                   if i['status'] == 'OPTIONAL_MARKETS_PARTIAL_COVERAGE')
    assert partial['requested_event_markets'] == 6 and partial['retrieved_event_markets'] == 3


def test_multi_market_query_keeps_available_family_when_another_is_missing(tmp_path, monkeypatch):
    _, client, _, feed = native_setup(tmp_path, monkeypatch)
    fixtures = collect_live(config(), client, NOW.date(), lambda: NOW)['fixtures']['records']
    feed.bind_fixtures(fixtures); f = fixtures[0]
    event = feed.extra(f['sport_key'], f['provider_fixture_id'], ['btts', 'totals'])
    assert {m['key'] for b in event['bookmakers'] for m in b['markets']} == {'totals'}
    assert all(m['last_update'] is None for b in event['bookmakers'] for m in b['markets'])


def test_only_all_missing_requested_families_raise(tmp_path, monkeypatch):
    _, client, _, feed = native_setup(tmp_path, monkeypatch, no_totals=True)
    fixtures = collect_live(config(), client, NOW.date(), lambda: NOW)['fixtures']['records']
    feed.bind_fixtures(fixtures); f = fixtures[0]
    with pytest.raises(ProviderError, match='requested markets unavailable'):
        feed.extra(f['sport_key'], f['provider_fixture_id'], ['btts', 'totals'])
