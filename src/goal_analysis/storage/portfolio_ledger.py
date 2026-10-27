"""Persistent, append-only ticket evidence and explicit manual wager accounting.

The mutable ``active`` index is a presentation index only: original run and ticket
JSON, wagers, provider observations and settlement revisions are never replaced.
Research tickets are never treated as bets placed with a bookmaker.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from datetime import date as Date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .monetary_projection import project_recorded_cohort, project_recorded_wager
from .recommendation_ledger import RecommendationLedger

BERLIN = ZoneInfo("Europe/Berlin")
TERMINAL = {"WON", "LOST", "VOID"}
FINAL_MATCH = {"FT", "AET", "PEN"}
FIRST_HALF_COMPLETE = FINAL_MATCH | {"HT", "2H", "ET", "BT", "P"}
MANUAL_REVIEW_MATCH = {"PST", "SUSP", "CANC", "ABD", "AWD", "WO", "INT"}


def _json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _stamp(value: str | datetime) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timestamp must include a time zone")
    return result.astimezone(UTC)


def _amount(value: Any, name: str, *, minimum: Decimal = Decimal(0)) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError(f"{name} must be a finite number greater than {minimum}")  # noqa: TRY004 - API validation contract.
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Invalid {name}") from error
    if not number.is_finite() or number <= minimum:
        raise ValueError(f"{name} must be a finite number greater than {minimum}")
    return number


def _money(value: Decimal | float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _day(value: str | Date | None) -> str | None:
    return Date.fromisoformat(str(value)).isoformat() if value is not None else None


def _bookmaker_return_provenance(db, ticket_id, settlement_hash):
    legacy = {"payout_basis": "LEGACY_REPORTED_RETURN_BASIS_UNSPECIFIED", "fee_tax_inclusion": "UNKNOWN", "provenance_status": "LEGACY_UNSPECIFIED"}
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='portfolio_bookmaker_return_provenance'").fetchone()
    if not exists:
        return legacy
    columns = db.execute("PRAGMA table_info(portfolio_bookmaker_return_provenance)").fetchall()
    expected = {"ticket_id","settlement_hash","payout_basis","fee_tax_inclusion","confirmed_at","contract_version"}
    if ({row["name"] for row in columns} != expected
            or {row["name"]:row["pk"] for row in columns if row["pk"]} != {"ticket_id":1,"settlement_hash":2}
            or any(str(row["type"]).upper() != "TEXT" or row["notnull"] != 1 for row in columns)):
        raise ValueError("INVALID_BOOKMAKER_RETURN_PROVENANCE_SCHEMA")
    row = db.execute("SELECT * FROM portfolio_bookmaker_return_provenance WHERE ticket_id=? AND settlement_hash=?", (ticket_id,settlement_hash)).fetchone()
    if row is None:
        return legacy
    if row["payout_basis"] != "BOOKMAKER_CONFIRMED_GROSS_SPORTS_RETURN" or row["fee_tax_inclusion"] != "EXCLUDED" or row["contract_version"] != "bookmaker-gross-return-v1":
        raise ValueError("INVALID_BOOKMAKER_RETURN_PROVENANCE_RECORD")
    _stamp(row["confirmed_at"])
    return {"payout_basis":row["payout_basis"], "fee_tax_inclusion":row["fee_tax_inclusion"], "provenance_confirmed_at":row["confirmed_at"], "provenance_status":"EXPLICIT_REVISION_BOUND", "contract_version":row["contract_version"]}


def _ensure_bookmaker_return_provenance(db):
    db.execute("CREATE TABLE IF NOT EXISTS portfolio_bookmaker_return_provenance (ticket_id TEXT NOT NULL,settlement_hash TEXT NOT NULL,payout_basis TEXT NOT NULL,fee_tax_inclusion TEXT NOT NULL,confirmed_at TEXT NOT NULL,contract_version TEXT NOT NULL,PRIMARY KEY(ticket_id,settlement_hash),FOREIGN KEY(ticket_id,settlement_hash) REFERENCES portfolio_bookmaker_returns(ticket_id,settlement_hash))")
    # Validate any preexisting table rather than treating corruption as no data.
    _bookmaker_return_provenance(db, "", "")
    for operation in ("UPDATE", "DELETE"):
        db.execute("CREATE TRIGGER IF NOT EXISTS bookmaker_return_provenance_no_" + operation.lower() + " BEFORE " + operation + " ON portfolio_bookmaker_return_provenance BEGIN SELECT RAISE(ABORT,'Bookmaker return provenance is immutable'); END")


class PortfolioLedger(RecommendationLedger):
    """A local ledger, with one SQLite connection/transaction per operation."""

    def __init__(
        self,
        path: str | Path,
        *,
        now: Callable[[], datetime] | None = None,
        max_daily_tickets: int = 5,
        refresh_call_limit: int = 20,
        correction_window_hours: int = 48,
    ) -> None:
        if type(max_daily_tickets) is not int or not 1 <= max_daily_tickets <= 5:
            raise ValueError("Daily ticket cap must be between 1 and 5")
        if type(refresh_call_limit) is not int or not 1 <= refresh_call_limit <= 100:
            raise ValueError("Result refresh call limit must be between 1 and 100")
        if type(correction_window_hours) is not int or not 48 <= correction_window_hours <= 720:
            raise ValueError("Correction window must be between 48 and 720 hours")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.now = now or (lambda: datetime.now(UTC))
        self.max_daily_tickets = max_daily_tickets
        self.refresh_call_limit = refresh_call_limit
        self.correction_window_hours = correction_window_hours
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS portfolio_runs (
                    run_id TEXT PRIMARY KEY, date TEXT NOT NULL, observed_at TEXT NOT NULL,
                    saved_at TEXT NOT NULL, snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS portfolio_tickets (
                    ticket_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES portfolio_runs,
                    date TEXT NOT NULL, profile_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS portfolio_tickets_day ON portfolio_tickets(date, active);
                CREATE TABLE IF NOT EXISTS portfolio_events (
                    id INTEGER PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES portfolio_tickets,
                    event_type TEXT NOT NULL, observed_at TEXT NOT NULL, payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS portfolio_wagers (
                    ticket_id TEXT PRIMARY KEY REFERENCES portfolio_tickets,
                    stake_eur TEXT NOT NULL, actual_combined_price TEXT NOT NULL,
                    recorded_at TEXT NOT NULL, price_source TEXT NOT NULL DEFAULT 'RESEARCH_QUOTE_ESTIMATE');
                CREATE TABLE IF NOT EXISTS portfolio_results (
                    id INTEGER PRIMARY KEY, fixture_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL, payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
                    UNIQUE(fixture_id, payload_hash));
                CREATE TABLE IF NOT EXISTS portfolio_refreshes (
                    id INTEGER PRIMARY KEY, fixture_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS portfolio_settlements (
                    id INTEGER PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES portfolio_tickets,
                    observed_at TEXT NOT NULL, settlement_hash TEXT NOT NULL, settlement_json TEXT NOT NULL,
                    UNIQUE(ticket_id, settlement_hash));
                CREATE TABLE IF NOT EXISTS portfolio_voids (
                    ticket_id TEXT NOT NULL REFERENCES portfolio_tickets, leg_index INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL, note TEXT NOT NULL, PRIMARY KEY(ticket_id, leg_index));
                CREATE TABLE IF NOT EXISTS portfolio_bookmaker_returns (
                    ticket_id TEXT NOT NULL REFERENCES portfolio_tickets, settlement_hash TEXT NOT NULL,
                    payout_eur TEXT NOT NULL, recorded_at TEXT NOT NULL, note TEXT NOT NULL,
                    PRIMARY KEY(ticket_id, settlement_hash));
                CREATE TABLE IF NOT EXISTS portfolio_result_observations (
                    id INTEGER PRIMARY KEY, fixture_id TEXT NOT NULL,
                    result_id INTEGER NOT NULL REFERENCES portfolio_results,
                    observed_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS portfolio_settlement_observations (
                    id INTEGER PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES portfolio_tickets,
                    settlement_id INTEGER NOT NULL REFERENCES portfolio_settlements,
                    observed_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS portfolio_freezes (
                    date TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES portfolio_runs,
                    frozen_at TEXT NOT NULL, snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS portfolio_freeze_revisions (
                    date TEXT NOT NULL REFERENCES portfolio_freezes(date),
                    revision INTEGER NOT NULL CHECK(revision >= 2),
                    run_id TEXT NOT NULL UNIQUE REFERENCES portfolio_runs,
                    frozen_at TEXT NOT NULL, parent_snapshot_hash TEXT NOT NULL,
                    reason TEXT NOT NULL, snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
                    PRIMARY KEY(date, revision));
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(portfolio_wagers)")}
            if "price_source" not in columns:
                db.execute(
                    "ALTER TABLE portfolio_wagers ADD COLUMN price_source TEXT NOT NULL "
                    "DEFAULT 'RESEARCH_QUOTE_ESTIMATE'"
                )
            db.execute("""INSERT INTO portfolio_result_observations(fixture_id,result_id,observed_at)
                SELECT r.fixture_id,r.id,r.observed_at FROM portfolio_results r
                WHERE NOT EXISTS(SELECT 1 FROM portfolio_result_observations o WHERE o.fixture_id=r.fixture_id)
                ORDER BY r.id""")
            db.execute("""INSERT INTO portfolio_settlement_observations(ticket_id,settlement_id,observed_at)
                SELECT s.ticket_id,s.id,s.observed_at FROM portfolio_settlements s
                WHERE NOT EXISTS(SELECT 1 FROM portfolio_settlement_observations o WHERE o.ticket_id=s.ticket_id)
                ORDER BY s.id""")

            self._init_recommendations(db)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_run(self, report: dict[str, Any], *, _transaction=None) -> dict[str, Any]:
        """Freeze a run; preserve played tickets and replace unplayed suggestions.

        Over-cap and older suggestions remain in the archive. The new run cannot
        offer a second ticket for a profile already manually played that day.
        An older run imported later never supersedes a newer run.
        """
        snapshot = _json(report)
        run_id = str(report["run_id"])
        day = _day(report["date"])
        observed = _stamp(report["observed_at"])
        saved = _stamp(self.now()).isoformat()
        tickets = report.get("tickets", [])
        if not isinstance(tickets, list):
            raise ValueError("tickets must be a list")  # noqa: TRY004 - API validation contract.
        if len({str(t["ticket_id"]) for t in tickets}) != len(tickets):
            raise ValueError("Duplicate ticket_id in run")
        if _transaction is not None and not _transaction.in_transaction:
            raise ValueError("An active publication transaction is required")
        with nullcontext(_transaction) if _transaction is not None else self._connection() as db:
            if _transaction is None:
                db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT snapshot_json FROM portfolio_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if old:
                if old[0] != snapshot:
                    raise ValueError("run_id already exists with a different immutable snapshot")
                return {"run_id": run_id, "saved": False, "ticket_count": len(tickets)}
            latest = db.execute(
                "SELECT observed_at FROM portfolio_runs WHERE date=?", (day,)
            ).fetchall()
            activate = not latest or observed >= max(_stamp(r[0]) for r in latest)
            db.execute(
                "INSERT INTO portfolio_runs VALUES (?,?,?,?,?,?)",
                (run_id, day, observed.isoformat(), saved, snapshot, _hash(report)),
            )
            for ticket in tickets:
                self._validate_ticket(ticket, day, observed)
                frozen = _json(ticket)
                existing = db.execute(
                    "SELECT snapshot_json FROM portfolio_tickets WHERE ticket_id=?",
                    (ticket["ticket_id"],),
                ).fetchone()
                if existing:
                    if existing[0] != frozen:
                        raise ValueError(
                            "ticket_id already exists with a different immutable snapshot"
                        )
                    continue
                db.execute(
                    "INSERT INTO portfolio_tickets VALUES (?,?,?,?,?,?,?,0)",
                    (
                        ticket["ticket_id"],
                        run_id,
                        day,
                        ticket["profile_id"],
                        ticket.get("created_at", observed.isoformat()),
                        frozen,
                        _hash(ticket),
                    ),
                )
            self._save_recommendations(db, report, saved)
            if activate and tickets:
                old_active = db.execute(
                    """SELECT ticket_id,snapshot_json FROM portfolio_tickets
                    WHERE date=? AND active=1 AND ticket_id NOT IN
                    (SELECT ticket_id FROM portfolio_wagers)""",
                    (day,),
                ).fetchall()
                for row in old_active:
                    frozen_ticket = json.loads(row[1])
                    # Once a match starts its published suggestion remains counted.
                    # A later rerun must not hide losing research recommendations.
                    if all(_stamp(leg["kickoff"]) > _stamp(saved) for leg in frozen_ticket["legs"]):
                        self._active(db, row[0], False, saved, run_id)
                locked = db.execute(
                    """SELECT profile_id FROM portfolio_tickets
                    WHERE date=? AND active=1""",
                    (day,),
                ).fetchall()
                occupied = {row[0] for row in locked}
                slots = max(0, self.max_daily_tickets - len(locked))
                priority = sorted(
                    enumerate(tickets),
                    key=lambda x: (x[1]["profile_id"].upper() != "DAILY_223", x[0]),
                )
                for _, ticket in priority:
                    if slots <= 0 or ticket["profile_id"] in occupied:
                        continue
                    self._active(db, ticket["ticket_id"], True, saved, run_id)
                    occupied.add(ticket["profile_id"])
                    slots -= 1
        return {"run_id": run_id, "saved": True, "ticket_count": len(tickets)}

    @staticmethod
    def _active(db, ticket_id, value, stamp, run_id):
        db.execute(
            "UPDATE portfolio_tickets SET active=? WHERE ticket_id=?", (int(value), ticket_id)
        )
        db.execute(
            "INSERT INTO portfolio_events(ticket_id,event_type,observed_at,payload_json) VALUES (?,?,?,?)",
            (ticket_id, "ACTIVATED" if value else "SUPERSEDED", stamp, _json({"run_id": run_id})),
        )

    @staticmethod
    def _validate_ticket(ticket: Mapping[str, Any], day: str, observed: datetime) -> None:
        if not isinstance(ticket.get("ticket_id"), str) or not ticket["ticket_id"]:
            raise ValueError("ticket_id is required")
        if not isinstance(ticket.get("profile_id"), str) or not ticket["profile_id"]:
            raise ValueError("profile_id is required")
        if _day(ticket.get("date", day)) != day:
            raise ValueError("Ticket date differs from run date")
        if not isinstance(ticket.get("legs"), list) or not ticket["legs"]:
            raise ValueError("Ticket must have legs")
        if len({leg["fixture_id"] for leg in ticket["legs"]}) != len(ticket["legs"]):
            raise ValueError("Each ticket leg must refer to a distinct fixture")
        _amount(ticket["combined_price"], "combined_price", minimum=Decimal(1))
        _amount(ticket.get("stake_eur", 1), "stake_eur")
        if _stamp(ticket.get("created_at", observed.isoformat())) > observed:
            raise ValueError("Ticket creation cannot be later than the run observation")
        for leg in ticket["legs"]:
            kickoff = _stamp(leg["kickoff"])
            if kickoff <= observed:
                raise ValueError("Cannot save a new selection after fixture kickoff")
            if kickoff.astimezone(BERLIN).date().isoformat() != day:
                raise ValueError("Fixture is not on the ticket's Berlin date")
            _amount(leg["decimal_price"], "decimal_price", minimum=Decimal(1))

    def list_runs(self, date: str | Date | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        day = _day(date)
        with self._connection() as db:
            rows = db.execute(
                "SELECT snapshot_json FROM portfolio_runs WHERE (? IS NULL OR date=?) "
                "ORDER BY observed_at DESC, rowid DESC LIMIT ?",
                (day, day, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def freeze_run(self, run_id: str) -> dict[str, Any]:
        """Explicitly lock the full published recommendation set before kickoff."""
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("Válassz egy elkészült napi elemzést.")
        now = _stamp(self.now())
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT date,snapshot_json FROM portfolio_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Az elemzés nem található.")
            if db.execute("SELECT 1 FROM portfolio_freezes WHERE date=?", (row["date"],)).fetchone():
                raise ValueError("Erre a napra már van rögzített csomag. Az eredeti változat megmarad.")
            report = json.loads(row["snapshot_json"])
            recommendations = [
                json.loads(item[0]) for item in db.execute(
                    "SELECT snapshot_json FROM portfolio_recommendations WHERE run_id=? ORDER BY rowid",
                    (run_id,),
                )
            ]
            if not recommendations:
                raise ValueError("Nincs rögzíthető szelvény vagy profilajánlat ebben a futásban.")
            if any(
                _stamp(leg["kickoff"]) <= now
                for recommendation in recommendations for leg in recommendation["legs"]
            ):
                raise ValueError("A csomag egyik mérkőzése már elkezdődött; utólag nem fagyasztható.")
            snapshot = {
                "schema_version": 1, "date": row["date"], "run_id": run_id,
                "frozen_at": now.isoformat(), "report": report,
                "recommendations": recommendations,
            }
            db.execute(
                "INSERT INTO portfolio_freezes VALUES (?,?,?,?,?)",
                (row["date"], run_id, now.isoformat(), _json(snapshot), _hash(snapshot)),
            )
        return {"date": row["date"], "run_id": run_id, "frozen_at": now.isoformat(),
                "recommendation_count": len(recommendations)}

    @staticmethod
    def _freeze_chains(db, day=None):
        """Verify every immutable ancestor before selecting an effective package."""
        chains = []
        for original in db.execute(
            "SELECT * FROM portfolio_freezes WHERE (? IS NULL OR date=?) ORDER BY date DESC",
            (day, day),
        ).fetchall():
            rows = [original, *db.execute(
                "SELECT * FROM portfolio_freeze_revisions WHERE date=? ORDER BY revision",
                (original["date"],),
            ).fetchall()]
            chain = []
            for ordinal, row in enumerate(rows, 1):
                snapshot = json.loads(row["snapshot_json"])
                valid = (
                    _hash(snapshot) == row["snapshot_hash"]
                    and all(snapshot.get(k) == row[k] for k in ("date", "run_id", "frozen_at"))
                    and snapshot.get("recommendations")
                    and snapshot.get("report", {}).get("run_id") == row["run_id"]
                    and snapshot["report"].get("date") == row["date"]
                )
                if ordinal > 1:
                    valid = valid and (
                        row["revision"] == ordinal
                        and snapshot.get("revision") == ordinal
                        and row["parent_snapshot_hash"] == chain[-1]["snapshot_hash"]
                        and snapshot.get("parent_snapshot_hash") == row["parent_snapshot_hash"]
                        and snapshot.get("reason") == row["reason"]
                        and _stamp(row["frozen_at"]) >= _stamp(chain[-1]["frozen_at"])
                    )
                if not valid:
                    raise ValueError("A rögzített napi csomag sérült; az archívum nem értékelhető.")
                chain.append({
                    "date": row["date"], "run_id": row["run_id"], "revision": ordinal,
                    "frozen_at": row["frozen_at"], "snapshot_hash": row["snapshot_hash"],
                    "parent_snapshot_hash": row["parent_snapshot_hash"] if ordinal > 1 else None,
                    "reason": row["reason"] if ordinal > 1 else None, "snapshot": snapshot,
                })
            chains.append(chain)
        return chains

    def publish_revision(self, report: dict[str, Any], expected_snapshot_hash: str,
                         reason: str, *, validator: Callable) -> dict[str, Any]:
        """Commit run, active index and freeze CAS as one publication transaction.

        The validator receives an immutable-by-contract report and a fresh clock
        under the same write lock, before writes and immediately before commit.
        An exact retry of a committed revision is a read-only success, including
        after kickoff; it cannot reopen or alter the historical publication.
        """
        if not callable(validator):
            raise TypeError("A final publication validator is required")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            chains = self._freeze_chains(db, _day(report["date"]))
            current = chains[0][-1] if chains else None
            if current and current["run_id"] == report["run_id"] and current["revision"] > 1:
                existing = db.execute("SELECT snapshot_json FROM portfolio_runs WHERE run_id=?",
                                      (report["run_id"],)).fetchone()
                if existing is None or existing[0] != _json(report):
                    raise ValueError("Published run differs from the immutable retry snapshot")
                return self.revise_freeze(report["run_id"], expected_snapshot_hash, reason,
                                          _transaction=db)
            frozen = _json(report)
            validator(report, _stamp(self.now()))
            if _json(report) != frozen:
                raise ValueError("Publication validator changed the report")
            self.save_run(report, _transaction=db)
            revision = self.revise_freeze(report["run_id"], expected_snapshot_hash, reason,
                                          _transaction=db)
            validator(report, _stamp(self.now()))
            if _json(report) != frozen:
                raise ValueError("Publication validator changed the report")
            return revision

    def revise_freeze(self, run_id: str, expected_snapshot_hash: str, reason: str,
                      *, _transaction=None) -> dict[str, Any]:
        """Append an explicitly requested correction; never replace an original freeze."""
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("Válassz egy elkészült napi elemzést.")
        if not isinstance(expected_snapshot_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_snapshot_hash
        ):
            raise ValueError("Az előző csomag ellenőrzőösszege szükséges.")
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
            raise ValueError("A javítás rövid indoklása szükséges.")
        reason = reason.strip()
        now = _stamp(self.now())
        if _transaction is not None and not _transaction.in_transaction:
            raise ValueError("An active publication transaction is required")
        with nullcontext(_transaction) if _transaction is not None else self._connection() as db:
            if _transaction is None:
                db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM portfolio_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("Az elemzés nem található.")
            chains = self._freeze_chains(db, row["date"])
            if not chains:
                raise ValueError("Előbb az eredeti napi csomagot kell rögzíteni.")
            chain = chains[0]
            current = chain[-1]
            # An exact retry after an uncertain write has no second effect.
            if current["run_id"] == run_id and current["revision"] > 1:
                if current["parent_snapshot_hash"] == expected_snapshot_hash and current["reason"] == reason:
                    return self._revision_result(current, chain[0], saved=False)
                raise ValueError("Ez a futás már másik javítás része.")
            if current["snapshot_hash"] != expected_snapshot_hash:
                raise ValueError("A rögzített csomag közben megváltozott; frissítsd az ellenőrzőösszeget.")
            if any(item["run_id"] == run_id for item in chain):
                raise ValueError("Korábbi csomag nem állítható vissza új javításként.")
            if Date.fromisoformat(row["date"]) < now.astimezone(BERLIN).date():
                raise ValueError("Korábbi napi csomag utólag nem javítható.")
            if db.execute(
                "SELECT 1 FROM portfolio_wagers w JOIN portfolio_tickets t "
                "ON t.ticket_id=w.ticket_id WHERE t.date=? LIMIT 1", (row["date"],),
            ).fetchone():
                raise ValueError("Megjátszott szelvény napjának csomagja nem javítható.")
            report = json.loads(row["snapshot_json"])
            if _hash(report) != row["snapshot_hash"]:
                raise ValueError("A mentett elemzés ellenőrzőösszege hibás.")
            observed = _stamp(report["observed_at"])
            finished = _stamp(report.get("finished_at", report["observed_at"]))
            if not observed <= finished <= _stamp(row["saved_at"]) <= now:
                raise ValueError("A javítás időbélyege nem igazolható.")
            if observed <= _stamp(current["snapshot"]["report"]["observed_at"]):
                raise ValueError("A javításnak az előző csomagnál frissebb elemzésből kell készülnie.")
            if any(_stamp(item["frozen_at"]) > now for item in chain):
                raise ValueError("A korábbi csomag időbélyege a jövőben van.")
            recommendations = [json.loads(item[0]) for item in db.execute(
                "SELECT snapshot_json FROM portfolio_recommendations WHERE run_id=? ORDER BY rowid",
                (run_id,),
            )]
            if not recommendations or any(not item.get("legs") for item in recommendations):
                raise ValueError("Nincs rögzíthető szelvény vagy profilajánlat ebben a futásban.")
            # Bind the materialized index to the complete immutable publication;
            # a missing row or altered leg must never silently shrink a correction.
            sources = report.get("recommendations")
            if sources is None:
                sources = report.get("tickets", []) + report.get("preview_tickets", [])
            expected = []
            try:
                for source in sources:
                    if not source.get("legs") or any(
                        _stamp(leg["kickoff"]) <= finished for leg in source["legs"]
                    ):
                        continue
                    record = deepcopy(source)
                    if record.get("ticket_id"):
                        record.setdefault("portfolio_ticket_id", record.pop("ticket_id"))
                    record.update(
                        recommendation_id=_hash([run_id, record["profile_id"]])[:24],
                        date=row["date"], run_id=run_id, created_at=finished.isoformat(),
                    )
                    expected.append(record)
                matching = (
                    len({item["profile_id"] for item in expected}) == len(expected)
                    and sorted(_json(item) for item in expected)
                    == sorted(_json(item) for item in recommendations)
                )
            except (KeyError, TypeError, AttributeError):
                matching = False
            if not matching:
                raise ValueError("Az ajánlatindex nem egyezik a teljes mentett elemzéssel.")
            packages = [item["snapshot"]["recommendations"] for item in chain] + [recommendations]
            for package in packages:
                for recommendation in package:
                    for leg in recommendation["legs"]:
                        kickoff = _stamp(leg["kickoff"])
                        if kickoff <= now:
                            raise ValueError("A csomag egyik mérkőzése már elkezdődött; utólag nem javítható.")
                        if kickoff.astimezone(BERLIN).date().isoformat() != row["date"]:
                            raise ValueError("A mérkőzés nem a csomag napjára esik.")
            ordinal = len(chain) + 1
            snapshot = {
                "schema_version": 2, "date": row["date"], "run_id": run_id,
                "frozen_at": now.isoformat(), "report": report, "recommendations": recommendations,
                "revision": ordinal, "parent_snapshot_hash": expected_snapshot_hash, "reason": reason,
            }
            digest = _hash(snapshot)
            db.execute("INSERT INTO portfolio_freeze_revisions VALUES (?,?,?,?,?,?,?,?)", (
                row["date"], ordinal, run_id, now.isoformat(), expected_snapshot_hash, reason,
                _json(snapshot), digest,
            ))
            entry = {**snapshot, "snapshot": snapshot, "snapshot_hash": digest}
            return self._revision_result(entry, chain[0], saved=True)

    @staticmethod
    def _revision_result(entry, original, *, saved):
        return {
            "date": entry["date"], "run_id": entry["run_id"], "frozen_at": entry["frozen_at"],
            "revision": entry["revision"], "snapshot_hash": entry["snapshot_hash"],
            "parent_snapshot_hash": entry["parent_snapshot_hash"],
            "original_snapshot_hash": original["snapshot_hash"], "saved": saved,
            "recommendation_count": len(entry["snapshot"]["recommendations"]),
        }

    def list_freeze_history(self, date: str | Date | None = None) -> list[dict[str, Any]]:
        """Original, full immutable snapshots, including superseded revisions."""
        with self._connection() as db:
            return [item for chain in self._freeze_chains(db, _day(date)) for item in chain]

    def list_freezes(self, date: str | Date | None = None) -> list[dict[str, Any]]:
        with self._connection() as db:
            chains = self._freeze_chains(db, _day(date))
        outcomes = {
            item["recommendation_id"]: item
            for item in self.list_recommendations(date, archive=True)
        }
        result = []
        for chain in chains:
            row = chain[-1]
            snapshot = row["snapshot"]
            recommendations = [
                outcomes.get(item["recommendation_id"], item)
                for item in snapshot["recommendations"]
            ]
            statuses = {item.get("outcome", "PENDING") for item in recommendations}
            outcome = (
                "LOST" if "LOST" in statuses else
                "REVIEW" if "REVIEW" in statuses else
                "PENDING" if "PENDING" in statuses else "WON"
            )
            result.append({
                "date": snapshot["date"], "run_id": snapshot["run_id"],
                "frozen_at": snapshot["frozen_at"], "snapshot_hash": row["snapshot_hash"],
                "revision": row["revision"], "revision_count": len(chain) - 1,
                "parent_snapshot_hash": row["parent_snapshot_hash"], "revision_reason": row["reason"],
                "original_snapshot_hash": chain[0]["snapshot_hash"],
                "revision_history": [{k: v for k, v in item.items() if k != "snapshot"} for item in chain],
                "recommendations": recommendations, "outcome": outcome,
                "won": sum(item.get("outcome") == "WON" for item in recommendations),
                "lost": sum(item.get("outcome") == "LOST" for item in recommendations),
            })
        return result

    def freeze_performance(self, month: str | None = None) -> dict[str, Any]:
        if month is not None and not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValueError("month must be YYYY-MM")
        packages = [
            item for item in self.list_freezes()
            if month is None or item["date"].startswith(month)
        ]
        recommendations = [r for package in packages for r in package["recommendations"]]
        won = sum(r.get("outcome") == "WON" for r in recommendations)
        lost = sum(r.get("outcome") == "LOST" for r in recommendations)
        decided = won + lost
        return {
            "mode": "freezes", "month": month, "packages": packages,
            "summary": {
                "packages": len(packages), "package_won": sum(p["outcome"] == "WON" for p in packages),
                "package_lost": sum(p["outcome"] == "LOST" for p in packages),
                "tickets": len(recommendations), "won": won, "lost": lost,
                "pending": sum(r.get("outcome") == "PENDING" for r in recommendations),
                "review": sum(r.get("outcome") == "REVIEW" for r in recommendations),
                "hit_rate_pct": round(100 * won / decided, 2) if decided else None,
            },
            "series": [{"date": p["date"], "tickets": len(p["recommendations"]),
                        "won": p["won"], "lost": p["lost"], "outcome": p["outcome"]}
                       for p in reversed(packages)],
            "net_definition": "Only frozen pre-kickoff recommendations; no assumed wager or profit.",
        }

    def list_tickets(
        self, date: str | Date | None = None, mode: str = "all", *, monetary_contract="LEGACY_RECORDED_V1"
    ) -> list[dict[str, Any]]:
        if mode not in {"all", "actual", "research", "archive"}:
            raise ValueError("mode must be all, actual, research or archive")
        day = _day(date)
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM portfolio_tickets WHERE (? IS NULL OR date=?) "
                "ORDER BY date DESC, created_at DESC, rowid DESC",
                (day, day),
            ).fetchall()
            tickets = [self._ticket_view(db, row, monetary_contract=monetary_contract) for row in rows]
        return [
            t
            for t in tickets
            if (mode == "archive" or t["active"] or t["played"])
            and (mode != "actual" or t["played"])
            and (mode != "research" or not t["played"])
        ]

    def get_ticket(self, ticket_id: str, *, monetary_contract="LEGACY_RECORDED_V1") -> dict[str, Any]:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM portfolio_tickets WHERE ticket_id=?", (ticket_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Unknown ticket_id")
            return self._ticket_view(db, row, monetary_contract=monetary_contract)

    def _ticket_view(self, db, row, *, monetary_contract="LEGACY_RECORDED_V1") -> dict[str, Any]:
        ticket = json.loads(row["snapshot_json"])
        wager_row = db.execute(
            "SELECT * FROM portfolio_wagers WHERE ticket_id=?", (row["ticket_id"],)
        ).fetchone()
        wager = (
            None
            if wager_row is None
            else {
                "stake_eur": float(wager_row["stake_eur"]),
                "actual_combined_price": float(wager_row["actual_combined_price"]),
                "recorded_at": wager_row["recorded_at"],
                "actual_price_source": wager_row["price_source"],
            }
        )
        observations = db.execute(
            "SELECT s.settlement_json,o.observed_at,s.settlement_hash FROM portfolio_settlement_observations o "
            "JOIN portfolio_settlements s ON s.id=o.settlement_id "
            "WHERE o.ticket_id=? ORDER BY o.id DESC",
            (row["ticket_id"],),
        ).fetchall()
        settled = observations[0] if observations else None
        settlement = json.loads(settled[0]) if settled else None
        if settlement:
            settlement["settlement_hash"] = settled[2]
        if settlement:
            settlement["settled_at"] = settled[1]
            settlement["updated_at"] = settled[1]
            if settlement["outcome"] in TERMINAL:
                for old_observation in observations[1:]:
                    if json.loads(old_observation[0])["outcome"] != settlement["outcome"]:
                        break
                    settlement["settled_at"] = old_observation[1]
            void_count = sum(leg["outcome"] == "VOID" for leg in settlement["legs"])
            if wager and settlement["outcome"] == "WON" and void_count:
                confirmation = db.execute(
                    "SELECT payout_eur,recorded_at,note FROM portfolio_bookmaker_returns "
                    "WHERE ticket_id=? AND settlement_hash=?",
                    (row["ticket_id"], settled[2]),
                ).fetchone()
                settlement["research_outcome"] = "WON"
                if confirmation:
                    settlement["bookmaker_return"] = {
                        "payout_eur": float(confirmation[0]),
                        "recorded_at": confirmation[1],
                        "note": confirmation[2],
                    }
                    settlement["bookmaker_return"]["settlement_hash"] = settled[2]
                    try:
                        settlement["bookmaker_return"].update(_bookmaker_return_provenance(db, row["ticket_id"], settled[2]))
                    except (ValueError, sqlite3.Error):
                        settlement["bookmaker_return"].update(payout_basis="INVALID_PROVENANCE", fee_tax_inclusion="UNKNOWN", provenance_status="INVALID")
                    settlement["settled_at"] = confirmation[1]
                    if Decimal(confirmation[0]) == Decimal(str(wager["stake_eur"])):
                        settlement["outcome"] = "VOID"
                else:
                    # A total accepted price does not reveal the accepted price of a void leg.
                    settlement["outcome"] = "REVIEW"
                    settlement["reason"] = (
                        "ACTUAL_PARTIAL_VOID_RETURN_REQUIRES_BOOKMAKER_CONFIRMATION"
                    )
        ticket.update(
            date=row["date"],
            created_at=row["created_at"],
            run_id=row["run_id"],
            active=bool(row["active"]),
            played=wager is not None,
            wager=wager,
            snapshot_sha256=row["snapshot_hash"],
            settlement=settlement,
            outcome=settlement["outcome"] if settlement else "PENDING",
        )
        ticket["bookmaker_return_contract"] = {
            "eligible": bool(wager and settlement and settlement.get("reason") == "ACTUAL_PARTIAL_VOID_RETURN_REQUIRES_BOOKMAKER_CONFIRMATION"),
            "expected_settlement_hash": settled[2] if settled else None,
            "stake_eur": wager["stake_eur"] if wager else None,
            "minimum_gross_return_eur": wager["stake_eur"] if wager else None,
            "currency": "EUR", "amount_step": "0.01",
            "payout_basis": "BOOKMAKER_CONFIRMED_GROSS_SPORTS_RETURN",
            "fee_tax_inclusion": "EXCLUDED", "contract_version": "bookmaker-gross-return-v1",
        }
        ticket["financial"] = _financial(ticket, monetary_contract=monetary_contract)
        return ticket

    def record_wager(
        self, ticket_id: str, stake_eur: float = 1, actual_combined_price: float | None = None
    ) -> dict[str, Any]:
        """Record the user's explicit pre-match confirmation; never place a bet."""
        stake = _amount(stake_eur, "stake_eur")
        if stake != stake.quantize(Decimal("0.01")):
            raise ValueError("stake_eur cannot have fractions of a cent")
        if stake != Decimal(1):
            raise ValueError("The agreed fixed stake is exactly 1 EUR per ticket")
        now = _stamp(self.now())
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM portfolio_tickets WHERE ticket_id=?", (ticket_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Unknown ticket_id")
            ticket = self._ticket_view(db, row)
            price = _amount(
                ticket["combined_price"]
                if actual_combined_price is None
                else actual_combined_price,
                "actual_combined_price",
                minimum=Decimal(1),
            )
            old = ticket["wager"]
            if old:
                if (
                    Decimal(str(old["stake_eur"])) != stake
                    or Decimal(str(old["actual_combined_price"])) != price
                ):
                    raise ValueError(
                        "An immutable wager is already recorded with different amounts"
                    )
                return ticket
            if not ticket["active"]:
                raise ValueError("This ticket is superseded or outside the daily cap")
            if ticket.get("status", "READY") != "READY" and actual_combined_price is None:
                raise ValueError(
                    "A draft requires explicit confirmation of the actual bookmaker price"
                )
            if str(ticket.get("currency", "EUR")).upper() != "EUR":
                raise ValueError("Only EUR wagers are supported by this ledger")
            if any(_stamp(leg["kickoff"]) <= now for leg in ticket["legs"]):
                raise ValueError(
                    "A fixture has already started; retrospective wagers are not recorded"
                )
            if ticket["outcome"] != "PENDING":
                raise ValueError("A result has already been observed for this ticket")
            played = db.execute(
                """SELECT count(*) FROM portfolio_wagers w JOIN portfolio_tickets t
                ON t.ticket_id=w.ticket_id WHERE t.date=?""",
                (ticket["date"],),
            ).fetchone()[0]
            if played >= self.max_daily_tickets:
                raise ValueError("Daily maximum of five played tickets reached")
            db.execute(
                "INSERT INTO portfolio_wagers VALUES (?,?,?,?,?)",
                (
                    ticket_id,
                    str(stake),
                    str(price),
                    now.isoformat(),
                    "USER_CONFIRMED"
                    if actual_combined_price is not None
                    else "RESEARCH_QUOTE_ESTIMATE",
                ),
            )
            db.execute(
                "INSERT INTO portfolio_events(ticket_id,event_type,observed_at,payload_json) VALUES (?,?,?,?)",
                (
                    ticket_id,
                    "MANUAL_WAGER_CONFIRMED",
                    now.isoformat(),
                    _json(
                        {
                            "stake_eur": float(stake),
                            "actual_combined_price": float(price),
                            "bookmaker_order_sent": False,
                        }
                    ),
                ),
            )
            return self._ticket_view(db, row)

    def record_void(self, ticket_id: str, fixture_id: str, note: str) -> dict[str, Any]:
        """Only an explicit confirmation of the bookmaker's void decision refunds a leg."""
        if not isinstance(note, str) or len(note.strip()) < 5:
            raise ValueError("A bookmaker void confirmation note is required")
        ticket = self.get_ticket(ticket_id)
        matches = [i for i, leg in enumerate(ticket["legs"]) if leg["fixture_id"] == fixture_id]
        if len(matches) != 1:
            raise ValueError("fixture_id must identify exactly one ticket leg")
        stamp = _stamp(self.now()).isoformat()
        with self._connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO portfolio_voids VALUES (?,?,?,?)",
                (ticket_id, matches[0], stamp, note.strip()),
            )
            self._settle(db, ticket, stamp)
        return self.get_ticket(ticket_id)

    def confirm_bookmaker_return(
        self, ticket_id: str, payout_eur: float, note: str, *,
        expected_settlement_hash: str | None = None, payout_basis: str | None = None,
    ) -> dict[str, Any]:
        """Explicit gross revision-CAS; legacy Python calls mean atomic latest.

        HTTP callers must supply both token and basis. The real legacy three
        positional API remains untyped recorded arithmetic, never fee-free cash.
        """
        amount = _amount(payout_eur, "payout_eur")
        try:
            cents = amount == amount.quantize(Decimal("0.01"))
        except InvalidOperation:
            cents = False
        if not cents:
            raise ValueError("Confirmed returns must be exact cents, without rounding")
        if not isinstance(note, str) or not 5 <= len(note.strip()) <= 1000:
            raise ValueError("A bounded bookmaker return confirmation note is required")
        typed = expected_settlement_hash is not None or payout_basis is not None
        if typed and (not isinstance(expected_settlement_hash, str)
                or re.fullmatch(r"[a-f0-9]{64}", expected_settlement_hash) is None
                or payout_basis != "BOOKMAKER_CONFIRMED_GROSS_SPORTS_RETURN"):
            raise ValueError("Explicit gross return requires its exact settlement hash and supported basis")
        stamp = _stamp(self.now()).isoformat()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            wager = db.execute("SELECT stake_eur FROM portfolio_wagers WHERE ticket_id=?", (ticket_id,)).fetchone()
            row = db.execute(
                "SELECT s.settlement_json,s.settlement_hash FROM portfolio_settlement_observations o "
                "JOIN portfolio_settlements s ON s.id=o.settlement_id "
                "WHERE o.ticket_id=? ORDER BY o.id DESC LIMIT 1", (ticket_id,),
            ).fetchone()
            if typed and row is not None and row[1] != expected_settlement_hash:
                raise RuntimeError("SETTLEMENT_REVISION_CHANGED")
            if not wager or row is None:
                raise ValueError("No actual partially voided wager is awaiting confirmation")
            settlement = json.loads(row[0])
            if settlement["outcome"] != "WON" or not any(leg["outcome"] == "VOID" for leg in settlement["legs"]):
                raise ValueError("No actual partially voided wager is awaiting confirmation")
            stake = _amount(wager["stake_eur"], "stored_wager_stake_eur")
            if amount < stake:
                raise ValueError("Confirmed gross partial-void return must cover the actual stored stake")
            # RO validation applies to the genuine legacy writer too.
            # Never commit an amount and discover corrupt provenance only on GET.
            _bookmaker_return_provenance(db, ticket_id, row[1])
            previous = db.execute("SELECT payout_eur,note FROM portfolio_bookmaker_returns WHERE ticket_id=? AND settlement_hash=?", (ticket_id,row[1])).fetchone()
            if previous:
                if Decimal(previous[0]) != amount or previous[1] != note.strip():
                    raise ValueError("An immutable bookmaker return confirmation already exists")
                provenance = _bookmaker_return_provenance(db, ticket_id, row[1])
                if typed and provenance["payout_basis"] != payout_basis:
                    raise ValueError("Existing legacy return basis remains unspecified; no automatic backfill")
            else:
                if typed:
                    # No DDL before CAS, actual wager/settlement, cents, minimum
                    # and immutable-conflict checks. execute(), not executescript().
                    _ensure_bookmaker_return_provenance(db)
                db.execute("INSERT INTO portfolio_bookmaker_returns VALUES (?,?,?,?,?)", (ticket_id,row[1],str(amount),stamp,note.strip()))
                if typed:
                    db.execute("INSERT INTO portfolio_bookmaker_return_provenance(ticket_id,settlement_hash,payout_basis,fee_tax_inclusion,confirmed_at,contract_version) VALUES (?,?,?,?,?,?)", (ticket_id,row[1],payout_basis,"EXCLUDED",stamp,"bookmaker-gross-return-v1"))
        return self.get_ticket(ticket_id)

    def refresh_results(self, football_client) -> dict[str, Any]:
        """Refresh unique started fixtures, with a hard per-invocation request cap.

        Uses the provider's single-fixture id query, avoiding assumptions about
        batch-id limits. Failed/unreturned ids stay pending and remain visible.
        """
        now = _stamp(self.now())
        tickets = self.list_tickets()
        recommendations = self.list_recommendations(archive=True)
        wanted: dict[str, tuple[str, datetime]] = {}
        urgent: dict[str, int] = {}
        today = now.astimezone(BERLIN).date()
        unsupported: set[str] = set()
        stats_primary = getattr(football_client, 'provider', None) == 'the_stats_api'
        result_legs = {}
        stats_resources = None
        if stats_primary:
            from goal_analysis.jobs.the_stats_context import Resources

            stats_resources = Resources(football_client.root, football_client, self.now, self.refresh_call_limit)
        with self._connection() as db:
            refresh_offset = db.execute("SELECT count(*) FROM portfolio_refreshes").fetchone()[0]
            for ticket in tickets + recommendations:
                voids = {
                    row[0]
                    for row in db.execute(
                        "SELECT leg_index FROM portfolio_voids WHERE ticket_id=?",
                        (ticket.get("ticket_id", ""),),
                    )
                }
                for index, leg in enumerate(ticket["legs"]):
                    if index in voids or _stamp(leg["kickoff"]) > now:
                        continue
                    fixture_id = str(leg["fixture_id"])
                    if not (re.fullmatch(r"(?:api_football:)?[0-9]+", fixture_id)
                            or stats_primary and re.fullmatch(r'the_stats_api:mt_[0-9]{1,30}', fixture_id)):
                        unsupported.add(fixture_id)
                        continue
                    cached = self._latest_result(db, fixture_id)
                    checked = db.execute(
                        "SELECT observed_at FROM portfolio_refreshes WHERE fixture_id=? "
                        "ORDER BY id DESC LIMIT 1",
                        (fixture_id,),
                    ).fetchone()
                    terminal = cached is not None and _leg_result(leg, cached)["outcome"] in TERMINAL
                    if terminal:
                        age = now - _stamp(leg["kickoff"])
                        since_checked = now - _stamp(checked[0]) if checked else timedelta.max
                        if age > timedelta(hours=self.correction_window_hours) or since_checked < timedelta(hours=6):
                            continue
                    wanted[fixture_id] = (checked[0] if checked else "", _stamp(leg["kickoff"]))
                    result_legs[fixture_id] = leg
                    if not terminal and (ticket.get("active") or ticket.get("played")):
                        priority = 0 if ticket.get("played") or _stamp(leg["kickoff"]).astimezone(BERLIN).date() == today else 1
                        urgent[fixture_id] = min(urgent.get(fixture_id, priority), priority)
        # Published unfinished matches need results before old archive corrections.
        # Keep one in five request slots for the background queue, including across
        # invocations with a one-call cap, so archive coverage is not starved.
        primary = iter(sorted(urgent, key=lambda key: (urgent[key], wanted[key], key)))
        background = iter(sorted(wanted.keys() - urgent.keys(), key=lambda key: (wanted[key], key)))
        next_primary, next_background = next(primary, None), next(background, None)
        requests = []
        while next_primary is not None or next_background is not None:
            background_slot = (refresh_offset + len(requests) + 1) % 5 == 0
            if next_background is not None and (next_primary is None or background_slot):
                requests.append(next_background)
                next_background = next(background, None)
            else:
                requests.append(next_primary)
                next_primary = next(primary, None)
        calls, received, errors = 0, 0, []
        for fixture_id in requests[: self.refresh_call_limit]:
            calls += 1
            refresh_status = "OK"
            try:
                if stats_primary:
                    from goal_analysis.jobs.the_stats_results import resolve_result

                    item = resolve_result(football_client, result_legs[fixture_id], resources=stats_resources)
                else:
                    payload = football_client.request(
                        "fixtures", {"id": int(fixture_id.split(":")[-1])}
                    )
                    expected_id = int(fixture_id.split(":")[-1])
                    matching = [item for item in payload.get("response", [])
                                if str(item.get("fixture", {}).get("id")) == str(expected_id)]
                    if len(matching) != 1:
                        raise ValueError("The provider did not return exactly the requested fixture")
                    item = matching[0]
                with self._connection() as db:
                    db.execute(
                        "INSERT OR IGNORE INTO portfolio_results(fixture_id,observed_at,payload_hash,payload_json) "
                        "VALUES (?,?,?,?)",
                        (fixture_id, now.isoformat(), _hash(item), _json(item)),
                    )
                    result_id = db.execute(
                        "SELECT id FROM portfolio_results WHERE fixture_id=? AND payload_hash=?",
                        (fixture_id, _hash(item)),
                    ).fetchone()[0]
                    old_observation = db.execute(
                        "SELECT result_id FROM portfolio_result_observations "
                        "WHERE fixture_id=? ORDER BY id DESC LIMIT 1",
                        (fixture_id,),
                    ).fetchone()
                    if old_observation is None or old_observation[0] != result_id:
                        db.execute(
                            "INSERT INTO portfolio_result_observations(fixture_id,result_id,observed_at) VALUES (?,?,?)",
                            (fixture_id, result_id, now.isoformat()),
                        )
                received += 1
            except Exception as error:  # noqa: BLE001 - Redact every external provider error at this boundary.
                refresh_status = type(error).__name__
                # Provider exceptions may contain a URL including credentials. Never export the text.
                errors.append(
                    {
                        "fixture_id": fixture_id,
                        "error": type(error).__name__,
                        "reason": "RESULT_REFRESH_FAILED",
                    }
                )
            with self._connection() as db:
                db.execute(
                    "INSERT INTO portfolio_refreshes(fixture_id,observed_at,status) VALUES (?,?,?)",
                    (fixture_id, now.isoformat(), refresh_status),
                )
        updated = 0
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for ticket in tickets:
                updated += int(self._settle(db, ticket, now.isoformat()))
        return {
            "football_calls": calls,
            "received": received,
            "updated_tickets": updated,
            "tracked_recommendations": len(recommendations),
            "remaining_fixtures": max(0, len(requests) - calls),
            "refresh_queue": {
                "active_unfinished": len(urgent),
                "background": len(wanted) - len(urgent),
                "remaining_active_unfinished": sum(key in urgent for key in requests[calls:]),
                "remaining_background": sum(key not in urgent for key in requests[calls:]),
            },
            "errors": errors,
            "unsupported_fixture_ids": sorted(unsupported),
            "observed_at": now.isoformat(),
        }

    @staticmethod
    def _latest_result(db, fixture_id):
        row = db.execute(
            "SELECT r.payload_json FROM portfolio_result_observations o "
            "JOIN portfolio_results r ON r.id=o.result_id WHERE o.fixture_id=? ORDER BY o.id DESC LIMIT 1",
            (fixture_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def _settle(self, db, ticket, stamp) -> bool:
        voids = {
            row[0]: row[1]
            for row in db.execute(
                "SELECT leg_index,note FROM portfolio_voids WHERE ticket_id=?",
                (ticket["ticket_id"],),
            )
        }
        legs = []
        for index, leg in enumerate(ticket["legs"]):
            result = self._latest_result(db, leg["fixture_id"])
            outcome = (
                {"outcome": "VOID", "reason": "BOOKMAKER_VOID_CONFIRMED", "note": voids[index]}
                if index in voids
                else _leg_result(leg, result)
            )
            legs.append(
                {
                    "fixture_id": leg["fixture_id"],
                    "market_key": leg["market_key"],
                    "selection_key": leg["selection_key"],
                    "period": leg.get("period", "FULL_TIME"),
                    "decimal_price": leg["decimal_price"],
                    **outcome,
                }
            )
        statuses = {leg["outcome"] for leg in legs}
        if "LOST" in statuses:
            outcome, multiplier = "LOST", Decimal(0)
        elif "REVIEW" in statuses:
            outcome, multiplier = "REVIEW", None
        elif "PENDING" in statuses:
            outcome, multiplier = "PENDING", None
        elif statuses == {"VOID"}:
            outcome, multiplier = "VOID", Decimal(1)
        else:
            outcome, multiplier = "WON", Decimal(str(ticket["combined_price"]))
            for leg in legs:
                if leg["outcome"] == "VOID":
                    multiplier /= Decimal(str(leg["decimal_price"]))
        settled = {
            "outcome": outcome,
            "return_multiplier": float(multiplier) if multiplier is not None else None,
            "legs": legs,
            "rules": "REGULATION_OR_FIRST_HALF;BOOKMAKER_CONFIRMED_VOID_ONLY",
        }
        digest = _hash(settled)
        previous = db.execute(
            "SELECT s.settlement_hash FROM portfolio_settlement_observations o "
            "JOIN portfolio_settlements s ON s.id=o.settlement_id "
            "WHERE o.ticket_id=? ORDER BY o.id DESC LIMIT 1",
            (ticket["ticket_id"],),
        ).fetchone()
        if previous is not None and previous[0] == digest:
            return False
        db.execute(
            "INSERT OR IGNORE INTO portfolio_settlements(ticket_id,observed_at,settlement_hash,settlement_json) "
            "VALUES (?,?,?,?)",
            (ticket["ticket_id"], stamp, digest, _json(settled)),
        )
        settlement_id = db.execute(
            "SELECT id FROM portfolio_settlements WHERE ticket_id=? AND settlement_hash=?",
            (ticket["ticket_id"], digest),
        ).fetchone()[0]
        db.execute(
            "INSERT INTO portfolio_settlement_observations(ticket_id,settlement_id,observed_at) VALUES (?,?,?)",
            (ticket["ticket_id"], settlement_id, stamp),
        )
        return True

    def performance(self, month: str | None = None, mode: str = "actual", *, monetary_contract="LEGACY_RECORDED_V1") -> dict[str, Any]:
        from .daily_ticket_kpis import safe_daily_ticket_kpis
        from .leg_error_analysis import safe_leg_error_analysis
        from .performance_breakdowns import safe_breakdowns
        from .rolling_ticket_kpis import safe_rolling_ticket_kpis

        if mode == "freezes":
            return self.freeze_performance(month)
        if mode == "recommendations":
            return self.recommendation_performance(month)
        if mode not in {"actual", "research"}:
            raise ValueError("performance mode must be actual, research or recommendations")
        if month is not None and (
            not re.fullmatch(r"\d{4}-\d{2}", month) or _day(month + "-01") is None
        ):
            raise ValueError("month must be YYYY-MM")
        all_tickets = self.list_tickets(mode=mode, monetary_contract=monetary_contract)
        tickets = [
            ticket
            for ticket in all_tickets
            if month is None or ticket["date"].startswith(month)
        ]
        profiles, days = defaultdict(list), defaultdict(list)
        for ticket in tickets:
            profiles[ticket["profile_id"]].append(ticket)
            days[ticket["date"]].append(ticket)
        series, cumulative, known_cumulative, complete = [], Decimal(0), Decimal(0), True
        for day, items in sorted(days.items()):
            summary = _summary(items)
            known = summary.get("known_subset_net_eur", summary["net_eur"])
            known_cumulative += Decimal(str(known))
            complete = complete and summary["net_eur"] is not None
            if summary["net_eur"] is not None:
                cumulative += Decimal(str(summary["net_eur"]))
            series.append({"date": day, **summary,
                           "cumulative_net_eur": _money(cumulative) if complete else None,
                           "known_subset_cumulative_net_eur": _money(known_cumulative)})
        leg_analysis = safe_leg_error_analysis(tickets, mode=mode)
        daily_kpis = safe_daily_ticket_kpis(tickets, mode=mode, leg_analysis=leg_analysis)
        return {
            "mode": mode,
            "month": month,
            "currency": "EUR",
            "basis": "ticket_date",
            "summary": _summary(tickets),
            "breakdowns": safe_breakdowns(tickets, _summary, mode=mode),
            "leg_error_analysis": leg_analysis,
            "daily_kpis": daily_kpis,
            "rolling_kpis": safe_rolling_ticket_kpis(all_tickets, _summary, mode=mode, monetary_contract=monetary_contract,
                as_of=self.now(), month=month,
                daily_kpis=daily_kpis if len(tickets) == len(all_tickets) else None),
            "series": series,
            "by_profile": [
                {
                    "profile_id": profile,
                    "profile_name": items[0].get("profile_name", profile),
                    **_summary(items),
                }
                for profile, items in sorted(profiles.items())
            ],
            "api_usage": self._usage(month),
            "api_costs_included_in_net": False,
            "net_definition": "Settled ticket profit; pending stakes are displayed separately",
        }

    def _usage(self, month):
        totals = {
            "runs": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "football_calls": 0,
            "odds_calls": 0,
            "known_costs_by_currency": {},
        }
        with self._connection() as db:
            runs = [
                json.loads(row[0]) for row in db.execute("SELECT snapshot_json FROM portfolio_runs")
            ]
            result_refreshes = db.execute("SELECT observed_at FROM portfolio_refreshes").fetchall()
        totals["result_football_calls"] = sum(
            1
            for row in result_refreshes
            if month is None or _stamp(row[0]).astimezone(BERLIN).strftime("%Y-%m") == month
        )
        for report in runs:
            if month and not report["date"].startswith(month):
                continue
            totals["runs"] += 1
            usage = report.get("usage", {})
            astra = (
                report.get("astra", {}).get("usage", {})
                if isinstance(report.get("astra"), dict)
                else {}
            )
            for key in ("input_tokens", "output_tokens", "football_calls", "odds_calls"):
                value = usage.get(key, astra.get(key, 0))
                if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                    totals[key] += value
            for currency, amount in usage.get("known_costs_by_currency", {}).items():
                if type(amount) in (int, float) and math.isfinite(amount) and amount >= 0:
                    known = totals["known_costs_by_currency"]
                    known[str(currency)] = round(known.get(str(currency), 0) + amount, 6)
        return totals

    def feedback_summary(self) -> dict[str, Any]:
        tickets = self.list_tickets(monetary_contract="CURRENT_HASH_V2")
        settled = [t for t in tickets if t["outcome"] in TERMINAL]
        settled.sort(key=lambda t: (t["date"], t["created_at"], t["ticket_id"]), reverse=True)
        return {
            "schema_version": 1,
            "generated_at": _stamp(self.now()).isoformat(),
            "actual": self.performance(mode="actual", monetary_contract="CURRENT_HASH_V2"),
            "research": self.performance(mode="research"),
            "recommendations": {
                k: v
                for k, v in self.recommendation_performance().items()
                if k in {"summary", "by_profile", "counting_basis"}
            },
            "recent_settled": [
                {
                    key: t.get(key)
                    for key in (
                        "ticket_id",
                        "profile_id",
                        "date",
                        "played",
                        "combined_price",
                        "outcome",
                        "financial",
                        "legs",
                    )
                }
                for t in settled[:30]
            ],
            "interpretation": "Descriptive observations, not proof of future edge or automatic model training",
        }


def _leg_result(leg: Mapping[str, Any], fixture: Mapping[str, Any] | None) -> dict[str, Any]:
    if fixture is None:
        return {"outcome": "PENDING", "reason": "RESULT_NOT_AVAILABLE"}
    status = fixture.get("fixture", {}).get("status", {}).get("short")
    period = leg.get("period", "FULL_TIME")
    if status in MANUAL_REVIEW_MATCH:
        return {
            "outcome": "REVIEW",
            "reason": "BOOKMAKER_RULE_REVIEW_REQUIRED",
            "provider_status": status,
        }
    if period not in {"FIRST_HALF", "FULL_TIME"}:
        return {"outcome": "REVIEW", "reason": "UNSUPPORTED_PERIOD"}
    complete = FIRST_HALF_COMPLETE if period == "FIRST_HALF" else FINAL_MATCH
    if status not in complete:
        return {"outcome": "PENDING", "reason": "PERIOD_NOT_FINISHED", "provider_status": status}
    score = fixture.get("score", {}).get("halftime" if period == "FIRST_HALF" else "fulltime") or {}
    home, away = score.get("home"), score.get("away")
    if any(type(value) is not int or value < 0 for value in (home, away)):
        return {"outcome": "REVIEW", "reason": "PERIOD_SCORE_MISSING", "provider_status": status}
    context = {"home_goals": home, "away_goals": away, "provider_status": status}
    market, selection = str(leg.get("market_key", "")), leg.get("selection_key")
    if market in {"h2h", "match_result"} and selection in {"home", "away", "draw"}:
        actual = "draw" if home == away else "home" if home > away else "away"
        won = selection == actual
    elif market == "btts" and selection in {"yes", "no"}:
        won = (home > 0 and away > 0) == (selection == "yes")
    elif market.startswith("totals_") and selection in {"over", "under"}:
        try:
            line = Decimal(market.removeprefix("totals_").replace("_", "."))
        except InvalidOperation:
            return {"outcome": "REVIEW", "reason": "INVALID_TOTAL_LINE", **context}
        # Half-goal lines only: Asian quarter lines and integer pushes require different rules.
        if not line.is_finite() or line < 0 or line % 1 != Decimal("0.5"):
            return {"outcome": "REVIEW", "reason": "UNSUPPORTED_TOTAL_LINE", **context}
        won = (Decimal(home + away) > line) == (selection == "over")
    else:
        return {"outcome": "REVIEW", "reason": "UNSUPPORTED_MARKET", **context}
    return {"outcome": "WON" if won else "LOST", "reason": "PERIOD_SCORE_CONFIRMED", **context}


def _financial(ticket: Mapping[str, Any], *, monetary_contract="LEGACY_RECORDED_V1") -> dict[str, Any]:
    if ticket.get("wager"):
        return project_recorded_wager(ticket, contract=monetary_contract,
            allow_estimated_price_arithmetic=monetary_contract == "LEGACY_RECORDED_V1")
    wager = ticket.get("wager")
    stake = Decimal(str(wager["stake_eur"] if wager else ticket.get("stake_eur", 1)))
    settlement = ticket.get("settlement")
    outcome = ticket.get("outcome", "PENDING")
    price = Decimal(str(wager["actual_combined_price"] if wager else ticket["combined_price"]))
    if settlement and settlement.get("bookmaker_return"):
        payout = Decimal(str(settlement["bookmaker_return"]["payout_eur"]))
    elif outcome == "WON":
        for leg in settlement["legs"]:
            if leg["outcome"] == "VOID":
                price /= Decimal(str(leg["decimal_price"]))
        payout = Decimal(str(_money(stake * price)))
    else:
        payout = stake if outcome == "VOID" else Decimal(0)
    settled = outcome in TERMINAL
    return {
        "stake_eur": _money(stake),
        "payout_eur": _money(payout),
        "net_eur": _money(payout - stake) if settled else 0.0,
        "open_stake_eur": 0.0 if settled else _money(stake),
        "cash_flow_eur": _money(payout - stake),
        "settled": settled,
        "basis": "MANUALLY_RECORDED" if wager else "RESEARCH_SIMULATION",
    }


def _summary(tickets):
    if any(ticket.get("wager") for ticket in tickets):
        return _recorded_summary(tickets)
    result = {
        "tickets": len(tickets),
        "won": 0,
        "lost": 0,
        "void": 0,
        "pending": 0,
        "review": 0,
        "stake_eur": 0.0,
        "settled_stake_eur": 0.0,
        "payout_eur": 0.0,
        "net_eur": 0.0,
        "open_stake_eur": 0.0,
        "cash_flow_eur": 0.0,
    }
    running = peak = drawdown = Decimal(0)
    ordered = sorted(
        tickets, key=lambda t: ((t.get("settlement") or {}).get("settled_at", ""), t["ticket_id"])
    )
    for ticket in ordered:
        result[ticket["outcome"].lower()] += 1
        financial = ticket["financial"]
        for key in ("stake_eur", "payout_eur", "net_eur", "open_stake_eur", "cash_flow_eur"):
            result[key] = _money(Decimal(str(result[key])) + Decimal(str(financial[key])))
        if financial["settled"]:
            result["settled_stake_eur"] = _money(
                Decimal(str(result["settled_stake_eur"])) + Decimal(str(financial["stake_eur"]))
            )
            running += Decimal(str(financial["net_eur"]))
            peak = max(peak, running)
            drawdown = max(drawdown, peak - running)
    decided = result["won"] + result["lost"]
    result.update(
        win_rate=result["won"] / decided if decided else None,
        yield_pct=round(result["net_eur"] / result["settled_stake_eur"] * 100, 2)
        if result["settled_stake_eur"]
        else None,
        max_drawdown_eur=_money(drawdown),
        drawdown_basis="settlement_observation_order",
    )
    return result


Ledger = PortfolioLedger


def _recorded_summary(tickets):
    contracts = {(ticket.get("financial") or {}).get("monetary_contract") for ticket in tickets}
    contract = "LEGACY_RECORDED_V1" if contracts == {"LEGACY_RECORDED_V1"} else "CURRENT_HASH_V2"
    projection = project_recorded_cohort(tickets, contract=contract,
        allow_estimated_price_arithmetic=contract == "LEGACY_RECORDED_V1")
    outcomes = Counter(ticket.get("outcome", "PENDING") for ticket in tickets)
    result = {"tickets": len(tickets), **{name.lower(): outcomes[name] for name in
        ("WON", "LOST", "VOID", "PENDING", "REVIEW")}}
    for name in ("stake_eur", "settled_stake_eur", "payout_eur", "net_eur",
                 "open_stake_eur", "cash_flow_eur"):
        result[name] = projection[name]
    decided = outcomes["WON"] + outcomes["LOST"]
    result["win_rate"] = outcomes["WON"] / decided if decided else None
    result["yield_pct"] = round(projection["yield_pct"], 2) if projection["yield_pct"] is not None else None
    projected = {row["ticket_id"]: row for row in projection["records"]}
    ordered = sorted(tickets, key=lambda ticket: (
        (ticket.get("settlement") or {}).get("settled_at", ""), ticket["ticket_id"]))
    running = peak = drawdown = Decimal(0)
    seen = set()
    for ticket in ordered:
        key = ticket["ticket_id"]
        row = projected.get(key)
        if key in seen or not row or row["status"] != "KNOWN":
            continue
        seen.add(key)
        running += Decimal(str(row["net_eur"]))
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    result.update(max_drawdown_eur=_money(drawdown) if projection["complete_financial_coverage"] else None,
                  known_subset_max_drawdown_eur=_money(drawdown),
                  known_subset_net_eur=projection["known_subset_net_eur"],
                  drawdown_basis="settlement_observation_order",
                  monetary_projection=projection,
                  bank_cash_receipt_eur=None, after_cost_system_net_eur=None)
    return result
