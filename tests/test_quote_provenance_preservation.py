"""Bookmaker and clock evidence survives compact snapshots and offline archival."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.engine.odds_movement import quote_snapshot
from goal_analysis.normalization.odds_snapshots import normalize_saved_quotes
from goal_analysis.providers.the_stats_odds import bookmaker_key
from goal_analysis.quote_metadata import quote_metadata
from goal_analysis.storage import Database
from goal_analysis.storage.odds_snapshots import OddsSnapshots

NOW = datetime(2026, 10, 21, 22, tzinfo=UTC)


def native(name="Bét365", raw=None):
    return {"candidate_id": "native-quote", "fixture_id": "the_stats_api:mt_123",
        "market_key": "h2h", "selection_key": "home", "period": "FULL_TIME",
        "settlement": "REGULATION", "bookmaker": bookmaker_key(name), "bookmaker_name": name,
        "provider_bookmaker": name, "provider_market_id": "match_odds", "region": "DE", "currency": "EUR",
        "decimal_price": 2.25, "quote_provider": "the_stats_api", "quote_available": True,
        "quote_source_id": "https://api.thestatsapi.com/api/football/matches/mt_123/odds",
        "quote_source_sha256": "a" * 64, "quote_observed_at": (NOW - timedelta(minutes=2)).isoformat(),
        "quote_time_basis": "PROVIDER_RESPONSE_RECEIPT_NOT_QUOTE_TIME", **quote_metadata(raw, NOW)}


def archive(rows):
    return normalize_saved_quotes("the_stats_api", rows, observed_at=NOW,
        source_url=None, source_sha256="b" * 64)


@pytest.mark.parametrize("stamp,status", [(None, "MISSING"), ("", "INVALID"),
    ("provider-invalid-time", "INVALID"), ("2026-10-21T21:00:00", "INVALID"),
    ("2026-10-21T21:00:00Z", "KNOWN"), ("2026-10-22T21:00:00Z", "FUTURE")])
def test_native_bookmaker_and_original_clock_survive_snapshot_archive_and_store(tmp_path, stamp, status):
    candidate = native(raw=stamp)
    candidate["bookmaker_id"] = "native-book-id"
    original = deepcopy(candidate)
    snapshot = quote_snapshot([candidate], NOW)
    row, = snapshot["quotes"]
    for key in ("bookmaker", "bookmaker_name", "bookmaker_id", "provider_bookmaker", "quote_timestamp_raw",
                "quote_timestamp_status", "quoted_at", "quote_observed_at", "quote_source_sha256"):
        assert row[key] == original[key]
    result = archive(snapshot["quotes"])
    assert not result["unsupported"] and not result["rejected"]
    record, = result["records"]
    assert record["fixture_id"] == "mt_123" and record["fixture_identity_status"] == "NATIVE_URL"
    assert record["bookmaker"] == candidate["bookmaker"] and record["bookmaker_name"] == "Bét365"
    assert record["bookmaker_id"] == "native-book-id" and record["price_kind"] == "LAST_SEEN"
    assert record["quote_time_status"] == status
    assert (record["quoted_at"] is None) == (status != "KNOWN")
    assert record["raw"]["quote_timestamp_raw"] == stamp
    assert record["raw"]["quote_observed_at"] == original["quote_observed_at"]
    store = OddsSnapshots(Database(tmp_path / "odds.sqlite3"), clock=lambda: NOW + timedelta(seconds=1))
    store.initialize()
    parent = {"provider": "the_stats_api", "observed_at": NOW, "source_url": None,
        "source_sha256": "b" * 64, "source_ref": "compact-snapshot-test"}
    store.ingest(result["records"], **parent)
    assert store.ingest(result["records"], **parent)["quotes_created"] == 0
    saved, = store.quotes(as_of=NOW + timedelta(seconds=1))
    assert saved["bookmaker_name"] == "Bét365" and saved["quote_time_status"] == status
    assert saved["raw"]["quote_timestamp_status"] == original["quote_timestamp_status"]
    assert candidate == original


def test_snapshot_metadata_is_detached_even_for_malformed_structured_clock():
    candidate = native(raw={"unexpected": []})
    snapshot = quote_snapshot([candidate], NOW)
    snapshot["quotes"][0]["quote_timestamp_raw"]["unexpected"].append(1)
    assert candidate["quote_timestamp_raw"] == {"unexpected": []}
    assert archive(quote_snapshot([candidate], NOW)["quotes"])["records"][0]["quote_time_status"] == "INVALID"


def test_explicit_missing_raw_clock_does_not_borrow_quoted_or_receipt_time():
    candidate = native()
    candidate["quoted_at"] = (NOW - timedelta(minutes=1)).isoformat()
    record, = archive(quote_snapshot([candidate], NOW)["quotes"])["records"]
    assert record["quote_time_status"] == "MISSING" and record["quoted_at"] is None
    assert record["raw"]["quoted_at"] == candidate["quoted_at"]


def test_native_receipt_does_not_become_a_current_quote_clock():
    candidate = native()
    row, = quote_snapshot([candidate], NOW)["quotes"]
    assert row["quote_timestamp_status"] == "MISSING" and row["quoted_at"] is None
    assert row["quote_observed_at"] != NOW.isoformat()
    record, = archive([row])["records"]
    assert record["observed_at"] == NOW.isoformat(timespec="microseconds")
    assert record["raw"]["quote_observed_at"] == candidate["quote_observed_at"]
    assert record["source_sha256"] != record["raw"]["quote_source_sha256"]


def test_similar_bookmaker_slugs_remain_separate_without_merging_names():
    first, second = native("Book A"), native("Book-A")
    result = archive(quote_snapshot([first, second], NOW)["quotes"])
    assert len({r["bookmaker"] for r in result["records"]}) == 2
    assert {r["bookmaker_name"] for r in result["records"]} == {"Book A", "Book-A"}


def test_optional_native_book_identifier_is_not_invented_and_raw_price_kind_wins():
    candidate = native()
    candidate["price_kind"] = "OPENING"
    row, = quote_snapshot([candidate], NOW)["quotes"]
    assert "bookmaker_id" not in row and row["price_kind"] == "OPENING"
    record, = archive([row])["records"]
    assert record["bookmaker_id"] is None and record["price_kind"] == "OPENING"


@pytest.mark.parametrize("path", ["/api/football/matches/mt_123/odds", "/football/matches/mt_123/odds"])
def test_canonical_api_url_and_previous_archive_spelling_both_keep_native_identity(path):
    candidate = native()
    candidate["quote_source_id"] = "https://api.thestatsapi.com" + path
    assert archive([candidate])["records"][0]["fixture_id"] == "mt_123"


@pytest.mark.parametrize("url", ["https://unrelated.example/api/football/matches/mt_123/odds",
    "https://api.thestatsapi.com/x/api/football/matches/mt_123/odds",
    "https://api.thestatsapi.com/api/football/matches/123/odds"])
def test_unknown_identity_stays_unknown_without_name_or_id_guessing(url):
    candidate = native()
    candidate["quote_source_id"] = url
    assert archive([candidate])["records"][0]["fixture_identity_status"] == "UNKNOWN_NATIVE_ID"


def test_missing_display_name_uses_retained_native_bookmaker_instead_of_opaque_slug():
    candidate = native()
    candidate.pop("bookmaker_name")
    row, = quote_snapshot([candidate], NOW)["quotes"]
    assert "bookmaker_name" not in row
    assert archive([row])["records"][0]["bookmaker_name"] == candidate["provider_bookmaker"]


def test_old_snapshot_without_new_optional_fields_remains_readable():
    candidate = native()
    for key in ("bookmaker_name", "quote_timestamp_raw", "quote_timestamp_status"):
        candidate.pop(key)
    result = archive([candidate])
    assert not result["rejected"] and result["records"][0]["quoted_at"] is None


def test_native_provider_to_portfolio_ledger_and_dashboard_preserves_metadata(tmp_path):
    from test_portfolio_flow import RecordedReviewer
    from test_the_stats_primary import NOW as native_now
    from test_the_stats_primary import config, setup

    from goal_analysis.jobs.portfolio import run_portfolio
    from goal_analysis.portfolio_dashboard import ArthurState
    from goal_analysis.providers.the_stats_odds import TheStatsOddsFeed
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger

    transport, client, cache, _ = setup(tmp_path)
    feed = TheStatsOddsFeed(tmp_path, "eu", 20, client=client, clock=lambda: native_now)
    bundle = run_portfolio(config(), {}, client, feed, cache, native_now.date(),
        RecordedReviewer(), clock=lambda: native_now)
    report = bundle["artifacts"]["report"]
    candidates = {c["candidate_id"]: c for c in bundle["artifacts"]["candidate_input"]["candidates"]}
    assert report["odds_history_snapshot"]["quotes"]
    for row in report["odds_history_snapshot"]["quotes"]:
        original = candidates[row["candidate_id"]]
        assert row["bookmaker_name"] == original["bookmaker_name"]
        assert row["provider_bookmaker"] == original["provider_bookmaker"]
        assert row["quote_timestamp_raw"] is None and row["quoted_at"] is None
        assert row["quote_timestamp_status"] == "MISSING"
        assert row["quote_observed_at"] == original["quote_observed_at"]
    ledger = PortfolioLedger(tmp_path / "data/arthur/ledger.sqlite3", now=lambda: native_now)
    ledger.save_run(report)
    view = ArthurState(tmp_path).snapshot(report["date"])
    assert view["tickets"]
    expected = {c["candidate_id"]: c for c in report["odds_history_snapshot"]["quotes"]}
    for ticket in view["tickets"]:
        for leg in ticket.get("legs", []):
            if leg.get("quote_provider") == "the_stats_api":
                assert leg["bookmaker_name"] == expected[leg["candidate_id"]]["bookmaker_name"]
                assert leg["quote_observed_at"] == expected[leg["candidate_id"]]["quote_observed_at"]
                assert leg["quoted_at"] is None and leg["quote_timestamp_status"] == "MISSING"
    assert all("the-odds-api.com" not in call for call in transport.calls)


@pytest.mark.parametrize("raw", [None, "malformed-clock"])
def test_independent_monitor_keeps_the_same_provenance_fields(tmp_path, raw):
    from goal_analysis.jobs.odds_monitor import MonitorPolicy, OddsMonitor, monitor_history

    candidate = native(raw=raw)
    candidate["bookmaker_id"] = "native-optional-id"
    job = {"provider": "the_stats_api", "query_key": "native-mt123", "fixture_ids": [candidate["fixture_id"]],
        "kickoff_by_fixture": {candidate["fixture_id"]: (NOW + timedelta(hours=2)).isoformat()}, "estimated_cost": 1}
    monitor = OddsMonitor(tmp_path, policy=MonitorPolicy(daily_credit_limits={"the_stats_api": 10}), clock=lambda: NOW)
    outcome = monitor.tick([job], lambda _: {"quotes": [candidate], "actual_cost": 1, "remaining_quota": 9})
    assert outcome["queries"][0]["status"] == "UPDATED"
    row, = monitor_history(tmp_path, NOW)[0]["odds_history_snapshot"]["quotes"]
    for key in ("bookmaker_name", "bookmaker_id", "quote_timestamp_raw", "quote_timestamp_status", "quote_observed_at"):
        assert row[key] == candidate[key]
    record, = archive([row])["records"]
    assert record["bookmaker_name"] == candidate["bookmaker_name"]
    assert record["quoted_at"] is None and record["quote_time_status"] == ("MISSING" if raw is None else "INVALID")
