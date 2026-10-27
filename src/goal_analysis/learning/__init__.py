"""Immutable daily learning reports, never automatic strategy changes."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from goal_analysis.storage.portfolio_ledger import _stamp

from ._findings import findings_and_proposals, review_packet, validate_priorities
from ._metrics import digest, measure
from .reviewer import CodexLearningReviewer, empty_usage, llm_result

__all__ = ["CodexLearningReviewer", "LearningAgent"]
BERLIN = ZoneInfo("Europe/Berlin")
LIMITS = [
    "Leíró megfigyelés, nem bizonyított előny vagy jövőbeli nyereségígéret.",
    "A meccsenként közös választások és a napi szelvények nem független minták.",
    "A kutatási és rögzített ajánlásokhoz nem találunk ki tétet vagy hiányzó szorzót.",
    "A rögzített csomagok átfedhetnek a tényleges és kutatási csoporttal; a csoportok nem összeadhatók.",
    "A tényleges nettó csak a kézzel rögzített téteket és árakat használja; szolgáltatási költséget nem tartalmaz.",
    "A régi ismeretlen stratégia-verzió ismeretlen marad; mai beállításból nem rekonstruáljuk.",
    "A modellmérés csak az eredetileg mentett, kiválasztott előrejelzéseket látja; kalibráció és okság nem igazolt.",
    "Ez a modul kizárólag jelentést és kézi elbírálásra szánt javaslatot ír; stratégiát, kódot, tétet és konfigurációt nem módosít.",
]


class LearningAgent:
    def __init__(self, root, ledger=None, clock=None):
        self.root = Path(root).resolve()
        self.ledger_path = Path(ledger.path if ledger is not None else self.root / "data/arthur/ledger.sqlite3")
        self.path = self.root / "data/learning/learning.sqlite3"
        self.clock = clock or (lambda: datetime.now(UTC))

    @contextmanager
    def _connection(self, *, initialize=False):
        if initialize:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=30)
        else:
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=15)
            db.execute("PRAGMA query_only=ON")
        db.row_factory = sqlite3.Row
        try:
            if initialize:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS learning_reports (
                        day TEXT PRIMARY KEY, report_id TEXT NOT NULL UNIQUE,
                        as_of TEXT NOT NULL, created_at TEXT NOT NULL,
                        report_json TEXT NOT NULL, report_hash TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS learning_attempts (
                        day TEXT PRIMARY KEY, claimed_at TEXT NOT NULL,
                        draft_json TEXT NOT NULL, draft_hash TEXT NOT NULL);
                    CREATE TRIGGER IF NOT EXISTS learning_reports_no_update
                        BEFORE UPDATE ON learning_reports BEGIN
                        SELECT RAISE(ABORT,'learning reports are immutable'); END;
                    CREATE TRIGGER IF NOT EXISTS learning_reports_no_delete
                        BEFORE DELETE ON learning_reports BEGIN
                        SELECT RAISE(ABORT,'learning reports are immutable'); END;
                """)
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _verify(row):
        report = json.loads(row["report_json"])
        recorded = report.pop("report_hash", None)
        if digest(report) != row["report_hash"] or recorded != row["report_hash"]:
            raise ValueError("A tanuló jelentés ellenőrző összege hibás.")
        report["report_hash"] = recorded
        return report

    def list_reports(self, limit=30):
        if type(limit) is not int or not 1 <= limit <= 365:
            raise ValueError("A jelentéslista korlátja 1 és 365 közötti egész szám.")
        if not self.path.is_file():
            return []
        with self._connection() as db:
            rows = db.execute("SELECT * FROM learning_reports ORDER BY day DESC LIMIT ?", (limit,)).fetchall()
        return [self._verify(row) for row in rows]

    def latest(self):
        reports = self.list_reports(1)
        return reports[0] if reports else None

    def release_review(self, calibration_artifact_id, *, as_of, enabled=False):
        """Separate, physically verified proposal; the original report stays immutable."""
        from goal_analysis.jobs.calibration_release import review_release

        return review_release(self.root, calibration_artifact_id, as_of=as_of,
                              enabled=enabled, clock=self.clock)

    def _build(self, as_of, now):
        measured = measure(self.ledger_path, as_of)
        findings, proposals = findings_and_proposals(measured["evidence"])
        total = sum(m["tickets"]["total"] for m in measured["metrics"].values())
        day = as_of.astimezone(BERLIN).date().isoformat()
        report = {
            "schema_version": 1, "report_id": f"learning-{day}-{digest(measured)[:12]}",
            "date": day, "as_of": as_of.isoformat(), "created_at": now.isoformat(),
            "status": "PARTIAL" if total else "NO_DATA",
            "summary": "A teljes, az időhatárig elérhető helyi napló ellenőrizhető napi mérése."
            if total else "Még nincs értékelhető szelvény vagy ajánlás a helyi naplóban.",
            "snapshot_policy": "FIRST_IMMUTABLE_REPORT_PER_BERLIN_DAY",
            "snapshot_note": "Az első napi pillanatkép változatlan. A később beérkező eredmények a következő napi jelentésben jelennek meg.",
            "counting_basis": "Teljes SQLite történet az as_of időhatárig; kutatás: legutolsó kezdés előtti profil/napi ajánlás, már elkezdett ajánlás nem helyettesíthető.",
            "automatic_changes": False, **measured, "findings": findings,
            "recommendations": proposals, "limits": list(LIMITS),
        }

        try:
            from .weather_forecast_quality import inspect_weather_forecast_quality

            report["weather_conditioned_forecast_quality"] = inspect_weather_forecast_quality(
                self.root, as_of=as_of, max_seconds=30)
        except Exception:  # noqa: BLE001 - optional diagnostics never veto baseline learning.
            report["weather_conditioned_forecast_quality"] = {
                "status": "UNAVAILABLE", "reason": "OPTIONAL_WEATHER_LEARNING_UNAVAILABLE",
                "mode": "SHADOW", "decision_effect": "NONE", "heldout_weather_comparison_complete": False,
                "weather_stratum_basis": "PREMATCH_FORECAST_FEATURE_PROXY_NOT_REALIZED_WEATHER",
                "external_requests": 0, "additional_model_fits": 0, "production_writes": 0}
        return report

    def _save(self, db, report):
        report["report_hash"] = digest(report)
        db.execute("INSERT OR IGNORE INTO learning_reports VALUES (?,?,?,?,?,?)", (
            report["date"], report["report_id"], report["as_of"], report["created_at"],
            json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True), report["report_hash"],
        ))
        return self._verify(db.execute("SELECT * FROM learning_reports WHERE day=?", (report["date"],)).fetchone())

    def _export(self, report):
        # SQLite is authoritative. An unavailable optional export never causes another AI request.
        directory = self.root / "reports/learning"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{report['date']}-{report['report_hash'][:16]}.json"
            with path.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, allow_nan=False, indent=2)
        except (FileExistsError, OSError):
            pass

    def run(self, as_of: datetime, reviewer=None):
        as_of, now = _stamp(as_of), _stamp(self.clock())
        if as_of > now:
            raise ValueError("A tanulás időhatára nem lehet a jövőben.")
        day = as_of.astimezone(BERLIN).date().isoformat()
        with self._connection(initialize=True) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM learning_reports WHERE day=?", (day,)).fetchone()
            if existing:
                final = self._verify(existing)
                self._export(final)
                return final
            attempt = db.execute("SELECT * FROM learning_attempts WHERE day=?", (day,)).fetchone()
            if attempt:
                report = json.loads(attempt["draft_json"])
                if digest(report) != attempt["draft_hash"]:
                    raise ValueError("A napi tanuló próbálkozás pillanatképe sérült.")
                if now - _stamp(attempt["claimed_at"]) < timedelta(minutes=10):
                    # A concurrent invocation never calls the model or overwrites its future report.
                    report["summary"] = "A napi tanuló értékelés már folyamatban van."
                    report["llm"] = llm_result("INTERRUPTED", category="ALREADY_IN_PROGRESS")
                    report["in_progress"] = True
                    return report
                usage = empty_usage()
                usage.update(requests=None, execution_uncertain=True)
                report["llm"] = llm_result("INTERRUPTED", usage, "UNCERTAIN_PREVIOUS_EXECUTION")
                final = self._save(db, report)
                self._export(final)
                return final
            report = self._build(as_of, now)
            # Commit the reservation BEFORE any subprocess/auth/model operation.
            db.execute("INSERT INTO learning_attempts VALUES (?,?,?,?)", (
                day, now.isoformat(), json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True),
                digest(report),
            ))
        packet = review_packet(report)
        decided = sum(m["tickets"]["decided"] for m in report["metrics"].values())
        if day != now.astimezone(BERLIN).date().isoformat() or not decided or not packet["proposal_options"]:
            report["llm"] = llm_result("SKIPPED_NO_DATA")
        else:
            usage = empty_usage()
            usage["requests"] = 1
            try:
                response = (reviewer or CodexLearningReviewer(self.root)).review(packet)
                if not isinstance(response, dict):
                    raise TypeError("Invalid reviewer result")
                status = response.get("status")
                if status not in {"COMPLETE", "UNAVAILABLE", "INVALID_RESPONSE"}:
                    raise ValueError("Unknown reviewer status")
                usage = empty_usage()
                supplied_usage = response.get("usage", {})
                for key in ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens", "total_tokens"):
                    value = supplied_usage.get(key)
                    if type(value) is int and value >= 0:
                        usage[key] = value
                usage["requests"] = 1 if supplied_usage.get("requests", 1) else 0
                usage["usage_available"] = all(usage[k] is not None for k in ("input_tokens", "output_tokens"))
                elapsed = supplied_usage.get("elapsed_seconds")
                if type(elapsed) in (int, float) and 0 <= elapsed <= 3600:
                    usage["elapsed_seconds"] = elapsed
                category = response.get("error_category")
                if category not in {None, "QUOTA_EXHAUSTED", "TRANSIENT_RATE_LIMIT", "AUTH_REQUIRED",
                                    "MODEL_UNAVAILABLE", "TRANSIENT_CONNECTION", "CLI_UNAVAILABLE",
                                    "UNKNOWN", "INPUT_LIMIT", "RESPONSE_CONTRACT"}:
                    category = "UNKNOWN"
                report["llm"] = llm_result(status, usage, category, bool(response.get("transient")))
                if status == "COMPLETE":
                    priorities = [{key: p[key] for key in ("recommendation_id", "priority", "evidence_ids")}
                                  for p in response.get("proposals", [])]
                    selected = validate_priorities({"priorities": priorities}, packet)
                    selected_ids = {p["recommendation_id"] for p in selected}
                    report["recommendations"] = selected + [p for p in report["recommendations"]
                                                              if p["recommendation_id"] not in selected_ids]
            except Exception:  # noqa: BLE001 - a reviewer failure must preserve deterministic evidence
                report["llm"] = llm_result("INVALID_RESPONSE", usage, "RESPONSE_CONTRACT")
        if report["status"] != "NO_DATA" and report["llm"]["status"] == "COMPLETE" and not report["coverage"]["invalid_records"]:
            report["status"] = "COMPLETE"
        with self._connection(initialize=True) as db:
            db.execute("BEGIN IMMEDIATE")
            final = self._save(db, report)
        self._export(final)
        return final
