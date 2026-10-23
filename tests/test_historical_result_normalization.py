"""Historical source identities, score scopes and unknown local-time boundaries."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone

import pytest

from goal_analysis.normalization.historical_results import (
    normalize_api_result,
    normalize_archive_result,
)

NOW = datetime(2026, 10, 20, 12, 0, 0, 123456, tzinfo=UTC)
EVIDENCE = {
    "observed_at": NOW,
    "source_url": "https://v3.football.api-sports.io/fixtures?league=39&season=2026",
    "source_sha256": "a" * 64,
}
FIELDS = {
    "provider", "provider_id", "competition_id", "season_id", "home_id", "away_id",
    "home_team", "away_team", "kickoff", "match_date", "kickoff_local_time", "status",
    "score_scope", "home_goals", "away_goals", "half_home_goals", "half_away_goals",
    "observed_at", "source_url", "source_sha256", "raw",
}


def api_raw():
    kickoff = datetime(2026, 10, 18, 18, 30, tzinfo=UTC)
    return {
        "fixture": {"id": "000123", "date": kickoff.isoformat(),
                    "timestamp": int(kickoff.timestamp()), "status": {"short": "FT"}},
        "league": {"id": "039", "season": 2026},
        "teams": {"home": {"id": "001", "name": "Équipe-A"},
                  "away": {"id": 2, "name": "Team B"}},
        "goals": {"home": 5, "away": 4},
        "score": {"fulltime": {"home": 2, "away": 1},
                  "halftime": {"home": 1, "away": 0},
                  "extratime": {"home": 3, "away": 2},
                  "penalty": {"home": 5, "away": 4}},
        "unrecognized": {"facts": [0, False, None, "full raw retained"]},
    }


def stats_pair():
    raw = {
        "id": "mt_000123", "competition_id": "comp_0406", "season_id": "sn_0026",
        "status": "finished", "utc_date": "2026-10-18T20:30:00+02:00",
        "home_team": {"id": "tm_001", "name": "Équipe-A"},
        "away_team": {"id": "tm_002", "name": "Team B"},
        "score": {"regulation": {"home": 2, "away": 1},
                  "home": 5, "away": 4, "final_score": {"home": 5, "away": 4},
                  "after_extra_time": {"home": 3, "away": 2},
                  "penalty_shootout": {"home": 5, "away": 4}},
        "new_fields": {"arbitrary": [False, 0, None]},
    }
    row = {
        "provider": "the_stats_api", "source_record_key": raw["id"],
        "league_code": raw["competition_id"], "season_code": raw["season_id"],
        "match_date": "2026-10-18", "home_team": "Équipe-A", "away_team": "Team B",
        "full_time_home": 2, "full_time_away": 1, "full_time_result": "H",
        "half_time_home": None, "half_time_away": None, "half_time_result": None,
        "kickoff_local_time": None,
    }
    return row, raw


def fd_pair():
    raw = {
        "Div": "E0", "Date": "18/10/26", "Time": "19:30",
        "HomeTeam": "Équipe-A", "AwayTeam": "Team B",
        "FTHG": "2", "FTAG": "1", "FTR": "H", "HTHG": "1", "HTAG": "0", "HTR": "H",
        "B365H": "2.20", "Referee": "  Preserve me  ", "Unknown": "",
    }
    row = {
        "provider": "football_data_co_uk", "source_record_key": "football_data_co_uk:" + "b" * 64,
        "league_code": "E0", "season_code": "2627", "match_date": "2026-10-18",
        "home_team": "Équipe-A", "away_team": "Team B",
        "full_time_home": 2, "full_time_away": 1, "full_time_result": "H",
        "half_time_home": 1, "half_time_away": 0, "half_time_result": "H",
        "kickoff_local_time": "19:30",
    }
    return row, raw


def api(raw=None, **evidence):
    return normalize_api_result(api_raw() if raw is None else raw, **{**EVIDENCE, **evidence})


def archive(pair, **evidence):
    return normalize_archive_result(*pair, **{**EVIDENCE, **evidence})


@pytest.mark.parametrize("status", ["FT", "AET", "PEN"])
def test_api_native_ids_and_regulation_score_are_independent_of_terminal_totals(status):
    raw = api_raw()
    raw["fixture"]["status"]["short"] = status
    result = api(raw)
    assert set(result) == FIELDS
    assert result["provider"] == "api_football"
    assert (result["provider_id"], result["competition_id"], result["season_id"]) == (
        "123", "39", "2026")
    assert (result["home_id"], result["away_id"]) == ("1", "2")
    assert result["status"] == status
    assert result["score_scope"] == "REGULATION"
    assert (result["home_goals"], result["away_goals"]) == (2, 1)
    assert (result["half_home_goals"], result["half_away_goals"]) == (1, 0)
    assert result["kickoff"] == "2026-10-18T18:30:00.000000+00:00"
    assert result["observed_at"] == "2026-10-20T12:00:00.123456+00:00"


@pytest.mark.parametrize("score", [None, {}, {"fulltime": None},
                                  {"fulltime": {"home": 2}},
                                  {"fulltime": {"home": None, "away": 1}}])
def test_api_missing_or_partial_regulation_is_an_unknown_pair_without_total_fallback(score):
    raw = api_raw()
    raw["score"] = score
    result = api(raw)
    assert (result["home_goals"], result["away_goals"]) == (None, None)
    assert result["score_scope"] == "REGULATION"


@pytest.mark.parametrize("status", ["NS", "1H", "HT", "2H", "PST", "CANC", "ABD"])
def test_api_noncompleted_rows_have_the_job_filterable_reason(status):
    raw = api_raw()
    raw["fixture"]["status"]["short"] = status
    with pytest.raises(ValueError, match="^NOT_FINISHED$"):
        api(raw)


@pytest.mark.parametrize("value", [True, False, 1.0, 0, -1, "api_football:123", "١٢٣", " 123", None])
def test_api_native_ids_reject_typed_or_scoped_collisions(value):
    raw = api_raw()
    raw["fixture"]["id"] = value
    with pytest.raises(ValueError):
        api(raw)


def test_api_preserves_opaque_season_and_exact_unicode_names():
    raw = api_raw()
    raw["league"]["season"] = "2026/27-playoffs"
    raw["teams"]["home"]["name"] = "  E\u0301quipe-A  "
    result = api(raw)
    assert result["season_id"] == "2026/27-playoffs"
    assert result["home_team"] == "  E\u0301quipe-A  "


def test_api_timezone_equivalence_and_utc_calendar_day():
    raw = api_raw()
    raw["fixture"]["date"] = "2026-10-19T00:30:00+02:00"
    raw["fixture"]["timestamp"] = int(datetime(2026, 10, 18, 22, 30, tzinfo=UTC).timestamp())
    result = api(raw, observed_at=NOW.astimezone(timezone(timedelta(hours=-4))))
    assert result["match_date"] == "2026-10-18"
    assert result["kickoff"] == "2026-10-18T22:30:00.000000+00:00"
    assert result["observed_at"] == EVIDENCE["observed_at"].isoformat()


@pytest.mark.parametrize("kickoff", ["2026-10-18T18:30:00", "bad", None,
                                    NOW.isoformat(), (NOW + timedelta(microseconds=1)).isoformat()])
def test_api_naive_invalid_or_nonpast_kickoff_is_rejected(kickoff):
    raw = api_raw()
    raw["fixture"]["date"] = kickoff
    with pytest.raises(ValueError):
        api(raw)


def test_api_microsecond_before_observation_is_valid_and_timestamp_contradiction_is_not():
    raw = api_raw()
    kickoff = NOW - timedelta(microseconds=1)
    raw["fixture"]["date"] = kickoff.isoformat()
    raw["fixture"]["timestamp"] = int(kickoff.timestamp())
    assert api(raw)["kickoff"] == kickoff.isoformat()
    raw["fixture"]["timestamp"] += 1
    with pytest.raises(ValueError, match="CONFLICTING_KICKOFF_TIMESTAMP"):
        api(raw)


@pytest.mark.parametrize("value", [True, -1, 1.5, "2", [], float("inf")])
def test_api_malformed_regulation_score_is_not_missing_or_coerced(value):
    raw = api_raw()
    raw["score"]["fulltime"]["home"] = value
    with pytest.raises(ValueError):
        api(raw)


def test_api_identical_team_ids_and_impossible_half_time_are_rejected():
    raw = api_raw()
    raw["teams"]["away"]["id"] = "01"
    with pytest.raises(ValueError, match="IDENTICAL_TEAMS"):
        api(raw)
    raw = api_raw()
    raw["score"]["halftime"]["home"] = 3
    with pytest.raises(ValueError, match="HALF_TIME_EXCEEDS_REGULATION"):
        api(raw)


def test_stats_native_strings_and_regulation_remain_separate_from_final_score():
    row, raw = stats_pair()
    result = archive((row, raw))
    assert set(result) == FIELDS
    assert (result["provider_id"], result["competition_id"], result["season_id"]) == (
        "mt_000123", "comp_0406", "sn_0026")
    assert (result["home_id"], result["away_id"]) == ("tm_001", "tm_002")
    assert result["kickoff"] == "2026-10-18T18:30:00.000000+00:00"
    assert result["status"] == "finished"
    assert result["score_scope"] == "REGULATION"
    assert (result["home_goals"], result["away_goals"]) == (2, 1)
    assert (result["half_home_goals"], result["half_away_goals"]) == (None, None)


@pytest.mark.parametrize("regulation", [None, {}, {"home": 2}, {"away": 1}])
def test_stats_missing_regulation_is_unknown_even_with_final_and_aet_scores(regulation):
    row, raw = stats_pair()
    raw["score"]["regulation"] = regulation
    row.update(full_time_home=None, full_time_away=None, full_time_result=None)
    result = archive((row, raw))
    assert (result["home_goals"], result["away_goals"]) == (None, None)
    assert result["raw"]["score"]["final_score"] == {"home": 5, "away": 4}


@pytest.mark.parametrize("field,value", [
    ("source_record_key", "mt_999"), ("league_code", "comp_406"),
    ("season_code", "sn_26"), ("home_team", "Equipe-A"),
    ("away_team", "Team-B"), ("match_date", "2026-10-19"),
    ("full_time_home", 3), ("kickoff_local_time", "18:30"),
])
def test_stats_archive_and_raw_cannot_silently_disagree(field, value):
    row, raw = stats_pair()
    row[field] = value
    with pytest.raises(ValueError):
        archive((row, raw))


@pytest.mark.parametrize("field,value", [("status", "scheduled"), ("utc_date", "2026-10-18T18:30"),
                                         ("utc_date", NOW.isoformat()), ("id", "mt_١٢٣")])
def test_stats_status_time_and_typed_ids_are_strict(field, value):
    row, raw = stats_pair()
    raw[field] = value
    with pytest.raises(ValueError):
        archive((row, raw))


def test_stats_half_time_cannot_be_invented_from_archive_columns():
    row, raw = stats_pair()
    row["half_time_home"] = 1
    with pytest.raises(ValueError, match="UNPROVEN_HALF_TIME_SCOPE"):
        archive((row, raw))


def test_fd_keeps_native_key_unknown_utc_and_unverified_full_time_scope():
    row, raw = fd_pair()
    result = archive((row, raw))
    assert set(result) == FIELDS
    assert result["provider_id"] == row["source_record_key"]
    assert (result["competition_id"], result["season_id"]) == ("E0", "2627")
    assert (result["home_id"], result["away_id"], result["kickoff"]) == (None, None, None)
    assert result["kickoff_local_time"] == "19:30"
    assert result["status"] == "FINISHED"
    assert result["score_scope"] == "SOURCE_FULL_TIME_UNVERIFIED"
    assert (result["home_goals"], result["away_goals"]) == (2, 1)
    assert result["raw"] == raw
    assert result["raw"]["Referee"] == "  Preserve me  "


@pytest.mark.parametrize("local", [None, "", "1:05"])
def test_fd_missing_or_one_digit_hour_stays_local_without_utc_inference(local):
    row, raw = fd_pair()
    if local is None:
        raw.pop("Time")
    else:
        raw["Time"] = local
    row["kickoff_local_time"] = "01:05" if local == "1:05" else None
    result = archive((row, raw))
    assert result["kickoff"] is None
    assert result["kickoff_local_time"] == row["kickoff_local_time"]


@pytest.mark.parametrize("text,iso", [("29/07/16", "2016-07-29"), ("1/8/93", "1993-08-01"),
                                     ("18/10/2026", "2026-10-18"), ("1/8/92", "2092-08-01")])
def test_fd_two_digit_year_pivot_matches_existing_parser(text, iso):
    row, raw = fd_pair()
    row["match_date"], raw["Date"] = iso, text
    result = archive((row, raw), observed_at=datetime(2093, 1, 1, tzinfo=UTC))
    assert result["match_date"] == iso


@pytest.mark.parametrize("field,value", [
    ("Div", "E1"), ("Date", "19/10/26"), ("HomeTeam", "Equipe-A"),
    ("AwayTeam", "Team-B"), ("FTHG", "5"), ("FTR", "D"),
    ("HTHG", "3"), ("Time", "18:30"),
])
def test_fd_raw_row_identity_scores_and_time_must_agree(field, value):
    row, raw = fd_pair()
    raw[field] = value
    with pytest.raises(ValueError):
        archive((row, raw))


def test_fd_partial_half_score_is_preserved_without_inventing_the_other_side():
    row, raw = fd_pair()
    row.update(half_time_away=None, half_time_result=None)
    raw.update(HTAG="", HTR="")
    result = archive((row, raw))
    assert (result["half_home_goals"], result["half_away_goals"]) == (1, None)


def test_fd_same_day_completed_record_does_not_claim_a_utc_kickoff():
    row, raw = fd_pair()
    result = archive((row, raw), observed_at=datetime(2026, 10, 18, 1, tzinfo=UTC))
    assert result["kickoff"] is None
    with pytest.raises(ValueError, match="FUTURE_MATCH_DATE"):
        archive((row, raw), observed_at=datetime(2026, 10, 17, 23, tzinfo=UTC))


@pytest.mark.parametrize("build", [api_raw, stats_pair, fd_pair])
def test_full_raw_json_is_defensively_copied_and_normalization_never_mutates_inputs(build):
    original = build()
    before = deepcopy(original)
    result = api(original) if build is api_raw else archive(original)
    assert original == before
    raw = original if build is api_raw else original[1]
    assert result["raw"] == raw and result["raw"] is not raw
    result["raw"]["annotation"] = {"new": [1]}
    assert "annotation" not in raw
    raw["subsequent"] = [2]
    assert "subsequent" not in result["raw"]
    if build is api_raw:
        result["raw"]["unrecognized"]["facts"].append("changed")
        assert raw["unrecognized"]["facts"] == [0, False, None, "full raw retained"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "nonstring key"}, (1, 2), {1, 2}])
def test_nonfinite_or_nonjson_nested_raw_is_an_atomic_valueerror(value):
    raw = api_raw()
    raw["unrecognized"] = value
    with pytest.raises(ValueError, match="INVALID_RAW_JSON"):
        api(raw)


@pytest.mark.parametrize("field,value", [
    ("observed_at", NOW.replace(tzinfo=None)), ("observed_at", "bad"),
    ("source_url", "not a URL"), ("source_url", "https://user:pass@example.test/fixtures"),
    ("source_sha256", "not-a-hash"), ("source_sha256", None),
])
def test_invalid_evidence_is_rejected_without_using_match_time_as_availability(field, value):
    with pytest.raises(ValueError):
        api(**{field: value})
    with pytest.raises(ValueError):
        archive(stats_pair(), **{field: value})


def test_hash_case_normalization_and_actual_receipt_are_preserved():
    result = api(source_sha256="AB" * 32, observed_at=NOW.isoformat())
    assert result["source_sha256"] == "ab" * 32
    assert result["observed_at"] == NOW.isoformat()
    assert result["observed_at"] != result["kickoff"]


@pytest.mark.parametrize("raw", [None, [], "{}", 1, False])
def test_wrong_api_top_level_shape_is_valueerror(raw):
    with pytest.raises(ValueError):
        normalize_api_result(raw, **EVIDENCE)


@pytest.mark.parametrize("field,value", [("provider", "api_football"), ("full_time_home", True),
                                         ("match_date", "20261018"), ("season_code", None)])
def test_invalid_archive_shape_or_scope_is_a_record_error(field, value):
    row, raw = fd_pair()
    row[field] = value
    before = deepcopy((row, raw))
    with pytest.raises(ValueError):
        archive((row, raw))
    assert (row, raw) == before
