from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from goal_analysis.agents import KerekasztalError, KerekasztalOrchestrator, Role


class FileRoleRunner:
    """Replay structured role responses without making external calls."""

    def __init__(self, responses: Mapping[str, Any]) -> None:
        self.responses = responses

    def run(self, role: Role, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        fixture_id = payload["fixture_id"]
        try:
            return self.responses[fixture_id][role.value]
        except KeyError as error:
            raise KerekasztalError(
                f"missing response for fixture={fixture_id}, role={role.value}"
            ) from error


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a structured Kerekasztal shadow run")
    parser.add_argument("fact_packet_json")
    parser.add_argument("responses_json")
    parser.add_argument("--output", default="reports/daily/kerekasztal-shadow.json")
    args = parser.parse_args()

    try:
        fact_packet = json.loads(Path(args.fact_packet_json).read_text("utf-8"))
        responses = json.loads(Path(args.responses_json).read_text("utf-8"))
        result = KerekasztalOrchestrator(FileRoleRunner(responses)).run(fact_packet)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(output)
    except (OSError, json.JSONDecodeError, KerekasztalError, ValueError) as error:
        print(f"Kerekasztal shadow run failed: {error}")
        return 1

    selected = sum(item["final"]["selected"] for item in result["fixtures"])
    print(f"Kerekasztal shadow run: fixtures={len(result['fixtures'])}, selected={selected}")
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
