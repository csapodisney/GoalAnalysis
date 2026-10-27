"""UNRUN public controls: pure transport spies and owned ephemeral loopback only."""
import http.client
import io
import json
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.request
from email.message import Message
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from goal_analysis import portfolio_dashboard as dashboard

TOKEN = "TESTONLY_REJECTION_TRANSPORT_TOKEN"
SCOPE = "local-http-auth-rejection-transport-repair"
ACTUAL_ROOT = Path("C:/AI-Work/GoalAnalysis").resolve()


@pytest.fixture(autouse=True)
def forbidden_production_io(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("NO_DB_PROVIDER_DECRYPT_PROCESS_MODEL_OR_ROOT_HTTP")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    from goal_analysis import models
    from goal_analysis.jobs.the_stats_context import Resources
    from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
    from goal_analysis.storage import Database
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger
    monkeypatch.setattr(models, "predict_models", forbidden)
    for cls in (Resources, TheStatsPrimaryClient, Database, PortfolioLedger):
        monkeypatch.setattr(cls, "__init__", forbidden)


def state():
    calls = []
    def settings(payload):
        calls.append(("settings", payload))
        return {"TESTONLY": True}
    def stale(*_args, **_kwargs):
        calls.append(("money", "TESTONLY"))
        raise RuntimeError("SETTLEMENT_REVISION_CHANGED")
    return SimpleNamespace(update_settings=settings,
        ledger=SimpleNamespace(confirm_bookmaker_return=stale)), calls


def pure_handler(*, length="7", encoding=None, chunks=None, read_error=None):
    value, calls = state()
    handler = object.__new__(dashboard.make_handler(value, TOKEN))
    events = []
    headers = Message()
    for key, item in (("Host", "127.0.0.1:19001"), ("X-Arthur-Token", "wrong"),
            ("Content-Length", length), ("Content-Type", "application/json")):
        if item is not None:
            headers[key] = item
    if encoding:
        headers["Transfer-Encoding"] = encoding
    handler.headers = headers
    handler.server = SimpleNamespace(server_port=19001)
    handler.path = "/api/bookmaker-return"
    handler.close_connection = False
    class Sink(io.BytesIO):
        def write(self, data):
            events.append(("write", len(data)))
            return super().write(data)
        def flush(self):
            events.append(("flush",))
            return super().flush()
    class Reader:
        def __init__(self):
            self.parts = list(chunks or [b'{"x":1}'])
            self.requested = []
        def read(self, _size):
            raise AssertionError("REJECT_MUST_NOT_USE_JSON_BODY_READ")
        def read1(self, size):
            self.requested.append(size)
            events.append(("read1", size))
            if read_error:
                raise read_error
            if not self.parts:
                return b""
            part = self.parts.pop(0)
            if len(part) > size:
                self.parts.insert(0, part[size:])
            return part[:size]
    class Connection:
        def gettimeout(self):
            return 4.0
        def settimeout(self, timeout):
            events.append(("timeout", timeout))
        def shutdown(self, how):
            events.append(("shutdown", how))
    handler.rfile, handler.wfile, handler.connection = Reader(), Sink(), Connection()
    handler.send_response = lambda status: events.append(("status", status))
    handler.send_header = lambda name, value: events.append(("header", name, value))
    handler.end_headers = lambda: events.append(("headers_end",))
    return handler, calls, events


@pytest.fixture
def loopback(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    copied_project = Path(dashboard.__file__).resolve().parents[2]
    assert not root.is_relative_to(ACTUAL_ROOT) and not ACTUAL_ROOT.is_relative_to(root)
    assert not root.is_relative_to(copied_project)
    (root / "OWNED_TESTONLY.json").write_text(json.dumps({"TESTONLY": True,
        "scope": SCOPE, "ephemeral_loopback_only": True}), encoding="utf-8")
    value, calls = state()
    finished = threading.Event()
    class OwnServer(ThreadingHTTPServer):
        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                finished.set()
    server = OwnServer(("127.0.0.1", 0), dashboard.make_handler(value, TOKEN))
    port = server.server_address[1]
    assert port != 8765
    original_connect = socket.socket.connect
    def connect(sock, address):
        assert address == ("127.0.0.1", port), "OWN_EPHEMERAL_LOOPBACK_ONLY"
        return original_connect(sock, address)
    monkeypatch.setattr(socket.socket, "connect", connect)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield port, calls, finished
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_wrong_auth_response_flush_halfclose_then_drain_without_json_or_money():
    handler, calls, events = pure_handler()
    handler.do_POST()
    assert calls == [] and handler.close_connection is True
    assert [item for item in events if item[0] == "status"] == [("status", 403)]
    assert ("header", "Connection", "close") in events
    assert events.index(("flush",)) < next(i for i, item in enumerate(events) if item[0] == "shutdown")
    assert next(i for i, item in enumerate(events) if item[0] == "shutdown") < next(i for i, item in enumerate(events) if item[0] == "read1")
    assert sum(handler.rfile.requested) <= 16384


@pytest.mark.parametrize("length,encoding", [(None, None), ("-1", None), ("not-a-size", None),
    ("99999999999999999999", None), ("16385", None), ("7", "chunked")])
def test_invalid_or_oversized_framing_never_grants_unbounded_unauthenticated_read(length, encoding):
    handler, calls, events = pure_handler(length=length, encoding=encoding, chunks=[b"x" * 20000])
    handler.do_POST()
    assert calls == []
    assert sum(handler.rfile.requested) == 16384 and max(handler.rfile.requested) <= 4096
    assert [item for item in events if item[0] == "status"] == [("status", 403)]


def test_drain_absolute_deadline_is_not_restarted_by_each_fragment(monkeypatch):
    handler, calls, events = pure_handler(length="100", chunks=[b"a"] * 100)
    clock = iter([0.0, .2, .8, 1.01])
    with monkeypatch.context() as patch:
        patch.setattr(dashboard.time, "monotonic", lambda: next(clock))
        handler.do_POST()
    assert calls == [] and len(handler.rfile.requested) == 2
    timeouts = [item[1] for item in events if item[0] == "timeout"]
    assert timeouts[0] == .8 and timeouts[1] == pytest.approx(.2) and timeouts[-1] == 4.0


@pytest.mark.parametrize("error", [TimeoutError("TESTONLY"), ConnectionAbortedError("TESTONLY"),
    ConnectionResetError("TESTONLY"), ValueError("TESTONLY_CLOSED_BUFFER")])
def test_drain_abort_is_local_no_second_response_or_business(error):
    handler, calls, events = pure_handler(read_error=error)
    handler.do_POST()
    assert calls == [] and [item for item in events if item[0] == "status"] == [("status", 403)]
    assert ("timeout", 4.0) in events


@pytest.mark.parametrize("change", ["host", "origin", "missing-token"])
def test_all_existing_auth_rejection_causes_still_precede_business(change):
    handler, calls, events = pure_handler()
    if change == "host":
        handler.headers.replace_header("X-Arthur-Token", TOKEN)
        handler.headers.replace_header("Host", "example.invalid")
    elif change == "origin":
        handler.headers.replace_header("X-Arthur-Token", TOKEN)
        handler.headers["Origin"] = "http://example.invalid"
    else:
        del handler.headers["X-Arthur-Token"]
    handler.do_POST()
    assert calls == [] and [item for item in events if item[0] == "status"] == [("status", 403)]


def test_original_http_client_wrong_token_receives_complete_403(loopback):
    port, calls, finished = loopback
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        client.request("POST", "/api/bookmaker-return", json.dumps({"TESTONLY": "not parsed"}),
            {"Content-Type": "application/json", "X-Arthur-Token": "wrong"})
        response = client.getresponse()
        assert response.status == 403 and response.getheader("Connection") == "close"
        assert "error" in json.loads(response.read()) and calls == []
    finally:
        client.close()
    assert finished.wait(2)


def test_response_is_readable_before_delayed_declared_body_then_clean_request_finish(loopback):
    port, calls, finished = loopback
    body = b'{"TESTONLY":1}'
    sock = socket.create_connection(("127.0.0.1", port), timeout=3)
    response = None
    try:
        headers = (f"POST /api/bookmaker-return HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Content-Length: {len(body)}\r\nContent-Type: application/json\r\nX-Arthur-Token: wrong\r\n\r\n").encode()
        sock.sendall(headers)
        response = http.client.HTTPResponse(sock)
        response.begin()
        assert response.status == 403 and "error" in json.loads(response.read())
        sock.sendall(body)
        assert finished.wait(2) and calls == []
    finally:
        if response is not None:
            response.close()
        sock.close()


def test_authorized_json_dispatch_and_stale_money_status_remain_unchanged(loopback):
    port, calls, finished = loopback
    def post(path, payload):
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            client.request("POST", path, json.dumps(payload),
                {"Content-Type": "application/json", "X-Arthur-Token": TOKEN})
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()
    assert post("/api/settings", {"TESTONLY": True}) == (200, {"settings": {"TESTONLY": True}})
    status, result = post("/api/bookmaker-return", {"ticket_id": "TESTONLY", "payout_eur": 1.01,
        "note": "TESTONLY", "expected_settlement_hash": "a" * 64,
        "payout_basis": "BOOKMAKER_CONFIRMED_GROSS_SPORTS_RETURN"})
    assert status == 409 and "SETTLEMENT_REVISION_CHANGED" in result["error"]
    assert calls == [("settings", {"TESTONLY": True}), ("money", "TESTONLY")]
    assert finished.wait(2)


def test_exact_D03_partitioned_headers_50ms_body_then_status_is_complete_403(loopback):
    """Same one-shot partition as the independently reproduced old-handler D03."""
    port, calls, finished = loopback
    body = b'{"TESTONLY":"unparsed bounded body"}'
    sock = socket.create_connection(("127.0.0.1", port), timeout=3)
    response = None
    try:
        headers = (f"POST /api/bookmaker-return HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Content-Type: application/json\r\nX-Arthur-Token: wrong\r\nContent-Length: {len(body)}\r\n\r\n").encode()
        sock.sendall(headers)
        time.sleep(.05)  # Exactly one predeclared schedule, never a retry.
        sock.sendall(body)
        response = http.client.HTTPResponse(sock)
        response.begin()
        assert response.status == 403 and response.getheader("Connection") == "close"
        received = response.read(16385)
        assert len(received) == int(response.getheader("Content-Length"))
        assert "error" in json.loads(received) and calls == []
        assert finished.wait(2)
    finally:
        if response is not None:
            response.close()
        sock.close()
