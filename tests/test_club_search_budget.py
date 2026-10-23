"""Wider club alternatives retain feasible fixtures without relaxing contracts."""

import math
from copy import deepcopy

from goal_analysis.tipsters import recommendations as module
from goal_analysis.tipsters.catalog import CATALOG_VERSION, TIPSTER_BY_ID
from goal_analysis.tipsters.contracts import valid_construction


def ranked_rows(count=12):
    return [{
        "score": 0.8 - index / 1000,
        "key": "home",
        "fixture": {"competition": f"league_{index % 3}"},
        "quote": {
            "fixture_id": f"fixture_{index:02}", "candidate_id": f"quote_{index:02}",
            "market_key": "h2h", "selection_key": "home", "period": "FULL_TIME",
            "decimal_price": 2.0, "bookmaker": "book", "region": "eu", "currency": "EUR",
        },
    } for index in range(count)]


def test_wider_club_search_restores_valid_fixture_alternatives_without_lowering_best(monkeypatch):
    rows = ranked_rows()
    original = deepcopy(rows)
    persona = TIPSTER_BY_ID["tipster_archiv_nora"]
    with monkeypatch.context() as old:
        old.setattr(module, "CLUB_OPTION_BEAM_WIDTH", 32)
        old.setattr(module, "CLUB_MAX_TICKET_OPTIONS", 128)
        previous, previous_diagnostics = module._options(rows, persona)
    current, diagnostics = module._options(rows, persona)

    def fixtures(options):
        return {row["quote"]["fixture_id"] for group in options for row in group}

    # The old beam had many combinations of one core, hiding another eligible
    # fixture entirely. Its alternative is now visible to the existing allocator.
    assert "fixture_11" not in fixtures(previous)
    assert "fixture_11" in fixtures(current)
    assert current[0] == previous[0]
    assert rows == original
    assert diagnostics["eligible_fixture_count"] == 12
    assert diagnostics["retained_option_fixture_count"] == 12
    assert diagnostics["eligible_fixtures_without_option_count"] == 0
    assert previous_diagnostics["eligible_fixtures_without_option_count"] > 0
    assert len(current) <= module.CLUB_MAX_TICKET_OPTIONS
    for group in current:
        ticket = {
            "profile_id": persona["profile_id"], "catalog_version": CATALOG_VERSION,
            "legs": [row["quote"] for row in group],
            "combined_price": math.prod(row["quote"]["decimal_price"] for row in group),
        }
        assert valid_construction(ticket)


def test_coverage_diagnostics_do_not_turn_incomplete_shapes_into_tickets():
    options, diagnostics = module._options(ranked_rows(1), TIPSTER_BY_ID["tipster_archiv_nora"])
    assert options == []
    assert diagnostics["eligible_fixture_count"] == 1
    assert diagnostics["retained_option_fixture_count"] == 0
    assert diagnostics["eligible_fixtures_without_option_count"] == 1
    options, diagnostics = module._options([], TIPSTER_BY_ID["tipster_archiv_nora"])
    assert options == []
    assert diagnostics["eligible_fixture_count"] == 0
    assert diagnostics["eligible_fixtures_without_option_count"] == 0
