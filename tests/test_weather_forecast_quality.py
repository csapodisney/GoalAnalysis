"""Bounded pure synthetic controls; positive content is not store evidence."""

import math
import socket
import sqlite3
import subprocess
import urllib.request
from copy import deepcopy
from unittest.mock import Mock

import pytest

from goal_analysis.jobs import native_forecast_capture, weather_feature_capture
from goal_analysis.learning import weather_forecast_quality as module
from goal_analysis.normalization.training_datasets import sha, snapshot
from goal_analysis.storage import Database

NOW = "2026-10-25T20:00:00+00:00"
KICKOFF = "2026-10-25T15:00:00+00:00"


@pytest.fixture(autouse=True)
def forbidden_io(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("FORBIDDEN_IO_MODEL_OR_WRITER"))
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(Database, "__init__", forbidden)
    monkeypatch.setattr(native_forecast_capture, "capture", forbidden)
    monkeypatch.setattr(weather_feature_capture, "capture", forbidden)
    yield forbidden
    forbidden.assert_not_called()


def source(name="1", p=.7, outcome=1, rain=.2):
    fixture = "the_stats_api:mt_" + name
    forecast = {"forecast_id": sha("forecast:" + name), "fixture_id": fixture, "kickoff": KICKOFF,
        "profile_id": "native_shadow_gauss", "strategy_version": "TESTONLY-source-version", "model_version": "gauss-test-v1",
        "model_as_of": "2026-10-25T11:00:00+00:00", "created_at": "2026-10-25T11:01:00+00:00",
        "saved_at": "2026-10-25T11:02:00+00:00", "market_key": "h2h", "selection_key": "home",
        "period": "FULL_TIME", "settlement": module.SETTLEMENT, "line": None,
        "probability": p, "probability_kind": "ORIGINAL_NUMERIC_MODEL_ESTIMATE", "run_sha256": sha("run:" + name),
        "source_receipt_ids": [sha("original-source:" + name)], "result_receipts": []}
    if outcome is not None:
        forecast["result_receipts"] = [{"receipt_id": sha("result:" + name), "source_sha256": sha("raw-result:" + name),
            "outcome": outcome, "observed_at": "2026-10-25T17:00:00+00:00", "available_at": "2026-10-25T17:01:00+00:00"}]
    target = {"fixture_id": fixture, "provider_fixture_id": "mt_" + name, "provider_league_id": "comp_1",
        "provider_season_id": "sn_1", "provider_home_team_id": "tm_1", "provider_away_team_id": "tm_2", "kickoff": KICKOFF}
    context = {"fixture_id": fixture, "kickoff": KICKOFF, "status": "FORECAST_CONTEXT_VERIFIED_SHADOW",
        "forecast": {"weather_kind": "FORECAST", "status": "SOURCED", "target_time": KICKOFF,
            "observed_at": "2026-10-25T11:30:00+00:00", "temperature_c": 12., "wind_speed_kmh": 20.,
            "precipitation_mm": rain, "wind_gusts_kmh": None, "relative_humidity_pct": 65.},
        "forecast_receipt": {"times": {"available_at": "2026-10-25T11:31:00+00:00"}},
        "target_receipt": {"times": {"available_at": "2026-10-25T11:50:00+00:00"}}}
    pair = {"pair_id": sha("pair:" + name), "pair_receipt_id": sha("pair-receipt:" + name),
        "saved_at": "2026-10-25T12:00:00+00:00", "fixture_id": fixture, "kickoff": KICKOFF,
        "baseline": {k: deepcopy(v) for k, v in forecast.items() if k != "result_receipts"}, "target": target,
        "weather_context": context, "weather_context_sha256": sha(context), "weather_model_probability": None,
        "probability_adjustment": 0, "ranking_multiplier": 1., "live_decision_effect": False}
    return pair, forecast


def bundles(pairs, forecasts, *, pair_gaps=None, forecast_gaps=None):
    return ({"dataset": snapshot(pairs, role=module.PAIR_ROLE, as_of=NOW, recipe={"version": "TESTONLY-not-store"}, lineage={}),
             "gaps": pair_gaps or {}},
            {"dataset": snapshot(forecasts, role=module.FORECAST_ROLE, as_of=NOW, recipe={"version": "TESTONLY-not-store"}, lineage={}),
             "gaps": forecast_gaps or {}})


def run(pairs, forecasts, **gaps):
    inputs = bundles(pairs, forecasts, **gaps)
    before = deepcopy(inputs)
    result = module.summarize_source_content(*inputs, as_of=NOW)
    assert inputs == before
    assert result["source_authentication"] == module.PURE_AUTH
    assert result["weather_stratum_basis"] == "PREMATCH_FORECAST_FEATURE_PROXY_NOT_REALIZED_WEATHER"
    assert all(row["weather_stratum_basis"] == result["weather_stratum_basis"] for row in result["dataset"]["rows"])
    assert result["heldout_weather_comparison_complete"] is result["live_promotion_allowed"] is False
    assert result["decision_effect"] == "NONE"
    assert sum(result["accounting"]["partitions"].values()) == len(pairs)
    assert result["sha256"] == sha({k: v for k, v in result.items() if k != "sha256"})
    return result


def test_known_baseline_scores_all_wins_and_losses_without_closing_quotes():
    a, x = source("1", .7, 1, .2)
    b, y = source("2", .2, 0, 0.)
    result = run([a, b], [x, y])
    assert result["accounting"]["partitions"] == {"SCORED": 2}
    assert result["baseline_scores"]["brier_binary"] == pytest.approx((.09 + .04) / 2)
    assert result["baseline_scores"]["log_loss_binary"] == pytest.approx((-math.log(.7) - math.log(.8)) / 2)
    assert {row["outcome"] for row in result["dataset"]["rows"]} == {"WON", "LOST"}
    assert {row["weather_stratum"] for row in result["weather_conditioned_groups"]} == {"WET", "DRY"}
    assert all(row["weather_model_probability"] is None for row in result["dataset"]["rows"])


def test_weather_changes_condition_dataset_and_summary_without_changing_baseline_probability():
    pair, forecast = source()
    first = run([pair], [forecast])
    pair["weather_context"]["forecast"].update(precipitation_mm=0., wind_speed_kmh=35.)
    pair["weather_context_sha256"] = sha(pair["weather_context"])
    second = run([pair], [forecast])
    assert first["baseline_scores"] == second["baseline_scores"]
    assert first["dataset"]["manifest"]["dataset_id"] != second["dataset"]["manifest"]["dataset_id"]
    assert second["weather_conditioned_groups"][0]["weather_stratum"] == "DRY"
    assert second["weather_feature_summary"]["wind_speed_kmh"]["mean"] == 35
    assert second["dataset"]["rows"][0]["probability"] == .7


@pytest.mark.parametrize("field,value", [("probability", .99), ("model_version", "foreign-model"),
    ("strategy_version", "foreign-strategy"), ("model_as_of", "2026-10-25T10:00:00+00:00"),
    ("run_sha256", "f" * 64), ("source_receipt_ids", ["e" * 64])])
def test_same_forecast_id_with_changed_original_content_does_not_join(field, value):
    pair, forecast = source()
    forecast[field] = value
    result = run([pair], [forecast])
    assert result["pair_occurrences"][0]["reason"] == "EXACT_ORIGINAL_BASELINE_CONTENT_MISMATCH"
    assert result["baseline_scores"]["eligible_selections"] == 0


@pytest.mark.parametrize("section,field,value", [("weather_context", "fixture_id", "the_stats_api:mt_999"),
    ("target", "fixture_id", "the_stats_api:mt_999"), ("target", "kickoff", "2026-10-25T16:00:00+00:00")])
def test_cross_fixture_or_kickoff_is_local_exclusion(section, field, value):
    pair, forecast = source()
    pair[section][field] = value
    pair["weather_context_sha256"] = sha(pair["weather_context"])
    assert run([pair], [forecast])["pair_occurrences"][0]["reason"] == "EXACT_WEATHER_TARGET_IDENTITY_REQUIRED"


def test_changed_weather_content_without_matching_hash_rejected():
    pair, forecast = source()
    pair["weather_context"]["forecast"]["temperature_c"] = 30.
    assert run([pair], [forecast])["pair_occurrences"][0]["reason"] == "WEATHER_CONTEXT_SOURCE_HASH_OR_STATUS_MISMATCH"


@pytest.mark.parametrize("change", ["pair_future", "pair_lockout", "weather_future", "weather_stale", "target_future", "model_cutoff"])
def test_future_stale_or_late_source_clocks_are_not_prematch_evidence(change):
    pair, forecast = source()
    if change == "pair_future":
        pair["saved_at"] = "2026-10-25T21:00:00+00:00"
    elif change == "pair_lockout":
        pair["saved_at"] = "2026-10-25T14:46:00+00:00"
    elif change == "weather_future":
        pair["weather_context"]["forecast_receipt"]["times"]["available_at"] = "2026-10-25T13:00:00+00:00"
    elif change == "weather_stale":
        pair["weather_context"]["forecast"]["observed_at"] = "2026-10-25T05:00:00+00:00"
    elif change == "target_future":
        pair["weather_context"]["target_receipt"]["times"]["available_at"] = "2026-10-25T13:00:00+00:00"
    else:
        forecast["model_as_of"] = "2026-10-25T13:00:00+00:00"
        pair["baseline"]["model_as_of"] = forecast["model_as_of"]
    pair["weather_context_sha256"] = sha(pair["weather_context"])
    assert run([pair], [forecast])["pair_occurrences"][0]["reason"] == "PREMATCH_SOURCE_AVAILABILITY_OR_TARGET_CUTOFF_MISMATCH"


def test_no_outcome_and_future_outcome_stay_pending_with_original_p():
    a, x = source("1", outcome=None)
    b, y = source("2")
    y["result_receipts"][0]["available_at"] = "2026-10-25T21:00:00+00:00"
    result = run([a, b], [x, y])
    assert result["accounting"]["partitions"] == {"PENDING": 2}
    assert len(result["dataset"]["rows"]) == 2
    assert result["baseline_scores"]["brier_binary"] is result["baseline_scores"]["log_loss_binary"] is None
    assert all(row["probability"] == .7 and row["outcome"] is None for row in result["dataset"]["rows"])


def test_conflicting_available_outcome_revision_not_winner_selected():
    pair, forecast = source()
    revision = dict(forecast["result_receipts"][0], receipt_id=sha("revision"), outcome=0)
    forecast["result_receipts"].append(revision)
    result = run([pair], [forecast])
    assert result["pair_occurrences"][0]["reason"] == "CONFLICTING_AVAILABLE_OUTCOME_REVISIONS"
    assert result["baseline_scores"]["eligible_selections"] == 0


def test_same_outcome_receipts_and_identical_native_duplicates_are_not_more_forecasts():
    pair, forecast = source()
    forecast["result_receipts"] *= 2
    result = run([pair], [forecast, deepcopy(forecast)])
    assert result["baseline_scores"]["eligible_selections"] == 1
    assert len(result["dataset"]["rows"][0]["result_receipt_ids"]) == 1
    assert result["accounting"]["native_forecast_occurrences"] == 2


def test_outcome_version_budget_counts_distinct_content_not_hundred_identical_aliases():
    pair, forecast = source()
    result = run([pair], [deepcopy(forecast) for _ in range(101)])
    assert result["baseline_scores"]["eligible_selections"] == 1
    assert result["accounting"]["native_forecast_occurrences"] == 101
    assert len(result["dataset"]["rows"][0]["result_receipt_ids"]) == 1


def test_result_before_kickoff_is_not_outcome_evidence():
    pair, forecast = source()
    forecast["result_receipts"][0]["observed_at"] = "2026-10-25T14:00:00+00:00"
    assert run([pair], [forecast])["pair_occurrences"][0]["reason"] == "OUTCOME_MUST_BE_OBSERVED_AFTER_TARGET"


def test_duplicate_pairs_and_predeclared_earliest_pair_rule():
    pair, forecast = source(rain=0.)
    later = deepcopy(pair)
    later.update(pair_id=sha("later-pair"), pair_receipt_id=sha("later-receipt"), saved_at="2026-10-25T12:10:00+00:00")
    later["weather_context"]["forecast"]["precipitation_mm"] = 1.
    later["weather_context_sha256"] = sha(later["weather_context"])
    result = run([later, pair, deepcopy(pair)], [forecast])
    assert result["accounting"]["partitions"] == {"SUPERSEDED": 1, "SCORED": 1, "DUPLICATE": 1}
    assert result["dataset"]["rows"][0]["weather_stratum"] == "DRY"
    assert result["baseline_scores"]["eligible_selections"] == 1


def test_conflicting_first_pairs_both_excluded_not_favorable_chosen():
    pair, forecast = source()
    conflict = deepcopy(pair)
    conflict.update(pair_id=sha("other-first"), pair_receipt_id=sha("other-receipt"))
    conflict["weather_context"]["forecast"]["precipitation_mm"] = 0.
    conflict["weather_context_sha256"] = sha(conflict["weather_context"])
    result = run([pair, conflict], [forecast])
    assert result["accounting"]["partitions"] == {"EXCLUDED": 2}
    assert all(row["reason"] == "CONFLICTING_FIRST_WEATHER_PAIRS" for row in result["pair_occurrences"])


def test_unknown_weather_remains_separate_and_is_not_probability_or_pitch_measurement():
    pair, forecast = source(rain=None)
    result = run([pair], [forecast])
    assert result["weather_conditioned_groups"][0]["weather_stratum"] == "UNKNOWN"
    assert result["weather_feature_summary"]["precipitation_mm"] == {"known": 0, "unknown": 1, "mean": None, "minimum": None, "maximum": None}
    assert result["dataset"]["rows"][0]["weather_model_probability"] is None
    assert result["dataset"]["manifest"]["recipe"]["moisture_is_proxy_not_pitch_measurement"]


@pytest.mark.parametrize("kind", ["REANALYSIS", "OBSERVATION"])
def test_reanalysis_or_observation_cannot_be_a_prematch_forecast(kind):
    pair, forecast = source()
    pair["weather_context"]["forecast"]["weather_kind"] = kind
    pair["weather_context_sha256"] = sha(pair["weather_context"])
    assert run([pair], [forecast])["pair_occurrences"][0]["reason"] == "PREMATCH_FORECAST_NOT_REANALYSIS_REQUIRED"


def test_full_cohort_accounts_pending_missing_and_losing_forecasts_not_just_winners():
    a, x = source("1", outcome=0)
    b, y = source("2", outcome=None)
    c, _ = source("3")
    _, unpaired = source("4")
    result = run([a, b, c], [x, y, unpaired])
    assert result["accounting"]["partitions"] == {"SCORED": 1, "PENDING": 1, "EXCLUDED": 1}
    assert result["accounting"]["native_unique_forecasts_without_joined_weather_pair"] == 1
    assert result["baseline_scores"]["brier_binary"] == pytest.approx(.49)
    assert len(result["dataset"]["lineage"]["pair_occurrences"]) == 3


def test_forged_favorable_content_never_authenticates_store_or_weather_challenger():
    pair, forecast = source(p=1., outcome=1)
    result = run([pair], [forecast])
    assert result["baseline_scores"]["brier_binary"] == 0
    assert result["source_authentication"] == module.PURE_AUTH
    assert result["weather_stratum_basis"] == "PREMATCH_FORECAST_FEATURE_PROXY_NOT_REALIZED_WEATHER"
    assert all(row["weather_stratum_basis"] == result["weather_stratum_basis"] for row in result["dataset"]["rows"])
    assert result["heldout_weather_comparison_complete"] is result["live_promotion_allowed"] is False
    with pytest.raises(TypeError):
        module.inspect_weather_forecast_quality("TESTONLY", as_of=NOW, forecasts=[forecast])


def test_dataset_hash_forgery_and_row_budget_are_not_silent_truncation(monkeypatch):
    pair, forecast = source()
    feature, native = bundles([pair], [forecast])
    feature["dataset"]["rows"][0]["weather_context_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="HASH_OR_SCHEMA"):
        module.summarize_source_content(feature, native, as_of=NOW)
    monkeypatch.setattr(module, "MAX_ROWS", 1)
    a, x = source("1")
    b, y = source("2")
    with pytest.raises(ValueError, match="ROW_BUDGET"):
        module.summarize_source_content(*bundles([a, b], [x, y]), as_of=NOW)


def test_actual_readers_empty_explicit_testonly_root_create_nothing(tmp_path):
    marker = tmp_path / "TESTONLY"
    marker.write_text("EMPTY_READONLY_SOURCE_CONTROL", encoding="utf-8")
    result = module.inspect_weather_forecast_quality(tmp_path, as_of=NOW)
    assert result["status"] == "NO_ELIGIBLE_WEATHER_PAIRS"
    assert result["source_authentication"] == "ACTUAL_READERS_EMPTY_NO_WEATHER_STORE_EVIDENCE"
    assert list(tmp_path.iterdir()) == [marker]


def test_source_budget_failure_is_safe_local_and_no_retry(monkeypatch, tmp_path):
    first = Mock(side_effect=ValueError("TESTONLY_SOURCE_BUDGET"))
    second = Mock(side_effect=AssertionError("SECOND_READER_SHOULD_NOT_RUN"))
    monkeypatch.setattr(weather_feature_capture, "read_pairs", first)
    monkeypatch.setattr(native_forecast_capture, "read_forecasts", second)
    result = module.inspect_weather_forecast_quality(tmp_path, as_of=NOW)
    assert result["status"] == "UNAVAILABLE" and result["reason"] == "SOURCE_READER_BUDGET"
    assert not result["automatic_retry"] and result["decision_effect"] == "NONE"
    first.assert_called_once()
    second.assert_not_called()


def test_source_failures_do_not_expose_raw_error_or_mutate_existing_baseline(monkeypatch, tmp_path):
    baseline = {"metrics": {"frozen": {"brier": .25}}, "recommendations": ["original"]}
    before = deepcopy(baseline)
    monkeypatch.setattr(weather_feature_capture, "read_pairs", Mock(side_effect=OSError("TESTONLY private provider text")))
    diagnostic = module.inspect_weather_forecast_quality(tmp_path, as_of=NOW)
    assert baseline == before
    assert "private provider text" not in str(diagnostic)
    assert diagnostic["reason"] == "WEATHER_LEARNING_SOURCE_UNAVAILABLE"


def test_bad_probability_or_unknown_period_is_local_not_repaired():
    good, x = source("1", p=.7, outcome=1)
    bad, y = source("2", p=None)
    period, z = source("3")
    period["baseline"]["period"] = z["period"] = "FIRST_HALF"
    result = run([good, bad, period], [x, y, z])
    assert result["accounting"]["partitions"] == {"SCORED": 1, "EXCLUDED": 2}
    assert result["baseline_scores"]["eligible_selections"] == 1


def test_input_source_gaps_are_explicit_not_a_veto_for_valid_rows():
    pair, forecast = source()
    result = run([pair], [forecast], pair_gaps={"OTHER_SOURCE_UNAVAILABLE": 1})
    assert result["baseline_scores"]["eligible_selections"] == 1
    assert result["source_inventory_complete"] is False
    assert result["dataset"]["lineage"]["feature_source_gaps"] == {"OTHER_SOURCE_UNAVAILABLE": 1}
