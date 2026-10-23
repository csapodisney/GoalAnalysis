from copy import deepcopy
from datetime import timedelta

import pytest
from test_daily_223 import DAY, NOW, candidate

from goal_analysis.engine.portfolio import build_portfolio
from goal_analysis.jobs.portfolio import _merge_astra_reviews, _review_packets


def source(candidates):
    return {
        "schema_version": 1,
        "date": DAY.isoformat(),
        "observed_at": NOW.isoformat(),
        "candidates": candidates,
        "preferred_bookmakers": ["betano"],
        "history_analysis": {"candidates": []},
    }


def all_markets():
    result = []
    for i in range(4):
        for market, selection, price in (
            ("totals_2_5", "over", 2.25),
            ("totals_2_5", "under", 2.25),
            ("btts", "yes", 2.25),
            ("h2h", "home", 2.25),
            ("h2h", "draw", 3.2),
        ):
            result.append(
                candidate(
                    f"f{i}-{market}-{selection}",
                    price,
                    fixture_id=f"fixture-{i}",
                    market_key=market,
                    selection_key=selection,
                )
            )
    return result


def test_five_independent_specialists_and_daily223_share_only_raw_evidence():
    report = build_portfolio(source(all_markets()), {"target_ticket_count": 5})
    assert report["schema_version"] == 3
    assert report["construction_status"] == "COMPLETE"
    assert len(report["tickets"]) == 5
    assert report["tickets"][0]["profile_id"] == "daily223"
    assert {item["profile_id"] for item in report["profiles"]} == {
        "daily223",
        "kronikas",
        "ritmusor",
        "parharcmester",
        "orszem",
        "merlin",
    }
    assert all(item["construction_status"] == "COMPLETE" for item in report["profiles"])
    assert report["overlap"]  # Visible shared exposure, never called independent outcomes.
    expected = {
        "kronikas": ("totals_2_5", "over"),
        "ritmusor": ("btts", "yes"),
        "parharcmester": ("h2h", "home"),
        "orszem": ("totals_2_5", "under"),
        "merlin": ("h2h", "draw"),
    }
    for profile, product in expected.items():
        scoped = build_portfolio(source(all_markets()), {"enabled_profiles": [profile]})
        specialist = next(ticket for ticket in scoped["tickets"] if ticket["profile_id"] == profile)
        expected_length = 3 if profile == "merlin" else 4
        assert len(specialist["legs"]) == expected_length
        assert 1 < specialist["combined_price"] <= 40
        assert specialist["minimum_combined_price"] == (10 if profile == "kronikas" else None)
        assert not specialist["fallback"]
        assert {(leg["market_key"], leg["selection_key"]) for leg in specialist["legs"]} == {
            product
        }
        assert len({leg["fixture_id"] for leg in specialist["legs"]}) == len(specialist["legs"])


def test_ticket_ids_are_deterministic_and_data_never_claims_a_wager():
    original = source(all_markets())
    reordered = deepcopy(original)
    reordered["candidates"].reverse()
    first, second = build_portfolio(original, {}), build_portfolio(reordered, {})
    assert first == second
    assert original == source(all_markets())
    assert len(first["tickets"]) == 2
    assert all(
        ticket["status"] == "DRAFT" and ticket["stake_eur"] == 1 for ticket in first["tickets"]
    )
    assert first["real_wager_placed"] is first["betting_approved"] is False


def test_review_must_cover_every_selected_leg_before_ready():
    payload = source(all_markets())
    draft = build_portfolio(payload, {})
    ids = {leg["candidate_id"] for ticket in draft["tickets"] for leg in ticket["legs"]}
    review = {
        "status": "COMPLETE",
        "reviews": [
            {
                "candidate_id": key,
                "assessment": "support",
                "support_arguments": ["Forrásolt megállapítás."],
                "risk_notes": [],
                "evidence_ids": [],
            }
            for key in sorted(ids)
        ],
    }
    ready = build_portfolio(payload, {}, astra_review=review)
    assert all(ticket["status"] == "READY" for ticket in ready["tickets"])
    # Published snapshots are immutable; adding review evidence produces a new version.
    assert [t["ticket_id"] for t in draft["tickets"]] != [t["ticket_id"] for t in ready["tickets"]]
    review["reviews"] = []
    assert all(
        ticket["status"] == "DRAFT"
        for ticket in build_portfolio(payload, {}, astra_review=review)["tickets"]
    )


def test_review_packets_cover_equivalent_daily223_quotes_at_other_bookmakers():
    rows = [candidate(str(i), price) for i, price in enumerate((2, 2, 3))]
    rows.extend(
        candidate(
            f"alternate-{i}",
            price,
            fixture_id=str(i),
            bookmaker="alternate-book",
        )
        for i, price in enumerate((2, 2, 3))
    )
    payload = source(rows)
    preliminary = build_portfolio(payload, {"target_ticket_count": 1})

    packets = _review_packets(payload, preliminary, maximum=30)

    assert {item["candidate_id"] for item in packets} >= {
        "0", "1", "2", "alternate-0", "alternate-1", "alternate-2"
    }


def test_review_merge_accepts_scalar_rejection_diagnostics():
    base = {
        "status": "COMPLETE",
        "research_status": "COMPLETE_WITH_SOURCES",
        "reviews": [{"candidate_id": "a"}],
        "rejected_web_quotes": 2,
        "usage": {"requests": 2},
    }
    supplement = {
        "status": "COMPLETE",
        "research_status": "COMPLETE_WITH_SOURCES",
        "reviews": [{"candidate_id": "b"}],
        "rejected_web_quotes": [],
        "usage": {"requests": 2},
    }

    merged = _merge_astra_reviews(base, supplement)

    assert [item["candidate_id"] for item in merged["reviews"]] == ["a", "b"]
    assert merged["rejected_web_quotes"] == []
    assert merged["usage"]["requests"] == 4


def test_strictness_controls_specialists_without_changing_independent_daily223_eligibility():
    rows = [candidate(str(i), 2.5 if i < 2 else 3.0, strength=0.4) for i in range(3)]
    for row in rows:
        row["evidence"] = row["evidence"][:2]
    assert build_portfolio(source(rows), {"strictness": 0})["tickets"]
    strict = build_portfolio(source(rows), {"strictness": 100})
    assert [ticket["profile_id"] for ticket in strict["tickets"]] == ["daily223"]
    assert any(
        item["status"] == "BELOW_CONFIGURED_EVIDENCE_THRESHOLD" for item in strict["diagnostics"]
    )
    rows[0]["quoted_at"] = (NOW - timedelta(seconds=301)).isoformat()
    loose = build_portfolio(source(rows), {"strictness": 0})
    assert any(leg["candidate_id"] == "0" for t in loose["tickets"] for leg in t["legs"])
    assert all(not t["requires_refresh"] for t in loose["tickets"])
    rows[0]["quoted_at"] = (NOW + timedelta(seconds=1)).isoformat()
    future = build_portfolio(source(rows), {"strictness": 0})
    assert any(leg["candidate_id"] == "0" for t in future["tickets"] for leg in t["legs"])
    assert not any(item["status"] == "QUOTE_STALE_OR_FROM_FUTURE" for item in future["diagnostics"])


def test_daily223_keeps_history_supported_price_roles_below_generic_specialist_floor():
    from goal_analysis.engine.daily_223 import build_daily_223

    rows = [candidate(str(i), price, strength=0.2) for i, price in enumerate((2.15, 2.27, 3.75))]
    for row in rows:
        row["evidence"] = row["evidence"][:2]
    original = deepcopy(rows)
    standalone = build_daily_223(rows, DAY, NOW)
    result = build_portfolio(source(rows), {"strictness": 35, "target_ticket_count": 1})
    assert standalone["construction_status"] == result["construction_status"] == "COMPLETE"
    assert result["eligible_candidate_count"] == 0
    assert result["daily223_eligible_candidate_count"] == 3
    selected = result["tickets"][0]
    assert selected["profile_id"] == "daily223" and selected["odds_band"] == "STRICT"
    assert [leg["candidate_id"] for leg in selected["legs"]] == [
        leg["candidate_id"] for leg in standalone["legs"]]
    assert selected["combined_price"] == pytest.approx(18.301875)
    assert all(leg["weighted_support_score"] < result["policy"]["minimum_weighted_support"]
               for leg in selected["legs"])
    assert all(leg["score_components"]["historical"] > 0
               and leg["score_components"]["venue_form"] > 0 for leg in selected["legs"])
    assert rows == original and not result["real_wager_placed"]


def test_daily223_separate_pool_does_not_relax_strict_history_or_quote_identity():
    rows = [candidate(str(i), price, strength=0.2) for i, price in enumerate((2, 2, 3))]
    for row in rows:
        row["evidence"] = row["evidence"][:2]
    rows[2]["evidence"] = rows[2]["evidence"][:1]
    assert not build_portfolio(source(rows), {"strictness": 35})["tickets"]
    rows[2]["evidence"] = deepcopy(rows[1]["evidence"])
    rows[2]["quote_available"] = False
    assert not build_portfolio(source(rows), {"strictness": 35})["tickets"]


def test_never_combines_different_bookmakers_regions_or_duplicate_fixtures():
    rows = [candidate(str(i), 4, bookmaker=f"book{i}") for i in range(3)]
    assert not build_portfolio(source(rows), {}, allowed_profiles=["daily223"])["tickets"]
    for row in rows:
        row["bookmaker"] = "same-book"
        row["region"] = row["candidate_id"]
    assert not build_portfolio(source(rows), {}, allowed_profiles=["daily223"])["tickets"]
    for row in rows:
        row["region"] = "DE"
        row["fixture_id"] = "one-fixture"
    assert not build_portfolio(source(rows), {}, allowed_profiles=["daily223"])["tickets"]


def test_betano_falls_back_when_it_cannot_build_complete_ticket():
    rows = all_markets()
    rows.append(candidate("only-betano", 5, bookmaker="betano"))
    result = build_portfolio(source(rows), {})
    assert result["tickets"]
    assert all(ticket["bookmaker"] == "test-book" for ticket in result["tickets"])


def test_specialist_can_share_daily_fixtures_without_duplicating_full_ticket():
    rows = [candidate(str(i), price) for i, price in enumerate((2, 2, 3, 2))]
    settings = {"enabled_profiles": ["kronikas", "ritmusor"]}
    report = build_portfolio(source(rows), settings)
    assert [ticket["profile_id"] for ticket in report["tickets"]] == ["daily223", "kronikas"]
    assert report["construction_status"] == "COMPLETE"
    assert len(report["tickets"][1]["legs"]) == 4
    assert report["overlap"]
    rows.extend(
        candidate(
            f"z-btts-{i}",
            3.2,
            strength=0.7,
            fixture_id=str(i),
            market_key="btts",
            selection_key="yes",
        )
        for i in range(3)
    )
    report = build_portfolio(source(rows), settings)
    assert [ticket["profile_id"] for ticket in report["tickets"]] == ["daily223", "kronikas"]
    assert report["construction_status"] == "COMPLETE"
    assert report["overlap"]  # Same matches remain allowed for different selections.


def test_astra_caution_reorders_a_daily_leg_without_changing_raw_evidence():
    rows = [candidate(str(i), price) for i, price in enumerate((2, 2, 3))]
    rows.append(candidate("alternative", 3, strength=0.79))
    payload = source(rows)
    draft = build_portfolio(payload, {"target_ticket_count": 1})
    assert "2" in {leg["candidate_id"] for leg in draft["tickets"][0]["legs"]}
    review = {
        "status": "COMPLETE",
        "reviews": [
            {
                "candidate_id": row["candidate_id"],
                "assessment": "caution" if row["candidate_id"] == "2" else "support",
                "support_arguments": [],
                "risk_notes": ["Forrásolt bizonytalanság."] if row["candidate_id"] == "2" else [],
            }
            for row in rows
        ],
    }
    reviewed = build_portfolio(payload, {"target_ticket_count": 1}, astra_review=review)
    selected = reviewed["tickets"][0]
    assert selected["status"] == "READY"
    assert "alternative" in {leg["candidate_id"] for leg in selected["legs"]}
    assert "2" not in {leg["candidate_id"] for leg in selected["legs"]}
    assert (
        next(
            leg["support_score"] for leg in selected["legs"] if leg["candidate_id"] == "alternative"
        )
        == 79
    )


def test_half_time_experiment_requires_actual_half_time_sample_and_can_use_lower_prices():
    rows = [candidate(str(i), 1.8, market_key="totals_0_5", period="FIRST_HALF") for i in range(4)]
    payload = source(rows)
    settings = {"enabled_profiles": ["h1_over05"]}
    assert not build_portfolio(payload, settings)["tickets"]
    payload["history_analysis"]["candidates"] = [
        {
            "candidate_id": row["candidate_id"],
            "status": "CALCULATED",
            "period": "FIRST_HALF",
            "profiles": {
                side: {window: {"sample_size": 5} for window in ("historical_20", "venue_10")}
                for side in ("home", "away")
            },
        }
        for row in rows
    ]
    report = build_portfolio(payload, settings)
    ticket = report["tickets"][0]
    assert ticket["profile_id"] == "h1_over05" and ticket["experimental"] is True
    assert len(ticket["legs"]) == 4 and ticket["combined_price"] == pytest.approx(1.8 ** 4)
    assert report["construction_status"] == "PARTIAL"  # DAILY_223 is still absent.
    payload["history_analysis"]["candidates"][0]["period"] = "FULL_TIME"
    remaining = build_portfolio(payload, settings)["tickets"]
    assert remaining and remaining[0]["legs"][0]["candidate_id"] != "0"


def test_high_odds_do_not_increase_evidence_score_or_force_weak_padding():
    rows = [candidate(str(i), 3.2, strength=0.8) for i in range(3)]
    rows.append(candidate("weak-expensive", 20, strength=0.01))
    report = build_portfolio(source(rows), {})
    assert report["tickets"]
    assert all(
        leg["candidate_id"] != "weak-expensive"
        for ticket in report["tickets"]
        for leg in ticket["legs"]
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"target_ticket_count": 6},
        {"max_ticket_count": 0},
        {"target_ticket_count": 3, "max_ticket_count": 2},
        {"strictness": 101},
        {"enabled_profiles": ["imaginary-tipster"]},
        {"target_min_odds": 50, "target_max_odds": 40},
    ],
)
def test_invalid_policy_is_not_silently_changed(settings):
    with pytest.raises(ValueError):
        build_portfolio(source([]), settings)
