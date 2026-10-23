"""Calendar denominators, existing cash policy and descriptive profile samples."""
import shutil
import subprocess
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from test_paper_performance import record

from goal_analysis.jobs.portfolio import _compact_feedback
from goal_analysis.storage import rolling_ticket_kpis as module
from goal_analysis.storage.daily_ticket_kpis import build_daily_ticket_kpis
from goal_analysis.storage.paper_performance import build_paper_performance
from goal_analysis.storage.portfolio_ledger import PortfolioLedger, _financial, _summary
from goal_analysis.storage.rolling_ticket_kpis import (
    build_rolling_ticket_kpis,
    safe_rolling_ticket_kpis,
)

END=date(2026,10,22)


def item(identifier='a', profile='p1', day=END, price=10, outcome='WON', version='v1', actual=False):
    when=datetime.combine(day,datetime.min.time(),UTC)+timedelta(hours=10)
    value=record(identifier,profile,when=when,combined_price=price,outcomes=[outcome,outcome],
                 ticket_id=identifier,stake_eur=5 if actual else 1,strategy_version=version)
    for leg,settled in zip(value['legs'],value['settlement']['legs'],strict=True):
        leg['fixture_id']=settled['fixture_id']=identifier+':'+leg['fixture_id']
    value['settlement']['settled_at']=(when+timedelta(hours=6)).isoformat()
    if actual:
        value['wager']={'stake_eur':5,'actual_combined_price':price,'actual_price_source':'USER_CONFIRMED'}
    value['financial']=_financial(value) if price is not None else None
    return value


def measure(rows,mode='research',**kwargs):
    return build_rolling_ticket_kpis(rows,_summary,mode=mode,as_of=END,**kwargs)


def test_closed_observed_denominator_excludes_missing_and_pending_days_not_seven_zeroes():
    rows=[item(str(n),'p'+str(n),day=END-timedelta(days=2)) for n in range(3)]
    rows += [item('loss',day=END-timedelta(days=1),outcome='LOST'),item('open',outcome='PENDING')]
    before=deepcopy(rows);window=measure(rows)['windows'][0];goal=window['goals']
    assert window['window_days']==7 and window['observed_days']==3 and window['missing_calendar_days']==4
    assert goal['observed_closed_days']==2 and goal['observed_open_days']==1
    assert goal['count_goal_met_closed_days']==1 and goal['count_goal_closed_observed_rate_pct']==50
    assert goal['joint_goal_closed_observed_rate_pct']==50
    assert goal['count_goal_full_calendar_rate_pct'] is None
    assert rows==before


def test_full_calendar_success_requires_all_seven_closed_observed_days():
    rows=[item(f'{d}-{n}','p'+str(n),day=END-timedelta(days=d)) for d in range(7) for n in range(3)]
    window=measure(rows)['windows'][0]
    assert window['full_calendar_coverage'] and window['goals']['count_goal_full_calendar_rate_pct']==100
    assert window['goals']['joint_goal_full_calendar_rate_pct']==100


@pytest.mark.parametrize('size',[7,30,90])
def test_calendar_boundary_inclusive_old_and_future_dates_excluded(size):
    rows=[item('start',day=END-timedelta(days=size-1)),item('outside',day=END-timedelta(days=size)),
          item('end'),item('future',day=END+timedelta(days=1))]
    window=next(w for w in measure(rows)['windows'] if w['window_days']==size)
    assert window['records']==2 and window['start_date']==(END-timedelta(days=size-1)).isoformat()
    assert window['missing_calendar_days']==size-2


def test_previous_month_data_not_truncated_from_thirty_or_ninety_day_window():
    rows=[item('sep',day=date(2026,9,30)),item('oct',day=date(2026,10,1))]
    windows=measure(rows,month='2026-10')['windows']
    assert windows[0]['records']==0 and windows[1]['records']==windows[2]['records']==2


def test_historical_month_anchors_at_month_end_current_results_not_pit_certificate():
    result=measure([item('sep',day=date(2026,9,30)),item('oct')],month='2026-09')
    assert result['window_end_date']=='2026-09-30' and result['windows'][0]['records']==1
    assert 'NOT_HISTORICAL_ASOF' in result['basis']
    assert measure([item()],month='2026-11')['status']=='FUTURE_PERIOD'


def test_berlin_calendar_day_from_aware_utc_clock_and_naive_clock_rejected():
    result=build_rolling_ticket_kpis([],mode='recommendations',as_of=datetime(2026,10,21,23,tzinfo=UTC))
    assert result['window_end_date']=='2026-10-22'
    assert safe_rolling_ticket_kpis([],mode='recommendations',as_of=datetime(2026,10,22))['status']=='UNAVAILABLE'  # noqa: DTZ001 - deliberate invalid clock


def test_joint_odds_target_unknown_is_not_success_or_failure_but_count_goal_known():
    rows=[item('a',price=12),item('b','p2',price=18),item('c','p3',price=None)]
    goal=measure(rows,mode='recommendations')['windows'][0]['goals']
    assert goal['count_goal_closed_observed_rate_pct']==100
    assert goal['joint_goal_unknown_closed_days']==1
    assert goal['joint_goal_closed_observed_rate_pct'] is None
    assert goal['joint_goal_known_subset_rate_pct'] is None


def test_less_than_three_winners_is_definite_joint_failure_even_if_odds_missing():
    goal=measure([item(price=None)],mode='recommendations')['windows'][0]['goals']
    assert goal['joint_goal_unknown_closed_days']==0 and goal['joint_goal_closed_observed_rate_pct']==0


def test_actual_profit_and_drawdown_are_existing_accounting_with_original_stakes():
    rows=[item('win',day=END-timedelta(days=2),actual=True),
          item('loss',day=END-timedelta(days=1),outcome='LOST',actual=True),
          item('open',outcome='PENDING',actual=True)]
    summary=_summary(rows);money=measure(rows,mode='actual')['windows'][0]['financial']
    assert money['settled_net_eur_to_date']==summary['net_eur']
    assert money['max_drawdown_eur']==summary['max_drawdown_eur']==5
    assert money['open_stake_eur']==5 and money['complete_financial_coverage']
    assert money['prior_window_bankroll_peak_included'] is False


def test_unverified_actual_legacy_price_does_not_become_verified_profit_or_zero():
    good=item('good',actual=True);bad=item('bad','p2',actual=True)
    bad['wager']['actual_price_source']='ESTIMATED'
    money=measure([good,bad],mode='actual')['windows'][0]['financial']
    assert money['settled_net_eur_to_date'] is money['max_drawdown_eur'] is None
    assert money['known_subset_settled_net_eur']==45 and money['excluded_records']==1


def test_known_actual_loss_uses_recorded_stake_without_needing_winning_odds():
    row=item(outcome='LOST',actual=True)
    row['wager']['actual_price_source']='ESTIMATED'
    money=measure([row],mode='actual')['windows'][0]['financial']
    assert money['settled_net_eur_to_date']==-5
    assert money['max_drawdown_eur']==5 and money['complete_financial_coverage']


def test_confirmed_partial_void_return_is_cash_proof_but_not_inferred_odds():
    row=item(actual=True)
    row['wager']['actual_price_source']='ESTIMATED'
    row['settlement']['legs'][1].update(outcome='VOID',reason='BOOKMAKER_VOID_CONFIRMED')
    row['settlement']['bookmaker_return']={'payout_eur':20}
    row['financial']=_financial(row)
    window=measure([row],mode='actual')['windows'][0]
    assert window['financial']['settled_net_eur_to_date']==15
    assert window['goals']['count_goal_met_closed_days']==0


def test_recommendations_never_receive_assumed_profit_or_bankroll_drawdown():
    money=measure([item()],mode='recommendations')['windows'][0]['financial']
    assert money['settled_net_eur_to_date'] is money['known_subset_settled_net_eur'] is None
    assert money['max_drawdown_eur'] is None


def test_paper_unknown_full_profit_and_priced_subset_are_kept_separate():
    when=datetime.combine(END,datetime.min.time(),UTC)+timedelta(hours=10)
    result=build_paper_performance([record('good',when=when),record('bad','p2',when=when,prices=(None,3))],as_of=END)
    money=result['rolling_kpis']['windows'][0]['financial']
    assert money['settled_net_eur_to_date'] is money['max_drawdown_eur'] is None
    assert money['known_subset_settled_net_eur']==5
    assert money['eligible_records']==1 and money['excluded_records']==1


def test_paper_drawdown_is_daily_settled_net_with_zero_window_opening():
    when=datetime.combine(END-timedelta(days=2),datetime.min.time(),UTC)+timedelta(hours=10)
    rows=[record('win',when=when),record('loss',when=when+timedelta(days=1),outcomes=['LOST','LOST']),
          record('loss2',when=when+timedelta(days=2),outcomes=['LOST','LOST'])]
    result=build_paper_performance(rows,as_of=END)
    money=result['rolling_kpis']['windows'][0]['financial']
    assert money['settled_net_eur_to_date']==3 and money['max_drawdown_eur']==2
    assert money['drawdown_basis']=='ORIGINAL_DAY_AGGREGATED_SETTLED_NET_ZERO_WINDOW_OPENING'


def test_dropped_unassignable_paper_input_blocks_false_full_financial_coverage():
    when=datetime.combine(END,datetime.min.time(),UTC)+timedelta(hours=10)
    result=build_paper_performance([record(when=when),'invalid raw record'],as_of=END)
    window=result['rolling_kpis']['windows'][0]
    assert window['unassigned_source_records']==1
    assert window['financial']['settled_net_eur_to_date'] is None
    assert window['financial']['known_subset_settled_net_eur']==5


def test_profile_reliability_separates_versions_and_never_becomes_a_predictive_grade():
    rows=[item('a',version='v1'),item('b',day=END-timedelta(days=1),version='v2'),
          item('c','unknown',version=None,outcome='PENDING')]
    profiles=measure(rows)['windows'][0]['profile_reliability']
    assert {(r['profile_id'],r['strategy_version']) for r in profiles}=={('p1','v1'),('p1','v2'),('unknown','UNKNOWN_LEGACY')}
    assert all(not r['predictive_reliability_validated'] and r['strategy_recommendation'] is None for r in profiles)
    assert next(r for r in profiles if r['profile_id']=='unknown')['decided_win_rate_pct'] is None


def test_unknown_fixture_id_does_not_become_a_fictitious_independent_match():
    row=item()
    row['legs'][0]['fixture_id']=row['settlement']['legs'][0]['fixture_id']=None
    profile=measure([row])['windows'][0]['profile_reliability'][0]
    assert profile['unique_fixtures']==1 and profile['unidentified_fixture_occurrences']==1
    assert profile['review']==1 and not profile['predictive_reliability_validated']


def test_duplicate_originals_do_not_inflate_calendar_success_or_profile_counts():
    row=item()
    window=measure([row,deepcopy(row)])['windows'][0]
    assert window['records']==window['profile_reliability'][0]['records']==1


def test_empty_window_unknown_goal_rate_and_profit_not_fabricated_loss_days():
    window=measure([])['windows'][0]
    assert window['missing_calendar_days']==7 and window['observed_days']==0
    assert window['goals']['count_goal_closed_observed_rate_pct'] is None
    assert window['financial']['settled_net_eur_to_date'] is None


def test_stale_daily_source_cohort_is_rejected_and_safe_error_has_no_decision_effect():
    rows=[item(),item('b','p2')]
    stale=build_daily_ticket_kpis(rows[:1],mode='research')
    result=safe_rolling_ticket_kpis(rows,_summary,mode='research',as_of=END,daily_kpis=stale)
    assert result['status']=='UNAVAILABLE' and result['decision_effect']=='NONE'


def test_optional_rolling_failure_preserves_prior_daily_and_financial_views(tmp_path):
    from test_portfolio_ledger import NOW, report, ticket
    ledger=PortfolioLedger(tmp_path/'ledger.sqlite3',now=lambda:NOW)
    ledger.save_run(report(ticket()))
    before=ledger.performance(mode='research')
    with patch.object(module,'build_rolling_ticket_kpis',side_effect=ValueError('bad optional evidence')):
        after=ledger.performance(mode='research')
    assert after['rolling_kpis']['status']=='UNAVAILABLE'
    assert {k:v for k,v in after.items() if k!='rolling_kpis'}=={k:v for k,v in before.items() if k!='rolling_kpis'}


def test_new_rolling_measurement_does_not_expand_llm_feedback(tmp_path):
    from test_portfolio_ledger import NOW, report, ticket
    ledger=PortfolioLedger(tmp_path/'ledger.sqlite3',now=lambda:NOW)
    ledger.save_run(report(ticket()))
    source=ledger.feedback_summary();compact=_compact_feedback(ledger)
    assert 'rolling_kpis' in source['research']
    for mode in ('actual','research','recommendations'):
        source[mode].pop('rolling_kpis',None)
    with patch.object(ledger,'feedback_summary',return_value=source):
        assert _compact_feedback(ledger)==compact


def test_rolling_frontend_closed_denominator_unknown_money_and_stale_view_clear():
    node=shutil.which('node')
    if not node:
        pytest.skip('Node.js not installed')
    result=subprocess.run([node,'tests/rolling_kpi_frontend_check.cjs'],capture_output=True,text=True,check=False,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'ROLLING_KPI_FRONTEND_PASS' in result.stdout
