"""Coverage repair preserves numerical, temporal and target-sample vetoes."""
import copy
import random
from datetime import timedelta

import pytest
from test_numeric_models import NOW, fixtures, sample_history

from goal_analysis.models import gauss, kepler, laplace, predict_models
from goal_analysis.models._data import (
    InsufficientHistory,
    check_competition,
    check_teams,
    competition_window,
    history_hash,
    prepare_history,
    sparse_team_core,
    window_records,
)


def normalized(rows):
    return prepare_history(rows, NOW, set())[0]


def nuisance(template, number, home, away, **changes):
    return dict(template, fixture_id=str(number), home_team_id=str(home),
                away_team_id=str(away), source_id=f"synthetic:{number}", **changes)


def test_healthy_core_keeps_original_history_hash_and_inputs():
    rows = normalized(sample_history())
    original = copy.deepcopy(rows)
    retained, details = sparse_team_core(rows)
    assert retained == rows == original
    assert details["iterations"] == details["removed_rows"] == details["removed_teams"] == 0
    assert details["input_history_hash"] == details["retained_history_hash"] == history_hash(rows)
    check_competition(retained, 120)


def test_one_sparse_nuisance_team_does_not_veto_healthy_core():
    rows = sample_history()
    clean = normalized(rows)
    extra = nuisance(rows[-1], 60000, 1, 99)
    with pytest.raises(InsufficientHistory, match="négynél"):
        check_competition(normalized(rows + [extra]), 80)
    retained, details = sparse_team_core(normalized(rows + [extra]))
    assert retained == clean
    assert details["removed_team_ids"] == ["99"]
    assert details["removed_rows"] == 1
    assert details["retained_history_hash"] == history_hash(clean)
    check_competition(retained, 120)


def test_sparse_removal_iterates_when_neighbor_becomes_sparse():
    rows = sample_history()
    extras = [nuisance(rows[-1], 61000 + i, 9, 1) for i in range(3)]
    extras += [nuisance(rows[-1], 61003, 9, 10)]
    retained, details = sparse_team_core(normalized(rows + extras))
    assert retained == normalized(rows)
    assert details["iterations"] == 2
    assert details["removed_team_ids"] == ["10", "9"]
    assert details["removed_rows"] == 4


def test_core_membership_does_not_use_outcomes():
    rows = normalized(sample_history())
    changed = copy.deepcopy(rows)
    for row in changed:
        row["full_time_home"], row["full_time_away"] = 30, 0
    assert [r["fixture_id"] for r in sparse_team_core(rows)[0]] == [
        r["fixture_id"] for r in sparse_team_core(changed)[0]
    ]


def test_order_is_deterministic_and_does_not_mutate_supplied_history():
    rows = normalized(sample_history())
    rows += normalized([nuisance(sample_history()[-1], 62000, 1, 99)])
    before = copy.deepcopy(rows)
    expected = sparse_team_core(rows)
    random.Random(39).shuffle(rows)
    assert sparse_team_core(rows) == expected
    assert sorted(rows, key=lambda r: r["fixture_id"]) == sorted(before, key=lambda r: r["fixture_id"])


def test_core_is_computed_separately_for_each_existing_window():
    rows = sample_history()
    extras = [nuisance(rows[-1], 63000 + i, 1, 99,
                      kickoff=(NOW - timedelta(days=days)).isoformat())
              for i, days in enumerate((100, 110, 500, 510))]
    cleaned = normalized(rows + extras)
    short, short_details = competition_window(cleaned, NOW, 365, 80)
    long, long_details = competition_window(cleaned, NOW, 730, 80)
    assert short_details["removed_team_ids"] == ["99"]
    assert long_details["removed_team_ids"] == []
    assert short_details["retained_history_hash"] == history_hash(short)
    assert long_details["retained_history_hash"] == history_hash(long)
    assert len(short) == len(window_records(normalized(rows), NOW, 365))
    with pytest.raises(InsufficientHistory, match="legalább 8"):
        check_teams(long, {"home_team_id": "1", "away_team_id": "99"})


def test_removed_target_team_still_abstains_in_real_numeric_prediction():
    rows = sample_history()
    rows.append(nuisance(rows[-1], 64000, 1, 99))
    target = dict(fixtures()[0], away_team_id="99")
    result = predict_models([target], rows, NOW, ("laplace",))["laplace"]
    assert result["status"] == "INSUFFICIENT_HISTORY"
    assert result["predictions"] == []
    assert any(d["code"] == "SPARSE_TRAINING_CORE" for d in result["diagnostics"])
    assert any(d["code"] == "INSUFFICIENT_HISTORY" and "legalább 8" in d["message"]
               for d in result["diagnostics"])
    assert result["training_selection"]["78"]["730"]["removed_team_ids"] == ["99"]


def test_disconnected_healthy_cores_still_fail_without_largest_component_selection():
    rows = sample_history()
    other = [dict(r, fixture_id=str(200000 + i), home_team_id=str(int(r["home_team_id"]) + 20),
                  away_team_id=str(int(r["away_team_id"]) + 20)) for i, r in enumerate(rows)]
    retained, details = sparse_team_core(normalized(rows + other))
    assert details["removed_rows"] == 0
    with pytest.raises(InsufficientHistory, match="összefüggő"):
        check_competition(retained, 120)
    with pytest.raises(InsufficientHistory) as failed:
        competition_window(normalized(rows + other), NOW, 730, 120)
    assert failed.value.training_selection["730"]["input_rows"] == 800


@pytest.mark.parametrize("minimum", [80, 120])
def test_pruning_never_bypasses_original_competition_row_minimum(minimum):
    rows = normalized(sample_history()[:8])
    with pytest.raises(InsufficientHistory):
        competition_window(rows, NOW, 730, minimum)


def test_temporal_invalid_and_target_rows_are_filtered_before_core_membership():
    rows = sample_history()
    additions = [nuisance(rows[-1], 65000 + i, 1, 99, **change) for i, change in enumerate((
        {"observed_at": (NOW + timedelta(seconds=1)).isoformat()},
        {"kickoff": (NOW - timedelta(hours=3)).isoformat()},
        {"kickoff": (NOW + timedelta(days=1)).isoformat()},
        {"status": "NS"}, {"full_time_home": True},
    ))]
    additions.append(nuisance(rows[-1], 900001, 1, 99))
    clean, issues = prepare_history(rows + additions, NOW, {"900001"})
    retained, details = sparse_team_core(clean)
    assert history_hash(retained) == history_hash(normalized(rows))
    assert details["removed_rows"] == details["removed_teams"] == 0
    assert {d["code"] for d in issues} >= {
        "TARGET_HISTORY_EXCLUDED", "FUTURE_OR_UNCLOSED_HISTORY", "INVALID_HISTORY",
        "UNFINISHED_OR_INVALID_CHRONOLOGY",
    }


def test_failed_kepler_holdout_keeps_pruned_sample_provenance():
    rows = sample_history()[-120:]
    # A simultaneous final block must stay wholly in holdout; it cannot be
    # divided to manufacture sixty earlier training rows.
    rows[-60:] = [dict(row, kickoff=(NOW - timedelta(days=2)).isoformat())
                  for row in rows[-60:]]
    rows.append(nuisance(rows[-1], 66000, 1, 99))
    result = predict_models(fixtures(), rows, NOW, ("kepler",))["kepler"]
    assert result["status"] == "INSUFFICIENT_HISTORY"
    assert not result["predictions"]
    coverage = result["training_selection"]["78"]["730"]
    assert coverage["removed_rows"] == 1
    assert coverage["retained_rows"] == 120
    assert any(d["code"] == "SPARSE_TRAINING_CORE" for d in result["diagnostics"])


def test_real_healthy_predictions_match_legacy_window_selection(monkeypatch):
    """Identical fit inputs produce identical probabilities and legacy history hashes."""
    pytest.importorskip("penaltyblog")
    pytest.importorskip("catboost")
    rows = sample_history()
    actual = predict_models(fixtures(), rows, NOW, ("gauss", "kepler", "laplace"))

    def legacy_window(records, now, days, minimum):
        sample = window_records(records, now, days)
        check_competition(sample, minimum)
        retained, details = sparse_team_core(sample)
        assert retained == sample and details["removed_rows"] == 0
        details.update(window_days=days, minimum_competition_matches=minimum)
        return sample, details

    for module in (gauss, kepler, laplace):
        monkeypatch.setattr(module, "competition_window", legacy_window)
    legacy = predict_models(fixtures(), rows, NOW, ("gauss", "kepler", "laplace"))
    for profile in ("gauss", "kepler", "laplace"):
        assert actual[profile]["status"] == legacy[profile]["status"] == "READY"
        assert actual[profile]["predictions"] == legacy[profile]["predictions"]
