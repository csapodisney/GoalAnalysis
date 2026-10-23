"""Entrypoint for the existing Windows task: no API or AI call outside the due window."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goal_analysis.jobs.nightly import NightlyWorkflow, automation_snapshot, failure_reason


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Arthur · 22:00 Europe/Berlin napi ciklus")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scheduled", action="store_true")
    mode.add_argument("--run-now", action="store_true")
    mode.add_argument("--check-schedule", action="store_true")
    parser.add_argument("--date", type=date.fromisoformat)
    args = parser.parse_args(argv)
    if args.date and not args.run_now:
        parser.error("--date requires --run-now")
    try:
        result = automation_snapshot(ROOT) if args.check_schedule else NightlyWorkflow(ROOT).run(
            scheduled=args.scheduled, target_date=args.date,
        )
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if args.check_schedule or result["status"] in {
            "NOT_DUE", "ALREADY_FINISHED", "COMPLETED", "BUSY"
        } else 2
    except Exception as error:  # noqa: BLE001 - no raw secrets, provider responses or tracebacks
        reason, retryable = failure_reason(error)
        print(json.dumps({"status": "ERROR", "reason": reason, "retryable": retryable}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
