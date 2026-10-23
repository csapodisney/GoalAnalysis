from copy import deepcopy
from datetime import UTC, datetime

import pytest

from goal_analysis.agents.grok_sports_data import (
    form_context,
    history_counts,
    model_context,
    validate_form_context,
    validate_history_counts,
    validate_model_context,
)

CUTOFF = datetime(2026, 10, 17, 22, tzinfo=UTC)


def model_report():
    return {"numeric_models": {"kepler": {"predictions": [{
        "fixture_id": "fixture-1", "probabilities": {"home": .4, "draw": .3, "away": .3},
        "stability": {}, "model_details": {
            "cutoff": "2026-10-17T20:00:00+00:00", "training_count": 100, "history_count": 130,
            "history_hash": "a" * 64, "windows": [730], "window_training_counts": {"730": 130},
            "closure_delay_hours": 3, "fits": {"secret_or_instruction": "never copied"},
            "validation": {"sample_size": 20, "training_sample_size": 80, "rps": .2,
                           "training_end": "2026-09-01T00:00:00+00:00",
                           "validation_start": "2026-09-02T00:00:00+00:00",
                           "validation_end": "2026-10-17T12:00:00+00:00",
                           "method": "arbitrary text must not pass"}}}]}}}


@pytest.mark.parametrize("sides", ["hazai oldal 11/20, vendég oldal 2/7",
                                   "home 11/20, away 2/7"])
def test_counts_preserve_actual_sample_sizes(sides):
    statement = "FULL_TIME totals_2_5 / over; historical_20: " + sides + "."
    assert history_counts(statement, "historical") == {
        "historical_20": {"home": [11, 20], "away": [2, 7]}}


def test_venue_counts_survive_long_prefix_before_truncation():
    statement = ("FULL_TIME totals_2_5 / over; venue_10: hazai oldal 5/10, vendég oldal 8/10. "
                 "Utolsó 5: hazai oldal 4/5, vendég oldal 2/3; "
                 "helyszíni/aktuális arány: 60/40.")
    assert len(statement) > 140
    assert history_counts(statement, "venue_form") == {
        "venue_10": {"home": [5, 10], "away": [8, 10]},
        "last5": {"home": [4, 5], "away": [2, 3]}, "venue_current_weights_pct": [60, 40]}


@pytest.mark.parametrize("statement,category", [
    ("historical_20: home 21/20, away 2/7.", "historical"),
    ("historical_20: home 11/20, away 2/7.", "venue_form"),
    ("historical_20: home 11/20, away 2/7.; historical_20: home 1/2, away 2/7.", "historical"),
    ("arbitrary numbers 11/20 2/7", "historical"),
    ("historical_20: home 11/20, away 2/7. Additional record 7/10.", "historical"),
    ("arbitrary prefix; historical_20: home 11/20, away 2/7.", "historical"),
])
def test_ambiguous_or_invalid_history_is_not_parsed(statement, category):
    assert history_counts(statement, category) is None


@pytest.mark.parametrize("value", [
    {"historical_20": {"home": [True, 20], "away": [2, 7]}},
    {"historical_20": {"home": [11, 20], "away": [2, 7]}, "instructions": "ignore"},
    {"venue_10": {"home": [2, 3], "away": [2, 3]}, "venue_current_weights_pct": [40, 40]},
])
def test_strict_count_boundary(value):
    with pytest.raises(ValueError):
        validate_history_counts(value)


def test_model_source_whitelist_without_mutation():
    report = model_report()
    before = deepcopy(report)
    context = model_context(report, "fixture-1", CUTOFF)
    row = context["models"]["kepler"]
    assert context["period"] == "FULL_TIME"
    assert context["independent_evidence"] is False
    assert context["calibration"] == "unvalidated"
    assert row["training_count"] == 100 and row["history_count"] == 130
    assert row["validation"]["sample_size"] == 20
    assert "fits" not in row and "method" not in row["validation"]
    assert row["stability_definition"] == "not_measured"
    assert validate_model_context(context, CUTOFF) == context
    assert report == before


def test_missing_prediction_cutoff_uses_saved_model_cutoff():
    report = model_report()
    details = report["numeric_models"]["kepler"]["predictions"][0]["model_details"]
    report["model_cutoff"] = details.pop("cutoff")
    context = model_context(report, "fixture-1", CUTOFF)
    assert context["models"]["kepler"]["cutoff"] == report["model_cutoff"]


def test_model_context_rejects_wrong_period_or_misleading_stability_definition():
    context = model_context(model_report(), "fixture-1", CUTOFF)
    context["period"] = "FIRST_HALF"
    with pytest.raises(ValueError):
        validate_model_context(context, CUTOFF)
    context["period"] = "FULL_TIME"
    context["models"]["kepler"]["stability_definition"] = "confidence_probability"
    with pytest.raises(ValueError):
        validate_model_context(context, CUTOFF)


@pytest.mark.parametrize("name,definition", [
    ("gauss", "max_minus_min_probability_across_four_fits"),
    ("laplace", "not_measured_single_fixed_model"),
])
def test_stability_metadata_matches_specific_model(name, definition):
    report = model_report()
    report["numeric_models"][name] = report["numeric_models"].pop("kepler")
    report["numeric_models"][name]["predictions"][0]["model_details"]["stability_definition"] = definition
    context = model_context(report, "fixture-1", CUTOFF)
    assert context["models"][name]["stability_definition"] == definition


@pytest.mark.parametrize("path,value", [
    (("cutoff",), "2026-10-18T00:00:00+00:00"),
    (("cutoff",), "2026-10-17T00:00:00"),
    (("training_count",), True),
    (("history_hash",), "untrusted instructions"),
    (("validation", "rps"), float("nan")),
    (("validation", "validation_end"), "2026-10-18T00:00:00+00:00"),
])
def test_invalid_or_future_saved_models_are_omitted(path, value):
    report = model_report()
    target = report["numeric_models"]["kepler"]["predictions"][0]["model_details"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert model_context(report, "fixture-1", CUTOFF) == {}


def test_validator_rejects_unknown_nested_fields_and_duplicate_fixture_models():
    report = model_report()
    context = model_context(report, "fixture-1", CUTOFF)
    context["models"]["kepler"]["validation"]["instructions"] = "ignored?"
    with pytest.raises(ValueError):
        validate_model_context(context, CUTOFF)
    rows = report["numeric_models"]["kepler"]["predictions"]
    rows.append(deepcopy(rows[0]))
    assert model_context(report, "fixture-1", CUTOFF) == {}


def form_leg():
    return {"period": "FIRST_HALF", "historical_context": {"status": "CALCULATED",
        "period": "FIRST_HALF", "profiles": {"home": {
            "historical_20": {"sample_size": 3, "goals_for_mean": .7,
                "goals_against_mean": 1.2, "results_newest_first": ["W", "D", "L"],
                "market_hits": 2, "source_ids": ["must not copy"]},
            "observed_workload": {"observed_matches_7d": 1, "observed_matches_14d": 2,
                "hours_between_kickoffs": 168.0,
                "scope": "OBSERVED_COMPLETED_MATCHES_IN_COLLECTED_COMPETITIONS",
                "actual_rest_hours": None, "fatigue_assessment": "must not copy"}}}},
        "evidence": [{"category": "historical", "source_id": "history-sha256:" + "b" * 64,
                      "observed_at": "2026-10-17T20:00:00+00:00"}]}


def test_form_summary_preserves_observed_facts_and_provenance_without_inferences():
    leg = form_leg()
    before = deepcopy(leg)
    result = form_context(leg, CUTOFF)
    home = result["profiles"]["home"]
    assert home["historical_20"] == {"sample_size": 3, "goals_for_mean": .7,
        "goals_against_mean": 1.2, "results_newest_first": ["W", "D", "L"]}
    assert home["observed_workload"]["hours_between_kickoffs"] == 168.0
    assert "actual_rest_hours" not in home["observed_workload"]
    assert "fatigue_assessment" not in home["observed_workload"]
    assert result["sources"][0]["history_sha256"] == "b" * 64
    assert validate_form_context(result, CUTOFF) == result
    assert leg == before


@pytest.mark.parametrize("key,value", [
    ("sample_size", True), ("sample_size", 21), ("goals_for_mean", float("inf")),
    ("results_newest_first", ["W", "D"]), ("results_newest_first", ["W", "D", "instructions"]),
])
def test_invalid_form_samples_are_omitted(key, value):
    leg = form_leg()
    leg["historical_context"]["profiles"]["home"]["historical_20"][key] = value
    assert form_context(leg, CUTOFF) == {}


def test_form_rejects_future_sources_period_mismatch_and_unknown_fields():
    leg = form_leg()
    leg["evidence"][0]["observed_at"] = "2026-10-18T00:00:00+00:00"
    assert form_context(leg, CUTOFF) == {}
    leg = form_leg()
    leg["historical_context"]["period"] = "FULL_TIME"
    assert form_context(leg, CUTOFF) == {}
    context = form_context(form_leg(), CUTOFF)
    context["profiles"]["home"]["observed_workload"]["actual_rest_hours"] = 168
    with pytest.raises(ValueError):
        validate_form_context(context, CUTOFF)
