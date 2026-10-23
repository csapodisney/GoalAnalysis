"""Krónikás remains an Over 2.5 accumulator through every publication path."""

from copy import deepcopy
from datetime import timedelta
from math import prod

import pytest
from test_daily_223 import NOW, candidate
from test_portfolio import source

from goal_analysis.engine.diversification import _eligible
from goal_analysis.engine.portfolio import build_portfolio
from goal_analysis.engine.recommendations import build_recommendations
from goal_analysis.engine.specialist_contracts import (
    contract_metadata,
    valid_specialist_construction,
)


def construct(rows, review=None, **settings):
    options = {}
    report = build_portfolio(
        source(rows), {"enabled_profiles": ["kronikas"], **settings},
        astra_review=review, allowed_profiles=["kronikas"], diversity_options=options,
    )
    return report, options.get("kronikas", [])


def reviewed(rows):
    return {"status": "COMPLETE", "research_status": "COMPLETE_WITH_SOURCES",
            "reviews": [{"candidate_id": row["candidate_id"], "assessment": "support"}
                        for row in rows]}


def raw_ticket(rows):
    return {"profile_id": "kronikas", "date": NOW.date().isoformat(), "status": "READY",
            "combined_price": prod(row["decimal_price"] for row in rows), "legs": deepcopy(rows),
            **contract_metadata("kronikas")}


def test_primary_and_every_alternate_follow_the_same_fixed_over25_contract():
    rows = [candidate(str(i), 1.8) for i in range(8)]
    rows.append(candidate("tempting-single", 1.11, strength=.99))
    original = deepcopy(rows)
    report, options = construct(rows)
    assert report["tickets"] and options
    for ticket in [*report["tickets"], *(option["ticket"] for option in options)]:
        assert 3 <= len(ticket["legs"]) <= 6
        assert 10 <= ticket["combined_price"] <= 40
        assert ticket["selection_mode"] == "FIXED_SPECIALIST_ACCUMULATOR"
        assert ticket["minimum_combined_price"] == 10 and ticket["minimum_legs"] == 3
        assert valid_specialist_construction(ticket)
        assert {(leg["market_key"], leg["selection_key"], leg["period"])
                for leg in ticket["legs"]} == {("totals_2_5", "over", "FULL_TIME")}
        assert len({leg["fixture_id"] for leg in ticket["legs"]}) == len(ticket["legs"])
    assert rows == original


def test_minimum_is_applied_before_quality_quotas_without_pruning_prefixes():
    # Low-price evidence leaders can fill all unrestricted beam quotas. Valid
    # prefixes must survive until a complete 10+ combination is constructed.
    rows = [candidate(f"low-{i}", 1.1, strength=.95) for i in range(10)]
    rows += [candidate(f"high-{i}", 2.3, strength=.7) for i in range(4)]
    report, options = construct(rows)
    assert report["tickets"] and options
    assert all(10 <= option["ticket"]["combined_price"] <= 40 for option in options)
    assert any(leg["candidate_id"].startswith("high-")
               for leg in report["tickets"][0]["legs"])


@pytest.mark.parametrize("rows", [
    [candidate("single", 1.11)],
    [candidate(str(i), 1.1) for i in range(6)],
    [candidate("strong", 1.11), candidate("weak-a", 4, strength=.01),
     candidate("weak-b", 4, strength=.01)],
])
def test_insufficient_combinations_abstain_instead_of_reintroducing_singles(rows):
    report, options = construct(rows)
    assert not report["tickets"] and not options
    cards = build_recommendations(source(rows), [], {"enabled_profiles": ["kronikas"]},
                                  NOW, report, reviewed(rows))
    assert len(cards) == 1
    assert cards[0]["status"] == "ABSTAINED" and not cards[0]["legs"]
    assert cards[0]["reason_code"] == "SPECIALIST_CONSTRUCTION_NOT_MET"
    assert not cards[0]["construction_verified"] and not cards[0]["playable"]


def test_review_of_one_leg_does_not_shorten_or_upgrade_the_accumulator():
    rows = [candidate(str(i), 2.2) for i in range(4)]
    report, options = construct(rows, reviewed(rows[:1]))
    assert all(option["ticket"]["status"] == "DRAFT" for option in options)
    cards = build_recommendations(source(rows), [], {"enabled_profiles": ["kronikas"]},
                                  NOW, report, reviewed(rows[:1]))
    assert len(cards[0]["legs"]) >= 3 and cards[0]["combined_price"] >= 10
    assert cards[0]["status"] == "DRAFT" and cards[0]["confidence"] == "LOW"
    complete, _ = construct(rows, reviewed(rows))
    assert complete["tickets"][0]["status"] == "READY"
    assert [leg["candidate_id"] for leg in complete["tickets"][0]["legs"]] == [
        leg["candidate_id"] for leg in report["tickets"][0]["legs"]]


@pytest.mark.parametrize("rows", [
    [candidate("single", 1.11)],
    [candidate(str(i), 1.4) for i in range(3)],
    [candidate(str(i), 4) for i in range(3)],
    [candidate(str(i), 2.2, market_key="btts", selection_key="yes") for i in range(3)],
])
def test_allocator_rejects_forged_out_of_contract_alternatives_even_with_ready_label(rows):
    ticket = raw_ticket(rows)
    for field in ("construction_contract", "selection_mode"):
        ticket.pop(field, None)  # Recognize the profile even without optional metadata.
    assert not valid_specialist_construction(ticket)
    assert not _eligible({"quality": 1, "ticket": ticket}, NOW, NOW.date().isoformat())


@pytest.mark.parametrize("change", [
    {"event_status": "CANCELLED"},
    {"quote_available": False},
    {"evidence": []},
    {"kickoff": NOW.isoformat()},
])
def test_accumulator_requirement_does_not_relax_original_candidate_gates(change):
    rows = [candidate("a", 2.2), candidate("b", 2.2), candidate("invalid", 2.2, **change)]
    report, options = construct(rows)
    assert not report["tickets"] and not options


def test_quote_provenance_is_preserved_and_lockout_still_rejects_whole_ticket():
    rows = [candidate(str(i), 2.2, quote_source_id=f"source-{i}") for i in range(3)]
    report, options = construct(rows, reviewed(rows))
    ticket = report["tickets"][0]
    originals = {row["candidate_id"]: row for row in rows}
    for leg in ticket["legs"]:
        for field in ("quote_source_id", "quoted_at", "decimal_price", "bookmaker", "region", "currency"):
            assert leg[field] == originals[leg["candidate_id"]][field]
    locked = deepcopy(options[0])
    locked["ticket"]["legs"][0]["kickoff"] = (NOW + timedelta(minutes=15)).isoformat()
    assert not _eligible(locked, NOW, NOW.date().isoformat())


@pytest.mark.parametrize("field,value", [("bookmaker", "other"), ("region", "HU"), ("currency", "HUF")])
def test_contract_and_constructor_reject_mixed_quote_contexts(field, value):
    rows = [candidate(str(i), 2.2) for i in range(3)]
    rows[-1][field] = value
    report, options = construct(rows)
    assert not report["tickets"] and not options
    assert not valid_specialist_construction(raw_ticket(rows))


def test_contract_validator_does_not_apply_kronikas_constraints_to_other_profiles():
    # Contract scope, not a declaration that arbitrary input is playable.
    for profile in ("daily223", "ritmusor", "parharcmester", "orszem", "merlin",
                    "h1_over05", "gauss", "kepler", "laplace", "tipster_golvadasz_lili"):
        ticket = {"profile_id": profile, "legs": [candidate("a", 1.11)], "combined_price": 1.11}
        original = deepcopy(ticket)
        assert valid_specialist_construction(ticket)
        assert ticket == original


def test_daily_ticket_object_is_unchanged_when_kronikas_contract_produces_options(monkeypatch):
    from goal_analysis.engine import portfolio

    rows = [candidate(str(i), price) for i, price in enumerate((2, 2, 3, 2))]
    settings = {"enabled_profiles": ["daily223", "kronikas"]}
    mixed = build_portfolio(source(rows), settings)
    # Keep identical settings (included in ticket identity), and exercise the
    # same DAILY solver when specialist construction yields no alternatives.
    monkeypatch.setattr(portfolio, "_specialist_options", lambda *_args, **_kwargs: [])
    baseline = build_portfolio(source(rows), settings)
    assert next(t for t in baseline["tickets"] if t["profile_id"] == "daily223") == next(
        t for t in mixed["tickets"] if t["profile_id"] == "daily223")
