"""Real native schemas through selection boundaries, without external requests."""
import hashlib
import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from test_daily_223_live import NOW, config, daily_rows, events, history_rows

from goal_analysis.agents import canonical_sha256
from goal_analysis.features.daily_223_history import enrich_daily223_input, validate_history
from goal_analysis.jobs.daily_223_live import run_daily223_live
from goal_analysis.jobs.the_stats_context import Resources
from goal_analysis.jobs.the_stats_live import collect_live, normalize_history
from goal_analysis.jobs.the_stats_results import resolve_result
from goal_analysis.normalization.live_identity import field_id
from goal_analysis.providers.api_football import ApiFootballClient
from goal_analysis.providers.base import ProviderError
from goal_analysis.providers.daily_223_odds import Daily223OddsFeed
from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
from goal_analysis.storage import CacheStore, Database


def native(row):
    row = deepcopy(row)
    for side in ('home', 'away'):
        row['teams'][side].setdefault('name', 'Team ' + str(row['teams'][side]['id']))
    return {'id': 'mt_' + str(row['fixture']['id']), 'competition_id': 'comp_4643',
            'season_id': 'sn_27', 'status': 'scheduled' if row['fixture']['status']['short'] == 'NS' else 'finished',
            'utc_date': row['fixture']['date'],
            'home_team': {'id': 'tm_' + str(row['teams']['home']['id']), 'name': row['teams']['home']['name']},
            'away_team': {'id': 'tm_' + str(row['teams']['away']['id']), 'name': row['teams']['away']['name']},
            'score': {'regulation': row['score']['fulltime'], 'half_time_home': row['score']['halftime']['home'],
                      'half_time_away': row['score']['halftime']['away']}, 'is_neutral': False}


class Transport:
    def __init__(self):
        self.calls = []
        self.calendar = [native(row) for row in daily_rows()]
        self.history = [native(row) for row in history_rows()]
        self.addon = False
        self.bad_pagination = False

    def __call__(self, url, headers, **kwargs):
        assert url.startswith('https://api.thestatsapi.com/api/')
        self.calls.append(url)
        path, query = urlsplit(url).path.removeprefix('/api'), parse_qs(urlsplit(url).query)
        if path.endswith('/odds/players'):
            return {'error': {'code': 'ADDON_REQUIRED'}}, {}, 403
        if path == '/football/matches':
            rows = self.history if query.get('status') == ['finished'] else self.calendar
            return {'data': deepcopy(rows), 'meta': {'page': 2 if self.bad_pagination else 1,
                    'per_page': 100, 'total_pages': 1, 'total': len(rows)}}, {}, 200
        if path.endswith('/odds'):
            match = path.split('/')[3]
            index = int(match.removeprefix('mt_')) - 9000
            price = (2, 2, 3)[index]
            return {'data': {'match_id': match, 'bookmakers': [{'bookmaker': 'Bet365', 'markets': {
                'match_odds': {'home': {'last_seen': price}, 'away': {'last_seen': 1.5}, 'draw': {'last_seen': 3.2}},
                'total_goals': {'2.5': {'over': {'last_seen': 2.0}, 'under': {'last_seen': 1.5}},
                                '3.5': {'over': {'last_seen': 3.0}, 'under': {'last_seen': 1.2}}},
                'btts': {'yes': {'last_seen': 2.1}, 'no': {'last_seen': 1.6}},
                'first_half_total_goals': {'0.5': {'over': {'last_seen': 1.4}, 'under': {'last_seen': 2.4}}},
            }}]}}, {}, 200
        if path.startswith('/football/matches/mt_'):
            row = next((row for row in self.history + self.calendar if path.endswith(row['id'])), None)
            return {'data': row}, {}, 200
        return {'data': {}}, {}, 200


def setup(tmp_path):
    transport = Transport()
    client = TheStatsPrimaryClient(tmp_path, api_key='test-only', transport=transport, clock=lambda: NOW, max_requests=30)
    db = Database(tmp_path / 'data/goal_analysis.sqlite3')
    db.initialize()
    cache = CacheStore(db)

    def prices(url, headers):
        return events(), {'x-requests-last': '2', 'x-requests-remaining': '100'}, json.dumps(events()).encode()

    odds = Daily223OddsFeed('test-odds', 'eu', 20, transport=prices)
    return transport, client, cache, odds


def test_native_collection_reaches_candidates_history_and_daily_engine(tmp_path):
    transport, client, cache, odds = setup(tmp_path)
    bundle = run_daily223_live(config(), client, odds, cache, NOW.date(), lambda: NOW)
    a = bundle['artifacts']
    assert a['report']['construction_status'] == 'COMPLETE', a['report']
    assert a['report']['combined_price'] >= 12
    assert len(a['report']['legs']) == 3
    assert len({leg['fixture_id'] for leg in a['report']['legs']}) == 3
    assert all(row['fixture_id'].startswith('the_stats_api:mt_') for row in a['candidate_input']['candidates'])
    assert all(not key.startswith('api_football_') for row in a['fixtures']['records'] for key in row)
    enriched = enrich_daily223_input(a['candidate_input'], a['history'])
    assert all(row['status'] != 'DATA_REQUIRED' for row in enriched['history_analysis']['candidates'])
    assert len(validate_history(a['history'])) == len(transport.history)
    assert a['report']['usage']['api_football_calls'] == 0


def test_cache_reuses_verified_raw_without_provider_calls(tmp_path):
    transport, client, _, _ = setup(tmp_path)
    first = collect_live(config(), client, NOW.date(), lambda: NOW)
    count = len(transport.calls)
    second = collect_live(config(), client, NOW.date(), lambda: NOW)
    assert len(transport.calls) == count
    assert second['sports_primary_usage']['requests'] == 0
    assert second['sports_primary_usage']['cache_hits'] == 2
    assert first['history']['records'] == second['history']['records']


def test_invalid_pagination_blocks_collection_without_silent_truncation(tmp_path):
    transport, client, _, _ = setup(tmp_path)
    transport.bad_pagination = True
    with pytest.raises(ProviderError, match='pagination'):
        collect_live(config(), client, NOW.date(), lambda: NOW)


def test_unknown_config_mapping_is_explicit_and_never_fake_api_id(tmp_path):
    _, client, _, _ = setup(tmp_path)
    cfg = config()
    cfg['leagues'][0]['api_football_id'] = 32
    result = collect_live(cfg, client, NOW.date(), lambda: NOW)
    assert not result['fixtures']['records']
    assert result['history']['data_issues'][0]['status'] == 'NO_VERIFIED_THE_STATS_COMPETITION'


def test_provider_history_rejects_cross_provider_ids(tmp_path):
    _, client, _, _ = setup(tmp_path)
    result = collect_live(config(), client, NOW.date(), lambda: NOW)
    history = result['history']
    history['records'][0]['home_team_id'] = '1'
    history.pop('snapshot_sha256')
    history['snapshot_sha256'] = canonical_sha256(history)
    with pytest.raises(ValueError, match='native TheStats'):
        validate_history(history)


@pytest.mark.parametrize('field,value', [('provider_fixture_id', 'mt_999'), ('provider_home_team_id', '1')])
def test_inconsistent_native_id_excluded(field, value, tmp_path):
    from goal_analysis.tipsters.recommendations import _fixtures

    _, client, _, _ = setup(tmp_path)
    fixture = collect_live(config(), client, NOW.date(), lambda: NOW)['fixtures']['records'][0]
    if field == 'provider_fixture_id':
        fixture[field] = value
        assert not _fixtures([fixture], NOW.date().isoformat(), NOW)
    else:
        fixture[field] = value
        with pytest.raises(ValueError):
            field_id(fixture, 'home_team')


def test_regulation_and_half_time_never_replaced_by_final_or_shootout():
    row = native(history_rows()[0])
    row['score'] = {'regulation': None, 'home': 8, 'away': 7,
                    'after_extra_time': {'home': 3, 'away': 2}, 'half_time_home': 1, 'half_time_away': 0}
    source = {'observed_at': NOW.isoformat(), 'source_url': 'https://api.thestatsapi.com/api/football/matches'}
    record = normalize_history(row, 'comp_4643', source, NOW)
    assert record['full_time_home'] is None and record['full_time_away'] is None
    assert record['first_half_home'] == 1


def test_addon_denial_is_one_per_capability_and_does_not_stop_basic_facts(tmp_path):
    transport, client, _, _ = setup(tmp_path)
    resources = Resources(tmp_path, client, lambda: NOW, 10)
    assert resources.get('/v2/football/matches/mt_1/odds/players')[2] == 'ADDON_REQUIRED'
    assert resources.get('/v2/football/matches/mt_2/odds/players')[2] == 'ADDON_REQUIRED'
    assert resources.get('/football/teams/tm_1')[2] is None
    assert len(transport.calls) == 2
    assert resources.usage['stopped_reason'] is None


def test_native_numeric_models_boundary_keeps_actual_team_ids(tmp_path):
    from goal_analysis.models._data import normalize_fixtures, prepare_history

    _, client, _, _ = setup(tmp_path)
    result = collect_live(config(), client, NOW.date(), lambda: NOW)
    fixtures, issues = normalize_fixtures(result['fixtures']['records'], NOW)
    assert len(fixtures) == 3 and not issues
    history, _ = prepare_history(result['history']['records'], NOW, set())
    assert history and all(row['home_team_id'].startswith('tm_') for row in history)


def test_native_lockout_applies_at_fifteen_minutes(tmp_path):
    transport, client, cache, odds = setup(tmp_path)
    for row in transport.calendar:
        row['utc_date'] = (NOW + timedelta(minutes=15)).isoformat()
    report = run_daily223_live(config(), client, odds, cache, NOW.date(), lambda: NOW)['artifacts']['report']
    assert not report['legs']
    assert report['usage']['odds_calls'] == 0


def test_legacy_client_cannot_make_real_http_calls(monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: pytest.fail('Retired provider requested'))
    with pytest.raises(ProviderError, match='retired'):
        ApiFootballClient(api_key='old-key').request('fixtures', {})


def test_native_result_retains_source_and_does_not_change_legacy_identity(tmp_path):
    from goal_analysis.storage.portfolio_ledger import _leg_result

    transport, client, _, _ = setup(tmp_path)
    row = transport.history[0]
    leg = {'fixture_id': 'the_stats_api:' + row['id'], 'home_team': row['home_team']['name'],
           'away_team': row['away_team']['name'], 'kickoff': row['utc_date'],
           'market_key': 'totals_2_5', 'selection_key': 'over', 'period': 'FULL_TIME'}
    result = resolve_result(client, leg)
    assert result['provider'] == 'the_stats_api'
    assert result['provider_match_id'] == row['id']
    assert _leg_result(leg, result)['outcome'] in {'WON', 'LOST'}


def test_daily_math_engine_is_still_original_bytes():
    root = Path(__file__).resolve().parents[1]
    assert hashlib.sha256((root / 'src/goal_analysis/engine/daily_223.py').read_bytes()).hexdigest() == 'd7a25d3d3ba0035918d8760935f3df49d9dfbbb31c7173af285d7e25d2c5100c'


def test_native_portfolio_reaches_ready_without_legacy_provider(tmp_path):
    from test_portfolio_flow import RecordedReviewer

    from goal_analysis.jobs.portfolio import run_portfolio

    _, client, cache, odds = setup(tmp_path)
    review = RecordedReviewer()
    report = run_portfolio(config(), {}, client, odds, cache, NOW.date(), review, clock=lambda: NOW)['artifacts']['report']
    daily = next(card for card in report['tickets'] if card['profile_id'] == 'daily223')
    assert daily['status'] == 'READY'
    assert daily['combined_price'] >= 12
    assert report['decision_data_usage']['sources']['the_stats_match_history']['status'] == 'USED_DIRECT'


def test_bad_odds_auth_is_single_attempt_no_llm_and_persistable(tmp_path):
    from goal_analysis.jobs.portfolio import run_portfolio, write_portfolio_bundle
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger

    _, client, cache, _ = setup(tmp_path)
    calls = []

    def denied(*args):
        calls.append(1)
        raise ProviderError('The Odds API HTTP 401')

    class ForbiddenReviewer:
        def review(self, *args, **kwargs):
            pytest.fail('Authentication failure must not spend LLM tokens')

    odds = Daily223OddsFeed('test', 'eu', 20, transport=denied)
    bundle = run_portfolio(config(), {}, client, odds, cache, NOW.date(), ForbiddenReviewer(), clock=lambda: NOW)
    report = bundle['artifacts']['report']
    assert report['construction_status'] == 'DATA_BLOCKED' and not report['tickets']
    assert report['usage']['llm_calls'] == 0 and len(calls) == 1
    write_portfolio_bundle(tmp_path / 'acceptance-report', bundle)
    ledger = PortfolioLedger(tmp_path / 'ledger.sqlite3', now=lambda: NOW)
    ledger.save_run(report)
    assert ledger.list_runs()[0]['run_id'] == report['run_id']


def test_venue_is_actual_match_detail_and_wrong_identity_is_not_used(tmp_path):
    from goal_analysis.jobs.the_stats_venue import resolve_venue

    transport, client, _, _ = setup(tmp_path)
    fixture = collect_live(config(), client, NOW.date(), lambda: NOW)['fixtures']['records'][0]
    transport.calendar[0]['venue'] = {'name': 'Actual neutral stadium', 'city': 'Berlin'}
    resources = Resources(tmp_path, client, lambda: NOW, 3)
    resolved, proof = resolve_venue(resources, fixture)
    assert proof['status'] == 'VERIFIED' and resolved['venue_city'] == 'Berlin'
    fixture['provider_home_team_id'] = 'tm_999'
    resolved, proof = resolve_venue(resources, fixture)
    assert proof['status'] == 'IDENTITY_OR_STATUS_MISMATCH'
    assert resolved['venue_city'] is None


def test_native_standings_conflict_is_explicit_not_first_row_choice(tmp_path):
    from goal_analysis.agents.the_stats_standings import collect

    transport, client, _, _ = setup(tmp_path)
    fixture = collect_live(config(), client, NOW.date(), lambda: NOW)['fixtures']['records'][0]
    base = transport.__call__

    def table(url, headers, **kwargs):
        if urlsplit(url).path.endswith('/standings'):
            row = {'team': {'id': fixture['provider_home_team_id'], 'name': fixture['home_team']},
                   'position': 1, 'matches_played': 2, 'goal_difference': 4, 'points': 6}
            return {'data': [row, {**row, 'points': 0}]}, {}, 200
        return base(url, headers, **kwargs)

    client._transport = table
    result = collect([fixture], client, as_of=NOW, max_requests=1, clock=lambda: NOW)
    assert not result['context_by_fixture']
    assert result['errors'][0]['code'] == 'AMBIGUOUS_PROVIDER_STANDINGS'


def test_the_stats_odds_make_real_daily_and_portfolio_with_null_provider_time(tmp_path):
    from test_portfolio_flow import RecordedReviewer

    from goal_analysis.jobs.portfolio import run_portfolio
    from goal_analysis.providers.the_stats_odds import TheStatsOddsFeed, valid_quote_source

    transport, client, cache, _ = setup(tmp_path)
    feed = TheStatsOddsFeed(tmp_path, 'eu', 20, client=client, clock=lambda: NOW)
    bundle = run_daily223_live(config(), client, feed, cache, NOW.date(), lambda: NOW)
    a = bundle['artifacts']
    assert a['report']['construction_status'] == 'COMPLETE'
    assert a['report']['usage']['odds_api_calls'] == 0
    assert feed.calls == 3
    assert all(leg['quoted_at'] is None and valid_quote_source(leg) for leg in a['report']['legs'])
    assert {'totals_2_5', 'totals_3_5'} <= {c['market_key'] for c in a['candidate_input']['candidates']}
    assert all(c['quote_timestamp_status'] == 'MISSING' for c in a['candidate_input']['candidates'])
    report = run_portfolio(config(), {}, client, feed, cache, NOW.date(), RecordedReviewer(), clock=lambda: NOW)['artifacts']['report']
    assert any(card['profile_id'] == 'daily223' for card in report['tickets'])
    assert all('the-odds-api.com' not in url for url in transport.calls)


def test_native_observation_history_never_fabricates_quoted_at(tmp_path):
    from goal_analysis.engine.odds_movement import annotate_odds_movement, quote_snapshot
    from goal_analysis.providers.the_stats_odds import TheStatsOddsFeed

    _, client, cache, _ = setup(tmp_path)
    feed = TheStatsOddsFeed(tmp_path, 'eu', 20, client=client, clock=lambda: NOW)
    candidates = run_daily223_live(config(), client, feed, cache, NOW.date(), lambda: NOW)['artifacts']['candidate_input']['candidates']
    current = next(c for c in candidates if c['decimal_price'] > 1.3)
    old = {**current, 'quote_observed_at': (NOW - timedelta(hours=30)).isoformat(), 'decimal_price': current['decimal_price'] / 1.2}
    history = [{'run_id': 'earlier-observation', 'observed_at': old['quote_observed_at'],
                'odds_history_snapshot': quote_snapshot([old], NOW - timedelta(hours=30))}]
    result = annotate_odds_movement({'candidates': [current]}, history, NOW)['candidates'][0]
    movement = result['odds_movement']
    assert movement['status'] == 'VERIFIED_RISE'
    assert movement['time_basis'] == 'LOCAL_SNAPSHOT_OBSERVATIONS'
    assert 'baseline_quoted_at' not in movement
    assert result['quoted_at'] is None


def test_native_odds_monitor_saves_observations_without_old_provider(tmp_path):
    from goal_analysis.jobs.odds_monitor_providers import build_monitor_queries, fetch_monitor_query

    _, client, _, _ = setup(tmp_path)
    cfg = config()
    result = collect_live(cfg, client, NOW.date(), lambda: NOW)
    queries = build_monitor_queries(result['fixtures']['records'], NOW,
                                    {**cfg, 'monitor_providers': ['the_stats_api']})
    assert len(queries) == 3 and all(q['provider'] == 'the_stats_api' for q in queries)
    fetched = fetch_monitor_query(queries[0], football_client=client, now=NOW)
    assert fetched['quotes'] and all(q['quoted_at'] is None and q['quote_observed_at'] == NOW.isoformat() for q in fetched['quotes'])


def test_native_odds_cache_preserves_receipt_and_fetch_rechecks_lockout(tmp_path):
    from goal_analysis.providers.the_stats_odds import TheStatsOddsFeed

    transport, client, cache, _ = setup(tmp_path)
    clock = [NOW]
    feed = TheStatsOddsFeed(tmp_path, 'eu', 20, client=client, clock=lambda: clock[0])
    candidates = run_daily223_live(config(), client, feed, cache, NOW.date(), lambda: NOW)['artifacts']['candidate_input']['candidates']
    quote = next(c for c in candidates if c['decimal_price'] > 1.3)
    fixture = feed.fixtures[quote['provider_fixture_id']] if 'provider_fixture_id' in quote else next(
        f for f in feed.fixtures.values() if f['fixture_id'] == quote['fixture_id'])
    before = len(transport.calls)
    clock[0] += timedelta(minutes=1)
    event = feed.extra(fixture['sport_key'], fixture['provider_fixture_id'], ['totals'])
    assert len(transport.calls) == before
    assert event['bookmakers'][0]['markets'][0]['quote_observed_at'] == NOW.isoformat()
    from datetime import datetime
    clock[0] = datetime.fromisoformat(fixture['kickoff']) - timedelta(minutes=15)
    with pytest.raises(ProviderError, match='lockout'):
        feed.extra(fixture['sport_key'], fixture['provider_fixture_id'], ['totals'])
    assert len(transport.calls) == before


def test_native_grok_batch_preserves_real_quote_source_without_fabricated_time(tmp_path):
    from test_grok_advisory import Advisor, configure, recommendation, report

    from goal_analysis.agents.grok_research import _packet
    from goal_analysis.jobs.grok_advisory import analyze
    from goal_analysis.providers.the_stats_odds import QUOTE_PROVENANCE, TheStatsOddsFeed

    _, client, cache, _ = setup(tmp_path)
    feed = TheStatsOddsFeed(tmp_path, 'eu', 20, client=client, clock=lambda: NOW)
    candidate = run_daily223_live(config(), client, feed, cache, NOW.date(), lambda: NOW)['artifacts']['candidate_input']['candidates'][0]
    configure(tmp_path)
    advisor = Advisor()
    supplied = report(recommendation(legs=[candidate]), date=NOW.date().isoformat(), observed_at=NOW.isoformat())
    analyze(tmp_path, supplied, advisor=advisor, now=NOW)
    assert len(advisor.packets) == 1
    packet = _packet(advisor.packets[0])[0]
    assert len(packet['quote_sources']) == 1
    source = packet['quote_sources'][0]
    assert source.get('quoted_at') is None
    assert all(source[k] == candidate[k] for k in QUOTE_PROVENANCE)
    assert packet['selections'][0]['quote_ref'] == source['quote_ref']


def test_retired_odds_default_transport_cannot_make_live_request():
    with pytest.raises(ProviderError, match='live access retired'):
        Daily223OddsFeed('not-a-secret', 'eu', 20).bulk('soccer_test', NOW.date())
