from goal_analysis.portfolio_objectives import assess_objectives


def rec(profile, price, legs=True):
    return {"profile_id": profile, "combined_price": price, "status": "READY", "publication_scope": "LIVE",
            "legs": [{"fixture_id": profile}] if legs else []}


def test_objectives_measure_count_prices_and_future_kpi_without_filler():
    report = {"recommendations": [
        rec("daily223", 3.2), rec("good", 12), rec("low", 8), rec("high", 55),
        rec("empty", None, False), rec("missing", None),
    ]}
    result = assess_objectives(report)
    assert result["ready_recommendations"] == 5
    assert result["capacity_shortfall"] == 15
    assert result["non_daily_below_price_range"] == ["low"]
    assert result["non_daily_above_price_range"] == ["high"]
    assert result["non_daily_missing_price"] == ["missing"]
    assert result["settlement_kpi"]["status"] == "PENDING_SETTLEMENT"
    assert result["filler_generation_allowed"] is False


def test_target_range_is_twenty_to_thirty():
    result = assess_objectives({"recommendations": [rec(str(i), 10) for i in range(24)]})
    assert result["capacity_status"] == "IN_TARGET"
    assert result["capacity_shortfall"] == 0
    assert result["target_ready_range"] == {"minimum": 20, "maximum": 30}
