from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from goal_analysis.settlement import FinalScore, SettlementError, settle_ticket
from goal_analysis.telemetry import AuditLogError, HashChainAuditLog


def main() -> int:
    parser = argparse.ArgumentParser(description="Settle an unchanged shadow ticket")
    parser.add_argument("ticket_json")
    parser.add_argument("scores_csv")
    parser.add_argument("--output", default="reports/settlement/latest.json")
    parser.add_argument("--audit-log", default="logs/performance.jsonl")
    args = parser.parse_args()

    try:
        ticket = json.loads(Path(args.ticket_json).read_text("utf-8"))
        scores = _load_scores(Path(args.scores_csv))
        settlement = settle_ticket(ticket, scores, datetime.now(UTC))
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(settlement, ensure_ascii=False, sort_keys=True, indent=2), "utf-8"
        )
        temporary.replace(output)
        entry = HashChainAuditLog(Path(args.audit_log)).append(settlement)
    except (OSError, json.JSONDecodeError, ValueError, SettlementError, AuditLogError) as error:
        print(f"Settlement failed: {error}")
        return 1

    print(f"Settlement: status={settlement['ticket_status']}, net_units={settlement['net_units']}")
    print(f"Frozen ticket hash: {settlement['frozen_ticket_sha256']}")
    print(f"Audit entry hash: {entry['entry_sha256']}")
    return 0


def _load_scores(path: Path) -> tuple[FinalScore, ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        required = {"fixture_id", "home_goals", "away_goals", "settled_at"}
        missing = required - set(rows.fieldnames or ())
        if missing:
            raise ValueError(f"scores CSV missing columns: {', '.join(sorted(missing))}")
        return tuple(
            FinalScore(
                row["fixture_id"],
                int(row["home_goals"]),
                int(row["away_goals"]),
                datetime.fromisoformat(row["settled_at"]),
            )
            for row in rows
        )


if __name__ == "__main__":
    raise SystemExit(main())
