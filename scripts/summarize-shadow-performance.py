from __future__ import annotations

import argparse
import json
from pathlib import Path

from goal_analysis.telemetry import (
    AuditLogError,
    HashChainAuditLog,
    summarize_shadow_performance,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize verified shadow performance")
    parser.add_argument("--audit-log", default="logs/performance.jsonl")
    parser.add_argument("--output", default="reports/telemetry/shadow-performance.json")
    args = parser.parse_args()

    try:
        entries = list(HashChainAuditLog(Path(args.audit_log)).read())
        summary = summarize_shadow_performance(entry["record"] for entry in entries)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2), "utf-8"
        )
        temporary.replace(output)
    except (OSError, AuditLogError, ValueError) as error:
        print(f"Performance summary failed: {error}")
        return 1

    print(
        f"Shadow performance: tickets={summary['ticket_count']}, "
        f"net_units={summary['net_units']}"
    )
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
