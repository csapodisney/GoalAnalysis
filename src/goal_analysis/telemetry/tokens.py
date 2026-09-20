from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any


def summarize_token_usage(kerekasztal_run: Mapping[str, Any]) -> dict[str, Any]:
    """Aggregate provider-reported tokens without inventing monetary cost."""

    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    by_role: dict[str, dict[str, int]] = defaultdict(_empty_totals)
    by_model: dict[str, dict[str, int]] = defaultdict(_empty_totals)
    call_count = 0
    calls_with_usage = 0

    for fixture in kerekasztal_run.get("fixtures", []):
        opinions = [*fixture.get("specialists", []), fixture.get("daniel"), fixture.get("arthur")]
        for opinion in (item for item in opinions if item):
            call_count += 1
            metadata = opinion.get("run_metadata") or {}
            role = opinion.get("role", "unknown")
            model = metadata.get("model") or "unknown"
            values = {
                key: metadata.get(key) if isinstance(metadata.get(key), int) else 0
                for key in totals
            }
            if any(values.values()):
                calls_with_usage += 1
            for key, value in values.items():
                totals[key] += value
                by_role[role][key] += value
                by_model[model][key] += value

    return {
        "schema_version": 1,
        "record_type": "token_usage_summary",
        "call_count": call_count,
        "calls_with_usage": calls_with_usage,
        "calls_missing_usage": call_count - calls_with_usage,
        "totals": totals,
        "by_role": dict(sorted(by_role.items())),
        "by_model": dict(sorted(by_model.items())),
        "monetary_cost": None,
        "cost_note": "No cost inferred without a versioned price table.",
    }


def _empty_totals() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
