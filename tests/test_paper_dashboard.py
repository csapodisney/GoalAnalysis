"""Read-only one-EUR paper ledger through the local HTTP boundary."""

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from test_model_integration import predictor_fixture
from test_portfolio_flow import NOW, RecordedReviewer, environment

from goal_analysis.jobs.portfolio import run_portfolio
from goal_analysis.portfolio_dashboard import ArthurState, make_handler
from goal_analysis.storage.portfolio_ledger import PortfolioLedger


def _database_rows(path):
    with sqlite3.connect(path) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in ("portfolio_runs", "portfolio_recommendations",
                              "portfolio_freezes", "portfolio_wagers")}


def test_read_only_paper_view_uses_current_daily_records_not_archive_duplicates(tmp_path):
    config, football, odds, cache, _, _, _ = environment(tmp_path)
    report = run_portfolio(config, {"operating_contract_version": 1}, football, odds, cache,
                           NOW.date(), RecordedReviewer(),
                           clock=lambda: NOW, model_predictor=predictor_fixture)["artifacts"]["report"]
    ledger = PortfolioLedger(tmp_path / "data/arthur/ledger.sqlite3", now=lambda: NOW)
    ledger.save_run(report)
    ledger.freeze_run(report["run_id"])
    state = ArthurState(tmp_path)
    before = _database_rows(ledger.path)
    result = state.paper_performance()
    assert result["unit_stake_eur"] == 1
    assert result["summary"]["records"] == len(ledger.list_recommendations())
    assert result["summary"]["eligible_records"] > 0
    assert result["summary"]["total_stake_eur"] == result["summary"]["eligible_records"]
    assert result["summary"]["settled_stake_eur"] == 0
    assert result["summary"]["open_stake_eur"] == result["summary"]["total_stake_eur"]
    assert result["summary"]["cash_balance_eur"] == -result["summary"]["total_stake_eur"]
    assert result["summary"]["realized_net_eur"] == 0
    assert not ledger.performance(mode="actual")["summary"]["tickets"]
    assert _database_rows(ledger.path) == before
    empty_month = state.paper_performance("2000-01")
    assert empty_month["summary"]["records"] == 0
    assert empty_month["overall_summary"] == result["overall_summary"]

    # A separately recorded EUR 1 wager must neither remove this recommendation
    # from the EUR 1 experiment nor replace its original quote with the accepted
    # real-money quote. The existing actual ledger still retains the real wager.
    actual = ledger.record_wager(report["tickets"][0]["ticket_id"], actual_combined_price=9.5)
    assert actual["wager"]["stake_eur"] == 1
    after_actual = state.paper_performance()
    assert after_actual["summary"] == result["summary"]
    assert after_actual["records"] == result["records"]
    assert ledger.performance(mode="actual")["summary"]["tickets"] == 1


def test_paper_http_filters_export_and_local_only_no_provider_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_KEY", "not-in-the-export")
    state = ArthurState(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, "local-session"))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/api/paper-performance?month=2026-10&download=1") as reply:
            body = reply.read()
            assert reply.headers["Content-Disposition"] == 'attachment; filename="Arthur-1EUR-2026-10.json"'
            assert reply.headers["Cache-Control"] == "no-store"
        payload = json.loads(body)
        assert payload["month"] == "2026-10" and payload["unit_stake_eur"] == 1
        assert b"not-in-the-export" not in body
        assert payload["summary"]["total_stake_eur"] == 0
        for bad in ("2026-13", "2026-1", "nan", ".."):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + "/api/paper-performance?month=" + bad)
            assert error.value.code == 400
        request = urllib.request.Request(base + "/api/paper-performance", headers={"Host": "outside.example"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
