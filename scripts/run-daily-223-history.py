"""Build an independent 2x2x3 research report using identified historical results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from goal_analysis.jobs.daily_223_history import build_daily223_from_history


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--history", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text("utf-8-sig"))
        history = json.loads(args.history.read_text("utf-8-sig"))
        if not isinstance(payload, dict) or not isinstance(history, dict):
            raise TypeError("input and history must be JSON objects")
        report = build_daily223_from_history(payload, history)
        serialized = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(serialized)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        print(f"DAILY_223 failed: {error}")
        return 1
    print(
        f"DAILY_223: {report['construction_status']}; legs={len(report['legs'])}; price={report['combined_price']}"
    )
    print("RESEARCH_ONLY; real_wager_placed=false")
    return 0 if report["construction_status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
