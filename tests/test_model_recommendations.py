"""Independent selection, matched real prices and honest missing-evidence cards."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.engine.model_recommendations import MARKETS, build_model_recommendations

NOW = datetime(2026, 9, 29, 10, tzinfo=UTC)


def fixture(number=1, **changes):
    return {
        "fixture_id": f"api_football:{number}",
        "api_football_fixture_id": str(number),
        "home_team": f"Home {number}",
        "away_team": f"Away {number}",
        "api_football_home_team_id": str(number * 10),
        "api_football_away_team_id": str(number * 10 + 1),
        "competition": "test_league",
        "competition_type": "LEAGUE",
        "sport_key": "soccer_test",
        "kickoff": (NOW + timedelta(hours=8)).isoformat(),
        "provider_status": "NS",
        "target_date": "2026-09-29",
        **changes,
    }


def prediction(number=1, home=0.60, **changes):
    return {
        "fixture_id": f"api_football:{number}",
        "probabilities": {
            "home": home,
            "draw": (1 - home) * 0.6,
            "away": (1 - home) * 0.4,
            "over25": 0.57,
            "under25": 0.43,
            "btts_yes": 0.53,
        },
        "stability": dict.fromkeys(MARKETS, 0.03),
        "model_details": {"training_matches": 80, "method": "test-model"},
        **changes,
    }


def quote(number=1, book="book_a", price=2, market="h2h", selection="home", **changes):
    return {
        **fixture(number),
        "candidate_id": f"legacy-{number}-{book}-{market}-{selection}",
        "event_status": "SCHEDULED",
        "market_key": market,
        "selection_key": selection,
        "period": "FULL_TIME",
        "bookmaker": book,
        "region": "eu",
        "currency": "EUR",
        "decimal_price": price,
        "quote_provider": "the_odds_api",
        "quote_source_id": f"https://api.the-odds-api.com/v4/sports/soccer_test/events/event{number}/odds",
        "quote_available": True,
        "quoted_at": NOW.isoformat(),
        **changes,
    }


def build(profile="kepler", predictions=None, fixtures=None, quotes=None, review=None,
          preferred=None, **result):
    return build_model_recommendations(
        {
            profile: {
                "profile_id": profile,
                "status": "READY",
                "message": "",
                "predictions": predictions if predictions is not None else [prediction()],
                "diagnostics": [],
                **result,
            }
        },
        fixtures if fixtures is not None else [fixture()],
        quotes or [],
        {"enabled_profiles": [profile], "preferred_bookmakers": preferred or []},
        NOW,
        review,
    )[0]


def test_independent_full_fixture_ranking_not_support_scores_or_review():
    fixtures = [fixture(n) for n in range(1, 6)]
    predictions = [prediction(n, home=0.50 + n * 0.06) for n in range(1, 6)]
    # Candidate coverage is irrelevant to unpriced model selection; only fixture 1
    # has legacy evidence, while a reviewer tries to invent favorable probabilities.
    legacy = quote(1, support_score=1000, decimal_price=None)
    review = {
        "reviews": [{"candidate_id": "legacy-1", "model_probability": 1.0}],
        "fixture_context": [
            {
                "fixture_id": "api_football:5",
                "topic": "lineup",
                "status": "SOURCED",
                "summary": "Context only",
            }
        ],
    }
    original = deepcopy(predictions)
    report = build(predictions=predictions, fixtures=fixtures, quotes=[legacy], review=review)
    assert [l["fixture_id"] for l in report["legs"]] == [f"api_football:{n}" for n in (3, 4, 5)]
    assert [l["model_probability"] for l in report["legs"]] == pytest.approx([0.68, 0.74, 0.8])
    assert report["legs"][-1]["fixture_context"][0]["summary"] == "Context only"
    assert predictions == original
    assert report["status"] == "ODDS_PENDING"
    assert report["probability"] is report["combined_price"] is report["stake_eur"] is None
    assert report["confidence_kind"] == "UNCALIBRATED_MODEL_ESTIMATE"
    assert report["confidence"] != "HIGH"
    assert not report["selected_for_portfolio"] and not report["playable"]
    shuffled = build(
        predictions=list(reversed(predictions)), fixtures=list(reversed(fixtures)), quotes=[legacy]
    )
    assert report["recommendation_id"] == shuffled["recommendation_id"]


def test_review_cannot_reprice_rerank_or_modify_priced_model_probability():
    quotes = [quote(1, price=2), quote(2, price=2)]
    fixtures = [fixture(1), fixture(2)]
    predictions = [prediction(1, home=0.70), prediction(2, home=0.60)]
    first = build(predictions=predictions, fixtures=fixtures, quotes=quotes)
    reviews = {
        "reviews": [
            {
                "candidate_id": first["legs"][0]["candidate_id"],
                "assessment": "reject",
                "model_probability": 0.01,
            }
        ],
        "web_odds": [{**quote(2), "decimal_price": 99}],
    }
    second = build(predictions=predictions, fixtures=fixtures, quotes=quotes, review=reviews)
    assert first["legs"] == second["legs"]
    assert all(l["candidate_id"].startswith("kepler-") for l in first["legs"])
    assert all(l["source_candidate_id"].startswith("legacy-") for l in first["legs"])


def test_priced_negative_edge_cannot_be_hidden_as_missing_odds():
    result = build(quotes=[quote(price=1.5)])
    assert result["status"] == "NO_STABLE_SELECTION"
    assert not result["legs"] and result["combined_price"] is None
    assert not result["odds_pending"]


def test_price_edge_floor_and_probability_floor_both_apply():
    at_floor = build(predictions=[prediction(home=0.40)], quotes=[quote(price=2.575)])
    assert at_floor["status"] == "MODEL_READY"
    assert at_floor["legs"][0]["estimated_edge"] == pytest.approx(0.03)
    assert build(predictions=[prediction(home=0.39)], quotes=[quote(price=99)])["legs"] == []
    assert build(quotes=[quote(price=1.70)])["legs"] == []
    assert build(predictions=[prediction(home=0.49)])["legs"] == []
    assert build(predictions=[prediction(home=0.50)])["status"] == "ODDS_PENDING"


def test_gauss_scans_all_markets_and_compares_strong_multileg_tickets():
    predictions = [prediction(n) for n in (1, 2, 3)]
    predictions[1]["probabilities"].update(over25=0.70, under25=0.30)
    predictions[2]["probabilities"]["btts_yes"] = 0.75
    quotes = [
        quote(1),
        quote(1, market="btts", selection="yes", price=2.1),
        quote(2, market="totals_2_5", selection="over", price=1.8),
        quote(3, market="btts", selection="yes", price=1.6),
    ]
    result = build("gauss", predictions, [fixture(n) for n in (1, 2, 3)], quotes)
    assert len(result["legs"]) == 3
    assert result["legs"][1]["fixture_id"] == "api_football:2"
    assert result["legs"][1]["market_key"] == "totals_2_5"
    assert result["combined_price"] == pytest.approx(2 * 1.8 * 1.6)
    assert result["model_summary"]["priced_selections"] == 4
    assert result["probability"] is None
    assert all(l["model_stability"] == 0.03 for l in result["legs"])


def test_kepler_ignores_goal_and_first_half_markets():
    p = prediction(home=0.45)
    p["probabilities"].update(over25=0.9, under25=0.1, btts_yes=0.9)
    quotes = [
        quote(market="btts", selection="yes", price=2),
        quote(market="totals_2_5", selection="over", price=2),
        quote(period="FIRST_HALF", price=9),
    ]
    assert build(predictions=[p], quotes=quotes)["legs"] == []


@pytest.mark.parametrize("stability", [0.081, None, float("nan"), -0.1, True])
def test_gauss_requires_stability_for_every_selected_market(stability):
    p = prediction(stability=dict.fromkeys(MARKETS, stability))
    assert build("gauss", [p], quotes=[quote(price=5)])["status"] == "NO_STABLE_SELECTION"
    p["stability"]["home"] = 0.08
    result = build("gauss", [p], quotes=[quote(price=2)])
    assert len(result["legs"]) == 1 and result["legs"][0]["model_stability"] == 0.08


def test_bookmaker_pools_never_mix_and_do_not_force_three_legs():
    fixtures = [fixture(n) for n in range(1, 5)]
    predictions = [prediction(n) for n in range(1, 5)]
    quotes = [
        quote(1, "book_a", price=3),
        quote(2, "book_b", price=2.1),
        quote(3, "book_b", price=2.1),
    ]
    result = build(predictions=predictions, fixtures=fixtures, quotes=quotes)
    assert result["bookmaker"] == "book_b"
    assert len(result["legs"]) == 2
    assert result["combined_price"] == pytest.approx(2.1 ** 2)
    assert {l["bookmaker"] for l in result["legs"]} == {"book_b"}


@pytest.mark.parametrize(
    "change",
    [
        {"fixture_id": "api_football:999"},
        {"home_team": "Unrelated"},
        {"away_team": "Unrelated"},
        {"api_football_home_team_id": "999"},
        {"kickoff": (NOW + timedelta(hours=8, minutes=1)).isoformat()},
        {"quote_provider": "astra"},
        {"quote_source_id": "https://example.org/odds"},
        {"market_key": "totals_3_5"},
        {"selection_key": "away"},
        {"period": "FIRST_HALF"},
        {"event_status": "LIVE"},
        {"quote_available": False},
        {"quote_available": "true"},
        {"decimal_price": 1},
        {"decimal_price": True},
        {"decimal_price": float("nan")},
    ],
)
def test_mismatched_or_invalid_quote_never_supplies_price(change):
    result = build(quotes=[quote(**change)])
    assert result["status"] == "ODDS_PENDING"
    assert result["legs"][0]["decimal_price"] is None
    assert result["legs"][0]["estimated_edge"] is None


@pytest.mark.parametrize(
    "stamp,expected",
    [
        (None, "MISSING"),
        ("bad", "INVALID"),
        ((NOW - timedelta(days=2)).isoformat(), "OLDER"),
        ((NOW + timedelta(hours=1)).isoformat(), "FUTURE"),
    ],
)
def test_timestamp_is_informational_not_a_price_gate(stamp, expected):
    q = quote(quoted_at=stamp)
    q.pop("quote_available")
    result = build(quotes=[q])
    assert result["status"] == "MODEL_READY"
    assert result["legs"][0]["quote_timestamp_status"] == expected


def test_api_football_quote_fixture_and_market_source_validation():
    q = quote(
        book="bet365_api_football_42",
        quote_provider="api_football",
        provider_market_id="1",
        quote_source_id="https://v3.football.api-sports.io/odds?fixture=1&bookmaker=42&bet=1",
    )
    assert build(quotes=[q])["status"] == "MODEL_READY"
    q["quote_source_id"] = q["quote_source_id"].replace("fixture=1", "fixture=2")
    assert build(quotes=[q])["status"] == "ODDS_PENDING"


@pytest.mark.parametrize(
    "status", ["INSUFFICIENT_HISTORY", "DEPENDENCY_MISSING", "MODEL_ERROR", "NO_FIXTURES"]
)
def test_empty_status_cards_exist_without_fabricating_predictions(status):
    result = build(status=status, message="Not ready", diagnostics=[{"reason": "test"}])
    assert result["status"] == status and not result["legs"]
    assert result["model_diagnostics"] == [{"reason": "test"}]
    assert result["combined_price"] is None and result["probability"] is None
    assert not result["selected_for_portfolio"] and not result["playable"]


def test_both_enabled_models_get_cards_even_without_results_or_fixtures():
    cards = build_model_recommendations(
        {}, [], [], {"enabled_profiles": ["daily223", "gauss", "kepler"]}, NOW
    )
    assert [c["profile_id"] for c in cards] == ["gauss", "kepler"]
    assert all(c["status"] == "NO_FIXTURES" for c in cards)
    assert (
        build_model_recommendations({}, [fixture()], [], {"enabled_profiles": ["gauss"]}, NOW)[0][
            "status"
        ]
        == "INSUFFICIENT_HISTORY"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"kickoff": NOW.isoformat()},
        {"kickoff": (NOW - timedelta(seconds=1)).isoformat()},
        {"kickoff": (NOW + timedelta(days=1)).isoformat()},
        {"provider_status": "1H"},
        {"provider_status": "PST"},
        {"kickoff": "2026-09-29T18:00:00"},
    ],
)
def test_started_wrong_day_unconfirmed_fixtures_never_selected(changes):
    assert build(fixtures=[fixture(**changes)])["status"] == "NO_FIXTURES"


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"home": None, "draw": 0.2, "away": 0.2},
        {"home": 0.8, "draw": 0.8, "away": 0.8},
        {"home": float("nan"), "draw": 0.2, "away": 0.2},
        {"home": True, "draw": 0, "away": 0},
    ],
)
def test_missing_invalid_or_inconsistent_probabilities_are_not_filled(values):
    assert build(predictions=[prediction(probabilities=values)])["legs"] == []


def test_duplicate_predictions_and_fixtures_are_ambiguous_not_counted_twice():
    assert build(predictions=[prediction(), prediction(home=0.9)])["legs"] == []
    assert build(fixtures=[fixture(), fixture()])["status"] == "NO_FIXTURES"


def test_wrong_profile_output_cannot_masquerade_as_other_model():
    assert build(profile_id="gauss")["status"] == "MODEL_ERROR"


def test_api_football_bookmaker_source_must_match_normalized_book():
    q = quote(
        book="bet365_api_football_42",
        quote_provider="api_football",
        quote_source_id="https://v3.football.api-sports.io/odds?fixture=1&bookmaker=12&bet=1",
    )
    assert build(quotes=[q])["status"] == "ODDS_PENDING"
