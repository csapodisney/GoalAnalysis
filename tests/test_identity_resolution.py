"""Offline boundaries for deterministic cross-provider fixture identity."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.normalization.identity import match_fixture

AS_OF = datetime(2026, 10, 20, 8, tzinfo=UTC)
KICKOFF = datetime(2026, 10, 20, 18, tzinfo=UTC)
COMPETITION = "comp_3039"


def fixture(**changes):
    return {"home_team": "Home United", "away_team": "Away City",
            "kickoff": KICKOFF.isoformat(), **changes}


def candidate(**changes):
    return {"id": "mt_1", "competition_id": COMPETITION, "season_id": "sn_1",
            "utc_date": KICKOFF.isoformat(), "status": "scheduled",
            "home_team": {"id": "tm_1", "name": "Home United"},
            "away_team": {"id": "tm_2", "name": "Away City"}, **changes}


def resolve(subject=None, rows=None, **changes):
    return match_fixture(fixture() if subject is None else subject,
                         [candidate()] if rows is None else rows,
                         competition_id=changes.pop("competition_id", COMPETITION),
                         as_of=changes.pop("as_of", AS_OF), **changes)


def assert_missing(result):
    assert result["status"] == "MISSING_LINK"
    assert result["row"] is None
    assert isinstance(result["reason"], str) and result["reason"]


def test_unique_exact_match_has_a_traceable_method_and_original_candidate():
    row = candidate()
    result = resolve(rows=[row])
    assert result["status"] == "MATCHED"
    assert result["row"] == row
    assert result["reason"] is None
    assert isinstance(result["method"], str) and result["method"]


def test_only_unicode_case_and_whitespace_normalization_is_allowed():
    result = resolve(subject=fixture(home_team="  CAFE\u0301   United\t",
                                     away_team="Away\nCity"),
                     rows=[candidate(home_team={"id": "tm_1", "name": "Caf\u00e9 united"})])
    assert result["status"] == "MATCHED"


@pytest.mark.parametrize("source_name,target_name", [
    ("Man Utd", "Manchester United"),
    ("Bayern Munchen", "Bayern M\u00fcnchen"),
    ("Home-United", "Home United"),
    ("Home.United", "Home United"),
    ("HomeUnited", "Home United"),
    ("Home & United", "Home and United"),
])
def test_aliases_accents_and_punctuation_cannot_establish_identity(source_name, target_name):
    assert_missing(resolve(subject=fixture(home_team=source_name),
                           rows=[candidate(home_team={"id": "tm_1", "name": target_name})]))


@pytest.mark.parametrize("start", [KICKOFF, "2026-10-20T20:00:00+02:00",
                                  "2026-10-20T18:00:00Z"])
def test_exact_kickoff_comparison_uses_utc_instant(start):
    assert resolve(subject=fixture(kickoff=start))["status"] == "MATCHED"


@pytest.mark.parametrize("difference", [timedelta(microseconds=1), timedelta(seconds=1),
                                       timedelta(minutes=1), timedelta(days=1)])
def test_nearby_kickoff_is_not_an_exact_match(difference):
    assert_missing(resolve(rows=[candidate(utc_date=(KICKOFF + difference).isoformat())]))


def test_repeated_dst_wall_clock_does_not_merge_distinct_instants():
    subject = fixture(kickoff="2026-10-25T02:30:00+02:00")
    assert_missing(resolve(subject=subject,
                           rows=[candidate(utc_date="2026-10-25T02:30:00+01:00")]))


@pytest.mark.parametrize("where", ["fixture", "candidate", "as_of"])
@pytest.mark.parametrize("bad", ["2026-10-20T18:00:00", KICKOFF.replace(tzinfo=None),
                                 "not-a-time", None, True])
def test_missing_naive_and_invalid_timestamps_are_not_identity_evidence(where, bad):
    if where == "fixture":
        result = resolve(subject=fixture(kickoff=bad))
    elif where == "candidate":
        result = resolve(rows=[candidate(utc_date=bad)])
    else:
        result = resolve(as_of=bad)
    assert_missing(result)


@pytest.mark.parametrize("remaining", [timedelta(minutes=15), timedelta(minutes=14),
                                       timedelta(), -timedelta(seconds=1)])
def test_lockout_is_inclusive_at_fifteen_minutes_and_includes_started_fixtures(remaining):
    start = AS_OF + remaining
    result = resolve(subject=fixture(kickoff=start.isoformat()),
                     rows=[candidate(utc_date=start.isoformat())])
    assert result["status"] == "LOCKED"
    assert result["row"] is None


def test_future_kickoff_just_outside_lockout_is_matchable():
    start = AS_OF + timedelta(minutes=15, microseconds=1)
    assert resolve(subject=fixture(kickoff=start),
                   rows=[candidate(utc_date=start.isoformat())])["status"] == "MATCHED"


def test_explicit_competition_and_home_away_order_are_required():
    assert_missing(resolve(rows=[candidate(competition_id="comp_4040")]))
    assert_missing(resolve(rows=[candidate(home_team={"id": "tm_2", "name": "Away City"},
                                           away_team={"id": "tm_1", "name": "Home United"})]))


@pytest.mark.parametrize("competition_id", [None, 3039, True, "", "3039", "tm_3039"])
def test_competition_id_is_a_typed_native_identifier(competition_id):
    assert_missing(resolve(competition_id=competition_id))


@pytest.mark.parametrize("field,bad", [("id", None), ("id", 1), ("id", True),
                                      ("id", "tm_1"), ("id", []),
                                      ("competition_id", 3039),
                                      ("competition_id", "tm_3039")])
def test_untyped_or_wrong_kind_candidate_ids_are_rejected(field, bad):
    assert_missing(resolve(rows=[candidate(**{field: bad})]))


@pytest.mark.parametrize("side", ["home_team", "away_team"])
@pytest.mark.parametrize("bad", [None, 1, True, "mt_1"])
def test_participant_ids_remain_typed_even_on_name_matching_path(side, bad):
    row = candidate()
    row[side]["id"] = bad
    assert_missing(resolve(rows=[row]))


@pytest.mark.parametrize("field,other_id", [("competition", "comp_4040"),
                                           ("season", "sn_2")])
def test_contradictory_direct_and_nested_provider_ids_are_not_overridden(field, other_id):
    assert_missing(resolve(rows=[candidate(**{field: {"id": other_id}})]))


def test_verified_team_ids_allow_names_to_differ_but_keep_match_context():
    rows = [candidate(home_team={"id": "tm_1", "name": "Different home label"},
                      away_team={"id": "tm_2", "name": "Different away label"})]
    assert resolve(rows=rows, verified_team_ids=("tm_1", "tm_2"))["status"] == "MATCHED"
    assert_missing(resolve(rows=rows, verified_team_ids=("tm_2", "tm_1")))
    assert_missing(resolve(rows=[candidate(competition_id="comp_4040")],
                           verified_team_ids=("tm_1", "tm_2")))
    assert_missing(resolve(rows=[candidate(utc_date=(KICKOFF + timedelta(seconds=1)).isoformat())],
                           verified_team_ids=("tm_1", "tm_2")))


@pytest.mark.parametrize("verified", [(1, "tm_2"), ("tm_1", True), ("mt_1", "tm_2"),
                                      ("tm_1", ""), ("tm_1",), ("tm_1", "tm_1")])
def test_verified_team_ids_must_be_a_valid_distinct_native_pair(verified):
    assert_missing(resolve(verified_team_ids=verified))


def test_ambiguous_distinct_fixture_ids_are_not_selected_by_order():
    rows = [candidate(), candidate(id="mt_2")]
    assert_missing(resolve(rows=rows))
    assert_missing(resolve(rows=list(reversed(rows))))


def test_identical_duplicate_rows_are_still_ambiguous():
    row = candidate()
    assert_missing(resolve(rows=[row, deepcopy(row)]))


@pytest.mark.parametrize("changes", [
    {"competition_id": "comp_4040"},
    {"utc_date": (KICKOFF + timedelta(hours=1)).isoformat()},
    {"home_team": {"id": "tm_9", "name": "Other club"}},
])
def test_conflicting_duplicate_provider_id_cannot_hide_behind_context_filtering(changes):
    rows = [candidate(), candidate(**changes)]
    assert_missing(resolve(rows=rows))
    assert_missing(resolve(rows=list(reversed(rows))))


def test_empty_candidate_set_is_a_missing_link():
    assert_missing(resolve(rows=[]))


@pytest.mark.parametrize("rows", [None, {}, "not-candidate-rows", [None], [True]])
def test_invalid_candidate_container_or_rows_do_not_raise(rows):
    # Call directly because the resolve helper reserves None for its default rows.
    assert_missing(match_fixture(fixture(), rows, competition_id=COMPETITION, as_of=AS_OF))


def test_matching_and_rejection_leave_caller_owned_inputs_unchanged():
    subject = fixture(home_team="  Home   United ")
    rows = [candidate()]
    before_subject, before_rows = deepcopy(subject), deepcopy(rows)
    assert resolve(subject=subject, rows=rows)["status"] == "MATCHED"
    assert subject == before_subject and rows == before_rows
    rows.append(candidate(id="mt_2"))
    before_rows = deepcopy(rows)
    assert_missing(resolve(subject=subject, rows=rows))
    assert subject == before_subject and rows == before_rows


def test_returned_match_is_detached_from_nested_provider_input():
    rows = [candidate()]
    original = deepcopy(rows)
    result = resolve(rows=rows)
    assert result["status"] == "MATCHED"
    result["row"]["home_team"]["name"] = "Caller annotation"
    assert rows == original
