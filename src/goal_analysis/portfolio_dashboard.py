"""Local-only HTTP control surface for the Arthur portfolio workflow."""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from goal_analysis.agents.codex_review import codex_installed
from goal_analysis.config.portfolio import load_settings, save_settings, validate_settings
from goal_analysis.jobs.league_scope import MINIMUM_PENDING_FIXTURES
from goal_analysis.jobs.nightly import automation_snapshot
from goal_analysis.jobs.portfolio import RunLock, safe_error, validate_target_day
from goal_analysis.quote_metadata import quote_metadata
from goal_analysis.storage.portfolio_ledger import PortfolioLedger
from goal_analysis.strategy_provenance import APPLICATION_VERSION


def model_environment() -> dict:
    packages = {}
    for package in ("penaltyblog", "catboost", "threadpoolctl"):
        try:
            packages[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            packages[package] = None
    return {
        "status": "INSTALLED" if all(packages.values()) else "DEPENDENCY_MISSING",
        "packages": packages,
        "mode": "EXPERIMENTAL_PAPER_ONLY",
        "probability_kind": "UNCALIBRATED_MODEL_ESTIMATE",
    }


def _json(path: Path):
    return json.loads(path.read_text("utf-8-sig"))


_STATUS_UNUSED_REPORT_FIELDS = frozenset({
    "odds_history_snapshot", "numeric_models", "profile_proposals", "strategy_manifest",
})
_STATUS_RUN_FIELDS = (
    "run_id", "date", "construction_status", "observed_at", "created_at", "finished_at",
    "report_sha256",
)
_STATUS_SOURCE_REF_LIMIT = 50


def _evidence_preview(report):
    """Keep the references the UI renders; never change the stored evidence."""
    if not isinstance(report, dict) or not isinstance(report.get("evidence"), list):
        return report
    rows = []
    for item in report["evidence"]:
        if isinstance(item, dict) and isinstance(item.get("source_refs"), list):
            item = {
                **item,
                "source_ref_count": len(item["source_refs"]),
                "source_refs": item["source_refs"][:_STATUS_SOURCE_REF_LIMIT],
            }
        rows.append(item)
    return {**report, "evidence": rows}


def compact_status_snapshot(snapshot: dict) -> dict:
    """A read-only UI projection; full artifacts remain available through export.

    The client uses run history only as a twelve-row date selector. Its numeric
    leg details are embedded in tickets/recommendations, not numeric_models.
    Keep every ticket and model_details intact, including protected DAILY_223.
    """
    result = dict(snapshot)
    latest = snapshot.get("latest")
    if isinstance(latest, dict):
        result["latest"] = {
            key: value for key, value in latest.items()
            if key not in _STATUS_UNUSED_REPORT_FIELDS
        }
        supplement = latest.get("sports_supplement")
        if isinstance(supplement, dict) and isinstance(supplement.get("the_stats_api"), dict):
            # Full team/player records belong to the persisted report and Grok
            # packet, while the phone only renders this small coverage summary.
            collection = supplement["the_stats_api"]
            result["latest"]["sports_supplement"] = {
                **supplement, "the_stats_api": {
                    key: collection[key] for key in
                    ("version", "status", "observed_at", "coverage", "usage", "data_hash")
                    if key in collection
                },
            }
    result["history"] = [
        {key: report[key] for key in _STATUS_RUN_FIELDS if key in report}
        for report in snapshot.get("history", [])[:12]
    ]
    result["recommendation_archive"] = []
    result["learning"] = _evidence_preview(snapshot.get("learning"))
    latest_learning_id = (snapshot.get("learning") or {}).get("report_id")
    result["learning_history"] = [
        _evidence_preview(report) for report in snapshot.get("learning_history", [])
        if not latest_learning_id or report.get("report_id") != latest_learning_id
    ]
    team = snapshot.get("team")
    if isinstance(team, dict):
        result["team"] = {
            **team, "latest_evaluation": _evidence_preview(team.get("latest_evaluation")),
        }
    result["status_projection"] = {
        "version": 1, "history_kind": "RUN_SUMMARIES", "history_limit": 12,
        "source_refs_limit": _STATUS_SOURCE_REF_LIMIT,
        "omitted_latest_fields": sorted(_STATUS_UNUSED_REPORT_FIELDS),
        "recommendation_archive_in_export": True,
        "artifact_hashes_refer_to": "FULL_STORED_REPORTS",
    }
    return result


GERMAN_PROFILE_NAMES = {
    "daily223": "DAILY_223",
    "DAILY_223": "DAILY_223",
    "kronikas": "Chronist",
    "ritmusor": "Rhythmuswächter",
    "parharcmester": "Duellmeister",
    "orszem": "Wächter",
    "merlin": "Merlin",
    "h1_over05": "Auftakt",
    "gauss": "GAUSS",
    "kepler": "KEPLER",
    "laplace": "LAPLACE",
}


def _one_line(value, fallback="") -> str:
    return " ".join(str(value or fallback).split())


def _german_market(leg: dict) -> str:
    market = str(leg.get("market_key") or leg.get("market") or "").lower()
    choice = str(leg.get("selection_key") or leg.get("selection") or "").lower()
    period = str(leg.get("period") or "").lower()
    first_half = period in {"h1", "1h", "first_half", "firsthalf"}
    prefix = "1. Halbzeit: " if first_half else ""
    if "btts" in market or "both_teams" in market:
        return prefix + f"Beide Teams treffen: {'Nein' if choice in {'no', 'nein'} else 'Ja'}"
    if any(token in market for token in ("total", "over", "under", "goals")):
        line = leg.get("line", leg.get("points", leg.get("total")))
        if line is None:
            match = re.search(r"(?:totals?|over|under|goals?)[_ :]*(\d+(?:[._]\d+)?)", market)
            line = match.group(1).replace("_", ".") if match else None
        direction = "Unter" if "under" in choice or market.startswith("under") else "Über"
        rendered = str(line).replace(".", ",") if line is not None else "Tore"
        return prefix + (f"{direction} {rendered} Tore" if line is not None else f"{direction} Tore")
    if market in {"h2h", "match_winner", "moneyline", "1x2"}:
        return prefix + {
            "home": "Heimsieg", "1": "Heimsieg", "home_win": "Heimsieg",
            "away": "Auswärtssieg", "2": "Auswärtssieg", "away_win": "Auswärtssieg",
            "draw": "Unentschieden", "x": "Unentschieden", "tie": "Unentschieden",
        }.get(choice, "Spielausgang")
    if "double_chance" in market:
        return prefix + {
            "home_draw": "Doppelte Chance: 1X", "1x": "Doppelte Chance: 1X",
            "away_draw": "Doppelte Chance: X2", "x2": "Doppelte Chance: X2",
            "home_away": "Doppelte Chance: 12", "12": "Doppelte Chance: 12",
        }.get(choice, "Doppelte Chance")
    return prefix + _one_line(leg.get("selection_label") or choice or market, "Auswahl")


def german_ticket_text(snapshot: dict) -> str:
    """Render the Results-page ticket set as terse German plain text."""
    recommendations = snapshot.get("recommendations") or (snapshot.get("latest") or {}).get(
        "recommendations", []
    )
    linked = {
        item.get("portfolio_ticket_id")
        for item in recommendations
        if item.get("portfolio_ticket_id")
    }
    entries = [*recommendations]
    entries.extend(
        item for item in snapshot.get("tickets", []) if item.get("ticket_id") not in linked
    )
    blocks = []
    seen = set()
    for ticket in entries:
        legs = ticket.get("legs") or []
        if not legs:
            continue
        identity = ticket.get("recommendation_id") or ticket.get("ticket_id") or (
            ticket.get("profile_id"),
            tuple(leg.get("candidate_id") for leg in legs),
        )
        if str(identity) in seen:
            continue
        seen.add(str(identity))
        profile = GERMAN_PROFILE_NAMES.get(
            ticket.get("profile_id"), _one_line(ticket.get("profile_name"), "Tippschein")
        )
        rows = [profile]
        for leg in legs:
            home = _one_line(leg.get("home_team"), "Heimteam")
            away = _one_line(leg.get("away_team"), "Auswärtsteam")
            rows.append(f"{home} – {away} | {_german_market(leg)}")
        blocks.append("\n".join(rows))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


class ArthurState:
    def __init__(
        self, root: Path, config_path: Path | None = None, settings_path: Path | None = None
    ):
        self.root = root.resolve()
        self.config_path = (config_path or self.root / "config/daily223-live.json").resolve()
        self.settings_path = (settings_path or self.root / "config/arthur-settings.json").resolve()
        self.ledger = PortfolioLedger(self.root / "data/arthur/ledger.sqlite3")
        self.lock = threading.Lock()
        self.competition_cache = {}
        self.source_health_cache = {}
        self.source_health_lock = threading.Lock()
        self.runtime = {
            "running": False,
            "operation": None,
            "last_output": "",
            "last_exit_code": None,
            "last_started_at": None,
            "last_finished_at": None,
        }

    def snapshot(self, day: str | None = None, *, full_export: bool = True) -> dict:
        from goal_analysis.agents.expert_registry import network_registry
        from goal_analysis.jobs.expert_advisory import snapshot as expert_snapshot
        from goal_analysis.jobs.odds_monitor import monitor_status

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
        tickets = self.ledger.list_tickets(date=day, monetary_contract="CURRENT_HASH_V2")
        now = datetime.now(UTC)
        for ticket in tickets:
            ticket["from_previous_run"] = bool(runs and ticket.get("run_id") != runs[0]["run_id"])
            for leg in ticket["legs"]:
                leg.update(
                    quote_metadata(leg.get("quote_timestamp_raw", leg.get("quoted_at")), now)
                )
        with self.lock:
            runtime = dict(self.runtime)
        runtime["odds_monitor"] = monitor_status(self.root, now)
        recommendations = self.ledger.list_recommendations(date=day)
        team = self.team_snapshot(settings)
        expert_advisory = expert_snapshot(self.root, day, runs[0] if runs else None)
        if runtime["running"]:
            try:
                progress = _json(self.root / "data/arthur/progress.json")
                if progress["at"] >= runtime["last_started_at"]:
                    runtime.update(message=progress["message"], stage=progress["stage"])
            except (OSError, ValueError, KeyError):
                pass
        result = {
            "today": today,
            "selected_date": day,
            "server_time": datetime.now(UTC).isoformat(),
            "timezone": "Europe/Berlin",
            "settings": settings,
            "application_version": APPLICATION_VERSION,
            "model_environment": model_environment(),
            "automation": automation_snapshot(self.root, now),
            **self.learning_snapshot(),
            "team": team,
            "expert_advisory": expert_advisory,
            "source_health": self.source_health(runs[0] if runs else None, config, now),
            "expert_network": network_registry(
                self.root, settings, runs[0] if runs else None, expert_advisory, team
            ),
            "tipster_club": self.tipster_snapshot(settings, runs, recommendations, team),
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
                "api_football": False,
                "the_stats_api": bool(os.environ.get("THE_STATS_API_KEY", "").strip()),
                "the_odds_api": False,
                "openai": False,
                "codex_cli": codex_installed(),
            },
            "runtime": runtime,
            "latest": runs[0] if runs else None,
            "history": runs,
            "tickets": tickets,
            "recommendations": recommendations,
            "recommendation_archive": (
                self.ledger.list_recommendations(date=day, archive=True) if full_export else []
            ),
            "freezes": self.ledger.list_freezes(date=day),
        }
        return result if full_export else compact_status_snapshot(result)

    def source_health(self, report, config, now):
        from goal_analysis.jobs.source_health import source_health
        from goal_analysis.normalization.canonical import utc

        key = json.dumps([report.get("run_id"), report.get("report_sha256"), report.get("date"),
                          config.get("leagues", [])] if report else [None, config.get("leagues", [])],
                         sort_keys=True)
        with self.source_health_lock:
            cached = self.source_health_cache.get(key)
            visible = False
            if cached:
                try:
                    moments = [cached[1].get("as_of"),
                               (cached[1].get("league_coverage") or {}).get("as_of"),
                               (report or {}).get("finished_at") or (report or {}).get("observed_at")]
                    visible = all(utc(moment) <= utc(now) for moment in moments if moment is not None)
                except (ValueError, TypeError):
                    pass
            if cached and visible and time.monotonic() - cached[0] < 60:
                from copy import deepcopy
                return deepcopy(cached[1])
            try:
                value = source_health(self.root, report, leagues=config.get("leagues", []), now=now)
            except Exception:  # noqa: BLE001 - diagnostics must not hide existing tickets.
                value = {"mode": "SHADOW", "status": "UNAVAILABLE",
                         "reason": "SOURCE_HEALTH_AUDIT_FAILED", "rows": [], "external_requests": 0}
            if value.get("status") == "UNAVAILABLE":
                return value
            if len(self.source_health_cache) >= 16:
                self.source_health_cache.clear()
            from copy import deepcopy
            self.source_health_cache[key] = (time.monotonic(), deepcopy(value))
            return value

    @staticmethod
    def tipster_snapshot(settings, runs, recommendations, team):
        """Current catalogue with original day-specific tickets, never backfilled tips."""
        from goal_analysis.jobs.portfolio import recommendation_overlap
        from goal_analysis.tipsters.catalog import TIPSTER_PROFILE_IDS, get_catalogue
        from goal_analysis.tipsters.strategy_contracts import club_strategy_contracts

        try:
            contracts = club_strategy_contracts()
        except (OSError, ValueError, TypeError, KeyError):
            # Read-only metadata must never hide already saved tickets.
            contracts = {"status": "UNAVAILABLE", "mode": "SHADOW_DECLARATION", "contracts": [],
                         "reason": "STRATEGY_CONTRACT_PROJECTION_FAILED", "decision_effect": False}
        current = {r["profile_id"]: r for r in recommendations if r["profile_id"] in TIPSTER_PROFILE_IDS}
        # Abstentions have no legs and are stored in the immutable report, not as wagers.
        # The ledger withdraws ordinary unstarted/unwagered explicit abstentions;
        # actual-wager or already-started historical evidence may remain visible.
        for item in (runs[0] if runs else {}).get("recommendations", []):
            if item["profile_id"] in TIPSTER_PROFILE_IDS and not item.get("legs"):
                current.setdefault(item["profile_id"], item)
        return {
            "catalogue": get_catalogue(), "recommendations": list(current.values()),
            "strategy_contracts": contracts,
            "enabled_profiles": [p for p in settings["enabled_profiles"] if p in TIPSTER_PROFILE_IDS],
            "overlap": recommendation_overlap(list(current.values())),
            "max_daily": len(TIPSTER_PROFILE_IDS), "existing_profile_count": 10,
            "members": [m for m in team.get("members", []) if m["profile_id"] in TIPSTER_PROFILE_IDS],
            "metrics_as_of": (team.get("latest_evaluation") or {}).get("as_of"),
            "selection_basis": "SHARED_DATA_AND_MODELS_WITH_PERSONA_PREFERENCES",
            "independent_models": False, "automatic_wager_changes": False,
        }

    def learning_snapshot(self) -> dict:
        from goal_analysis.learning import LearningAgent

        try:
            agent = LearningAgent(self.root)
            return {"learning": agent.latest(), "learning_history": agent.list_reports(limit=14)}
        except (OSError, ValueError, sqlite3.Error):
            return {"learning": None, "learning_history": [], "learning_error": "LEARNING_REPORT_UNAVAILABLE"}

    def paper_performance(self, month: str | None = None) -> dict:
        """One EUR per canonical daily recommendation, never an actual wager."""
        from goal_analysis.storage.paper_performance import build_paper_performance

        result = build_paper_performance(self.ledger.list_recommendations(), month=month)
        result["as_of"] = datetime.now(UTC).isoformat()
        result["application_version"] = APPLICATION_VERSION
        return result

    def update_settings(self, patch: dict) -> dict:
        with self.lock:
            if self.runtime["running"]:
                raise RuntimeError("Futó elemzés alatt a beállítások nem módosíthatók.")
            with RunLock(self.root / "data/arthur/run.lock"):
                current = load_settings(self.settings_path)
                if "openai" in patch:
                    if not isinstance(patch["openai"], dict):
                        raise ValueError("Az OpenAI-beállítás objektum legyen.")
                    patch = {**patch, "openai": {**current["openai"], **patch["openai"]}}
                return save_settings(self.settings_path, {**current, **patch})

    def team_snapshot(self, settings=None) -> dict:
        from goal_analysis.team.manager import TeamManager
        from goal_analysis.team.scouting import Scout

        try:
            result = TeamManager(self.root).snapshot()
            result["scouting"] = Scout(self.root).snapshot()
            result["enabled_profiles"] = (settings or load_settings(self.settings_path))["enabled_profiles"]
            for member in result.get("members", []):
                member["enabled_by_user"] = member["profile_id"] in result["enabled_profiles"]
            result["latest_auditions"] = []
            for report in self.ledger.list_runs(limit=30):
                auditions = [dict(item, run_id=report["run_id"], date=report["date"])
                             for item in report.get("recommendations", [])
                             if item.get("publication_scope") == "PAPER_AUDITION"]
                if auditions:
                    result["latest_auditions"] = auditions
                    break
            return result
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            return {"name": "A Szent Grál", "status": "UNAVAILABLE", "error": "TEAM_STORE_UNAVAILABLE",
                    "members": [], "events": [], "latest_auditions": [], "automatic_wager_changes": False}

    def update_team_status(self, payload: dict) -> dict:
        from goal_analysis.team.manager import TeamManager

        if set(payload) != {"profile_id", "status", "reason"}:
            raise ValueError("Agent, státusz és indoklás szükséges.")
        reason = payload["reason"]
        if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 500:
            raise ValueError("Adj 3–500 karakteres indoklást a visszakövethető döntéshez.")
        with self.lock:
            if self.runtime["running"]:
                raise RuntimeError("Futó feladat alatt a csapat nem módosítható.")
            with RunLock(self.root / "data/arthur/run.lock"):
                return TeamManager(self.root).set_status(payload["profile_id"], payload["status"], reason.strip())

    def start_team(self, operation: str) -> dict:
        if operation not in {"review", "scout"}:
            raise ValueError("Ismeretlen csapatművelet.")
        with self.lock:
            if self.runtime["running"]:
                raise RuntimeError("Már fut egy Arthur-feladat.")
            self.runtime.update(running=True, operation=f"team-{operation}",
                                last_output="A Szent Grál feladata elindult.", last_exit_code=None,
                                last_started_at=datetime.now(UTC).isoformat(), last_finished_at=None)
        threading.Thread(target=self._execute_team, args=(operation,), daemon=True,
                         name=f"arthur-team-{operation}").start()
        return {"started": True, "operation": f"team-{operation}"}

    def _execute_team(self, operation):
        from goal_analysis.learning import LearningAgent
        from goal_analysis.team.manager import TeamManager
        from goal_analysis.team.scouting import Scout

        try:
            with RunLock(self.root / "data/arthur/run.lock"):
                now = datetime.now(UTC)
                if operation == "review":
                    result = TeamManager(self.root).evaluate(
                        now, learning_report=LearningAgent(self.root).latest(),
                        settings=load_settings(self.settings_path),
                    )
                else:
                    result = Scout(self.root).run(now)
            code = 0 if result.get("status") not in {"UNAVAILABLE", "INTERRUPTED", "PARTIAL"} else 2
            output = "A Szent Grál jelentése elmentve; új AI-hívás vagy fogadáskötés nem történt."
            if code:
                output = "A feladat részleges eredménnyel zárult. A részletek a Szent Grál lapon láthatók."
        except Exception:  # noqa: BLE001 - never expose remote messages or local paths
            output, code = "A csapatfeladat nem fejeződött be. A mentett előzmények megmaradtak.", 1
        with self.lock:
            self.runtime.update(running=False, last_output=output, last_exit_code=code,
                                last_finished_at=datetime.now(UTC).isoformat())

    def competition_catalogue(self, day: str, force: bool = False) -> dict:
        """Passive GET projection; refresh means local reread, never collection."""
        target = date.fromisoformat(day)
        now = datetime.now(UTC)
        validate_target_day(target, now)
        from goal_analysis.jobs.readonly_competitions import read_competition_catalogue

        result = read_competition_catalogue(self.root, _json(self.config_path), target, as_of=now)
        result['local_reload_requested'] = bool(force)
        return result

    # Backward-compatible explicit collection command; never route passive GET here.
    def competitions(self, day: str, force: bool = False) -> dict:
        target = date.fromisoformat(day)
        validate_target_day(target, datetime.now(UTC))
        cached = self.competition_cache.get(day)
        if not force and cached and (datetime.now(UTC) - cached[0]).total_seconds() < 300:
            return cached[1]
        config = _json(self.config_path)
        from goal_analysis.jobs.the_stats_live import calendar_options

        options = calendar_options(self.root, config, target, lambda: datetime.now(UTC))
        result = {
            "date": day,
            "competitions": options,
            "serie_c_fallback_below": MINIMUM_PENDING_FIXTURES,
        }
        with self.lock:
            self.competition_cache[day] = (datetime.now(UTC), result)
        return result

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
            selected = payload.get("competition_ids")
            if selected is not None and (
                not isinstance(selected, list)
                or not 1 <= len(selected) <= 40
                or any(type(item) is not int or item <= 0 for item in selected)
                or len(set(selected)) != len(selected)
            ):
                raise ValueError("Válassz 1–40 különböző sorozatot az adott nap kínálatából.")
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
            if selected is not None:
                command += ["--competition-ids", ",".join(map(str, selected))]
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
            inline=None,
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
            elif inline:
                self.send_header("Content-Disposition", f'inline; filename="{inline}"')
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
                    return self._send(state.snapshot(query.get("date", [None])[0], full_export=False))
                if parsed.path == "/api/tickets.txt":
                    snapshot = state.snapshot(query.get("date", [None])[0], full_export=False)
                    payload = german_ticket_text(snapshot)
                    return self._send(
                        payload,
                        media="text/plain; charset=utf-8",
                        inline=f"Arthur-Tippscheine-{snapshot['selected_date']}.txt",
                    )
                if parsed.path in {"/api/codex-advisory", "/api/nemotron"}:
                    from goal_analysis.jobs.nemotron import snapshot

                    return self._send(snapshot(state.root, query.get("run_id", [None])[0]))
                if parsed.path == "/api/grok":
                    from goal_analysis.jobs.grok_advisory import snapshot

                    return self._send(snapshot(state.root, query.get("date", [None])[0]))
                if parsed.path == "/api/competitions":
                    if not secrets.compare_digest(self.headers.get("X-Arthur-Token", ""), token):
                        return self._send({"error": "Érvénytelen helyi munkamenet."}, 403)
                    day = query.get("date", [None])[0]
                    if day is None:
                        raise ValueError("Válassz napot a sorozatok lekéréséhez.")
                    return self._send(state.competition_catalogue(day, query.get("refresh") == ["1"]))
                if parsed.path == "/api/performance":
                    return self._send(
                        state.ledger.performance(
                            month=query.get("month", [None])[0],
                            mode=query.get("mode", ["actual"])[0],
                            monetary_contract="CURRENT_HASH_V2",
                        )
                    )
                if parsed.path == "/api/paper-performance":
                    month = query.get("month", [None])[0]
                    payload = state.paper_performance(month)
                    download = (
                        f"Arthur-1EUR-{month or 'all'}.json"
                        if query.get("download") == ["1"] else None
                    )
                    return self._send(payload, download=download)
                if parsed.path == "/api/export":
                    snapshot = state.snapshot(query.get("date", [None])[0], full_export=True)
                    payload = {
                        "schema_version": 3,
                        "date": snapshot["selected_date"],
                        "reports": snapshot["history"],
                        "tickets": snapshot["tickets"],
                        "recommendations": snapshot["recommendations"],
                        "recommendation_archive": snapshot["recommendation_archive"],
                        "freezes": snapshot["freezes"],
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
                if path == "/api/team/status":
                    return self._send(state.update_team_status(payload))
                if path in {"/api/team/review", "/api/team/scout"}:
                    return self._send(state.start_team(path.rsplit("/", 1)[-1]), 202)
                if path == "/api/freeze":
                    return self._send(state.ledger.freeze_run(payload["run_id"]))
                if path == "/api/wager":
                    result = state.ledger.record_wager(
                        payload["ticket_id"],
                        stake_eur=payload.get("stake_eur", 1),
                        actual_combined_price=payload.get("actual_combined_price"),
                    )
                    return self._send(state.ledger.get_ticket(payload["ticket_id"], monetary_contract="CURRENT_HASH_V2") if result else {"recorded": True})
                if path == "/api/void":
                    state.ledger.record_void(payload["ticket_id"], payload["fixture_id"], payload["note"])
                    return self._send(state.ledger.get_ticket(payload["ticket_id"], monetary_contract="CURRENT_HASH_V2"))
                if path == "/api/bookmaker-return":
                    expected = payload.get("expected_settlement_hash")
                    basis = payload.get("payout_basis")
                    if not isinstance(expected, str) or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
                        raise ValueError("A megjelenített elszámolás azonosítója szükséges; frissítsd a nézetet.")
                    if basis != "BOOKMAKER_CONFIRMED_GROSS_SPORTS_RETURN":
                        raise ValueError("Itt csak az iroda díjak előtti bruttó sportelszámolása rögzíthető.")
                    state.ledger.confirm_bookmaker_return(
                        payload["ticket_id"], payload["payout_eur"], payload["note"],
                        expected_settlement_hash=expected, payout_basis=basis,
                    )
                    return self._send(state.ledger.get_ticket(payload["ticket_id"], monetary_contract="CURRENT_HASH_V2"))
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
