"""Advisory integration cannot rewrite tickets, freeze state, or initiate GET work."""

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from goal_analysis.jobs import nemotron
from goal_analysis.portfolio_dashboard import ArthurState, make_handler


def enable(root):
    folder = root / "config"
    folder.mkdir(exist_ok=True)
    (folder / "codex-advisory.json").write_text('{"schema_version":1,"enabled":true}', encoding="utf-8")


@pytest.mark.parametrize("payload", [None, "not-json", "[]", '{"enabled":"true"}', '{"enabled":false}'])
def test_only_explicit_boolean_opt_in_can_run(tmp_path, payload):
    if payload is not None:
        enable(tmp_path)
        (tmp_path / "config/codex-advisory.json").write_text(payload, encoding="utf-8")
    assert not nemotron.enabled(tmp_path)
    assert nemotron.analyze(tmp_path, {}, advisor=object())["status"] == "DISABLED"
    assert not (tmp_path / "data").exists()


def test_advisor_cannot_mutate_saved_input_or_leak_exceptions(tmp_path):
    enable(tmp_path)
    original = {"run_id": "original", "recommendations": [{"legs": [{"decimal_price": 2}]}]}
    before = deepcopy(original)

    class Broken:
        def review_report(self, report):
            report["recommendations"].clear()
            raise RuntimeError("private-key-must-never-be-returned")

    result = nemotron.analyze(tmp_path, original, advisor=Broken())
    assert original == before
    assert result["status"] == "UNAVAILABLE" and not result["automatic_changes"]
    assert "private-key" not in json.dumps(result)


def test_learning_and_report_are_explicit_separate_scopes(tmp_path):
    enable(tmp_path)
    calls = []

    class Reviewer:
        def review_report(self, report):
            calls.append(("report", report))
            return {"status": "COMPLETE"}

        def review_learning(self, report):
            calls.append(("learning", report))
            return {"status": "COMPLETE"}

    for scope in ("report", "learning"):
        assert nemotron.analyze(tmp_path, {"id": scope}, scope=scope, advisor=Reviewer())["status"] == "COMPLETE"
    assert calls == [("report", {"id": "report"}), ("learning", {"id": "learning"})]
    with pytest.raises(ValueError):
        nemotron.analyze(tmp_path, {}, scope="place_wager")


def test_nemotron_get_is_local_only_read_only_and_no_provider_calls(tmp_path, monkeypatch):
    from goal_analysis.agents.nemotron import NemotronAdvisor

    def forbidden(*args, **kwargs):
        raise AssertionError("GET must never start inference")

    monkeypatch.setattr(NemotronAdvisor, "review_report", forbidden)
    monkeypatch.setattr(NemotronAdvisor, "review_learning", forbidden)
    state = ArthurState(tmp_path)
    initial = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, "test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        for endpoint in ("/api/codex-advisory", "/api/nemotron"):
            for suffix in ("", "?run_id=original"):
                with urllib.request.urlopen(base + endpoint + suffix) as reply:
                    result = json.load(reply)
                    assert reply.headers["Cache-Control"] == "no-store"
                assert not result["enabled"]
                assert result["report"] is None and result["learning"] is None
        assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == initial
        for suffix, code, headers in (("?run_id=" + "x" * 161, 400, {}),
                                       ("", 403, {"Host": "outside.example"})):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(base + "/api/codex-advisory" + suffix, headers=headers))
            assert error.value.code == code
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_nightly_advice_runs_after_archive_and_cannot_change_it(tmp_path, monkeypatch):
    from test_nightly import setup_workflow

    workflow, _, calls = setup_workflow(tmp_path)
    observed = []

    def review(root, report, *, scope="report"):
        assert workflow.ledger.list_freezes()  # Advice latency follows freeze.
        observed.append((scope, deepcopy(report)))
        return {"status": "UNAVAILABLE"}  # Must not re-run generation or learning.

    monkeypatch.setattr(nemotron, "analyze", review)
    result = workflow.run()
    assert calls == ["settlement", "generation", "learning"]
    assert [s for s, _ in observed] == ["report", "learning"]
    assert observed[0][1] == workflow.ledger.list_runs()[0]
    assert "nemotron" not in workflow.ledger.list_runs()[0]
    stage = next(s for s in result["job"]["stages"] if s["name"] == "learning")
    assert stage["result"]["nemotron"] == {"report": "UNAVAILABLE", "learning": "UNAVAILABLE"}
    assert workflow.run()["status"] == "ALREADY_FINISHED"
    assert len(observed) == 2


def test_nightly_learning_advice_uses_original_evidence_not_supervisor_overlays(tmp_path, monkeypatch):
    from test_nightly import setup_workflow

    workflow, _, _ = setup_workflow(tmp_path)
    original = {"report_id": "learning-test", "date": "2026-10-07", "status": "COMPLETE",
                "llm": {"status": "COMPLETE"}, "report_hash": "original-hash", "evidence": []}
    workflow.learner = lambda as_of: {**deepcopy(original), "team": {"id": "later"},
                                    "scouting": {"status": "COMPLETE"}, "team_error": None}
    received = []

    def review(root, report, *, scope="report"):
        if scope == "learning":
            received.append(deepcopy(report))
        return {"status": "COMPLETE"}

    monkeypatch.setattr(nemotron, "analyze", review)
    workflow.run()
    assert received == [original]


def test_nightly_rejects_corrupt_saved_report_before_advisory(tmp_path, monkeypatch):
    from test_nightly import setup_workflow

    workflow, _, _ = setup_workflow(tmp_path)

    class CorruptDatabase:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args):
            return self

        def fetchone(self):
            return ('{"run_id":"corrupt"}', "0" * 64)

    monkeypatch.setattr(workflow.ledger, "_connection", CorruptDatabase)
    with pytest.raises(ValueError, match="integrity"):
        workflow._saved_report("corrupt")


def test_cli_standalone_reads_exact_immutable_report(tmp_path, monkeypatch, capsys):
    from test_portfolio_ledger import NOW, report, ticket

    from goal_analysis.storage.portfolio_ledger import PortfolioLedger

    path = Path(__file__).resolve().parents[1] / "scripts/run-arthur-nemotron.py"
    spec = importlib.util.spec_from_file_location("standalone_nemotron_test", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    ledger = PortfolioLedger(tmp_path / "data/arthur/ledger.sqlite3", now=lambda: NOW)
    saved = report(ticket("one", "daily223"))
    ledger.save_run(saved)
    original_bytes = ledger.path.read_bytes()
    received = []

    def review(root, data, **kwargs):
        received.append(deepcopy(data))
        return {"status": "COMPLETE", "automatic_changes": False}

    monkeypatch.setattr(cli, "analyze", review)
    assert cli.main(["--run-id", saved["run_id"]]) == 0
    assert received == [saved]
    assert ledger.path.read_bytes() == original_bytes
    assert "COMPLETE" in capsys.readouterr().out
    assert cli.main(["--run-id", "missing"]) == 2
    assert len(received) == 1
