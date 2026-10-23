"""Non-DAILY utility permits supported combinations without padding odds."""

from copy import deepcopy
from math import sqrt

import pytest
from test_daily_223 import candidate as _candidate
from test_portfolio import source

from goal_analysis.engine import portfolio


def candidate(*args, **changes):
    """Use an unchanged specialist for generic evidence/utility regressions."""
    return _candidate(*args, **{"market_key": "btts", "selection_key": "yes", **changes})


def build(rows, **settings):
    options = {}
    report = portfolio.build_portfolio(
        source(rows), {"enabled_profiles": ["ritmusor"], **settings},
        diversity_options=options,
    )
    return report, options.get("ritmusor", [])


def test_supported_low_price_combination_outranks_single_without_minimum_odds():
    rows = [candidate(str(index), 1.35) for index in range(3)]
    original = deepcopy(rows)
    report, options = build(rows)
    ticket = report["tickets"][0]
    assert len(ticket["legs"]) == 3
    assert ticket["combined_price"] == pytest.approx(1.35 ** 3)
    assert ticket["combined_price"] < 10
    scoring = ticket["selection_diagnostics"]["ticket_scoring"]
    assert scoring["raw_leg_scores"] == [80.0] * 3
    assert scoring["normalized_leg_scores"] == [0.8] * 3
    assert scoring["normalized_quality"] == pytest.approx(0.8)
    assert scoring["ticket_score"] == pytest.approx(0.8 * sqrt(3))
    assert options[0]["quality"] == scoring["ticket_score"]
    assert options[0]["scoring"] == scoring
    assert rows == original


def test_eligible_but_weak_padding_does_not_automatically_win_length_bonus():
    rows = [candidate("strong", 1.35), candidate("weak", 1.4, strength=0.08)]
    report, options = build(rows)
    assert [leg["candidate_id"] for leg in report["tickets"][0]["legs"]] == ["strong"]
    assert any(len(option["ticket"]["legs"]) == 2 for option in options)
    assert any(option["scoring"]["normalized_quality"] == pytest.approx(0.08)
               for option in options)


def test_multi_options_keep_all_single_alternatives_and_complete_quote_provenance():
    rows = [candidate(str(index), 1.4) for index in range(4)]
    for row in rows:
        row["quote_source_id"] = "https://source.test/odds?fixture=" + row["fixture_id"]
    report, options = build(rows)
    assert len({option["ticket"]["legs"][0]["fixture_id"]
                for option in options if len(option["ticket"]["legs"]) == 1}) == 4
    assert {len(option["ticket"]["legs"]) for option in options} == {1, 2, 3, 4}
    for option in options:
        legs = option["ticket"]["legs"]
        assert len({leg["fixture_id"] for leg in legs}) == len(legs)
        assert all(leg["quote_source_id"] ==
                   "https://source.test/odds?fixture=" + leg["fixture_id"] for leg in legs)
    reversed_report, reversed_options = build(list(reversed(rows)))
    assert (report, options) == (reversed_report, reversed_options)


def test_options_never_mix_bookmaker_region_or_currency_cohorts():
    rows = []
    for index, changes in enumerate(({}, {"bookmaker": "other"},
                                     {"region": "HU"}, {"currency": "HUF"})):
        rows.extend(candidate(f"{index}-{n}", 1.5, **changes) for n in range(2))
    _, options = build(rows)
    assert any(len(option["ticket"]["legs"]) > 1 for option in options)
    for option in options:
        assert len({(leg["bookmaker"], leg["region"], leg["currency"])
                    for leg in option["ticket"]["legs"]}) == 1


def test_original_quality_history_and_upper_price_guards_still_apply():
    missing_history = candidate("missing", 1.2)
    missing_history["evidence"] = missing_history["evidence"][2:]
    rows = [candidate("good", 1.35), candidate("below-floor", 1.4, strength=0.01),
            missing_history, candidate("above-upper", 51)]
    _, options = build(rows)
    assert {leg["candidate_id"] for option in options for leg in option["ticket"]["legs"]} == {"good"}
    _, capped = build([candidate(str(index), 4) for index in range(4)])
    assert all(option["ticket"]["combined_price"] <= 50 for option in capped)
    assert max(len(option["ticket"]["legs"]) for option in capped) == 2


def test_six_leg_bound_does_not_drop_single_fixture_risk_alternatives():
    _, options = build([candidate(str(index), 1.1) for index in range(9)])
    assert len([option for option in options if len(option["ticket"]["legs"]) == 1]) == 9
    assert max(len(option["ticket"]["legs"]) for option in options) == 6
    assert {len(option["ticket"]["legs"]) for option in options} == {1, 2, 3, 4, 5, 6}
    assert len([option for option in options if len(option["ticket"]["legs"]) > 1]) <= 64


def test_daily_constructor_does_not_use_new_specialist_score_or_options(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("DAILY must not use specialist ranking")

    monkeypatch.setattr(portfolio, "_specialist_scoring", forbidden)
    monkeypatch.setattr(portfolio, "_specialist_options", forbidden)
    result = portfolio.build_portfolio(
        source([_candidate(str(index), price) for index, price in enumerate((2, 2, 3))]),
        {"enabled_profiles": ["daily223"]},
    )
    ticket = result["tickets"][0]
    assert ticket["profile_id"] == "daily223" and ticket["combined_price"] == 12
    assert "selection_diagnostics" not in ticket
    assert [leg["target_price"] for leg in ticket["legs"]] == [2.0, 2.0, 3.0]


def test_emergency_combination_remains_explicitly_draft_with_original_warnings():
    report = portfolio.build_portfolio(
        source([candidate(str(index), 1.4) for index in range(3)]),
        {"strictness": 0, "enabled_profiles": ["daily223", "gauss"]},
    )
    ticket = report["tickets"][0]
    assert ticket["profile_id"] == "available" and len(ticket["legs"]) == 3
    assert ticket["status"] == "DRAFT" and ticket["fallback"]
    assert ticket["quality_warnings"] and ticket["confidence"] == "LOW"
    assert ticket["real_wager_placed"] is False
