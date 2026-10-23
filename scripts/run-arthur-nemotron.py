"""Review one saved artifact without rerunning sports/Astra or changing any ticket."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goal_analysis.jobs.nemotron import analyze
from goal_analysis.jobs.portfolio import RunLock
from goal_analysis.learning import LearningAgent
from goal_analysis.storage.portfolio_ledger import _hash


def main(argv=None):
    parser = argparse.ArgumentParser(description="Arthur · Codex kiegészítő elemzés (örökölt indítónév)")
    choose = parser.add_mutually_exclusive_group(required=True)
    choose.add_argument("--run-id", help="An existing immutable report ID")
    choose.add_argument("--learning", action="store_true", help="Review the latest saved learning report")
    args = parser.parse_args(argv)
    try:
        with RunLock(ROOT / "data/arthur/run.lock"):
            if args.learning:
                report = LearningAgent(ROOT).latest()
            else:
                uri = (ROOT / "data/arthur/ledger.sqlite3").as_uri() + "?mode=ro"
                with sqlite3.connect(uri, uri=True) as db:
                    row = db.execute("SELECT snapshot_json,snapshot_hash FROM portfolio_runs WHERE run_id=?", (args.run_id,)).fetchone()
                report = json.loads(row[0]) if row else None
                if row and _hash(report) != row[1]:
                    raise ValueError("Stored report integrity failure")
            if not report:
                print(json.dumps({"status": "NOT_FOUND", "automatic_changes": False}))
                return 2
            result = analyze(ROOT, report, scope="learning" if args.learning else "report")
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result.get("status") in {"COMPLETE", "SKIPPED_NO_DATA"} else 2
    except Exception:  # noqa: BLE001 - never expose credentials, raw helper output or tracebacks
        print(json.dumps({"status": "UNAVAILABLE", "message": "Az elemzés nem indítható vagy nem menthető."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
