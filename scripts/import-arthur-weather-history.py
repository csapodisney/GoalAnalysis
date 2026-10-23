"""Fetch a small ERA5 batch using archived forecast locations; no paid API calls."""

import argparse
import json
from pathlib import Path

from goal_analysis.jobs.weather_history import backfill_weather


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-requests", type=int, default=5)
    args = parser.parse_args()
    result = backfill_weather(Path(__file__).resolve().parents[1], max_requests=args.max_requests)
    print(json.dumps({key: value for key, value in result.items() if key != "items"},
                     ensure_ascii=True))


if __name__ == "__main__":
    main()
