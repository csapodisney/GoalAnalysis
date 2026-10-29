"""TESTONLY native-shaped rows: no real provider, DB, model or live import probe."""

import socket
import sqlite3
import subprocess
import urllib.request
from copy import deepcopy
from datetime import date

import pytest

from goal_analysis.jobs.league_scope import resolve_full_day_scope

DAY = date(2026, 10, 28)


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('NO_PROVIDER_DB_NETWORK_OR_PROCESS_IN_SCOPE_UNIT_TEST')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def configured():
    return [{'competition_id': 'TEST_BUNDESLIGA', 'api_football_id': 78,
             'season': 2026, 'history_seasons': [2025, 2026],
             'odds_sport_key': 'soccer_test_bundesliga', 'competition_type': 'LEAGUE'}]


def match(identifier, competition_id='comp_4643', **overrides):
    return {'id': f'mt_{identifier}', 'competition_id': competition_id, 'season_id': 'sn_2026',
            'utc_date': '2026-10-28T20:00:00+00:00', 'status': 'scheduled',
            'home_team': {'id': f'tm_{identifier * 2}', 'name': f'TEST Home {identifier}'},
            'away_team': {'id': f'tm_{identifier * 2 + 1}', 'name': f'TEST Away {identifier}'},
            **overrides}


def pages(*sizes, total=None):
    return [{'page': index, 'per_page': 100,
             'total_pages': len(sizes) if total is None else total, 'returned_rows': size,
             'source_id': f'https://test.invalid/football/matches?page={index}',
             'content_hash': str(index) * 64}
            for index, size in enumerate(sizes, 1)]


def scope(rows, **kwargs):
    return resolve_full_day_scope(rows, DAY, configured(), operating_contract_version=2,
                                 **kwargs)


def test_paginated_scope_includes_extra_competitions_above_old_15_threshold():
    rows = [match(index) for index in range(1, 101)]
    rows += [match(index, 'comp_451101') for index in range(101, 111)]
    rows += [match(index, 'comp_29967') for index in range(111, 121)]
    original = deepcopy(rows)
    leagues, receipt = scope(rows, page_receipts=pages(100, 20))
    assert {league['api_football_id'] for league in leagues} == {78, 138, 10}
    assert receipt['status'] == receipt['provider_calendar_status'] == 'COMPLETE'
    assert receipt['full_product_run'] is True
    assert len(receipt['provider_fixture_ids']) == len(receipt['routed_fixture_ids']) == 120
    assert receipt['events'][100]['source_id'].endswith('page=2')
    assert receipt['events'][100]['source_content_hash'] == '2' * 64
    assert rows == original


def test_45_event_universe_does_not_filter_missing_price_model_stats_or_lockout():
    rows = [match(index, stats=None, model=None, odds=None) for index in range(1, 46)]
    rows[0]['utc_date'] = '2026-10-28T00:01:00+00:00'
    rows[1]['status'] = 'finished'
    _, receipt = scope(rows, page_receipts=pages(45))
    assert len(receipt['events']) == len(receipt['provider_fixture_ids']) == 45
    assert all(event['status'] == 'ROUTED' for event in receipt['events'])
    assert receipt['events'][1]['provider_status'] == 'finished'
    assert receipt['full_product_run'] is True


def test_missing_mapping_preserves_named_native_event_and_routes_source_bound_id():
    rows = [match(1), match(2, 'comp_999999',
                           competition={'id': 'comp_999999', 'name': 'TEST Unmapped Cup'})]
    leagues, receipt = scope(rows, page_receipts=pages(2))
    assert [league['api_football_id'] for league in leagues] == [78]
    assert receipt['provider_fixture_ids'] == ['the_stats_api:mt_1', 'the_stats_api:mt_2']
    assert receipt['routed_fixture_ids'] == receipt['provider_fixture_ids']
    event = receipt['events'][1]
    assert event['provider_league_id'] == 'comp_999999'
    assert event['provider_season_id'] == 'sn_2026'
    assert event['status'] == 'ROUTED'
    assert event['source_id'].endswith('page=1')
    route, = receipt['native_competition_routes']
    assert route['provider_league_id'] == 'comp_999999'
    assert route['provider_season_ids'] == ['sn_2026']
    assert route['fixture_ids'] == ['the_stats_api:mt_2']
    assert route['routing_key'] == 'soccer_the_stats_comp_999999'
    assert route['competition_name'] == 'TEST Unmapped Cup'
    assert route['source_receipts'] == [{'source_id': pages(2)[0]['source_id'], 'content_hash': '1' * 64}]
    assert 'api_football_id' not in route
    assert receipt['uncovered_competitions'] == []
    assert receipt['provider_calendar_status'] == 'COMPLETE'
    assert receipt['routing_status'] == receipt['status'] == 'COMPLETE'
    assert receipt['full_product_run'] is True
    assert receipt['unobserved_events'] == 'UNKNOWN'


def test_unmapped_competition_name_remains_unknown_with_native_source_route():
    leagues, receipt = scope([match(1, 'comp_999999')], page_receipts=pages(1))
    assert leagues == []
    assert receipt['events'][0]['competition_name'] is None
    assert receipt['events'][0]['name_status'] == 'UNKNOWN'
    assert receipt['events'][0]['status'] == 'ROUTED'
    route, = receipt['native_competition_routes']
    assert route['competition_name'] is None
    assert route['metadata_status'] == 'UNKNOWN'
    assert route['provider_league_id'] == 'comp_999999'
    assert route['provider_season_ids'] == ['sn_2026']
    assert receipt['uncovered_competitions'] == []


def test_manual_research_is_labelled_and_preserves_excluded_event_inventory():
    rows = [match(1), match(2, 'comp_451101')]
    leagues, receipt = scope(rows, selected_competition_ids=[78], page_receipts=pages(2))
    assert leagues == configured()
    assert receipt['scope'] == 'MANUALLY_RESTRICTED_RESEARCH'
    assert receipt['status'] == 'MANUAL_RESEARCH'
    assert receipt['full_product_run'] is False
    assert len(receipt['provider_fixture_ids']) == 2
    assert receipt['events'][1]['status'] == 'MANUAL_RESEARCH_EXCLUDED'


@pytest.mark.parametrize('selected', [[], [78, 78], [True], '78'])
def test_invalid_manual_scope_cannot_be_relabelled_full_day(selected):
    with pytest.raises(ValueError, match='distinct configured competitions'):
        scope([match(1)], selected_competition_ids=selected, page_receipts=pages(1))


def test_contract_v1_returns_unchanged_detached_config_for_existing_legacy_branch():
    config = configured()
    leagues, receipt = resolve_full_day_scope([match(1, 'comp_451101')], DAY, config)
    assert leagues == config
    assert receipt['status'] == 'LEGACY_RESTRICTED'
    assert receipt['full_product_run'] is False
    leagues[0]['history_seasons'].append(2000)
    assert config == configured()


@pytest.mark.parametrize('page_receipts,calendar_status', [
    ([], 'UNKNOWN'),
    (pages(1, total=2), 'PARTIAL'),
    ([pages(1, 1)[1]], 'PARTIAL'),
    (pages(1, 1), 'PARTIAL'),
])
def test_missing_or_inconsistent_page_proof_never_claims_complete(page_receipts, calendar_status):
    _, receipt = scope([match(1)], page_receipts=page_receipts)
    assert receipt['provider_calendar_status'] == calendar_status
    assert receipt['full_product_run'] is False
    assert receipt['status'] == 'PARTIAL'
    assert receipt['page_issues']


def test_moving_pagination_snapshot_cannot_pass_complete_check():
    proof = pages(1, 1)
    proof[1]['total_pages'] = 3
    _, receipt = scope([match(1), match(2)], page_receipts=proof)
    assert receipt['provider_calendar_status'] == 'PARTIAL'
    assert 'UNSTABLE_OR_INVALID_PAGE_TOTAL' in receipt['page_issues']


def test_duplicate_bad_identity_and_wrong_day_are_accounted_by_event_id():
    duplicate = match(1)
    rows = [duplicate, deepcopy(duplicate), match(2, utc_date='2026-10-29T20:00:00Z'), match(3)]
    rows[-1]['away_team']['id'] = rows[-1]['home_team']['id']
    _, receipt = scope(rows, page_receipts=pages(4))
    assert [event['status'] for event in receipt['events']] == ['DUPLICATE_CALENDAR_FIXTURE'] * 2
    assert {(event['fixture_id'], event['status']) for event in receipt['rejected_rows']} == {
        ('the_stats_api:mt_2', 'OUTSIDE_TARGET_DAY'),
        ('the_stats_api:mt_3', 'INVALID_CALENDAR_FIXTURE')}
    assert receipt['routed_fixture_ids'] == []
    assert receipt['status'] == 'PARTIAL'


def test_complete_empty_provider_day_does_not_invent_matches():
    leagues, receipt = scope([], page_receipts=pages(0, total=0))
    assert leagues == []
    assert receipt['status'] == 'COMPLETE'
    assert receipt['provider_fixture_ids'] == receipt['events'] == []
