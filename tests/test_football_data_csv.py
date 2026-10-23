import csv
import io
from datetime import UTC, date, datetime

import pytest

from goal_analysis.providers.football_data_csv import PROVIDER, parse_csv

AS_OF = date(2026, 10, 18)
BASE = {
    "Div": "E0", "Date": "17/10/26", "HomeTeam": "Home", "AwayTeam": "Away",
    "FTHG": "2", "FTAG": "1", "FTR": "H",
}


def csv_bytes(*rows, encoding="utf-8"):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode(encoding)


def parse(content):
    return parse_csv(content, "E0", "2627", AS_OF)


def test_all_raw_columns_values_order_and_snapshot_types_are_preserved():
    row = BASE | {
        "HomeTeam": '  Home, "Town"  ', "Time": "9:05", "HTHG": "1", "HTAG": "0",
        "HTR": "H", "HS": "", "HC": "6", "B365H": "2.1200", "B365CH": "2.01",
        "B365>2.5": " 1.910 ", "B365C>2.5": "1.88", "AvgC<2.5": "2.10",
        "PSCH": "2.0", "AHh": "-0.5", "AHCh": "-0.75", "B365CAHH": "2.01",
        "BbMx>2.5": "2.2", "Bb1X2": "30", "FutureBookC<3.5": "1.234500",
    }
    parsed = parse(csv_bytes(row))
    record = parsed["records"][0]
    assert parsed["columns"] == list(row)
    assert record["raw"] == row
    assert list(record["raw"]) == list(row)
    assert record["home_team"] == 'Home, "Town"'
    assert record["match_date"] == "2026-10-17"
    assert record["kickoff_local_time"] == "09:05"
    assert record["half_time_home"] == 1
    assert record["half_time_away"] == 0
    assert record["half_time_result"] == "H"
    assert record["odds_snapshot_types"] == {
        "B365H": "pre_closing", "B365CH": "closing", "B365>2.5": "pre_closing",
        "B365C>2.5": "closing", "AvgC<2.5": "closing", "PSCH": "closing",
        "AHh": "pre_closing", "AHCh": "closing", "B365CAHH": "closing",
        "BbMx>2.5": "pre_closing", "Bb1X2": "pre_closing",
    }
    assert not any("utc" in field or "timestamp" in field for field in record)
    assert parsed["rejections"] == []


@pytest.mark.parametrize("day,expected", [
    ("1/9/26", "2026-09-01"), ("01/09/2026", "2026-09-01"),
    ("01/09/93", "1993-09-01"), ("01/09/00", "2000-09-01"),
    ("18/10/2026", "2026-10-18"),
])
def test_day_first_dates(day, expected):
    assert parse(csv_bytes(BASE | {"Date": day}))["records"][0]["match_date"] == expected


@pytest.mark.parametrize("encoding,home", [
    ("utf-8", "Ferencváros"), ("utf-8-sig", "Düsseldorf"), ("cp1252", "Nîmes"),
])
def test_encoding_roundtrip(encoding, home):
    row = BASE | {"HomeTeam": home}
    result = parse(csv_bytes(row, encoding=encoding))
    assert result["records"][0]["raw"] == row
    assert result["encoding"] == encoding


@pytest.mark.parametrize("fields,reason", [
    ({"Date": "19/10/26"}, "future_result_date"),
    ({"Date": "02/31/26"}, "invalid_date"),
    ({"Date": "2026-10-17"}, "invalid_date"),
    ({"Date": "29/02/2025"}, "invalid_date"),
    ({"Div": "E1"}, "league_mismatch"),
    ({"HomeTeam": ""}, "invalid_teams"),
    ({"AwayTeam": "Home"}, "invalid_teams"),
    ({"FTHG": "-1"}, "invalid_full_time_score"),
    ({"FTHG": "2.0"}, "invalid_full_time_score"),
    ({"FTAG": ""}, "invalid_full_time_score"),
    ({"FTR": ""}, "invalid_full_time_result"),
    ({"FTR": "D"}, "inconsistent_full_time_result"),
    ({"HTHG": "three"}, "invalid_half_time_score"),
    ({"HTHG": "3"}, "half_time_exceeds_full_time"),
    ({"HTAG": "2"}, "half_time_exceeds_full_time"),
    ({"HTHG": "1", "HTAG": "0", "HTR": "D"}, "inconsistent_half_time_result"),
    ({"HTR": "X"}, "invalid_half_time_result"),
    ({"Time": "24:01"}, "invalid_local_time"),
    ({"Time": "12:60"}, "invalid_local_time"),
    ({"Time": "12:00Z"}, "invalid_local_time"),
])
def test_invalid_rows_report_fixed_reasons_without_losing_other_rows(fields, reason):
    good = BASE | dict.fromkeys(fields, "")
    good.update(BASE)
    bad = good | fields
    result = parse(csv_bytes(good, bad, good))
    assert len(result["records"]) == 2
    assert result["rejections"] == [{"row_number": 3, "reason_code": reason}]
    assert result["rejection_counts"] == {reason: 1}


@pytest.mark.parametrize("fields", [{}, {"HTHG": "", "HTAG": "", "HTR": "", "Time": ""}])
def test_optional_blanks_are_not_zero_or_inferred(fields):
    result = parse(csv_bytes(BASE | fields))["records"][0]
    assert result["half_time_home"] is None
    assert result["half_time_away"] is None
    assert result["half_time_result"] is None
    assert result["kickoff_local_time"] is None


def test_partial_optional_halftime_is_preserved_without_inference():
    row = BASE | {"HTHG": "1", "HTAG": "", "HTR": ""}
    record = parse(csv_bytes(row))["records"][0]
    assert record["half_time_home"] == 1
    assert record["half_time_away"] is None
    assert record["half_time_result"] is None


def test_stable_namespaced_identity_ignores_result_and_odds_updates():
    first = parse(csv_bytes(BASE | {"B365H": "2.1"}))["records"][0]
    second = parse(csv_bytes(BASE | {"FTHG": "3", "B365H": "2.2"}))["records"][0]
    assert first["source_record_key"] == second["source_record_key"]
    assert first["source_record_key"].startswith(PROVIDER + ":")
    assert parse_csv(csv_bytes(BASE), "E0", "2526", AS_OF)["records"][0]["source_record_key"] != (
        first["source_record_key"]
    )


def test_duplicates_retained_for_caller_conflict_detection():
    records = parse(csv_bytes(BASE, BASE | {"FTHG": "3"}))["records"]
    assert len(records) == 2
    assert records[0]["source_record_key"] == records[1]["source_record_key"]
    assert records[0]["raw"] != records[1]["raw"]


def test_blank_rows_skipped_and_bad_width_reported():
    content = b"\r\n" + csv_bytes(BASE) + b"\r\n,,,,,,\r\nE0,17/10/26\r\n"
    result = parse(content)
    assert len(result["records"]) == 1
    assert result["blank_rows_skipped"] == 3
    assert result["rejections"] == [{"row_number": 6, "reason_code": "column_count_mismatch"}]


@pytest.mark.parametrize("content,reason", [
    (b"", "missing_csv_header"),
    (b" \n<html>error</html>", "html_instead_of_csv"),
    (b"<!DOCTYPE html>error", "html_instead_of_csv"),
    (b"Div,Date,Div\n", "duplicate_csv_headers"),
    (b"Div,Date, Date \n", "duplicate_csv_headers"),
    (b"Div,,Date\n", "blank_csv_header"),
    (b"Div,Date\n", "missing_required_headers"),
    (b'"Div,Date\n', "malformed_csv"),
    (b"Div,Date\x00\n", "invalid_csv_content"),
])
def test_malformed_files_raise_fixed_codes(content, reason):
    with pytest.raises(ValueError, match=f"^{reason}$"):
        parse(content)


def test_unclosed_row_quote_fails_entire_parse():
    with pytest.raises(ValueError, match="^malformed_csv$"):
        parse(csv_bytes(BASE) + b'E0,17/10/26,"Home,Away,1,1,D\n')


def test_header_whitespace_retained_and_handled_for_required_fields():
    row = {f" {key} ": value for key, value in BASE.items()}
    record = parse(csv_bytes(row))["records"][0]
    assert record["raw"] == row
    assert record["home_team"] == "Home"


@pytest.mark.parametrize("league,season", [("../E0", "2627"), ("E0", "2628"), ("E0", "2026")])
def test_invalid_caller_identity_rejected(league, season):
    with pytest.raises(ValueError):
        parse_csv(csv_bytes(BASE), league, season, AS_OF)


def test_datetime_as_of_rejected_to_avoid_implicit_timezone_assumptions():
    with pytest.raises(TypeError, match="as_of_must_be_date"):
        parse_csv(csv_bytes(BASE), "E0", "2627", datetime(2026, 10, 18, tzinfo=UTC))


def test_empty_trailing_unnamed_columns_are_explicitly_recorded():
    lines = csv_bytes(BASE | {"B365C>2.5": "1.9000"}).decode().splitlines()
    content = (lines[0] + ",,\r\n" + lines[1] + ",,\r\n").encode()
    parsed = parse(content)
    assert parsed["ignored_empty_trailing_columns"] == 2
    assert parsed["original_columns"] == parsed["columns"] + ["", ""]
    assert parsed["records"][0]["raw"] == BASE | {"B365C>2.5": "1.9000"}


def test_rows_may_omit_only_the_confirmed_empty_trailing_columns():
    lines = csv_bytes(BASE).decode().splitlines()
    content = (lines[0] + ",,\n" + lines[1] + "\n" + lines[1] + ",\n").encode()
    parsed = parse(content)
    assert len(parsed["records"]) == 2
    assert parsed["ignored_empty_trailing_columns"] == 2
    assert parsed["rejections"] == []


@pytest.mark.parametrize("cell", ["0.38", " ", "\t", "0"])
def test_any_nonempty_unnamed_value_quarantines_row_without_losing_good_rows(cell):
    lines = csv_bytes(BASE).decode().splitlines()
    content = (lines[0] + ",\n" + lines[1] + ",\n" + lines[1] + "," + cell + "\n").encode()
    parsed = parse(content)
    assert len(parsed["records"]) == 1
    assert parsed["records"][0]["raw"] == BASE
    assert parsed["rejections"] == [{"row_number": 3, "reason_code": "nonempty_unnamed_column"}]


def test_nonempty_unnamed_value_on_invalid_result_row_is_identified():
    lines = csv_bytes(BASE, BASE | {"FTR": "X"}).decode().splitlines()
    content = (lines[0] + ",\n" + lines[1] + ",\n" + lines[2] + ",0.38\n").encode()
    parsed = parse(content)
    assert len(parsed["records"]) == 1
    assert parsed["rejections"] == [{"row_number": 3, "reason_code": "nonempty_unnamed_column"}]


def test_unnamed_whitespace_on_otherwise_blank_row_is_not_discarded():
    lines = csv_bytes(BASE).decode().splitlines()
    content = (lines[0] + ",\n" + lines[1] + ",\n" + "," * len(BASE) + " \n").encode()
    parsed = parse(content)
    assert len(parsed["records"]) == 1
    assert parsed["rejections"] == [{"row_number": 3, "reason_code": "nonempty_unnamed_column"}]


def test_no_ignored_columns_metadata_for_regular_csv():
    parsed = parse(csv_bytes(BASE))
    assert parsed["original_columns"] == parsed["columns"] == list(BASE)
    assert parsed["ignored_empty_trailing_columns"] == 0
