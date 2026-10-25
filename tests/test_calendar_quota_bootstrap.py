"""A single renewal uses witnessed capacity, including prior daily claims."""
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.jobs.calendar_collection_shadow import QUOTA, _bootstrap_allowed
from goal_analysis.jobs.player_collection_shadow import QUOTA as PLAYER_QUOTA
from goal_analysis.providers.the_stats_api import TheStatsApiUsage
from goal_analysis.storage.provider_quota import SharedQuota

NOW = datetime(2026, 10, 25, 8, 58, 5, tzinfo=UTC)


def seeded(root, *, recent=44, remaining=66, limit=120, monthly=93769,
           age=12, month_reset=None, minute_reset=None, status=200,
           pending=0, policy=None, retry_after=None):
    stamp = [NOW-timedelta(seconds=max(age+10, 30))]
    observer = SharedQuota(root, clock=lambda: stamp[0])
    for _ in range(recent-1-pending):
        attempt = observer.begin()
        observer.finish(attempt, {'authenticated': True, 'http_status': 200,
            'observed_at': stamp[0].isoformat()}, TheStatsApiUsage())
    stamp[0] = NOW-timedelta(seconds=age)
    attempt = observer.begin()
    observer.finish(attempt, {'authenticated': True, 'http_status': status,
        'observed_at': stamp[0].isoformat()}, TheStatsApiUsage(
        minute_limit=limit, minute_remaining=remaining,
        minute_reset=minute_reset if minute_reset is not None else int((NOW-timedelta(seconds=5)).timestamp()),
        monthly_limit=100000, monthly_remaining=monthly,
        monthly_reset=month_reset if month_reset is not None else int((NOW+timedelta(days=30)).timestamp()),
        retry_after=retry_after))
    stamp[0] = NOW-timedelta(seconds=1)
    for _ in range(pending):
        observer.begin()
    return SharedQuota(root, policy=policy or QUOTA, clock=lambda: NOW)


@pytest.mark.parametrize('recent', [10, 27, 44, 109])
def test_known_real_capacity_can_renew_after_more_than_nine_daily_claims(tmp_path, recent):
    quota = seeded(tmp_path, recent=recent, remaining=119)
    before = quota.path.read_bytes()
    assert quota.allowance()['status'] == 'QUOTA_REFRESH_REQUIRED'
    assert _bootstrap_allowed(quota, NOW) is True
    assert quota.path.read_bytes() == before


@pytest.mark.parametrize('recent', [110, 120, 121])
def test_minute_limit_minus_reserve_must_have_room_for_one_actual_claim(tmp_path, recent):
    assert _bootstrap_allowed(seeded(tmp_path, recent=recent, remaining=119), NOW) is False


@pytest.mark.parametrize('remaining', [0, 9, 10])
def test_last_witnessed_remaining_cannot_spend_the_reserve(tmp_path, remaining):
    assert _bootstrap_allowed(seeded(tmp_path, remaining=remaining), NOW) is False


@pytest.mark.parametrize('limit', [None, 0])
def test_missing_or_zero_provider_minute_limit_cannot_authorize_renewal(tmp_path, limit):
    assert _bootstrap_allowed(seeded(tmp_path, limit=limit), NOW) is False


def test_remaining_above_the_witnessed_limit_is_invalid(tmp_path):
    assert _bootstrap_allowed(seeded(tmp_path, remaining=121), NOW) is False


@pytest.mark.parametrize('status', [401, 403, 429])
def test_provider_denial_still_stops_bootstrap(tmp_path, status):
    assert _bootstrap_allowed(seeded(tmp_path, status=status), NOW) is False


@pytest.mark.parametrize('age', [-1, 3601])
def test_future_or_stale_quota_receipt_is_not_retimed(tmp_path, age):
    assert _bootstrap_allowed(seeded(tmp_path, age=age), NOW) is False


def test_monthly_reset_is_not_inferred_after_the_billing_cycle(tmp_path):
    reset = int((NOW-timedelta(seconds=1)).timestamp())
    assert _bootstrap_allowed(seeded(tmp_path, month_reset=reset), NOW) is False


def test_minute_reset_must_have_actually_elapsed(tmp_path):
    reset = int((NOW+timedelta(seconds=1)).timestamp())
    assert _bootstrap_allowed(seeded(tmp_path, minute_reset=reset), NOW) is False


def test_active_retry_after_is_not_bypassed_by_a_later_success_status(tmp_path):
    assert _bootstrap_allowed(seeded(tmp_path, retry_after=60), NOW) is False


def test_unlimited_monthly_header_does_not_invent_minute_capacity(tmp_path):
    assert _bootstrap_allowed(seeded(tmp_path, monthly=-1, remaining=10), NOW) is False


def test_unknown_monthly_dimension_stays_unknown(tmp_path):
    assert _bootstrap_allowed(seeded(tmp_path, monthly=None), NOW) is False


def test_inflight_claims_are_debited_before_spending_observed_remaining(tmp_path):
    assert _bootstrap_allowed(seeded(tmp_path, remaining=12, pending=2), NOW) is False


def test_player_policy_keeps_its_larger_minute_and_monthly_reserves(tmp_path):
    calendar = seeded(tmp_path/'calendar', remaining=20)
    player_minute = seeded(tmp_path/'player-minute', remaining=20, policy=PLAYER_QUOTA)
    player_month = seeded(tmp_path/'player-month', remaining=119, monthly=30000, policy=PLAYER_QUOTA)
    assert _bootstrap_allowed(calendar, NOW) is True
    assert _bootstrap_allowed(player_minute, NOW) is False
    assert _bootstrap_allowed(player_month, NOW) is False


def test_future_pending_claim_is_a_clock_conflict(tmp_path):
    quota = seeded(tmp_path, recent=1)
    observer = SharedQuota(tmp_path, clock=lambda: NOW+timedelta(seconds=1))
    observer.begin()
    assert _bootstrap_allowed(quota, NOW) is False


def test_synthetic_setup_matches_the_real_archived_44_claim_failure_shape(tmp_path):
    quota = seeded(tmp_path, recent=44, remaining=66, pending=2)
    with sqlite3.connect(quota.path) as db:
        count = db.execute('SELECT count(*) FROM quota_attempts').fetchone()[0]
    assert count == 44
    assert _bootstrap_allowed(quota, NOW) is True
