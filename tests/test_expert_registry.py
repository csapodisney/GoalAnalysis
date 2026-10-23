"""Network view uses existing catalogue and never starts specialist/model work."""

import json
from copy import deepcopy
from types import SimpleNamespace

from goal_analysis.agents.expert_registry import network_registry
from goal_analysis.config.portfolio import PROFILE_IDS, validate_settings
from goal_analysis.engine.portfolio import PROFILES
from goal_analysis.tipsters.catalog import get_catalogue


def test_complete_registry_reuses_existing_names_and_keeps_daily_protected(tmp_path):
    settings = validate_settings({})
    before = deepcopy(settings)
    rows = network_registry(tmp_path, settings, None, {}, {})
    indexed = {row["id"]: row for row in rows}
    assert len(indexed) == len(rows) == len(PROFILE_IDS) + 8
    for profile in PROFILE_IDS:
        assert indexed[profile]["name"] == PROFILES[profile]["name"]
    for persona in get_catalogue():
        assert indexed[persona["profile_id"]]["name"] == persona["name"]
        assert "nem külön betanított LLM" in indexed[persona["profile_id"]]["detail"]
    assert indexed["daily223"]["status"] == "PROTECTED"
    assert indexed["daniel"]["role"] == "Ellenérvek és szerkezeti kockázatok vizsgálata"
    assert "vétót" in indexed["daniel"]["detail"]
    assert indexed["weather_expert"]["status"] == "NOT_RUN"
    assert indexed["motivation_expert"]["status"] == "NOT_RUN"
    assert indexed["grok_review"]["status"] == "DISABLED"
    assert settings == before
    assert not list(tmp_path.iterdir())


def test_disabled_members_and_provisional_members_are_not_presented_as_running(tmp_path):
    settings = validate_settings({"enabled_profiles": ["daily223", "gauss", "laplace"]})
    team = {"members": [{"profile_id": "laplace", "status": "PROBATION"},
                        {"profile_id": "gauss", "status": "SUSPENDED"}]}
    rows = {row["id"]: row for row in network_registry(tmp_path, settings, {}, {}, team)}
    assert rows["kepler"]["status"] == "DISABLED"
    assert rows["laplace"]["status"] == "PROBATION"
    assert rows["gauss"]["status"] == "SUSPENDED"


def test_codex_replacement_reads_optin_without_claiming_nemotron_api(tmp_path):
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "codex-advisory.json").write_text('{"enabled":true}', encoding="utf-8")
    rows = {row["id"]: row for row in network_registry(tmp_path, {}, {}, {}, {})}
    assert rows["codex_advisory"]["status"] == "CONFIGURED"
    assert "Codex" in rows["codex_advisory"]["name"]
    assert "nincs aktív Nemotron API-hívás" in rows["codex_advisory"]["detail"]


def test_current_analysis_descriptive_table_not_numeric_weather_validation(tmp_path):
    report = {"run_id": "current", "date": "2026-10-18"}
    advisory = {"status": "AVAILABLE", **report,
                "weather": {"fixtures": [{"live_weight_eligible": False}]},
                "motivation": {"fixtures": [{"standings_context": {"rules_status": "UNVERIFIED"}}]}}
    before = deepcopy(advisory)
    rows = {row["id"]: row for row in network_registry(tmp_path, {}, report, advisory, {})}
    assert rows["weather_expert"]["status"] == "INSUFFICIENT_DATA"
    assert rows["motivation_expert"]["status"] == "ADVISORY"
    assert "nem bizonyít játékosi motivációt" in rows["motivation_expert"]["detail"]
    assert advisory == before
    advisory["run_id"] = "previous"
    rows = {row["id"]: row for row in network_registry(tmp_path, {}, report, advisory, {})}
    assert rows["weather_expert"]["status"] == "NOT_RUN"
    assert rows["motivation_expert"]["status"] == "NOT_RUN"


def test_dashboard_exposes_overlay_without_inference_or_report_mutation(tmp_path, monkeypatch):
    from goal_analysis import portfolio_dashboard as dashboard
    from goal_analysis.jobs import expert_advisory, odds_monitor

    report = {"run_id": "current", "date": "2026-10-18", "recommendations": []}
    original = deepcopy(report)
    calls = []

    def expert_snapshot(root, day, selected):
        calls.append((root, day, selected))
        return {"status": "NOT_RUN", "date": day}

    def forbidden(*args, **kwargs):
        raise AssertionError("Dashboard GET must not run a specialist or model")

    monkeypatch.setattr(expert_advisory, "snapshot", expert_snapshot)
    monkeypatch.setattr(expert_advisory, "analyze_report", forbidden)
    monkeypatch.setattr(odds_monitor, "monitor_status", lambda *args: {})
    monkeypatch.setattr(dashboard, "automation_snapshot", lambda *args: {})
    monkeypatch.setattr(dashboard, "model_environment", dict)
    monkeypatch.setattr(dashboard, "codex_installed", lambda: False)
    state = dashboard.ArthurState(tmp_path)
    state.ledger = SimpleNamespace(list_runs=lambda **kwargs: [report],
                                   list_tickets=lambda **kwargs: [],
                                   list_recommendations=lambda **kwargs: [],
                                   list_freezes=lambda **kwargs: [])
    monkeypatch.setattr(state, "team_snapshot", lambda *args: {"members": []})
    monkeypatch.setattr(state, "learning_snapshot", dict)
    result = state.snapshot(report["date"])
    assert result["expert_advisory"] == {"status": "NOT_RUN", "date": report["date"]}
    assert len(result["expert_network"]) == len(PROFILE_IDS) + 8
    assert calls == [(tmp_path.resolve(), report["date"], report)]
    assert report == original
    assert "Nemotron API-hívás" in json.dumps(result, ensure_ascii=False)
