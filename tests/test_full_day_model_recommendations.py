"""Pure v2 numeric-card boundaries; no model fitting, provider, DB or runtime work."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.engine.model_recommendations import MARKETS, build_model_recommendations

NOW = datetime(2026, 10, 29, 10, tzinfo=UTC)
PROFILES = ("gauss", "kepler", "laplace")


def fixture(number=1):
    return {"fixture_id": f"api_football:{number}", "api_football_fixture_id": str(number),
            "api_football_home_team_id": str(number * 10), "api_football_away_team_id": str(number * 10 + 1),
            "home_team": f"TESTONLY Home {number}", "away_team": f"TESTONLY Away {number}",
            "competition": "TESTONLY League", "competition_type": "LEAGUE", "sport_key": "soccer_test",
            "kickoff": (NOW + timedelta(hours=8)).isoformat(), "provider_status": "NS",
            "target_date": NOW.date().isoformat()}


def prediction(number=1):
    return {"fixture_id": fixture(number)["fixture_id"],
            "probabilities": {"home": .6, "draw": .24, "away": .16,
                              "over25": .57, "under25": .43, "btts_yes": .53},
            "stability": dict.fromkeys(MARKETS, .03),
            "model_details": {"training_matches": 80, "source_id": "TESTONLY-model-output"}}


def quote(number=1, price=3):
    return {**fixture(number), "candidate_id": f"TESTONLY-quote-{number}",
            "market_key": "h2h", "selection_key": "home", "period": "FULL_TIME",
            "bookmaker": "TESTONLY-book", "region": "eu", "currency": "EUR", "decimal_price": price,
            "quote_provider": "the_odds_api", "quote_available": True,
            "quote_source_id": f"https://api.the-odds-api.com/v4/sports/soccer_test/events/test{number}/odds",
            "quoted_at": NOW.isoformat()}


def settings(fixtures, *, version=2, profiles=PROFILES):
    events = [{**item, "status": "ROUTED", "competition_name": "TESTONLY League",
               "source_id": "TESTONLY-calendar"} for item in fixtures]
    result = {"enabled_profiles": list(profiles), "date": NOW.date().isoformat(),
              "calendar_universe": events,
              "calendar_scope_receipt": {"scope": "ACCESSIBLE_PROVIDER_FOOTBALL_DAY", "status": "COMPLETE",
                                         "provider_calendar_status": "COMPLETE", "full_product_run": True,
                                         "provider_fixture_ids": [item["fixture_id"] for item in events]}}
    if version is not None:
        result["operating_contract_version"] = version
    return result


def build(fixtures=None, quotes=None, predictions=None, *, config=None, review=None, options=None, status="READY"):
    fixtures = [fixture(index) for index in range(1, 4)] if fixtures is None else fixtures
    quotes = [quote(index) for index in range(1, 4)] if quotes is None else quotes
    predictions = [prediction(index) for index in range(1, 4)] if predictions is None else predictions
    results = {profile: {"profile_id": profile, "status": status, "predictions": deepcopy(predictions)}
               for profile in PROFILES}
    return build_model_recommendations(results, fixtures, quotes, config or settings(fixtures), NOW, review,
                                       diversity_options=options)


def test_all_three_models_account_for_same45_events_with_own_missing_predictions():
    fixtures = [fixture(index) for index in range(1, 45)]
    config = settings([fixture(index) for index in range(1, 46)])
    config["calendar_universe"][-1].update(status="UNMAPPED_COMPETITION", competition_name="TESTONLY Unmapped Cup")
    config["calendar_scope_receipt"].update(status="PARTIAL", full_product_run=False)
    original = deepcopy((fixtures, config))
    cards = build(fixtures, config=config)
    assert len(cards) == 3
    expected = sorted(f"api_football:{index}" for index in range(1, 46))
    for card in cards:
        trace = card["selection_diagnostics"]
        assert trace["input_fixture_ids"] == expected and trace["input_fixture_count"] == 45
        assert not trace["all_daily_fixtures_considered"]
        missing = next(row for row in trace["fixture_decisions"] if row["fixture_id"] == "api_football:45")
        assert {"UNMAPPED_COMPETITION", "NO_VALID_NORMALIZED_FIXTURE", "MODEL_PREDICTION_UNKNOWN", "NO_VALID_QUOTES"} <= set(missing["reasons"])
        assert card["status"] == "DRAFT" and card["combined_price"] == 27
        assert card["model_status"] == "READY" and card["probability"] is None
        assert [leg["model_probability"] for leg in card["legs"]] == [.6, .6, .6]
        assert all(leg["estimated_edge"] == pytest.approx(.8) for leg in card["legs"])
        assert not card["playable"] and card["stake_eur"] is None
    assert (fixtures, config) == original


@pytest.mark.parametrize("price", [15, 40])
def test_model_singleton_boundaries_preserve_one_to_three_leg_shape(price):
    cards = build([fixture()], [quote(price=price)], [prediction()])
    for card in cards:
        assert card["combined_price"] == price and len(card["legs"]) == 1
        assert card["minimum_legs"] == 1 and card["maximum_legs"] == 3
        assert card["minimum_combined_price"] == 15 and card["maximum_combined_price"] == 40
        assert card["target_odds_minimum_enforced"] is True


@pytest.mark.parametrize("price", [14.99, 40.01])
def test_outside_current_odds_is_not_issued_as_a_complete_model_card(price):
    for card in build([fixture()], [quote(price=price)], [prediction()]):
        assert not card["legs"] and card["combined_price"] is None
        assert card["status"] == "NO_STABLE_SELECTION"


def test_three_leg_cap_is_not_increased_to_manufacture_odds_floor():
    for card in build([fixture(index) for index in range(1, 5)],
                      [quote(index, 2) for index in range(1, 5)],
                      [prediction(index) for index in range(1, 5)]):
        assert card["legs"] == [] and card["combined_price"] is None


def test_no_quote_or_model_never_becomes_an_inverted_odds_probability():
    for card in build(quotes=[]):
        assert card["legs"] == [] and card["probability"] is None and card["combined_price"] is None
        assert "NO_VALID_QUOTES" in card["selection_diagnostics"]["fixture_decisions"][0]["reasons"]
    for card in build(predictions=[]):
        assert card["legs"] == [] and card["probability"] is None
        assert "MODEL_PREDICTION_UNKNOWN" in card["selection_diagnostics"]["fixture_decisions"][0]["reasons"]


@pytest.mark.parametrize("stability", [None, {"home": .081}, {"home": True}])
def test_gauss_own_stability_rule_remains_a_local_gate(stability):
    model = prediction()
    model["stability"] = stability
    cards = build([fixture()], [quote(price=15)], [model])
    gauss = next(card for card in cards if card["profile_id"] == "gauss")
    assert not gauss["legs"]
    assert gauss["selection_diagnostics"]["candidate_rejections"]["TESTONLY-quote-1"] == "GAUSS_STABILITY_REQUIRED"
    assert all(card["legs"] for card in cards if card["profile_id"] != "gauss")


def test_kepler_stays_1x2_while_other_models_can_select_goal_market():
    source = quote(price=15)
    source.update(market_key="totals_2_5", selection_key="over")
    cards = build([fixture()], [source], [prediction()])
    assert not next(card for card in cards if card["profile_id"] == "kepler")["legs"]
    assert all(card["legs"][0]["market_key"] == "totals_2_5" for card in cards if card["profile_id"] != "kepler")


def test_original_estimated_edge_floor_remains_explicit():
    model = prediction()
    model["probabilities"].update(home=.4, draw=.36, away=.24)
    for card in build([fixture()], [quote(price=2)], [model]):
        assert not card["legs"]
        assert card["selection_diagnostics"]["candidate_rejections"]["TESTONLY-quote-1"] == "ESTIMATED_EDGE_BELOW_FLOOR"


def test_lockout_and_calendar_identity_remain_hard_local_rejections():
    item, source = fixture(), quote(price=15)
    item["kickoff"] = source["kickoff"] = (NOW + timedelta(minutes=15)).isoformat()
    for card in build([item], [source], [prediction()]):
        assert not card["legs"]
        assert "PREMATCH_LOCKOUT" in card["selection_diagnostics"]["fixture_decisions"][0]["reasons"]
    config = settings([fixture()])
    config["calendar_universe"][0]["kickoff"] = (NOW + timedelta(hours=7)).isoformat()
    assert all(not card["legs"] for card in build([fixture()], [quote(price=15)], [prediction()], config=config))


def test_review_cannot_change_model_math_and_missing_optional_review_keeps_draft():
    fixtures, quotes, predictions = [fixture(index) for index in range(1, 4)], [quote(index) for index in range(1, 4)], [prediction(index) for index in range(1, 4)]
    original = deepcopy((fixtures, quotes, predictions))
    plain = build(fixtures, quotes, predictions)
    forged = build(fixtures, quotes, predictions, review={"status": "COMPLETE", "reviews": [{"model_probability": 1, "decimal_price": 99}]})
    assert [card["legs"] for card in plain] == [card["legs"] for card in forged]
    assert all(card["status"] == "DRAFT" for card in forged)
    assert (fixtures, quotes, predictions) == original


def test_explicit_v1_and_unmarked_json_shape_are_exactly_equal_with_original_small_odds():
    fixtures = [fixture(index) for index in range(1, 4)]
    quotes = [quote(index, 2) for index in range(1, 4)]
    legacy = build(fixtures, quotes, config=settings(fixtures, version=1))
    unmarked = build(fixtures, quotes, config=settings(fixtures, version=None))
    assert json.dumps(legacy, ensure_ascii=False) == json.dumps(unmarked, ensure_ascii=False)
    assert all(card["combined_price"] == 8 and card["status"] == "MODEL_READY" for card in legacy)
    assert all("input_fixture_ids" not in card["selection_diagnostics"] and "operating_contract_version" not in card for card in legacy)
    current = build(fixtures)
    old_same_prices = build(fixtures, config=settings(fixtures, version=1))
    assert [card["legs"] for card in current] == [card["legs"] for card in old_same_prices]


def test_alternative_cards_keep_their_own_selected_ids_in_same_calendar_receipt():
    fixtures = [fixture(index) for index in range(1, 4)]
    options = {}
    build(fixtures, [quote(index, 5) for index in range(1, 4)],
          config=settings(fixtures, profiles=("kepler",)), options=options)
    assert len(options["kepler"]) >= 2
    for alternative in options["kepler"]:
        card = alternative["recommendation"]
        assert card["status"] == "DRAFT" and 15 <= card["combined_price"] <= 40
        selected = {leg["fixture_id"] for leg in card["legs"]}
        trace = card["selection_diagnostics"]
        assert {row["fixture_id"] for row in trace["fixture_decisions"] if row["status"] == "SELECTED"} == selected
        assert set(trace["selected_source_candidate_ids"]) == {leg["source_candidate_id"] for leg in card["legs"]}
