"""Shared durable entry for manual operator and fixed morning task, without settlement."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from goal_analysis.workshop_service import production_workshop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--morning', action='store_true')
    parser.add_argument('--date')
    parser.add_argument('--request-id')
    args = parser.parse_args()
    if args.morning and (args.date or args.request_id):
        parser.error('Morning uses only its actual Berlin day and fixed durable slot.')
    if not args.morning and not (args.date and args.request_id):
        parser.error('Manual generation needs an explicit date and request UUID.')
    service = production_workshop(ROOT, ROOT / 'config/arthur-settings.json')
    result = service.morning() if args.morning else service.start({
        'date': args.date, 'request_id': args.request_id, 'mode': 'TEN_AGENT_FULL_DAY'})
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    # Receipt means durable acceptance or an existing slot, never finished business.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
