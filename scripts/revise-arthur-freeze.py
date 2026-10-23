"""Explicit prospective package correction; never places a wager or overwrites a freeze."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goal_analysis.storage.portfolio_ledger import PortfolioLedger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-snapshot-hash", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    path = ROOT / "data/arthur/ledger.sqlite3"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT date FROM portfolio_runs WHERE run_id=?", (args.run_id,)).fetchone()
    if row is None or row[0] != args.date.isoformat():
        parser.error("The saved run must belong to the explicitly requested target date.")
    ledger = PortfolioLedger(path)
    result = ledger.revise_freeze(args.run_id, args.expected_snapshot_hash, args.reason)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
