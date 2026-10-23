"""Offline numerical profiles: real-library smoke tests plus leakage boundaries."""

import copy
import math
import random
from collections import Counter
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.models import gauss, kepler, predict_models
from goal_analysis.models._data import (
    check_competition,
    history_hash,
    normalize_fixtures,
    prepare_history,
)

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)


def fixtures():
    return [{
        "fixture_id": str(900001 + index), "api_football_league_id": "78",
        "home_team_id": str(home), "away_team_id": str(away),
        "kickoff": (NOW + timedelta(hours=6 + index)).isoformat(), "provider_status": "NS",
    } for index, (home, away) in enumerate(((1, 2), (3, 4)))]


def sample_history():
    rng = random.Random(713)

    def poisson(rate):
        count, product = -1, 1.0
        while product > math.exp(-rate):
            count += 1
            product *= rng.random()
        return count

    rows, teams = [], list(range(1, 9))
    for round_number in range(100):
        kickoff = NOW - timedelta(days=700 - round_number * 7)
        for index in range(4):
            home, away = teams[index], teams[-index - 1]
            if round_number % 2:
                home, away = away, home
            match_id = str(1000 + round_number * 4 + index)
            rows.append({
                "fixture_id": match_id, "league_id": "78", "season": kickoff.year,
                "kickoff": kickoff.isoformat(), "status": "FT",
                "home_team_id": str(home), "away_team_id": str(away),
                "full_time_home": poisson(1.4 + 0.06 * home),
                "full_time_away": poisson(1.0 + 0.05 * away),
                "observed_at": (NOW - timedelta(minutes=1)).isoformat(),
                "source_id": f"synthetic:{match_id}",
            })
        teams = [teams[0], teams[-1], *teams[1:-1]]
    return rows


@pytest.fixture
def historical():
    return sample_history()


def test_history_filters_future_unclosed_missing_and_target(historical):
    baseline, _ = prepare_history(historical, NOW, set())
    added = []
    for index, changes in enumerate((
        {"kickoff": (NOW + timedelta(days=1)).isoformat()},
        {"kickoff": (NOW - timedelta(hours=2)).isoformat()},
        {"kickoff": (NOW - timedelta(hours=3)).isoformat()},
        {"observed_at": (NOW + timedelta(seconds=1)).isoformat()},
        {"status": "NS"}, {"full_time_home": None}, {"full_time_home": True},
        {"source_id": ""}, {"kickoff": "2026-09-10T00:00:00"},
    )):
        row = dict(historical[0], fixture_id=str(60000 + index), **changes)
        added.append(row)
    target = dict(historical[0], fixture_id="900001")
    actual, issues = prepare_history(historical + added + [target], NOW, {"900001"})
    assert history_hash(actual) == history_hash(baseline)
    assert {i["code"] for i in issues} >= {
        "FUTURE_OR_UNCLOSED_HISTORY", "INVALID_HISTORY", "TARGET_HISTORY_EXCLUDED",
    }


def test_duplicate_conflicts_are_withheld_and_order_independent(historical):
    first = historical[0]
    conflict = dict(first, full_time_home=first["full_time_home"] + 1)
    rows = historical + [dict(historical[1]), conflict]
    forward, issues = prepare_history(rows, NOW, set())
    reverse, _ = prepare_history(list(reversed(rows)), NOW, set())
    assert len(forward) == len(historical) - 1
    assert first["fixture_id"] not in {r["fixture_id"] for r in forward}
    assert history_hash(forward) == history_hash(reverse)
    assert any(i["code"] == "CONFLICTING_HISTORY" for i in issues)


def test_input_and_fixture_boundaries(historical, tmp_path):
    assert predict_models([], historical, NOW)["gauss"]["status"] == "NO_FIXTURES"
    assert predict_models(fixtures(), [], NOW)["gauss"]["status"] == "INSUFFICIENT_HISTORY"
    assert predict_models(fixtures(), [], NOW.replace(tzinfo=None))["gauss"]["status"] == "MODEL_ERROR"
    assert predict_models(fixtures(), [], NOW, artifact_dir=tmp_path)["gauss"]["status"] == "MODEL_ERROR"
    assert predict_models(fixtures(), [], NOW, enabled_profiles=("gauss",)).keys() == {"gauss"}
    raw = {
        "fixture": {"id": 10, "date": (NOW + timedelta(hours=4)).isoformat(),
                    "status": {"short": "NS"}},
        "league": {"id": 78}, "teams": {"home": {"id": 1}, "away": {"id": 2}},
    }
    normalized, issues = normalize_fixtures([raw], NOW)
    assert not issues
    assert normalized[0]["league_id"] == "78"
    started = dict(fixtures()[0], kickoff=NOW.isoformat())
    assert predict_models([started], historical, NOW)["kepler"]["status"] == "NO_FIXTURES"


def test_missing_dependency_is_discrete_not_a_heuristic(historical, monkeypatch):
    def unavailable():
        raise ModuleNotFoundError("secret local path should not be exposed")

    monkeypatch.setattr(gauss, "load_dependencies", unavailable)
    result = predict_models(fixtures(), historical, NOW, ("gauss",))["gauss"]
    assert result["status"] == "DEPENDENCY_MISSING"
    assert result["predictions"] == []
    assert "secret" not in str(result)


@pytest.mark.parametrize("prefix", ["api_football:", "api-football:"])
def test_real_daily_fixture_identity_and_history_crosswalk(historical, prefix, monkeypatch):
    from goal_analysis.jobs.daily_223_candidates import normalize_daily_fixtures

    raw = {
        "fixture": {"id": 900001, "date": (NOW + timedelta(hours=6)).isoformat(),
                    "status": {"short": "NS"}},
        "league": {"id": 78, "season": 2026},
        "teams": {"home": {"id": 1, "name": "One"}, "away": {"id": 2, "name": "Two"}},
    }
    league = {"api_football_id": 78, "season": 2026, "competition_id": "DE1",
              "competition_type": "League", "odds_sport_key": "soccer_germany_bundesliga"}
    actual = normalize_daily_fixtures({"response": [raw]}, league, NOW.date())
    actual[0]["fixture_id"] = prefix + "900001"
    normalized, issues = normalize_fixtures(actual, NOW)
    assert not issues
    assert normalized[0]["fixture_id"] == prefix + "900001"
    assert normalized[0]["provider_fixture_id"] == "900001"
    assert normalized[0]["home_team_id"] == "1"
    assert normalized[0]["away_team_id"] == "2"
    assert predict_models(actual, [], NOW)["gauss"]["status"] == "INSUFFICIENT_HISTORY"
    target_in_history = dict(historical[0], fixture_id="900001")
    # No numerical work needed: inspect the exact boundary presented to the model.
    captured = []
    monkeypatch.setattr(gauss, "load_dependencies", lambda: ())
    monkeypatch.setattr(gauss, "fit_league", lambda records, *args: captured.extend(records))
    monkeypatch.setattr(gauss, "predict_fixture", lambda fixture, *args: {
        "fixture_id": fixture["fixture_id"], "probabilities": {}, "stability": {},
        "model_details": {},
    })
    result = predict_models(actual, historical + [target_in_history], NOW, ("gauss",))["gauss"]
    assert result["predictions"][0]["fixture_id"] == prefix + "900001"
    assert "900001" not in {r["fixture_id"] for r in captured}
    assert any(d["code"] == "TARGET_HISTORY_EXCLUDED" for d in result["diagnostics"])


def test_model_error_isolated_and_no_fake_fallback(historical, monkeypatch):
    monkeypatch.setattr(gauss, "load_dependencies", lambda: ())

    def broken(*args):
        raise RuntimeError("private local path")

    monkeypatch.setattr(gauss, "fit_league", broken)
    result = predict_models(fixtures(), historical, NOW, ("gauss",))["gauss"]
    assert result["status"] == "MODEL_ERROR"
    assert not result["predictions"]
    assert len(result["diagnostics"]) == 2
    assert "private" not in str(result)


def test_gauss_two_families_two_fixed_windows_stability(historical, monkeypatch):
    fits = []

    class Grid:
        def __init__(self, probability):
            self.home_draw_away = [probability, 0.25, 0.75 - probability]
            self.grid = [self.home_draw_away]
            self.btts_yes = probability

        def total_goals(self, side, strike):
            assert strike == 2.5
            return 0.6 if side == "over" else 0.4

    class Bivariate:
        delta = 0.0

        def __init__(self, home_goals, away_goals, home, away):
            self.count = len(home_goals)
            self.fitted = False

        def fit(self, minimizer_options):
            fits.append((self.__class__.__name__, self.count))
            self.fitted = True
            self.loglikelihood = -1.0

        def get_params(self):
            return {"parameter": 0.1}

        def predict(self, home, away, **kwargs):
            return Grid(0.4 + self.delta + self.count / 10000)

    class Dixon(Bivariate):
        delta = 0.05

    monkeypatch.setattr(gauss, "load_dependencies", lambda: (
        (("bivariate_poisson", Bivariate), ("dixon_coles", Dixon)), lambda **kw: nullcontext()
    ))
    monkeypatch.setattr(gauss, "version", lambda name: "test")
    before = copy.deepcopy(historical)
    result = predict_models(fixtures(), historical, NOW, ("gauss",))["gauss"]
    assert result["status"] == "READY"
    assert len(result["predictions"]) == 2  # Both full-universe fixtures, not one Arthur pick.
    assert len(fits) == 4  # Fitted once per league, shared by both daily fixtures.
    assert len({n for _, n in fits}) == 2
    prediction = result["predictions"][0]
    variants = prediction["model_details"]["variants"]
    home_values = [p["home"] for p in variants.values()]
    assert prediction["probabilities"]["home"] == pytest.approx(sum(home_values) / 4)
    assert prediction["stability"]["home"] == pytest.approx(max(home_values) - min(home_values))
    assert prediction["model_details"]["windows"] == [365, 730]
    assert historical == before


class RecordingPi:
    """Only for inspecting feature chronology; production uses penaltyblog."""

    def __init__(self):
        self.team_ratings = {}

    def initialize_team(self, team):
        self.team_ratings.setdefault(team, {"home": 0.0, "away": 0.0})

    def expected_goal_difference(self, home, away):
        return self.team_ratings[home]["home"] - self.team_ratings[away]["away"]

    def update_ratings(self, home, away, difference):
        self.initialize_team(home)
        self.initialize_team(away)
        self.team_ratings[home]["home"] += difference
        self.team_ratings[away]["away"] -= difference


def test_pi_training_features_are_pre_result_even_same_kickoff(historical):
    records, _ = prepare_history(historical, NOW, set())
    baseline, *_ = kepler.chronological_features(records, NOW, RecordingPi)
    changed = copy.deepcopy(records)
    affected_time = baseline[10]["kickoff"]
    for row in changed:
        if row["_kickoff"] >= affected_time:
            row["full_time_home"] = 20
            row["full_time_away"] = 0
    rerun, *_ = kepler.chronological_features(changed, NOW, RecordingPi)
    early = {r["fixture_id"]: r["features"] for r in baseline if r["kickoff"] <= affected_time}
    assert early == {
        r["fixture_id"]: r["features"] for r in rerun if r["kickoff"] <= affected_time
    }
    assert any(a["features"] != b["features"] for a, b in zip(baseline, rerun, strict=True))


def test_pi_features_wait_three_hours_for_previous_results(historical):
    additions = [dict(
        historical[0], fixture_id=str(70000 + index), home_team_id="1", away_team_id="2",
        kickoff=(NOW - timedelta(hours=hours)).isoformat(), full_time_home=1, full_time_away=1,
    ) for index, hours in enumerate((8, 6, 4))]
    records, _ = prepare_history(historical + additions, NOW, set())
    baseline, *_ = kepler.chronological_features(records, NOW, RecordingPi)
    changed = copy.deepcopy(records)
    next(row for row in changed if row["fixture_id"] == "70000")["full_time_home"] = 20
    rerun, *_ = kepler.chronological_features(changed, NOW, RecordingPi)
    before = {r["fixture_id"]: r["features"] for r in baseline}
    after = {r["fixture_id"]: r["features"] for r in rerun}
    assert before["70000"] == after["70000"]  # Its own result is unavailable.
    assert before["70001"] == after["70001"]  # Previous match only started 2h earlier.
    assert before["70002"] != after["70002"]  # Previous match started 4h earlier.


def test_holdout_metrics_are_proper_scores():
    perfect = kepler.holdout_metrics([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 1, 2])
    assert perfect == {"sample_size": 3, "brier_multiclass_sum": 0, "rps": 0, "log_loss": 0}
    uniform = kepler.holdout_metrics([[1 / 3] * 3] * 3, [0, 1, 2])
    assert uniform["brier_multiclass_sum"] == pytest.approx(2 / 3)
    assert uniform["log_loss"] == pytest.approx(math.log(3))


def test_invalid_goal_grid_is_not_repaired_or_presented_as_probability():
    class InvalidGrid:
        grid = ((1.1, -0.1), (0.0, 0.0))

    with pytest.raises(ValueError, match="scoreline"):
        gauss.probabilities(InvalidGrid())


def test_disconnected_sparse_competitions_abstain(historical):
    from goal_analysis.models._data import InsufficientHistory

    records, _ = prepare_history(historical[:8], NOW, set())
    with pytest.raises(InsufficientHistory):
        check_competition(records, 80)
    records = []
    for index in range(100):
        records.append({"home_team_id": str(1 if index % 2 else 3),
                        "away_team_id": str(2 if index % 2 else 4)})
    with pytest.raises(InsufficientHistory, match="összefüggő"):
        check_competition(records, 80)


def test_real_library_fits_and_probabilities(historical):
    pytest.importorskip("penaltyblog")
    pytest.importorskip("catboost")
    pytest.importorskip("threadpoolctl")
    result = predict_models(fixtures(), historical, NOW)
    for profile in ("gauss", "kepler"):
        assert result[profile]["status"] == "READY", result[profile]
        assert len(result[profile]["predictions"]) == 2
        for prediction in result[profile]["predictions"]:
            probs = prediction["probabilities"]
            assert sum(probs[key] for key in ("home", "draw", "away")) == pytest.approx(1)
            assert all(math.isfinite(p) and 0 <= p <= 1 for p in probs.values())
            assert prediction["model_details"]["calibration"] == "unvalidated"
    assert set(result["kepler"]["predictions"][0]["probabilities"]) == {"home", "draw", "away"}
    metadata = result["kepler"]["predictions"][0]["model_details"]
    validation = metadata["validation"]
    assert validation["sample_size"] >= 20
    assert datetime.fromisoformat(validation["training_end"]) + timedelta(hours=3) < (
        datetime.fromisoformat(validation["validation_start"])
    )
    assert 0 <= validation["brier_multiclass_sum"] <= 2
    assert 0 <= validation["rps"] <= 1
    assert len(result["gauss"]["predictions"][0]["model_details"]["variants"]) == 4
    assert metadata["library_versions"]["catboost"]


def test_real_kepler_deterministic_no_files_or_future_rows(historical, tmp_path, monkeypatch):
    pytest.importorskip("penaltyblog")
    pytest.importorskip("catboost")
    pytest.importorskip("threadpoolctl")
    monkeypatch.chdir(tmp_path)
    first = predict_models(fixtures(), historical, NOW, ("kepler",))["kepler"]
    assert first["status"] == "READY", first
    future = dict(historical[0], fixture_id="99999", observed_at=(NOW + timedelta(days=1)).isoformat())
    second = predict_models(fixtures(), historical + [future], NOW, ("kepler",))["kepler"]
    assert second["status"] == "READY", second
    assert first["predictions"] == second["predictions"]
    assert not list(tmp_path.iterdir())


def test_kepler_hyperparameters_restrict_cpu_and_writes():
    received = {}

    def capture(**kwargs):
        received.update(kwargs)

    kepler.classifier(capture)
    assert received["task_type"] == "CPU"
    assert received["thread_count"] == 2
    assert received["allow_writing_files"] is False
    assert received["has_time"] is True
    assert received["random_seed"] == kepler.SEED
    assert Counter(received)["iterations"] == 120
