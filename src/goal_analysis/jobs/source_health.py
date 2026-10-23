"""Small, read-only dashboard projection; old run bodies remain immutable."""
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

from goal_analysis.jobs.league_coverage import inspect_league_coverage
from goal_analysis.jobs.source_resolution import build_source_resolution
from goal_analysis.normalization.canonical import utc


def _league_summary(value):
    result = {key: value.get(key) for key in ("status", "as_of", "active_competitions", "counts")}
    result["leagues"] = []
    for row in value.get("leagues", []):
        item = {key: row.get(key) for key in ("api_football_id", "competition_id", "scope",
            "mapping_status", "mapping_method", "collector_enabled", "observed_in_run")}
        stats = row.get("the_stats")
        item["the_stats"] = {key: stats.get(key) for key in ("name", "country", "advertised")} if stats else None
        result["leagues"].append(item)
    return result


def source_health(root, report, *, leagues, now):
    """Re-audit old archived inputs at their original cutoff, without backfilling."""
    root, now = Path(root).resolve(), utc(now)
    original = report or {}
    if not original:
        coverage = inspect_league_coverage(root, {}, as_of=now, leagues=leagues)
        return {"mode": "SHADOW", "status": "MISSING", "reason": "NO_RUN",
                "rows": [], "league_coverage": _league_summary(coverage), "external_requests": 0}
    cutoff = utc(original.get("finished_at") or original["observed_at"])
    if cutoff > now:
        raise ValueError("FUTURE_RUN")
    summary = original.get("source_resolution") or {}
    origin, artifact_hash = "RECORDED_RUN", None
    if not isinstance(summary.get("provider_matrix"), list):
        artifacts = {}
        run_id = original.get("run_id")
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", run_id):
            raise ValueError("INVALID_RUN_ID")
        archive = (root / "reports/arthur").resolve()
        path = (archive / run_id / "candidate_input.json").resolve()
        if not path.is_relative_to(archive):
            raise ValueError("INVALID_ARCHIVE_PATH")
        if path.is_file():
            raw = path.read_bytes()
            candidates = json.loads(raw)
            if (candidates.get("date") != original["date"] or
                    utc(candidates["observed_at"]) != utc(original["observed_at"])):
                raise ValueError("ARCHIVE_RUN_MISMATCH")
            artifacts["candidate_input"] = candidates
            artifact_hash = hashlib.sha256(raw).hexdigest()
        summary = build_source_resolution(original, artifacts, as_of=cutoff)["summary"]
        origin = "ARCHIVED_INPUT_AUDIT"
    if summary.get("mode") != "SHADOW" or summary.get("effective_data_path") != "LEGACY_UNCHANGED":
        raise ValueError("INVALID_SOURCE_AUDIT_MODE")
    audit_time = utc(summary["as_of"])
    if audit_time > cutoff:
        raise ValueError("FUTURE_SOURCE_AUDIT")
    collection = original.get("sports_supplement", {}).get("the_stats_api") or {}
    coverage = collection.get("league_coverage")
    if not isinstance(coverage, dict) or "leagues" not in coverage:
        coverage = inspect_league_coverage(root, original, as_of=cutoff, leagues=leagues)
    if coverage.get("as_of") is not None and utc(coverage["as_of"]) > cutoff:
        raise ValueError("FUTURE_LEAGUE_AUDIT")
    return deepcopy({"mode": "SHADOW", "status": "AVAILABLE", "coverage_status": summary.get("status"),
        "effective_data_path": "LEGACY_UNCHANGED", "run_id": original.get("run_id"),
        "date": original.get("date"), "as_of": summary["as_of"], "derived_at": now.isoformat(),
        "origin": origin, "candidate_input_sha256": artifact_hash,
        "rows": summary["provider_matrix"], "matrix_scope": summary.get("matrix_scope"),
        "conflict_fields": summary.get("conflict_fields"),
        "unresolved_conflict_fields": summary.get("unresolved_conflict_fields"),
        "league_coverage": _league_summary(coverage), "external_requests": 0})
