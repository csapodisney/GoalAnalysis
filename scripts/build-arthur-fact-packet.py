from __future__ import annotations

import argparse
import json
from pathlib import Path

from goal_analysis.agents import FactPacketError, build_fact_packet, write_fact_packet


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a compact odds-free Arthur fact packet")
    parser.add_argument("screening_json")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    source = Path(args.screening_json)
    output = Path(args.output or source.with_name(f"arthur-{source.name}"))
    try:
        screening = json.loads(source.read_text("utf-8"))
        packet = build_fact_packet(screening)
        write_fact_packet(output, packet)
    except (OSError, json.JSONDecodeError, FactPacketError) as error:
        print(f"Fact packet failed: {error}")
        return 1

    print(f"Arthur fact packet: fixtures={packet['fixture_count']}")
    print(f"Frozen ranking: {', '.join(packet['ranking_order'])}")
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
