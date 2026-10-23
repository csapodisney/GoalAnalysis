"""Private Tailscale Serve adapter for the unchanged loopback Arthur dashboard.

Serve authenticates the remote peer and injects Tailscale-User-Login. This adapter
must remain on 127.0.0.1; never use Funnel or expose its listener to the LAN.
Local OS processes are trusted just as they already are by the local dashboard.
No Arthur modules, configuration, tokens, data, or application jobs are loaded.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MAX_BODY_BYTES = 16384
# The live Arthur status includes saved reports and can exceed 32 MB after a
# fully sourced 30-profile generation. Keep a finite ceiling while allowing the
# dashboard's current response through the private gateway.
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_HEADER_BYTES = 32768
MAX_CONCURRENT_REQUESTS = 8
SOCKET_TIMEOUT = 10
CONNECTION_DEADLINE = 45
UPSTREAM_TIMEOUT = 30
HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
})
SECURITY_HEADERS = frozenset({
    "host", "origin", "tailscale-user-login", "x-arthur-token",
    "content-length", "content-type", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest",
})
REQUEST_HEADERS = frozenset({"accept", "accept-language", "content-type", "x-arthur-token"})
ERROR_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
}


@dataclass(frozen=True)
class GatewayConfig:
    hostname: str
    allowed_login: str
    https_port: int = 8443
    port: int = 8770
    upstream_port: int = 8765

    def __post_init__(self):
        if not re.fullmatch(r"[a-z0-9-]+\.[a-z0-9-]+\.ts\.net", self.hostname):
            raise ValueError("invalid_private_hostname")
        if (not self.allowed_login or len(self.allowed_login) > 254
                or any(ord(c) < 33 or ord(c) > 126 or c == "," for c in self.allowed_login)):
            raise ValueError("invalid_private_identity")
        if not (0 <= self.port <= 65535 and 1 <= self.upstream_port <= 65535
                and 1 <= self.https_port <= 65535 and self.port != self.upstream_port):
            raise ValueError("invalid_gateway_port")

    @property
    def authority(self):
        return f"{self.hostname}:{self.https_port}"

    @property
    def origin(self):
        return f"https://{self.authority}"

    @property
    def upstream_authority(self):
        return f"127.0.0.1:{self.upstream_port}"


def _close_socket(connection):
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _discard_input(read, connection, maximum):
    end = time.monotonic() + 0.05
    while maximum and time.monotonic() < end:
        connection.settimeout(max(0.001, end - time.monotonic()))
        discarded = read(min(4096, maximum))
        if not discarded:
            break
        maximum -= len(discarded)


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, config):
        self.config = config
        self.slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        super().__init__(("127.0.0.1", config.port), GatewayHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(
                    b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n"
                    b"Cache-Control: no-store\r\nConnection: close\r\n\r\n"
                )
                request.shutdown(socket.SHUT_WR)
                _discard_input(request.recv, request, MAX_HEADER_BYTES + MAX_BODY_BYTES)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        request.settimeout(SOCKET_TIMEOUT)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        deadline = threading.Timer(CONNECTION_DEADLINE, _close_socket, args=(request,))
        deadline.daemon = True
        deadline.start()
        try:
            super().process_request_thread(request, client_address)
        finally:
            deadline.cancel()
            self.slots.release()

    def handle_error(self, request, client_address):
        # Never dump request headers, payloads, user identity, or session tokens.
        pass


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "ArthurPrivateGateway/1"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def log_message(self, *_args):
        pass

    def send_error(self, code, message=None, explain=None):
        self._reject(code, "invalid_http_request")

    def _reject(self, status, message):
        self.close_connection = True
        body = json.dumps({"error": message}).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            for name, value in ERROR_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)
            # Windows can reset a socket closed with unread POST bytes, hiding
            # the rejection from the caller. Discard only a bounded remainder.
            _discard_input(self.rfile.read1, self.connection, MAX_BODY_BYTES)
        except OSError:
            pass

    def _validate(self):
        config = self.server.config
        if self.client_address[0] != "127.0.0.1":
            self._reject(403, "private_access_required")
            return None
        if sum(len(k) + len(v) for k, v in self.headers.items()) > MAX_HEADER_BYTES:
            self._reject(431, "request_headers_too_large")
            return None
        for name in SECURITY_HEADERS:
            values = self.headers.get_all(name, [])
            if len(values) > 1 or any(
                any(ord(c) < 32 or ord(c) > 126 for c in value) for value in values
            ):
                self._reject(400, "ambiguous_security_header")
                return None
        if (self.headers.get_all("host", []) != [config.authority]
                or self.headers.get_all("tailscale-user-login", []) != [config.allowed_login]):
            self._reject(403, "private_access_required")
            return None
        origin = self.headers.get("origin")
        if (origin is not None and origin != config.origin) or (
            self.command == "POST" and origin != config.origin
        ):
            self._reject(403, "same_origin_required")
            return None
        site = self.headers.get("sec-fetch-site")
        if site is not None and not (
            site == "same-origin" or (
                site in {"none", "same-site", "cross-site"}
                and self.command == "GET" and self.path == "/"
                and self.headers.get("sec-fetch-mode") == "navigate"
                and self.headers.get("sec-fetch-dest") == "document"
            )
        ):
            self._reject(403, "same_origin_required")
            return None
        if self.command == "POST" and not self.headers.get("x-arthur-token"):
            self._reject(403, "session_token_required")
            return None
        if self.headers.get_all("transfer-encoding"):
            self._reject(400, "unsupported_request_framing")
            return None
        if self.headers.get_all("expect"):
            self._reject(417, "unsupported_expectation")
            return None
        connection_fields = {
            part.strip().lower()
            for value in self.headers.get_all("connection", []) for part in value.split(",")
        }
        if connection_fields & (SECURITY_HEADERS | {"transfer-encoding"}):
            self._reject(400, "ambiguous_security_header")
            return None
        length_value = self.headers.get("content-length", "0")
        if not re.fullmatch(r"[0-9]{1,6}", length_value):
            self._reject(400, "invalid_request_size")
            return None
        length = int(length_value)
        if length > MAX_BODY_BYTES:
            self._reject(413, "request_too_large")
            return None
        if (self.command == "POST" and length == 0) or (self.command == "GET" and length != 0):
            self._reject(400, "invalid_request_size")
            return None
        try:
            parsed = urlsplit(self.path)
        except ValueError:
            self._reject(400, "invalid_request_target")
            return None
        raw_target = self.raw_requestline.decode("iso-8859-1").split()[1]
        if (raw_target != self.path or len(self.path) > 8192 or not self.path.startswith("/")
                or self.path.startswith("//") or "\\" in self.path or "#" in self.path
                or any(ord(c) < 33 or ord(c) > 126 for c in self.path)
                or parsed.scheme or parsed.netloc or parsed.fragment):
            self._reject(400, "invalid_request_target")
            return None
        return length, connection_fields

    def _proxy(self):
        self.close_connection = True
        validated = self._validate()
        if validated is None:
            return
        length, connection_fields = validated
        try:
            body = self.rfile.read(length) if length else None
        except OSError:
            self._reject(408, "request_timeout")
            return
        if body is not None and len(body) != length:
            self._reject(400, "incomplete_request_body")
            return
        config = self.server.config
        headers = {
            name: value for name, value in self.headers.items()
            if name.lower() in REQUEST_HEADERS and name.lower() not in connection_fields
        }
        headers["Host"] = config.upstream_authority
        if self.headers.get("origin") is not None:
            headers["Origin"] = f"http://{config.upstream_authority}"
        headers["Connection"] = "close"
        upstream = http.client.HTTPConnection("127.0.0.1", config.upstream_port,
                                              timeout=UPSTREAM_TIMEOUT)
        deadline = None
        try:
            upstream.connect()
            # Absolute deadline also bounds peers that trickle headers or body bytes.
            deadline = threading.Timer(CONNECTION_DEADLINE, _close_socket, args=(upstream.sock,))
            deadline.daemon = True
            deadline.start()
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            raw_headers = response.getheaders()
            if sum(len(k) + len(v) for k, v in raw_headers) > MAX_HEADER_BYTES:
                self._reject(502, "upstream_headers_too_large")
                return
            chunks, size = [], 0
            while True:
                chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    self._reject(502, "upstream_response_too_large")
                    return
                chunks.append(chunk)
            blocked = HOP_HEADERS | {
                "content-length", "server", "date", "set-cookie", "authorization",
                "www-authenticate", "forwarded",
            } | {
                part.strip().lower() for name, value in raw_headers
                if name.lower() == "connection" for part in value.split(",")
            }
            self.send_response(response.status)
            for name, value in raw_headers:
                lower = name.lower()
                if (lower not in blocked and not lower.startswith("tailscale-")
                        and not lower.startswith("x-forwarded-")
                        and not lower.startswith("access-control-")):
                    self.send_header(name, value)
            self.send_header("Content-Length", str(size))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"".join(chunks))
        except (OSError, http.client.HTTPException):
            self._reject(502, "local_dashboard_unavailable")
        finally:
            if deadline is not None:
                deadline.cancel()
            upstream.close()

    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()


def create_gateway(config: GatewayConfig):
    return GatewayServer(config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--allowed-login", required=True)
    parser.add_argument("--https-port", type=int, default=8443)
    parser.add_argument("--port", "--listen-port", dest="port", type=int, default=8770)
    parser.add_argument("--upstream-port", type=int, default=8765)
    args = parser.parse_args()
    if args.port == 0:
        parser.error("the runtime listener requires an explicit nonzero port")
    server = create_gateway(GatewayConfig(**vars(args)))
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
