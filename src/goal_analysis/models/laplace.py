"""Audited local challenger: one fixed-window independent Poisson baseline.

Only the already installed penaltyblog library runs, never downloaded code.
Re-fitting numeric parameters is real model training; no LLM weights are changed.
"""

from __future__ import annotations

import importlib
import math
from importlib.metadata import version

from ._data import InsufficientHistory, check_teams, competition_window, history_hash
from .datasets import fitting, goal_rows, lineage, recipe
from .gauss import probabilities

VERSION = "laplace-poisson-1.1-sparse-core"
WINDOW_DAYS = 730
MIN_MATCHES = 80
SOURCE_URL = "https://github.com/martineastwood/penaltyblog"


def load_dependencies():
    return (importlib.import_module("penaltyblog.models").PoissonGoalsModel,
            importlib.import_module("threadpoolctl").threadpool_limits)


def fit_league(records, now, dependencies):
    coverage = {}
    try:
        return _fit_league(records, now, dependencies, coverage)
    except Exception as error:  # Retain sample diagnostics, never error text.
        error.training_selection = {**coverage, **getattr(error, "training_selection", {})}
        raise


def _fit_league(records, now, dependencies, coverage):
    model_type, limits = dependencies
    sample, selection = competition_window(records, now, WINDOW_DAYS, MIN_MATCHES)
    coverage[str(WINDOW_DAYS)] = selection
    # Use the same conservative venue boundary as the existing Gauss adapter.
    if any(r["neutral_venue"] for r in sample):
        raise InsufficientHistory("Semleges pályás adatokhoz külön validált modell kell.")
    model = model_type(
        [r["full_time_home"] for r in sample], [r["full_time_away"] for r in sample],
        [r["home_team_id"] for r in sample], [r["away_team_id"] for r in sample],
    )
    with fitting('laplace', 'poisson_' + str(WINDOW_DAYS), goal_rows(sample), now,
            recipe=recipe(__file__, VERSION, window_days=WINDOW_DAYS,
                columns=['home_goals', 'away_goals', 'home_team_id', 'away_team_id'],
                penaltyblog=version('penaltyblog'), maxiter=500, ftol=1e-8), lineage=lineage(sample)), limits(limits=1):
        model.fit(minimizer_options={"maxiter": 500, "ftol": 1e-8})
    if not model.fitted or not math.isfinite(float(model.loglikelihood)):
        raise ValueError("Poisson optimization did not converge")
    if any(not math.isfinite(float(value)) for value in model.get_params().values()):
        raise ValueError("Non-finite fitted parameter")
    return model, sample, coverage


def predict_fixture(fixture, records, now, fitted_league):
    model, sample, coverage = fitted_league
    if fixture["neutral_venue"]:
        raise InsufficientHistory("Semleges pályás meccs: a profil egyelőre várakozik.")
    check_teams(sample, fixture)
    values = probabilities(model.predict(
        fixture["home_team_id"], fixture["away_team_id"], max_goals=15, normalize=True,
    ))
    return {
        "fixture_id": fixture["fixture_id"], "probabilities": values, "stability": {},
        "model_details": {
            "version": VERSION, "library_versions": {"penaltyblog": version("penaltyblog")},
            "training_count": len(sample), "cutoff": now.isoformat(),
            "history_hash": history_hash(sample), "windows": [WINDOW_DAYS],
            "training_selection": coverage,
            "calibration": "unvalidated", "ensemble": "none_independent_poisson_baseline",
            "stability_definition": "not_measured_single_fixed_model",
            "source_url": SOURCE_URL, "closure_delay_hours": 3,
            "venue_assumption": "non_neutral_unless_explicitly_marked",
        },
    }
