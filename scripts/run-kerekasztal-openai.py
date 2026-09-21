from __future__ import annotations

import argparse
import json
from pathlib import Path

from goal_analysis.agents import (
    KerekasztalError,
    KerekasztalOrchestrator,
    OpenAIResponsesRoleRunner,
    PromptRegistry,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Kerekasztal through OpenAI Responses")
    parser.add_argument("fact_packet_json")
    parser.add_argument("--output", default="reports/daily/kerekasztal-openai-shadow.json")
    parser.add_argument("--prompt-version", default="kerekasztal-v1")
    args = parser.parse_args()

    try:
        fact_packet = json.loads(Path(args.fact_packet_json).read_text("utf-8"))
        prompts = PromptRegistry(Path("config/prompts"), args.prompt_version)
        runner = OpenAIResponsesRoleRunner(prompts)
        result = KerekasztalOrchestrator(runner).run(fact_packet)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(output)
    except (OSError, json.JSONDecodeError, KerekasztalError, ValueError) as error:
        print(f"OpenAI Kerekasztal run failed: {error}")
        return 1

    usage = [
        opinion.get("run_metadata", {})
        for fixture in result["fixtures"]
        for opinion in [*fixture["specialists"], fixture["daniel"], fixture["arthur"]]
    ]
    total_tokens = sum(item.get("total_tokens") or 0 for item in usage)
    print(f"OpenAI Kerekasztal shadow run: fixtures={len(result['fixtures'])}")
    print(f"Reported total tokens: {total_tokens}")
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
