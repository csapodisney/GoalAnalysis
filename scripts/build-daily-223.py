"""Build one independent DAILY_223 research report from an identified evidence snapshot."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from goal_analysis.engine.daily_223 import Daily223Policy, build_daily_223


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text("utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("expected input schema_version 1")
        if not isinstance(payload["candidates"], list):
            raise TypeError("candidates must be a list")
        result = build_daily_223(
            payload["candidates"],
            date.fromisoformat(payload["date"]),
            datetime.fromisoformat(payload["observed_at"]),
            Daily223Policy(**payload.get("policy", {})),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Refuse to overwrite a published research decision.
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"DAILY_223 failed: {error}")
        return 1
    print(
        f"DAILY_223: {result['construction_status']}, legs={len(result['legs'])}, "
        f"price={result['combined_price']}, band={result['odds_band']}"
    )
    print("RESEARCH_ONLY; real_wager_placed=false")
    return 0 if result["construction_status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
