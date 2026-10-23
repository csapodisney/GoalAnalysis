"""Local, append-only supervision of recommendation profiles, never wagers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from goal_analysis.config.portfolio import (
    DEFAULT_PROBATION_PROFILE_IDS,
    load_settings,
    validate_settings,
)
from goal_analysis.learning._findings import findings_and_proposals
from goal_analysis.learning._metrics import digest, measure
from goal_analysis.storage.portfolio_ledger import _stamp
from goal_analysis.strategy_provenance import strategy_manifest

from .metrics import BERLIN, POLICY, PROFILES, REASONS, assess, build_cohorts

STATES = {"ACTIVE", "PROBATION", "SUSPENDED"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


class TeamManager:
    def __init__(self, root, clock=None):
        self.root = Path(root).resolve()
        self.path = self.root / "data/team/team.sqlite3"
        self.ledger_path = self.root / "data/arthur/ledger.sqlite3"
        self.clock = clock or (lambda: datetime.now(UTC))

    @contextmanager
    def _connection(self, *, write=False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=30)
        else:
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=15)
            db.execute("PRAGMA query_only=ON")
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS team_enrollment (
                        profile_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS team_evaluations (
                        day TEXT PRIMARY KEY, evaluation_id TEXT UNIQUE NOT NULL,
                        payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS team_events (
                        id INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL,
                        payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL);
                """)
                for table in ("team_enrollment", "team_evaluations", "team_events"):
                    for action in ("UPDATE", "DELETE"):
                        db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                            BEFORE {action} ON {table} BEGIN
                            SELECT RAISE(ABORT,'team records are immutable'); END""")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _verify(row):
        payload = json.loads(row["payload_json"])
        if digest(payload) != row["payload_hash"]:
            raise ValueError("A csapatnapló ellenőrző összege hibás; automatikus döntés leállítva.")
        if "snapshot_hash" in payload:
            base = {k: v for k, v in payload.items() if k != "snapshot_hash"}
            if digest(base) != payload["snapshot_hash"]:
                raise ValueError("A csapatértékelés ellenőrző összege hibás.")
        return payload

    @staticmethod
    def _initial(profile, started_at=None):
        status = "PROBATION" if profile in DEFAULT_PROBATION_PROFILE_IDS else "ACTIVE"
        return {"profile_id": profile, "status": status, "started_at": started_at,
                "status_since": started_at, "evidence_since": started_at,
                "eligible_for_live": status == "ACTIVE", "decision": "KEEP",
                "reason_code": "INITIAL_MEMBER", "reason": REASONS["INITIAL_MEMBER"],
                "evidence_ids": [], "training_plan": [], "awards": [], "podium": None}

    def _enroll(self, db, now, profiles=PROFILES):
        for profile in profiles:
            member = self._initial(profile, now.isoformat())
            db.execute("INSERT OR IGNORE INTO team_enrollment VALUES (?,?,?)",
                       (profile, _json(member), digest(member)))

    def enroll_missing(self):
        """Explicit run-start enrollment, never a constructor or GET side effect.

        The caller holds the shared application RunLock. This creates only
        missing members, at the actual enrollment time, before any new picks.
        Existing daily evaluations and their status events remain immutable.
        """
        now = _stamp(self.clock())
        with self._connection(write=True) as db:
            self._state(db, now)  # Fail closed on corrupted existing hashes/chains.
            existing = {row[0] for row in db.execute("SELECT profile_id FROM team_enrollment")}
            missing = [profile for profile in PROFILES if profile not in existing]
            self._enroll(db, now, profiles=missing)
        return {"enrolled_profiles": missing, "enrolled_count": len(missing),
                "as_of": now.isoformat(), "snapshot": self.snapshot()}

    def _state(self, db, cutoff):
        members = {p: self._initial(p) for p in PROFILES}
        for row in db.execute("SELECT * FROM team_enrollment"):
            member = self._verify(row)
            if member["profile_id"] in members and _stamp(member["started_at"]) <= cutoff:
                members[member["profile_id"]] = member
        events = []
        for row in db.execute("SELECT * FROM team_events ORDER BY id"):
            event = self._verify(row)
            if _stamp(event["effective_at"]) > cutoff:
                continue
            profile = event["profile_id"]
            if profile not in members or event["to_status"] not in STATES:
                raise ValueError("Ismeretlen csapattag vagy érvénytelen naplózott státusz.")
            member = members[profile]
            if member["status"] != event["from_status"]:
                raise ValueError("A csapatesemények státuszlánca nem egyezik.")
            member.update(status=event["to_status"], status_since=event["effective_at"],
                          eligible_for_live=event["to_status"] == "ACTIVE",
                          reason_code=event["reason_code"], reason=event["reason"],
                          decision="KEEP", last_event_id=event["event_id"])
            if event["source"] == "MANUAL":
                member["evidence_since"] = event["effective_at"]
            events.append(event)
        return list(members.values()), events

    @staticmethod
    def _event(profile, old, new, now, source, reason_code, reason, evaluation_id=None, ordinal=0):
        event = {"profile_id": profile, "from_status": old, "to_status": new,
                 "effective_at": now.isoformat(), "source": source, "reason_code": reason_code,
                 "reason": reason, "evaluation_id": evaluation_id, "ordinal": ordinal,
                 "automatic_wager_changes": False}
        event["event_id"] = "team-event-" + digest(event)[:24]
        return event

    @staticmethod
    def _save_event(db, event):
        db.execute("INSERT INTO team_events(event_id,payload_json,payload_hash) VALUES (?,?,?)",
                   (event["event_id"], _json(event), digest(event)))

    def _training(self, report, measured, cutoff):
        """Only attach canonical proposals whose evidence re-computes from the ledger."""
        if not isinstance(report, dict):
            return None, {}, []
        try:
            base = {k: v for k, v in report.items() if k != "report_hash"}
            when = _stamp(report["as_of"])
            if digest(base) != report.get("report_hash") or when > cutoff:
                return None, {}, []
            verified = measured if when == cutoff else measure(self.ledger_path, when)
            evidence = {e["evidence_id"]: e for e in verified.get("evidence", [])}
            supplied = {e["evidence_id"]: e for e in report.get("evidence", [])}
            _, proposals = findings_and_proposals(list(evidence.values()))
            allowed = {p["recommendation_id"]: p for p in proposals}
            plans, selected = {}, {}
            for raw in report.get("recommendations", []):
                original = allowed.get(raw.get("recommendation_id"))
                if (not original or any(raw.get(k) != original[k] for k in
                        ("action", "title", "detail", "status", "evidence_ids"))
                        or raw.get("priority") not in {"LOW", "MEDIUM", "HIGH"}
                        or raw.get("source") not in {"deterministic", "codex"}):
                    continue
                refs = [evidence.get(identifier) for identifier in original["evidence_ids"]]
                if not refs or any(e is None or e != supplied.get(e["evidence_id"]) for e in refs):
                    continue
                targets = set()
                for item in refs:
                    if item["kind"] == "PROFILE" and str(item["label"]).lower() in PROFILES:
                        targets.add(str(item["label"]).lower())
                    elif item["kind"] in {"CALIBRATION", "DATA_QUALITY"}:
                        targets.update(PROFILES)
                if not targets:
                    continue
                for item in refs:
                    selected[item["evidence_id"]] = deepcopy(item)
                for profile in sorted(targets):
                    plans.setdefault(profile, []).append({
                        **deepcopy(original), "code": original["action"],
                        "priority": raw["priority"], "source": raw["source"],
                        "plan_scope": "ALL_TEAM" if len(targets) == len(PROFILES) else "PROFILE",
                        "learning_report_id": report.get("report_id"),
                        "execution": "REVIEW_PLAN_ONLY_NO_MODEL_WEIGHT_CHANGE",
                    })
            return ({"report_id": report.get("report_id"), "report_hash": report["report_hash"]},
                    plans, [selected[k] for k in sorted(selected)])
        except (KeyError, TypeError, AttributeError, ValueError):
            return None, {}, []

    def evaluate(self, as_of, learning_report=None, *, settings=None):
        now, cutoff = _stamp(self.clock()), _stamp(as_of)
        if cutoff > now or cutoff.astimezone(BERLIN).date() != now.astimezone(BERLIN).date():
            raise ValueError("Éles csapatértékelés csak a mai, nem jövőbeli időhatárral indítható.")
        day = cutoff.astimezone(BERLIN).date().isoformat()
        with self._connection(write=True) as db:
            existing = db.execute("SELECT * FROM team_evaluations WHERE day=?", (day,)).fetchone()
            if existing:
                return self._verify(existing)
            self._enroll(db, now)
            members, previous_events = self._state(db, now)
            measured = measure(self.ledger_path, cutoff, include_records=True)
            effective_settings = validate_settings(settings) if settings is not None else load_settings(
                self.root / "config/arthur-settings.json")
            current_manifest = strategy_manifest(effective_settings)
            cohorts, evidence = build_cohorts(measured, members, cutoff, expected_manifest=current_manifest)
            decisions = assess(cohorts, members, cutoff)
            learning_reference, plans, learning_evidence = self._training(learning_report, measured, cutoff)
            evidence.extend(learning_evidence)
            for member in decisions:
                member["training_plan"].extend(plans.get(member["profile_id"], []))
            identity = digest([POLICY, day, cutoff.isoformat(), members, evidence])
            evaluation_id = f"team-{day}-{identity[:16]}"
            events = []
            for member in decisions:
                if member["status"] == member["previous_status"]:
                    continue
                event = self._event(member["profile_id"], member["previous_status"], member["status"],
                                    now, "AUTOMATIC", member["reason_code"], member["reason"],
                                    evaluation_id, len(previous_events) + len(events))
                self._save_event(db, event)
                events.append(event)
                member["status_since"] = now.isoformat()
                member["last_event_id"] = event["event_id"]
            result = {
                "evaluation_id": evaluation_id, "date": day, "as_of": cutoff.isoformat(),
                "created_at": now.isoformat(), "status": "COMPLETE" if any(
                    m["metrics"]["total_tickets"] for m in decisions) else "NO_DATA",
                "policy_version": POLICY["version"], "policy": deepcopy(POLICY),
                "snapshot_policy": "FIRST_IMMUTABLE_EVALUATION_PER_BERLIN_DAY",
                "members": decisions, "events": events, "evidence": evidence,
                "learning_reference": learning_reference, "automatic_wager_changes": False,
                "coverage": measured["coverage"],
                "current_manifest_sha256": current_manifest["manifest_sha256"],
            }
            result["snapshot_hash"] = digest(result)
            db.execute("INSERT INTO team_evaluations VALUES (?,?,?,?)",
                       (day, evaluation_id, _json(result), digest(result)))
        return result

    def snapshot(self):
        """A read-only view: neither constructor nor this method creates a DB."""
        now, latest, events = _stamp(self.clock()), None, []
        members = [self._initial(profile) for profile in PROFILES]
        if self.path.is_file():
            with self._connection() as db:
                members, events = self._state(db, now)
                row = db.execute("SELECT * FROM team_evaluations ORDER BY day DESC LIMIT 1").fetchone()
                if row:
                    latest = self._verify(row)
        empty, _ = build_cohorts({"records": {}}, members, now)
        old = {m["profile_id"]: m for m in (latest or {}).get("members", [])}
        for member in members:
            profile = member["profile_id"]
            measured = old.get(profile, {})
            for key in ("metrics", "qualification", "evidence_ids", "training_plan", "awards", "podium"):
                member[key] = deepcopy(measured.get(key, empty[profile].get(key, member.get(key))))
            if measured and member.get("last_event_id") == measured.get("last_event_id"):
                member.update(decision=measured["decision"], reason=measured["reason"],
                              reason_code=measured["reason_code"])
            elif measured:
                member["qualification"] = {"eligible": False, "sample_complete": False,
                                            "blockers": ["DECISION_COOLDOWN"]}
                member["awards"], member["podium"] = [], None
            member["metrics_current_epoch"] = bool(measured and measured.get("evidence_since")
                                                   == member.get("evidence_since"))
        enrolled = [m["started_at"] for m in members if m["started_at"]]
        return {"name": "A Szent Grál", "mode": "LIVE_PROFILE_SUPERVISION",
                "policy": deepcopy(POLICY), "policy_version": POLICY["version"],
                "started_at": min(enrolled) if enrolled else None,
                "members": members, "events": events, "latest_evaluation": latest,
                "automatic_wager_changes": False}

    def effective_profiles(self, requested):
        active = {m["profile_id"] for m in self.snapshot()["members"] if m["status"] == "ACTIVE"}
        return list(dict.fromkeys(p for p in requested if p in active))

    def set_status(self, profile_id, status, reason, as_of=None):
        if not isinstance(profile_id, str) or not isinstance(status, str) or profile_id not in PROFILES or status not in STATES:
            raise ValueError("Csak ellenőrzött csapattag és érvényes státusz választható.")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError("A kézi döntéshez 1–500 karakteres indoklás szükséges.")
        reason = " ".join(reason.split())
        now = _stamp(self.clock())
        if as_of is not None and _stamp(as_of) != now:
            raise ValueError("Kézi státuszváltás nem dátumozható vissza vagy a jövőbe.")
        with self._connection(write=True) as db:
            # Explicit owner action enrolls only this member; other new members
            # begin their own prospective epoch at the next daily evaluation.
            self._enroll(db, now, profiles=(profile_id,))
            members, events = self._state(db, now)
            current = next(m for m in members if m["profile_id"] == profile_id)
            event = self._event(profile_id, current["status"], status, now, "MANUAL",
                                "MANUAL_OVERRIDE", reason, ordinal=len(events))
            self._save_event(db, event)
        return self.snapshot()
