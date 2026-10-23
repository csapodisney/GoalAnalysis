"""Proper scores are measurements, never heuristic probabilities or cash."""
import json
import math
from copy import deepcopy

import pytest

from goal_analysis.learning.forecast_quality import evaluate

NOW = "2026-10-22T22:00:00+00:00"


def record(p=0.7, outcome="WON", **changes):
    item = {"id": "a", "profile_id": "expert", "version": "strategy-1",
            "created_at": "2026-10-22T10:00:00+00:00", "original_run_match": True,
            "legs": [{"fixture_id": "the_stats_api:1", "market": "FULL_TIME:TOTAL_GOALS_2_5",
                      "selection": "OVER", "kickoff": "2026-10-22T18:00:00+00:00",
                      "model_version": "gauss-v1", "model_probability": p,
                      "confidence_kind": "UNCALIBRATED_MODEL_ESTIMATE", "model_details": {},
                      "outcome": outcome, "decimal_price": 2.2,
                      "quote_provenance": {"quote_provider": "the_stats_api", "bookmaker": "b1",
                                           "settlement": "REGULATION_TIME", "line": "2.5",
                                           "region": "eu", "currency": "EUR",
                                           "quote_source_sha256": "b" * 64, "quote_source_id": "entry-1",
                                           "quoted_at": "2026-10-22T09:50:00+00:00",
                                           "quote_observed_at": "2026-10-22T09:55:00+00:00"}}]}
    item.update(changes)
    return item


def closing(**changes):
    leg = record()["legs"][0]
    q = {**leg["quote_provenance"], **{k: leg[k] for k in ("fixture_id", "market", "selection")},
         "decimal_price": 2.0, "price_kind": "CLOSING", "quote_time_status": "KNOWN",
         "quoted_at": "2026-10-22T17:59:00+00:00", "observed_at": "2026-10-22T18:01:00+00:00",
         "ingested_at": "2026-10-22T18:02:00+00:00", "sealed_at": "2026-10-22T18:03:00+00:00",
         "quote_id": "q1", "source_id": "s1", "source_sha256": "a" * 64}
    q.update(changes)
    return q


def test_known_binary_scores_bins_empty_intervals_and_original_immutability():
    inputs = [record(), record(0.2, "LOST", id="b", profile_id="other")]
    inputs[1]["legs"][0]["fixture_id"]="the_stats_api:2"
    before = deepcopy(inputs)
    result = evaluate(inputs, as_of=NOW)
    assert result["brier_binary"] == pytest.approx((0.09 + 0.04) / 2)
    assert result["log_loss_binary"] == pytest.approx((-math.log(0.7) - math.log(0.8)) / 2)
    assert result["unique_products"] == result["unique_fixtures"] == 2
    assert sum(b["count"] for b in result["bins"]) == 2
    assert result["bins"][0]["observed_rate"] is None
    assert result["decision_effect"] == "NONE" and not result["validated"]
    assert inputs == before


@pytest.mark.parametrize("p,y,expected", [(0,"WON",1),(1,"LOST",1),(0,"LOST",0),(1,"WON",0)])
def test_endpoints_have_explicit_finite_log_floor(p,y,expected):
    result = evaluate([record(p,y)], as_of=NOW)
    assert result["brier_binary"] == expected
    assert result["log_loss_floor_applied"] == expected
    assert math.isfinite(result["log_loss_binary"])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("p", [None, True, float("nan"), float("inf"), -0.1, 1.1, "0.7"])
def test_missing_or_invalid_probability_is_never_repaired(p):
    result = evaluate([record(p)],as_of=NOW)
    assert result["eligible_selections"] == 0
    assert result["brier_binary"] is result["log_loss_binary"] is None


@pytest.mark.parametrize("kind", ["EVIDENCE_HEURISTIC_NOT_PROBABILITY", "EVIDENCE_STRENGTH_NOT_CALIBRATED_PROBABILITY"])
def test_heuristic_is_not_a_probability_even_with_numeric_model_version(kind):
    item=record();item["legs"][0]["confidence_kind"]=kind
    result=evaluate([item],as_of=NOW)
    assert result["excluded"] == {"HEURISTIC_OR_UNKNOWN_PROBABILITY_KIND":1}


def test_unknown_unmarked_score_excluded_but_explicit_original_legacy_numeric_version_kept():
    item=record();leg=item["legs"][0];leg["confidence_kind"]=None
    assert evaluate([item],as_of=NOW)["eligible_selections"]==1
    leg["model_version"]="UNKNOWN_LEGACY"
    assert evaluate([item],as_of=NOW)["eligible_selections"]==0


def test_shared_mean_requires_original_components_and_normalized_weights():
    item=record();leg=item["legs"][0];leg["confidence_kind"]="UNCALIBRATED_SHARED_MODEL_ESTIMATE"
    assert evaluate([item],as_of=NOW)["eligible_selections"]==0
    leg["model_details"]={"method":"SHARED_MODEL_WEIGHTED_MEAN",
        "shared_model_probabilities":{"gauss":0.8,"laplace":0.6},
        "shared_model_weights":{"gauss":0.5,"laplace":0.5}}
    assert evaluate([item],as_of=NOW)["eligible_selections"]==1
    leg["model_probability"]=0.9
    assert evaluate([item],as_of=NOW)["eligible_selections"]==0


@pytest.mark.parametrize("outcome",["VOID","REVIEW","PENDING"])
def test_nonbinary_outcomes_do_not_become_losses(outcome):
    assert evaluate([record(outcome=outcome)],as_of=NOW)["eligible_selections"]==0


def test_first_forecast_kept_before_filtering_versions_separate_and_result_conflicts_excluded():
    first=record(None);later=record(0.9,id="b",created_at="2026-10-22T11:00:00+00:00")
    assert evaluate([later,first],as_of=NOW)["eligible_selections"]==0
    later["version"]="strategy-2"
    result=evaluate([first,later],as_of=NOW)
    assert result["eligible_selections"]==1
    assert result["by_profile_strategy_model_market"][0]["strategy_version"]=="strategy-2"
    conflict=record(0.6,"LOST",id="c",profile_id="other")
    assert evaluate([later,conflict],as_of=NOW)["eligible_selections"]==0


@pytest.mark.parametrize("field,value",[("created_at","2026-10-22T18:00:00+00:00"),
    ("created_at","2026-10-23T10:00:00+00:00"),("created_at","2026-10-22T10:00:00"),
    ("original_run_match",False)])
def test_unverified_postkickoff_naive_or_future_forecasts_are_excluded(field,value):
    assert evaluate([record(**{field:value})],as_of=NOW)["eligible_selections"]==0


def test_clv_uses_explicit_same_bookmaker_provider_closing_price_not_profit():
    result=evaluate([record()],as_of=NOW,closing_quotes=[closing()])["closing_line_value"]
    assert result["matched_selections"]==1
    assert result["mean_clv_price_ratio_minus_one"]==pytest.approx(0.1)
    assert not result["fair_probability_or_profit"]
    assert result["rows"][0]["closing_quote_id"]=="q1"


@pytest.mark.parametrize("field,value",[("bookmaker","b2"),("quote_provider","the_odds_api"),
    ("fixture_id","the_stats_api:2"),("market","FIRST_HALF:TOTAL_GOALS_2_5"),
    ("selection","UNDER"),("line","3.5"),("currency","USD"),("region","us"),
    ("settlement","INCLUDING_EXTRA_TIME")])
def test_clv_product_or_cohort_mismatch_never_matches(field,value):
    assert evaluate([record()],as_of=NOW,closing_quotes=[closing(**{field:value})])["closing_line_value"]["matched_selections"]==0


@pytest.mark.parametrize("changes",[{"quoted_at":None,"quote_time_status":"MISSING"},
    {"price_kind":"LAST_SEEN"},{"price_kind":"PRE_CLOSING"},{"price_kind":"CURRENT"},
    {"quoted_at":"2026-10-22T18:00:00+00:00"},{"quoted_at":"2026-10-22T09:00:00+00:00"},
    {"observed_at":"2026-10-22T17:58:00+00:00"},{"sealed_at":"2026-10-23T00:00:00+00:00"},
    {"source_sha256":"z"*64},{"quote_id":None},{"decimal_price":True}])
def test_receipt_only_inplay_preentry_unsealed_or_unverified_quote_does_not_create_clv(changes):
    assert evaluate([record()],as_of=NOW,closing_quotes=[closing(**changes)])["closing_line_value"]["matched_selections"]==0


def test_missing_entry_quote_clock_is_not_replaced_by_receipt():
    item=record();item["legs"][0]["quote_provenance"]["quoted_at"]=None
    result=evaluate([item],as_of=NOW,closing_quotes=[closing()])
    assert result["closing_line_value"]["matched_selections"]==0
    assert result["closing_line_value"]["mean_clv_price_ratio_minus_one"] is None


def test_latest_closing_timestamp_ties_conflict_not_cherry_picked_duplicates_not_extra_sample():
    values=[closing(),closing(quote_id="q2",decimal_price=1.8)]
    assert evaluate([record()],as_of=NOW,closing_quotes=values)["closing_line_value"]["missing_reasons"]=={"CONFLICTING_CLOSING_PRICES":1}
    values[1]["decimal_price"]=2.0
    result=evaluate([record(),record(id="b")],as_of=NOW,closing_quotes=values)
    assert result["eligible_selections"]==result["closing_line_value"]["matched_selections"]==1


def test_empty_measurement_remains_unknown_and_has_five_empty_curve_bins():
    result=evaluate([],as_of=NOW)
    assert result["brier_binary"] is result["log_loss_binary"] is None
    assert len(result["bins"])==5 and all(b["count"]==0 for b in result["bins"])
    assert result["closing_line_value"]["mean_clv_price_ratio_minus_one"] is None


def test_simultaneous_conflicting_original_forecasts_are_not_chosen_by_id():
    values=[record(0.6),record(0.8,id="b")]
    for inputs in (values,list(reversed(values))):
        result=evaluate(inputs,as_of=NOW)
        assert result["eligible_selections"]==0
        assert result["excluded"]=={"CONFLICTING_FIRST_FORECASTS":1}


def test_same_product_different_profiles_remains_repeated_correlated_evidence():
    result=evaluate([record(),record(id="b",profile_id="other")],as_of=NOW)
    assert result["eligible_selections"]==2 and result["unique_products"]==1
    assert result["unique_fixtures"]==1


def test_first_forecast_is_chronological_across_timezones_not_iso_string_order():
    early=record(0.2,created_at="2026-10-22T10:00:00+02:00")
    late=record(0.8,id="b",created_at="2026-10-22T09:00:00Z")
    result=evaluate([late,early],as_of=NOW)
    assert result["eligible_selections"]==1
    assert result["rows"][0]["probability"]==0.2
    assert result["brier_binary"]==pytest.approx(0.64)


def test_closing_comparison_requires_entry_source_reference():
    item=record();item["legs"][0]["quote_provenance"].pop("quote_source_sha256")
    result=evaluate([item],as_of=NOW,closing_quotes=[closing()])["closing_line_value"]
    assert result["missing_reasons"]=={"MISSING_ENTRY_SOURCE_REFERENCE":1}


def test_learning_reader_preserves_original_probability_kind_and_bounds_llm_briefing(tmp_path):
    from datetime import timedelta

    from test_learning import populated
    from test_portfolio_ledger import NOW as LEDGER_NOW

    from goal_analysis.learning import LearningAgent
    from goal_analysis.learning._findings import review_packet
    from goal_analysis.learning._metrics import measure

    ledger=populated(tmp_path)
    now=LEDGER_NOW+timedelta(hours=8)
    measured=measure(ledger.path,now,include_records=True)
    scores=measured["metrics"]["research"]["forecast_quality"]
    assert scores["eligible_selections"]==3
    assert scores["log_loss_binary"]==pytest.approx(-math.log(0.7))
    original=next(r for r in measured["records"]["research"] if r["profile_id"]=="gauss")
    assert original["legs"][0]["model_details"]=={"version":"gauss-1.0"}
    assert "quoted_at" in original["legs"][0]["quote_provenance"]
    agent=LearningAgent(tmp_path,ledger,lambda:now)
    draft=agent._build(now,now)
    packet=review_packet(draft)
    assert "forecast_quality" not in json.dumps(packet)
    assert "closing_line_value" not in json.dumps(packet)
    assert "by_profile_strategy_model_market" not in json.dumps(packet)
    assert not agent.path.exists()  # measurement/build does not save a daily report


def test_learning_does_not_rewrite_old_daily_reports_after_measurement(tmp_path):
    from datetime import timedelta

    from test_learning import Review, populated
    from test_portfolio_ledger import NOW as LEDGER_NOW

    from goal_analysis.learning import LearningAgent
    from goal_analysis.learning._metrics import measure

    ledger=populated(tmp_path);now=LEDGER_NOW+timedelta(hours=8)
    agent=LearningAgent(tmp_path,ledger,lambda:now)
    saved=agent.run(now,Review())
    measure(ledger.path,now+timedelta(hours=1))
    assert agent.latest()==saved
