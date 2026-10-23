"""Optional, cited soft-veto approvals must survive actual review validation."""

from copy import deepcopy

import pytest
from test_astra_review import SOURCE, candidate, review
from test_daily_223 import NOW

from goal_analysis.agents.astra_review import _ReviewError, _schema, _validate_reviews
from goal_analysis.engine.veto_overrides import verified_veto_override
from goal_analysis.jobs.portfolio import _attach_veto_reviews, _contrarian_review_packets


def packet():
    result = candidate()
    result["veto_override_context"] = {"gates": ["HIGH_ODDS"], "current_price": 2.1}
    return result


def assessed():
    result = review(urls=[SOURCE])
    result["veto_override"] = {
        "enabled": True, "gates": ["HIGH_ODDS"],
        "justification": "A megadott, forrásolt mérkőzésadat támogatja a választást.",
        "source_urls": [SOURCE], "evidence_ids": ["ev-1"],
    }
    return result


def test_ordinary_schema_stays_unchanged_and_override_can_be_declined():
    assert "veto_override" not in _schema(["candidate-1"])["properties"]["reviews"]["items"]["properties"]
    assert "veto_override" in _schema(["candidate-1"], veto_overrides=True)["properties"]["reviews"]["items"]["required"]
    record = review()
    record["veto_override"] = None
    assert _validate_reviews({"reviews": [record]}, [packet()], [])


def test_source_validated_review_is_bound_to_current_club_quote_and_rejects_stale_price():
    original = {"candidates": [candidate()]}
    club = deepcopy(original)
    result = {"status": "COMPLETE", "reviews": [assessed()], "sources": [{"url": SOURCE}]}
    _attach_veto_reviews(club, [packet()], result, NOW)
    quote = club["candidates"][0]
    assert verified_veto_override(quote, now=NOW)["gates"] == ["HIGH_ODDS"]
    quote["decimal_price"] += .1
    assert verified_veto_override(quote, now=NOW) is None
    assert "astra_veto_review" not in original["candidates"][0]


@pytest.mark.parametrize("case", ["raw_boolean", "hard_gate", "unretrieved", "unattached_evidence", "empty_reason"])
def test_unverified_or_out_of_scope_override_is_rejected(case):
    record = assessed()
    sources = [{"url": SOURCE}]
    if case == "raw_boolean":
        record["veto_override"] = True
    elif case == "hard_gate":
        record["veto_override"]["gates"] = ["MISSING_HISTORY"]
    elif case == "unretrieved":
        sources = []
    elif case == "unattached_evidence":
        record["veto_override"]["evidence_ids"] = ["other-candidate-evidence"]
    else:
        record["veto_override"]["justification"] = ""
    with pytest.raises(_ReviewError):
        _validate_reviews({"reviews": [record]}, [packet()], sources)


def test_prospective_soft_veto_candidate_uses_existing_review_budget_and_never_a_proof_flag():
    extra = candidate("extra")
    contexts = {"extra": {"gates": ["HIGH_ODDS"], "soft_ceiling": 2}}
    result = _contrarian_review_packets(
        [candidate("daily")], {"candidates": [extra]}, {}, {"daily"}, 2,
        veto_contexts=contexts,
    )
    assert len(result) == 2 and result[0]["candidate_id"] == "daily"
    assert result[1]["veto_override_context"] == contexts["extra"]
    assert "astra_veto_review" not in result[1] and "astra_veto_override" not in result[1]
