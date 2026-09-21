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
from collections import defaultdict
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from datetime import date as Date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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


class PortfolioLedger(RecommendationLedger):
    """A local ledger, with one SQLite connection/transaction per operation."""

    def __init__(
        self,
        path: str | Path,
        *,
        now: Callable[[], datetime] | None = None,
        max_daily_tickets: int = 5,
        refresh_call_limit: int = 20,
    ) -> None:
        if type(max_daily_tickets) is not int or not 1 <= max_daily_tickets <= 5:
            raise ValueError("Daily ticket cap must be between 1 and 5")
        if type(refresh_call_limit) is not int or not 1 <= refresh_call_limit <= 100:
            raise ValueError("Result refresh call limit must be between 1 and 100")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.now = now or (lambda: datetime.now(UTC))
        self.max_daily_tickets = max_daily_tickets
        self.refresh_call_limit = refresh_call_limit
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

    def save_run(self, report: dict[str, Any]) -> dict[str, Any]:
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
        with self._connection() as db:
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
        _amount(ticket.get("stake_eur", 5), "stake_eur")
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

    def list_tickets(
        self, date: str | Date | None = None, mode: str = "all"
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
            tickets = [self._ticket_view(db, row) for row in rows]
        return [
            t
            for t in tickets
            if (mode == "archive" or t["active"] or t["played"])
            and (mode != "actual" or t["played"])
            and (mode != "research" or not t["played"])
        ]

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM portfolio_tickets WHERE ticket_id=?", (ticket_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Unknown ticket_id")
            return self._ticket_view(db, row)

    def _ticket_view(self, db, row) -> dict[str, Any]:
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
        ticket["financial"] = _financial(ticket)
        return ticket

    def record_wager(
        self, ticket_id: str, stake_eur: float = 5, actual_combined_price: float | None = None
    ) -> dict[str, Any]:
        """Record the user's explicit pre-match confirmation; never place a bet."""
        stake = _amount(stake_eur, "stake_eur")
        if stake != stake.quantize(Decimal("0.01")):
            raise ValueError("stake_eur cannot have fractions of a cent")
        if stake != Decimal(5):
            raise ValueError("The agreed fixed stake is exactly 5 EUR per ticket")
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
        self, ticket_id: str, payout_eur: float, note: str
    ) -> dict[str, Any]:
        """Resolve a partial-void actual wager using its bookmaker-confirmed return.

        The original total accepted price cannot reconstruct accepted prices of
        individual legs. A confirmation is tied to the exact settlement revision.
        """
        amount = _amount(payout_eur, "payout_eur")
        if amount != amount.quantize(Decimal("0.01")) or amount < Decimal(5):
            raise ValueError(
                "The confirmed partial-void return must be at least the 5 EUR stake in cents"
            )
        if not isinstance(note, str) or len(note.strip()) < 5:
            raise ValueError("A bookmaker return confirmation note is required")
        stamp = _stamp(self.now()).isoformat()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            wager = db.execute(
                "SELECT 1 FROM portfolio_wagers WHERE ticket_id=?", (ticket_id,)
            ).fetchone()
            row = db.execute(
                "SELECT s.settlement_json,s.settlement_hash FROM portfolio_settlement_observations o "
                "JOIN portfolio_settlements s ON s.id=o.settlement_id "
                "WHERE o.ticket_id=? ORDER BY o.id DESC LIMIT 1",
                (ticket_id,),
            ).fetchone()
            if not wager or row is None:
                raise ValueError("No actual partially voided wager is awaiting confirmation")
            settlement = json.loads(row[0])
            if settlement["outcome"] != "WON" or not any(
                leg["outcome"] == "VOID" for leg in settlement["legs"]
            ):
                raise ValueError("No actual partially voided wager is awaiting confirmation")
            old = db.execute(
                "SELECT payout_eur,note FROM portfolio_bookmaker_returns "
                "WHERE ticket_id=? AND settlement_hash=?",
                (ticket_id, row[1]),
            ).fetchone()
            if old and (Decimal(old[0]) != amount or old[1] != note.strip()):
                raise ValueError("An immutable bookmaker return confirmation already exists")
            db.execute(
                "INSERT OR IGNORE INTO portfolio_bookmaker_returns VALUES (?,?,?,?,?)",
                (ticket_id, row[1], str(amount), stamp, note.strip()),
            )
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
        unsupported: set[str] = set()
        with self._connection() as db:
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
                    if not re.fullmatch(r"(?:api_football:)?[0-9]+", fixture_id):
                        unsupported.add(fixture_id)
                        continue
                    cached = self._latest_result(db, fixture_id)
                    checked = db.execute(
                        "SELECT observed_at FROM portfolio_refreshes WHERE fixture_id=? "
                        "ORDER BY id DESC LIMIT 1",
                        (fixture_id,),
                    ).fetchone()
                    if cached is not None and _leg_result(leg, cached)["outcome"] in TERMINAL:
                        age = now - _stamp(leg["kickoff"])
                        since_checked = now - _stamp(checked[0]) if checked else timedelta.max
                        if age > timedelta(hours=48) or since_checked < timedelta(hours=6):
                            continue
                    wanted[fixture_id] = (checked[0] if checked else "", _stamp(leg["kickoff"]))
        requests = sorted(wanted, key=lambda key: (wanted[key], key))
        calls, received, errors = 0, 0, []
        for fixture_id in requests[: self.refresh_call_limit]:
            calls += 1
            refresh_status = "OK"
            try:
                payload = football_client.request(
                    "fixtures", {"id": int(fixture_id.split(":")[-1])}
                )
                expected_id = int(fixture_id.split(":")[-1])
                matching = [
                    item
                    for item in payload.get("response", [])
                    if str(item.get("fixture", {}).get("id")) == str(expected_id)
                ]
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

    def performance(self, month: str | None = None, mode: str = "actual") -> dict[str, Any]:
        if mode == "recommendations":
            return self.recommendation_performance(month)
        if mode not in {"actual", "research"}:
            raise ValueError("performance mode must be actual, research or recommendations")
        if month is not None and (
            not re.fullmatch(r"\d{4}-\d{2}", month) or _day(month + "-01") is None
        ):
            raise ValueError("month must be YYYY-MM")
        tickets = [
            ticket
            for ticket in self.list_tickets(mode=mode)
            if month is None or ticket["date"].startswith(month)
        ]
        profiles, days = defaultdict(list), defaultdict(list)
        for ticket in tickets:
            profiles[ticket["profile_id"]].append(ticket)
            days[ticket["date"]].append(ticket)
        series, cumulative = [], Decimal(0)
        for day, items in sorted(days.items()):
            summary = _summary(items)
            cumulative += Decimal(str(summary["net_eur"]))
            series.append({"date": day, **summary, "cumulative_net_eur": _money(cumulative)})
        return {
            "mode": mode,
            "month": month,
            "currency": "EUR",
            "basis": "ticket_date",
            "summary": _summary(tickets),
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
        tickets = self.list_tickets()
        settled = [t for t in tickets if t["outcome"] in TERMINAL]
        settled.sort(key=lambda t: (t["date"], t["created_at"], t["ticket_id"]), reverse=True)
        return {
            "schema_version": 1,
            "generated_at": _stamp(self.now()).isoformat(),
            "actual": self.performance(mode="actual"),
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


def _financial(ticket: Mapping[str, Any]) -> dict[str, Any]:
    wager = ticket.get("wager")
    stake = Decimal(str(wager["stake_eur"] if wager else ticket.get("stake_eur", 5)))
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
