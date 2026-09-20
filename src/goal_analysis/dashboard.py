"""Local control panel for DAILY_223 runs and reports."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import webbrowser
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text("utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def summarize_report(report: dict[str, Any], run_id: str) -> dict[str, Any]:
    selection = report.get("bookmaker_selection") or {}
    legs = []
    for item in report.get("legs") or []:
        if not isinstance(item, dict):
            continue
        legs.append(
            {
                "home_team": item.get("home_team"),
                "away_team": item.get("away_team"),
                "kickoff": item.get("kickoff"),
                "market_key": item.get("market_key"),
                "selection_key": item.get("selection_key"),
                "period": item.get("period"),
                "decimal_price": item.get("decimal_price"),
                "support_score": item.get("support_score"),
            }
        )
    return {
        "run_id": run_id,
        "date": report.get("date"),
        "observed_at": report.get("observed_at"),
        "construction_status": report.get("construction_status", "UNKNOWN"),
        "combined_price": report.get("combined_price"),
        "odds_band": report.get("odds_band"),
        "bookmaker": selection.get("selected"),
        "bookmaker_method": selection.get("method"),
        "input_candidate_count": report.get("input_candidate_count", 0),
        "eligible_candidate_count": report.get("eligible_candidate_count", 0),
        "data_issue_count": len(report.get("data_issues") or []),
        "usage": report.get("usage") or {},
        "reason": report.get("reason"),
        "legs": legs,
    }


def discover_reports(reports_root: Path, limit: int = 30) -> list[dict[str, Any]]:
    if not reports_root.is_dir():
        return []
    found = []
    for directory in reports_root.iterdir():
        if not directory.is_dir():
            continue
        report_path = directory / "report.json"
        report = _read_json(report_path)
        if report is None:
            continue
        try:
            modified = report_path.stat().st_mtime
        except OSError:
            continue
        found.append((modified, summarize_report(report, directory.name)))
    found.sort(key=lambda item: (-item[0], item[1]["run_id"]))
    return [item for _, item in found[:limit]]


class DashboardState:
    def __init__(self, root: Path, config_path: Path | None = None) -> None:
        self.root = root.resolve()
        self.config_path = (config_path or self.root / "config/daily223-live.json").resolve()
        self.reports_root = self.root / "reports/daily223"
        self._lock = threading.Lock()
        self.running = False
        self.last_started_at: str | None = None
        self.last_finished_at: str | None = None
        self.last_exit_code: int | None = None
        self.last_output = ""

    def snapshot(self) -> dict[str, Any]:
        config = _read_json(self.config_path) or {}
        reports = discover_reports(self.reports_root)
        with self._lock:
            runtime = {
                "running": self.running,
                "last_started_at": self.last_started_at,
                "last_finished_at": self.last_finished_at,
                "last_exit_code": self.last_exit_code,
                "last_output": self.last_output,
            }
        return {
            "server_time": datetime.now(UTC).isoformat(),
            "timezone": "Europe/Berlin",
            "configuration": {
                "path": str(self.config_path.relative_to(self.root))
                if self.config_path.is_relative_to(self.root)
                else self.config_path.name,
                "competition_count": len(config.get("leagues") or []),
                "odds_region": config.get("odds_region"),
                "preferred_bookmakers": config.get("preferred_bookmakers") or [],
            },
            "credentials": {
                "api_football": bool(os.environ.get("API_FOOTBALL_KEY", "").strip()),
                "the_odds_api": bool(os.environ.get("THE_ODDS_API_KEY", "").strip()),
                "openai": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
            },
            "runtime": runtime,
            "latest": reports[0] if reports else None,
            "history": reports,
        }

    def start_run(self) -> tuple[bool, str]:
        with self._lock:
            if self.running:
                return False, "Már fut egy elemzés."
            self.running = True
            self.last_started_at = datetime.now(UTC).isoformat()
            self.last_finished_at = None
            self.last_exit_code = None
            self.last_output = "Az adatgyűjtés elindult."
        thread = threading.Thread(target=self._run, name="daily223-run", daemon=True)
        thread.start()
        return True, "Az elemzés elindult."

    def _run(self) -> None:
        berlin_day = datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
        output = self.reports_root / f"{berlin_day}-dashboard-{uuid4().hex[:12]}"
        command = [
            sys.executable,
            str(self.root / "scripts/run-daily-223-live.py"),
            "--config",
            str(self.config_path),
            "--live",
            "--output-dir",
            str(output),
        ]
        try:
            result = subprocess.run(
                command,
                cwd=self.root,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            combined = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part)
            exit_code = result.returncode
        except OSError as error:
            combined, exit_code = f"Nem indítható az elemzés: {error}", 1
        with self._lock:
            self.running = False
            self.last_finished_at = datetime.now(UTC).isoformat()
            self.last_exit_code = exit_code
            self.last_output = combined[-8000:]


def _dashboard_html(token: str) -> str:
    safe_token = json.dumps(token)
    return _HTML.replace("__DASHBOARD_TOKEN__", safe_token)


def make_handler(state: DashboardState, token: str) -> type[BaseHTTPRequestHandler]:
    html = _dashboard_html(token).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "GoalAnalysisDashboard/1"

        def _security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'",
            )

        def _send_json(self, value: Any, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self._security_headers()
                self.end_headers()
                self.wfile.write(html)
                return
            if self.path == "/api/status":
                self._send_json(state.snapshot())
                return
            self._send_json({"error": "Nincs ilyen végpont."}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if self.path != "/api/run":
                self._send_json({"error": "Nincs ilyen végpont."}, HTTPStatus.NOT_FOUND)
                return
            if self.headers.get("X-Dashboard-Token") != token:
                self._send_json({"error": "Érvénytelen vezérlőpult-token."}, HTTPStatus.FORBIDDEN)
                return
            started, message = state.start_run()
            self._send_json(
                {"started": started, "message": message},
                HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT,
            )

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    return Handler


def serve_dashboard(
    root: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("the dashboard can only bind to localhost")
    if not 1024 <= port <= 65535:
        raise ValueError("dashboard port must be between 1024 and 65535")
    state = DashboardState(root)
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer((host, port), make_handler(state, token))
    url = f"http://{host}:{port}/"
    if open_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    print(f"Arthur vezérlőpult: {url}")
    print("Leállítás: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


_HTML = r'''<!doctype html>
<html lang="hu"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Arthur Goal Analysis</title><style>
:root{color-scheme:dark;--bg:#09111f;--panel:#101c2e;--panel2:#15243a;--line:#263952;--text:#edf4ff;--muted:#8fa3bd;--blue:#55a7ff;--green:#4bd19f;--amber:#ffbf69;--red:#ff6b7a}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#142743 0,transparent 35%),var(--bg);color:var(--text);font:15px/1.5 Inter,Segoe UI,Arial,sans-serif}.wrap{max-width:1240px;margin:auto;padding:30px 22px 60px}header{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:28px}.brand h1{font-size:26px;margin:0;letter-spacing:-.5px}.brand p{margin:4px 0 0;color:var(--muted)}button{border:0;border-radius:12px;padding:12px 18px;background:var(--blue);color:#07101e;font-weight:800;cursor:pointer}button:disabled{opacity:.45;cursor:wait}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:0 14px 40px #0003}.metric{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.1em}.value{font-size:25px;font-weight:800;margin-top:7px}.span2{grid-column:span 2}.span4{grid-column:span 4}.section{margin-top:14px}.section h2{font-size:16px;margin:0 0 14px}.pill{display:inline-block;padding:5px 9px;border-radius:999px;background:#21344e;color:#c9daf0;font-size:12px;font-weight:700}.ok{color:var(--green)}.warn{color:var(--amber)}.bad{color:var(--red)}.legs{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.leg{border:1px solid var(--line);border-radius:14px;padding:14px;background:#0d1828}.teams{font-weight:800;font-size:16px}.detail{color:var(--muted);font-size:13px;margin-top:6px}.odds{font-size:24px;color:var(--amber);font-weight:800;margin-top:12px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);font-size:13px}th{color:var(--muted);font-weight:600}.log{white-space:pre-wrap;background:#07101d;border:1px solid var(--line);border-radius:12px;padding:12px;max-height:230px;overflow:auto;color:#a9bdd5;font:12px/1.5 Consolas,monospace}.empty{color:var(--muted);padding:18px 0}.credentials{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}.span4{grid-column:span 2}.legs{grid-template-columns:1fr}}@media(max-width:560px){header{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:1fr}.span2,.span4{grid-column:span 1}.wrap{padding:20px 14px}}
.actions{display:flex;gap:10px;flex-wrap:wrap}.secondary{background:#21344e;color:var(--text);border:1px solid var(--line)}
</style></head><body><div class="wrap"><header><div class="brand"><h1>Arthur Goal Analysis</h1><p>Napi 2×2×3 kutatási vezérlőpult</p></div><div class="actions"><button class="secondary" id="copy" disabled>Másolás Astrának</button><button id="run">Elemzés indítása</button></div></header>
<main class="grid"><section class="card"><div class="metric">Rendszer</div><div class="value" id="system">Betöltés…</div><div class="credentials" id="credentials"></div></section><section class="card"><div class="metric">Versenysorozatok</div><div class="value" id="competitions">–</div><div class="detail" id="region"></div></section><section class="card"><div class="metric">Utolsó riport</div><div class="value" id="status">–</div><div class="detail" id="observed"></div></section><section class="card"><div class="metric">Elméleti összszorzó</div><div class="value" id="price">–</div><div class="detail" id="bookmaker"></div></section>
<section class="card span4 section"><h2>Mai szelvény</h2><div class="legs" id="legs"></div></section><section class="card span2 section"><h2>Legutóbbi futások</h2><div style="overflow:auto"><table><thead><tr><th>Idő</th><th>Állapot</th><th>Iroda</th><th>Szorzó</th></tr></thead><tbody id="history"></tbody></table></div></section><section class="card span2 section"><h2>Futási napló</h2><div class="log" id="log">A vezérlőpult készen áll.</div></section></main></div>
<script>const TOKEN=__DASHBOARD_TOKEN__;let latestData=null;const q=s=>document.querySelector(s);const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const fmt=v=>v==null?'–':Number(v).toFixed(2);const badge=(text,kind='')=>`<span class="pill ${kind}">${esc(text)}</span>`;
function render(d){latestData=d.latest;const r=d.runtime;q('#run').disabled=r.running;q('#run').textContent=r.running?'Elemzés folyamatban…':'Elemzés indítása';q('#copy').disabled=!d.latest;q('#system').textContent=r.running?'Fut':'Készen áll';q('#system').className='value '+(r.running?'warn':'ok');q('#competitions').textContent=d.configuration.competition_count;q('#region').textContent=`Odds-régió: ${d.configuration.odds_region||'–'} · Elsődleges: ${(d.configuration.preferred_bookmakers||[]).join(', ')||'–'}`;q('#credentials').innerHTML=Object.entries(d.credentials).map(([k,v])=>badge(`${k}: ${v?'rendben':'hiányzik'}`,v?'ok':'bad')).join('');const x=d.latest;q('#status').textContent=x?.construction_status||'Nincs riport';q('#status').className='value '+(x?.construction_status==='COMPLETE'?'ok':x?.construction_status==='DATA_BLOCKED'?'bad':'warn');q('#observed').textContent=x?.observed_at?new Date(x.observed_at).toLocaleString('hu-HU'):'–';q('#price').textContent=fmt(x?.combined_price);q('#bookmaker').textContent=x?.bookmaker?`Iroda: ${x.bookmaker}`:'Nincs kiválasztott iroda';q('#legs').innerHTML=x?.legs?.length?x.legs.map((l,i)=>`<article class="leg"><div class="metric">${i+1}. láb</div><div class="teams">${esc(l.home_team)} – ${esc(l.away_team)}</div><div class="detail">${esc(l.market_key)} · ${esc(l.selection_key)} · ${esc(l.period)}</div><div class="detail">Bizonyítékpont: ${fmt(l.support_score)}</div><div class="odds">${fmt(l.decimal_price)}</div></article>`).join(''):'<div class="empty">A legutóbbi futásból nem állt össze három láb.</div>';q('#history').innerHTML=(d.history||[]).slice(0,12).map(h=>`<tr><td>${esc(h.date||'–')}</td><td>${badge(h.construction_status,h.construction_status==='COMPLETE'?'ok':'warn')}</td><td>${esc(h.bookmaker||'–')}</td><td>${fmt(h.combined_price)}</td></tr>`).join('');q('#log').textContent=r.last_output||'A vezérlőpult készen áll.'}
async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});render(await r.json())}catch(e){q('#system').textContent='Kapcsolati hiba'}}q('#run').addEventListener('click',async()=>{q('#run').disabled=true;const r=await fetch('/api/run',{method:'POST',headers:{'X-Dashboard-Token':TOKEN}});const d=await r.json();q('#log').textContent=d.message;refresh()});q('#copy').addEventListener('click',async()=>{if(!latestData)return;const packet={date:latestData.date,status:latestData.construction_status,combined_price:latestData.combined_price,bookmaker:latestData.bookmaker,legs:latestData.legs,input_candidates:latestData.input_candidate_count,eligible_candidates:latestData.eligible_candidate_count,data_issues:latestData.data_issue_count};await navigator.clipboard.writeText('Arthur DAILY_223 Astra-felülvizsgálat:\n'+JSON.stringify(packet,null,2));q('#copy').textContent='Vágólapra másolva';setTimeout(()=>q('#copy').textContent='Másolás Astrának',1800)});refresh();setInterval(refresh,2500);</script></body></html>'''
