"""Lightweight existing-timer tick; never imports an Astra reviewer or generates tickets."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main(root=ROOT):
    from goal_analysis.config.daily_223_live import validate_live_config
    from goal_analysis.jobs.odds_monitor import MonitorPolicy, OddsMonitor, saved_monitor_input
    from goal_analysis.jobs.odds_monitor_providers import build_monitor_queries, fetch_monitor_query
    from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient

    now = datetime.now(UTC)
    root = Path(root)
    try:
        config = validate_live_config(json.loads((root / "config/daily223-live.json").read_text("utf-8-sig")))
        saved = saved_monitor_input(root, now)
        if not saved["fixtures"]:
            return {"status": "NO_UNLOCKED_FIXTURES", "provider_calls": 0, "astra_calls": 0}
        limits = {'the_stats_api': 2000}  # <=62,000/month; daily collection retains headroom.
        policy = MonitorPolicy(daily_credit_limits=limits, quota_reserve_limits={'the_stats_api': 1000},
                               max_requests_per_minute=30, max_queries_per_tick=12)
        queries = build_monitor_queries(saved, now, {**config, 'monitor_providers': ['the_stats_api']})
        if queries and not os.environ.get('THE_STATS_API_KEY', '').strip():
            return {'status': 'MONITOR_UNAVAILABLE', 'reason': 'THE_STATS_CREDENTIAL_UNAVAILABLE',
                    'provider_calls': 0, 'astra_calls': 0}
        football = TheStatsPrimaryClient(root, max_requests=12) if queries else None
        return OddsMonitor(root, policy=policy).tick(
            queries, lambda query: fetch_monitor_query(query, football_client=football,
                                                       now=datetime.now(UTC)),
        )
    except Exception:  # noqa: BLE001 - no provider bodies, private paths or credentials
        return {"status": "MONITOR_UNAVAILABLE", "provider_calls": None, "astra_calls": 0}


def process_exit_code(result):
    """Operational failure only; optional shadow and legitimate no-ops stay separate."""
    return 0 if isinstance(result, dict) and isinstance(result.get("status"), str) and result.get("status") in {
        "UPDATED", "NO_CHANGE", "NO_UNLOCKED_FIXTURES",
    } else 1


if __name__ == "__main__":
    result = main()
    try:
        from goal_analysis.jobs.prospective_odds import tick

        result['prospective_shadow'] = tick(ROOT)
    except Exception:  # noqa: BLE001 - optional first observations cannot veto daily monitoring
        result['prospective_shadow'] = {'status':'PROSPECTIVE_UNAVAILABLE','llm_calls':0}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    sys.exit(process_exit_code(result))
