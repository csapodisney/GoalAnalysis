from copy import deepcopy

import pytest

from goal_analysis.decision_trace import build_decision_data_usage


def sample():
    report = {
        "odds_history_snapshot": {"quotes": [{"id": "q"}]},
        "sports_supplement": {"the_stats_api": {"context_by_fixture": {"f": {"facts": [
            {"category": "team_season_stats", "status": "AVAILABLE"},
            {"category": "player_season_stats", "status": "AVAILABLE"},
            {"category": "injuries_suspensions", "status": "AVAILABLE"},
            {"category": "match_odds", "status": "AVAILABLE"},
        ]}}}},
        "expert_ranking": {"weather_weighted_options": 1, "the_stats_api": {
            "evidence_attached_options": 2, "player_context_legs": 2,
            "player_availability_adjusted_legs": 1}},
        "expert_advisory": {"weather": {"fixtures": [{"fixture_id": "f",
            "weather": {'weather_kind':'FORECAST','status':'SOURCED',
                'observed_at':'2026-10-22T11:00:00+00:00','target_time':'2026-10-22T15:00:00+00:00',
                'temperature_c':12,'wind_speed_kmh':20,'precipitation_mm':0},
            "live_weight_eligible": True, "teams": {
                "home": {'matched_history_count':20}, "away": {'matched_history_count':20}}}]},
                            "motivation": {"fixtures": [{"fixture_id": "f",
                                "human_factors": [{"claim": "verified"}]}]}},
        "grok_review": {"status": "COMPLETE", "changed_profiles": ["club"],
                        "critiques": [{"profile_id": "club"}]},
        'finished_at':'2026-10-22T12:00:00+00:00',
    }
    artifacts = {
        "fixtures": {"records": [{"fixture_id": "f",'kickoff':'2026-10-22T15:00:00+00:00'}]},
        "candidate_input": {"candidates": [{"candidate_id": "c"}]},
        "history": {"records": [{"fixture_id": "old"}]},
        "numeric_models": {"gauss": {"predictions": [{"fixture_id": "f"}]}},
    }
    review = {"status": "COMPLETE", "reviews": [{"candidate_id": "c"}]}
    return report, artifacts, review


def test_trace_reports_influence_instead_of_collection_and_is_pure():
    report, artifacts, review = sample()
    before = deepcopy((report, artifacts, review))
    result = build_decision_data_usage(report, artifacts, candidate_review=review,
                                       feedback_used=True)
    assert (report, artifacts, review) == before
    assert result["semantics"] == "INFLUENCE_NOT_COLLECTION"
    assert result["sources"]["current_odds"]["status"] == "USED_DIRECT"
    assert result["sources"]["local_odds_history"] == {
        "status": "MISSING", "role": "line_movement_and_contrarian_value",
        "observations": 0, "reason": "NO_VERIFIED_COMPARABLE_HISTORY",
    }
    assert result["sources"]["the_stats_team_xg"]["status"] == "USED_RANKING"
    assert result["sources"]["the_stats_player_stats"]["status"] == "USED_RANKING"
    assert result["sources"]["the_stats_availability"]["status"] == "USED_RANKING"
    assert result["sources"]["team_specific_weather_history"] == {
        "status": "USED_RANKING",
        "role": "home_away_opponent_strength_matched_weather_holdout",
        "observations": 2,
    }
    assert result["sources"]["grok_package_review"]["status"] == "USED_DIRECT"
    assert result["sources"]["settled_performance_feedback"]["status"] == "USED_REVIEW"
    assert result["sources"]["the_stats_current_odds"]["status"] == "STORED_ONLY"
    assert result["daily223_protected"] is True


def test_missing_inputs_are_never_reported_as_used():
    result = build_decision_data_usage({}, {})
    assert result["sources"]["fixture_calendar"]["status"] == "MISSING"
    assert result["sources"]["current_odds"]["status"] == "MISSING"
    assert result["sources"]["the_stats_player_stats"]["status"] == "MISSING"
    assert result["sources"]["motivation_and_news"]["status"] == "MISSING"
    assert result["sources"]["the_stats_current_odds"]["status"] == "MISSING"
    assert result["sources"]["the_stats_and_football_data_archives"]["status"] == "STORED_ONLY"


def movement_candidates(*, runs=None, price=3.6):
    from test_odds_movement import NOW, history, priced

    from goal_analysis.engine.odds_movement import annotate_odds_movement

    return annotate_odds_movement(
        {"candidates": [priced(price)]}, [history()] if runs is None else runs, NOW,
    )["candidates"]


@pytest.mark.parametrize("snapshot", [
    None, {}, {"quotes": []}, {"quotes": [{"id": "current-only"}]},
])
def test_current_snapshot_and_successful_history_read_do_not_prove_movement(snapshot):
    report = {"odds_history_snapshot": snapshot, "tipster_club": {
        "contrarian_history_status": "LOCAL_SNAPSHOTS_READ",
        "contrarian_eligible_source_candidates": 1,
    }}
    result = build_decision_data_usage(report, {}, odds_movement_candidates=movement_candidates(runs=[]))
    assert result["sources"]["local_odds_history"]["status"] == "MISSING"
    assert result["sources"]["local_odds_history"]["observations"] == 0


@pytest.mark.parametrize("price", [3.6, 3.0, 2.8])
def test_verified_history_without_ranking_proof_is_available_not_used(price):
    candidates = movement_candidates(price=price)
    result = build_decision_data_usage({}, {}, odds_movement_candidates=candidates)
    entry = result["sources"]["local_odds_history"]
    assert entry["status"] == "STORED_ONLY"
    assert entry["observations"] == 1


def test_ranked_contrarian_candidates_prove_use_without_requiring_a_published_ticket():
    candidates = movement_candidates()
    report = {"tipster_club": {"contrarian_eligible_source_candidates": 1}}
    original = deepcopy((report, candidates))
    result = build_decision_data_usage(report, {}, odds_movement_candidates=candidates * 2)
    assert result["sources"]["local_odds_history"]["status"] == "USED_RANKING"
    assert result["sources"]["local_odds_history"]["observations"] == 1
    assert (report, candidates) == original


def test_selected_leg_proof_is_deduplicated_across_report_views():
    movement = movement_candidates()[0]["odds_movement"]
    ticket = {"legs": [{"contrarian_analysis": {"odds_movement": movement}}]}
    report = {"tickets": [deepcopy(ticket)], "recommendations": [deepcopy(ticket)],
              "profile_proposals": [deepcopy(ticket)]}
    result = build_decision_data_usage(report, {})
    assert result["sources"]["local_odds_history"]["status"] == "USED_RANKING"
    assert result["sources"]["local_odds_history"]["observations"] == 1


@pytest.mark.parametrize("change", [
    {"status": "INSUFFICIENT_HISTORY"}, {"status": "INVALID_QUOTE"},
    {"source_run_id": ""}, {"evidence_id": "not-the-evidence-hash"},
    {"baseline_price": 1.01},
])
def test_missing_invalid_or_modified_evidence_is_not_reported_as_used(change):
    candidates = movement_candidates()
    candidates[0]["odds_movement"].update(change)
    result = build_decision_data_usage(
        {"tipster_club": {"contrarian_eligible_source_candidates": 1}}, {},
        odds_movement_candidates=candidates,
    )
    assert result["sources"]["local_odds_history"]["status"] == "MISSING"


@pytest.mark.parametrize("variant", ["future", "too_old", "wrong_bookmaker"])
def test_adapter_rejected_historical_rows_never_become_coverage(variant):
    from datetime import timedelta

    from test_odds_movement import NOW, history

    old = history(age=49) if variant == "too_old" else history()
    if variant == "future":
        old["observed_at"] = old["finished_at"] = (NOW + timedelta(hours=1)).isoformat()
    if variant == "wrong_bookmaker":
        old = history(bookmaker="different_book")
    result = build_decision_data_usage({}, {},
        odds_movement_candidates=movement_candidates(runs=[old]))
    assert result["sources"]["local_odds_history"]["status"] == "MISSING"
