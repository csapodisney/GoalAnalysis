"""Compose the independent history evidence layer and the DAILY_223 constructor."""

from datetime import date

from goal_analysis.agents import canonical_sha256
from goal_analysis.engine.daily_223 import Daily223Policy, build_daily_223
from goal_analysis.features.daily_223_history import enrich_daily223_input
from goal_analysis.providers.api_football_history import aware_time


def build_daily223_from_history(payload: dict, history: dict) -> dict:
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError("candidate input schema_version 1 required")
    enriched = enrich_daily223_input(payload, history)
    report = build_daily_223(
        enriched["candidates"],
        date.fromisoformat(enriched["date"]),
        aware_time(enriched["observed_at"]),
        Daily223Policy(**enriched.get("policy", {})),
    )
    report["history_analysis"] = enriched["history_analysis"]
    del report["report_sha256"]
    report["report_sha256"] = canonical_sha256(report)
    return report
