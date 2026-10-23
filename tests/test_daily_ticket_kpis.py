"""Day and odds arithmetic cannot invent winnings, odds or independent samples."""
import json
import shutil
import subprocess
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pytest
from test_paper_performance import record
from test_portfolio_ledger import NOW, Football, fixture, report, ticket

from goal_analysis.jobs.portfolio import _compact_feedback
from goal_analysis.storage import daily_ticket_kpis as module
from goal_analysis.storage.daily_ticket_kpis import build_daily_ticket_kpis, safe_daily_ticket_kpis
from goal_analysis.storage.leg_error_analysis import build_leg_error_analysis
from goal_analysis.storage.paper_performance import build_paper_performance
from goal_analysis.storage.portfolio_ledger import PortfolioLedger


def indicative(identifier="a", profile="p1", odds=12.0, outcomes=None, **changes):
    return record(identifier,profile,combined_price=odds,outcomes=outcomes,**changes)


def test_daily_winning_odds_are_summed_not_multiplied_or_net_profit_and_inputs_unchanged():
    rows=[indicative(odds=12),indicative('b','p2',odds=18),
          indicative('c','p3',odds=40,outcomes=['WON','LOST'])]
    for original_leg,settled_leg in zip(rows[2]['legs'],rows[2]['settlement']['legs'],strict=True):
        original_leg['fixture_id']=settled_leg['fixture_id']=original_leg['fixture_id']+'-other'
    original=deepcopy(rows)
    result=build_daily_ticket_kpis(rows,mode='recommendations')
    day=result['days'][0]
    assert day['confirmed_winning_tickets']==2
    assert day['winning_combined_odds_sum']==30  # not 216, not 27 EUR net
    assert day['confirmed_losing_tickets']==1 and day['day_results_complete']
    assert result['decision_effect']=='NONE' and not result['cohorts_additive']
    assert rows==original


def test_pending_and_review_are_not_losses_and_final_day_flag_is_explicit():
    rows=[indicative(),indicative('b','p2',outcomes=['WON','PENDING']),
          indicative('c','p3',outcomes=['WON','REVIEW'])]
    day=build_daily_ticket_kpis(rows,mode='research')['days'][0]
    assert day['confirmed_winning_tickets']==1 and day['confirmed_losing_tickets']==0
    assert day['pending_tickets']==day['review_tickets']==1
    assert not day['day_results_complete'] and day['winning_combined_odds_sum']==12


@pytest.mark.parametrize('price',[None,True,0,1,-2,'12',float('nan'),float('inf'),10**400])
def test_missing_or_invalid_winning_odds_keep_win_count_but_full_sum_unknown(price):
    rows=[indicative(odds=price),indicative('b','p2',odds=18)]
    day=build_daily_ticket_kpis(rows,mode='research')['days'][0]
    assert day['confirmed_winning_tickets']==2
    assert day['winning_combined_odds_sum'] is None
    assert day['known_winning_odds_sum']==18 and day['missing_winning_odds_tickets']==1
    assert not day['winning_odds_complete']
    json.dumps(day,allow_nan=False)
    assert day['confirmed_losing_tickets']==0


def test_ordinary_decimal_sums_do_not_round_each_winner_to_integer():
    day=build_daily_ticket_kpis([indicative(odds=14.9),indicative('b','p2',odds=15.1)],mode='research')['days'][0]
    assert day['winning_combined_odds_sum']==30.0
    assert day['winning_combined_odds_sum_decimal']=='30.0'


def test_no_winner_is_zero_with_known_losses_not_a_missing_price_sample():
    result=build_daily_ticket_kpis([indicative(odds=None,outcomes=['LOST','LOST'])],mode='research')
    day=result['days'][0]
    assert day['confirmed_winning_tickets']==0 and day['winning_combined_odds_sum']==0
    assert day['winning_odds_complete'] and day['day_results_complete']
    assert build_daily_ticket_kpis([],mode='actual')['days']==[]


def test_original_day_is_preserved_no_settlement_day_shift_or_missing_calendar_days_added():
    early=indicative()
    late=indicative('b','p2',when=NOW+timedelta(days=2))
    late['settlement']['settled_at']=(NOW+timedelta(days=5)).isoformat()
    result=build_daily_ticket_kpis([late,early],mode='research')
    assert [r['date'] for r in result['days']]==sorted([early['date'],late['date']])
    assert len(result['days'])==2


@pytest.mark.parametrize('day',['2026-99-11','2026-1-1',None,'UNKNOWN'])
def test_invalid_original_day_is_unassigned_not_todays_win(day):
    result=build_daily_ticket_kpis([indicative(date=day)],mode='research')
    assert not result['all_input_days_assigned'] and result['days']==[]
    assert result['unassigned_records']=={'INVALID_ORIGINAL_TICKET_DAY':1}


def test_all_confirmed_void_legacy_won_is_refund_not_winner():
    item=indicative(outcomes=['VOID','VOID'])
    item['outcome']=item['settlement']['outcome']='WON'
    day=build_daily_ticket_kpis([item],mode='recommendations')['days'][0]
    assert day['confirmed_winning_tickets']==0 and day['void_tickets']==1
    assert day['winning_combined_odds_sum']==0


def test_partial_void_research_nominal_price_does_not_inflate_effective_sum():
    item=indicative(odds=12,outcomes=['WON','VOID'])
    day=build_daily_ticket_kpis([item],mode='research')['days'][0]
    assert day['confirmed_winning_tickets']==day['void_affected_winners']==1
    assert day['winning_original_nominal_odds_sum']==12
    assert day['winning_combined_odds_sum'] is None


def test_paper_uses_exact_original_winning_leg_prices_and_void_one_not_reported_combo_or_cent_payout():
    item=record(prices=(2.123,3.456),outcomes=['WON','VOID'],combined_price=50)
    result=build_paper_performance([item]);day=result['daily_kpis']['days'][0]
    assert day['winning_combined_odds_sum']==2.123
    assert result['records'][0]['returned_eur']==2.12
    assert day['winning_combined_odds_sum']!=50


def test_unpriced_paper_still_has_verified_winner_but_is_not_financially_eligible():
    result=build_paper_performance([record(prices=(None,3))])
    day=result['daily_kpis']['days'][0]
    assert day['confirmed_winning_tickets']==1 and day['winning_combined_odds_sum'] is None
    assert result['summary']['won']==0 and result['summary']['excluded_records']==1
    assert result['daily_kpis']['records'][0]['paper_financially_eligible'] is False


def test_actual_price_requires_user_confirmation_not_research_combo_or_payout_stake_inference():
    item=indicative(ticket_id='ticket-1',wager={'actual_price_source':'USER_CONFIRMED',
        'actual_combined_price':11.9,'stake_eur':5})
    result=build_daily_ticket_kpis([item],mode='actual')
    assert result['days'][0]['winning_combined_odds_sum']==11.9
    item['wager']['actual_price_source']='ESTIMATED'
    assert build_daily_ticket_kpis([item],mode='actual')['days'][0]['winning_combined_odds_sum'] is None
    item['wager']['actual_price_source']='USER_CONFIRMED'
    item['settlement']['legs'][1].update(outcome='VOID',reason='BOOKMAKER_VOID_CONFIRMED')
    item['settlement']['bookmaker_return']={'payout_eur':20}
    day=build_daily_ticket_kpis([item],mode='actual')['days'][0]
    assert day['confirmed_winning_tickets']==1 and day['winning_combined_odds_sum'] is None
    assert day['winning_original_nominal_odds_sum']==11.9


def test_source_result_identity_mismatch_and_cross_record_result_conflicts_are_review():
    item=indicative();item['settlement']['legs'][0]['fixture_id']='other'
    day=build_daily_ticket_kpis([item],mode='research')['days'][0]
    assert day['confirmed_winning_tickets']==0 and day['review_tickets']==1
    rows=[indicative(),indicative('b','p2',outcomes=['LOST','LOST'])]
    day=build_daily_ticket_kpis(rows,mode='research')['days'][0]
    assert day['review_tickets']==2 and day['confirmed_winning_tickets']==0


def test_duplicate_join_copies_not_extra_winners_distinct_ticket_ids_are_exposures():
    item=indicative();other=indicative('b','p2')
    result=build_daily_ticket_kpis([item,deepcopy(item),other],mode='recommendations')
    assert result['duplicate_exact_copies_ignored']==1
    assert result['days'][0]['confirmed_winning_tickets']==2
    assert not result['shared_fixtures_are_independent']
    other['recommendation_id']='a'
    result=build_daily_ticket_kpis([item,other],mode='recommendations')
    assert result['days'][0]['review_tickets']==2


def test_stale_cached_analysis_cannot_drop_rows_silently():
    rows=[indicative(),indicative('b','p2')]
    cached=build_leg_error_analysis(rows[:1],mode='research')
    result=safe_daily_ticket_kpis(rows,mode='research',leg_analysis=cached)
    assert result['status']=='UNAVAILABLE'


def test_modes_and_month_filter_use_existing_cohort_and_optional_failure_keeps_old_views(tmp_path):
    ledger=PortfolioLedger(tmp_path/'ledger.sqlite3',now=lambda:NOW)
    ledger.save_run(report(ticket(),ticket('research','MERLIN')))
    ledger.record_wager('ticket-1',actual_combined_price=11.9)
    ledger.now=lambda:NOW+timedelta(hours=6)
    ledger.refresh_results(Football({i:fixture(i) for i in (1,2,3)}))
    before={mode:ledger.performance(mode=mode) for mode in ('actual','research','recommendations')}
    assert before['actual']['daily_kpis']['days'][0]['winning_combined_odds_sum']==11.9
    assert before['research']['daily_kpis']['days'][0]['winning_combined_odds_sum']==12
    for mode,value in before.items():
        assert value['daily_kpis']['mode']==mode
        assert ledger.performance('2000-01',mode)['daily_kpis']['days']==[]
    with patch.object(module,'build_daily_ticket_kpis',side_effect=ValueError('optional measurement failed')):
        for mode,value in before.items():
            failed=ledger.performance(mode=mode)
            assert failed['daily_kpis']['status']=='UNAVAILABLE'
            assert failed['rolling_kpis']['status']=='UNAVAILABLE'  # derived from the failed daily measurement
            assert {k:v for k,v in failed.items() if k not in {'daily_kpis','rolling_kpis'}}=={k:v for k,v in value.items() if k not in {'daily_kpis','rolling_kpis'}}


def test_kpi_does_not_increase_llm_feedback_payload(tmp_path):
    ledger=PortfolioLedger(tmp_path/'ledger.sqlite3',now=lambda:NOW)
    ledger.save_run(report(ticket()))
    feedback=ledger.feedback_summary();compact=_compact_feedback(ledger)
    assert 'daily_kpis' in feedback['actual'] and 'daily_kpis' not in json.dumps(compact)
    old=deepcopy(feedback)
    for mode in ('actual','research','recommendations'):
        old[mode].pop('daily_kpis',None)
    with patch.object(ledger,'feedback_summary',return_value=old):
        assert _compact_feedback(ledger)==compact


def test_actual_frontend_renders_missing_vs_zero_and_clears_stale_cohort():
    node=shutil.which('node')
    if not node:
        pytest.skip('Node.js not installed')
    result=subprocess.run([node,'tests/daily_kpi_frontend_check.cjs'],capture_output=True,text=True,timeout=20,check=False)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'DAILY_KPI_FRONTEND_PASS' in result.stdout
