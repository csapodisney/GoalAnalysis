"""One-time idempotent local weather/news backfill; no API requests."""
import json
from pathlib import Path

from goal_analysis.jobs.context_backfill import backfill_context

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    result = backfill_context(root)
    output = root / 'data/arthur/context-backfill.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
