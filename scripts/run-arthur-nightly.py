"""Scheduled nightly work or explicit bounded reuse of a completed normal run."""
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
    mode.add_argument('--recover-run', metavar='EXACT_RUN_ID')
    parser.add_argument('--expected-snapshot-sha256')
    parser.add_argument('--expected-file-sha256')
    parser.add_argument("--date", type=date.fromisoformat)
    args = parser.parse_args(argv)
    if args.date and not args.run_now:
        parser.error("--date requires --run-now")
    if args.recover_run and (not args.expected_snapshot_sha256 or not args.expected_file_sha256):
        parser.error('--recover-run requires both immutable snapshot and physical file SHA256')
    if not args.recover_run and (args.expected_snapshot_sha256 or args.expected_file_sha256):
        parser.error('Recovery hashes require --recover-run')
    try:
        if args.recover_run:
            from goal_analysis.jobs.nightly_recovery import recover_existing

            result = recover_existing(ROOT, run_id=args.recover_run,
                expected_snapshot_hash=args.expected_snapshot_sha256, expected_file_hash=args.expected_file_sha256)
        else:
            result = automation_snapshot(ROOT) if args.check_schedule else NightlyWorkflow(ROOT).run(
                scheduled=args.scheduled, target_date=args.date,
            )
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if args.check_schedule or result["status"] in {
            "NOT_DUE", "ALREADY_FINISHED", "COMPLETED", "BUSY"
        } or args.recover_run and result['status'] in {'ALREADY_RECOVERED', 'COMPLETED_WITH_WARNINGS'} else 2
    except Exception as error:  # noqa: BLE001 - no raw secrets, provider responses or tracebacks
        reason, retryable = failure_reason(error)
        print(json.dumps({"status": "ERROR", "reason": reason, "retryable": retryable}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
