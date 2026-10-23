"""Global exposure limits operate on publications, never only on UI cards."""

from copy import deepcopy
from datetime import timedelta

import pytest
from test_portfolio_ledger import NOW

from goal_analysis.engine.diversification import allocate


def card(profile, fixture, *, status="READY", scope="LIVE", ticket=False):
    result = {
        "profile_id": profile, "date": NOW.date().isoformat(), "status": status,
        "publication_scope": scope, "created_at": NOW.isoformat(), "combined_price": 1.5,
        "quality_warnings": [], "legs": [{"fixture_id": fixture,
            "kickoff": (NOW + timedelta(hours=3)).isoformat(), "decimal_price": 1.5,
            "candidate_id": profile + fixture, "market_key": "totals_2_5",
            "selection_key": "over", "period": "FULL_TIME"}],
    }
    result["ticket_id" if ticket else "recommendation_id"] = profile + fixture
    return result


def option(profile, fixture, quality, *, main=False, scope="LIVE"):
    result = {"quality": quality, "recommendation": card(profile, fixture, scope=scope)}
    if main:
        result["ticket"] = card(profile, fixture, scope=scope, ticket=True)
    return result


def report(options, *, target=2):
    daily_ticket = card("daily223", "daily-a", ticket=True)
    daily_ticket["legs"].extend([card("daily223", fixture)["legs"][0]
                                for fixture in ("daily-b", "daily-c")])
    daily_rec = deepcopy(daily_ticket)
    daily_rec.update(sentinel={"entire": "object must remain unchanged"})
    cards = [deepcopy(rows[0]["recommendation"]) for rows in options.values() if rows]
    data = {"date": NOW.date().isoformat(), "tickets": [daily_ticket],
            "recommendations": [daily_rec, *cards], "profile_proposals": [deepcopy(daily_ticket)],
            "preview_tickets": [], "profiles": [{"profile_id": row["profile_id"]} for row in cards]}
    settings = {"enabled_profiles": ["daily223", *options], "target_ticket_count": target,
                "max_ticket_count": 5}
    return data, settings


def test_shared_fixture_suggestions_survive_but_small_ready_package_cannot_concentrate():
    options = {"strong": [option("strong", "daily-a", 100, main=True)],
               "weaker": [option("weaker", "daily-b", 80, main=True)],
               "paper": [option("paper", "daily-c", 1000, scope="PAPER_AUDITION")],
               "model": [option("model", "daily-c", 0.9)]}
    data, settings = report(options, target=5)
    before = deepcopy(data["tickets"][0]), deepcopy(data["recommendations"][0])
    allocate(data, options, settings, NOW)
    assert [t["profile_id"] for t in data["tickets"]] == ["daily223"]
    assert data["construction_status"] == "PARTIAL"
    assert set(data["diversification"]["assigned"]) == {"strong", "weaker", "model"}
    assert data["diversification"]["assigned"]["strong"]["penalty_factor"] == .7
    assert data["diversification"]["ready_exposure"]["single_ready_ticket_exception"]
    assert (data["tickets"][0], data["recommendations"][0]) == before


def test_quality_preserving_distinct_main_precedes_stronger_shared_main():
    options = {"strong": [option("strong", "daily-a", 100, main=True)],
               "distinct": [option("distinct", "free", 80, main=True)]}
    data, settings = report(options)
    allocate(data, options, settings, NOW)
    assert [t["profile_id"] for t in data["tickets"]] == ["daily223", "distinct"]
    assert data["diversification"]["ready_exposure"]["compliant"]
    assert "strong" not in data["diversification"]["assigned"]
    assert data["diversification"]["ready_recommendation_exposure"]["compliant"]


def test_exact_duplicate_tries_the_next_best_complete_alternative():
    options = {"flexible": [option("flexible", "x", .99), option("flexible", "y", .95)],
               "sole": [option("sole", "x", 1)]}
    data, settings = report(options)
    allocate(data, options, settings, NOW)
    assigned = data["diversification"]["assigned"]
    assert assigned["flexible"]["fixture_ids"] == ["y"] and assigned["sole"]["fixture_ids"] == ["x"]


def test_old_ninety_percent_cutoff_no_longer_discards_a_valid_alternative():
    options = {"model": [option("model", "daily-a", 100), option("model", "x", 89)]}
    data, settings = report(options)
    allocate(data, options, settings, NOW)
    assert data["diversification"]["assigned"]["model"]["fixture_ids"] == ["x"]
    assert data["diversification"]["assigned"]["model"]["quality"] == .89
    assert data["diversification"]["own_quality_retention"] is None


def test_quality_retention_diagnostics_do_not_change_choices_or_protected_daily_objects():
    options = {"model": [option("model", "daily-a", 100, main=True),
                         option("model", "x", 89, main=True)]}
    data, settings = report(options)
    before_options = deepcopy(options)
    before_daily = {group: deepcopy([row for row in data[group] if row["profile_id"] == "daily223"])
                    for group in ("tickets", "recommendations", "profile_proposals", "preview_tickets")}
    allocate(data, options, settings, NOW)
    assigned = data["diversification"]["assigned"]["model"]
    # The established selection remains the distinct 0.89 option: reporting
    # its retention must not reinstate the removed 90% eligibility cutoff.
    assert assigned["fixture_ids"] == ["x"]
    assert assigned["own_best_quality"] == 1.0
    assert assigned["retained_own_quality_ratio"] == pytest.approx(.89)
    assert assigned["quality"] == .89
    for group, protected in before_daily.items():
        assert [row for row in data[group] if row["profile_id"] == "daily223"] == protected
    for group in ("tickets", "recommendations", "profile_proposals"):
        selected = next(row for row in data[group] if row["profile_id"] == "model")
        assert selected["diversification"]["own_best_quality"] == 1.0
        assert selected["diversification"]["retained_own_quality_ratio"] == pytest.approx(.89)
    assert options == before_options


def test_negative_utilities_are_invalid_but_zero_is_not_a_probability_claim():
    options = {"model": [option("model", "daily-a", 0), option("model", "x", -.01),
                         option("model", "y", 0)]}
    data, settings = report(options)
    allocate(data, options, settings, NOW)
    assert data["diversification"]["assigned"]["model"]["quality"] == 0
    assert data["diversification"]["assigned"]["model"]["fixture_ids"] != ["x"]
    assert data["diversification"]["assigned"]["model"]["own_best_quality"] == 0
    assert data["diversification"]["assigned"]["model"]["retained_own_quality_ratio"] is None


def test_same_fixture_different_book_does_not_create_independence():
    options = {"first": [option("first", "x", 1)], "second": [option("second", "x", 1)]}
    options["first"][0]["recommendation"]["legs"][0]["bookmaker"] = "book-a"
    options["second"][0]["recommendation"]["legs"][0]["bookmaker"] = "book-b"
    data, settings = report(options)
    allocate(data, options, settings, NOW)
    assert len(data["diversification"]["assigned"]) == 1


def test_live_scope_precedes_paper_and_aliases_consume_one_allocation():
    options = {"paper": [option("paper", "x", 99, scope="PAPER_AUDITION")],
               "live": [option("live", "x", 1, main=True)]}
    data, settings = report(options)
    allocate(data, options, settings, NOW)
    assert list(data["diversification"]["assigned"]) == ["live"]
    chosen_ticket = data["tickets"][1]
    chosen_rec = next(x for x in data["recommendations"] if x["profile_id"] == "live")
    assert chosen_rec["portfolio_ticket_id"] == chosen_ticket["ticket_id"]
    assert chosen_rec["selected_for_portfolio"]
    assert len(data["profile_proposals"]) == 2


def test_started_options_do_not_reserve_a_fixture_and_errors_are_preserved():
    options = {"expired": [option("expired", "x", 100)], "valid": [option("valid", "x", 1)]}
    options["expired"][0]["recommendation"]["legs"][0]["kickoff"] = NOW.isoformat()
    data, settings = report(options)
    error = {"profile_id": "broken", "status": "MODEL_ERROR", "legs": []}
    data["recommendations"].append(deepcopy(error))
    settings["enabled_profiles"].append("broken")
    allocate(data, options, settings, NOW)
    assert list(data["diversification"]["assigned"]) == ["valid"]
    assert data["recommendations"][-1] == error


def test_unconstructed_research_cannot_fill_the_main_target():
    data, settings = report({})
    data["recommendations"].append(card("research", "free", status="DRAFT"))
    allocate(data, {}, settings, NOW)
    assert len(data["tickets"]) == 1 and data["construction_status"] == "PARTIAL"
    assert data["recommendations"][-1]["status"] == "ABSTAINED"


@pytest.mark.parametrize("failure", ["DATA_BLOCKED", "FAILED", "ERROR", "UNAVAILABLE", "TEAM_PAUSED"])
def test_failed_whole_report_cannot_be_rewritten_as_a_successful_withdrawal(failure):
    options = {"profile": [option("profile", "daily-a", 1)]}
    data, settings = report(options)
    data["construction_status"] = failure
    original = deepcopy(data)
    assert allocate(data, options, settings, NOW) == original
    assert "diversification" not in data


def test_final_abstentions_withdraw_old_future_suggestions_and_freeze_only_real_picks(tmp_path):
    from test_portfolio_ledger import report as old_report
    from test_portfolio_ledger import ticket as old_ticket

    from goal_analysis.learning._metrics import measure
    from goal_analysis.storage.portfolio_ledger import PortfolioLedger

    clock = [NOW]
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: clock[0])
    ledger.save_run(old_report(old_ticket("old-ritmusor", "ritmusor")))
    options = {"h1_over05": [option("h1_over05", "daily-a", 100, main=True)],
               "ritmusor": [option("ritmusor", "daily-a", 80, main=True)]}
    data, settings = report(options)
    clock[0] += timedelta(minutes=1)
    data.update(run_id="diversified", observed_at=clock[0].isoformat(), finished_at=clock[0].isoformat())
    allocate(data, options, settings, clock[0])
    ledger.save_run(data)
    published = ledger.list_recommendations()
    assert {item["profile_id"] for item in published} == {"daily223"}
    old = next(item for item in ledger.list_recommendations(archive=True) if item["profile_id"] == "ritmusor")
    assert not old["active"] and old["legs"]
    frozen = ledger.freeze_run("diversified")
    assert frozen["recommendation_count"] == 1
    learned = measure(ledger.path, clock[0], include_records=True)
    assert {item["profile_id"] for item in learned["records"]["published"]} == {"daily223"}


def test_failed_abstention_report_does_not_withdraw_an_existing_valid_tip(tmp_path):
    from test_portfolio_ledger import report as old_report
    from test_portfolio_ledger import ticket as old_ticket

    from goal_analysis.storage.portfolio_ledger import PortfolioLedger

    clock = [NOW]
    ledger = PortfolioLedger(tmp_path / "ledger.sqlite3", now=lambda: clock[0])
    ledger.save_run(old_report(old_ticket("old-ritmusor", "ritmusor")))
    clock[0] += timedelta(minutes=1)
    failed = old_report(identifier="failed", observed_at=clock[0])
    failed.update(construction_status="DATA_BLOCKED", recommendations=[{
        "profile_id": "ritmusor", "status": "ABSTAINED", "legs": [],
    }])
    allocate(failed, {}, {"enabled_profiles": ["ritmusor"]}, clock[0])
    ledger.save_run(failed)
    assert ledger.list_recommendations()[0]["profile_id"] == "ritmusor"
    assert ledger.list_recommendations()[0]["active"]


def test_abstained_club_metadata_describes_zero_selected_exposure():
    profile = "tipster_rovid_lotti"
    options = {profile: [option(profile, "daily-a", 1, scope="PAPER_AUDITION")]}
    data, settings = report(options)
    options[profile][0]["recommendation"]["legs"] = deepcopy(data["tickets"][0]["legs"])
    data["recommendations"][1].update(model_summary={"selected_legs": 1}, selection_diagnostics={
        "selected_fixture_ids": ["daily-a"], "club_published_profile_count": 18,
        "club_selected_fixture_count": 1, "selected_min_estimated_edge": .2,
    })
    allocate(data, options, settings, NOW)
    item = data["recommendations"][1]
    assert item["model_summary"]["selected_legs"] == 0
    assert item["selection_diagnostics"]["selected_fixture_ids"] == []
    assert item["selection_diagnostics"]["club_published_profile_count"] == 0
    assert item["selection_diagnostics"]["club_selected_fixture_count"] == 0
    assert "selected_min_estimated_edge" not in item["selection_diagnostics"]


def test_full_ready_recommendation_package_repairs_fixture_concentration():
    options = {}
    for index in range(4):
        profile = f"paper-{index}"
        options[profile] = [
            option(profile, "shared", 100 - index, scope="PAPER_AUDITION"),
            option(profile, f"unique-{index}", 20 - index, scope="PAPER_AUDITION"),
        ]
        options[profile][0]["recommendation"]["legs"][0]["line"] = 2.5 + index
    data, settings = report(options)
    allocate(data, options, settings, NOW)
    exposure = data["diversification"]["ready_recommendation_exposure"]
    assert exposure["ready_ticket_count"] == 5
    assert exposure["compliant"]
    assert exposure["fixture_ticket_counts"]["shared"] <= 2
    assert sum(item["fixture_id"] == "shared" for card in data["recommendations"]
               for item in card.get("legs", [])) <= 2


def test_full_ready_package_abstains_only_when_no_exposure_safe_alternative_exists():
    options = {f"paper-{index}": [option(f"paper-{index}", "shared", 100 - index,
               scope="PAPER_AUDITION")] for index in range(4)}
    for index, rows in enumerate(options.values()):
        rows[0]["recommendation"]["legs"][0]["line"] = 2.5 + index
    data, settings = report(options)
    allocate(data, options, settings, NOW)
    exposure = data["diversification"]["ready_recommendation_exposure"]
    assert exposure["compliant"]
    assert exposure["fixture_ticket_counts"]["shared"] == 1
    assert sum(item["status"] == "READY" for item in data["recommendations"]) == 2
    assert sum(item["action"] == "ABSTAINED_FOR_EXPOSURE"
               for item in data["diversification"]["recommendation_exposure_repairs"]) == 3
