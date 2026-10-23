"""Chronological penaltyblog Pi features and a fixed CPU CatBoost classifier."""

from __future__ import annotations

import importlib
import math
from collections import Counter, deque
from importlib.metadata import version

from ._data import (
    CLOSURE_DELAY,
    MIN_TEAM_MATCHES,
    InsufficientHistory,
    check_teams,
    competition_window,
    history_hash,
)
from .datasets import fitting, lineage, recipe, record

VERSION = "kepler-1.1-sparse-core"
WINDOW_DAYS = 730
MIN_MATCHES = 120
MIN_TRAINING_ROWS = 60
MIN_VALIDATION_ROWS = 20
SEED = 223
FEATURE_NAMES = (
    "home_pi_home", "home_pi_away", "away_pi_home", "away_pi_away", "pi_difference",
    "home_prior_matches", "away_prior_matches", "home_rest_days", "away_rest_days",
)


def load_dependencies():
    pi_type = importlib.import_module("penaltyblog.ratings").PiRatingSystem
    classifier_type = importlib.import_module("catboost").CatBoostClassifier
    limits = importlib.import_module("threadpoolctl").threadpool_limits
    return pi_type, classifier_type, limits


def feature_vector(ratings, counts, last_match, home, away, kickoff):
    ratings.initialize_team(home)
    ratings.initialize_team(away)
    h, a = ratings.team_ratings[home], ratings.team_ratings[away]
    return [
        h["home"], h["away"], a["home"], a["away"],
        ratings.expected_goal_difference(home, away), counts[home], counts[away],
        (kickoff - last_match[home]).total_seconds() / 86400 if home in last_match else 0,
        (kickoff - last_match[away]).total_seconds() / 86400 if away in last_match else 0,
    ]


def chronological_features(records, now, pi_type):
    """Labels never update their own features or another unfinished match.

    Provider observation timestamps describe snapshot ingestion. They are not
    falsified to look historical: the caller filters them against `now`.
    Historical feature construction separately assumes results available 3h
    after kickoff. This assumption is disclosed; this is not an archival replay.
    """
    ratings, counts, last_match, pending = pi_type(), Counter(), {}, deque()
    training = []

    def release(before):
        while pending and pending[0]["_kickoff"] + CLOSURE_DELAY < before:
            row = pending.popleft()
            home, away = row["home_team_id"], row["away_team_id"]
            ratings.update_ratings(home, away, row["full_time_home"] - row["full_time_away"])
            counts.update((home, away))
            last_match[home] = last_match[away] = row["_kickoff"]

    for row in sorted(records, key=lambda r: (r["_kickoff"], r["fixture_id"])):
        release(row["_kickoff"])
        home, away = row["home_team_id"], row["away_team_id"]
        if min(counts[home], counts[away]) >= MIN_TEAM_MATCHES:
            label = 0 if row["full_time_home"] > row["full_time_away"] else (
                1 if row["full_time_home"] == row["full_time_away"] else 2
            )
            training.append({
                "fixture_id": row["fixture_id"], "kickoff": row["_kickoff"], "label": label,
                "features": feature_vector(ratings, counts, last_match, home, away, row["_kickoff"]),
            })
        pending.append(row)
    release(now)
    return training, ratings, counts, last_match


def classifier(classifier_type):
    return classifier_type(
        iterations=120, depth=4, learning_rate=0.04, l2_leaf_reg=5.0,
        loss_function="MultiClass", classes_count=3, random_seed=SEED,
        task_type="CPU", thread_count=2, has_time=True, boosting_type="Ordered",
        allow_writing_files=False, verbose=False,
    )


def checked_probabilities(model, features) -> list[list[float]]:
    classes = [int(c) for c in model.classes_]
    if set(classes) != {0, 1, 2}:
        raise ValueError("All three outcome classes are required")
    result = []
    for raw in model.predict_proba(features, thread_count=2):
        values = [float(raw[classes.index(index)]) for index in (0, 1, 2)]
        if any(not math.isfinite(p) or not 0 <= p <= 1 for p in values):
            raise ValueError("Invalid classifier probability")
        if not math.isclose(sum(values), 1, abs_tol=1e-6):
            raise ValueError("Classifier probabilities do not sum to one")
        result.append(values)
    return result


def holdout_metrics(predictions, labels) -> dict:
    brier, rps, log_loss = 0.0, 0.0, 0.0
    for probabilities, label in zip(predictions, labels, strict=True):
        truth = [float(index == label) for index in (0, 1, 2)]
        brier += sum((p - y) ** 2 for p, y in zip(probabilities, truth, strict=True))
        rps += ((probabilities[0] - truth[0]) ** 2 + (
            sum(probabilities[:2]) - sum(truth[:2])
        ) ** 2) / 2
        log_loss -= math.log(max(probabilities[label], 1e-15))
    count = len(labels)
    return {
        "sample_size": count, "brier_multiclass_sum": brier / count,
        "rps": rps / count, "log_loss": log_loss / count,
    }


def fit_league(records, now, dependencies):
    coverage = {}
    try:
        return _fit_league(records, now, dependencies, coverage)
    except Exception as error:  # Retain sample diagnostics, never error text.
        error.training_selection = {**coverage, **getattr(error, "training_selection", {})}
        raise


def _fit_league(records, now, dependencies, coverage):
    pi_type, classifier_type, limits = dependencies
    sample, selection = competition_window(records, now, WINDOW_DAYS, MIN_MATCHES)
    coverage[str(WINDOW_DAYS)] = selection
    if any(r["neutral_venue"] for r in sample):
        raise InsufficientHistory("Semleges pályás adatokhoz külön validált Pi-modell kell.")
    training, ratings, counts, last_match = chronological_features(sample, now, pi_type)
    split = int(len(training) * 0.8)
    if not training or split >= len(training):
        raise InsufficientHistory("Kevés időrendhelyes tanítóminta a Pi-bemelegítés után.")
    # Keep simultaneous fixtures on the same side of the holdout boundary.
    while split > 0 and training[split - 1]["kickoff"] == training[split]["kickoff"]:
        split -= 1
    validation = training[split:]
    first_validation = validation[0]["kickoff"]
    train = [r for r in training[:split] if r["kickoff"] + CLOSURE_DELAY < first_validation]
    if len(train) < MIN_TRAINING_ROWS or len(validation) < MIN_VALIDATION_ROWS:
        raise InsufficientHistory("Legalább 60 tanító- és 20 időben későbbi ellenőrző minta kell.")
    outcomes = Counter(r["label"] for r in train)
    if min(outcomes.get(index, 0) for index in (0, 1, 2)) < 8:
        raise InsufficientHistory("A tanítórészben mindhárom kimenetből legalább 8 példa kell.")
    with limits(limits=1):
        evaluation_model = classifier(classifier_type)
        spec = recipe(__file__, VERSION, random_seed=SEED, catboost=version('catboost'),
            window_days=WINDOW_DAYS, columns=['features', 'label'],
            feature_policy='chronological_pi_with_3h_result_delay', classifier_parameters=classifier_type.__name__)
        source = {'matches': lineage(sample), 'chronological_features': training}
        with fitting('kepler', 'chronological_holdout_train', [[r['features'], r['label']] for r in train], now,
                     recipe=spec, lineage=source):
            evaluation_model.fit([r["features"] for r in train], [r["label"] for r in train])
        record('kepler', 'chronological_validation', [[r['features'], r['label']] for r in validation], now,
               recipe=spec, lineage=source)
        validation_predictions = checked_probabilities(
            evaluation_model, [r["features"] for r in validation]
        )
        evaluation = holdout_metrics(validation_predictions, [r["label"] for r in validation])
        # Fixed hyperparameters, no test-set selection or early stopping on holdout.
        model = classifier(classifier_type)
        with fitting('kepler', 'final_refit', [[r['features'], r['label']] for r in training], now,
                     recipe=spec, lineage=source):
            model.fit([r["features"] for r in training], [r["label"] for r in training])
    evaluation.update(
        training_sample_size=len(train), training_end=train[-1]["kickoff"].isoformat(),
        validation_start=first_validation.isoformat(),
        validation_end=validation[-1]["kickoff"].isoformat(),
        method="fixed_80_20_chronological_holdout_with_3h_purge_and_rolling_pi",
        final_model_refit=True, calibration="unvalidated",
        publication_time_assumption="historical_result_available_kickoff_plus_3h",
    )
    return model, ratings, counts, last_match, sample, training, evaluation, coverage


def predict_fixture(fixture, records, now, fitted_league):
    model, ratings, counts, last_match, sample, training, evaluation, coverage = fitted_league
    check_teams(sample, fixture)
    if fixture["neutral_venue"]:
        raise InsufficientHistory("Semleges pályás meccs: a profil egyelőre várakozik.")
    features = feature_vector(
        ratings, counts, last_match, fixture["home_team_id"], fixture["away_team_id"],
        fixture["kickoff"],
    )
    values = checked_probabilities(model, [features])[0]
    return {
        "fixture_id": fixture["fixture_id"],
        "probabilities": dict(zip(("home", "draw", "away"), values)), "stability": {},
        "model_details": {
            "version": VERSION,
            "library_versions": {name: version(name) for name in ("penaltyblog", "catboost")},
            "training_count": len(training), "history_count": len(sample),
            "cutoff": now.isoformat(), "history_hash": history_hash(sample),
            "training_selection": coverage,
            "windows": [WINDOW_DAYS], "calibration": "unvalidated", "seed": SEED,
            "task_type": "CPU", "thread_count": 2, "features": list(FEATURE_NAMES),
            "rating_model": "penaltyblog.PiRatingSystem", "classifier": "CatBoostClassifier",
            "validation": evaluation, "closure_delay_hours": 3,
            "venue_assumption": "non_neutral_unless_explicitly_marked",
            "publication_time_assumption": "historical_result_available_kickoff_plus_3h",
        },
    }
