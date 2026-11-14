from types import SimpleNamespace

import pytest

from goal_analysis import portfolio_dashboard as dashboard


def test_windows_exclusive_option_precedes_bind_without_reuse(monkeypatch):
    events = []
    server = object.__new__(dashboard.ExclusiveDashboardServer)
    server.socket = SimpleNamespace(setsockopt=lambda *args: events.append(("option", args)))
    monkeypatch.setattr(dashboard, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(dashboard, "socket", SimpleNamespace(SOL_SOCKET=1, SO_EXCLUSIVEADDRUSE=99))

    def bind(actual):
        assert actual is server
        assert actual.allow_reuse_address is False
        assert actual.allow_reuse_port is False
        events.append(("bind",))

    monkeypatch.setattr(dashboard.ThreadingHTTPServer, "server_bind", bind)
    server.server_bind()
    assert events == [("option", (1, 99, 1)), ("bind",)]


def test_conflicting_listener_never_constructs_state_and_only_matching_instance_opens(monkeypatch):
    events = []

    class Occupied:
        def __init__(self, address, handler, *, bind_and_activate):
            assert address == ("127.0.0.1", 8765) and bind_and_activate is False

        def server_bind(self):
            raise OSError(dashboard.errno.EADDRINUSE, "TESTONLY occupied")

        def server_close(self):
            events.append("closed")

    def forbidden_state(*args):
        pytest.fail("Writer-capable state must not be constructed on bind refusal")

    monkeypatch.setattr(dashboard, "ExclusiveDashboardServer", Occupied)
    monkeypatch.setattr(dashboard, "ArthurState", forbidden_state)
    monkeypatch.setattr(dashboard, "_matching_dashboard", lambda *args: True)
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: events.append(url))
    dashboard.serve("TESTONLY-root", open_browser=True)
    assert events == ["closed", "http://127.0.0.1:8765"]
    events.clear()
    dashboard.serve("TESTONLY-root", open_browser=False)
    assert events == ["closed"]
    monkeypatch.setattr(dashboard, "_matching_dashboard", lambda *args: False)
    with pytest.raises(OSError, match="TESTONLY occupied"):
        dashboard.serve("TESTONLY-root", open_browser=True)
    assert events == ["closed", "closed"]


def test_listener_reserved_before_state_and_closed_on_state_failure(monkeypatch):
    events = []

    class Reserved:
        server_port = 8765

        def __init__(self, *args, **kwargs):
            assert kwargs == {"bind_and_activate": False}

        def server_bind(self):
            events.append("bind")

        def server_activate(self):
            events.append("listen")

        def server_close(self):
            events.append("close")

        def serve_forever(self):
            events.append("serve")
            assert self.RequestHandlerClass == "TESTONLY-handler"

    def state(*args):
        assert events[-2:] == ["bind", "listen"]
        events.append("state")
        return object()

    monkeypatch.setattr(dashboard, "ExclusiveDashboardServer", Reserved)
    monkeypatch.setattr(dashboard, "ArthurState", state)
    monkeypatch.setattr(dashboard, "make_handler", lambda *args: "TESTONLY-handler")
    dashboard.serve("TESTONLY-root")
    assert events == ["bind", "listen", "state", "serve", "close"]
    events.clear()

    def failed_state(*args):
        assert events == ["bind", "listen"]
        raise ValueError("TESTONLY state failure")

    monkeypatch.setattr(dashboard, "ArthurState", failed_state)
    with pytest.raises(ValueError, match="TESTONLY state failure"):
        dashboard.serve("TESTONLY-root")
    assert events == ["bind", "listen", "close"]
