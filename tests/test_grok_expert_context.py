"""Expert provenance reaches the single Grok package without a network request."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest
from test_grok_advisory import NOW
from test_grok_sports_packet_integration import sports_report

from goal_analysis.agents.grok_packet_tables import expand_tables
from goal_analysis.agents.grok_research import GrokAdvisor
from goal_analysis.agents.grok_research import _packet as validate
from goal_analysis.agents.motivation_expert import analyze_motivation
from goal_analysis.jobs import grok_advisory as job


def experts_report():
    data = sports_report()
    fixture = data["fixtures"][0]
    past = NOW - timedelta(minutes=5)
    forecast = {"fixture_id": fixture["fixture_id"], "status": "SOURCED", "provider": "open_meteo",
                "weather_kind": "FORECAST", "observed_at": past.isoformat(),
                "forecast_time": (NOW + timedelta(hours=5)).isoformat(), "temperature_c": 18,
                "source_url": "https://api.open-meteo.com/v1/forecast?latitude=50&longitude=8"}
    fixture["weather"] = forecast
    context = {"status": "PARTIAL", "league_id": "78", "season": 2026,
               "observed_at": past.isoformat(), "source_url":
               "https://v3.football.api-sports.io/standings?league=78&season=2026",
               "rules_status": "UNVERIFIED", "missing_teams": ["20"], "ambiguous_teams": [],
               "teams": [{"team_id": "10", "team_name": "Home 1", "rank": 5, "points": 16,
                          "played": 9, "goals_difference": 3, "form": "WWDLL", "group": "League",
                          "side": "home", "provider_zone": "Europa League", "games_remaining": None,
                          "points_gap_to_group_leader": 5, "nearby_provider_zones": []}]}
    post_id = (int(past.timestamp() * 1000) - 1288834974657) << 22
    claim = {"fixture_id": fixture["fixture_id"], "claim_id": "previous-c1", "kind": "ROTATION",
             "claim": "The coach reportedly plans some rotation.", "author": "ClubNews",
             "source_url": f"https://x.com/ClubNews/status/{post_id}", "published_at": past.isoformat(),
             "fetched_at": NOW.isoformat(), "content_status": "UNVERIFIED",
             "provenance_status": "CITATION_AND_TIMESTAMP_CHECKED"}
    motivation = analyze_motivation([fixture], as_of=NOW, claims=[claim],
                                    standings_context={fixture["fixture_id"]: context})
    weather = {"version": "weather-expert-v1", "observed_at": NOW.isoformat(),
               "data_hash": "a" * 64, "policy_hash": "b" * 64, "fixtures": [{
                   "fixture_id": fixture["fixture_id"], "status": "INSUFFICIENT_DATA",
                   "reason": "INSUFFICIENT_COMPARABLE_HISTORY", "score_multiplier": 1.0,
                   "live_weight_eligible": False, "teams": {
                       "home": {"matched_history_count": 3}, "away": {"matched_history_count": 0}}}]}
    data["expert_advisory"] = {"observed_at": NOW.isoformat(), "motivation": motivation, "weather": weather}
    return data


def test_expert_evidence_reaches_one_request_with_provenance_and_neutrality():
    data = experts_report()
    before = deepcopy(data)
    packet, *_ = job._packet(data, NOW)
    captured = []

    def transport(payload, **_kwargs):
        captured.append(payload)
        incoming = expand_tables(json.loads(payload["input"]))
        replies = {row["recommendation_id"]: {"verdict": "KEEP", "strengths": "Forrásolt adatok.",
                   "risks": "Kevés időjárási minta.", "flagged_legs": [], "claim_ids": []}
                   for row in incoming["recommendations"]}
        return {"status": "completed", "citations": [], "usage": {}, "output": [{
            "type": "message", "content": [{"type": "output_text", "text": json.dumps({
                "claims": [], "critiques": replies})}]}]}

    result = GrokAdvisor(api_key="offline", transport=transport).review(packet)
    assert result["status"] == "COMPLETE" and len(captured) == 1
    received = expand_tables(json.loads(captured[0]["input"]))
    expert = received["fixtures"][0]["expert_context"]
    assert expert["weather"]["team_samples"] == {"home": 3, "away": 0}
    assert expert["weather"]["score_multiplier"] == 1
    assert expert["motivation"]["stakes"] == ["UNKNOWN"]
    assert expert["motivation"]["standings"]["source_url"].endswith("?league=78&season=2026")
    team = expert["motivation"]["standings"]["teams"]["home"]
    assert (team["rank"], team["points"], team["played"]) == (5, 16, 9)
    assert "team_name" not in team and "nearby_provider_zones" not in team
    assert expert["motivation"]["claims"][0]["status"] == "SOURCED_CLAIM_UNVERIFIED"
    assert captured[0]["max_turns"] == 2 and captured[0]["max_output_tokens"] == 6500
    assert "Do not re-fetch supplied standings" in captured[0]["instructions"]
    assert "at most two focused searches TOTAL" in captured[0]["instructions"]
    assert len(captured[0]["input"]) < 100_000
    assert len(received["recommendations"]) == 2
    assert data == before


@pytest.mark.parametrize("mutate", [
    lambda expert: expert.update(observed_at=(NOW + timedelta(seconds=1)).isoformat()),
    lambda expert: expert["weather"].update(source_observed_at=(NOW + timedelta(seconds=1)).isoformat()),
    lambda expert: expert["weather"].update(score_multiplier=1.05),
    lambda expert: expert["weather"].update(live_weight_eligible=True, status="VALIDATED"),
    lambda expert: expert["motivation"].update(stakes=["QUALIFIED"]),
    lambda expert: expert["motivation"]["claims"][0].update(status="TRUE"),
    lambda expert: expert["motivation"]["claims"][0].update(fetched_at=(NOW + timedelta(seconds=1)).isoformat()),
    lambda expert: expert["motivation"]["standings"].update(league_id="79"),
    lambda expert: expert["motivation"]["standings"]["teams"]["home"].update(games_remaining=29),
    lambda expert: expert["motivation"]["standings"].update(source_url="https://api.example/table?key=private"),
])
def test_provider_rejects_future_unverified_or_cross_scope_expert_facts(mutate):
    packet, *_ = job._packet(experts_report(), NOW)
    mutate(packet["fixtures"][0]["expert_context"])
    with pytest.raises(ValueError):
        validate(packet)


def test_future_expert_report_is_not_available_and_saved_tickets_remain_reviewable():
    data = experts_report()
    data["expert_advisory"]["observed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    packet, *_ = job._packet(data, NOW)
    assert "expert_context" not in packet["fixtures"][0]
    assert len(packet["recommendations"]) == 2


def test_fresh_forecast_from_expert_refresh_replaces_stale_display_weather_without_mutation():
    data = experts_report()
    old = deepcopy(data["fixtures"][0]["weather"])
    updated = {**old, "temperature_c": 20, "observed_at": NOW.isoformat()}
    data["expert_advisory"]["weather"]["fixtures"][0]["forecast"] = updated
    packet, *_ = job._packet(data, NOW)
    clean, *_ = validate(packet)
    assert clean["fixtures"][0]["weather"]["temperature_c"] == 20
    assert clean["fixtures"][0]["expert_context"]["weather"]["source_observed_at"] == NOW.isoformat()
    assert data["fixtures"][0]["weather"] == old


def test_six_weather_effects_require_goal_and_binary_validation_for_same_team():
    packet, *_ = job._packet(experts_report(), NOW)
    weather = packet["fixtures"][0]["expert_context"]["weather"]
    weather.update(status="VALIDATED", live_weight_eligible=True, score_multiplier=1.02,
        effects=[{"side": side, "metric": metric, "analog_count": 100, "control_count": 200,
            "analog_ess": 80, "control_ess": 160, "shrunk_effect": .03, "interval": [.01, .05],
            "training_count": 150, "holdout_count": 50, "retrospective_passed": True,
            "forecast_passed": True, "current_anchor_matches": True}
            for side in ("home", "away") for metric in ("total_goals", "goals_for", "over25")])
    clean, *_ = validate(packet)
    assert len(clean["fixtures"][0]["expert_context"]["weather"]["effects"]) == 6
    for effect in weather["effects"]:
        if (effect["side"], effect["metric"]) in {("home", "over25"), ("away", "total_goals")}:
            effect["forecast_passed"] = False
    with pytest.raises(ValueError, match="Unvalidated weather uplift"):
        validate(packet)
