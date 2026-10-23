"""Explicit local opt-in for bounded, non-mutating Codex advisory reviews."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


def enabled(root: Path) -> bool:
    """Missing/malformed configuration is disabled, including isolated tests."""
    try:
        config = Path(root) / "config/codex-advisory.json"
        if not config.is_file():
            # One-release migration path: the old boolean contained no secret.
            config = Path(root) / "config/nemotron.json"
        value = json.loads(config.read_text("utf-8-sig"))
        return isinstance(value, dict) and value.get("enabled") is True
    except (OSError, ValueError):
        return False


def analyze(root: Path, report: dict, *, scope="report", advisor=None) -> dict:
    """Do not let an optional advisor fail publication or expose raw exceptions."""
    if scope not in {"report", "learning"}:
        raise ValueError("Unknown advisory scope")
    if not enabled(root):
        return {"status": "DISABLED", "automatic_changes": False}
    try:
        from goal_analysis.agents.nemotron import CodexAdvisor

        service = advisor or CodexAdvisor(root)
        method = service.review_report if scope == "report" else service.review_learning
        return method(deepcopy(report))
    except Exception:  # noqa: BLE001 - preserve the already saved primary artifact
        return {"status": "UNAVAILABLE", "automatic_changes": False,
                "message": "A Codex-kiegészítés nem érhető el; az eredeti mentés megmaradt."}


def snapshot(root: Path, run_id=None) -> dict:
    """Local status reads never claim work or call any provider."""
    from goal_analysis.agents.nemotron import MODEL, CodexAdvisor

    if run_id is not None and (not isinstance(run_id, str) or not 1 <= len(run_id) <= 160):
        raise ValueError("Érvénytelen elemzésazonosító.")
    try:
        result = CodexAdvisor(root).snapshot(run_id=run_id)
    except Exception:  # noqa: BLE001 - no store paths or response contents in HTTP errors
        result = {"configured": False, "model": MODEL, "report": None, "learning": None,
                  "status": "UNAVAILABLE", "message": "A Codex-napló nem olvasható."}
    return {**result, "enabled": enabled(root), "automatic_changes": False}
