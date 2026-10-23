"""Comparable local quote history, with explicit temporal and identity gaps."""

from copy import deepcopy
from datetime import timedelta

import pytest
from test_tipster_personas import NOW, quote

from goal_analysis.engine.odds_movement import annotate_odds_movement, quote_snapshot


def priced(price=3.6, **changes):
    changes.setdefault("settlement", "REGULATION_90_PLUS_STOPPAGE")
    return quote(1, "away", price, **changes)


def history(price=3, age=36, **changes):
    at = NOW - timedelta(hours=age)
    baseline = priced(price, quoted_at=at.isoformat(), **changes)
    return {
        "run_id": "baseline", "observed_at": at.isoformat(), "finished_at": at.isoformat(),
        "odds_history_snapshot": quote_snapshot([baseline], at),
    }


def movement(current=None, runs=None):
    result = annotate_odds_movement(
        {"candidates": [priced() if current is None else current]},
        [history()] if runs is None else runs, NOW,
    )
    return result["candidates"][0]["odds_movement"]


def test_verified_rise_has_exact_identity_cutoff_and_immutable_source_provenance():
    current, previous = priced(), history()
    original = deepcopy([current, previous])
    result = movement(current, [previous])
    assert result["status"] == "VERIFIED_RISE"
    assert result["relative_change"] == pytest.approx(.2)
    assert result["baseline_price"] == 3 and result["current_price"] == 3.6
    assert result["window_hours"] == result["baseline_age_hours"] == 36
    assert result["source_run_id"] == "baseline" and len(result["source_snapshot_sha256"]) == 64
    assert result["evidence_id"].startswith("odds-movement:")
    assert result["identity"]["selection_key"] == "away"
    assert [current, previous] == original
    assert "odds_movement" not in current


@pytest.mark.parametrize("age,verified", [(24, True), (48, True), (23.99, False), (48.01, False)])
def test_baseline_age_window_has_explicit_boundaries(age, verified):
    assert (movement(runs=[history(age=age)])["status"] == "VERIFIED_RISE") is verified


@pytest.mark.parametrize("field,value", [
    ("fixture_id", "api_football:999"), ("market_key", "btts"),
    ("selection_key", "home"), ("period", "FIRST_HALF"),
    ("settlement", "INCLUDING_EXTRA_TIME"), ("bookmaker", "other_book"),
    ("region", "us"), ("currency", "USD"),
])
def test_different_market_book_settlement_or_currency_is_not_comparable(field, value):
    assert movement(runs=[history(**{field: value})])["status"] == "INSUFFICIENT_HISTORY"


def test_use_latest_eligible_baseline_instead_of_cheapest_observation():
    older, latest = history(2.5, 47), history(3.5, 25)
    older["run_id"], latest["run_id"] = "older", "latest"
    result = movement(runs=[older, latest])
    assert result["status"] == "NO_SIGNIFICANT_RISE"
    assert result["source_run_id"] == "latest" and result["baseline_price"] == 3.5


def test_later_saved_report_cannot_supply_earlier_quoted_price_as_of_now():
    previous = history()
    previous["observed_at"] = previous["finished_at"] = (NOW + timedelta(seconds=1)).isoformat()
    assert movement(runs=[previous])["status"] == "INSUFFICIENT_HISTORY"


@pytest.mark.parametrize("changes", [
    {"quoted_at": "not-a-time"}, {"quoted_at": "2026-10-11T10:00:00"},
    {"quoted_at": (NOW + timedelta(seconds=1)).isoformat()},
    {"quote_source_id": "https://unrelated.example/odds"},
    {"quote_available": False}, {"decimal_price": float("nan")},
])
def test_invalid_or_future_current_quote_never_proves_movement(changes):
    current = priced()
    current.update(changes)
    assert movement(current)["status"] == "INVALID_QUOTE"


def test_complete_legacy_selected_leg_is_usable_without_inventing_missing_history():
    previous = history()
    saved_quote = previous.pop("odds_history_snapshot")["quotes"][0]
    previous["recommendations"] = [{"legs": [saved_quote]}]
    assert movement(runs=[previous])["status"] == "VERIFIED_RISE"
    del saved_quote["settlement"]
    assert movement(runs=[previous])["status"] == "INSUFFICIENT_HISTORY"


def test_snapshot_keeps_real_quote_identity_and_drops_invalid_observations():
    snapshot = quote_snapshot([priced(), priced(quote_available=False)], NOW)
    assert len(snapshot["quotes"]) == 1
    assert snapshot["quotes"][0]["quote_source_id"].startswith("https://api.the-odds-api.com/")
    assert "evidence" not in snapshot["quotes"][0]
