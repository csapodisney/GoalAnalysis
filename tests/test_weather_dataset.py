from __future__ import annotations

import copy
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents.weather_dataset import load_weather_dataset
from goal_analysis.storage.db import Database
from goal_analysis.storage.snapshots import SnapshotStore
from goal_analysis.storage.sports_weather import WeatherStore

KICKOFF = datetime(2026, 10, 1, 15, tzinfo=UTC)
CUTOFF = datetime(2026, 10, 18, tzinfo=UTC)


@pytest.fixture
def storage(tmp_path):
    database = Database(tmp_path / "data" / "goal_analysis.sqlite3")
    database.initialize()
    weather = WeatherStore(database)
    weather.initialize()
    return tmp_path, database, SnapshotStore(database, tmp_path / "data" / "raw"), weather


def fixture(identity=1, *, kickoff=KICKOFF, status="FT", goals=(2, 1)):
    return {
        "fixture": {"id": identity, "date": kickoff.isoformat(), "status": {"short": status},
                    "venue": {"id": 123, "name": "Stadium", "city": "Town"}},
        "teams": {"home": {"id": 100 + identity, "name": "Home"},
                  "away": {"id": 200 + identity, "name": "Away"}},
        "league": {"id": 39, "season": 2026, "type": "League"},
        "score": {"fulltime": {"home": goals[0], "away": goals[1]}},
        "goals": {"home": 9, "away": 8},
    }


def save_match(storage, *matches, observed=KICKOFF + timedelta(hours=3)):
    return storage[2].save(
        "api_football", "fixtures", json.dumps({"response": matches}).encode(),
        "application/json", observed, "https://v3.football.api-sports.io/fixtures?league=39",
    )


def save_weather(storage, identity=1, *, kind="REANALYSIS", observed=None, target=KICKOFF,
                 kickoff=KICKOFF, status="SOURCED", temperature=15, time_basis="FETCHED_AT"):
    observed = observed or (KICKOFF + timedelta(days=3) if kind == "REANALYSIS"
                            else KICKOFF - timedelta(hours=1))
    weather = {
        "fixture_id": f"api_football:{identity}", "provider": "open_meteo",
        "weather_kind": kind, "status": status, "forecast_time": target.isoformat(),
        "observed_at": observed.isoformat(), "temperature_c": temperature,
        "relative_humidity_pct": 70, "wind_speed_kmh": 8, "precipitation_mm": 0.2,
        "model": "era5" if kind == "REANALYSIS" else None,
        "source_url": "https://archive-api.open-meteo.com/v1/archive",
    }
    return storage[3].ingest(
        weather, {"fixture_id": f"api_football:{identity}", "kickoff": kickoff.isoformat()},
        source_run_id="test", time_basis=time_basis, raw_payload={"temperature": temperature},
    )


def test_read_only_exact_join_provenance_and_stable_hash(storage):
    saved = save_match(storage, fixture(), fixture(2))
    weather = save_weather(storage)
    before = storage[1].path.read_bytes()
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert storage[1].path.read_bytes() == before
    assert result == load_weather_dataset(storage[0], as_of=CUTOFF)
    assert result["coverage"]["completed_matches"] == 2
    assert result["coverage"]["matches_with_reanalysis"] == 1
    assert result['dataset_manifest']['version'] == 'training-dataset-v1'
    assert result['dataset_manifest']['row_count'] == len(result['matches'])
    assert len(result['dataset_manifest']['rows_sha256']) == 64
    first, second = result["matches"]
    assert first["home_team_id"] == "api_football:101"
    assert first["league_id"] == "api_football:39"
    assert first["source_content_hash"] == saved.content_hash
    assert first["weather"]["snapshot_id"] == weather["snapshot_id"]
    assert first["weather"]["model"] == "era5"
    assert first["weather"]["observed_at"] == (KICKOFF + timedelta(days=3)).isoformat()
    assert "weather" not in second


def test_missing_database_does_not_create_files(tmp_path):
    result = load_weather_dataset(tmp_path, as_of=CUTOFF)
    assert result["matches"] == []
    assert result["coverage"]["missing_database"] == 1
    assert list(tmp_path.iterdir()) == []


def test_reject_naive_cutoff(tmp_path):
    with pytest.raises(ValueError, match="invalid_timestamp"):
        load_weather_dataset(tmp_path, as_of=datetime(2026, 10, 18))  # noqa: DTZ001


def test_later_result_correction_does_not_leak_to_earlier_cutoff(storage):
    save_match(storage, fixture(goals=(2, 1)))
    save_match(storage, fixture(goals=(3, 1)), observed=CUTOFF + timedelta(hours=1))
    old = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert old["matches"][0]["home_goals"] == 2
    new = load_weather_dataset(storage[0], as_of=CUTOFF + timedelta(hours=2))
    assert new["matches"][0]["home_goals"] == 3
    assert old["data_hash"] != new["data_hash"]


def test_latest_incomplete_correction_does_not_fall_back_to_old_score(storage):
    save_match(storage, fixture())
    save_match(storage, fixture(status="CANC"), observed=KICKOFF + timedelta(days=1))
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert result["matches"] == []
    assert result["coverage"]["rejected_fixture:not_completed"] == 1


def test_equally_recent_conflicting_results_are_excluded(storage):
    save_match(storage, fixture(goals=(2, 1)))
    save_match(storage, fixture(goals=(3, 1)))
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert result["matches"] == []
    assert result["coverage"]["rejected_fixture:ambiguous_latest_version"] == 1


@pytest.mark.parametrize("status", ["FT", "AET", "PEN"])
def test_90_minute_score_never_uses_cumulative_extra_time_goals(storage, status):
    save_match(storage, fixture(status=status, goals=(0, 0)))
    match = load_weather_dataset(storage[0], as_of=CUTOFF)["matches"][0]
    assert match["home_goals"] == 0
    assert match["away_goals"] == 0
    assert match["result_period"] == "FULL_TIME_90_MINUTES"


@pytest.mark.parametrize("goals", [(None, None), (True, 1), (-1, 0), ("2", 1)])
def test_missing_or_invalid_fulltime_score_is_not_inferred(storage, goals):
    save_match(storage, fixture(status="AET", goals=goals))
    assert load_weather_dataset(storage[0], as_of=CUTOFF)["matches"] == []


def test_result_fetched_before_regulation_end_is_rejected(storage):
    save_match(storage, fixture(), observed=KICKOFF + timedelta(minutes=89))
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert result["coverage"]["rejected_fixture:result_observed_before_regulation_end"] == 1


def test_future_match_rejected_even_when_bad_provider_says_finished(storage):
    save_match(storage, fixture(kickoff=CUTOFF + timedelta(days=1)), observed=CUTOFF)
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert result["coverage"]["rejected_fixture:future_kickoff"] == 1


def test_unknown_competition_kind_and_missing_identity_are_not_invented(storage):
    first = fixture()
    first["league"].pop("type")
    second = fixture(2)
    second["teams"]["home"]["id"] = None
    save_match(storage, first, second)
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert len(result["matches"]) == 1
    assert result["matches"][0]["competition_type"] == "UNKNOWN"


def test_reanalysis_received_after_cutoff_is_not_available(storage):
    save_match(storage, fixture())
    save_weather(storage, observed=CUTOFF + timedelta(seconds=1))
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert "weather" not in result["matches"][0]
    assert result["coverage"]["reanalysis_after_cutoff"] == 1


@pytest.mark.parametrize("change,reason", [
    ({"target": KICKOFF + timedelta(hours=2)}, "weather_target_outside_match_hour"),
    ({"kickoff": KICKOFF + timedelta(minutes=30)}, "weather_kickoff_mismatch"),
    ({"temperature": 200}, "invalid_weather_value"),
])
def test_reanalysis_join_rejects_invalid_time_or_value(storage, change, reason):
    save_match(storage, fixture())
    save_weather(storage, **change)
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert "weather" not in result["matches"][0]
    assert result["coverage"][f"rejected_weather:{reason}"] == 1


def test_forecast_and_unknown_are_never_used_as_actual_weather(storage):
    save_match(storage, fixture())
    save_weather(storage, kind="FORECAST")
    save_weather(storage, status="UNKNOWN")
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert "weather" not in result["matches"][0]
    assert result["matches"][0]["prematch_weather"]["weather_kind"] == "FORECAST"


def test_prematch_forecast_selects_latest_before_15_minute_lockout(storage):
    save_match(storage, fixture())
    save_weather(storage, kind="FORECAST", observed=KICKOFF - timedelta(days=3), temperature=9)
    save_weather(storage, kind="FORECAST", observed=KICKOFF - timedelta(hours=2), temperature=10)
    save_weather(storage, kind="FORECAST", observed=KICKOFF - timedelta(minutes=15), temperature=12)
    save_weather(storage, kind="FORECAST", observed=KICKOFF - timedelta(minutes=14), temperature=19)
    save_weather(storage, kind="FORECAST", observed=KICKOFF + timedelta(hours=1), temperature=25)
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    prematch = result["matches"][0]["prematch_weather"]
    assert prematch["temperature_c"] == 12
    assert prematch["observed_at"] == (KICKOFF - timedelta(minutes=15)).isoformat()
    assert result["coverage"]["forecast_outside_prematch_window"] == 3


def test_report_upper_bound_only_eligible_if_prematch(storage):
    save_match(storage, fixture())
    save_weather(storage, kind="FORECAST", time_basis="REPORT_AVAILABLE_AT_UPPER_BOUND")
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert result["matches"][0]["prematch_weather"]["time_basis"] == (
        "REPORT_AVAILABLE_AT_UPPER_BOUND"
    )


def test_exact_id_join_never_uses_same_names_for_other_provider(storage):
    save_match(storage, fixture())
    save_weather(storage, identity=2)
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert "weather" not in result["matches"][0]
    assert result["coverage"]["reanalysis_without_matching_completed_fixture"] == 1


def test_corrupted_snapshot_is_rejected(storage):
    saved = save_match(storage, fixture())
    path = storage[0] / "data" / "raw" / saved.relative_path
    path.write_bytes(path.read_bytes() + b" ")
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert result["matches"] == []
    assert result["coverage"]["rejected_snapshot:fixture_snapshot_hash_or_size_mismatch"] == 1


def test_snapshot_escape_path_is_rejected(storage, tmp_path):
    saved = save_match(storage, fixture())
    with sqlite3.connect(storage[1].path) as connection:
        connection.execute("UPDATE raw_snapshots SET relative_path=? WHERE content_hash=?",
                           ("../../outside.bin", saved.content_hash))
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert result["matches"] == []
    assert result["coverage"]["rejected_snapshot:unsafe_or_oversize_snapshot"] == 1


def test_duplicate_fixture_in_richer_payload_is_not_double_counted(storage):
    match = fixture()
    save_match(storage, match)
    richer = copy.deepcopy(match)
    richer["events"] = [{"type": "Goal"}]
    save_match(storage, richer)
    result = load_weather_dataset(storage[0], as_of=CUTOFF)
    assert result["coverage"]["completed_matches"] == 1
