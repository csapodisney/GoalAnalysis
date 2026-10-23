"""Construction bounds hold for every returned alternative, not just the winner."""

import math

from goal_analysis.engine.ticket_options import bounded_ticket_groups


def row(fixture, price, quality=0.8, *, book="book", outcome="home"):
    return {"fixture": fixture, "price": price, "quality": quality,
            "book": book, "outcome": outcome}


def rank(group):
    return (-sum(item["quality"] for item in group) / len(group),
            tuple((item["fixture"], item["outcome"], item["book"]) for item in group))


def search(rows, **options):
    return bounded_ticket_groups(
        rows, rank=rank, fixture=lambda item: item["fixture"],
        cohort=lambda item: item["book"], price=lambda item: item["price"],
        identity=lambda item: (item["fixture"], item["outcome"]),
        **{"max_legs": 4, "max_price": 20, **options},
    )


def test_below_minimum_prefixes_grow_into_valid_accumulator():
    options, _ = search([row(str(n), 1.5) for n in range(4)],
                        min_legs=4, min_price=5, max_price=6)
    assert len(options) == 1
    assert len(options[0]) == 4
    assert math.prod(item["price"] for item in options[0]) == 5.0625


def test_every_alternative_meets_leg_and_total_price_bounds():
    rows = [row(str(n), 1.5 + n / 20) for n in range(9)]
    options, _ = search(rows, min_legs=3, min_price=5, max_price=10,
                        beam_width=16, max_options=20)
    assert options
    assert {len(group) for group in options} == {3, 4}
    assert all(5 <= math.prod(item["price"] for item in group) <= 10
               for group in options)
    assert search(list(reversed(rows)), min_legs=3, min_price=5, max_price=10,
                  beam_width=16, max_options=20)[0] == options


def test_invalid_lower_price_source_cannot_hide_compliant_quote_before_dedup():
    rows = [row("a", 1.5, 0.9, book="high-support-low-price"),
            row("a", 2.5, 0.8, book="eligible-price")]
    options, _ = search(rows, max_legs=1, min_price=2)
    assert len(options) == 1
    assert options[0][0]["book"] == "eligible-price"


def test_options_budget_is_not_spent_on_disallowed_singles():
    options, _ = search([row(str(n), 2) for n in range(8)],
                        min_legs=3, max_legs=3, min_price=7, max_options=4)
    assert len(options) == 4
    assert all(len(group) == 3 for group in options)


def test_incomplete_odds_cannot_prove_a_minimum_accumulator_price():
    rows = [row("a", None), row("b", None)]
    assert search(rows, min_legs=2, min_price=4)[0] == []
    # The optional bounds preserve the numeric model's odds-pending path.
    assert search(rows)[0]


def test_construction_abstains_instead_of_returning_below_bounds():
    assert search([row("a", 2), row("b", 2)], min_legs=3)[0] == []
    assert search([row("a", 2), row("b", 2)], min_price=5)[0] == []


def test_prefix_rank_can_preserve_a_reachable_target_without_changing_final_rank():
    rows = [row("a", 1.1, 0.95), row("b", 2.0, 0.8), row("c", 2.0, 0.8)]
    options, _ = search(
        rows, min_legs=2, max_legs=2, min_price=3, beam_width=1,
        beam_rank=lambda group: (-math.prod(item["price"] for item in group), rank(group)),
    )
    assert [[item["fixture"] for item in group] for group in options] == [["b", "c"]]


def test_price_strata_keep_a_weaker_prefix_needed_for_a_fixed_length_target():
    rows = [row("a", 1.1, 0.99), row("b", 1.1, 0.98),
            row("c", 3.0, 0.7), row("d", 3.0, 0.7)]
    options, diagnostics = search(rows, min_legs=2, max_legs=2, min_price=8,
                                  max_price=10, beam_width=2)
    assert diagnostics["bounded_search"] is True
    assert [[item["fixture"] for item in group] for group in options] == [["c", "d"]]
    assert math.prod(item["price"] for item in options[0]) == 9


def test_default_bounds_retain_singles_and_all_lengths_for_existing_callers():
    options, _ = search([row(str(n), 1.2) for n in range(6)], max_options=16)
    assert {len(group) for group in options} == {1, 2, 3, 4}


def test_candidate_override_does_not_bypass_accumulator_minimum_length():
    options, _ = search([row("a", 30)], min_legs=2, min_price=5,
                        allow_price_overflow=lambda group: len(group) == 1)
    assert options == []
