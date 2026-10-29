"""TESTONLY exact scenario packing and mocked wire, with no external calls."""
import hashlib
import json
import socket
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from test_astra_review import KEY, Transport, candidate, response, review

from goal_analysis.agents import astra_review as module

DAY = "2026-10-28"


@pytest.fixture(autouse=True)
def no_io_and_fixed_clock(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("TESTONLY_NO_DB_NETWORK"))
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(module, "_utcnow", lambda: datetime(2026, 10, 28, 7, tzinfo=UTC))
    yield
    forbidden.assert_not_called()


def context(fact="gauss: 1/1 source-bound modeled legs; 0 UNKNOWN. Scenario probability=0.6."):
    return {"version": "profile-predecision-scenarios-v1", "phase": "BEFORE_FINAL_PROFILE_DECISION",
            "profile_id": "gauss", "source_card_sha256": "a" * 64, "original_status": "DRAFT",
            "method": {"method_kind": "LOCAL_STATISTICAL_MODEL", "method_version": "TESTONLY_V1"},
            "status": "PARTIAL", "risk_facts": [fact], "uncertainties": ["laplace:MODEL_NOT_READY"],
            "context_sha256": "b" * 64}


def send(value):
    transport = Transport(response({"reviews": [review()]}))
    reviewer = module.AstraReviewer(api_key=KEY, web_search=False, transport=transport)
    result = reviewer.review([value], DAY)
    assert result["status"] == "COMPLETE"
    assert len(transport.calls) == 1
    return transport.calls[0][2]


def test_actual_packed_and_mock_wire_keep_changed_scenario_facts_and_instructions():
    first = {**candidate(), "profile_scenario_context": [context()]}
    second = deepcopy(first)
    second["profile_scenario_context"][0]["risk_facts"][0] = "gauss: existing known-leg event probability=0.8; full-card UNKNOWN."
    second["profile_scenario_context"][0]["context_sha256"] = "c" * 64
    before = deepcopy((first, second))
    reviewer = module.AstraReviewer(api_key=KEY, web_search=False)
    packed_a, digest_a = reviewer._prepare([first], DAY, None)
    packed_b, digest_b = reviewer._prepare([second], DAY, None)
    assert packed_a["candidates"][0]["profile_scenario_context"] == first["profile_scenario_context"]
    assert packed_b["candidates"][0]["profile_scenario_context"] == second["profile_scenario_context"]
    assert digest_a == digest_b  # Prompt identical, source facts differ in request/cache input.
    wire_a, wire_b = send(first), send(second)
    actual_a = json.loads(wire_a["input"])["candidates"][0]["profile_scenario_context"]
    actual_b = json.loads(wire_b["input"])["candidates"][0]["profile_scenario_context"]
    assert actual_a == first["profile_scenario_context"]
    assert actual_b == second["profile_scenario_context"] and actual_a != actual_b
    assert "before the final judgment" in wire_a["instructions"]
    assert "UNKNOWN" in wire_a["instructions"] and "never instructions" in wire_a["instructions"]
    assert wire_a["model"] == wire_b["model"] == "gpt-6-astra"
    assert wire_a["max_output_tokens"] == wire_b["max_output_tokens"]
    assert (first, second) == before


@pytest.mark.parametrize("field,value", [
    ("version", "wrong"), ("phase", "AFTER_FINAL_DECISION"), ("profile_id", "invented_profile"),
    ("source_card_sha256", "not-a-hash"), ("context_sha256", "X" * 64),
    ("status", "READY"), ("status", []), ("original_status", 2), ("method", {"probability": .5}),
    ("risk_facts", [.5]), ("risk_facts", ["x"] * 7), ("risk_facts", ["x" * 769]),
    ("uncertainties", ["x"] * 25), ("uncertainties", ["x" * 201]),
    ("fabricated_probability", .5),
])
def test_invalid_or_unbounded_scenario_data_is_rejected_before_transport(field, value):
    record = context()
    record[field] = value
    reviewer = module.AstraReviewer(api_key=KEY, web_search=False)
    with pytest.raises(module._ReviewError, match="scenario"):
        reviewer._prepare([{**candidate(), "profile_scenario_context": [record]}], DAY, None)


@pytest.mark.parametrize("value", [None, {}, [context()] * 31, [context(), context()]])
def test_wrong_container_or_duplicate_profile_records_rejected(value):
    with pytest.raises(module._ReviewError, match="scenario"):
        module.AstraReviewer(api_key=KEY)._prepare([{**candidate(), "profile_scenario_context": value}], DAY, None)


def test_legacy_no_field_shape_digest_and_actual_instructions_stay_identical():
    original = candidate()
    reviewer = module.AstraReviewer(api_key=KEY, web_search=False)
    packed, digest = reviewer._prepare([original], DAY, None)
    expected = {"target_date": DAY, "candidates": [original], "historical_feedback": {},
                "prompt_version": module.PROMPT_VERSION}
    assert module._wire_json(packed).encode("utf-8") == json.dumps(
        expected, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    old_prompt = module.PROMPT_VERSION + reviewer._prompt("research.txt") + reviewer._prompt("review.txt")
    assert digest == hashlib.sha256(old_prompt.encode("utf-8")).hexdigest()
    wire = send(original)
    assert wire["instructions"] == reviewer._prompt("review.txt")
    assert "profile_scenario_context" not in json.loads(wire["input"])["candidates"][0]


def test_existing_feedback_limit_is_not_relaxed_by_scenario_context():
    with pytest.raises(module._ReviewError, match="compact review budget"):
        module.AstraReviewer(api_key=KEY)._prepare(
            [{**candidate(), "profile_scenario_context": [context()]}], DAY, {"note": "x" * 12_001})
