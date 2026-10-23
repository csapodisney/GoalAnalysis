"""Source-native CSV price extraction without fabricated market timestamps."""
import copy
import json
from datetime import UTC, datetime

import pytest

from goal_analysis.normalization.football_data_odds import describe_column, iter_fd_quotes
from goal_analysis.storage import Database
from goal_analysis.storage.odds_snapshots import OddsSnapshots

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
OBSERVED = datetime(2026, 10, 17, 23, tzinfo=UTC)
URL = "https://www.football-data.co.uk/mmz4281/1617/G1.csv"
SHA = "a" * 64


def row(**cells):
    return {"provider": "football_data_co_uk", "source_record_key": "football_data_co_uk:" + "b" * 64,
        "league_code": "G1", "season_code": "1617", "match_date": "2016-09-17",
        "kickoff_local_time": "15:00", "raw": {"Date": "17/09/16", "Time": "15:00",
            "HomeTeam": "Home", "AwayTeam": "Away", "FTHG": "2", "FTAG": "1", **cells}}


def quotes(source, **kwargs):
    return list(iter_fd_quotes(source, observed_at=kwargs.get("observed_at", OBSERVED),
        source_url=kwargs.get("source_url", URL), source_sha256=kwargs.get("source_sha256", SHA)))


def test_native_bookmaker_prefixes_are_distinct_and_never_human_alias_guesses():
    values = quotes(row(BFD="2.10", BFDH="2.20", BFEH="2.30", PH="2.40", PSH="2.50"))
    assert [(value["bookmaker"], value["selection_key"]) for value in values] == [
        ("BF", "draw"), ("BFD", "home"), ("BFE", "home"), ("P", "home"), ("PS", "home")]
    assert all(value["quote_type"] == "BOOKMAKER" for value in values)
    assert all(value["region"] is value["currency"] is None for value in values)


def test_handicap_lines_bookmaker_counts_and_unknown_columns_are_not_prices():
    source = row(AHh="2.50", AHCh="3.50", BbAHh="4.50", Bb1X2="25", BbOU="20", BbAH="15",
        FutureBookH="10", B365AH="4.00", B365H="2.00")
    assert [quote["bookmaker"] for quote in quotes(source)] == ["B365"]
    for key in ("AHh", "AHCh", "BbAHh", "Bb1X2", "BbOU", "BbAH", "FutureBookH", "B365AH"):
        assert describe_column(key) is None


def test_aggregate_prices_stay_explicitly_distinct_from_bookmaker_quotes():
    values = quotes(row(MaxH="2.50", AvgH="2.20", BbMxH="2.40", BbAvH="2.10", B365H="2.30"))
    assert [(value["bookmaker"], value["quote_type"], value["aggregate_kind"]) for value in values] == [
        ("Max", "AGGREGATE", "MAXIMUM"), ("Avg", "AGGREGATE", "AVERAGE"),
        ("BbMx", "AGGREGATE", "MAXIMUM"), ("BbAv", "AGGREGATE", "AVERAGE"),
        ("B365", "BOOKMAKER", None)]


def test_missing_or_invalid_price_cells_are_not_interpreted_as_offers():
    for value in (None, False, True, 2.5, "", " ", "NaN", "Infinity", "-Infinity", "1", "0.5",
                  "-2", "2,50", "1_000", "1e1000000000"):
        assert quotes(row(B365H=value)) == []
    exact = "1.0000000000000000000000000000000000000001"
    assert quotes(row(B365H=exact))[0]["decimal_price"] == exact
    assert quotes(row(B365H="2.5000"))[0]["decimal_price"] == "2.5000"


def test_asian_handicap_line_uses_its_source_period_and_home_reference():
    values = quotes(row(AHh="-0.25", AHCh="+0.75", BbAHh="-1.5",
        B365AHH="1.95", B365AHA="1.85", B365CAHH="2.05", BbMxAHH="2.10"))
    assert [(value["bookmaker"], value["selection_key"], value["price_kind"], value["line"])
            for value in values] == [
        ("B365", "home", "PRE_CLOSING", "-0.25"),
        ("B365", "away", "PRE_CLOSING", "-0.25"),
        ("B365", "home", "CLOSING", "0.75"),
        ("BbMx", "home", "PRE_CLOSING", "-1.5")]
    assert all(value["line_reference"] == "HOME_TEAM_HANDICAP" for value in values)
    assert values[2]["raw"] == {"B365CAHH": "2.05", "AHCh": "+0.75"}


def test_missing_or_invalid_handicap_line_stays_unknown_instead_of_becoming_zero():
    for cells in ({"B365AHH": "1.95"}, {"B365AHH": "1.95", "AHh": "NaN"},
                  {"B365AHH": "1.95", "AHh": "1e100000000"}):
        value = quotes(row(**cells))[0]
        assert value["line"] is None and value["line_status"] == "MISSING"
        assert value["line_reference"] == "HOME_TEAM_HANDICAP"


def test_totals_line_is_explicit_and_all_quote_timestamps_remain_unknown():
    values = quotes(row(**{"B365>2.5": "1.90", "B365C<2.5": "2.00"}))
    assert [(value["selection_key"], value["line"], value["price_kind"]) for value in values] == [
        ("over", "2.5", "PRE_CLOSING"), ("under", "2.5", "CLOSING")]
    assert all(value["quoted_at"] is None and value["quote_time_status"] == "MISSING" for value in values)
    assert all(value["observed_at"] == OBSERVED.isoformat(timespec="microseconds") for value in values)
    assert all(value["period"] == "FULL_TIME" and value["settlement"] == "SOURCE_FULL_TIME_UNVERIFIED"
               for value in values)
    assert all("kickoff" not in value for value in values)


def test_raw_json_archive_shape_and_original_column_spelling_are_preserved():
    source = row(**{" B365H ": " 2.50 "})
    raw = source.pop("raw")
    source["raw_json"] = json.dumps(raw)
    before = copy.deepcopy(source)
    value = quotes(source)[0]
    assert value["decimal_price"] == "2.50" and value["raw"] == {" B365H ": " 2.50 "}
    assert value["raw_path"].endswith("/raw/ B365H ")
    assert value["fixture_id"] == source["source_record_key"] and source == before


def test_ambiguous_stripped_headers_and_invalid_parent_proofs_are_rejected():
    with pytest.raises(ValueError, match="AMBIGUOUS_CSV_COLUMN"):
        quotes(row(**{"B365H": "2.00", " B365H ": "2.10"}))
    for kwargs in ({"source_url": "https://example.org/data.csv"},
                   {"source_url": URL + "?api_key=synthetic"}, {"source_sha256": "bad"}):
        with pytest.raises(ValueError):
            quotes(row(B365H="2.00"), **kwargs)
    source = row(B365H="2.00")
    source["source_record_key"] = "Home-Away"
    with pytest.raises(ValueError, match="SOURCE_NATIVE_FIXTURE_KEY_REQUIRED"):
        quotes(source)


def test_csv_price_kinds_share_product_but_keep_separate_source_columns(tmp_path):
    database = Database(tmp_path / "isolated.sqlite3")
    store = OddsSnapshots(database, clock=lambda: NOW)
    store.initialize()
    values = quotes(row(B365H="2.00", B365CH="1.90"))
    result = store.ingest(values, provider="football_data_co_uk", observed_at=OBSERVED,
        source_url=URL, source_sha256=SHA, source_ref="rawcsv:" + SHA)
    assert result["products_created"] == 1 and result["quotes_created"] == 2
    repeated = store.ingest(values, provider="football_data_co_uk", observed_at=OBSERVED,
        source_url=URL, source_sha256=SHA, source_ref="rawcsv:" + SHA)
    assert all(repeated[key] == 0 for key in ("sources_created", "products_created", "quotes_created"))
    assert repeated["source_id"] == result["source_id"]
