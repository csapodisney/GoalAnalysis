"""Start the local Arthur DAILY_223 control panel."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from goal_analysis.dashboard import serve_dashboard


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    try:
        serve_dashboard(PROJECT_ROOT, args.host, args.port, args.open_browser)
    except (OSError, ValueError) as error:
        print(f"A vezérlőpult nem indítható: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
