"""Contrarian rationale is source-linked review output, never fabricated text."""

from copy import deepcopy

import pytest
from test_astra_review import SOURCE, candidate, review
from test_daily_223 import NOW

from goal_analysis.agents.astra_review import _ReviewError, _schema, _validate_reviews
from goal_analysis.jobs.portfolio import _attach_contrarian_reviews, _contrarian_review_packets


def packet():
    result = candidate()
    result["evidence_ids"] = ["movement-test"]
    result["contrarian_context"] = {"evidence_id": "movement-test", "expected_return": 1.2}
    return result


def assessed():
    result = review(evidence_id="movement-test")
    result["contrarian_rationale"] = {
        "price_case": "Az összehasonlítható ár 24–48 órán belül emelkedett; az ok nem ismert.",
        "math_case": "A megadott, nem kalibrált modellbecslés és ár szorzata 1,20.",
        "cause_status": "UNKNOWN", "cause_source_urls": [],
    }
    return result


def test_conditional_schema_preserves_existing_daily_and_ordinary_review_contract():
    ordinary = _schema(["candidate-1"])["properties"]["reviews"]["items"]
    assert "contrarian_rationale" not in ordinary["properties"]
    extended = _schema(["candidate-1"], contrarian=True)["properties"]["reviews"]["items"]
    assert "contrarian_rationale" in extended["required"]
    assert _validate_reviews({"reviews": [review()]}, [candidate()], [])


def test_complete_actual_validated_review_attaches_both_sections_to_club_copy_only():
    source = {"candidates": [candidate()]}
    club = deepcopy(source)
    result = {"status": "COMPLETE", "reviews": [assessed()], "sources": []}
    assert _attach_contrarian_reviews(club, [packet()], result, NOW) == "COMPLETE"
    proof = club["candidates"][0]["contrarian_review"]
    assert proof["price_case"] and proof["math_case"] and proof["cause_status"] == "UNKNOWN"
    assert proof["reviewed_at"] == NOW.isoformat()
    assert "contrarian_review" not in source["candidates"][0]


@pytest.mark.parametrize("case", ["missing", "price", "math", "unretrieved_cause", "wrong_evidence"])
def test_absent_or_unverified_explanation_is_not_accepted(case):
    assessed_record = assessed()
    if case == "missing":
        assessed_record.pop("contrarian_rationale")
    elif case in {"price", "math"}:
        assessed_record["contrarian_rationale"][case + "_case"] = ""
    elif case == "unretrieved_cause":
        assessed_record["contrarian_rationale"].update(cause_status="SOURCED", cause_source_urls=[SOURCE])
    else:
        assessed_record["evidence_ids"] = ["ev-1"]
    club = {"candidates": [candidate()]}
    result = {"status": "COMPLETE", "reviews": [assessed_record], "sources": []}
    assert _attach_contrarian_reviews(club, [packet()], result, NOW) == "STRUCTURED_EXPLANATION_MISSING_OR_INVALID"
    assert "contrarian_review" not in club["candidates"][0]


def test_rationale_for_an_unrequested_candidate_is_rejected():
    with pytest.raises(_ReviewError):
        _validate_reviews({"reviews": [assessed()]}, [candidate()], [])


def test_supplemental_review_retains_all_daily_legs_without_increasing_budget():
    ordinary = [candidate(f"daily-{i}") for i in range(3)]
    extra = [candidate(f"contra-{i}") for i in range(10)]
    contexts = {item["candidate_id"]: {"evidence_id": f"move-{i}", "expected_return": 1.2 + i / 100}
                for i, item in enumerate(extra)}
    original = deepcopy(ordinary)
    result = _contrarian_review_packets(
        ordinary, {"candidates": extra}, contexts, {item["candidate_id"] for item in ordinary}, 5,
    )
    assert len(result) == 5
    assert [item["candidate_id"] for item in result[:3]] == [item["candidate_id"] for item in ordinary]
    assert all("contrarian_context" in item for item in result[3:])
    assert ordinary == original
