from __future__ import annotations

import argparse
from pathlib import Path

from goal_analysis.agents import KerekasztalError, load_master_prompt


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify an immutable Arthur master prompt")
    parser.add_argument("--root", default="config/prompts")
    parser.add_argument("--version", default="arthur-pentagram-v2")
    args = parser.parse_args()

    try:
        artifact = load_master_prompt(Path(args.root), args.version)
    except KerekasztalError as error:
        print(f"Master prompt verification failed: {error}")
        return 1

    print(f"Prompt version: {artifact.version}")
    print(f"SHA-256: {artifact.sha256}")
    print(f"Bytes: {artifact.size_bytes}")
    print(f"Status: {artifact.activation_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
