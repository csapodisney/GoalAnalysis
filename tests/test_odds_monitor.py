"""Polling cadence, provider budgets and immutable history use temporary storage."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from goal_analysis.jobs.odds_monitor import (
    MonitorPolicy,
    OddsMonitor,
    monitor_history,
    monitor_status,
)

NOW = datetime(2026, 10, 15, 10, tzinfo=UTC)


def query(identifier="fixture-1", *, provider="api_football", kickoff=None, cost=1):
    return {"provider": provider, "query_key": provider + ":" + identifier,
            "fixture_ids": [identifier], "kickoff_by_fixture": {identifier: (kickoff or NOW + timedelta(hours=8)).isoformat()},
            "estimated_cost": cost}


def result(job, *, remaining=100, actual_cost=1):
    identifier = job["fixture_ids"][0]
    return {"remaining_quota": remaining, "actual_cost": actual_cost,
            "quotes": [{"fixture_id": identifier, "market_key": "h2h", "selection_key": "home",
                        "period": "FULL_TIME", "settlement": "REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES",
                        "bookmaker": "book", "region": "DE", "currency": "EUR",
                        "quote_provider": job["provider"], "decimal_price": 2.1,
                        "quoted_at": NOW.isoformat(), "quote_source_id": "https://v3.football.api-sports.io/odds?fixture=1"}]}


def test_thirty_minute_cache_survives_new_instances_and_uses_last_success(tmp_path):
    clock, calls = [NOW], []
    fetch = lambda job: (calls.append(job) or result(job))
    first = OddsMonitor(tmp_path, clock=lambda: clock[0])
    assert first.tick([query()], fetch)["provider_calls"] == 1
    clock[0] += timedelta(minutes=29, seconds=59)
    second = OddsMonitor(tmp_path, clock=lambda: clock[0])
    assert second.tick([query()], fetch)["queries"][0]["status"] == "CACHED"
    clock[0] += timedelta(seconds=1)
    assert second.tick([query()], fetch)["provider_calls"] == 1
    assert len(calls) == 2
    assert len(monitor_history(tmp_path, clock[0])) == 2
    assert monitor_status(tmp_path, clock[0])["astra_calls"] == 0


def test_fifteen_minute_boundary_is_locked_and_has_no_cache_or_provider_call(tmp_path):
    tick = OddsMonitor(tmp_path, clock=lambda: NOW).tick(
        [query(kickoff=NOW + timedelta(minutes=15))], lambda _: (_ for _ in ()).throw(AssertionError()),
    )
    assert tick["provider_calls"] == 0
    assert tick["queries"][0]["status"] == "LOCKED_OR_INVALID"
    assert not (tmp_path / "data").exists()


def test_single_flight_lease_blocks_concurrent_process_before_fetch(tmp_path):
    first = OddsMonitor(tmp_path, clock=lambda: NOW)
    second = OddsMonitor(tmp_path, clock=lambda: NOW)
    nested = []

    def fetch(job):
        nested.append(second.tick([job], lambda _: (_ for _ in ()).throw(AssertionError())))
        return result(job)

    assert first.tick([query()], fetch)["provider_calls"] == 1
    assert nested[0]["queries"][0]["status"] == "BUSY"
    assert len(monitor_history(tmp_path, NOW)) == 1


def test_429_uses_retry_after_provider_backoff_and_never_marks_success(tmp_path):
    clock = [NOW]

    class Limited(Exception):
        status_code = 429
        retry_after_seconds = 300

    def fail(_):
        raise Limited("Secret-bearing provider text must never be stored")

    monitor = OddsMonitor(tmp_path, clock=lambda: clock[0])
    assert monitor.tick([query()], fail)["queries"][0]["status"] == "RATE_LIMITED"
    assert monitor_history(tmp_path, NOW) == []
    assert monitor.tick([query("another")], fail)["queries"][0]["status"] == "PROVIDER_BACKOFF"
    state = monitor_status(tmp_path, NOW)
    assert "Secret-bearing" not in str(state)
    assert "last_success_at" not in state["queries"][0]
    clock[0] += timedelta(minutes=6)
    assert monitor.tick([query()], result)["queries"][0]["status"] == "UPDATED"


def test_minute_burst_and_known_provider_quota_are_reserved_before_requests(tmp_path):
    policy = MonitorPolicy(max_requests_per_minute=2)
    monitor = OddsMonitor(tmp_path, policy=policy, clock=lambda: NOW)
    tick = monitor.tick([query(str(index)) for index in range(4)], result)
    assert tick["provider_calls"] == 2
    assert [row["status"] for row in tick["queries"]] == ["UPDATED", "UPDATED", "MINUTE_BUDGET", "MINUTE_BUDGET"]
    other = OddsMonitor(tmp_path / "empty-quota", clock=lambda: NOW)
    tick = other.tick([query("one"), query("two")], lambda job: result(job, remaining=0))
    assert tick["provider_calls"] == 1 and tick["queries"][1]["status"] == "QUOTA_PAUSED"


def test_daily_credit_caps_survive_restart_and_failures_consume_reservation(tmp_path):
    policy = MonitorPolicy(daily_credit_limits={"api_football": 1, "the_odds_api": 0})
    first = OddsMonitor(tmp_path, policy=policy, clock=lambda: NOW)
    first.tick([query()], lambda _: (_ for _ in ()).throw(ValueError("failure")))
    second = OddsMonitor(tmp_path, policy=policy, clock=lambda: NOW + timedelta(minutes=1))
    assert second.tick([query("second")], result)["queries"][0]["status"] == "DAILY_BUDGET"
    assert second.tick([query(provider="the_odds_api")], result)["queries"][0]["status"] == "DAILY_BUDGET"


def test_response_arriving_inside_lockout_does_not_publish_new_prices(tmp_path):
    clock = [NOW]
    monitor = OddsMonitor(tmp_path, clock=lambda: clock[0])
    job = query(kickoff=NOW + timedelta(minutes=15, seconds=1))

    def fetch(current):
        clock[0] += timedelta(seconds=2)
        return result(current)

    assert monitor.tick([job], fetch)["provider_calls"] == 1
    assert monitor_history(tmp_path, clock[0])[0]["odds_history_snapshot"]["quotes"] == []


def test_history_reads_never_create_storage_and_respect_as_of_cutoff(tmp_path):
    assert monitor_history(tmp_path, NOW) == []
    assert monitor_status(tmp_path, NOW)["status"] == "NOT_STARTED"
    assert not (tmp_path / "data").exists()
    job = query()
    original = deepcopy(job)
    OddsMonitor(tmp_path, clock=lambda: NOW).tick([job], result)
    assert monitor_history(tmp_path, NOW - timedelta(seconds=1)) == []
    assert job == original
    history = monitor_history(tmp_path, NOW)
    assert history[0]["run_id"].startswith("odds-monitor-")
    assert history[0]["finished_at"] == NOW.isoformat()


def test_unexpected_real_credit_cost_halts_provider_without_losing_valid_history(tmp_path):
    monitor = OddsMonitor(tmp_path, clock=lambda: NOW)
    tick = monitor.tick([query("one"), query("two")], lambda job: result(job, actual_cost=2))
    assert tick["provider_calls"] == 1
    assert tick["queries"][1]["status"] == "PROVIDER_BACKOFF"
    assert monitor_status(tmp_path, NOW)["providers"]["api_football"]["status"] == "UNEXPECTED_COST"


def test_quota_exhausted_error_pauses_other_queries_without_repeated_attempts(tmp_path):
    class EmptyQuota(Exception):
        quota_exhausted = True

    def fail(_):
        raise EmptyQuota()

    monitor = OddsMonitor(tmp_path, clock=lambda: NOW)
    tick = monitor.tick([query("one"), query("two")], fail)
    assert tick["provider_calls"] == 1
    assert tick["queries"][1]["status"] == "QUOTA_PAUSED"


def test_unfetched_queries_precede_old_cached_queries_to_prevent_starvation(tmp_path):
    clock = [NOW]
    policy = MonitorPolicy(max_requests_per_minute=1)
    monitor = OddsMonitor(tmp_path, policy=policy, clock=lambda: clock[0])
    jobs = [query(str(index)) for index in range(3)]
    assert monitor.tick(jobs, result)["provider_calls"] == 1
    clock[0] += timedelta(minutes=30)
    tick = monitor.tick(jobs, result)
    assert tick["queries"][0]["query_key"].endswith(":1")
    clock[0] += timedelta(minutes=30)
    tick = monitor.tick(jobs, result)
    assert tick["queries"][0]["query_key"].endswith(":2")


def test_monitor_preserves_configured_nightly_quota_reserve(tmp_path):
    policy = MonitorPolicy(quota_reserve_limits={"api_football": 10})
    monitor = OddsMonitor(tmp_path, policy=policy, clock=lambda: NOW)
    tick = monitor.tick([query("one"), query("two")], lambda job: result(job, remaining=10))
    assert tick["provider_calls"] == 1
    assert tick["queries"][1]["status"] == "QUOTA_RESERVED"


def test_dashboard_exposes_monitor_status_without_starting_or_creating_monitor(tmp_path):
    from goal_analysis.portfolio_dashboard import ArthurState

    path = tmp_path / "data/arthur/odds-monitor.sqlite3"
    assert not path.exists()
    snapshot = ArthurState(tmp_path).snapshot()
    assert snapshot["runtime"]["odds_monitor"]["status"] == "NOT_STARTED"
    assert not path.exists()
