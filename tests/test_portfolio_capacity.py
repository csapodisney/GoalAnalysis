"""Capacity metadata controls; synthetic records never represent real coupons."""

from copy import deepcopy

import pytest

from goal_analysis.portfolio_capacity import classify


def card(profile="a", scope="LIVE", status="READY"):
    return {"profile_id": profile, "status": status, "publication_scope": scope,
            "legs": [{"candidate_id": profile + "-quote", "fixture_id": profile,
                      "decimal_price": 2.0, "bookmaker": "book", "currency": "EUR"}]}


def alias_report():
    rec = card(status="REVIEWED")
    rec.update(portfolio_ticket_id="ticket-a", selected_for_portfolio=True, playable=False)
    ticket = card()
    ticket["ticket_id"] = "ticket-a"
    return {"recommendations": [rec], "tickets": [ticket]}


def test_reviewed_alias_counts_once_without_promoting_or_mutating_it():
    report = alias_report()
    before = deepcopy(report)
    result = classify(report)
    assert len(result["live_cards"]) == result["published_ready_live_tickets"] == 1
    assert result["paper_cards"] == [] and report == before
    assert result["readiness_or_publication_rights_changed"] is False


@pytest.mark.parametrize("mutation", [
    lambda r: r["tickets"].clear(),
    lambda r: r["tickets"][0].update(status="DRAFT"),
    lambda r: r["tickets"][0].update(publication_scope="PAPER_AUDITION"),
    lambda r: r["tickets"][0].update(profile_id="other"),
    lambda r: r["tickets"][0]["legs"][0].update(candidate_id="other-quote"),
    lambda r: r["tickets"][0]["legs"][0].update(decimal_price=2.1),
    lambda r: r["tickets"][0]["legs"][0].update(bookmaker="other"),
    lambda r: r["tickets"][0]["legs"][0].update(quote_source_sha256="other-source"),
    lambda r: r["tickets"].append(deepcopy(r["tickets"][0])),
    lambda r: r["recommendations"][0].update(selected_for_portfolio=False),
    lambda r: r["recommendations"][0].update(publication_scope="PAPER_AUDITION"),
])
def test_reviewed_alias_never_borrows_an_unproved_or_different_ticket(mutation):
    report = alias_report()
    mutation(report)
    result = classify(report)
    assert result["live_cards"] == result["paper_cards"] == []
    assert result["excluded_nonempty_recommendations"][0]["reason"] == "REVIEWED_WITHOUT_EXACT_READY_LIVE_TICKET"


def test_paper_cannot_fill_live_capacity_and_model_ready_label_stays_separate():
    report = {"recommendations": [card("live"), card("draft", status="DRAFT"),
                                  card("paper", "PAPER_AUDITION", "MODEL_READY")]}
    result = classify(report)
    assert [c["profile_id"] for c in result["live_cards"]] == ["live"]
    assert [c["profile_id"] for c in result["paper_cards"]] == ["paper"]
    assert result["published_ready_live_tickets"] == 0
    assert result["paper_contributes_to_live_target"] is False


@pytest.mark.parametrize("scope", [None, "SUSPENDED", "UNKNOWN"])
def test_unknown_or_suspended_scope_is_not_live(scope):
    result = classify({"recommendations": [card(scope=scope)]})
    assert result["live_cards"] == result["paper_cards"] == []


def test_empty_cards_and_duplicate_profiles_never_inflate_counts():
    first = card()
    result = classify({"recommendations": [first, deepcopy(first), {**card("empty"), "legs": []}]})
    assert result["live_cards"] == [] and result["nonempty_recommendations"] == 2
    assert all(r["reason"] == "MISSING_OR_DUPLICATE_PROFILE"
               for r in result["excluded_nonempty_recommendations"])


@pytest.mark.parametrize("price", [float("nan"), True, None, 1.0])
def test_invalid_quote_prices_do_not_establish_alias_identity(price):
    report = alias_report()
    report["recommendations"][0]["legs"][0]["decimal_price"] = price
    report["tickets"][0]["legs"][0]["decimal_price"] = price
    assert classify(report)["live_cards"] == []


def test_objectives_keep_twenty_paper_cards_out_of_the_live_target():
    from goal_analysis.portfolio_objectives import assess_objectives

    report = {"recommendations": [card(str(i), "PAPER_AUDITION") for i in range(20)]}
    result = assess_objectives(report)
    assert result["ready_recommendations"] == result["ready_live_recommendations"] == 0
    assert result["ready_paper_recommendations"] == result["ready_recommendations_all_scopes"] == 20
    assert result["capacity_shortfall"] == 20 and result["capacity_status"] == "BELOW_TARGET"
    assert result["non_ready_recommendations_with_legs"] == 0


def test_objectives_count_the_exact_reviewed_daily_alias_without_mutating_it():
    from goal_analysis.portfolio_objectives import assess_objectives

    report = alias_report()
    original = deepcopy(report)
    result = assess_objectives(report)
    assert result["ready_live_recommendations"] == result["published_ready_live_tickets"] == 1
    assert result["capacity_shortfall"] == 19
    assert result["settlement_kpi"]["status"] == "PENDING_SETTLEMENT"
    assert result["filler_generation_allowed"] is False and report == original
