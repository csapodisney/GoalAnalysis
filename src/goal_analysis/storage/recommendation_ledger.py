"""Immutable recommendations, including unpriced ideas, in the existing local SQLite DB."""

import json
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date as Date
from zoneinfo import ZoneInfo


class RecommendationLedger:
    def _init_recommendations(self, db):
        db.executescript("""
            CREATE TABLE IF NOT EXISTS portfolio_recommendations (
                recommendation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES portfolio_runs,
                date TEXT NOT NULL, profile_id TEXT NOT NULL, created_at TEXT NOT NULL,
                snapshot_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
            CREATE INDEX IF NOT EXISTS recommendations_day ON portfolio_recommendations(date,profile_id);
            CREATE TABLE IF NOT EXISTS recommendation_imports (run_id TEXT PRIMARY KEY REFERENCES portfolio_runs);
        """)
        # Existing v3.4 previews were already frozen inside run snapshots. Import them
        # using their original publication time, without generating retrospective picks.
        for row in db.execute(
            "SELECT snapshot_json,saved_at FROM portfolio_runs WHERE run_id NOT IN (SELECT run_id FROM recommendation_imports) ORDER BY observed_at,rowid"
        ).fetchall():
            self._save_recommendations(db, json.loads(row[0]), row[1])

    def _save_recommendations(self, db, report, saved):
        from .portfolio_ledger import _hash, _json, _stamp

        records = report.get("recommendations")
        if records is None:
            records = report.get("tickets", []) + report.get("preview_tickets", [])
        cutoff = _stamp(report.get("finished_at", report["observed_at"]))
        for source in records:
            if not source.get("legs"):
                continue
            record = deepcopy(source)
            if record.get("ticket_id"):
                record.setdefault("portfolio_ticket_id", record.pop("ticket_id"))
            if any(_stamp(l["kickoff"]) <= cutoff for l in record["legs"]):
                continue
            if any(
                _stamp(l["kickoff"]).astimezone(ZoneInfo("Europe/Berlin")).date().isoformat()
                != report["date"]
                for l in record["legs"]
            ):
                raise ValueError("Recommendation fixture date differs from run date")
            if len({l["fixture_id"] for l in record["legs"]}) != len(record["legs"]):
                raise ValueError("Recommendation fixtures must be distinct")
            profile = record["profile_id"]
            record.update(
                recommendation_id=_hash([report["run_id"], profile])[:24],
                date=report["date"],
                run_id=report["run_id"],
                created_at=cutoff.isoformat(),
            )
            previous = db.execute(
                "SELECT recommendation_id,created_at,snapshot_json FROM portfolio_recommendations WHERE date=? AND profile_id=? AND active=1",
                (report["date"], profile),
            ).fetchall()
            active = True
            for old in previous:
                old_snapshot = json.loads(old["snapshot_json"])
                if _stamp(old["created_at"]) > cutoff or any(
                    _stamp(l["kickoff"]) <= cutoff for l in old_snapshot["legs"]
                ):
                    active = False
                else:
                    db.execute(
                        "UPDATE portfolio_recommendations SET active=0 WHERE recommendation_id=?",
                        (old["recommendation_id"],),
                    )
            db.execute(
                "INSERT OR IGNORE INTO portfolio_recommendations VALUES (?,?,?,?,?,?,?)",
                (
                    record["recommendation_id"],
                    report["run_id"],
                    record["date"],
                    profile,
                    record["created_at"],
                    _json(record),
                    int(active),
                ),
            )
        db.execute("INSERT OR IGNORE INTO recommendation_imports VALUES (?)", (report["run_id"],))

    def list_recommendations(self, date=None, *, archive=False):
        from .portfolio_ledger import _leg_result

        day = Date.fromisoformat(str(date)).isoformat() if date is not None else None
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM portfolio_recommendations WHERE (? IS NULL OR date=?) AND (? OR active=1) ORDER BY date DESC,created_at DESC,rowid DESC",
                (day, day, int(archive)),
            ).fetchall()
            output = []
            for row in rows:
                record = json.loads(row["snapshot_json"])
                legs = [
                    {
                        "fixture_id": l["fixture_id"],
                        "market_key": l["market_key"],
                        "selection_key": l["selection_key"],
                        "period": l["period"],
                        **_leg_result(l, self._latest_result(db, l["fixture_id"])),
                    }
                    for l in record["legs"]
                ]
                statuses = {l["outcome"] for l in legs}
                outcome = (
                    "LOST"
                    if "LOST" in statuses
                    else "REVIEW"
                    if "REVIEW" in statuses
                    else "PENDING"
                    if "PENDING" in statuses
                    else "WON"
                )
                record.update(
                    active=bool(row["active"]),
                    outcome=outcome,
                    settlement={"outcome": outcome, "legs": legs},
                    played=False,
                    financial=None,
                )
                output.append(record)
        return output

    def recommendation_performance(self, month=None):
        if month is not None and Date.fromisoformat(month + "-01").strftime("%Y-%m") != month:
            raise ValueError("month must be YYYY-MM")
        records = [
            r for r in self.list_recommendations() if month is None or r["date"].startswith(month)
        ]

        def summary(items):
            counts = Counter(r["outcome"] for r in items)
            won, lost = counts["WON"], counts["LOST"]
            return {
                "tickets": len(items),
                "won": won,
                "lost": lost,
                "pending": counts["PENDING"],
                "review": counts["REVIEW"],
                "hit_rate_pct": round(100 * won / (won + lost), 2) if won + lost else None,
            }

        profiles, days = defaultdict(list), defaultdict(list)
        for r in records:
            profiles[r["profile_id"]].append(r)
            days[r["date"]].append(r)
        return {
            "mode": "recommendations",
            "month": month,
            "summary": summary(records),
            "by_profile": [{"profile_id": p, **summary(rs)} for p, rs in profiles.items()],
            "series": [{"date": d, **summary(rs)} for d, rs in sorted(days.items())],
            "api_usage": self._usage(month),
            "records": records,
            "net_definition": "Outcome tracking only. No assumed stakes or invented prices.",
            "counting_basis": "Latest recommendation before kickoff per profile/day; started recommendations stay frozen. Earlier and subsequent versions remain archived.",
        }
