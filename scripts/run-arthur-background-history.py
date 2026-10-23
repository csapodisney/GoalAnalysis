"""Existing-timer background tick. No model calls or ticket writes."""
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

if __name__ == '__main__':
    from goal_analysis.jobs.background_history import tick

    try:
        result=tick(ROOT)
    except Exception:  # noqa: BLE001 - never log raw provider/persistence text
        result={'status':'BACKGROUND_HISTORY_UNAVAILABLE','requests':None,'llm_calls':0}
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
