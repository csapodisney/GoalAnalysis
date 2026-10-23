"""Explicit source-time meanings, missing evidence and UTC boundary ordering."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone

import pytest

from goal_analysis.normalization.temporal import project_times

RECEIPT = datetime(2026, 10, 18, 12, 0, 0, 123456, tzinfo=UTC)
INGESTED = RECEIPT + timedelta(days=2)
WITNESS = INGESTED + timedelta(minutes=5)
EVENT = RECEIPT - timedelta(days=1)


def result_source(**changes):
    return {"version_id": "a" * 64, "observed_at": RECEIPT.isoformat(),
        "ingested_at": INGESTED.isoformat(),
        "record_json": json.dumps({"provider": "the_stats_api", "kickoff": EVENT.isoformat(),
            "match_date": EVENT.date().isoformat(), "kickoff_local_time": None,
            "observed_at": RECEIPT.isoformat()}), **changes}


def weather_source(kind="FORECAST", *, target=None, issue=None, basis="FETCHED_AT"):
    target = RECEIPT + timedelta(days=1) if target is None else target
    snapshot = {"weather_kind": kind, "observed_at": RECEIPT.isoformat(),
        "target_time": target.isoformat(), "provider_issue_time": issue.isoformat() if issue else None,
        "kickoff": (RECEIPT + timedelta(days=1)).isoformat(), "time_basis": basis}
    return {"snapshot_id": "b" * 64, "observed_at": snapshot["observed_at"],
        "target_time": snapshot["target_time"], "provider_issue_time": snapshot["provider_issue_time"],
        "kickoff": snapshot["kickoff"], "snapshot_json": json.dumps(snapshot)}


def news_source(**changes):
    return {"published_at": EVENT.isoformat(), "event_at": None,
        "source_fetched_at": (RECEIPT - timedelta(minutes=1)).isoformat(),
        "observed_at": RECEIPT.isoformat(), **changes}


def test_result_preserves_all_original_times_and_only_explicit_witness_proves_availability():
    source = result_source()
    unproven = project_times("result", source)
    assert unproven["event_time"] == EVENT.isoformat()
    assert unproven["event_time_kind"] == "KICKOFF"
    assert unproven["observed_at"] == RECEIPT.isoformat()
    assert unproven["ingested_at"] == INGESTED.isoformat()
    assert unproven["published_at"] is unproven["available_at"] is None
    assert unproven["availability_basis"] == "UNPROVEN"
    assert unproven["missing_times"] == ["published_at", "available_at"]
    proven = project_times("result", source, available_at=WITNESS)
    assert proven["available_at"] == WITNESS.isoformat()
    assert proven["availability_basis"] == "SOURCE_PRESENCE_UPPER_BOUND"
    assert proven["missing_times"] == ["published_at"]
    assert proven["observed_time_basis"] == "SOURCE_RECEIPT"
    assert proven["ingested_time_basis"] == "RECORDED_LOCAL_INGESTION_WALL"
    assert proven["ingested_at"] != proven["available_at"]


def test_result_publication_is_not_guessed_from_kickoff_or_arbitrary_payload_fields():
    source = result_source()
    record = json.loads(source["record_json"])
    record.update(published_at=EVENT.isoformat(), updated_at=RECEIPT.isoformat())
    source.update(record_json=json.dumps(record), published_at=RECEIPT.isoformat(), available_at=WITNESS.isoformat())
    projected = project_times("result", source)
    assert projected["published_at"] is projected["available_at"] is None


def test_fd_keeps_date_and_unknown_timezone_wall_clock_without_fabricated_utc_event():
    source = result_source(record_json=json.dumps({"provider": "football_data_co_uk",
        "kickoff": None, "match_date": "2026-10-17", "kickoff_local_time": "23:30"}))
    projected = project_times("result", source, available_at=WITNESS)
    assert projected["event_time"] is projected["published_at"] is None
    assert projected["event_time_kind"] == "DATE_ONLY"
    assert projected["match_date"] == "2026-10-17"
    assert projected["kickoff_local_time"] == "23:30"
    assert projected["missing_times"] == ["event_time", "published_at"]
    record = json.loads(source["record_json"])
    record["kickoff"] = "2026-10-17T23:30:00Z"
    source["record_json"] = json.dumps(record)
    with pytest.raises(ValueError, match="UNPROVEN_FOOTBALL_DATA_UTC"):
        project_times("result", source)


def test_unknown_result_event_remains_unknown_and_legacy_ingestion_is_not_inferred():
    source = result_source(record_json={"kickoff": None}, ingested_at=None)
    source["record_json"]["ingested_at"] = INGESTED.isoformat()
    projected = project_times("result", source, available_at=WITNESS)
    assert projected["event_time_kind"] == "UNKNOWN" and projected["event_time"] is None
    assert projected["ingested_at"] is None
    assert projected["ingested_time_basis"] == "UNKNOWN"
    assert projected["missing_times"] == ["event_time", "published_at", "ingested_at"]


def test_date_only_finished_result_cannot_claim_a_future_utc_date_but_does_not_infer_local_kickoff():
    source = result_source(record_json={"provider": "football_data_co_uk", "kickoff": None,
        "match_date": "2026-10-19", "kickoff_local_time": "23:30"})
    with pytest.raises(ValueError, match="FUTURE_FINISHED_RESULT_DATE"):
        project_times("result", source)
    source["record_json"]["match_date"] = "2026-10-18"
    projected = project_times("result", source)
    assert projected["event_time"] is None
    assert projected["match_date"] == "2026-10-18" and projected["kickoff_local_time"] == "23:30"
    source["observed_at"] = "2026-10-18T00:30:00+02:00"  # Its UTC receipt date is October 17.
    with pytest.raises(ValueError, match="FUTURE_FINISHED_RESULT_DATE"):
        project_times("result", source)


@pytest.mark.parametrize("kind", ["FORECAST", "OBSERVATION", "REANALYSIS"])
def test_weather_target_is_typed_and_issue_time_is_only_publication_source(kind):
    source = weather_source(kind, target=EVENT)
    projected = project_times("weather", source, available_at=WITNESS)
    assert projected["event_time_kind"] == kind + "_TARGET"
    assert projected["event_time"] == EVENT.isoformat()
    assert projected["published_at"] is projected["ingested_at"] is None
    assert projected["observed_at"] == RECEIPT.isoformat()
    assert projected["missing_times"] == ["published_at", "ingested_at"]
    issued = weather_source(kind, target=EVENT, issue=EVENT - timedelta(hours=1))
    assert project_times("weather", issued)["published_at"] == (EVENT - timedelta(hours=1)).isoformat()


def test_forecast_target_may_be_future_while_reanalysis_and_observation_cannot():
    future = RECEIPT + timedelta(microseconds=1)
    assert project_times("weather", weather_source(target=future))["event_time"] == future.isoformat()
    for kind in ("OBSERVATION", "REANALYSIS"):
        with pytest.raises(ValueError, match="WEATHER_TARGET_AFTER_RECEIPT"):
            project_times("weather", weather_source(kind, target=future))
        assert project_times("weather", weather_source(kind, target=RECEIPT))["event_time"] == RECEIPT.isoformat()


@pytest.mark.parametrize("basis", ["REPORT_AVAILABLE_AT_UPPER_BOUND", "LOOKUP_ATTEMPT_AT", None])
def test_weather_preserves_report_upper_bound_and_unknown_basis_without_faking_exact_fetch(basis):
    source = weather_source(basis=basis)
    projected = project_times("weather", source)
    assert projected["observed_time_basis"] == (basis if basis else "UNKNOWN")
    assert projected["ingested_at"] is projected["available_at"] is None


def test_missing_weather_target_is_not_substituted_with_fixture_kickoff():
    source = weather_source()
    snapshot = json.loads(source["snapshot_json"])
    snapshot["target_time"] = source["target_time"] = None
    source["snapshot_json"] = json.dumps(snapshot)
    result = project_times("weather", source)
    assert result["event_time"] is None and result["event_time_kind"] == "FORECAST_TARGET"


def test_news_keeps_publication_fetch_and_model_result_receipt_separate():
    source = news_source()
    result = project_times("news", source, available_at=WITNESS)
    assert result["published_at"] == EVENT.isoformat()
    assert result["source_fetched_at"] == source["source_fetched_at"]
    assert result["observed_at"] == RECEIPT.isoformat()
    assert result["observed_time_basis"] == "RESULT_RECEIPT"
    assert result["event_time"] is result["ingested_at"] is None
    assert result["event_time_kind"] == "UNKNOWN"
    source["event_at"] = (EVENT - timedelta(hours=1)).isoformat()
    assert project_times("news", source)["event_time_kind"] == "SOURCE_EVENT"


@pytest.mark.parametrize("changes", [
    {"published_at": RECEIPT.isoformat()},
    {"source_fetched_at": (RECEIPT + timedelta(microseconds=1)).isoformat()},
    {"source_fetched_at": None}, {"published_at": None},
])
def test_news_publication_fetch_receipt_order_must_be_provable(changes):
    with pytest.raises(ValueError):
        project_times("news", news_source(**changes))


def test_raw_only_proves_original_retrieval_until_a_separate_presence_witness_exists():
    source = {"fetched_at": RECEIPT.isoformat(), "event_time": EVENT.isoformat(),
              "published_at": EVENT.isoformat()}
    result = project_times("raw", source)
    assert result["event_time"] is result["published_at"] is result["ingested_at"] is result["available_at"] is None
    assert result["observed_at"] == RECEIPT.isoformat()
    assert result["observed_time_basis"] == "FETCHED_AT"
    assert result["missing_times"] == ["event_time", "published_at", "ingested_at", "available_at"]


@pytest.mark.parametrize("value", [RECEIPT.replace(tzinfo=None), "2026-10-18T12:00:00", 0,
                                  True, False, 1.5, float("nan"), "invalid", "", None])
def test_naive_numeric_invalid_or_missing_required_receipt_is_rejected(value):
    with pytest.raises(ValueError):
        project_times("raw", {"fetched_at": value})


@pytest.mark.parametrize("field", ["ingested_at", "available_at"])
@pytest.mark.parametrize("value", [INGESTED.replace(tzinfo=None), "2026-10-20T12:00:00", 0, False, "bad"])
def test_optional_time_when_supplied_must_still_be_explicitly_aware(field, value):
    source = result_source()
    if field == "available_at":
        with pytest.raises(ValueError):
            project_times("result", source, available_at=value)
    else:
        source[field] = value
        with pytest.raises(ValueError):
            project_times("result", source)


def test_boundary_ordering_uses_full_microseconds_and_timezone_equivalence():
    with pytest.raises(ValueError, match="INGESTION_BEFORE_RECEIPT"):
        project_times("raw", {"fetched_at": RECEIPT, "ingested_at": RECEIPT - timedelta(microseconds=1)})
    with pytest.raises(ValueError, match="AVAILABILITY_BEFORE"):
        project_times("result", result_source(), available_at=INGESTED - timedelta(microseconds=1))
    with pytest.raises(ValueError, match="AVAILABILITY_BEFORE"):
        project_times("raw", {"fetched_at": RECEIPT}, available_at=RECEIPT - timedelta(microseconds=1))
    offset = timezone(timedelta(hours=2))
    projected = project_times("raw", {"fetched_at": RECEIPT.astimezone(offset), "ingested_at": RECEIPT},
                              available_at=RECEIPT.astimezone(offset))
    assert projected["observed_at"] == projected["ingested_at"] == projected["available_at"] == RECEIPT.isoformat()


def test_result_kickoff_is_strictly_before_receipt_and_never_uses_source_publication():
    for event in (RECEIPT, RECEIPT + timedelta(microseconds=1)):
        with pytest.raises(ValueError, match="RESULT_KICKOFF_NOT_BEFORE"):
            project_times("result", result_source(record_json={"kickoff": event.isoformat()}))
    before = RECEIPT - timedelta(microseconds=1)
    assert project_times("result", result_source(record_json={"kickoff": before.isoformat()}))["event_time"] == before.isoformat()


@pytest.mark.parametrize("namespace,field", [("result", "record_json"), ("weather", "snapshot_json")])
@pytest.mark.parametrize("payload", [None, [], "null", "[]", "{bad", '{"same":1,"same":2}', '{"value":NaN}'])
def test_embedded_source_json_must_be_an_unambiguous_finite_object(namespace, field, payload):
    source = result_source() if namespace == "result" else weather_source()
    source[field] = payload
    with pytest.raises(ValueError):
        project_times(namespace, source)


def test_sql_and_embedded_time_assertions_cannot_conflict():
    result = result_source(record_json={"kickoff": EVENT.isoformat(), "observed_at": INGESTED.isoformat()})
    with pytest.raises(ValueError, match="CONFLICTING_SOURCE_TIME"):
        project_times("result", result)
    weather = weather_source()
    weather["target_time"] = EVENT.isoformat()
    with pytest.raises(ValueError, match="CONFLICTING_SOURCE_TIME"):
        project_times("weather", weather)
    future_issue = weather_source(issue=RECEIPT + timedelta(microseconds=1))
    with pytest.raises(ValueError, match="PUBLICATION_AFTER_RECEIPT"):
        project_times("weather", future_issue)


@pytest.mark.parametrize("namespace,build", [("result", result_source), ("weather", weather_source),
                                           ("news", news_source), ("raw", lambda: {"fetched_at": RECEIPT})])
def test_input_and_returned_metadata_have_no_mutable_aliases(namespace, build):
    source = build()
    before = deepcopy(source)
    first = project_times(namespace, source)
    assert source == before
    first["missing_times"].append("annotation")
    assert "annotation" not in project_times(namespace, source)["missing_times"]


@pytest.mark.parametrize("namespace", [None, [], True, "fixture", "RESULT"])
def test_only_four_explicit_namespaces_are_allowed(namespace):
    with pytest.raises(ValueError, match="INVALID_TEMPORAL_NAMESPACE"):
        project_times(namespace, {})
