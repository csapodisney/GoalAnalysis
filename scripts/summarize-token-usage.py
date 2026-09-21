from __future__ import annotations

import argparse
import json
from pathlib import Path

from goal_analysis.telemetry import summarize_token_usage


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize Kerekasztal token usage")
    parser.add_argument("kerekasztal_json")
    parser.add_argument("--output", default="reports/telemetry/token-usage.json")
    args = parser.parse_args()

    try:
        run = json.loads(Path(args.kerekasztal_json).read_text("utf-8"))
        summary = summarize_token_usage(run)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2), "utf-8"
        )
        temporary.replace(output)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Token summary failed: {error}")
        return 1

    print(
        f"Token usage: calls={summary['call_count']}, "
        f"total_tokens={summary['totals']['total_tokens']}"
    )
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
