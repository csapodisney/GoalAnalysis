"""Review a saved whole package separately; never alter its recorded tickets."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goal_analysis.jobs.grok_advisory import analyze, snapshot


def main():
    parser = argparse.ArgumentParser(description="Grok: a teljes mentett szelvénycsomag véleményezése")
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        result = snapshot(ROOT, args.date.isoformat())
    else:
        ledger = ROOT / "data/arthur/ledger.sqlite3"
        with sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute("SELECT snapshot_json FROM portfolio_runs WHERE date=? "
                             "ORDER BY observed_at DESC LIMIT 1", (args.date.isoformat(),)).fetchone()
        if row is None:
            result = {"status": "NO_SAVED_REPORT"}
        else:
            result = analyze(ROOT, json.loads(row[0]))
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0 if result.get("status") not in {"UNAVAILABLE", "ERROR"} else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001 - never log credentials or provider bodies.
        print('{"status":"UNAVAILABLE","error_code":"GROK_RUN_FAILED"}')
        raise SystemExit(2) from None
