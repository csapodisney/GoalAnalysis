"""Launch the unified local Arthur dashboard."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goal_analysis.portfolio_dashboard import serve


def main():
    parser = argparse.ArgumentParser(description="Arthur dashboard")
    parser.add_argument("--config", type=Path, default=ROOT / "config/daily223-live.json")
    parser.add_argument("--settings", type=Path, default=ROOT / "config/arthur-settings.json")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    serve(ROOT, args.config, args.settings, args.port, args.open)


if __name__ == "__main__":
    main()
