"""Regression coverage for as-of boundaries in the offline Grok input builder."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents.grok_research import _packet as provider_packet
from goal_analysis.jobs.grok_advisory import _packet

NOW = datetime(2026, 10, 18, 8, tzinfo=UTC)
_MODEL_FIELDS = {
    "model_probability", "model_probabilities", "model_stability", "estimated_edge",
    "confidence_kind", "calibration",
}


def report():
    detail = {"cutoff": (NOW - timedelta(minutes=10)).isoformat(),
              "calibration": "unvalidated", "shared_model_probabilities": {"gauss": .63}}
    leg = {"fixture_id": "api_football:123", "home_team": "Home", "away_team": "Away",
           "kickoff": (NOW + timedelta(hours=5)).isoformat(), "market_key": "totals_2_5",
           "selection_key": "over", "period": "FULL_TIME", "decimal_price": 2.3,
           "quoted_at": (NOW - timedelta(minutes=1)).isoformat(), "quote_provider": "api_football",
           "bookmaker": "bet365_api_football_8", "region": "DE", "currency": "EUR",
           "quote_source_id": "https://v3.football.api-sports.io/odds?fixture=123&bookmaker=8&bet=5",
           "model_probability": .63, "model_stability": .04, "estimated_edge": .15,
           "confidence_kind": "UNVALIDATED_MODEL_PROBABILITY", "model_details": detail}
    return {"run_id": "boundary-test", "date": "2026-10-18", "observed_at": NOW.isoformat(),
            "model_cutoff": detail["cutoff"],
            "fixtures": [{"fixture_id": leg["fixture_id"]}],
            "recommendations": [{"profile_id": "tipster_szines_reka", "status": "READY",
                                 "recommendation_id": "r1", "legs": [leg]}]}


@pytest.mark.parametrize("timestamp", [
    "2026-10-18T08:00:00.000001+00:00",
    "2026-10-18T10:01:00+02:00",
])
def test_future_quote_rejects_packet_instead_of_laundering_price(timestamp):
    data = report()
    data["recommendations"][0]["legs"][0]["quoted_at"] = timestamp
    before = deepcopy(data)
    with pytest.raises(ValueError):
        _packet(data, NOW)
    assert data == before


def test_quote_at_exact_cutoff_keeps_price_and_timestamp():
    data = report()
    leg = data["recommendations"][0]["legs"][0]
    leg["quoted_at"] = NOW.isoformat()
    packet, *_ = _packet(data, NOW)
    clean, *_ = provider_packet(packet)
    assert clean["selections"][0]["decimal_price"] == leg["decimal_price"]
    quote = clean["quote_sources"][0]
    assert quote["quoted_at"] == NOW.isoformat()
    assert quote["quote_source_id"] == leg["quote_source_id"]


@pytest.mark.parametrize("cutoff_location", ["model_details", "report"])
def test_future_selection_model_fields_are_removed_but_quote_is_preserved(cutoff_location):
    data = report()
    leg = data["recommendations"][0]["legs"][0]
    future = (NOW + timedelta(seconds=1)).isoformat()
    if cutoff_location == "model_details":
        leg["model_details"]["cutoff"] = future
    else:
        leg["model_details"].pop("cutoff")
        data["model_cutoff"] = future
    data["numeric_models"] = {"gauss": {"predictions": [{
        "fixture_id": leg["fixture_id"], "probabilities": {"over25": .63},
        "model_details": deepcopy(leg["model_details"])}]}}
    before = deepcopy(data)
    packet, *_ = _packet(data, NOW)
    clean, *_ = provider_packet(packet)
    selection = clean["selections"][0]
    assert not _MODEL_FIELDS.intersection(selection)
    assert "numeric_models" not in clean["fixtures"][0]
    assert selection["decimal_price"] == leg["decimal_price"]
    quote = clean["quote_sources"][0]
    assert quote["quoted_at"] == leg["quoted_at"]
    assert quote["quote_provider"] == leg["quote_provider"]
    assert quote["bookmaker"] == leg["bookmaker"]
    assert quote["quote_source_id"] == leg["quote_source_id"]
    assert data == before


def test_model_fields_at_cutoff_are_retained():
    data = report()
    leg = data["recommendations"][0]["legs"][0]
    leg["model_details"]["cutoff"] = NOW.isoformat()
    packet, *_ = _packet(data, NOW)
    clean, *_ = provider_packet(packet)
    assert _MODEL_FIELDS <= set(clean["selections"][0])
