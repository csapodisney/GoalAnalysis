"""Named soft exceptions require actual reviewed sources and intact hard gates."""

from copy import deepcopy
from datetime import timedelta

import pytest
from test_daily_223 import NOW, candidate
from test_portfolio import source

from goal_analysis.engine import portfolio
from goal_analysis.engine.veto_overrides import (
    league_base_rate_context,
    verified_veto_override,
    veto_candidate_signature,
)

URL = "https://example.org/match-reported-lineup"


def historical_candidate(identifier="case", price=1.5):
    row = candidate(identifier, price, strength=0.2, api_football_league_id=100)
    row["evidence"] = row["evidence"][:2]
    for evidence in row["evidence"]:
        evidence.update(evidence_id=f"history-v1:{identifier}:{evidence['category']}",
                        source_id="history-sha256:" + "a" * 64)
    analysis = {"candidate_id": identifier, "status": "CALCULATED", "period": "FULL_TIME",
                "history_league_id": 100,
                "profiles": {side: {"historical_20": {"sample_size": 20},
                                    "venue_10": {"sample_size": 10}}
                             for side in ("home", "away")}}
    return row, analysis


def reviewed(row, gates=None):
    proof = {"enabled": True, "gates": gates or ["LEAGUE_BASE_RATE"],
             "justification": "A forrásolt tényleges kezdőcsapat indokolja az egyedi eltérést.",
             "source_urls": [URL], "evidence_ids": [row["evidence"][0]["evidence_id"]]}
    record = {"candidate_id": row["candidate_id"], "assessment": "support",
              "support_arguments": ["Forrásolt egyedi körülmény."], "risk_notes": [],
              "source_urls": [URL], "evidence_ids": proof["evidence_ids"],
              "veto_override": proof}
    return {"status": "COMPLETE", "reviewed_at": NOW.isoformat(),
            "sources": [{"url": URL, "title": "Ellenőrzött szintetikus tesztforrás"}],
            "reviews": [record]}


def proof_input():
    row, analysis = historical_candidate()
    row["veto_override_context"] = league_base_rate_context(row, analysis, 6.275, 7.2)
    return row, reviewed(row)


def test_valid_review_proof_preserves_raw_scores_and_traceable_source_identity():
    row, review = proof_input()
    original = deepcopy((row, review))
    result = verified_veto_override(row, review, NOW)
    assert result["gates"] == ["LEAGUE_BASE_RATE"]
    assert result["source_urls"] == [URL]
    assert result["candidate_snapshot_sha256"] == veto_candidate_signature(row)
    assert result["score_is_probability"] is False
    assert (row, review) == original


@pytest.mark.parametrize("change", ["flag-only", "source-not-returned", "source-not-cited",
                                   "evidence-not-candidate", "future", "hard-gate", "caution",
                                   "wrong-candidate", "insufficient-samples", "missing-history-source"])
def test_unverified_or_inapplicable_flags_cannot_authorize_an_exception(change):
    row, review = proof_input()
    record = review["reviews"][0]
    if change == "flag-only":
        row["astra_veto_override"] = {"enabled": True}
        review = None
    elif change == "source-not-returned":
        review["sources"] = []
    elif change == "source-not-cited":
        record["source_urls"] = []
    elif change == "evidence-not-candidate":
        record["veto_override"]["evidence_ids"] = ["invented"]
        record["evidence_ids"] = ["invented"]
    elif change == "future":
        review["reviewed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif change == "hard-gate":
        record["veto_override"]["gates"] = ["MINIMUM_SAMPLE_SIZE"]
    elif change == "caution":
        record["assessment"] = "caution"
    elif change == "wrong-candidate":
        record["candidate_id"] = "another-match"
    elif change == "insufficient-samples":
        row["veto_override_context"]["history_basis"]["profiles"]["home"]["historical_20"]["sample_size"] = 2
    else:
        row["evidence"][0]["source_id"] = "unverified-arbitrary-source"
    assert verified_veto_override(row, review, NOW) is None


def test_attached_review_is_bound_to_current_price_and_stable_under_metadata_normalization():
    row, review = proof_input()
    row["astra_veto_review"] = {**review, "candidate_snapshot_sha256": veto_candidate_signature(row)}
    assert verified_veto_override(row, now=NOW)
    normalized = deepcopy(row)
    normalized["quote_timestamp_raw"] = row["quoted_at"]
    assert verified_veto_override(normalized, now=NOW)
    row["decimal_price"] += 0.1
    assert verified_veto_override(row, now=NOW) is None


def test_high_odds_exception_is_named_and_does_not_imply_a_history_override():
    row = candidate("high-odds", 8)
    row["veto_override_context"] = {"gates": ["HIGH_ODDS"], "persona_price_maximum": 6}
    result = verified_veto_override(row, reviewed(row, ["HIGH_ODDS"]), NOW)
    assert result["gates"] == ["HIGH_ODDS"]
    row["profile_id"] = "daily223"
    assert verified_veto_override(row, reviewed(row, ["HIGH_ODDS"]), NOW) is None


def test_context_is_only_offered_after_original_identity_history_and_h1_guards():
    row, analysis = historical_candidate()
    enriched = source([row])
    enriched["history_analysis"]["candidates"] = [analysis]
    settings = {"enabled_profiles": ["kronikas"]}
    assert portfolio.specialist_veto_override_contexts(enriched, settings, NOW)
    for field, value in (("quote_available", False), ("kickoff", NOW.isoformat())):
        invalid = deepcopy(enriched)
        invalid["candidates"][0][field] = value
        assert portfolio.specialist_veto_override_contexts(invalid, settings, NOW) == {}
    invalid = deepcopy(enriched)
    invalid["history_analysis"]["candidates"][0]["profiles"]["away"]["venue_10"]["sample_size"] = 2
    assert portfolio.specialist_veto_override_contexts(invalid, settings, NOW) == {}


def test_verified_base_soft_exception_never_mutates_daily_on_identical_reviewed_input(monkeypatch):
    rows_and_analyses = [historical_candidate(str(index), price)
                         for index, price in enumerate((2, 2, 3, 2))]
    rows = [pair[0] for pair in rows_and_analyses]
    enriched = source(rows)
    enriched["history_analysis"]["candidates"] = [pair[1] for pair in rows_and_analyses]
    review = reviewed(rows[0])
    review["reviews"] = [reviewed(row)["reviews"][0] for row in rows]
    settings = {"enabled_profiles": ["daily223", "kronikas"]}
    original = deepcopy(enriched)
    real_validator = portfolio.verified_veto_override
    monkeypatch.setattr(portfolio, "verified_veto_override", lambda *_args, **_kwargs: None)
    baseline = portfolio.build_portfolio(enriched, settings, astra_review=review)
    monkeypatch.setattr(portfolio, "verified_veto_override", real_validator)
    updated = portfolio.build_portfolio(enriched, settings, astra_review=review)
    daily_before = next(ticket for ticket in baseline["tickets"] if ticket["profile_id"] == "daily223")
    daily_after = next(ticket for ticket in updated["tickets"] if ticket["profile_id"] == "daily223")
    assert daily_before == daily_after
    specialist = next(ticket for ticket in updated["tickets"] if ticket["profile_id"] == "kronikas")
    assert all(leg["veto_override"]["gates"] == ["LEAGUE_BASE_RATE"] for leg in specialist["legs"])
    assert all(leg["weighted_support_score"] == 6.275 for leg in specialist["legs"])
    assert all("veto_override" not in leg for leg in daily_after["legs"])
    assert enriched == original
