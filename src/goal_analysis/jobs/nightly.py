"""Durable Berlin-time nightly workflow. No wager or strategy changes are possible."""

from __future__ import annotations

import json
import os
import sqlite3
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.daily_223_live import validate_live_config
from goal_analysis.config.portfolio import load_settings
from goal_analysis.jobs.portfolio import RunLock, run_portfolio, write_portfolio_bundle
from goal_analysis.storage.portfolio_ledger import PortfolioLedger, _hash
from goal_analysis.strategy_provenance import attach_strategy_provenance, strategy_manifest

BERLIN = ZoneInfo("Europe/Berlin")
STAGES = ("settlement", "generation", "archive", "learning")
TERMINAL = {"SUCCESS", "PARTIAL", "BLOCKED", "FAILED", "SKIPPED", "EXPIRED"}
MAX_ATTEMPTS = 3
SETTLEMENT_BUDGET = 20
THE_STATS_HISTORY_BUDGET = 20
THE_STATS_HISTORY_WINDOW_SECONDS = 600


def aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware clock is required")
    return value.astimezone(UTC)


def schedule_plan(now: datetime) -> dict:
    local = aware(now).astimezone(BERLIN)
    today_at_22 = datetime.combine(local.date(), time(22), BERLIN)
    next_run = today_at_22 if local < today_at_22 else datetime.combine(
        local.date() + timedelta(days=1), time(22), BERLIN
    )
    slot = local.date() if local.hour >= 22 else local.date() - timedelta(days=1)
    due_at = datetime.combine(slot, time(22), BERLIN)
    deadline = datetime.combine(slot + timedelta(days=1), time(6), BERLIN)
    return {
        "timezone": "Europe/Berlin", "scheduled_time": "22:00", "catchup_until": "06:00",
        "next_run_at": next_run.isoformat(),
        "next_target_date": (next_run.date() + timedelta(days=1)).isoformat(),
        "due": due_at <= local < deadline, "slot_date": slot.isoformat(),
        "target_date": (slot + timedelta(days=1)).isoformat(),
        "scheduled_at": due_at.isoformat(), "deadline": deadline.isoformat(),
    }


def _read_json(path: Path):
    return json.loads(path.read_text("utf-8-sig"))


class WorkflowError(RuntimeError):
    def __init__(self, reason: str, *, retryable: bool = True):
        self.reason = reason
        self.retryable = retryable
        super().__init__(reason)


def failure_reason(error) -> tuple[str, bool]:
    if isinstance(error, WorkflowError):
        return error.reason, error.retryable
    text = str(error).lower()
    if "daily_request_budget" in text:
        return "DAILY_REQUEST_BUDGET", False
    if "auth_required" in text:
        return "AUTH_REQUIRED", False
    if "provider_limit" in text:
        return "PROVIDER_LIMIT", False
    if any(part in text for part in ("not configured", "401", "403", "unauthorized", "invalid api")):
        return "AUTH_REQUIRED", False
    if any(part in text for part in ("429", "quota", "rate limit", "daily limit", "usage limit")):
        return "PROVIDER_LIMIT", False
    if isinstance(error, (ValueError, TypeError, KeyError)):
        return "INVALID_CONFIGURATION_OR_DATA", False
    return "TEMPORARY_FAILURE", True


class JobStore:
    def __init__(self, root: Path):
        self.path = Path(root) / "data/arthur/automation.sqlite3"

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("CREATE TABLE IF NOT EXISTS nightly_jobs (target_date TEXT PRIMARY KEY, state_json TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS nightly_events (id INTEGER PRIMARY KEY, target_date TEXT NOT NULL, at TEXT NOT NULL, state_json TEXT NOT NULL)")
        return db

    def save(self, job: dict, *, event=True):
        payload = json.dumps(job, ensure_ascii=False, sort_keys=True, allow_nan=False)
        db = self._connect()
        try:
            with db:
                db.execute("INSERT INTO nightly_jobs VALUES (?,?) ON CONFLICT(target_date) DO UPDATE SET state_json=excluded.state_json", (job["target_date"], payload))
                if event:
                    db.execute("INSERT INTO nightly_events(target_date,at,state_json) VALUES (?,?,?)", (job["target_date"], job["updated_at"], payload))
        finally:
            db.close()

    def jobs(self, limit=14):
        if not self.path.exists():
            return []
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT state_json FROM nightly_jobs ORDER BY target_date DESC LIMIT ?", (limit,)
            )]
        finally:
            db.close()

    def get(self, target):
        if not self.path.exists():
            return None
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            row = db.execute("SELECT state_json FROM nightly_jobs WHERE target_date=?", (target,)).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            db.close()


def public_job(job: dict) -> dict:
    return {key: deepcopy(job.get(key)) for key in (
        "job_id", "slot_date", "target_date", "status", "created_at", "updated_at",
        "next_retry_at", "stages", "usage", "manifest_sha256", "completed_at",
        "native_shadow",
    )}


def automation_snapshot(root: Path, now: datetime | None = None) -> dict:
    root = Path(root).resolve()
    plan = schedule_plan(now or datetime.now(UTC))
    try:
        jobs = JobStore(root).jobs()
        store_status = "AVAILABLE"
    except (OSError, ValueError, sqlite3.Error):
        jobs, store_status = [], "AUTOMATION_STORE_UNAVAILABLE"
    try:
        registration = _read_json(root / "data/arthur/schedule-status.json")
    except (OSError, ValueError):
        registration = {"status": "NOT_REGISTERED", "enabled": False}
    plan.update(
        schedule=registration, store_status=store_status, jobs=[public_job(job) for job in jobs],
        current_job=public_job(jobs[0]) if jobs else None,
        last_success_at=next((j.get("completed_at") for j in jobs if j["status"] == "COMPLETED"), None),
        notes=[
            "A Windows 15 percenként ellenőrzi az esedékességet; API/AI csak a napi munkánál fut.",
            "22:00 Europe/Berlin, nyári–téli idővel; kimaradás után legfeljebb másnap 06:00-ig pótol.",
            "Bekapcsolt vagy felébreszthető gép, internet és bejelentkezett Windows-felhasználó szükséges.",
            "A Szent Grál bizonyítékküszöbök alapján kezeli az éles csapatot; nem köt fogadást és nem emel tétet.",
        ],
    )
    return plan


class NightlyWorkflow:
    def __init__(self, root: Path, *, clock=None, generator=None, settler=None, learner=None,
                 ledger=None, config_path=None, settings_path=None):
        self.root = Path(root).resolve()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.store = JobStore(self.root)
        self.ledger = ledger
        self.generator = generator or self._generate
        self.settler = settler or self._settle
        self.learner = learner or self._learn
        self.config_path = config_path or self.root / "config/daily223-live.json"
        self.settings_path = settings_path or self.root / "config/arthur-settings.json"

    def _stamp(self):
        return aware(self.clock()).isoformat()

    def _save(self, job, *, event=True):
        job["updated_at"] = self._stamp()
        self.store.save(job, event=event)

    def _new_job(self, plan):
        from goal_analysis.team.manager import TeamManager
        from goal_analysis.team.routing import generation_plan

        settings = load_settings(Path(self.settings_path))
        config = validate_live_config(_read_json(Path(self.config_path)))
        # Enrollment precedes the first publication; otherwise the first day's
        # persona evidence would predate its own prospective evaluation epoch.
        team = TeamManager(self.root, clock=self.clock).enroll_missing()["snapshot"]
        stamp = self._stamp()
        return {
            "job_id": "nightly-" + plan["target_date"], "slot_date": plan["slot_date"],
            "target_date": plan["target_date"], "scheduled_at": plan["scheduled_at"],
            "deadline": plan["deadline"], "status": "PENDING", "created_at": stamp,
            "updated_at": stamp, "next_retry_at": None, "completed_at": None,
            "settings": settings, "config": config,
            "team_plan": generation_plan(settings["enabled_profiles"], team),
            "manifest_sha256": strategy_manifest(settings)["manifest_sha256"],
            "usage": {"generation_football_calls": 0, "settlement_football_calls": 0},
            "stages": [{"name": name, "status": "PENDING", "attempts": 0,
                        "max_attempts": MAX_ATTEMPTS, "last_started_at": None,
                        "last_finished_at": None, "next_retry_at": None,
                        "reason": None, "result": {}} for name in STAGES],
        }

    @staticmethod
    def _stage(job, name):
        return next(stage for stage in job["stages"] if stage["name"] == name)

    def _saved_report(self, run_id):
        with self.ledger._connection() as db:
            row = db.execute("SELECT snapshot_json,snapshot_hash FROM portfolio_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        report = json.loads(row[0])
        if _hash(report) != row[1]:
            raise ValueError("Stored report integrity failure")
        return report

    def run(self, *, scheduled=True, target_date: date | None = None):
        now = aware(self.clock())
        plan = schedule_plan(now)
        if scheduled and not plan["due"]:
            self._expire_jobs(now)
            return {"status": "NOT_DUE", **plan}
        if not scheduled:
            target = target_date or now.astimezone(BERLIN).date() + timedelta(days=1)
            if target != now.astimezone(BERLIN).date() + timedelta(days=1):
                raise ValueError("Manual nightly run must target tomorrow in Europe/Berlin")
            slot = target - timedelta(days=1)
            plan.update(target_date=target.isoformat(), slot_date=slot.isoformat(),
                        scheduled_at=datetime.combine(slot, time(22), BERLIN).isoformat(),
                        deadline=datetime.combine(target, time(6), BERLIN).isoformat())
        try:
            with RunLock(self.root / "data/arthur/run.lock"):
                self.ledger = self.ledger or PortfolioLedger(
                    self.root / "data/arthur/ledger.sqlite3", now=self.clock,
                    correction_window_hours=168,
                )
                job = self.store.get(plan["target_date"]) or self._new_job(plan)
                if job["status"] in {"COMPLETED", "COMPLETED_WITH_WARNINGS", "EXPIRED"}:
                    return {"status": "ALREADY_FINISHED", "job": public_job(job)}
                self._save(job)
                for name in STAGES:
                    self._execute_stage(job, name)
                    if name == 'archive':
                        # Publication has finished before this bounded local
                        # observer starts. It cannot hold up a pre-match freeze.
                        self._daily_native_shadow(job)
                self._summarize(job)
                self._save(job)
                return {"status": job["status"], "job": public_job(job)}
        except RuntimeError as error:
            if str(error) == "Már fut egy Arthur-feladat. Várd meg a befejezését.":
                return {"status": "BUSY", "reason": "EXISTING_ARTHUR_RUN"}
            raise

    def _expire_jobs(self, now):
        expired = [job for job in self.store.jobs() if job["status"] not in {
            "COMPLETED", "COMPLETED_WITH_WARNINGS", "EXPIRED"
        } and now >= datetime.fromisoformat(job["deadline"])]
        if not expired:
            return
        try:
            with RunLock(self.root / "data/arthur/run.lock"):
                for job in expired:
                    # Re-read after acquiring the cross-process lock.
                    job = self.store.get(job["target_date"])
                    if job["status"] in {"COMPLETED", "COMPLETED_WITH_WARNINGS", "EXPIRED"}:
                        continue
                    for stage in job["stages"]:
                        if stage["status"] not in TERMINAL:
                            stage.update(status="EXPIRED", reason="CATCHUP_WINDOW_EXPIRED",
                                         next_retry_at=None, last_finished_at=self._stamp())
                    job.update(status="EXPIRED", next_retry_at=None, completed_at=self._stamp())
                    self._save(job)
        except RuntimeError:
            return  # A running owner retains the state until its next checkpoint.

    def _execute_stage(self, job, name):
        stage = self._stage(job, name)
        if stage["status"] in TERMINAL:
            return
        now = aware(self.clock())
        if now >= datetime.fromisoformat(job["deadline"]):
            stage.update(status="EXPIRED", reason="CATCHUP_WINDOW_EXPIRED", last_finished_at=self._stamp())
            self._save(job)
            return
        if stage["next_retry_at"] and now < datetime.fromisoformat(stage["next_retry_at"]):
            return
        generation = self._stage(job, "generation")
        if name == "archive" and generation["status"] not in TERMINAL:
            return
        if name == "learning" and any(self._stage(job, n)["status"] not in TERMINAL for n in STAGES[:-1]):
            return
        recovering = stage["status"] == "RUNNING"
        checkpoint = None
        if name == "generation" and stage.get("run_id"):
            try:
                checkpoint = self._saved_report(stage["run_id"])
                staged = self.root / "data/arthur/nightly-staging" / f"{stage['run_id']}.json"
                if checkpoint is None and staged.exists():
                    checkpoint = _read_json(staged)["artifacts"]["report"]
            except (OSError, ValueError, KeyError, sqlite3.Error):
                # An unreadable checkpoint is not evidence that AI never ran.
                stage.update(status="BLOCKED", reason="CHECKPOINT_REQUIRES_REVIEW",
                             last_finished_at=self._stamp(), next_retry_at=None)
                self._save(job)
                return
        reuse_generation = checkpoint is not None and not (
            checkpoint.get("construction_status") == "DATA_BLOCKED" and checkpoint.get("reason")
        )
        if recovering and name == "generation" and checkpoint is None:
            stage.update(status="FAILED", reason="UNKNOWN_EXECUTION_REQUIRES_REVIEW",
                         last_finished_at=self._stamp())
            self._save(job)
            return
        if stage["attempts"] >= MAX_ATTEMPTS:
            stage.update(status="FAILED", reason="RETRY_EXHAUSTED", last_finished_at=self._stamp())
            self._save(job)
            return
        stage["attempts"] += 1
        stage.update(status="RUNNING", last_started_at=self._stamp(), next_retry_at=None)
        job["status"] = "RUNNING"
        if name == "generation" and not reuse_generation:
            stage["run_id"] = f"{job['job_id']}-a{stage['attempts']}"
        self._save(job)
        try:
            if name == "settlement":
                result = self.settler(job)
                stage["result"] = result
                if result.get("errors"):
                    raise WorkflowError("RESULT_REFRESH_FAILED")
                status = "PARTIAL" if result.get("remaining_fixtures", 0) else "SUCCESS"
            elif name == "generation":
                result, status = self._generation_stage(job, stage)
            elif name == "archive":
                result, status = self._archive(job)
            else:
                # Run advice only AFTER archival; its latency cannot invalidate
                # a pre-kickoff freeze. A separate durable claim prevents replay.
                from goal_analysis.jobs.nemotron import analyze

                issued = self._saved_report(generation.get("run_id")) if generation.get("run_id") else None
                from goal_analysis.jobs.grok_advisory import analyze as analyze_grok

                grok_advice = (issued.get("grok_review") or analyze_grok(
                    self.root, issued, now=aware(self.clock()), completion_clock=self.clock,
                )) if issued else {"status": "NO_ELIGIBLE_FIXTURES"}
                ticket_advice = analyze(self.root, issued) if issued else {"status": "SKIPPED_NO_DATA"}
                result = self.learner(aware(self.clock()))
                # Supervisor overlays are not part of LearningAgent's immutable
                # report. Match the standalone command's exact source identity.
                learning_input = {k: v for k, v in result.items()
                                  if k not in {"team", "team_error", "scouting"}}
                learning_advice = analyze(self.root, learning_input, scope="learning")
                llm = result.get("llm", {})
                status = "PARTIAL" if llm.get("status") not in {"COMPLETE", "SUCCESS", "COMPLETED", "NO_DATA", "SKIPPED_NO_DATA"} else "SUCCESS"
                if result.get("team_error") or result.get("scouting", {}).get("status") in {"UNAVAILABLE", "INTERRUPTED", "PARTIAL"}:
                    status = "PARTIAL"
                if any(advice.get("status") not in {"DISABLED", "COMPLETE", "SKIPPED_NO_DATA"}
                       for advice in (ticket_advice, learning_advice)):
                    status = "PARTIAL"
                result = {key: result.get(key) for key in ("report_id", "report_hash", "status", "as_of", "llm", "team", "team_error", "scouting")}
                result["nemotron"] = {"report": ticket_advice.get("status"),
                                      "learning": learning_advice.get("status")}
                result["grok"] = {"status": grok_advice.get("status"), "mode": grok_advice.get("mode", "SHADOW")}
                result["weather_history"] = self._weather_history(job)
                result["the_stats_history"] = self._the_stats_history(job)
            stage.update(status=status, result=result, reason="BACKLOG_OR_COVERAGE" if status == "PARTIAL" else None)
        except Exception as error:  # noqa: BLE001 - isolate stages, never publish provider/CLI raw errors
            reason, retryable = failure_reason(error)
            retry = retryable and stage["attempts"] < MAX_ATTEMPTS
            stage.update(status="RETRY_WAIT" if retry else "FAILED" if retryable else "BLOCKED",
                         reason=reason, next_retry_at=(now + timedelta(minutes=15 * stage["attempts"])).isoformat() if retry else None)
        stage["last_finished_at"] = self._stamp()
        self._save(job)

    def _generation_stage(self, job, stage):
        run_id = stage["run_id"]
        report = self._saved_report(run_id)
        bundle_path = self.root / "data/arthur/nightly-staging" / f"{run_id}.json"
        if report is None:
            if bundle_path.exists():
                bundle = _read_json(bundle_path)
            else:
                if strategy_manifest(job["settings"])["manifest_sha256"] != job["manifest_sha256"]:
                    raise WorkflowError("STRATEGY_CHANGED_DURING_RETRY", retryable=False)
                bundle = self.generator(date.fromisoformat(job["target_date"]), job["settings"], job)
                report = bundle["artifacts"]["report"]
                report["run_id"] = run_id
                report["automation"] = {"job_id": job["job_id"], "slot_date": job["slot_date"],
                                        "attempt": stage["attempts"], "scheduled_at": job["scheduled_at"]}
                from goal_analysis.engine.portfolio import PROFILES

                covered = {item["profile_id"] for item in report.get("recommendations", [])}
                for profile in job["settings"]["enabled_profiles"]:
                    if profile not in covered:
                        report.setdefault("recommendations", []).append({
                            "profile_id": profile, "profile_name": PROFILES[profile]["name"],
                            "status": "DATA_BLOCKED" if report.get("reason") else "NO_FIXTURES",
                            "execution_status": "NO_USABLE_INPUT", "legs": [],
                            "combined_price": None, "stake_eur": None, "playable": False,
                            "quality_warnings": ["A profilhoz nem volt feldolgozható bemenet; nem készült választás."],
                        })
                attach_strategy_provenance(report, job["settings"])
                from goal_analysis.team.routing import apply_publication_policy

                if job.get("team_plan"):
                    apply_publication_policy(report, job["team_plan"])
                from goal_analysis.jobs.ticket_evidence import (
                    build_ticket_evidence,
                    validate_ticket_evidence,
                )

                # The generator's audit predates final nightly IDs, provenance
                # and publication metadata. Seal only this new, unpersisted run.
                # Existing checkpoints and immutable ledger rows are never resealed.
                unavailable_evidence = {"mode": "AUDIT_ONLY", "status": "UNAVAILABLE",
                    "reason": "FINAL_TICKET_EVIDENCE_FAILED", "decision_effect": "NONE"}
                try:
                    evidence = build_ticket_evidence(report, bundle["artifacts"])
                    validate_ticket_evidence(evidence, report, bundle["artifacts"])
                    report["ticket_evidence"] = evidence
                except Exception:  # noqa: BLE001 - an optional audit cannot veto stable selections
                    report["ticket_evidence"] = dict(unavailable_evidence)
                report.pop("report_sha256", None)
                report["report_sha256"] = canonical_sha256(report)
                try:
                    bundle_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = bundle_path.with_suffix(".tmp")
                    temporary.write_text(json.dumps(bundle, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                    written = _read_json(temporary)
                    if report["ticket_evidence"].get("status") != "UNAVAILABLE":
                        try:
                            validate_ticket_evidence(written["artifacts"]["report"]["ticket_evidence"],
                                written["artifacts"]["report"], written["artifacts"])
                        except Exception:  # noqa: BLE001 - no positive audit claim after a failed disk rebind
                            report["ticket_evidence"] = dict(unavailable_evidence)
                            report.pop("report_sha256", None)
                            report["report_sha256"] = canonical_sha256(report)
                            temporary.write_text(json.dumps(bundle, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                            written = _read_json(temporary)
                    if canonical_sha256(written["artifacts"]["report"]) != canonical_sha256(report):
                        raise ValueError("FINAL_GENERATION_SERIALIZATION_MISMATCH")
                    temporary.replace(bundle_path)
                except (OSError, ValueError, TypeError) as error:
                    # No completed durable checkpoint: do not spend another model
                    # request after a disk/serialization failure of a computed run.
                    raise WorkflowError("UNKNOWN_EXECUTION_REQUIRES_REVIEW", retryable=False) from error
            report = bundle["artifacts"]["report"]
            self.ledger.save_run(report)
        # The immutable ledger is canonical. A crash before file export is resumable.
        directory = self.root / "reports/arthur" / run_id
        if not directory.exists():
            bundle = _read_json(bundle_path) if bundle_path.exists() else {"artifacts": {"report": report}}
            temporary_dir = directory.with_name(f".{run_id}-writing-{uuid4().hex[:12]}")
            write_portfolio_bundle(temporary_dir, bundle)
            temporary_dir.rename(directory)
        else:
            if _read_json(directory / "report.json") != report:
                raise WorkflowError("ARCHIVE_HASH_MISMATCH", retryable=False)
        if report.get("construction_status") == "DATA_BLOCKED" and report.get("reason"):
            reason, retry = failure_reason(RuntimeError(report["reason"]))
            raise WorkflowError(reason, retryable=retry)
        recommendations = report.get("recommendations", [])
        covered = {r["profile_id"] for r in recommendations}
        if set(job["settings"]["enabled_profiles"]) - covered:
            raise WorkflowError("INCOMPLETE_PROFILE_RUN", retryable=False)
        participating = set(job["settings"]["enabled_profiles"]) - set(
            job.get("team_plan", {}).get("blocked_profiles", [])
        )
        count = sum(bool(r.get("legs")) for r in recommendations if r["profile_id"] in participating)
        result = {"run_id": run_id, "recommendations": count, "profile_count": len(participating),
                  "suspended_profiles": sorted(covered - participating),
                  "report_sha256": report.get("report_sha256"),
                  "astra_status": report.get("astra", {}).get("status")}
        from goal_analysis.config.portfolio import MODEL_PROFILE_IDS
        from goal_analysis.tipsters.catalog import TIPSTER_PROFILE_IDS

        needs_astra = any(p not in (*MODEL_PROFILE_IDS, *TIPSTER_PROFILE_IDS) for p in participating)
        status = "SUCCESS" if count == len(participating) and (
            not needs_astra or result["astra_status"] == "COMPLETE"
        ) else "PARTIAL"
        return result, status

    def _archive(self, job):
        generation = self._stage(job, "generation")
        run_id = generation.get("run_id")
        report = self._saved_report(run_id) if run_id else None
        if report and 'canonical_contexts' not in job:
            from goal_analysis.jobs.fixture_contexts import shadow_archive

            context_proof = shadow_archive(self.root, report, clock=self.clock)
            if context_proof['status'] != 'DISABLED':
                job['canonical_contexts'] = context_proof
                self._save(job, event=False)
        if not report or not any(r.get("legs") for r in report.get("recommendations", [])):
            return {"run_id": run_id, "reason": "NO_RECOMMENDATIONS", "immutable_report_saved": bool(report)}, "PARTIAL"
        existing = self.ledger.list_freezes(date=job["target_date"])
        if existing:
            same = existing[0]["run_id"] == run_id
            return {"run_id": run_id, "frozen_run_id": existing[0]["run_id"],
                    "reason": "ALREADY_FROZEN" if same else "EXISTING_FREEZE_PRESERVED"}, "SUCCESS" if same else "PARTIAL"
        if any(datetime.fromisoformat(leg["kickoff"]) <= aware(self.clock())
               for r in report.get("recommendations", []) for leg in r.get("legs", [])):
            return {"run_id": run_id, "reason": "KICKOFF_PASSED", "immutable_report_saved": True}, "PARTIAL"
        return self.ledger.freeze_run(run_id), "SUCCESS"

    def _summarize(self, job):
        pending = [s for s in job["stages"] if s["status"] not in TERMINAL]
        times = [s["next_retry_at"] for s in pending if s.get("next_retry_at")]
        job["next_retry_at"] = min(times) if times else None
        if pending:
            job["status"] = "RETRY_WAIT"
        else:
            job["status"] = "COMPLETED" if all(s["status"] == "SUCCESS" for s in job["stages"]) else "COMPLETED_WITH_WARNINGS"
            job["completed_at"] = self._stamp()

    def _weather_history(self, job):
        """Spend at most two free weather requests after daily publication.

        The durable pre-call claim also bounds uncertain/crashed executions.
        History cannot delay or change the already completed ticket freeze.
        """
        if "weather_history" in job:
            return job["weather_history"]
        if self._stage(job, "archive")["status"] != "SUCCESS":
            return {"status": "SKIPPED_DAILY_INCOMPLETE"}
        if datetime.fromisoformat(job["deadline"]) - aware(self.clock()) < timedelta(seconds=45):
            return {"status": "SKIPPED_NO_SPARE_WINDOW"}
        job["weather_history"] = {"status": "STARTED", "max_requests": 2,
                                  "attempted_at": self._stamp()}
        self._save(job)
        try:
            from goal_analysis.jobs.weather_history import backfill_weather

            job["weather_history"] = backfill_weather(self.root, max_requests=2, clock=self.clock)
        except Exception:  # noqa: BLE001 - optional history cannot fail daily publication
            job["weather_history"] = {"status": "FAILED", "error_code": "WEATHER_HISTORY_UNAVAILABLE"}
        self._save(job)
        return job["weather_history"]

    def _daily_native_shadow(self, job):
        """Durably at most once per run; no provider or LLM request."""
        if 'native_shadow' in job:
            return job['native_shadow']
        try:
            from goal_analysis.jobs import daily_native_shadow as shadow

            if not shadow.enabled(self.root):
                return {'status': 'DISABLED', 'mode': 'SHADOW'}
            if self._stage(job, 'archive')['status'] != 'SUCCESS':
                return {'status': 'SKIPPED_DAILY_INCOMPLETE', 'mode': 'SHADOW'}
            remaining = (datetime.fromisoformat(job['deadline'])-aware(self.clock())).total_seconds()
            if remaining < 45:
                return {'status': 'SKIPPED_NO_SPARE_WINDOW', 'mode': 'SHADOW'}
            run_id = self._stage(job, 'generation').get('run_id')
            report = self._saved_report(run_id) if run_id else None
            if report is None:
                return {'status': 'SKIPPED_NO_SAVED_REPORT', 'mode': 'SHADOW'}
            budget = min(shadow.MAX_SECONDS, int(remaining)-15)
            job['native_shadow'] = {'status': 'STARTED', 'mode': 'SHADOW',
                'run_id': run_id, 'attempted_at': self._stamp(), 'max_seconds': budget,
                'external_requests': 0, 'llm_requests': 0, 'live_decision_effect': False}
            self._save(job)  # A crash/timeout cannot silently run the fit again.
            job['native_shadow'] = shadow.run(self.root, run_id, canonical_sha256(report),
                job['settings'], as_of=aware(self.clock()), max_seconds=budget, prepare_sources=True)
            self._save(job)
        except Exception:  # noqa: BLE001 - never veto a freeze, expose secrets or repeat uncertain work.
            job['native_shadow'] = {'status': 'UNAVAILABLE', 'mode': 'SHADOW',
                'reason': 'DAILY_NATIVE_SHADOW_FAILED', 'external_requests': 0,
                'llm_requests': 0, 'live_decision_effect': False}
            try:
                self._save(job)
            except Exception:  # noqa: BLE001 - disk failure must not undo an already completed archive.
                job['native_shadow']['persistence_status'] = 'UNAVAILABLE'
        return job['native_shadow']

    def _the_stats_history(self, job):
        """Continue an explicit history plan only after the daily freeze.

        This optional work cannot hold up publication: archive is already
        successful before it can start. A pre-call durable claim prevents a
        crashed/retried nightly job from spending a second batch that day.
        """
        from goal_analysis.jobs.background_history import managed

        if managed(self.root):
            return {'status':'MANAGED_BY_BACKGROUND_TICK','requests':0}
        if "the_stats_history" in job:
            return job["the_stats_history"]
        if self._stage(job, "archive")["status"] != "SUCCESS":
            return {"status": "SKIPPED_DAILY_INCOMPLETE"}
        path = self.root / "data/arthur/the-stats-history/active-plan.json"
        if not path.is_file():
            return {"status": "SKIPPED_NO_ACTIVE_PLAN"}
        now = aware(self.clock())
        deadline = aware(datetime.fromisoformat(job["deadline"]))
        if deadline - now < timedelta(seconds=45):
            return {"status": "SKIPPED_NO_SPARE_WINDOW"}
        # The importer checks again before every request. Leave the shared
        # workflow fifteen seconds for final status/SQLite bookkeeping.
        stop_at = min(deadline - timedelta(seconds=15),
                      now + timedelta(seconds=THE_STATS_HISTORY_WINDOW_SECONDS))
        job["the_stats_history"] = {
            "status": "STARTED", "max_requests": THE_STATS_HISTORY_BUDGET,
            "attempted_at": now.isoformat(), "stop_at": stop_at.isoformat(),
        }
        self._save(job)
        try:
            from goal_analysis.jobs.the_stats_history import load_plan, run_history

            plan = load_plan(path)
            job["the_stats_history"] = run_history(
                self.root, plan, execute=True, daily_work_complete=True,
                max_requests=THE_STATS_HISTORY_BUDGET, monthly_reserve=100,
                clock=self.clock, stop_at=stop_at, unified_results=True,
            )
        except Exception:  # noqa: BLE001 - history cannot fail published daily work or expose keys
            job["the_stats_history"] = {
                "status": "FAILED", "error_code": "THE_STATS_HISTORY_UNAVAILABLE",
            }
        self._save(job)
        return job["the_stats_history"]

    def _football(self, job, stage):
        from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient

        if not os.environ.get("THE_STATS_API_KEY", "").strip():
            raise WorkflowError("AUTH_REQUIRED", retryable=False)
        budget = SETTLEMENT_BUDGET if stage == "settlement" else job['config']['max_odds_credits'] if stage == 'odds' else job["config"]["max_football_calls"]
        counter = stage + "_football_calls"
        workflow = self
        client = TheStatsPrimaryClient(self.root, max_requests=budget, clock=self.clock)

        class BudgetedFootball:
            blocked = None
            provider = 'the_stats_api'
            root = workflow.root
            _clock = staticmethod(workflow.clock)

            @property
            def last_usage(self):
                return client.last_usage

            @property
            def last_response(self):
                return client.last_response

            @property
            def last_raw_response(self):
                return client.last_raw_response

            def request(self, endpoint, params):
                if self.blocked:
                    raise self.blocked
                if job["usage"].get(counter, 0) >= budget:
                    self.blocked = WorkflowError("DAILY_REQUEST_BUDGET", retryable=False)
                    raise self.blocked
                job["usage"][counter] = job["usage"].get(counter, 0) + 1
                workflow._save(job, event=False)  # Reserve before the external call, including crashes.
                try:
                    return client.request(endpoint, params)
                except Exception as error:
                    reason, retryable = failure_reason(error)
                    if not retryable:
                        self.blocked = WorkflowError(reason, retryable=False)
                    raise

        return BudgetedFootball()

    def _settle(self, job):
        remaining = SETTLEMENT_BUDGET - job["usage"].get("settlement_football_calls", 0)
        if remaining <= 0:
            raise WorkflowError("DAILY_REQUEST_BUDGET", retryable=False)
        self.ledger.refresh_call_limit = min(remaining, SETTLEMENT_BUDGET)
        client = self._football(job, "settlement")
        result = self.ledger.refresh_results(client)
        self._stage(job, "settlement")["result"] = result
        if client.blocked:
            raise client.blocked
        return result

    def _generate(self, target, settings, job):
        from goal_analysis.agents.codex_review import CodexReviewer
        from goal_analysis.jobs.expert_advisory import refresh_news, review_experts
        from goal_analysis.jobs.grok_gate import review_and_repair
        from goal_analysis.jobs.nemotron_tickets import curate as curate_nemotron_tickets
        from goal_analysis.jobs.sports_context_archive import make_weather_provider
        from goal_analysis.providers.the_stats_odds import TheStatsOddsFeed
        from goal_analysis.storage import CacheStore, Database

        config = deepcopy(job["config"])
        used = job["usage"].get("generation_football_calls", 0)
        if used >= config["max_football_calls"]:
            raise WorkflowError("DAILY_REQUEST_BUDGET", retryable=False)
        db = Database(self.root / "data/goal_analysis.sqlite3")
        db.initialize()
        odds = TheStatsOddsFeed(self.root, config['odds_region'], config['max_odds_credits'],
                               client=self._football(job, 'odds'), clock=self.clock)
        ai = settings["openai"]
        reviewer = CodexReviewer(model=ai["model"], reasoning_effort=ai["reasoning_effort"],
                                 web_search=ai["web_search"], max_output_tokens=ai["max_output_tokens"],
                                 cache_dir=self.root / "data/arthur/codex-cache")
        football, cache = self._football(job, 'generation'), CacheStore(db)
        weather = make_weather_provider(self.root, self._stage(job, 'generation').get('run_id')
            or f'{target}-{self.clock().isoformat()}', clock=self.clock)
        return run_portfolio(config, settings, football, odds,
                             cache, target, reviewer, self.ledger, clock=self.clock,
                             weather_provider=weather,
                             expert_reviewer=lambda report, options: review_experts(
                                 self.root, report, options, weather_provider=weather, clock=self.clock,
                                 standings_client=football, standings_cache=cache,
                                 max_standings_requests=min(8, max(0, config['max_football_calls']
                                     - job['usage'].get('generation_football_calls', 0)))),
                             model_artifact_dir=self.root / "data/arthur/models",
                             team_plan=job.get("team_plan"),
                             nemotron_stylist=lambda options, cards, day, now: curate_nemotron_tickets(
                                 self.root, options, cards, day, now),
                             package_reviewer=lambda report, options: refresh_news(self.root,
                                 review_and_repair(self.root, report, options, clock=self.clock),
                                 clock=self.clock))

    def _learn(self, as_of):
        from goal_analysis.learning import LearningAgent
        from goal_analysis.team.manager import TeamManager
        from goal_analysis.team.scouting import Scout

        report = deepcopy(LearningAgent(self.root, clock=self.clock).run(as_of))
        evaluation = TeamManager(self.root, clock=self.clock).evaluate(
            as_of, learning_report=report, settings=load_settings(Path(self.settings_path)),
        )
        report["team"] = {key: evaluation.get(key) for key in (
            "evaluation_id", "date", "status", "as_of", "snapshot_hash", "policy_version",
        )}
        scouting = Scout(self.root, clock=self.clock).run(as_of)
        report["scouting"] = {key: scouting.get(key) for key in (
            "scout_id", "status", "period_start", "as_of", "request_count",
        )}
        return report
