"""Bounded review must cover a fixed accumulator, not just its first leg."""

from copy import deepcopy

import pytest
from test_daily_223 import candidate
from test_portfolio import source

from goal_analysis.jobs.portfolio import _contrarian_review_packets, _review_packets


def inputs():
    daily = [candidate(f"daily-{i}", price) for i, price in enumerate((2, 2, 3))]
    cohort = [candidate(f"quote-{i}", row["decimal_price"], fixture_id=row["fixture_id"],
                        bookmaker="alternate-book") for i, row in enumerate(daily)]
    kronikas = [candidate(f"kronikas-{i}", 1.8, strength=.5) for i in range(6)]
    extras = [candidate(f"extra-{i:02}", 1.5, strength=.99) for i in range(12)]
    payload = source([*daily, *cohort, *kronikas, *extras])
    ticket = {"profile_id": "daily223", "legs": deepcopy(daily)}
    proposal = {"profile_id": "kronikas", "legs": deepcopy(kronikas)}
    preliminary = {"policy": {"strictness": 35}, "tickets": [ticket],
                   "profile_proposals": [deepcopy(ticket), proposal]}
    return payload, preliminary, daily, cohort, kronikas, extras


def ids(rows):
    return [row["candidate_id"] for row in rows]


def test_all_six_accumulator_legs_precede_stronger_generic_extras_after_daily_quotes():
    payload, preliminary, daily, cohort, kronikas, extras = inputs()
    original = deepcopy((payload, preliminary))
    packets = _review_packets(payload, preliminary, maximum=18)
    assert ids(packets[:3]) == ids(daily)
    assert set(ids(packets[3:6])) == set(ids(cohort))
    assert ids(packets[6:12]) == ids(kronikas)
    assert ids(packets[12:]) == ids(extras[:6])
    assert len(packets) == 18
    assert (payload, preliminary) == original


@pytest.mark.parametrize("maximum", [3, 5, 6, 12, 15, 30])
def test_daily_priority_and_maximum_budget_survive_fixed_accumulator_reservation(maximum):
    payload, preliminary, daily, cohort, kronikas, _extras = inputs()
    packets = _review_packets(payload, preliminary, maximum)
    assert len(packets) <= maximum
    assert ids(packets[:3]) == ids(daily)
    if maximum >= 6:
        assert set(ids(packets[3:6])) == set(ids(cohort))
    if maximum >= 12:
        assert ids(packets[6:12]) == ids(kronikas)
    assert len(ids(packets)) == len(set(ids(packets)))


def test_insufficient_capacity_does_not_reserve_a_partial_accumulator_ahead_of_extras():
    payload, preliminary, daily, cohort, _kronikas, extras = inputs()
    # Six DAILY/quote slots leave only five positions for a six-leg proposal.
    # It must not take a partial priority block; normal evidence ranking resumes.
    packets = _review_packets(payload, preliminary, maximum=11)
    assert ids(packets[:3]) == ids(daily)
    assert set(ids(packets[3:6])) == set(ids(cohort))
    assert ids(packets[6:]) == ids(extras[:5])


def test_shared_already_reviewed_candidate_uses_one_slot_in_atomic_reservation():
    payload, preliminary, daily, _cohort, kronikas, _extras = inputs()
    preliminary["profile_proposals"][1]["legs"][0] = deepcopy(daily[0])
    packets = _review_packets(payload, preliminary, maximum=11)
    assert len(packets) == 11
    assert ids(packets).count(daily[0]["candidate_id"]) == 1
    assert ids(packets[6:]) == ids(kronikas[1:])


@pytest.mark.parametrize("maximum", [9, 12, 15])
def test_contrarian_and_veto_slots_cannot_displace_protected_daily_and_accumulator(maximum):
    payload, preliminary, daily, _cohort, kronikas, _extras = inputs()
    packets = _review_packets(payload, preliminary, maximum=18)
    protected = set(ids(daily)) | set(ids(kronikas))
    contrarian = [candidate(f"contra-{i}", 2.5) for i in range(8)]
    veto = [candidate(f"veto-{i}", 2.5) for i in range(8)]
    contexts = {row["candidate_id"]: {"evidence_id": f"movement-{i}", "expected_return": 1.5}
                for i, row in enumerate(contrarian)}
    veto_contexts = {row["candidate_id"]: {"gates": ["HIGH_ODDS"]} for row in veto}
    enriched = {"candidates": [*payload["candidates"], *contrarian, *veto]}
    original = deepcopy((packets, enriched, contexts, veto_contexts))
    result = _contrarian_review_packets(packets, enriched, contexts, protected, maximum,
                                        veto_contexts=veto_contexts)
    assert ids(result[:9]) == [*ids(daily), *ids(kronikas)]
    assert len(result) == maximum and len(ids(result)) == len(set(ids(result)))
    assert (packets, enriched, contexts, veto_contexts) == original


def test_coordinator_passes_fixed_specialist_ids_into_contrarian_protection(tmp_path, monkeypatch):
    from test_portfolio_flow import NOW, RecordedReviewer, environment

    from goal_analysis.jobs import portfolio
    from goal_analysis.tipsters import recommendations as tipster_recommendations

    config, football, odds, cache, _calls, _state, _db = environment(tmp_path)
    captured = {}
    original_packets = portfolio._review_packets
    original_contrarian = portfolio._contrarian_review_packets

    def remember_proposals(enriched, preliminary, maximum):
        for profile in ("daily223", "kronikas"):
            captured[profile] = {leg["candidate_id"] for ticket in preliminary["profile_proposals"]
                                 if ticket["profile_id"] == profile for leg in ticket["legs"]}
        return original_packets(enriched, preliminary, maximum)

    def one_contrarian_context(enriched, *_args):
        identifier = enriched["candidates"][-1]["candidate_id"]
        return {identifier: {"evidence_id": "synthetic-movement", "expected_return": 1.5}}

    def remember_protection(packets, enriched, contexts, protected, maximum, **kwargs):
        captured["protected"] = set(protected)
        result = original_contrarian(packets, enriched, contexts, protected, maximum, **kwargs)
        captured["final_packet_ids"] = set(ids(result))
        assert len(result) <= maximum
        return result

    monkeypatch.setattr(portfolio, "_review_packets", remember_proposals)
    monkeypatch.setattr(portfolio, "_contrarian_review_packets", remember_protection)
    monkeypatch.setattr(tipster_recommendations, "contrarian_candidate_context", one_contrarian_context)
    portfolio.run_portfolio(config, {"operating_contract_version": 1, "enabled_profiles": ["daily223", "kronikas"]},
                            football, odds, cache, NOW.date(), RecordedReviewer(), clock=lambda: NOW)
    assert len(captured["daily223"]) == len(captured["kronikas"]) == 3
    assert captured["kronikas"] - captured["daily223"]  # Not a vacuous DAILY-only assertion.
    assert captured["protected"] == captured["daily223"] | captured["kronikas"]
    assert captured["protected"] <= captured["final_packet_ids"]
