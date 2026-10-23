"""Phone-sized status views retain displayed evidence and complete exports."""

import json
import threading
from copy import deepcopy
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import urlopen

from goal_analysis import portfolio_dashboard as dashboard


def example_snapshot():
    refs = [{"table": "results", "key": str(i), "hash": "a" * 64} for i in range(200)]
    evidence = [{"evidence_id": "proof", "metrics": {"matches": 200}, "source_refs": refs}]
    learning = {"report_id": "learning-new", "evidence": evidence,
                "findings": [{"evidence_ids": ["proof"]}], "metrics": {"roi": 0.12}}
    card = {"profile_id": "daily223", "profile_name": "DAILY_223", "ticket_id": "daily",
            "legs": [{"home_team": "Home", "away_team": "Away", "market_key": "totals_2_5",
                      "selection_key": "over", "candidate_id": "1",
                      "model_details": {"GAUSS": {"probability": 0.63, "details": refs}}}]}
    report = {"run_id": "run-1", "date": "2026-10-18", "observed_at": "2026-10-18T08:00:00Z",
              "construction_status": "READY", "tickets": [card], "recommendations": [card],
              "preview_tickets": [card], "profiles": [{"profile_id": "daily223"}],
              "astra": {"sources": refs}, "diagnostics": {"rejections": 10},
              "fixtures": [{"fixture_id": "1"}], "odds_coverage": {"fixture_count": 1},
              "decision_data_usage": {"sources": {"current_odds": {"status": "USED_DIRECT"}}},
              "portfolio_objectives": {"ready_recommendations": 24},
              "numeric_models": {"unused": refs}, "odds_history_snapshot": refs,
              "profile_proposals": [card], "strategy_manifest": {"source_files": refs}}
    return {
        "selected_date": "2026-10-18", "latest": report,
        "history": [dict(report, run_id=f"run-{i}") for i in range(15)],
        "tickets": [card], "recommendations": [card], "recommendation_archive": [card],
        "freezes": [{"recommendations": [card]}], "learning": learning,
        "learning_history": [learning, dict(learning, report_id="learning-old")],
        "team": {"latest_evaluation": {"evidence": evidence}, "latest_auditions": [card]},
    }


def test_status_retains_all_visible_cards_models_and_evidence_without_mutation():
    original = example_snapshot()
    before = deepcopy(original)
    view = dashboard.compact_status_snapshot(original)
    assert original == before
    assert len(view["history"]) == 12
    assert view["history"][0] == {
        "run_id": "run-0", "date": "2026-10-18", "observed_at": "2026-10-18T08:00:00Z",
        "construction_status": "READY",
    }
    for field in ("tickets", "recommendations", "freezes"):
        assert view[field] == original[field]
    for field in ("tickets", "recommendations", "preview_tickets", "profiles", "astra",
                  "diagnostics", "fixtures", "odds_coverage", "decision_data_usage",
                  "portfolio_objectives"):
        assert view["latest"][field] == original["latest"][field]
    for field in ("numeric_models", "odds_history_snapshot", "profile_proposals", "strategy_manifest"):
        assert field not in view["latest"]
    assert view["team"]["latest_auditions"] == original["team"]["latest_auditions"]
    assert view["learning"]["metrics"] == original["learning"]["metrics"]
    assert view["learning"]["findings"] == original["learning"]["findings"]
    assert [r["report_id"] for r in view["learning_history"]] == ["learning-old"]
    for report in (view["learning"], *view["learning_history"], view["team"]["latest_evaluation"]):
        evidence = report["evidence"][0]
        assert evidence["source_ref_count"] == 200
        assert evidence["source_refs"] == original["learning"]["evidence"][0]["source_refs"][:50]
        assert evidence["metrics"] == {"matches": 200}
    assert dashboard.german_ticket_text(view) == dashboard.german_ticket_text(original)
    assert len(json.dumps(view)) < len(json.dumps(original)) / 4


def test_full_snapshot_preserves_archive_and_every_reference(tmp_path, monkeypatch):
    from goal_analysis.agents import expert_registry
    from goal_analysis.jobs import expert_advisory, odds_monitor

    source = example_snapshot()
    before = deepcopy(source)
    archive_reads = []

    def recommendations(**kwargs):
        archive_reads.append(kwargs.get("archive", False))
        return source["recommendation_archive"] if kwargs.get("archive") else source["recommendations"]

    monkeypatch.setattr(expert_advisory, "snapshot", lambda *args: {})
    monkeypatch.setattr(expert_registry, "network_registry", lambda *args: [])
    monkeypatch.setattr(odds_monitor, "monitor_status", lambda *args: {})
    monkeypatch.setattr(dashboard, "automation_snapshot", lambda *args: {})
    monkeypatch.setattr(dashboard, "model_environment", dict)
    monkeypatch.setattr(dashboard, "codex_installed", lambda: False)
    state = dashboard.ArthurState(tmp_path)
    state.ledger = SimpleNamespace(
        list_runs=lambda **kwargs: source["history"], list_tickets=lambda **kwargs: [],
        list_recommendations=recommendations, list_freezes=lambda **kwargs: source["freezes"],
    )
    monkeypatch.setattr(state, "team_snapshot", lambda *args: source["team"])
    monkeypatch.setattr(state, "tipster_snapshot", lambda *args: {})
    monkeypatch.setattr(state, "learning_snapshot", lambda: {
        "learning": source["learning"], "learning_history": source["learning_history"],
    })
    compact = state.snapshot("2026-10-18", full_export=False)
    assert True not in archive_reads
    assert compact["recommendation_archive"] == []
    full = state.snapshot("2026-10-18", full_export=True)
    assert archive_reads[-1] is True
    assert full["history"] == source["history"]
    assert full["latest"] == source["history"][0]
    assert full["recommendation_archive"] == source["recommendation_archive"]
    assert full["learning"] == source["learning"]
    assert full["learning_history"] == source["learning_history"]
    assert full["team"] == source["team"]
    assert "status_projection" not in full
    assert state.snapshot("2026-10-18")["history"] == full["history"]
    assert source == before


def test_http_export_explicitly_requests_full_artifacts():
    full = example_snapshot()
    calls = []

    def snapshot(day, *, full_export=False):
        calls.append((day, full_export))
        return full if full_export else dashboard.compact_status_snapshot(full)

    state = SimpleNamespace(snapshot=snapshot, ledger=SimpleNamespace(feedback_summary=dict))
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.make_handler(state, "test"))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        prefix = f"http://127.0.0.1:{server.server_port}"
        with urlopen(prefix + "/api/status?date=2026-10-18", timeout=5) as response:
            status = json.load(response)
        with urlopen(prefix + "/api/export?date=2026-10-18", timeout=5) as response:
            exported = json.load(response)
        assert calls == [("2026-10-18", False), ("2026-10-18", True)]
        assert len(status["history"]) == 12
        assert exported["reports"] == full["history"]
        assert exported["recommendation_archive"] == full["recommendation_archive"]
        assert exported["freezes"] == full["freezes"]
        assert exported["reports"][0]["numeric_models"] == full["history"][0]["numeric_models"]
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
