"""Operate the local supervisor; no model request or wager is made here."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goal_analysis.jobs.portfolio import RunLock
from goal_analysis.learning import LearningAgent
from goal_analysis.team.manager import TeamManager
from goal_analysis.team.scouting import Scout


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="A Szent Grál – helyi csapatfelügyelet")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot", action="store_true", help="Read-only local summary, no network")
    mode.add_argument("--review", action="store_true", help="Daily idempotent team evaluation, no AI")
    mode.add_argument("--scout", action="store_true", help="Weekly bounded public GitHub metadata scan")
    args = parser.parse_args()
    try:
        manager = TeamManager(ROOT)
        if args.snapshot:
            result = manager.snapshot()
            output = {"name": result["name"], "mode": result["mode"], "started_at": result["started_at"],
                      "evaluation_id": (result["latest_evaluation"] or {}).get("evaluation_id"),
                      "members": [{k: m.get(k) for k in ("profile_id", "status", "reason_code")}
                                  for m in result["members"]], "automatic_wager_changes": False}
        else:
            with RunLock(ROOT / "data/arthur/run.lock"):
                now = datetime.now(UTC)
                result = manager.evaluate(now, learning_report=LearningAgent(ROOT).latest()) if args.review else Scout(ROOT).run(now)
            output = {key: result.get(key) for key in (
                "evaluation_id", "scout_id", "date", "as_of", "status", "snapshot_hash",
                "request_count", "automatic_wager_changes",
            ) if key in result}
            if args.scout:
                output["candidates"] = [{"repository": c.get("repository", {}).get("name"),
                                         "stage": c.get("onboarding_stage"), "runnable": c.get("runnable")}
                                        for c in result.get("candidates", [])]
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if result.get("status") not in {"UNAVAILABLE", "INTERRUPTED", "PARTIAL"} else 2
    except Exception as error:  # noqa: BLE001 - only a safe type, no provider paths or response bodies
        print(json.dumps({"status": "FAILED", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
