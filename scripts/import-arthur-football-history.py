"""Import public archived results; never generate tickets or call paid providers."""

import argparse
import json
from pathlib import Path

from goal_analysis.jobs.football_data_archive import run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-year", type=int, default=2016)
    parser.add_argument("--to-year", type=int, default=2026)
    parser.add_argument("--max-downloads", type=int, default=11)
    args = parser.parse_args()
    result = run(Path(__file__).resolve().parents[1], range(args.from_year, args.to_year + 1),
                 max_downloads=args.max_downloads)
    print(json.dumps({"requests": result["downloads"]["requests"],
                      "summary": result["import"]["summary"],
                      "failed_files": [x for x in result["import"]["files"] if x["status"] != "IMPORTED"]},
                     ensure_ascii=True))


if __name__ == "__main__":
    main()
