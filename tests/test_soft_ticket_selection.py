"""Behavioral policy scenarios, including final rather than requested exposure."""

from copy import deepcopy

import pytest
from test_global_diversification import NOW, card, option, report

from goal_analysis.engine.diversification import allocate, ready_exposure, ticket_similarity
from goal_analysis.engine.ticket_scoring import normalize_score, ticket_quality, ticket_score


def multi(profile, fixtures, quality=.8, *, main=False, status="READY", selection="over"):
    result = option(profile, fixtures[0], quality, main=main)
    for group in ("recommendation", "ticket"):
        if group not in result:
            continue
        result[group]["status"] = status
        result[group]["legs"] = [card(profile, f)["legs"][0] for f in fixtures]
        for leg in result[group]["legs"]:
            leg["selection_key"] = selection
        result[group]["ticket_id" if group == "ticket" else "recommendation_id"] = profile + "-".join(fixtures)
    result["quality"] = quality
    result["scoring"] = {"normalized_quality": quality, "score_kind": "TEST_UNIT_SUPPORT"}
    return result


def test_jaccard_is_distinct_from_two_of_three_overlap():
    a = multi("a", ["a", "b", "c"])["recommendation"]
    b = multi("b", ["a", "b", "d"])["recommendation"]
    measured = ticket_similarity(a, b)
    assert measured["fixture_jaccard"] == .5
    assert measured["candidate_fixture_overlap"] == pytest.approx(2 / 3)
    assert not measured["exact_duplicate"]


def test_two_of_three_is_penalized_but_not_deleted_and_one_of_three_is_free():
    options = {"major": [multi("major", ["a", "b", "c"], .95)],
               "similar": [multi("similar", ["a", "b", "d"], .9)],
               "distinct": [multi("distinct", ["a", "e", "f"], .85)],
               **{f"filler-{index}": [multi(f"filler-{index}", [f"u{index}"], .5)]
                  for index in range(4)}}
    data, settings = report(options)
    allocate(data, options, settings, NOW)
    assigned = data["diversification"]["assigned"]
    assert set(assigned) == set(options)
    assert assigned["similar"]["adjusted_quality"] == pytest.approx(.9 * .7)
    assert assigned["distinct"]["penalty_factor"] == 1


def test_same_fixture_different_outcome_survives_and_bookmaker_alias_does_not():
    options = {"first": [multi("first", ["x"], .99)],
               "different": [multi("different", ["x"], .9, selection="under")],
               "alias": [multi("alias", ["x"], .8)],
               "filler-a": [multi("filler-a", ["a"], .7)],
               "filler-b": [multi("filler-b", ["b"], .6)]}
    options["alias"][0]["recommendation"]["bookmaker"] = "another_book"
    data, settings = report(options)
    allocate(data, options, settings, NOW)
    assert set(data["diversification"]["assigned"]) == {
        "first", "different", "filler-a", "filler-b"}


def test_four_ready_tickets_may_share_a_fixture_in_two_tickets():
    options = {"shared": [multi("shared", ["daily-a", "new"], .95, main=True)],
               "other": [multi("other", ["x"], .9, main=True)],
               "third": [multi("third", ["y"], .85, main=True)]}
    data, settings = report(options, target=4)
    before = deepcopy(data["tickets"][0]), deepcopy(data["recommendations"][0])
    allocate(data, options, settings, NOW)
    exposure = data["diversification"]["ready_exposure"]
    assert exposure["ready_ticket_count"] == 4
    assert exposure["fixture_ticket_shares"]["daily-a"] == .5
    assert exposure["compliant"]
    assert (data["tickets"][0], data["recommendations"][0]) == before


def test_final_count_not_target_or_draft_card_denominator_selects_alternative():
    options = {"shared": [multi("shared", ["daily-a"], .95, main=True),
                           multi("shared", ["alternative"], .7, main=True)],
               "other": [multi("other", ["x"], .9, main=True)],
               "draft": [multi("draft", ["z"], .85, main=True, status="DRAFT")]}
    data, settings = report(options, target=4)
    allocate(data, options, settings, NOW)
    exposure = data["diversification"]["ready_exposure"]
    assert exposure["ready_ticket_count"] == 3
    assert exposure["allowed_tickets_per_fixture"] == 1
    assert data["diversification"]["assigned"]["shared"]["fixture_ids"] == ["alternative"]
    assert all(t["status"] == "READY" for t in data["tickets"])


def test_unreviewed_stronger_combination_cannot_displace_a_ready_main_alternative():
    options = {"p": [multi("p", ["x", "y"], .95, main=True, status="DRAFT"),
                      multi("p", ["z"], .7, main=True)]}
    data, settings = report(options)
    allocate(data, options, settings, NOW)
    assert data["diversification"]["assigned"]["p"]["fixture_ids"] == ["z"]
    assert data["tickets"][1]["status"] == "READY"


def test_selected_card_discloses_expert_ranking_provenance():
    chosen = multi("p", ["x"], .8, main=True)
    chosen["scoring"]["the_stats_api"] = {
        "kind": "SEASON_XG_RANKING_TIEBREAKER_NOT_PROBABILITY", "multiplier": 1.02}
    data, settings = report({"p": [chosen]})
    allocate(data, {"p": [chosen]}, settings, NOW)
    assert data["tickets"][1]["expert_ranking"]["the_stats_api"]["multiplier"] == 1.02
    assert data["recommendations"][1]["expert_ranking"]["the_stats_api"]["kind"].endswith(
        "NOT_PROBABILITY")


@pytest.mark.parametrize("count,limit", [(1, 1), (2, 1), (3, 1), (4, 2), (5, 2)])
def test_integer_risk_limit(count, limit):
    tickets = [card("p" + str(i), "f" + str(i), ticket=True) for i in range(count)]
    assert ready_exposure(tickets)["allowed_tickets_per_fixture"] == limit
    assert ready_exposure(tickets)["single_ready_ticket_exception"] == (count == 1)


def test_unselected_paper_suggestions_do_not_dilute_main_risk():
    options = {"shared": [multi("shared", ["daily-a"], .9, main=True)],
               **{f"paper{i}": [multi(f"paper{i}", [f"p{i}"], .8)] for i in range(10)}}
    data, settings = report(options)
    for c in data["recommendations"][2:]:
        c["publication_scope"] = "PAPER_AUDITION"
    allocate(data, options, settings, NOW)
    assert len(data["tickets"]) == 1
    assert data["diversification"]["ready_exposure"]["ready_ticket_count"] == 1
    assert len(data["diversification"]["assigned"]) == 11


def test_one_started_leg_or_alias_mismatch_rejects_whole_candidate():
    options = {"started": [multi("started", ["x", "y"], main=True)],
               "mismatch": [multi("mismatch", ["u", "v"], main=True)]}
    options["started"][0]["recommendation"]["legs"][1]["kickoff"] = NOW.isoformat()
    options["mismatch"][0]["recommendation"]["legs"].pop()
    data, settings = report(options)
    allocate(data, options, settings, NOW)
    assert not data["diversification"]["assigned"]


def test_protected_daily_proposal_also_anchors_exact_dedup_without_capital_use():
    options = {"clone": [multi("clone", ["daily-a", "daily-b", "daily-c"], main=True)]}
    data, settings = report(options)
    data["tickets"] = []
    allocate(data, options, settings, NOW)
    assert not data["tickets"]
    assert not data["diversification"]["assigned"]


def test_fixed_scale_normalization_and_length_utility_leave_raw_values_unchanged():
    raw = [80, 75, 70]
    details = ticket_quality(raw, scale=100)
    assert details["normalized_leg_scores"] == [.8, .75, .7]
    assert details["ticket_score"] == pytest.approx(.75 * 3 ** .5)
    assert details["ticket_score"] > ticket_score([.8])
    assert raw == [80, 75, 70] and not details["calibrated_probability"]
    assert normalize_score(2, scale=100) == .02
    with pytest.raises(ValueError):
        ticket_score([1.1])
