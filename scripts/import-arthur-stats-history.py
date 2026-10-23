"""Explicit bounded import. Without --execute, only create/show the frozen plan."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goal_analysis.jobs.the_stats_history import (
    load_plan,
    prepare_plan,
    run_history,
    save_plan,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--plan-file", type=Path, required=True)
    parser.add_argument("--competition", action="append", default=[], help="Exact TheStatsAPI comp_ ID")
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--years", type=int, default=5,
                        help="History depth for a new plan (default: 5 years, newest first; today is handled by daily collection)")
    parser.add_argument("--max-requests", type=int, default=20)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--daily-work-complete", action="store_true")
    args = parser.parse_args()
    if args.plan_file.exists():
        if args.competition or args.date_from or args.date_to:
            parser.error("A saved plan is immutable; resume with --plan-file alone.")
        plan = load_plan(args.plan_file)
    else:
        if not args.competition:
            parser.error("A new plan requires explicit --competition comp_ IDs.")
        plan = prepare_plan(args.competition, years=args.years,
                            date_from=args.date_from, date_to=args.date_to)
        save_plan(args.plan_file, plan)
    result = run_history(args.root, plan, execute=args.execute,
                         daily_work_complete=args.daily_work_complete,
                         max_requests=args.max_requests)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] not in {"PROVIDER_STOPPED", "INVALID_PAGE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
