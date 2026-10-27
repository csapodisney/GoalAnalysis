"""Only this test package owns marked outside-project SQLite fixtures."""
import json
import socket
import sqlite3
import subprocess
import urllib.request
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import unquote, urlparse

import pytest

from goal_analysis import portfolio_dashboard as dashboard
from goal_analysis.storage import portfolio_ledger as ledger

from . import _money_fixtures as original

__all__ = ["ROOT", "allowed", "allowed_root", "dashboard", "ledger", "original"]

ROOT = Path(__file__).resolve().parents[2]
_BASE = None


def allowed_root(path):
    if isinstance(path, str) and path.startswith("file:"):
        value = unquote(urlparse(path).path)
        if value.startswith("/") and len(value) > 2 and value[2] == ":":
            value = value[1:]
        path = value
    path = Path(path).resolve()
    assert _BASE is not None and path.is_relative_to(_BASE)
    assert not path.is_relative_to(ROOT) and path != ROOT
    assert json.loads((_BASE / "TESTONLY-MONEY.json").read_text(encoding="utf-8")) == {
        "TESTONLY": True, "scope": "actual-money-contract-tests", "root": str(_BASE)}
    return path


allowed = allowed_root


@pytest.fixture(autouse=True)
def isolated_io(monkeypatch, tmp_path, request):
    global _BASE
    _BASE = tmp_path.resolve()
    assert not _BASE.is_relative_to(ROOT) and not ROOT.is_relative_to(_BASE)
    (_BASE / "TESTONLY-MONEY.json").write_text(json.dumps({
        "TESTONLY": True, "scope": "actual-money-contract-tests", "root": str(_BASE)}), encoding="utf-8")
    original_connect = sqlite3.connect
    original_init = ledger.PortfolioLedger.__init__
    forbidden = Mock(side_effect=AssertionError("NO_PRODUCTION_DB_PROVIDER_NETWORK_DECRYPT_PROCESS"))
    def connect(database, *args, **kwargs):
        allowed_root(database)
        return original_connect(database, *args, **kwargs)
    def initialize(self, path, *args, **kwargs):
        allowed_root(path)
        return original_init(self, path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(ledger.PortfolioLedger, "__init__", initialize)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    if not request.node.module.__name__.endswith("test_money_http"):
        monkeypatch.setattr(socket, "create_connection", forbidden)
        monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    from goal_analysis.jobs.the_stats_context import Resources
    from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
    from goal_analysis.storage import Database
    monkeypatch.setattr(Database, "__init__", forbidden)
    monkeypatch.setattr(TheStatsPrimaryClient, "__init__", forbidden)
    monkeypatch.setattr(Resources, "__init__", forbidden)
    yield forbidden
    forbidden.assert_not_called()
