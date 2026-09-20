import json

import pytest

from goal_analysis.telemetry import (
    AuditLogError,
    HashChainAuditLog,
    summarize_shadow_performance,
    summarize_token_usage,
)


def opinion(role, model, input_tokens, output_tokens):
    return {
        "role": role,
        "run_metadata": {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }


def test_token_usage_is_aggregated_by_role_and_model() -> None:
    run = {
        "fixtures": [
            {
                "specialists": [opinion("kronikas", "model-a", 100, 20)],
                "daniel": opinion("daniel", "model-b", 150, 30),
                "arthur": opinion("arthur", "model-b", 200, 40),
            }
        ]
    }

    result = summarize_token_usage(run)

    assert result["call_count"] == 3
    assert result["totals"] == {
        "input_tokens": 450,
        "output_tokens": 90,
        "total_tokens": 540,
    }
    assert result["by_model"]["model-b"]["total_tokens"] == 420
    assert result["monetary_cost"] is None


def test_missing_usage_is_reported_not_invented() -> None:
    run = {
        "fixtures": [
            {
                "specialists": [{"role": "kronikas"}],
                "daniel": None,
                "arthur": None,
            }
        ]
    }

    result = summarize_token_usage(run)

    assert result["calls_missing_usage"] == 1
    assert result["totals"]["total_tokens"] == 0


def test_hash_chain_detects_tampering(tmp_path) -> None:
    path = tmp_path / "performance.jsonl"
    log = HashChainAuditLog(path)
    first = log.append({"ticket_status": "win"})
    second = log.append({"ticket_status": "loss"})

    assert first["previous_sha256"] == "0" * 64
    assert second["previous_sha256"] == first["entry_sha256"]
    assert log.verify() == 2

    lines = path.read_text("utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["record"]["ticket_status"] = "loss"
    lines[0] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n", "utf-8")

    with pytest.raises(AuditLogError, match="hash mismatch"):
        HashChainAuditLog(path).verify()


def test_shadow_performance_is_kept_separate_and_uses_units() -> None:
    records = [
        {
            "record_type": "shadow_ticket_settlement",
            "ticket_status": "win",
            "stake_units": 1.0,
            "return_units": 3.0,
        },
        {
            "record_type": "shadow_ticket_settlement",
            "ticket_status": "loss",
            "stake_units": 1.0,
            "return_units": 0.0,
        },
        {"record_type": "unrelated"},
    ]

    summary = summarize_shadow_performance(records)

    assert summary["ticket_count"] == 2
    assert summary["status_counts"] == {"loss": 1, "win": 1}
    assert summary["net_units"] == 1.0
    assert summary["roi_units"] == 0.5
    assert summary["scope"] == "research_shadow_only"
