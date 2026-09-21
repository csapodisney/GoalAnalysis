"""CLI regression: a failed result refresh must not hide a successful new collection."""

import importlib.util
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest
from test_daily_223_live import config
from test_portfolio_ledger import NOW, report, ticket

from goal_analysis.agents import canonical_sha256
from goal_analysis.storage.portfolio_ledger import PortfolioLedger


@pytest.mark.parametrize("preview_only", [False, True])
@pytest.mark.parametrize("settlement_fails,expected_exit", [(True, 2), (False, 0)])
def test_combined_settlement_and_live_preserve_collection_and_failure_status(
    tmp_path, monkeypatch, capsys, settlement_fails, expected_exit, preview_only
):
    source = Path(__file__).resolve().parents[1] / "scripts" / "run-arthur.py"
    spec = importlib.util.spec_from_file_location("arthur_cli_regression", source)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(cli, "datetime", Clock)
    secret = "private-provider-key-for-cli-regression"
    monkeypatch.setenv("API_FOOTBALL_KEY", secret)
    monkeypatch.setenv("THE_ODDS_API_KEY", "synthetic-odds-key")
    config_path = tmp_path / "config" / "daily223-live.json"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(config()), encoding="utf-8")

    ledger = PortfolioLedger(tmp_path / "data" / "arthur" / "ledger.sqlite3", now=lambda: NOW)
    monkeypatch.setattr(cli, "PortfolioLedger", lambda path: ledger)
    football, odds, reviewer = object(), object(), object()
    monkeypatch.setattr(cli, "ApiFootballClient", lambda **kwargs: football)
    monkeypatch.setattr(cli, "Daily223OddsFeed", lambda *args: odds)
    monkeypatch.setattr(cli, "make_reviewer", lambda settings: reviewer)
    calls = []

    def refresh_results(client):
        assert client is football
        calls.append("settle")
        if settlement_fails:
            raise RuntimeError(f"Temporary provider failure: {secret}")
        return {"checked_fixtures": 0, "updated_tickets": 0}

    monkeypatch.setattr(ledger, "refresh_results", refresh_results)
    issued = report(ticket("daily", "daily223"), ticket("specialist", "kronikas"))
    issued.update(
        construction_status="COMPLETE",
        profiles=[],
        diagnostics=[],
        astra={"status": "COMPLETE", "model": "gpt-6-astra"},
        real_wager_placed=False,
    )
    for current in issued["tickets"]:
        for leg in current["legs"]:
            leg.update(home_team="Home", away_team="Away")
    if preview_only:
        issued["preview_tickets"] = issued["tickets"]
        issued["tickets"] = []
        issued["construction_status"] = "ODDS_PENDING"
        for current in issued["preview_tickets"]:
            current.update(
                status="ODDS_PENDING", combined_price=None, quality_warnings=["Szorzó hiányzik."]
            )
            current.pop("ticket_id", None)
            for leg in current["legs"]:
                leg["decimal_price"] = None
    issued["report_sha256"] = canonical_sha256(issued)

    def collect(*args):
        assert args[2] is football and args[3] is odds
        assert args[6] is reviewer and args[7] is ledger
        assert calls == ["settle"]
        calls.append("collect")
        return {"schema_version": 3, "artifacts": {"report": deepcopy(issued)}}

    monkeypatch.setattr(cli, "run_portfolio", collect)
    assert cli.main(["--settle", "--live", "--date", NOW.date().isoformat()]) == expected_exit
    assert calls == ["settle", "collect"]
    saved = ledger.list_runs(date=NOW.date())[0]
    persisted = json.loads(
        (tmp_path / "reports" / "arthur" / issued["run_id"] / "report.json").read_text("utf-8")
    )
    assert persisted == saved
    assert len(saved["tickets"]) == (0 if preview_only else 2)
    assert all(item["status"] == "READY" for item in saved["tickets"])
    assert len(ledger.list_tickets(date=NOW.date())) == (0 if preview_only else 2)
    assert saved["real_wager_placed"] is False
    assert secret not in json.dumps(saved)
    assert secret not in capsys.readouterr().out
    diagnostics = [item for item in saved["diagnostics"] if item["code"] == "SETTLEMENT_FAILED"]
    if settlement_fails:
        assert saved["settlement_status"] == "FAILED"
        assert len(diagnostics) == 1
        assert "[REDACTED]" in diagnostics[0]["message"]
    else:
        assert not diagnostics
        assert saved.get("settlement_status") != "FAILED"
    without_hash = {key: value for key, value in saved.items() if key != "report_sha256"}
    assert saved["report_sha256"] == canonical_sha256(without_hash)
