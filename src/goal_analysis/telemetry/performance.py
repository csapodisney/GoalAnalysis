from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


def summarize_shadow_performance(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    settlements = [
        record for record in records if record.get("record_type") == "shadow_ticket_settlement"
    ]
    statuses = Counter(record.get("ticket_status", "unknown") for record in settlements)
    stake_units = sum(float(record.get("stake_units", 0)) for record in settlements)
    return_units = sum(float(record.get("return_units", 0)) for record in settlements)
    net_units = return_units - stake_units
    return {
        "schema_version": 1,
        "record_type": "shadow_performance_summary",
        "ticket_count": len(settlements),
        "status_counts": dict(sorted(statuses.items())),
        "stake_units": round(stake_units, 6),
        "return_units": round(return_units, 6),
        "net_units": round(net_units, 6),
        "roi_units": round(net_units / stake_units, 6) if stake_units else None,
        "scope": "research_shadow_only",
    }
