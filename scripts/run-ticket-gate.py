from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from goal_analysis.engine import TicketGateError, TicketGatePolicy, evaluate_ticket
from goal_analysis.providers import CsvOddsProvider


def main() -> int:
    parser = argparse.ArgumentParser(description="Attach post-ranking prices in shadow mode")
    parser.add_argument("kerekasztal_json")
    parser.add_argument("odds_csv")
    parser.add_argument("--output", default="reports/daily/ticket-gate-shadow.json")
    parser.add_argument("--max-legs", type=int, default=6)
    parser.add_argument("--max-quote-age", type=int, default=600)
    parser.add_argument("--target-price", type=float, default=None)
    args = parser.parse_args()

    try:
        kerekasztal = json.loads(Path(args.kerekasztal_json).read_text("utf-8"))
        selected = [item for item in kerekasztal["fixtures"] if item["final"]["selected"]]
        fixture_ids = [item["fixture_id"] for item in selected]
        market_keys = sorted(
            {
                item["arthur"].get("market_key")
                for item in selected
                if item["arthur"].get("market_key")
            }
        )
        observed_at = datetime.now(UTC)
        quotes = CsvOddsProvider(Path(args.odds_csv)).get_quotes(
            fixture_ids, market_keys, observed_at
        )
        result = evaluate_ticket(
            kerekasztal,
            quotes,
            observed_at,
            TicketGatePolicy(args.max_legs, args.max_quote_age, args.target_price),
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        temporary.replace(output)
    except (OSError, json.JSONDecodeError, TicketGateError, ValueError) as error:
        print(f"Ticket gate failed: {error}")
        return 1

    print(
        f"Ticket gate: ready={result['ticket_ready']}, "
        f"legs={len(result['legs'])}, combined_price={result['combined_price']}"
    )
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
