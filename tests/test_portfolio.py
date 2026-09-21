from copy import deepcopy
from datetime import timedelta

import pytest
from test_daily_223 import DAY, NOW, candidate

from goal_analysis.engine.portfolio import build_portfolio


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
        assert 2 <= len(specialist["legs"]) <= 6
        assert 10 <= specialist["combined_price"] <= 40
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
        ticket["status"] == "DRAFT" and ticket["stake_eur"] == 5 for ticket in first["tickets"]
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


def test_strictness_keeps_old_quotes_as_explicit_drafts_only_in_permissive_mode():
    rows = [candidate(str(i), 2.5 if i < 2 else 3.0, strength=0.4) for i in range(3)]
    for row in rows:
        row["evidence"] = row["evidence"][:2]
    assert build_portfolio(source(rows), {"strictness": 0})["tickets"]
    strict = build_portfolio(source(rows), {"strictness": 100})
    assert not strict["tickets"]
    assert any(
        item["status"] == "BELOW_CONFIGURED_EVIDENCE_THRESHOLD" for item in strict["diagnostics"]
    )
    rows[0]["quoted_at"] = (NOW - timedelta(seconds=301)).isoformat()
    loose = build_portfolio(source(rows), {"strictness": 0})
    assert any(item["status"] == "ACCEPTED_WITH_WARNINGS" for item in loose["diagnostics"])
    affected = [
        ticket
        for ticket in loose["tickets"]
        if any(leg["candidate_id"] == "0" for leg in ticket["legs"])
    ]
    assert affected and all(
        t["status"] == "DRAFT" and t["requires_refresh"] and t["quality_warnings"] for t in affected
    )
    rows[0]["quoted_at"] = (NOW + timedelta(seconds=1)).isoformat()
    future = build_portfolio(source(rows), {"strictness": 0})
    assert any(item["status"] == "QUOTE_STALE_OR_FROM_FUTURE" for item in future["diagnostics"])
    assert not any(
        leg["candidate_id"] == "0" for ticket in future["tickets"] for leg in ticket["legs"]
    )


def test_never_combines_different_bookmakers_regions_or_duplicate_fixtures():
    rows = [candidate(str(i), 4, bookmaker=f"book{i}") for i in range(3)]
    assert not build_portfolio(source(rows), {})["tickets"]
    for row in rows:
        row["bookmaker"] = "same-book"
        row["region"] = row["candidate_id"]
    assert not build_portfolio(source(rows), {})["tickets"]
    for row in rows:
        row["region"] = "DE"
        row["fixture_id"] = "one-fixture"
    assert not build_portfolio(source(rows), {})["tickets"]


def test_betano_falls_back_when_it_cannot_build_complete_ticket():
    rows = all_markets()
    rows.append(candidate("only-betano", 5, bookmaker="betano"))
    result = build_portfolio(source(rows), {})
    assert result["tickets"]
    assert all(ticket["bookmaker"] == "test-book" for ticket in result["tickets"])


def test_exact_duplicate_ticket_exposure_is_not_published_under_another_tipster_name():
    rows = [candidate(str(i), price) for i, price in enumerate((2, 2, 3))]
    settings = {"enabled_profiles": ["kronikas", "ritmusor"]}
    report = build_portfolio(source(rows), settings)
    assert [ticket["profile_id"] for ticket in report["tickets"]] == ["daily223"]
    assert report["construction_status"] == "PARTIAL"
    assert any(item["status"] == "DUPLICATE_TICKET_EXPOSURE" for item in report["diagnostics"])
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
    assert [ticket["profile_id"] for ticket in report["tickets"]] == ["daily223", "ritmusor"]
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
    assert len(ticket["legs"]) == 4 and ticket["combined_price"] == pytest.approx(1.8**4)
    assert report["construction_status"] == "PARTIAL"  # DAILY_223 is still absent.
    payload["history_analysis"]["candidates"][0]["period"] = "FULL_TIME"
    assert not build_portfolio(payload, settings)["tickets"]


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
