"""Network-boundary checks against disposable servers, never the live Arthur app."""

import hashlib
import http.client
import importlib.util
import socket
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "arthur_tailscale_gateway", Path(__file__).parents[1] / "scripts/arthur-tailscale-gateway.py"
)
gateway = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gateway
SPEC.loader.exec_module(gateway)

HOSTNAME = "desktop-jpahkrb.tail7c3a3d.ts.net"
LOGIN = "csapodisney@github"
AUTHORITY = f"{HOSTNAME}:8443"
ORIGIN = f"https://{AUTHORITY}"
TOKEN = "opaque-test-session-value"
CSP = "default-src 'self'; script-src 'self' 'nonce-test-nonce'; connect-src 'self'"


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01},
                              daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def private_servers():
    requests = []

    class Backend(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self):
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length) if length else b""
            requests.append((self.command, self.path, self.headers, body))
            if self.path == "/slow":
                time.sleep(0.2)
            status = 200
            data = b'{"backend":"unchanged"}'
            if self.path == "/api/__remote_access_probe__":
                status, data = 404, b'{"error":"unknown operation"}'
            if self.command == "POST" and self.headers.get("x-arthur-token") != TOKEN:
                status, data = 403, b'{"error":"invalid backend session"}'
            if self.path == "/redirect":
                status, data = 302, b""
            if self.path == "/large":
                data = b"x" * 40
            if self.path == "/api/status?large=1":
                data = b'{"status":"' + b"x" * 12_280_947 + b'"}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Connection", "close, X-Private-Hop")
            self.send_header("X-Private-Hop", "not-for-browser")
            self.send_header("Set-Cookie", "irrelevant=test")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Tailscale-User-Login", LOGIN)
            if status == 302:
                self.send_header("Location", "https://untrusted.invalid/destination")
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass

        do_GET = respond
        do_POST = respond

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    backend.daemon_threads = True
    config = gateway.GatewayConfig(HOSTNAME, LOGIN, port=0, upstream_port=backend.server_port)
    proxy = gateway.create_gateway(config)
    with serving(backend), serving(proxy):
        yield proxy, backend, requests


def call(proxy, method="GET", path="/", *, headers=None, body=None):
    pairs = [("Host", AUTHORITY), ("Tailscale-User-Login", LOGIN)]
    if headers is not None:
        replaced = {name.lower() for name, _ in headers}
        pairs = [(name, value) for name, value in pairs if name.lower() not in replaced]
        pairs += [(name, value) for name, value in headers if value is not None]
    if body is not None and not any(name.lower() == "content-length" for name, _ in pairs):
        pairs.append(("Content-Length", str(len(body))))
    client = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=2)
    try:
        client.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for name, value in pairs:
            client.putheader(name, value)
        client.endheaders(body)
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        client.close()


def post_headers():
    return [("Origin", ORIGIN), ("X-Arthur-Token", TOKEN),
            ("Content-Type", "application/json"), ("Sec-Fetch-Site", "same-origin")]


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/static/style.css",
                                     "/api/status?date=2026-10-14", "/api/export",
                                     "/api/tickets.txt?date=2026-10-14"])
def test_get_fixed_backend_and_security_passthrough(private_servers, path):
    proxy, backend, seen = private_servers
    status, headers, body = call(proxy, path=path)
    assert status == 200 and body == b'{"backend":"unchanged"}'
    assert headers["Content-Security-Policy"] == CSP
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Content-Length"] == str(len(body))
    assert seen[-1][1] == path
    assert seen[-1][2]["Host"] == f"127.0.0.1:{backend.server_port}"
    assert proxy.server_address[0] == "127.0.0.1"


@pytest.mark.parametrize("header,value", [
    ("Host", None), ("Host", "evil.example:8443"), ("Host", HOSTNAME),
    ("Host", f"{HOSTNAME}:443"), ("Host", AUTHORITY.upper()),
    ("Tailscale-User-Login", None), ("Tailscale-User-Login", "other@github"),
    ("Tailscale-User-Login", LOGIN.upper()),
])
def test_exact_private_identity_and_host(private_servers, header, value):
    proxy, _, seen = private_servers
    assert call(proxy, headers=[(header, value)])[0] == 403
    assert not seen


@pytest.mark.parametrize("header,value", [
    ("Host", AUTHORITY), ("Origin", ORIGIN), ("Tailscale-User-Login", LOGIN),
    ("X-Arthur-Token", TOKEN), ("Content-Length", "2"),
    ("Content-Type", "application/json"), ("Sec-Fetch-Site", "same-origin"),
])
def test_duplicate_security_headers_rejected(private_servers, header, value):
    proxy, _, seen = private_servers
    headers = [(name, val) for name, val in post_headers() if name.lower() != header.lower()]
    headers += [(header, value), (header, value)]
    assert call(proxy, "POST", headers=headers, body=b"{}")[0] == 400
    assert not seen


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("origin", ["https://evil.example", "null", "", ORIGIN + "/",
                                    "http://127.0.0.1:8765", ORIGIN + ".evil"])
def test_unmatched_origin_never_rewritten(private_servers, method, origin):
    proxy, _, seen = private_servers
    headers = [(name, value) for name, value in post_headers() if name != "Origin"]
    headers += [("Origin", origin)]
    assert call(proxy, method, headers=headers, body=b"{}" if method == "POST" else None)[0] == 403
    assert not seen


def test_post_requires_origin_and_nonempty_token(private_servers):
    proxy, _, seen = private_servers
    for omitted in ("Origin", "X-Arthur-Token"):
        headers = [(name, value) for name, value in post_headers() if name != omitted]
        assert call(proxy, "POST", headers=headers, body=b"{}")[0] == 403
    assert not seen


@pytest.mark.parametrize("token,status", [(TOKEN, 404), ("wrong-session-token", 403)])
def test_token_unchanged_backend_checks_and_unknown_probe_passthrough(private_servers, token, status):
    proxy, backend, seen = private_servers
    headers = [(name, value) for name, value in post_headers() if name != "X-Arthur-Token"]
    headers += [("X-Arthur-Token", token)]
    result, _, body = call(proxy, "POST", "/api/__remote_access_probe__",
                           headers=headers, body=b"{}")
    assert result == status
    assert seen[-1][2]["X-Arthur-Token"] == token
    assert seen[-1][2]["Origin"] == f"http://127.0.0.1:{backend.server_port}"
    assert seen[-1][3] == b"{}"
    assert b"error" in body


def test_matching_get_origin_can_be_rewritten(private_servers):
    proxy, backend, seen = private_servers
    assert call(proxy, headers=[("Origin", ORIGIN)])[0] == 200
    assert seen[-1][2]["Origin"] == f"http://127.0.0.1:{backend.server_port}"


@pytest.mark.parametrize("fetch", ["cross-site", "same-site", "none", "invalid"])
def test_fetch_site_gate(private_servers, fetch):
    proxy, _, seen = private_servers
    assert call(proxy, path="/api/status", headers=[("Sec-Fetch-Site", fetch)])[0] == 403
    assert not seen


@pytest.mark.parametrize("site", ["none", "same-site", "cross-site"])
def test_root_browser_navigation_allowed(private_servers, site):
    proxy, _, seen = private_servers
    assert call(proxy, headers=[("Sec-Fetch-Site", site), ("Sec-Fetch-Mode", "navigate"),
                                ("Sec-Fetch-Dest", "document")])[0] == 200
    assert len(seen) == 1


@pytest.mark.parametrize("path", ["/api/status", "/static/app.js"])
def test_cross_site_navigation_exception_never_covers_api_or_assets(private_servers, path):
    proxy, _, seen = private_servers
    assert call(proxy, path=path, headers=[("Sec-Fetch-Site", "cross-site"),
        ("Sec-Fetch-Mode", "navigate"), ("Sec-Fetch-Dest", "document")])[0] == 403
    assert not seen


def test_cross_site_navigation_never_bypasses_explicit_origin(private_servers):
    proxy, _, seen = private_servers
    assert call(proxy, headers=[("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "navigate"),
        ("Sec-Fetch-Dest", "document"), ("Origin", "https://evil.example")])[0] == 403
    assert not seen


@pytest.mark.parametrize("name", ["Host", "Origin", "Tailscale-User-Login", "X-Arthur-Token",
                                 "Content-Length", "Transfer-Encoding"])
def test_connection_cannot_hide_security_or_framing_headers(private_servers, name):
    proxy, _, seen = private_servers
    assert call(proxy, "POST", headers=post_headers() + [("Connection", name)], body=b"{}")[0] == 400
    assert not seen


@pytest.mark.parametrize("extra,status", [
    ([("Transfer-Encoding", "chunked")], 400),
    ([("Content-Length", "-2")], 400),
    ([("Content-Length", "+2")], 400),
    ([("Content-Length", "2, 2")], 400),
    ([("Content-Length", "16385")], 413),
    ([("Content-Length", "0")], 400),
    ([("Expect", "100-continue")], 417),
])
def test_body_framing_gate(private_servers, extra, status):
    proxy, _, seen = private_servers
    assert call(proxy, "POST", headers=post_headers() + extra, body=b"{}")[0] == status
    assert not seen


def test_body_limit_boundary_and_get_body(private_servers):
    proxy, _, seen = private_servers
    body = b"x" * 16384
    assert call(proxy, "POST", headers=post_headers(), body=body)[0] == 200
    assert seen[-1][3] == body
    seen.clear()
    assert call(proxy, body=b"{}")[0] == 400
    assert not seen


@pytest.mark.parametrize("path", ["http://evil.invalid/path", "//evil.invalid/path", "/bad\\path",
                                   "/bad#fragment"])
def test_no_arbitrary_upstream_targets(private_servers, path):
    proxy, _, seen = private_servers
    assert call(proxy, path=path)[0] == 400
    assert not seen


def test_no_identity_authorization_cookies_or_hop_headers_forwarded(private_servers):
    proxy, _, seen = private_servers
    extras = [("Cookie", "irrelevant=test"), ("Authorization", "Bearer irrelevant-test"),
              ("Proxy-Authorization", "Basic irrelevant-test"), ("Forwarded", "host=evil"),
              ("X-Forwarded-Host", "evil"), ("X-Forwarded-For", "8.8.8.8"),
              ("Tailscale-User-Name", "Other"), ("Connection", "Accept-Language"),
              ("Accept-Language", "hu"), ("Accept", "application/json")]
    status, headers, _ = call(proxy, headers=extras)
    assert status == 200
    backend_names = {name.lower() for name in seen[-1][2]}
    assert not backend_names & {name.lower() for name, _ in extras if name != "Accept"
                                and name != "Connection"}
    assert "tailscale-user-login" not in backend_names
    assert seen[-1][2]["Accept"] == "application/json"
    response_names = {name.lower() for name in headers}
    assert not response_names & {"set-cookie", "x-private-hop", "tailscale-user-login",
                                  "access-control-allow-origin"}


def test_redirect_status_passthrough_without_following(private_servers):
    proxy, _, seen = private_servers
    status, headers, body = call(proxy, path="/redirect")
    assert status == 302 and body == b""
    assert headers["Location"] == "https://untrusted.invalid/destination"
    assert len(seen) == 1


def test_response_size_is_bounded(private_servers, monkeypatch):
    proxy, _, _ = private_servers
    monkeypatch.setattr(gateway, "MAX_RESPONSE_BYTES", 16)
    assert call(proxy, path="/large")[0] == 502


def test_current_dashboard_status_size_passes_through_intact(private_servers):
    proxy, _, seen = private_servers
    status, headers, body = call(proxy, path="/api/status?large=1")
    assert status == 200
    assert len(body) == 12_280_960
    assert headers["Content-Length"] == "12280960"
    expected = b'{"status":"' + b"x" * 12_280_947 + b'"}'
    assert hashlib.sha256(body).digest() == hashlib.sha256(expected).digest()
    assert seen[-1][1] == "/api/status?large=1"


def test_upstream_timeout_returns_redacted_error(private_servers, monkeypatch):
    proxy, _, _ = private_servers
    monkeypatch.setattr(gateway, "UPSTREAM_TIMEOUT", 0.05)
    status, _, body = call(proxy, path="/slow")
    assert status == 502 and TOKEN.encode() not in body


def test_busy_server_rejects_without_new_threads_or_backend(private_servers):
    proxy, _, seen = private_servers
    for _ in range(gateway.MAX_CONCURRENT_REQUESTS):
        assert proxy.slots.acquire(blocking=False)
    try:
        assert call(proxy)[0] == 503
        assert not seen
    finally:
        for _ in range(gateway.MAX_CONCURRENT_REQUESTS):
            proxy.slots.release()


def test_inbound_timeout_for_unfinished_body(private_servers, monkeypatch):
    proxy, _, seen = private_servers
    monkeypatch.setattr(gateway, "SOCKET_TIMEOUT", 0.05)
    with socket.create_connection(("127.0.0.1", proxy.server_port), timeout=2) as client:
        message = (f"POST /api/__remote_access_probe__ HTTP/1.1\r\nHost: {AUTHORITY}\r\n"
                   f"Tailscale-User-Login: {LOGIN}\r\nOrigin: {ORIGIN}\r\n"
                   f"X-Arthur-Token: {TOKEN}\r\nContent-Length: 2\r\n\r\n")
        client.sendall(message.encode("ascii"))
        assert b"408 Request Timeout" in client.recv(4096)
    assert not seen


@pytest.mark.parametrize("method", ["PUT", "DELETE", "OPTIONS", "HEAD"])
def test_unsupported_methods_never_forwarded(private_servers, method):
    proxy, _, seen = private_servers
    assert call(proxy, method)[0] == 501
    assert not seen
