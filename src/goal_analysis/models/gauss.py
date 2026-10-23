"""Four actual penaltyblog fits: two model families, two fixed time windows."""

from __future__ import annotations

import importlib
import math
from importlib.metadata import version

from ._data import (
    InsufficientHistory,
    check_teams,
    competition_window,
    history_hash,
)
from .datasets import fitting, goal_rows, lineage, recipe

WINDOWS = (365, 730)
MIN_MATCHES = 80
VERSION = "gauss-1.1-sparse-core"


def load_dependencies():
    models = importlib.import_module("penaltyblog.models")
    limits = importlib.import_module("threadpoolctl").threadpool_limits
    return (
        ("bivariate_poisson", models.BivariatePoissonGoalModel),
        ("dixon_coles", models.DixonColesGoalModel),
    ), limits


def probabilities(grid) -> dict:
    cells = [float(value) for row in grid.grid for value in row]
    if not cells or any(not math.isfinite(p) or not 0 <= p <= 1 for p in cells):
        raise ValueError("Invalid scoreline probability grid")
    if not math.isclose(sum(cells), 1, abs_tol=1e-6):
        raise ValueError("Scoreline probabilities do not sum to one")
    values = dict(zip(("home", "draw", "away"), map(float, grid.home_draw_away)))
    values.update(
        over25=float(grid.total_goals("over", 2.5)),
        under25=float(grid.total_goals("under", 2.5)),
        btts_yes=float(grid.btts_yes),
    )
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in values.values()):
        raise ValueError("Invalid probability grid")
    if not math.isclose(sum(values[k] for k in ("home", "draw", "away")), 1, abs_tol=1e-6):
        raise ValueError("1X2 probabilities do not sum to one")
    if not math.isclose(values["over25"] + values["under25"], 1, abs_tol=1e-6):
        raise ValueError("Goal probabilities do not sum to one")
    return values


def fit_league(records, now, dependencies):
    coverage = {}
    try:
        return _fit_league(records, now, dependencies, coverage)
    except Exception as error:  # Retain sample diagnostics, never error text.
        error.training_selection = {**coverage, **getattr(error, "training_selection", {})}
        raise


def _fit_league(records, now, dependencies, coverage):
    types, limits = dependencies
    fitted, samples = {}, {}
    for days in WINDOWS:
        try:
            sample, selection = competition_window(records, now, days, MIN_MATCHES)
        except InsufficientHistory as error:
            error.training_selection = {**coverage, **error.training_selection}
            raise
        coverage[str(days)] = selection
        samples[days] = sample
        arguments = (
            [r["full_time_home"] for r in sample],
            [r["full_time_away"] for r in sample],
            [r["home_team_id"] for r in sample],
            [r["away_team_id"] for r in sample],
        )
        for name, model_type in types:
            # No scraper, remote fit, automatic hyperparameter search or heuristic fallback.
            model = model_type(*arguments)
            if any(r["neutral_venue"] for r in sample):
                # Older penaltyblog releases have no neutral-venue parameter.
                raise InsufficientHistory("Semleges pályás adatokhoz külön validált modell kell.")
            with fitting('gauss', name + '_' + str(days), goal_rows(sample), now,
                    recipe=recipe(__file__, VERSION, family=name, window_days=days,
                        columns=['home_goals', 'away_goals', 'home_team_id', 'away_team_id'],
                        penaltyblog=version('penaltyblog'), maxiter=500, ftol=1e-8), lineage=lineage(sample)), limits(limits=1):
                model.fit(minimizer_options={"maxiter": 500, "ftol": 1e-8})
            # penaltyblog 1.12.2 BaseGoalsModel._fit raises on optimizer success=False
            # and only then sets fitted=True. Also reject any non-finite solution.
            if not model.fitted or not math.isfinite(float(model.loglikelihood)):
                raise ValueError("Numerical optimization did not produce a fitted model")
            if any(not math.isfinite(float(value)) for value in model.get_params().values()):
                raise ValueError("Non-finite fitted parameters")
            fitted[f"{name}_{days}"] = model
    return fitted, samples, coverage


def predict_fixture(fixture, records, now, fitted_league):
    fitted, samples, coverage = fitted_league
    if fixture["neutral_venue"]:
        raise InsufficientHistory("Semleges pályás meccs: a profil egyelőre várakozik.")
    for sample in samples.values():
        check_teams(sample, fixture)
    variants = {
        name: probabilities(model.predict(
            fixture["home_team_id"], fixture["away_team_id"], max_goals=15, normalize=True
        )) for name, model in fitted.items()
    }
    keys = next(iter(variants.values())).keys()
    mean = {key: sum(v[key] for v in variants.values()) / len(variants) for key in keys}
    stability = {
        key: max(v[key] for v in variants.values()) - min(v[key] for v in variants.values())
        for key in keys
    }
    return {
        "fixture_id": fixture["fixture_id"], "probabilities": mean, "stability": stability,
        "model_details": {
            "version": VERSION, "library_versions": {"penaltyblog": version("penaltyblog")},
            "training_count": len(samples[730]), "cutoff": now.isoformat(),
            "history_hash": history_hash(samples[730]), "windows": list(WINDOWS),
            "window_training_counts": {str(d): len(s) for d, s in samples.items()},
            "training_selection": coverage,
            "calibration": "unvalidated", "ensemble": "equal_mean_of_four_fits",
            "stability_definition": "max_minus_min_probability_across_four_fits",
            "variants": variants, "closure_delay_hours": 3,
            "fits": {name: {
                "fitted": bool(model.fitted), "log_likelihood": float(model.loglikelihood),
                "parameter_count": len(model.get_params()),
            } for name, model in fitted.items()},
            "venue_assumption": "non_neutral_unless_explicitly_marked",
        },
    }
