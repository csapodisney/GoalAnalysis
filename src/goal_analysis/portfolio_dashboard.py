"""Local-only HTTP control surface for the Arthur portfolio workflow."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import webbrowser
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from goal_analysis.agents.codex_review import codex_installed
from goal_analysis.config.portfolio import load_settings, save_settings, validate_settings
from goal_analysis.jobs.portfolio import safe_error, validate_target_day
from goal_analysis.quote_metadata import quote_metadata
from goal_analysis.storage.portfolio_ledger import PortfolioLedger


def _json(path: Path):
    return json.loads(path.read_text("utf-8-sig"))


class ArthurState:
    def __init__(
        self, root: Path, config_path: Path | None = None, settings_path: Path | None = None
    ):
        self.root = root.resolve()
        self.config_path = (config_path or self.root / "config/daily223-live.json").resolve()
        self.settings_path = (settings_path or self.root / "config/arthur-settings.json").resolve()
        self.ledger = PortfolioLedger(self.root / "data/arthur/ledger.sqlite3")
        self.lock = threading.Lock()
        self.runtime = {
            "running": False,
            "operation": None,
            "last_output": "",
            "last_exit_code": None,
            "last_started_at": None,
            "last_finished_at": None,
        }

    def snapshot(self, day: str | None = None) -> dict:
        today = datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
        day = day or today
        date.fromisoformat(day)
        config_error = None
        try:
            config = _json(self.config_path)
        except (OSError, ValueError) as error:
            config, config_error = {}, safe_error(error)
        try:
            settings = load_settings(self.settings_path)
        except (OSError, ValueError) as error:
            settings, config_error = validate_settings({}), safe_error(error)
        runs = self.ledger.list_runs(date=day, limit=50)
        tickets = self.ledger.list_tickets(date=day)
        now = datetime.now(UTC)
        for ticket in tickets:
            ticket["from_previous_run"] = bool(runs and ticket.get("run_id") != runs[0]["run_id"])
            for leg in ticket["legs"]:
                leg.update(
                    quote_metadata(leg.get("quote_timestamp_raw", leg.get("quoted_at")), now)
                )
        with self.lock:
            runtime = dict(self.runtime)
        if runtime["running"]:
            try:
                progress = _json(self.root / "data/arthur/progress.json")
                if progress["at"] >= runtime["last_started_at"]:
                    runtime.update(message=progress["message"], stage=progress["stage"])
            except (OSError, ValueError, KeyError):
                pass
        return {
            "today": today,
            "selected_date": day,
            "server_time": datetime.now(UTC).isoformat(),
            "timezone": "Europe/Berlin",
            "settings": settings,
            "configuration": {
                "competition_count": len(config.get("leagues", [])),
                "odds_region": config.get("odds_region"),
                "preferred_bookmakers": config.get("preferred_bookmakers", []),
                "max_football_calls": config.get("max_football_calls"),
                "max_odds_credits": config.get("max_odds_credits"),
                "error": config_error,
                "setup_command": r"powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\configure-arthur-codex.ps1",
            },
            "credentials": {
                "api_football": bool(os.environ.get("API_FOOTBALL_KEY", "").strip()),
                "the_odds_api": bool(os.environ.get("THE_ODDS_API_KEY", "").strip()),
                "openai": False,
                "codex_cli": codex_installed(),
            },
            "runtime": runtime,
            "latest": runs[0] if runs else None,
            "history": runs,
            "tickets": tickets,
            "recommendations": self.ledger.list_recommendations(date=day),
            "recommendation_archive": self.ledger.list_recommendations(date=day, archive=True),
        }

    def update_settings(self, patch: dict) -> dict:
        with self.lock:
            if self.runtime["running"]:
                raise RuntimeError("Futó elemzés alatt a beállítások nem módosíthatók.")
            current = load_settings(self.settings_path)
            if "openai" in patch:
                if not isinstance(patch["openai"], dict):
                    raise ValueError("Az OpenAI-beállítás objektum legyen.")
                patch = {**patch, "openai": {**current["openai"], **patch["openai"]}}
            return save_settings(self.settings_path, {**current, **patch})

    def start(self, operation: str, payload: dict) -> dict:
        if operation not in {"run", "settle", "check-openai"}:
            raise ValueError("Ismeretlen művelet.")
        settings = load_settings(self.settings_path)
        day = date.fromisoformat(
            payload.get("date") or datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
        )
        if operation == "run":
            validate_target_day(day, datetime.now(UTC))
            overrides = {
                name: payload[name]
                for name in ("strictness", "target_ticket_count")
                if name in payload
            }
            settings = validate_settings({**settings, **overrides})
        command = [
            sys.executable,
            str(self.root / "scripts/run-arthur.py"),
            "--config",
            str(self.config_path),
            "--settings",
            str(self.settings_path),
        ]
        if operation == "run":
            command += [
                "--live",
                "--settle",
                "--date",
                day.isoformat(),
                "--strictness",
                str(settings["strictness"]),
                "--ticket-count",
                str(settings["target_ticket_count"]),
            ]
        elif operation == "settle":
            command += ["--settle"]
        else:
            command += ["--check-openai"]
        with self.lock:
            if self.runtime["running"]:
                raise RuntimeError("Már fut egy Arthur-feladat.")
            self.runtime.update(
                running=True,
                operation=operation,
                last_output="A feladat elindult.",
                last_exit_code=None,
                last_started_at=datetime.now(UTC).isoformat(),
                last_finished_at=None,
            )
        threading.Thread(
            target=self._execute, args=(command,), name=f"arthur-{operation}", daemon=True
        ).start()
        return {"started": True, "operation": operation, "date": day.isoformat()}

    def _execute(self, command):
        try:
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            completed = subprocess.run(
                command,
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=1800,
                check=False,
            )
            output = safe_error((completed.stdout + "\n" + completed.stderr).strip())
            code = completed.returncode
        except Exception as error:  # noqa: BLE001 - Child process boundary; return a redacted failure.
            output, code = safe_error(error), 1
        with self.lock:
            self.runtime.update(
                running=False,
                last_output=output,
                last_exit_code=code,
                last_finished_at=datetime.now(UTC).isoformat(),
            )


def make_handler(state: ArthurState, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Arthur/3"

        def log_message(self, *args):
            pass

        def _local(self):
            host = self.headers.get("Host", "")
            return host in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }

        def _send(
            self,
            value,
            status=200,
            media="application/json; charset=utf-8",
            download=None,
            nonce=None,
        ):
            data = (
                json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
                if isinstance(value, (dict, list))
                else value
            )
            if isinstance(data, str):
                data = data.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            if nonce:
                self.send_header(
                    "Content-Security-Policy",
                    f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
                )
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._local():
                return self._send({"error": "Csak helyi hozzáférés engedélyezett."}, 403)
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path == "/":
                    nonce = secrets.token_urlsafe(18)
                    html = (Path(__file__).parent / "web/index.html").read_text("utf-8")
                    bootstrap = (
                        f'<script nonce="{nonce}">window.ARTHUR_TOKEN={json.dumps(token)};</script>'
                    )
                    return self._send(
                        html.replace("__ARTHUR_BOOTSTRAP__", bootstrap),
                        media="text/html; charset=utf-8",
                        nonce=nonce,
                    )
                if parsed.path in {"/static/app.js", "/static/style.css"}:
                    name = parsed.path.rsplit("/", 1)[1]
                    media = (
                        "text/javascript; charset=utf-8"
                        if name.endswith(".js")
                        else "text/css; charset=utf-8"
                    )
                    return self._send(
                        (Path(__file__).parent / "web" / name).read_bytes(), media=media
                    )
                if parsed.path == "/api/status":
                    return self._send(state.snapshot(query.get("date", [None])[0]))
                if parsed.path == "/api/performance":
                    return self._send(
                        state.ledger.performance(
                            month=query.get("month", [None])[0],
                            mode=query.get("mode", ["actual"])[0],
                        )
                    )
                if parsed.path == "/api/export":
                    snapshot = state.snapshot(query.get("date", [None])[0])
                    payload = {
                        "schema_version": 3,
                        "date": snapshot["selected_date"],
                        "reports": snapshot["history"],
                        "tickets": snapshot["tickets"],
                        "recommendations": snapshot["recommendations"],
                        "recommendation_archive": snapshot["recommendation_archive"],
                        "feedback": state.ledger.feedback_summary(),
                        "interpretation": "Előzetesen rögzített tippek és eredmények. Az adatokat későbbi elemzésre használjuk, nem automatikus modell-újratanításra.",
                    }
                    return self._send(payload, download=f"Arthur-{snapshot['selected_date']}.json")
                return self._send({"error": "Nincs ilyen oldal."}, 404)
            except (ValueError, TypeError) as error:
                return self._send({"error": safe_error(error)}, 400)
            except Exception as error:  # noqa: BLE001 - HTTP boundary must not expose tracebacks.
                return self._send({"error": safe_error(error)}, 500)

        def do_POST(self):
            origin = self.headers.get("Origin")
            valid_origins = {
                f"http://127.0.0.1:{self.server.server_port}",
                f"http://localhost:{self.server.server_port}",
            }
            if (
                not self._local()
                or (origin is not None and origin not in valid_origins)
                or not secrets.compare_digest(self.headers.get("X-Arthur-Token", ""), token)
            ):
                return self._send(
                    {"error": "Érvénytelen helyi munkamenet. Frissítsd az oldalt."}, 403
                )
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    raise ValueError("Érvénytelen kérésméret.")
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("JSON-kérés szükséges.")

                def invalid_constant(value):
                    raise ValueError("Nem véges szám nem engedélyezett.")

                payload = json.loads(self.rfile.read(length), parse_constant=invalid_constant)
                if not isinstance(payload, dict):
                    raise TypeError("JSON-objektum szükséges.")
                path = urlsplit(self.path).path
                if path == "/api/settings":
                    return self._send({"settings": state.update_settings(payload)})
                if path in {"/api/run", "/api/settle", "/api/check-openai"}:
                    return self._send(state.start(path.split("/")[-1], payload), 202)
                if path == "/api/wager":
                    result = state.ledger.record_wager(
                        payload["ticket_id"],
                        stake_eur=payload.get("stake_eur", 5),
                        actual_combined_price=payload.get("actual_combined_price"),
                    )
                    return self._send(result or {"recorded": True})
                if path == "/api/void":
                    return self._send(
                        state.ledger.record_void(
                            payload["ticket_id"], payload["fixture_id"], payload["note"]
                        )
                    )
                if path == "/api/bookmaker-return":
                    return self._send(
                        state.ledger.confirm_bookmaker_return(
                            payload["ticket_id"], payload["payout_eur"], payload["note"]
                        )
                    )
                return self._send({"error": "Nincs ilyen művelet."}, 404)
            except (ValueError, TypeError, KeyError) as error:
                return self._send({"error": safe_error(error)}, 400)
            except RuntimeError as error:
                return self._send({"error": safe_error(error)}, 409)
            except Exception as error:  # noqa: BLE001 - HTTP boundary must not expose tracebacks.
                return self._send({"error": safe_error(error)}, 500)

    return Handler


def serve(
    root: Path,
    config_path: Path | None = None,
    settings_path: Path | None = None,
    port=8765,
    open_browser=False,
):
    state = ArthurState(root, config_path, settings_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", port), make_handler(state, secrets.token_urlsafe(32))
    )
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Arthur dashboard: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
