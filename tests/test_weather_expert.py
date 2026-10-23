"""Weather effects need sufficient independent matches and untouched forecast validation."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.agents.weather_expert import _elo_history, analyze_weather


def scenario(*, size=180, forecasts=True, wrong_forecasts=False, reverse_holdout=False,
             low_weather=False, competition="League", wet=False):
    start = datetime(2024, 1, 1, 15, tzinfo=UTC)
    matches = []
    for index in range(size):
        kickoff = start + timedelta(days=3 * index)
        analog = index % 2 == 0
        scored = 4 if analog != low_weather else 1
        if reverse_holdout and index >= 132:
            scored = 1 if analog != low_weather else 4
        match = {
            "fixture_id": f"api_football:{index}", "league_id": "api_football:league",
            "home_team_id": "api_football:A", "away_team_id": "api_football:B",
            "home_team": "Alpha", "away_team": "Beta", "competition_type": competition,
            "season": kickoff.year, "kickoff": kickoff.isoformat(),
            "observed_at": (kickoff + timedelta(hours=3)).isoformat(),
            "home_goals": scored, "away_goals": scored,
        }
        weather = {
            "status": "SOURCED", "weather_kind": "REANALYSIS",
            "observed_at": (kickoff + timedelta(hours=3)).isoformat(),
            "target_time": kickoff.isoformat(), "temperature_c": 10 if analog else 30,
            "wind_speed_kmh": 10, "precipitation_mm": 0.5 if wet else 0,
            "relative_humidity_pct": 60,
        }
        match["weather"] = weather
        if forecasts:
            match["prematch_weather"] = dict(
                weather, weather_kind="FORECAST",
                observed_at=(kickoff - timedelta(hours=1)).isoformat(),
                temperature_c=(30 if analog else 10) if wrong_forecasts else
                weather["temperature_c"],
            )
        matches.append(match)
    kickoff = start + timedelta(days=3 * size)
    as_of = kickoff - timedelta(hours=2)
    target = {
        "fixture_id": "api_football:future", "league_id": "api_football:league",
        "home_team_id": "api_football:A", "away_team_id": "api_football:B",
        "competition_type": competition, "kickoff": kickoff.isoformat(),
    }
    forecast = {
        "fixture_id": target["fixture_id"], "status": "SOURCED", "weather_kind": "FORECAST",
        "forecast_time": kickoff.isoformat(), "observed_at": as_of.isoformat(),
        "temperature_c": 10, "wind_speed_kmh": 10,
        "precipitation_mm": 0.5 if wet else 0, "relative_humidity_pct": 60,
    }
    dataset = {"matches": matches, "coverage": {"weather_matches": size}, "data_hash": "abc"}
    return [target], [forecast], dataset, as_of


def analyze(arguments):
    fixtures, forecasts, dataset, as_of = arguments
    return analyze_weather(fixtures, forecasts, dataset, as_of=as_of)


def fixture_result(arguments):
    return analyze(arguments)["fixtures"][0]


def test_three_actual_weather_matches_do_not_create_a_signal():
    arguments = scenario()
    for match in arguments[2]["matches"][:-3]:
        match.pop("weather")
    report = fixture_result(arguments)
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["score_multiplier"] == 1.0
    assert report["shadow_score_multiplier"] == 1.0
    assert not report["live_weight_eligible"]
    assert report["teams"]["home"]["matched_history_count"] == 3


def test_retrospective_signal_is_shadow_only_without_archived_forecast_validation():
    report = fixture_result(scenario(forecasts=False))
    home = report["teams"]["home"]
    assert home["signals"]["total_goals"]["signal"]
    assert home["validation"]["metrics"]["total_goals"]["retrospective"]["passed"]
    assert home["validation"]["metrics"]["total_goals"]["forecast"]["count"] == 0
    assert report["status"] == "UNVALIDATED"
    assert report["score_multiplier"] == 1.0
    assert report["shadow_score_multiplier"] > 1.0
    assert not report["live_weight_eligible"]


def test_real_signal_requires_both_untouched_holdout_checks_and_is_market_directional():
    report = fixture_result(scenario())
    assert report["status"] == "VALIDATED"
    assert report["live_weight_eligible"]
    assert 1 < report["market_effects"]["over25"] <= 1.1
    assert 0.9 <= report["market_effects"]["under25"] < 1
    assert not report["association_is_causal"]
    home, away = report["teams"].values()
    assert home["signals"]["total_goals"]["shrunk_effect"] == pytest.approx(
        away["signals"]["total_goals"]["shrunk_effect"])
    # The shared home/away fixture sample is averaged, not doubled as independent evidence.
    signal = home["signals"]["total_goals"]
    validation = home["validation"]["metrics"]["total_goals"]
    expected = 1 + signal["shrunk_effect"] / validation["retrospective"]["baseline_mean"] / 10
    assert report["score_multiplier"] == pytest.approx(min(1.1, expected))
    assert signal["analog"]["count"] >= 20 and signal["control"]["count"] >= 30
    assert signal["analog"]["ess"] >= 15 and signal["control"]["ess"] >= 15
    assert signal["shrunk_effect"] < signal["raw_effect"]
    assert signal["interval"][0] > 0
    assert validation["forecast"]["weather_mse"] < validation["forecast"]["baseline_mse"]
    assert home["validation"]["training_last_kickoff"] < home["validation"]["holdout_start"]
    assert not home["validation"]["fit_uses_holdout_outcomes"]


def test_failing_actual_forecasts_cannot_be_replaced_by_hindsight_weather():
    report = fixture_result(scenario(wrong_forecasts=True))
    checks = report["teams"]["home"]["validation"]["metrics"]["total_goals"]
    assert checks["retrospective"]["passed"]
    assert not checks["forecast"]["passed"]
    assert checks["forecast"]["weather_mse"] > checks["forecast"]["baseline_mse"]
    assert report["score_multiplier"] == 1.0
    assert report["shadow_score_multiplier"] > 1.0
    assert not report["live_weight_eligible"]


def test_reverse_holdout_rejects_the_signal_without_refitting_training():
    good = fixture_result(scenario())
    bad = fixture_result(scenario(reverse_holdout=True))
    assert good["teams"]["home"]["signals"] == bad["teams"]["home"]["signals"]
    checks = bad["teams"]["home"]["validation"]["metrics"]["total_goals"]
    assert not checks["retrospective"]["passed"]
    assert bad["score_multiplier"] == bad["shadow_score_multiplier"] == 1.0
    assert bad["status"] == "UNVALIDATED"


def test_holdout_results_cannot_select_training_rows_indirectly_through_current_elo():
    clean = fixture_result(scenario())
    changed_arguments = scenario()
    for match in changed_arguments[2]["matches"][132:]:
        match.update(home_goals=15, away_goals=0)
    changed = fixture_result(changed_arguments)
    # The fixed pre-holdout strength snapshot is used for fitting, not today's Elo.
    for side in ("home", "away"):
        before, after = clean["teams"][side], changed["teams"][side]
        assert before["strength_anchor"] == after["strength_anchor"]
        assert before["signals"] == after["signals"]
        assert not after["current_strength_matches_validation_anchor"]
    assert changed["score_multiplier"] == changed["shadow_score_multiplier"] == 1


def test_lower_total_goals_signal_has_opposite_over_under_effects():
    report = fixture_result(scenario(low_weather=True))
    assert report["live_weight_eligible"]
    assert report["effect_direction"] == "TOTAL_GOALS_LOWER"
    assert 0.9 <= report["market_effects"]["over25"] < 1.0
    assert 1.0 < report["market_effects"]["under25"] <= 1.1


def test_same_scores_in_all_conditions_do_not_become_a_weather_signal():
    arguments = scenario()
    for match in arguments[2]["matches"]:
        match.update(home_goals=2, away_goals=2)
    report = fixture_result(arguments)
    assert report["status"] == "NO_SIGNAL"
    assert report["score_multiplier"] == report["shadow_score_multiplier"] == 1


def test_goals_against_much_weaker_opponents_are_not_misread_as_weather_effect():
    arguments = scenario()
    matches = arguments[2]["matches"]
    beginning = datetime.fromisoformat(matches[0]["kickoff"])
    warmup = []
    for index in range(100):
        kickoff = beginning - timedelta(days=200 - index * 2)
        template = dict(matches[0], kickoff=kickoff.isoformat(),
                        observed_at=(kickoff + timedelta(hours=3)).isoformat())
        template.pop("weather")
        template.pop("prematch_weather")
        warmup.append(dict(template, fixture_id=f"api_football:warmAB{index}",
                           home_goals=1, away_goals=1))
        warmup.append(dict(template, fixture_id=f"api_football:warmCD{index}",
                           home_team_id="api_football:C", away_team_id="api_football:D",
                           home_goals=0, away_goals=3))
    for index, match in enumerate(matches):
        if index % 2 == 0:
            match.update(away_team_id="api_football:C", home_goals=4, away_goals=0)
        else:
            match.update(home_goals=1, away_goals=1)
    arguments[2]["matches"] = warmup + matches
    report = fixture_result(arguments)
    assert report["teams"]["home"]["excluded"][
        "OPPONENT_OR_RELATIVE_STRENGTH_MISMATCH"] > 0
    assert report["teams"]["home"]["signals"]["total_goals"]["analog"]["count"] == 0
    assert report["score_multiplier"] == report["shadow_score_multiplier"] == 1


def test_strong_effect_is_bounded_and_is_not_a_model_probability():
    arguments = scenario()
    for index, match in enumerate(arguments[2]["matches"]):
        goals = 15 if index % 2 == 0 else 0
        match.update(home_goals=goals, away_goals=goals)
    report = fixture_result(arguments)
    assert report["score_multiplier"] == pytest.approx(1.1)
    assert report["market_effects"]["under25"] == pytest.approx(0.9)
    assert "NOT_WIN_PROBABILITY" in report["score_semantics"]


def test_rare_big_scores_cannot_boost_over25_when_its_hit_rate_moves_the_other_way():
    arguments = scenario(size=360)
    for index, match in enumerate(arguments[2]["matches"]):
        # Similar weather has fewer Overs but some extreme scores, raising its mean.
        goals = (10 if index % 4 == 0 else 0) if index % 2 == 0 else 2
        match.update(home_goals=goals, away_goals=goals)
    report = fixture_result(arguments)
    home = report["teams"]["home"]
    assert home["signals"]["total_goals"]["shrunk_effect"] > 0
    assert home["signals"]["over25"]["shrunk_effect"] < 0
    assert home["signals"]["total_goals"]["signal"]
    assert home["signals"]["over25"]["signal"]
    assert home["reason"] == "TOTAL_MEAN_AND_OVER25_RATE_DISAGREE"
    assert report["score_multiplier"] == report["shadow_score_multiplier"] == 1


@pytest.mark.parametrize("field,value,reason", [
    ("observed_at", "2099-01-01T00:00:00+00:00", "UNKNOWN_OR_FUTURE_WEATHER_RECEIPT"),
    ("observed_at", "2020-01-01T00:00:00+00:00", "STALE_FORECAST"),
    ("observed_at", "2025-01-01", "UNKNOWN_OR_FUTURE_WEATHER_RECEIPT"),
    ("forecast_time", "2020-01-01T00:00:00+00:00", "WEATHER_TIME_MISMATCH"),
    ("weather_kind", "REANALYSIS", "NOT_A_FORECAST"),
    ("temperature_c", None, "MISSING_OR_INVALID_WEATHER_VALUES"),
    ("temperature_c", True, "MISSING_OR_INVALID_WEATHER_VALUES"),
    ("wind_speed_kmh", float("nan"), "MISSING_OR_INVALID_WEATHER_VALUES"),
    ("precipitation_mm", -1, "MISSING_OR_INVALID_WEATHER_VALUES"),
    ("relative_humidity_pct", 101, "INVALID_HUMIDITY"),
    ("status", "UNKNOWN", "UNSOURCED_WEATHER"),
])
def test_bad_current_weather_fails_closed(field, value, reason):
    arguments = scenario()
    arguments[1][0][field] = value
    report = fixture_result(arguments)
    assert report["reason"] == reason
    assert report["score_multiplier"] == 1.0
    assert not report["live_weight_eligible"]


def test_missing_current_forecast_is_neutral_and_no_history_is_invented():
    fixtures, _, dataset, as_of = scenario()
    report = analyze_weather(fixtures, {}, dataset, as_of=as_of)["fixtures"][0]
    assert report["reason"] == "MISSING_WEATHER"
    assert report["teams"] == {}
    assert report["score_multiplier"] == 1


def test_weather_from_another_fixture_cannot_be_used_under_current_fixture_key():
    fixtures, forecasts, dataset, as_of = scenario()
    lookup = {fixtures[0]["fixture_id"]: dict(forecasts[0], fixture_id="api_football:wrong")}
    report = analyze_weather(fixtures, lookup, dataset, as_of=as_of)["fixtures"][0]
    assert report["reason"] == "FORECAST_FIXTURE_MISMATCH"


def test_forecast_versions_use_latest_known_receipt_not_future_version():
    arguments = scenario()
    future = dict(arguments[1][0], observed_at=(arguments[3] + timedelta(hours=1)).isoformat(),
                  temperature_c=50)
    expected = fixture_result(arguments)
    arguments[1].append(future)
    assert fixture_result(arguments) == expected


def test_kickoff_lockout_keeps_every_multiplier_neutral():
    fixtures, forecasts, dataset, _ = scenario()
    kickoff = datetime.fromisoformat(fixtures[0]["kickoff"])
    report = analyze_weather(fixtures, forecasts, dataset,
                             as_of=kickoff - timedelta(minutes=15))["fixtures"][0]
    assert report["reason"] == "PREMATCH_LOCKOUT"
    assert not report["live_weight_eligible"]


def test_future_result_and_weather_receipts_are_never_used():
    arguments = scenario()
    future = (arguments[3] + timedelta(hours=1)).isoformat()
    for match in arguments[2]["matches"][:90]:
        match["observed_at"] = future
    for match in arguments[2]["matches"][90:]:
        match["weather"]["observed_at"] = future
    report = analyze(arguments)
    assert report["accepted_history_count"] == 90
    assert report["excluded_history"]["UNKNOWN_OR_FUTURE_RESULT_RECEIPT"] == 90
    assert report["fixtures"][0]["score_multiplier"] == 1
    assert report["fixtures"][0]["teams"]["home"]["matched_history_count"] == 0


def test_just_played_or_future_fixture_cannot_supply_a_known_result():
    arguments = scenario()
    for match in arguments[2]["matches"]:
        match["kickoff"] = (arguments[3] - timedelta(hours=1)).isoformat()
    report = analyze(arguments)
    assert report["accepted_history_count"] == 0
    assert report["excluded_history"]["RESULT_NOT_YET_USABLE"] == 180


def test_unknown_competition_type_is_usable_only_inside_exact_same_league():
    arguments = scenario(competition="UNKNOWN")
    assert fixture_result(arguments)["status"] == "VALIDATED"
    arguments[0][0]["league_id"] = "api_football:different"
    assert fixture_result(arguments)["status"] == "INSUFFICIENT_DATA"


def test_known_cup_and_league_type_conflict_is_not_merged():
    arguments = scenario()
    arguments[0][0]["competition_type"] = "Cup"
    report = fixture_result(arguments)
    assert report["teams"]["home"]["matched_history_count"] == 0
    assert report["teams"]["home"]["excluded"]["DIFFERENT_COMPETITION_TYPE"] == 180
    assert report["score_multiplier"] == 1


def test_no_strength_estimation_from_fewer_than_twenty_prior_matches():
    report = fixture_result(scenario(size=19))
    assert report["teams"]["home"]["reason"] == "INSUFFICIENT_ELO_HISTORY"
    assert report["score_multiplier"] == 1


def test_missing_humidity_is_not_silently_classed_as_nonanalog_control():
    arguments = scenario()
    for match in arguments[2]["matches"]:
        match["weather"].pop("relative_humidity_pct")
    report = fixture_result(arguments)
    assert report["teams"]["home"]["matched_history_count"] == 0
    assert report["teams"]["home"]["excluded"]["MISSING_COMPARABLE_HUMIDITY"] == 160
    # Humidity is optional if it is also absent from the requested forecast.
    arguments[1][0].pop("relative_humidity_pct")
    assert fixture_result(arguments)["status"] == "VALIDATED"


def test_prematch_forecast_must_exist_before_fifteen_minute_lockout():
    arguments = scenario()
    for match in arguments[2]["matches"]:
        kickoff = datetime.fromisoformat(match["kickoff"])
        match["prematch_weather"]["observed_at"] = (kickoff - timedelta(minutes=5)).isoformat()
    report = fixture_result(arguments)
    assert report["shadow_score_multiplier"] > 1
    assert report["score_multiplier"] == 1
    assert report["teams"]["home"]["validation"]["metrics"]["total_goals"]["forecast"][
        "count"] == 0


def test_forecast_validation_cannot_cherry_pick_small_available_holdout_fraction():
    arguments = scenario()
    for index, match in enumerate(arguments[2]["matches"]):
        if index % 2 == 0:
            match.pop("prematch_weather")
    report = fixture_result(arguments)
    checks = report["teams"]["home"]["validation"]["metrics"]["total_goals"]["forecast"]
    assert checks["coverage"] == 0.5
    assert not checks["passed"]
    assert report["score_multiplier"] == 1


def test_duplicate_results_are_not_counted_twice_and_conflicting_versions_quarantine():
    arguments = scenario()
    arguments[2]["matches"] += deepcopy(arguments[2]["matches"])
    report = analyze(arguments)
    assert report["accepted_history_count"] == 180
    assert report["excluded_history"]["DUPLICATE_MATCH_VERSION"] == 180
    arguments[2]["matches"][0]["home_goals"] = 0
    report = analyze(arguments)
    assert report["accepted_history_count"] == 179
    assert report["excluded_history"]["CONFLICTING_MATCH_VERSIONS"] == 2


def test_elo_uses_only_finished_earlier_matches_including_simultaneous_batch():
    kickoff = datetime(2025, 1, 1, 15, tzinfo=UTC)
    rows = []
    for index, (hours, opponent) in enumerate(((0, "B"), (0, "C"), (1, "D"), (3, "E"))):
        rows.append({"fixture_id": str(index), "league_id": "L", "home_team_id": "A",
                     "away_team_id": opponent, "kickoff": kickoff + timedelta(hours=hours),
                     "home_goals": 3, "away_goals": 0})
    enriched, _, _, _ = _elo_history(rows)
    assert [row["home_elo"] for row in enriched[:3]] == [1500, 1500, 1500]
    assert [row["home_prior_count"] for row in enriched[:3]] == [0, 0, 0]
    assert enriched[3]["home_elo"] == 1530
    assert enriched[3]["home_prior_count"] == 3


def test_inputs_including_protected_daily_metadata_are_untouched_and_order_deterministic():
    arguments = scenario()
    arguments[0][0]["ticket_profiles"] = ["DAILY_223"]
    original = deepcopy(arguments)
    first = analyze(arguments)
    assert arguments == original
    arguments[2]["matches"].reverse()
    assert analyze(arguments) == first
    assert first["data_hash"] == "abc"
    assert len(first["policy_hash"]) == 64
    assert first["science_basis"] == ["PMC11474995", "APPLSCI_10_19_6750", "STATPAIR_WEATHER"]


def test_more_fixture_searches_widen_intervals_for_the_same_sample():
    fixtures, forecasts, dataset, as_of = scenario()
    small = analyze_weather(fixtures, forecasts, dataset, as_of=as_of)
    fixtures = [dict(fixtures[0], fixture_id=f"api_football:target{index}") for index in range(25)]
    forecasts = [dict(forecasts[0], fixture_id=fixture["fixture_id"]) for fixture in fixtures]
    large = analyze_weather(fixtures, forecasts, dataset, as_of=as_of)
    assert large["interval_family_comparisons"] == 150
    a = small["fixtures"][0]["teams"]["home"]["signals"]["total_goals"]
    b = large["fixtures"][0]["teams"]["home"]["signals"]["total_goals"]
    assert a["shrunk_effect"] == b["shrunk_effect"]
    assert b["interval"][0] < a["interval"][0]
    assert b["interval"][1] > a["interval"][1]


def test_naive_asof_timestamp_is_rejected():
    fixtures, forecasts, dataset, as_of = scenario()
    with pytest.raises(ValueError, match="timezone-aware"):
        analyze_weather(fixtures, forecasts, dataset, as_of=as_of.replace(tzinfo=None))
