"""Recorded provider shapes only; no credential reads or real HTTP requests."""

import json
from copy import deepcopy
from datetime import timedelta
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from test_api_football_odds import calendar, odds_rows
from test_daily_223_live import NOW, config, events

from goal_analysis.jobs.daily_223_candidates import assemble_daily223_candidates
from goal_analysis.jobs.odds_monitor import OddsMonitor
from goal_analysis.jobs.odds_monitor_providers import (
    MonitorFetchError,
    MonitorProviderAdapter,
    build_monitor_queries,
    fetch_monitor_query,
)
from goal_analysis.providers.api_football import ApiFootballClient
from goal_analysis.providers.daily_223_odds import Daily223OddsFeed


def saved_report(*, with_mapping=True):
    fixtures = calendar()
    quotes = assemble_daily223_candidates(fixtures, events(), config(), NOW.date(), NOW, NOW)["candidates"]
    return {"fixtures": fixtures,
            "recommendations": [{"legs": quotes}] if with_mapping else [],
            "run_id": "synthetic-report"}


def queries(provider=None, *, cfg=None, report=None, now=NOW):
    result = build_monitor_queries(report or saved_report(), now, cfg or config())
    return [query for query in result if provider is None or query["provider"] == provider]


def test_both_provider_plans_reuse_exact_fixture_and_saved_event_mappings():
    cfg = config()
    cfg["extra_markets"] = ["btts", "totals_h1"]
    planned = queries(cfg=cfg)
    assert sum(query["provider"] == "api_football" for query in planned) == 3
    assert sum(query["kind"] == "BULK_ODDS" for query in planned) == 1
    extras = [query for query in planned if query["kind"] == "EXTRA_ODDS"]
    assert len(extras) == 3
    assert {query["params"]["event_id"] for query in extras} == {"event-0", "event-1", "event-2"}
    assert all(query["estimated_cost"] == 2 for query in extras)
    assert all(query["params"]["markets"] == ["btts", "totals_h1"] for query in extras)
    assert all(set(query["fixture_ids"]) == set(query["kickoff_by_fixture"]) for query in planned)
    assert planned == queries(cfg=cfg, report={**saved_report(), "fixtures": list(reversed(calendar()))})


def test_no_exact_saved_event_mapping_means_no_paid_extra_discovery_calls():
    cfg = config()
    cfg["extra_markets"] = ["btts"]
    planned = queries(cfg=cfg, report=saved_report(with_mapping=False))
    assert not any(query["kind"] == "EXTRA_ODDS" for query in planned)
    assert any(query["kind"] == "BULK_ODDS" for query in planned)


def test_ambiguous_saved_event_id_is_not_used_for_extra_query():
    report, cfg = saved_report(), config()
    cfg["extra_markets"] = ["btts"]
    different = deepcopy(report["recommendations"][0]["legs"][0])
    original_event = urlsplit(different["quote_source_id"]).path.split("/events/")[1].split("/")[0]
    different["quote_source_id"] = different["quote_source_id"].replace(original_event + "/", "other-event/")
    report["recommendations"][0]["legs"].append(different)
    affected = different["fixture_id"]
    assert all(affected not in query["fixture_ids"] for query in queries(cfg=cfg, report=report)
               if query["kind"] == "EXTRA_ODDS")


@pytest.mark.parametrize("change", ["at_lockout", "started", "duplicate", "wrong_api_id", "bad_sport", "naive"])
def test_invalid_or_locked_fixture_never_schedules_provider_request(change):
    report = saved_report()
    row = report["fixtures"][0]
    identifier = row["fixture_id"]
    if change == "at_lockout":
        row["kickoff"] = (NOW + timedelta(minutes=15)).isoformat()
    elif change == "started":
        row["provider_status"] = "1H"
    elif change == "duplicate":
        report["fixtures"].append(deepcopy(row))
    elif change == "wrong_api_id":
        row["api_football_fixture_id"] = "999999"
    elif change == "bad_sport":
        row["sport_key"] = "https://invalid.example"
    elif change == "naive":
        row["kickoff"] = NOW.replace(tzinfo=None).isoformat()
    assert all(identifier not in query["fixture_ids"] for query in queries(report=report))


def test_bulk_cache_key_tracks_http_parameters_not_volatile_fixture_membership(tmp_path):
    report = saved_report()
    report["fixtures"][0]["kickoff"] = (NOW + timedelta(minutes=16)).isoformat()
    first = next(query for query in queries(report=report) if query["kind"] == "BULK_ODDS")
    later = NOW + timedelta(minutes=2)
    second = next(query for query in queries(report=report, now=later) if query["kind"] == "BULK_ODDS")
    assert len(first["fixture_ids"]) == 3 and len(second["fixture_ids"]) == 2
    assert first["query_key"] == second["query_key"]
    calls, clock = [], [NOW]
    monitor = OddsMonitor(tmp_path, clock=lambda: clock[0])
    fetch = lambda query: (calls.append(query["query_key"]) or {"quotes": [], "actual_cost": 2})
    assert monitor.tick([first], fetch)["provider_calls"] == 1
    clock[0] = later
    assert monitor.tick([second], fetch)["provider_calls"] == 0
    assert len(calls) == 1
    changed = deepcopy(config())
    changed["odds_region"] = "uk"
    other = next(query for query in queries(cfg=changed, report=report) if query["kind"] == "BULK_ODDS")
    assert other["query_key"] != first["query_key"]


def test_api_football_fetch_reuses_normalizer_and_preserves_provider_quota_provenance():
    planned = queries("api_football")[0]
    seen = []

    def transport(url, headers):
        seen.append((url, headers))
        identifier = int(parse_qs(urlsplit(url).query)["fixture"][0])
        rows = [row for row in odds_rows() if row["fixture"]["id"] == identifier]
        return {"response": rows, "paging": {"current": 1, "total": 1}, "errors": []}, {
            "X-RateLimit-Requests-Remaining": "7", "X-RateLimit-Requests-Limit": "100"}

    client = ApiFootballClient(api_key="synthetic-secret", transport=transport)
    result = fetch_monitor_query(planned, football_client=client, now=NOW)
    assert len(seen) == 1 and result["remaining_quota"] == 7 and result["actual_cost"] == 1
    assert result["quotes"]
    assert all(quote["quote_provider"] == "api_football" for quote in result["quotes"])
    assert all(quote["bookmaker"] == "betano_api_football_42" for quote in result["quotes"])
    assert all(quote["quoted_at"] == NOW.isoformat() for quote in result["quotes"])
    assert all(quote["region"] == "DE" and quote["currency"] == "EUR" for quote in result["quotes"])
    assert "synthetic-secret" not in json.dumps([planned, result])
    assert seen[0][1] == {"x-apisports-key": "synthetic-secret"}


def test_odds_api_bulk_and_extra_fetch_use_existing_feed_and_never_reprice():
    cfg = config()
    cfg["extra_markets"] = ["btts"]
    planned = queries("the_odds_api", cfg=cfg)
    seen = []

    def transport(url, headers):
        seen.append(url)
        path = urlsplit(url).path
        if "/events/" in path:
            identifier = path.split("/events/")[1].split("/")[0]
            event = next(item for item in events() if item["id"] == identifier)
            event["bookmakers"][0]["markets"] = [{"key": "btts", "outcomes": [
                {"name": "Yes", "price": 2.6}, {"name": "No", "price": 1.6}]}]
            payload = event
        else:
            payload = events()
        cost = len(parse_qs(urlsplit(url).query)["markets"][0].split(","))
        return payload, {"x-requests-remaining": "40", "x-requests-last": str(cost)}, json.dumps(payload).encode()

    feed = Daily223OddsFeed("synthetic-odds-secret", "eu", 20, transport=transport)
    bulk = next(query for query in planned if query["kind"] == "BULK_ODDS")
    primary = fetch_monitor_query(bulk, odds_feed=feed, now=NOW)
    assert len(seen) == 1 and primary["quotes"] and primary["remaining_quota"] == 40
    assert primary["actual_cost"] == 2
    extra = next(query for query in planned if query["kind"] == "EXTRA_ODDS")
    extended = MonitorProviderAdapter(odds_feed=feed, clock=lambda: NOW)(extra)
    assert len(seen) == 2 and extended["actual_cost"] == 1
    assert {quote["market_key"] for quote in extended["quotes"]} == {"btts"}
    assert {quote["decimal_price"] for quote in extended["quotes"]} == {2.6, 1.6}
    assert all(quote["quote_provider"] == "the_odds_api" for quote in extended["quotes"])
    assert "synthetic-odds-secret" not in json.dumps([planned, primary, extended])


def test_api_football_saved_bookmaker_id_is_an_exact_request_parameter():
    report = saved_report()
    candidate = next(quote for quote in report["recommendations"][0]["legs"]
                     if quote["fixture_id"] == "api_football:9000")
    candidate.update(quote_provider="api_football", bookmaker="betano_api_football_42",
                     quote_source_id="https://v3.football.api-sports.io/odds?fixture=9000&bookmaker=42&bet=1")
    selected = next(query for query in queries("api_football", report=report)
                    if query["fixture_ids"] == ["api_football:9000"])
    assert selected["params"]["bookmaker"] == 42


def test_error_429_preserves_retry_after_and_does_not_expose_provider_body_or_url():
    planned = queries("api_football")[0]

    def transport(*args):
        raise HTTPError("https://provider.example/?secret=DO_NOT_LOG", 429, "DO_NOT_LOG", {"Retry-After": "120"}, None)

    client = ApiFootballClient(api_key="synthetic-secret", transport=transport)
    with pytest.raises(MonitorFetchError) as caught:
        fetch_monitor_query(planned, football_client=client, now=NOW)
    assert caught.value.status_code == 429 and caught.value.retry_after_seconds == 120
    assert str(caught.value) == "PROVIDER_UNAVAILABLE"
    assert "DO_NOT_LOG" not in str(caught.value)


def test_quota_failure_is_structured_and_invalid_pagination_does_not_make_hidden_calls():
    planned = queries("api_football")[0]
    client = ApiFootballClient(api_key="synthetic-secret", transport=lambda *_: (
        {"errors": {"requests": "private provider detail"}, "response": []},
        {"x-ratelimit-requests-remaining": "0"}))
    with pytest.raises(MonitorFetchError) as caught:
        fetch_monitor_query(planned, football_client=client, now=NOW)
    assert caught.value.quota_exhausted
    calls = []

    def paged(*args):
        calls.append(1)
        return {"response": [], "paging": {"current": 1, "total": 2}}, {}

    client = ApiFootballClient(api_key="synthetic-secret", transport=paged)
    with pytest.raises(MonitorFetchError, match="INVALID_PROVIDER_RESPONSE"):
        fetch_monitor_query(planned, football_client=client, now=NOW)
    assert len(calls) == 1


def test_future_provider_update_is_not_manufactured_into_historical_observation():
    planned = queries("api_football")[0]
    rows = odds_rows()
    for row in rows:
        row["update"] = (NOW + timedelta(minutes=1)).isoformat()
    client = ApiFootballClient(api_key="synthetic-secret", transport=lambda *_: (
        {"response": rows, "paging": {"current": 1, "total": 1}}, {}))
    assert fetch_monitor_query(planned, football_client=client, now=NOW)["quotes"] == []


def test_fetch_rechecks_lockout_before_request_even_with_old_saved_plan():
    planned = queries("api_football")[0]
    kickoff = min(planned["kickoff_by_fixture"].values())
    from datetime import datetime
    clock = datetime.fromisoformat(kickoff) - timedelta(minutes=15)
    with pytest.raises(MonitorFetchError, match="KICKOFF_LOCKOUT"):
        fetch_monitor_query(planned, now=clock)


def test_real_odds_feed_cost_overrun_preserves_charge_and_blocks_fresh_monitor_request(tmp_path):
    planned = next(query for query in queries("the_odds_api") if query["kind"] == "BULK_ODDS")
    calls = []

    def transport(*args):
        calls.append(1)
        return events(), {"x-requests-last": "3", "x-requests-remaining": "19"}, b"[]"

    feed = Daily223OddsFeed("synthetic-secret", "eu", 20, transport=transport)
    with pytest.raises(MonitorFetchError) as caught:
        fetch_monitor_query(planned, odds_feed=feed, now=NOW)
    assert caught.value.actual_cost == 3 and caught.value.unexpected_cost
    assert caught.value.remaining_quota == 19 and feed.halted
    # A new CLI process creates a fresh feed; the durable monitor must retain
    # the unexpected-cost stop independently of that in-memory feed flag.
    calls.clear()
    feed = Daily223OddsFeed("synthetic-secret", "eu", 20, transport=transport)
    monitor = OddsMonitor(tmp_path, clock=lambda: NOW)
    adapter = MonitorProviderAdapter(odds_feed=feed, clock=lambda: NOW)
    assert monitor.tick([planned], adapter)["provider_calls"] == 1
    changed = deepcopy(planned)
    changed["query_key"] += "-different-external-query"
    fresh = Daily223OddsFeed("synthetic-secret", "eu", 20, transport=transport)
    result = OddsMonitor(tmp_path, clock=lambda: NOW + timedelta(minutes=1)).tick(
        [changed], MonitorProviderAdapter(odds_feed=fresh, clock=lambda: NOW + timedelta(minutes=1)))
    assert result["provider_calls"] == 0 and len(calls) == 1
    import sqlite3
    with sqlite3.connect(monitor.path) as db:
        assert db.execute("SELECT SUM(cost) FROM monitor_attempts").fetchone()[0] == 3
