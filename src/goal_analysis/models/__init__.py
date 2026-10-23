"""Independent, local experimental models; no network or model-API calls.

API: predict_models(all_daily_fixtures, normalized_history, aware_now).
Probabilities are model estimates, not calibrated guarantees or proof of value.
No cached pickle/model deserialization is performed. Optional artifact_dir saves
immutable JSON training snapshots only, never serialized executable models.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import gauss, kepler, laplace
from ._data import InsufficientHistory, aware_time, diagnostic, normalize_fixtures, prepare_history

__all__ = ["predict_models"]

_PROFILES = {"gauss": gauss, "kepler": kepler, "laplace": laplace}


def _result(profile_id, status, message, diagnostics=None):
    return {
        "profile_id": profile_id, "status": status, "message": message,
        "predictions": [], "diagnostics": list(diagnostics or []),
    }


def predict_models(
    fixtures: list[dict], history_records: list[dict], now: datetime,
    enabled_profiles: tuple = ("gauss", "kepler"), artifact_dir: Path | None = None,
) -> dict:
    from goal_analysis.storage.training_datasets import DatasetStore

    from .datasets import Capture

    with Capture() as capture:
        results = _predict_models(fixtures, history_records, now, enabled_profiles, artifact_dir)
    archived = {'status': 'NOT_REQUESTED'}
    if artifact_dir is not None and capture.datasets:
        root = Path(__file__).resolve().parents[3]
        if not Path(artifact_dir).resolve().is_relative_to(root):
            raise ValueError('Dataset archive outside project')
        try:
            archive_path = (Path(artifact_dir) / 'datasets').resolve()
            if not archive_path.is_relative_to(root):
                raise ValueError('Dataset archive symlink outside project')
            store = DatasetStore(archive_path)
            for value in capture.datasets.values():
                store.put(value)
            archived = {'status': 'ARCHIVED', 'dataset_count': len(capture.datasets), 'scope': 'SHARED_RUN_ARCHIVE'}
        except (OSError, ValueError):
            archived = {'status': 'UNAVAILABLE', 'reason': 'DATASET_ARCHIVE_FAILED'}
    for profile, result in results.items():
        result['training_datasets'] = {'version': 'training-dataset-v1', 'mode': 'SHADOW',
            'manifests': capture.manifests(profile),
            'uses': [row for row in capture.uses if row['profile'] in {profile, 'shared'}], 'archive': archived}
    return results


def _predict_models(
    fixtures: list[dict], history_records: list[dict], now: datetime,
    enabled_profiles: tuple = ('gauss', 'kepler'), artifact_dir: Path | None = None,
) -> dict:
    """Run on the complete supplied fixture universe, never an Arthur shortlist.

    A failed league/fixture is isolated from other leagues. An unavailable
    dependency or failed fit never falls back to a hand-written score formula.
    READY can be partial: diagnostics retain every withheld fixture and cause.
    """
    profiles = tuple(dict.fromkeys(str(profile).lower() for profile in enabled_profiles))
    try:
        now = aware_time(now)
        if not isinstance(fixtures, list) or not isinstance(history_records, list):
            raise TypeError("Fixture and history lists required")
        if artifact_dir is not None:
            root = Path(__file__).resolve().parents[3]
            if not Path(artifact_dir).resolve().is_relative_to(root):
                raise ValueError("artifact_dir must be inside the project")
        upcoming, fixture_issues = normalize_fixtures(fixtures, now)
        excluded_ids = {row["provider_fixture_id"] for row in upcoming}
        history, history_issues = prepare_history(history_records, now, excluded_ids)
        from .datasets import lineage, recipe, record

        record('shared', 'normalized_history_universe', lineage(history), now,
               recipe=recipe(__file__, 'normalized-model-history-v1', excluded_target_ids=sorted(excluded_ids)),
               lineage={'diagnostics': history_issues, 'scope': 'PREPARED_UNIVERSE_NOT_ALL_ROWS_USED_IN_EACH_FIT'})
    except (TypeError, ValueError, OSError):
        return {p: _result(p, "MODEL_ERROR", "Érvénytelen modellbemenet.", [
            diagnostic("INVALID_INPUT")
        ]) for p in profiles}

    results = {}
    for profile_id in profiles:
        issues = [dict(issue) for issue in fixture_issues + history_issues]
        module = _PROFILES.get(profile_id)
        if module is None:
            results[profile_id] = _result(profile_id, "MODEL_ERROR", "Ismeretlen modellprofil.")
            continue
        if not upcoming:
            results[profile_id] = _result(
                profile_id, "NO_FIXTURES", "Nincs még el nem kezdődött mérkőzés.", issues
            )
            continue
        if not history:
            results[profile_id] = _result(
                profile_id, "INSUFFICIENT_HISTORY", "Nincs használható, lezárt mérkőzéselőzmény.", issues
            )
            continue
        try:
            dependencies = module.load_dependencies()
        except (ImportError, OSError):
            results[profile_id] = _result(
                profile_id, "DEPENDENCY_MISSING", "A helyi modellkönyvtár nem érhető el.",
                issues + [diagnostic("DEPENDENCY_MISSING", profile_id=profile_id)],
            )
            continue
        except Exception as error:  # noqa: BLE001 - isolate optional binary-library failures
            results[profile_id] = _result(
                profile_id, "MODEL_ERROR", "A modellkönyvtár nem inicializálható.",
                issues + [diagnostic("DEPENDENCY_ERROR", error_type=type(error).__name__)],
            )
            continue

        result = _result(profile_id, "INSUFFICIENT_HISTORY", "A profil több történeti adatot vár.")
        result["diagnostics"] = issues
        fitted_leagues, failures, model_error = {}, {}, False

        def record_selection(league, coverage, target_result=result):
            if not isinstance(coverage, dict) or not coverage or any(
                not isinstance(v, dict) or v.get("policy") != "iterative_four_appearance_team_core_v1"
                for v in coverage.values()
            ):
                return
            selections = target_result.setdefault("training_selection", {})
            if league in selections:
                return
            selections[league] = coverage
            for selection in coverage.values():
                if selection["removed_rows"]:
                    target_result["diagnostics"].append(diagnostic(
                        "SPARSE_TRAINING_CORE", league_id=league, **selection,
                    ))

        for fixture in upcoming:
            league = fixture["league_id"]
            league_history = [row for row in history if row["league_id"] == league]
            try:
                if league in failures:
                    raise failures[league]
                if league not in fitted_leagues:
                    try:
                        fitted_leagues[league] = module.fit_league(league_history, now, dependencies)
                        fitted = fitted_leagues[league]
                        if isinstance(fitted, tuple) and fitted:
                            record_selection(league, fitted[-1])
                    except Exception as error:
                        record_selection(league, getattr(error, "training_selection", None))
                        failures[league] = error
                        raise
                prediction = module.predict_fixture(
                    fixture, league_history, now, fitted_leagues[league]
                )
                result["predictions"].append(prediction)
            except InsufficientHistory as error:
                result["diagnostics"].append(diagnostic(
                    "INSUFFICIENT_HISTORY", fixture_id=fixture["fixture_id"],
                    league_id=league, message=str(error),
                ))
            except Exception as error:  # noqa: BLE001 - one failed model must not abort the day
                model_error = True
                # Dependency exception text may contain environment paths; retain only its class.
                result["diagnostics"].append(diagnostic(
                    "MODEL_ERROR", fixture_id=fixture["fixture_id"], league_id=league,
                    error_type=type(error).__name__,
                ))
        if result["predictions"]:
            result["status"] = "READY"
            result["message"] = (
                f"{len(result['predictions'])}/{len(upcoming)} meccs: kísérleti, "
                "nem kalibrált modellbecslés."
            )
        elif model_error:
            result["status"] = "MODEL_ERROR"
            result["message"] = "A numerikus illesztés nem adott használható becslést."
        results[profile_id] = result
    return results
