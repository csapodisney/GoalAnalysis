"""No-network tests of downloaded sports facts reaching the Grok transport."""

import json
from copy import deepcopy

import pytest
from test_grok_advisory import NOW, leg, recommendation, report
from test_grok_sports_data import model_report

from goal_analysis.agents.astra_review import _ReviewError
from goal_analysis.agents.grok_packet_tables import expand_tables
from goal_analysis.agents.grok_research import GrokAdvisor
from goal_analysis.agents.grok_research import _packet as validate
from goal_analysis.jobs import grok_advisory as job


def sports_report():
    data = report(recommendation(legs=[leg(1, quote_provider="api_football", bookmaker="Book A",
        region="de", currency="EUR", quoted_at=NOW.isoformat(), quote_source_id=
        "https://v3.football.api-sports.io/odds?fixture=1&bookmaker=8&api_key=PRIVATE_VALUE",
        evidence=[{"category": "historical", "claim_type": "INFERENCE",
                   "source_id": "history-sha256:" + "a" * 64, "observed_at": NOW.isoformat(),
                   "statement": "FULL_TIME totals_2_5 / over; historical_20: home 11/20, away 2/7."}])]),
                  recommendation("daily223", (1,)))
    data["numeric_models"] = model_report()["numeric_models"]
    data["numeric_models"]["kepler"]["predictions"][0]["fixture_id"] = "api_football:1"
    data["fixtures"] = [{"fixture_id": "api_football:1", "competition": "Bundesliga",
                         "competition_type": "LEAGUE", "season": 2026,
                         "provider_status": "NS", "venue_name": "Test Stadion",
                         "api_football_home_team_id": "10", "api_football_away_team_id": "20",
                         "api_football_league_id": "78", "source_id":
                         "https://v3.football.api-sports.io/fixtures?id=1"}]
    return data


def test_every_ticket_and_existing_sports_fact_reaches_one_bounded_request():
    data = sports_report()
    before = deepcopy(data)
    packet, coverage, *_ = job._packet(data, NOW)
    captured = []

    def transport(payload, **_kwargs):
        captured.append(payload)
        incoming = expand_tables(json.loads(payload["input"]))
        critiques = {row["recommendation_id"]: {
            "verdict": "KEEP", "strengths": "A forrásolt adatokat áttekintettem.",
            "risks": "A modellbecslés nem kalibrált.", "flagged_legs": [], "claim_ids": []}
            for row in incoming["recommendations"]}
        return {"status": "completed", "citations": [], "usage": {}, "output": [{
            "type": "message", "content": [{"type": "output_text", "text": json.dumps(
                {"claims": [], "critiques": critiques})}]}]}

    result = GrokAdvisor(api_key="dummy-key", transport=transport).review(packet)
    assert result["status"] == "COMPLETE"
    assert len(captured) == 1 and len(result["critiques"]) == 2
    received = expand_tables(json.loads(captured[0]["input"]))
    assert len(received["fixtures"]) == 1 and len(received["recommendations"]) == 2
    assert "SECRET" not in json.dumps(received) and "PRIVATE_VALUE" not in json.dumps(received)
    fixture = received["fixtures"][0]
    assert fixture["source_id"].endswith("?id=1")
    assert fixture["season"] == 2026 and fixture["venue_name"] == "Test Stadion"
    models = fixture["numeric_models"]
    assert models["period"] == "FULL_TIME"
    assert models["models"]["kepler"]["probabilities"] == {"home": .4, "draw": .3, "away": .3}
    assert models["models"]["kepler"]["training_count"] == 100
    assert received["evidence"][0]["history_counts"] == {
        "historical_20": {"home": [11, 20], "away": [2, 7]}}
    quote = next(row for row in received["quote_sources"] if "bookmaker" in row)
    assert quote["bookmaker"] == "Book A"
    assert quote["quote_source_id"].endswith("?fixture=1&bookmaker=8")
    assert quote["quoted_at"] == NOW.isoformat()
    assert next(row for row in received["recommendations"] if row["profile_id"] == "daily223")["protected"]
    assert data == before
    assert coverage["supplied_sports_data"]["fresh_sports_requests"] == 0
    assert captured[0]["max_output_tokens"] == 6500


@pytest.mark.parametrize("url", [
    "https://example.com/odds?fixture=1&api_key=private",
    "https://example.com/odds?fixture=unknown",
    "https://example.com/odds?fixture=1#private",
])
def test_direct_provider_refuses_unsanitized_sports_urls(url):
    packet, *_ = job._packet(sports_report(), NOW)
    packet["fixtures"][0]["source_id"] = url
    with pytest.raises((ValueError, _ReviewError)):
        validate(packet)


def test_unknown_long_evidence_is_explicitly_rejected_instead_of_losing_counts():
    data = sports_report()
    data["recommendations"][0]["legs"][0]["evidence"][0]["statement"] = "x" * 330 + " 8/10"
    with pytest.raises(ValueError, match="SPORTS_EVIDENCE_EXCEEDS_LIMIT"):
        job._packet(data, NOW)
