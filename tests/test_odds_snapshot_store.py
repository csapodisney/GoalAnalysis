"""Immutable native quote storage is evidence archival, not price selection."""
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.storage import Database
from goal_analysis.storage.odds_snapshots import TABLES, OddsSnapshots

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
RECEIPT = NOW - timedelta(hours=3)
QUOTED = RECEIPT - timedelta(minutes=2)


def stamp(value):
    return value.isoformat(timespec="microseconds")


def quote(**changes):
    return {
        "provider": "the_stats_api", "observed_at": stamp(RECEIPT),
        "source_url": "https://api.thestatsapi.com/api/football/odds",
        "source_sha256": "a" * 64, "fixture_id": "fixture-42", "bookmaker": "bet365",
        "market_key": "totals", "selection_key": "over", "period": "FULL_TIME",
        "settlement": "REGULATION_EXCLUDING_EXTRA_TIME_AND_PENALTIES", "line": "2.5",
        "region": "DE", "currency": "EUR", "native_market_id": "market_1",
        "native_outcome_id": "outcome_1", "decimal_price": "2.30", "price_kind": "CURRENT",
        "quoted_at": stamp(QUOTED), "quote_time_status": "KNOWN",
        "raw_path": "data[0].bookmakers[0].markets[0].outcomes[0]",
        "raw": {"native_price": "2.30", "provider_timestamp": stamp(QUOTED),
                "nested": [{"retained": True}]}, **changes,
    }


def setup_store(tmp_path):
    database = Database(tmp_path / "odds.sqlite3")
    with database.transaction() as connection:
        connection.execute("CREATE TABLE legacy_ticket (payload TEXT)")
        connection.execute("INSERT INTO legacy_ticket VALUES ('unchanged')")
    store = OddsSnapshots(database, clock=lambda: NOW)
    store.initialize()
    return store


def parent(record=None, **changes):
    record = quote() if record is None else record
    return {**{key: record[key] for key in ("provider", "observed_at", "source_url", "source_sha256")},
            "source_ref": "immutable-parent-1", **changes}


def counts(store):
    with store.database.connect() as connection:
        return {table: connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                for table in TABLES}


def test_native_snapshot_is_atomic_idempotent_and_does_not_modify_legacy_tables(tmp_path):
    store = setup_store(tmp_path)
    original = quote()
    frozen = deepcopy(original)
    first = store.ingest([original], **parent(original), issue_counts={"MISSING_NATIVE_FIELD": 1})
    assert first["sources_created"] == first["products_created"] == first["quotes_created"] == 1
    assert original == frozen
    before = counts(store)
    store.initialize()
    store.clock = lambda: NOW + timedelta(hours=1)
    replay = store.ingest([deepcopy(original)], **parent(original))
    assert replay["source_id"] == first["source_id"]
    assert replay["sources_created"] == replay["products_created"] == replay["quotes_created"] == 0
    assert counts(store) == before
    stored = store.quotes(as_of=NOW)[0]
    assert stored["raw"] == original["raw"]
    assert stored["raw_path"] == original["raw_path"]
    assert stored["source_ref"] == "immutable-parent-1"
    assert stored["ingested_at"] == stamp(NOW)
    with store.database.connect() as connection:
        assert connection.execute("SELECT payload FROM legacy_ticket").fetchone()[0] == "unchanged"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT issue_counts_json FROM sports_odds_sources").fetchone()[0] == (
            '{"MISSING_NATIVE_FIELD":1}')


def test_original_quote_receipt_and_actual_ingestion_are_not_interchangeable(tmp_path):
    store = setup_store(tmp_path)
    store.ingest([quote()], **parent())
    assert store.quotes(as_of=RECEIPT) == []
    assert store.quotes(as_of=NOW - timedelta(microseconds=1)) == []
    row = store.quotes(as_of=NOW)[0]
    assert row["quoted_at"] == stamp(QUOTED)
    assert row["observed_at"] == stamp(RECEIPT)
    assert row["ingested_at"] == stamp(NOW)
    assert len({row[field] for field in ("quoted_at", "observed_at", "ingested_at")}) == 3


@pytest.mark.parametrize(("field", "different"), [
    ("fixture_id", "fixture-43"), ("bookmaker", "pinnacle"), ("market_key", "team_totals"),
    ("selection_key", "under"), ("period", "FIRST_HALF"),
    ("settlement", "INCLUDING_EXTRA_TIME"), ("line", "3.5"),
    ("region", "GB"), ("currency", "GBP"),
    ("native_market_id", "market_2"), ("native_outcome_id", "outcome_2"),
])
def test_every_native_product_dimension_keeps_a_separate_product(tmp_path, field, different):
    store = setup_store(tmp_path)
    store.ingest([quote(), quote(**{field: different})], **parent())
    rows = store.quotes(as_of=NOW)
    assert len(rows) == 2
    assert len({row["product_id"] for row in rows}) == 2
    assert {row[field] for row in rows} == {quote()[field], different}


def test_same_bookmaker_label_under_different_providers_is_not_silently_merged(tmp_path):
    store = setup_store(tmp_path)
    for provider in ("the_stats_api", "api_football", "the_odds_api", "football_data_co_uk"):
        item = quote(provider=provider)
        store.ingest([item], **parent(item))
    rows = store.quotes(as_of=NOW)
    assert len(rows) == len({row["product_id"] for row in rows}) == 4
    assert {row["bookmaker"] for row in rows} == {"bet365"}
    assert len(store.quotes(as_of=NOW, provider="the_stats_api")) == 1


def test_same_time_conflicting_prices_are_both_retained_without_choosing_a_winner(tmp_path):
    store = setup_store(tmp_path)
    first, conflicting = quote(), quote(decimal_price="2.40", raw={"native_price": "2.40"})
    store.ingest([first, conflicting], **parent())
    rows = store.quotes(as_of=NOW)
    assert len(rows) == 2
    assert len({row["product_id"] for row in rows}) == 1
    assert {row["decimal_price"] for row in rows} == {"2.3", "2.4"}
    assert len({row["quoted_at"] for row in rows}) == 1
    assert len(store.quotes(as_of=NOW, product_id=rows[0]["product_id"])) == 2


def test_later_source_price_revision_preserves_old_as_of_and_old_quote(tmp_path):
    store = setup_store(tmp_path)
    store.ingest([quote()], **parent())
    later = NOW + timedelta(hours=1)
    store.clock = lambda: later
    updated = quote(decimal_price="2.60", observed_at=stamp(later), quoted_at=stamp(later),
                    source_sha256="b" * 64, raw={"native_price": "2.60"})
    store.ingest([updated], **parent(updated, source_ref="immutable-parent-2"))
    assert len(store.quotes(as_of=NOW)) == 1
    current = store.quotes(as_of=later)
    assert len(current) == 2
    assert len({row["product_id"] for row in current}) == 1
    assert {row["decimal_price"] for row in current} == {"2.3", "2.6"}


@pytest.mark.parametrize("status", ("MISSING", "INVALID", "FUTURE"))
def test_unknown_quote_time_is_retained_explicitly_and_never_filled_from_receipt(tmp_path, status):
    store = setup_store(tmp_path)
    raw_time = (NOW + timedelta(days=1)).isoformat() if status == "FUTURE" else "not-a-time"
    item = quote(quoted_at=None, quote_time_status=status, raw={"provider_timestamp": raw_time})
    store.ingest([item], **parent(item))
    row = store.quotes(as_of=NOW)[0]
    assert row["quoted_at"] is None
    assert row["quote_time_status"] == status
    assert row["raw"]["provider_timestamp"] == raw_time
    summary = store.summary()
    assert summary["mode"] == "SHADOW"
    assert summary["quote_time_status"] == [{"quote_time_status": status, "count": 1}]


def test_price_kinds_are_distinct_quotes_for_same_native_product(tmp_path):
    store = setup_store(tmp_path)
    kinds = {"CURRENT", "OPENING", "LAST_SEEN", "PRE_CLOSING", "CLOSING"}
    store.ingest([quote(price_kind=kind) for kind in sorted(kinds)], **parent())
    rows = store.quotes(as_of=NOW)
    assert len(rows) == 5
    assert len({row["product_id"] for row in rows}) == 1
    assert {row["price_kind"] for row in rows} == kinds


def test_decimal_price_precision_is_preserved_without_float_rounding(tmp_path):
    store = setup_store(tmp_path)
    exact = "1.123456789012345678901234567890123456789"
    store.ingest([quote(decimal_price=exact)], **parent())
    assert store.quotes(as_of=NOW)[0]["decimal_price"] == exact


def test_equivalent_decimal_formatting_has_one_product_without_float_rounding(tmp_path):
    store = setup_store(tmp_path)
    first, equivalent = quote(), quote(line="2.50", decimal_price="2.3")
    imported = store.ingest([first, equivalent], **parent())
    assert imported["products_created"] == imported["quotes_created"] == 1
    assert store.quotes(as_of=NOW)[0]["line"] == "2.5"


@pytest.mark.parametrize("value", (2.3, 2, True, None, "", "1", "0", "-2", "NaN", "Infinity", "-Infinity", "1e10000"))
def test_invalid_or_inexact_decimal_price_rolls_back_complete_source_batch(tmp_path, value):
    store = setup_store(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        store.ingest([quote(), quote(decimal_price=value)], **parent())
    assert all(count == 0 for count in counts(store).values())


@pytest.mark.parametrize("changes", [
    {"provider": "api_football"}, {"observed_at": stamp(RECEIPT - timedelta(seconds=1))},
    {"source_sha256": "b" * 64}, {"source_url": "https://other.example.test/odds"},
    {"fixture_id": ""}, {"bookmaker": None}, {"market_key": ""}, {"selection_key": ""},
    {"period": ""}, {"settlement": ""}, {"raw": []}, {"raw_path": None},
    {"price_kind": "ASSUMED_CURRENT"}, {"line": "NaN"}, {"line": 2.5}, {"line": "1e10000"},
])
def test_invalid_record_or_parent_provenance_cannot_leave_partial_products(tmp_path, changes):
    store = setup_store(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        store.ingest([quote(), quote(**changes)], **parent())
    assert all(count == 0 for count in counts(store).values())


@pytest.mark.parametrize("missing", ("provider", "fixture_id", "quoted_at", "raw"))
def test_missing_required_quote_field_rolls_back_earlier_valid_rows(tmp_path, missing):
    store = setup_store(tmp_path)
    incomplete = quote()
    incomplete.pop(missing)
    with pytest.raises(ValueError):
        store.ingest([quote(), incomplete], **parent())
    assert all(count == 0 for count in counts(store).values())


@pytest.mark.parametrize("changes", [
    {"quote_time_status": "UNKNOWN"}, {"quote_time_status": "KNOWN", "quoted_at": None},
    {"quoted_at": stamp(RECEIPT + timedelta(microseconds=1))},
    {"quoted_at": "2026-10-20T12:00:00"}, {"quoted_at": "not-a-time"},
    {"quote_time_status": "INVALID"}, {"quote_time_status": "MISSING"},
    {"quote_time_status": "FUTURE"},
])
def test_inconsistent_quote_time_status_is_rejected_atomically(tmp_path, changes):
    store = setup_store(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        store.ingest([quote(), quote(**changes)], **parent())
    assert all(count == 0 for count in counts(store).values())


def test_future_source_observation_rejects_before_any_rows_are_saved(tmp_path):
    store = setup_store(tmp_path)
    item = quote(observed_at=stamp(NOW + timedelta(microseconds=1)))
    with pytest.raises(ValueError, match="FUTURE"):
        store.ingest([item], **parent(item))
    assert all(count == 0 for count in counts(store).values())


@pytest.mark.parametrize("url", [
    "http://api.thestatsapi.com/api/football/odds",
    "https://user:secret@api.thestatsapi.com/api/football/odds",
    "https://api.thestatsapi.com/api/football/odds?api_key=secret",
    "https://api.thestatsapi.com/api/football/odds?token=secret",
    "https://api.thestatsapi.com/api/football/odds#private",
])
def test_secret_bearing_or_nonpublic_parent_reference_is_never_persisted(tmp_path, url):
    store = setup_store(tmp_path)
    item = quote(source_url=url)
    with pytest.raises(ValueError):
        store.ingest([item], **parent(item))
    assert all(count == 0 for count in counts(store).values())


@pytest.mark.parametrize("issue_counts", ([], False, "", {"BAD": True}, {"BAD": -1}, {"BAD": "1"}))
def test_issue_counts_must_be_an_explicit_nonnegative_integer_mapping(tmp_path, issue_counts):
    store = setup_store(tmp_path)
    with pytest.raises(ValueError):
        store.ingest([quote()], **parent(), issue_counts=issue_counts)
    assert all(count == 0 for count in counts(store).values())


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("operation", ("UPDATE", "DELETE"))
def test_all_odds_tables_enforce_append_only_at_database_boundary(tmp_path, table, operation):
    store = setup_store(tmp_path)
    store.ingest([quote()], **parent())
    before = counts(store)
    key = {"sports_odds_sources": "source_id", "sports_odds_products": "product_id",
           "sports_odds_quotes": "quote_id", "sports_odds_publications": "source_id"}[table]
    command = ("UPDATE " + table + " SET " + key + "=" + key
               if operation == "UPDATE" else "DELETE FROM " + table)
    with store.database.connect() as connection, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(command)
    assert counts(store) == before


def test_interrupted_streaming_batch_rolls_back_source_products_and_quotes(tmp_path):
    store = setup_store(tmp_path)

    def broken_stream():
        yield quote()
        raise OSError("simulated archive stream interruption")

    with pytest.raises(OSError, match="stream interruption"):
        store.ingest(broken_stream(), **parent())
    assert all(count == 0 for count in counts(store).values())
    assert store.ingest([quote()], **parent())["quotes_created"] == 1


def test_clock_floor_survives_restart_and_never_retimes_older_source(tmp_path):
    store = setup_store(tmp_path)
    store.ingest([quote()], **parent())
    restarted = OddsSnapshots(store.database, clock=lambda: NOW - timedelta(seconds=1))
    before = counts(store)
    with pytest.raises(ValueError, match="CLOCK_MOVED_BACKWARDS"):
        restarted.ingest([quote()], **parent())
    assert counts(store) == before
    assert store.quotes(as_of=NOW)[0]["ingested_at"] == stamp(NOW)


def test_quotes_are_detached_and_filters_do_not_mutate_immutable_storage(tmp_path):
    store = setup_store(tmp_path)
    item = quote()
    store.ingest([item], **parent(item))
    item["raw"]["nested"][0]["retained"] = False
    first = store.quotes(as_of=NOW, fixture_id="fixture-42")[0]
    first["raw"]["nested"][0]["retained"] = False
    assert store.quotes(as_of=NOW)[0]["raw"]["nested"][0]["retained"] is True
    assert store.quotes(as_of=NOW, fixture_id="different-fixture") == []
    assert store.quotes(as_of=NOW, product_id=first["product_id"])[0]["quote_id"] == first["quote_id"]


def test_sealed_source_cannot_receive_late_quotes_under_its_old_ingestion_time(tmp_path):
    store = setup_store(tmp_path)
    first = store.ingest([quote()], **parent())
    before = counts(store)
    later = NOW + timedelta(hours=1)
    store.clock = lambda: later
    late = quote(fixture_id="late-fixture", decimal_price="2.6", raw={"late_record": True})
    with pytest.raises(ValueError, match="SEALED_SOURCE_NEW_QUOTE"):
        store.ingest([quote(), late], **parent())
    assert counts(store) == before
    for cutoff in (NOW, later):
        rows = store.quotes(as_of=cutoff)
        assert len(rows) == 1
        assert rows[0]["fixture_id"] == "fixture-42"
        assert rows[0]["sealed_at"] == stamp(NOW)
    replay = store.ingest([quote()], **parent())
    assert replay["source_id"] == first["source_id"]
    assert replay["quotes_created"] == 0
    assert counts(store) == before


def test_unsealed_chunks_are_hidden_until_final_seal_without_backdated_availability(tmp_path):
    store = setup_store(tmp_path)
    first = store.ingest([quote()], **parent(), _publish=False)
    assert counts(store)["sports_odds_quotes"] == 1
    assert counts(store)["sports_odds_publications"] == 0
    assert store.quotes(as_of=NOW) == []
    second_at = NOW + timedelta(hours=1)
    store.clock = lambda: second_at
    second = quote(fixture_id="fixture-43", decimal_price="2.6", raw={"second_chunk": True})
    store.ingest([second], **parent(), _publish=False)
    assert counts(store)["sports_odds_quotes"] == 2
    assert store.quotes(as_of=second_at) == []
    sealed = NOW + timedelta(hours=2)
    store.clock = lambda: sealed
    store.seal(first["source_id"])
    assert store.quotes(as_of=sealed - timedelta(microseconds=1)) == []
    assert store.quotes(as_of=NOW) == []
    rows = store.quotes(as_of=sealed)
    assert len(rows) == 2
    assert {row["sealed_at"] for row in rows} == {stamp(sealed)}
    assert {row["ingested_at"] for row in rows} == {stamp(NOW)}
    assert {row["observed_at"] for row in rows} == {stamp(RECEIPT)}


def test_failed_chunk_rolls_back_its_rows_while_prior_staging_remains_hidden(tmp_path):
    store = setup_store(tmp_path)
    staged = store.ingest([quote()], **parent(), _publish=False)
    before = counts(store)
    later = NOW + timedelta(hours=1)
    store.clock = lambda: later
    with pytest.raises(ValueError):
        store.ingest([quote(fixture_id="would-have-been-new"), quote(decimal_price=True)],
                     **parent(), _publish=False)
    assert counts(store) == before
    assert store.quotes(as_of=later) == []
    sealed = later + timedelta(hours=1)
    store.clock = lambda: sealed
    store.seal(staged["source_id"])
    rows = store.quotes(as_of=sealed)
    assert len(rows) == 1
    assert rows[0]["fixture_id"] == "fixture-42"
    assert rows[0]["sealed_at"] == stamp(sealed)


def test_seal_is_idempotent_and_repeated_seal_preserves_original_publication_bound(tmp_path):
    store = setup_store(tmp_path)
    staged = store.ingest([quote()], **parent(), _publish=False)
    sealed = NOW + timedelta(hours=1)
    store.clock = lambda: sealed
    store.seal(staged["source_id"])
    before = counts(store)
    store.clock = lambda: sealed + timedelta(hours=1)
    store.seal(staged["source_id"])
    assert counts(store) == before
    assert before["sports_odds_publications"] == 1
    assert store.quotes(as_of=sealed)[0]["sealed_at"] == stamp(sealed)


def test_final_seal_clock_floor_persists_across_restart_for_new_sources(tmp_path):
    store = setup_store(tmp_path)
    staged = store.ingest([quote()], **parent(), _publish=False)
    sealed = NOW + timedelta(hours=2)
    store.clock = lambda: sealed
    store.seal(staged["source_id"])
    before = counts(store)
    restarted = OddsSnapshots(store.database, clock=lambda: NOW + timedelta(hours=1))
    another = quote(source_sha256="b" * 64)
    with pytest.raises(ValueError, match="CLOCK_MOVED_BACKWARDS"):
        restarted.ingest([another], **parent(another, source_ref="new-source"))
    assert counts(store) == before
