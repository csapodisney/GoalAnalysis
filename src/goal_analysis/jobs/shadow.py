from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from goal_analysis.agents import (
    KerekasztalOrchestrator,
    RoleRunner,
    build_fact_packet,
    canonical_sha256,
)
from goal_analysis.engine import TicketGatePolicy, evaluate_ticket
from goal_analysis.providers import OddsProvider
from goal_analysis.telemetry import summarize_token_usage


def complete_shadow_run(
    screening: Mapping[str, Any],
    role_runner: RoleRunner,
    odds_provider: OddsProvider,
    observed_at: datetime,
    gate_policy: TicketGatePolicy | None = None,
) -> dict[str, Any]:
    """Join the post-screening stages without permitting side effects or real wagers."""

    fact_packet = build_fact_packet(screening)
    kerekasztal = KerekasztalOrchestrator(role_runner).run(fact_packet)
    selected = [item for item in kerekasztal["fixtures"] if item["final"]["selected"]]
    fixture_ids = [item["fixture_id"] for item in selected]
    market_keys = sorted(
        {
            item["arthur"].get("market_key")
            for item in selected
            if item["arthur"].get("market_key")
        }
    )
    quotes = (
        odds_provider.get_quotes(fixture_ids, market_keys, observed_at)
        if fixture_ids and market_keys
        else ()
    )
    ticket_gate = evaluate_ticket(kerekasztal, quotes, observed_at, gate_policy)
    token_usage = summarize_token_usage(kerekasztal)
    artifacts = {
        "screening": dict(screening),
        "fact_packet": fact_packet,
        "kerekasztal": kerekasztal,
        "ticket_gate": ticket_gate,
        "token_usage": token_usage,
    }
    return {
        "schema_version": 1,
        "run_type": "end_to_end_shadow",
        "observed_at": observed_at.isoformat(),
        "real_wager_placed": False,
        "artifacts": artifacts,
        "artifact_sha256": {
            name: canonical_sha256(value) for name, value in artifacts.items()
        },
    }


def write_shadow_bundle(root: Path, bundle: Mapping[str, Any]) -> None:
    destination = Path(root)
    destination.mkdir(parents=True, exist_ok=True)
    for name, payload in bundle["artifacts"].items():
        _write_json(destination / f"{name}.json", payload)
    manifest = {key: value for key, value in bundle.items() if key != "artifacts"}
    _write_json(destination / "manifest.json", manifest)


def _write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )
    temporary.replace(path)
